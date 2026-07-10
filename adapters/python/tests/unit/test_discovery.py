# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""
Unit tests for pyadapter.discovery — list entry generation and cache integration.
"""
from __future__ import annotations

import importlib.metadata
import json
from collections.abc import Iterator
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

from ms_dsc import DscResource, dsc_resource
from ms_dsc.metadata import SetReturn, TestReturn
from ms_dsc.schema import DataclassSchemaProvider
from ms_dsc.results import SetResult, TestResult


# ---------------------------------------------------------------------------
# Test resource classes
# ---------------------------------------------------------------------------

@dataclass
class Schema:
    name: str
    _exist: bool = True


@dsc_resource(type="Disc/Full", version="1.2.3", description="All caps",
              tags=["a", "b"], set_return=SetReturn.STATE_AND_DIFF)
class FullResource(DscResource[Schema]):
    schema_provider = DataclassSchemaProvider(Schema)
    def get(self, i): return i
    def set(self, i): return SetResult(actual_state=i, changed_properties=[])
    def test(self, i): return TestResult(actual_state=i, differing_properties=[])
    def delete(self, i): pass
    def export(self, i): return iter([])


@dsc_resource(type="Disc/GetOnly", version="0.1.0")
class GetOnlyResource(DscResource[Schema]):
    schema_provider = DataclassSchemaProvider(Schema)
    def get(self, i): return i


# ---------------------------------------------------------------------------
# Tests for _build_list_entry
# ---------------------------------------------------------------------------

class TestBuildListEntry:
    def test_type_name(self):
        from pyadapter.discovery import _build_list_entry
        entry = _build_list_entry("Disc/Full", FullResource)
        assert entry["type"] == "Disc/Full"

    def test_version_from_metadata(self):
        from pyadapter.discovery import _build_list_entry
        entry = _build_list_entry("Disc/Full", FullResource)
        assert entry["version"] == "1.2.3"

    def test_description_from_metadata(self):
        from pyadapter.discovery import _build_list_entry
        entry = _build_list_entry("Disc/Full", FullResource)
        assert entry["description"] == "All caps"

    def test_no_tags_in_entry(self):
        """Tags are excluded from list entries (not supported in DSC adapted resource schema)."""
        from pyadapter.discovery import _build_list_entry
        entry = _build_list_entry("Disc/Full", FullResource)
        assert "tags" not in entry

    def test_require_adapter_field(self):
        from pyadapter.discovery import _build_list_entry
        entry = _build_list_entry("Disc/Full", FullResource)
        assert entry["requireAdapter"] == "Microsoft.Adapter/Python"

    def test_capabilities_all_five(self):
        from pyadapter.discovery import _build_list_entry
        entry = _build_list_entry("Disc/Full", FullResource)
        for cap in ("get", "set", "test", "delete", "export"):
            assert cap in entry["capabilities"]

    def test_capabilities_get_only(self):
        from pyadapter.discovery import _build_list_entry
        entry = _build_list_entry("Disc/GetOnly", GetOnlyResource)
        assert entry["capabilities"] == ["get"]
        for cap in ("set", "test", "delete", "export"):
            assert cap not in entry["capabilities"]

    def test_schema_embedded(self):
        from pyadapter.discovery import _build_list_entry
        entry = _build_list_entry("Disc/Full", FullResource)
        assert "schema" in entry
        assert "embedded" in entry["schema"]
        schema = entry["schema"]["embedded"]
        assert schema["type"] == "object"

    def test_kind_is_resource(self):
        from pyadapter.discovery import _build_list_entry
        entry = _build_list_entry("Disc/Full", FullResource)
        assert entry["kind"] == "resource"

    def test_no_description_key_when_empty(self):
        from pyadapter.discovery import _build_list_entry
        entry = _build_list_entry("Disc/GetOnly", GetOnlyResource)
        # description is empty string → should not appear
        assert "description" not in entry or entry.get("description") == ""

    def test_no_tags_key_when_empty(self):
        from pyadapter.discovery import _build_list_entry
        entry = _build_list_entry("Disc/GetOnly", GetOnlyResource)
        assert "tags" not in entry or entry.get("tags") == []

    def test_content_has_module_and_class(self):
        """List entries include content metadata with module and class."""
        from pyadapter.discovery import _build_list_entry
        entry = _build_list_entry("Disc/Full", FullResource)
        assert "content" in entry
        content = entry["content"]
        assert content["module"] == "unit.test_discovery"
        assert content["class"] == "FullResource"

    def test_no_path_field_in_entry(self):
        """List entries should use 'content' instead of 'path'."""
        from pyadapter.discovery import _build_list_entry
        entry = _build_list_entry("Disc/Full", FullResource)
        assert "path" not in entry


# ---------------------------------------------------------------------------
# Tests for cmd_list
# ---------------------------------------------------------------------------

class TestCmdList:
    def _make_ep(self, type_name, cls):
        return type("EP", (), {"name": type_name, "value": "x:Y", "load": lambda self, c=cls: c})()

    def test_returns_zero(self, tmp_path):
        """cmd_list exits with 0 when at least one entry point exists."""
        import io
        from pyadapter.discovery import cmd_list, DISCOVER_CACHE, LIST_CACHE

        # Point caches to tmp locations to avoid interference.
        DISCOVER_CACHE._path = tmp_path / "disc.json"
        LIST_CACHE._path = tmp_path / "list.json"

        eps = [self._make_ep("Disc/Full", FullResource)]
        with patch.object(importlib.metadata, "entry_points", return_value=eps):
            with patch("pyadapter.discovery._covered_types", return_value=set()):
                buf = io.StringIO()
                with patch("sys.stdout", buf):
                    rc = cmd_list()
        assert rc == 0

    def test_emits_ndjson(self, tmp_path):
        """Each line of list output is a valid JSON object."""
        import io
        from pyadapter.discovery import cmd_list, DISCOVER_CACHE, LIST_CACHE

        DISCOVER_CACHE._path = tmp_path / "disc.json"
        LIST_CACHE._path = tmp_path / "list.json"

        eps = [self._make_ep("Disc/Full", FullResource), self._make_ep("Disc/GetOnly", GetOnlyResource)]
        with patch.object(importlib.metadata, "entry_points", return_value=eps):
            with patch("pyadapter.discovery._covered_types", return_value=set()):
                buf = io.StringIO()
                with patch("sys.stdout", buf):
                    cmd_list()

        lines = [l for l in buf.getvalue().splitlines() if l.strip()]
        assert len(lines) == 2
        for line in lines:
            obj = json.loads(line)
            assert "type" in obj
            assert "capabilities" in obj

    def test_skips_covered_types(self, tmp_path):
        """Resources already covered by pre-built manifests are excluded from list output."""
        import io
        from pyadapter.discovery import cmd_list, DISCOVER_CACHE, LIST_CACHE

        DISCOVER_CACHE._path = tmp_path / "disc.json"
        LIST_CACHE._path = tmp_path / "list.json"

        eps = [self._make_ep("Disc/Full", FullResource), self._make_ep("Disc/GetOnly", GetOnlyResource)]
        # Disc/Full is "covered" by a pre-built manifest
        covered = {"disc/full"}
        with patch.object(importlib.metadata, "entry_points", return_value=eps):
            with patch("pyadapter.discovery._covered_types", return_value=covered):
                buf = io.StringIO()
                with patch("sys.stdout", buf):
                    cmd_list()

        lines = [l for l in buf.getvalue().splitlines() if l.strip()]
        types = [json.loads(l)["type"] for l in lines]
        assert "Disc/Full" not in types
        assert "Disc/GetOnly" in types


# ---------------------------------------------------------------------------
# Tests for cmd_clear_cache
# ---------------------------------------------------------------------------

class TestCmdClearCache:
    def test_clear_removes_both_caches(self, tmp_path):
        from pyadapter.discovery import cmd_clear_cache, DISCOVER_CACHE, LIST_CACHE

        DISCOVER_CACHE._path = tmp_path / "disc.json"
        LIST_CACHE._path = tmp_path / "list.json"

        DISCOVER_CACHE.save("fp", [])
        LIST_CACHE.save("fp", [])
        assert DISCOVER_CACHE._path.exists()
        assert LIST_CACHE._path.exists()

        rc = cmd_clear_cache()
        assert rc == 0
        assert not DISCOVER_CACHE._path.exists()
        assert not LIST_CACHE._path.exists()

    def test_clear_is_idempotent(self, tmp_path):
        from pyadapter.discovery import cmd_clear_cache, DISCOVER_CACHE, LIST_CACHE

        DISCOVER_CACHE._path = tmp_path / "disc.json"
        LIST_CACHE._path = tmp_path / "list.json"

        rc = cmd_clear_cache()
        assert rc == 0

