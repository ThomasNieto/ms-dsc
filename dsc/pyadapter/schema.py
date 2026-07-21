# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""
Basic JSON Schema generation from Python dataclasses.

This module provides a standalone, stdlib-only schema generator used by the
adapter at runtime when ms-dsc is not available, or when a resource class has
not registered a schema_provider.  The full DataclassSchemaProvider in ms-dsc
uses the same logic.
"""
from __future__ import annotations

import dataclasses
import enum
import inspect
import types
import typing
from typing import Any, cast


def generate_schema(cls: type) -> dict:
    """
    Attempt to produce a JSON Schema dict for a resource class.

    Resolution order:
      1. cls.get_schema()  — SDK path (DscResource subclass)
      2. cls.schema()      — explicit classmethod
      3. Dataclass introspection of the class itself
      4. Empty schema {}   — DSC will accept any input
    """
    if hasattr(cls, "get_schema") and callable(cls.get_schema):
        try:
            return cast(dict, cls.get_schema())
        except Exception:
            pass

    if hasattr(cls, "schema") and callable(cls.schema):
        try:
            return cast(dict, cls.schema())
        except Exception:
            pass

    schema_type = _get_schema_type(cls)
    if schema_type is not None and dataclasses.is_dataclass(schema_type):
        return _dataclass_to_json_schema(schema_type)

    return {}


def _get_schema_type(cls: type) -> type | None:
    """Resolve T from DscResource[T] via schema_provider or __orig_bases__."""
    provider = getattr(cls, "schema_provider", None)
    if provider is not None and hasattr(provider, "schema_type"):
        return provider.schema_type  # type: ignore[no-any-return]

    for base in getattr(cls, "__orig_bases__", ()):
        args = typing.get_args(base)
        if len(args) == 1 and isinstance(args[0], type) and dataclasses.is_dataclass(args[0]):
            return args[0]
    return None


def _dataclass_to_json_schema(cls: type) -> dict:
    props: dict = {}
    required: list[str] = []

    try:
        hints = typing.get_type_hints(cls, include_extras=True)
    except Exception:
        hints = {f.name: f.type for f in dataclasses.fields(cls)}

    for field in dataclasses.fields(cls):
        field_schema = _type_to_schema(hints.get(field.name, type(None)))

        meta = field.metadata
        if "description" in meta:
            field_schema["description"] = meta["description"]
        if "title" in meta:
            field_schema["title"] = meta["title"]

        props[field.name] = field_schema

        if field.default is dataclasses.MISSING and field.default_factory is dataclasses.MISSING:  # type: ignore[misc]
            required.append(field.name)

    schema: dict = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "properties": props,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


def _type_to_schema(t: Any) -> dict:  # noqa: C901 (complexity acceptable for type mapping)
    # Python 3.10+ union syntax: X | Y
    if isinstance(t, types.UnionType):
        args = typing.get_args(t)
        return _union_schema(args)

    origin = typing.get_origin(t)
    args = typing.get_args(t)

    # typing.Union[X, Y] / Optional[X]
    if origin is typing.Union:
        return _union_schema(args)

    # list[T]
    if origin is list:
        schema: dict = {"type": "array"}
        if args:
            schema["items"] = _type_to_schema(args[0])
        return schema

    # dict[str, V]
    if origin is dict:
        schema = {"type": "object"}
        if len(args) >= 2:
            schema["additionalProperties"] = _type_to_schema(args[1])
        return schema

    # Literal["a", "b"]
    if origin is typing.Literal:
        return {"enum": list(args)}

    # Primitives
    if t is str:
        return {"type": "string"}
    if t is int:
        return {"type": "integer"}
    if t is float:
        return {"type": "number"}
    if t is bool:
        return {"type": "boolean"}
    if t is type(None):
        return {"type": "null"}

    # Enum subclass
    if inspect.isclass(t) and issubclass(t, enum.Enum):
        return {"enum": [m.value for m in t]}

    # Nested dataclass
    if inspect.isclass(t) and dataclasses.is_dataclass(t):
        return _dataclass_to_json_schema(t)

    return {}


def _union_schema(args: tuple) -> dict:
    non_none = [a for a in args if a is not type(None)]
    has_none = len(non_none) < len(args)

    if len(non_none) == 1:
        schema = _type_to_schema(non_none[0])
        if has_none:
            existing = schema.get("type")
            if isinstance(existing, str):
                schema["type"] = [existing, "null"]
            elif existing is None:
                schema["type"] = "null"
        return schema

    schemas = [_type_to_schema(a) for a in args]
    return {"anyOf": schemas}
