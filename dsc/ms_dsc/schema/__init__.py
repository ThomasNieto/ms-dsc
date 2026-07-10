# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""
Schema provider Protocol and public re-exports.

from ms_dsc.schema import DataclassSchemaProvider, PydanticSchemaProvider, SchemaProvider
"""
from ms_dsc.schema._dataclass import DataclassSchemaProvider
from ms_dsc.schema._pydantic import PydanticSchemaProvider
from ms_dsc.schema._protocol import SchemaProvider

__all__ = ["DataclassSchemaProvider", "PydanticSchemaProvider", "SchemaProvider"]
