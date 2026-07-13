# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""
Unit tests for pyadapter.discovery — list entry generation and cache integration.
"""
from __future__ import annotations

import importlib.metadata
import importlib.resources
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
        assert "description" not in entry or entry.get("description") == ""

    def test_no_tags_key_when_empty(self):
        from pyadapter.discovery import _build_list_entry
        entry = _build_list_entry("Disc/GetOnly", GetOnlyResource)
        assert "tags" not in entry or entry.get("tags") == []

    def test_no_content_field_in_list_entry(self):
        """List entries must NOT include 'content' — it is not a valid DscResource field."""
        from pyadapter.discovery import _build_list_entry
        entry = _build_list_entry("Disc/Full", FullResource)
        assert "content" not in entry

    def test_no_path_field_in_entry(self):
        """List entries should not include a 'path' field."""
        from pyadapter.discovery import _build_list_entry
        entry = _build_list_entry("Disc/Full", FullResource)
        assert "path" not in entry


# ---------------------------------------------------------------------------
# Tests for _manifests_for_dist (importlib.resources-based discovery)
# ---------------------------------------------------------------------------

class TestManifestsForDist:
    def _make_dist(self, name: str) -> MagicMock:
        d = MagicMock()
        d.name = name
        return d

    def test_returns_empty_for_non_dsc_package(self):
        """Distributions without a 'dsc' subpackage return no manifests."""
        from pyadapter.discovery import _manifests_for_dist
        dist = self._make_dist("some-regular-package")
        with patch("importlib.resources.files", side_effect=ModuleNotFoundError):
            result = _manifests_for_dist(dist)
        assert result == []

    def test_normalises_hyphenated_name(self, tmp_path):
        """Distribution names with hyphens are normalised to underscores."""
        from pyadapter.discovery import _manifests_for_dist
        dist = self._make_dist("my-dsc-resource")
        # Should try 'my_dsc_resource.dsc', not 'my-dsc-resource.dsc'.
        with patch("importlib.resources.files", side_effect=ModuleNotFoundError) as mock_files:
            _manifests_for_dist(dist)
        mock_files.assert_called_once_with("my_dsc_resource.dsc")

    def test_finds_manifest_via_resources(self, tmp_path):
        """Returns manifest path entries when importlib.resources finds manifests."""
        from pyadapter.discovery import _manifests_for_dist

        # Create a real manifest file on disk.
        manifest = tmp_path / "Test.Resource.dsc.adaptedResource.json"
        manifest.write_text('{"type": "Test/Resource"}', encoding="utf-8")

        dist = self._make_dist("test-resource")

        mock_resource = MagicMock()
        mock_resource.name = "Test.Resource.dsc.adaptedResource.json"
        mock_resource.__str__ = lambda self: str(manifest)

        mock_ref = MagicMock()
        mock_ref.iterdir.return_value = [mock_resource]

        with patch("importlib.resources.files", return_value=mock_ref):
            result = _manifests_for_dist(dist)

        assert len(result) == 1
        assert "manifestPath" in result[0]

    def test_skips_non_manifest_files(self, tmp_path):
        """Non-manifest files in the dsc package are ignored."""
        from pyadapter.discovery import _manifests_for_dist

        dist = self._make_dist("test-resource")

        mock_resource = MagicMock()
        mock_resource.name = "README.md"
        mock_ref = MagicMock()
        mock_ref.iterdir.return_value = [mock_resource]

        with patch("importlib.resources.files", return_value=mock_ref):
            result = _manifests_for_dist(dist)

        assert result == []


# ---------------------------------------------------------------------------
# Tests for cmd_list
# ---------------------------------------------------------------------------

class TestCmdList:
    def _make_ep(self, type_name, cls):
        return type("EP", (), {"name": type_name, "value": "x:Y", "load": lambda self, c=cls: c})()

    def test_returns_zero(self, tmp_path):
        """cmd_list exits with 0 when at least one entry point exists."""
        import io
        from pyadapter.discovery import cmd_list, LIST_CACHE

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
        from pyadapter.discovery import cmd_list, LIST_CACHE

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

    def test_list_entries_have_no_content_field(self, tmp_path):
        """List entries must not contain 'content' (not a valid DscResource field)."""
        import io
        from pyadapter.discovery import cmd_list, LIST_CACHE

        LIST_CACHE._path = tmp_path / "list.json"

        eps = [self._make_ep("Disc/Full", FullResource)]
        with patch.object(importlib.metadata, "entry_points", return_value=eps):
            with patch("pyadapter.discovery._covered_types", return_value=set()):
                buf = io.StringIO()
                with patch("sys.stdout", buf):
                    cmd_list()

        for line in buf.getvalue().splitlines():
            if line.strip():
                assert "content" not in json.loads(line)

    def test_skips_covered_types(self, tmp_path):
        """Resources already covered by pre-built manifests are excluded from list output."""
        import io
        from pyadapter.discovery import cmd_list, LIST_CACHE

        LIST_CACHE._path = tmp_path / "list.json"

        eps = [self._make_ep("Disc/Full", FullResource), self._make_ep("Disc/GetOnly", GetOnlyResource)]
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
    def test_clear_removes_list_cache(self, tmp_path):
        from pyadapter.discovery import cmd_clear_cache, LIST_CACHE

        LIST_CACHE._path = tmp_path / "list.json"
        LIST_CACHE.save("fp", [])
        assert LIST_CACHE._path.exists()

        rc = cmd_clear_cache()
        assert rc == 0
        assert not LIST_CACHE._path.exists()

    def test_clear_is_idempotent(self, tmp_path):
        from pyadapter.discovery import cmd_clear_cache, LIST_CACHE

        LIST_CACHE._path = tmp_path / "list.json"
        rc = cmd_clear_cache()
        assert rc == 0



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

