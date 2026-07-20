# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""
JSON Schema generation from Python dataclasses.

Delegates to ms_dsc.schema.DataclassSchemaProvider for schema generation.
"""
from __future__ import annotations

from ms_dsc.schema import DataclassSchemaProvider


def generate_schema(cls: type) -> dict:
    """
    Produce a JSON Schema dict for a resource class.

    Resolution order:
      1. cls.get_schema()  — SDK path (DscResource subclass)
      2. cls.schema()      — explicit classmethod
      3. Dataclass introspection via ms_dsc.schema.DataclassSchemaProvider
      4. Empty schema {}   — DSC will accept any input
    """
    if hasattr(cls, "get_schema") and callable(cls.get_schema):
        try:
            return cls.get_schema()
        except Exception:
            pass

    if hasattr(cls, "schema") and callable(cls.schema):
        try:
            return cls.schema()
        except Exception:
            pass

    # Try to use DataclassSchemaProvider from ms_dsc
    schema_type = _get_schema_type(cls)
    if schema_type is not None:
        try:
            provider = DataclassSchemaProvider(schema_type)
            return provider.get_schema()
        except Exception:
            pass

    return {}


def _get_schema_type(cls: type) -> type | None:
    """Resolve T from DscResource[T] via schema_provider or __orig_bases__."""
    import dataclasses
    import typing

    provider = getattr(cls, "schema_provider", None)
    if provider is not None and hasattr(provider, "schema_type"):
        return provider.schema_type  # type: ignore[no-any-return]

    for base in getattr(cls, "__orig_bases__", ()):
        args = typing.get_args(base)
        if len(args) == 1 and dataclasses.is_dataclass(args[0]):
            return args[0]
    return None
