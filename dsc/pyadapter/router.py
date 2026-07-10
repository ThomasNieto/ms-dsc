# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""
Operation router for the Python DSC adapter.

Resolves a resource class from the `microsoft.dsc.resources` entry-point group,
instantiates the resource and its schema type from the JSON input, dispatches
to the appropriate Protocol method, and formats stdout per DSC's adapter contract.

stdout contract (single-config mode):
  get    → one JSON line: actual state dict
  set    → one line (state only) OR two lines (state + changed_properties)
  test   → one line (state only) OR two lines (state + differing_properties)
  delete → no stdout
  export → zero or more JSON lines, one per exported instance
"""
from __future__ import annotations

import dataclasses
import importlib.metadata
import json
import logging
import sys
import typing
from typing import Any

_logger = logging.getLogger(__name__)


def dispatch(operation: str, resource_type: str, stdin_json: str) -> int:
    try:
        cls = _resolve_class(resource_type)
    except (ValueError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2

    try:
        data: dict[str, Any] = json.loads(stdin_json.strip() or "{}")
    except json.JSONDecodeError as exc:
        print(json.dumps({"error": f"Invalid JSON input: {exc}"}), file=sys.stderr)
        return 2

    try:
        resource = cls()
    except Exception as exc:
        _logger.error("Failed to instantiate %s: %s", resource_type, exc)
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1

    metadata = getattr(cls, "__dsc_metadata__", None)

    try:
        # Export is the only operation where an empty input means "no filter" (None).
        if operation == "export":
            instance = _build_schema_instance(cls, data) if data else None
            return _do_export(resource, instance)

        schema_instance = _build_schema_instance(cls, data)

        if operation == "get":
            return _do_get(resource, schema_instance)
        if operation == "set":
            return _do_set(resource, schema_instance, metadata)
        if operation == "test":
            return _do_test(resource, schema_instance, metadata)
        if operation == "delete":
            return _do_delete(resource, schema_instance)
        print(json.dumps({"error": f"Unknown operation: {operation}"}), file=sys.stderr)
        return 2
    except Exception as exc:
        _logger.error("Operation '%s' on '%s' failed: %s", operation, resource_type, exc, exc_info=True)
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1


def _resolve_class(resource_type: str) -> type:
    if not resource_type:
        raise ValueError("--resource is required")

    type_lower = resource_type.casefold()
    try:
        eps = importlib.metadata.entry_points(group="microsoft.dsc.resources")
    except Exception as exc:
        raise RuntimeError(f"Failed to enumerate entry points: {exc}") from exc

    for ep in eps:
        if ep.name.casefold() == type_lower:
            _logger.debug("Resolved %s → %s", resource_type, getattr(ep, "value", "?"))
            return ep.load()

    raise ValueError(
        f"Resource type '{resource_type}' not found. "
        "Ensure the package is installed and registers a "
        "'microsoft.dsc.resources' entry point."
    )


def _get_schema_type(cls: type) -> type | None:
    """Resolve T from DscResource[T] via schema_provider or __orig_bases__."""
    provider = getattr(cls, "schema_provider", None)
    if provider is not None and hasattr(provider, "schema_type"):
        return provider.schema_type  # type: ignore[no-any-return]

    for base in getattr(cls, "__orig_bases__", ()):
        args = typing.get_args(base)
        if len(args) == 1 and dataclasses.is_dataclass(args[0]):
            return args[0]
    return None


def _build_schema_instance(cls: type, data: dict) -> Any:
    """Construct the schema (T) instance from JSON data."""
    schema_type = _get_schema_type(cls)

    if schema_type is None:
        return data

    if dataclasses.is_dataclass(schema_type):
        known = {f.name for f in dataclasses.fields(schema_type)}
        filtered = {k: v for k, v in data.items() if k in known}
        return schema_type(**filtered)

    # Pydantic BaseModel
    if hasattr(schema_type, "model_validate"):
        return schema_type.model_validate(data)

    try:
        return schema_type(**data)
    except Exception:
        return data


def _to_dict(obj: Any) -> dict:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    raise TypeError(f"Cannot serialise {type(obj)!r} to dict")


def _do_get(resource: Any, instance: Any) -> int:
    result = resource.get(instance)
    print(json.dumps(_to_dict(result)))
    return 0


def _do_set(resource: Any, instance: Any, metadata: Any) -> int:
    result = resource.set(instance)
    print(json.dumps(_to_dict(result.actual_state)))

    # Use .value attribute so we never import from ms_dsc — adapter is stdlib-only.
    set_return = getattr(getattr(metadata, "set_return", None), "value", "state") if metadata is not None else "state"
    if set_return == "stateAndDiff":
        print(json.dumps(result.changed_properties or []))
    return 0


def _do_test(resource: Any, instance: Any, metadata: Any) -> int:
    result = resource.test(instance)
    print(json.dumps(_to_dict(result.actual_state)))

    # Use .value attribute so we never import from ms_dsc — adapter is stdlib-only.
    test_return = getattr(getattr(metadata, "test_return", None), "value", "state") if metadata is not None else "state"
    if test_return == "stateAndDiff":
        print(json.dumps(result.differing_properties or []))
    return 0


def _do_delete(resource: Any, instance: Any) -> int:
    resource.delete(instance)
    return 0


def _do_export(resource: Any, instance: Any | None) -> int:
    for item in resource.export(instance):
        print(json.dumps(_to_dict(item)))
    return 0
