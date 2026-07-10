# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""
Unit tests for ms_dsc.cli — dsc-gen manifest command.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from ms_dsc import DscResource, dsc_resource
from ms_dsc.metadata import SetReturn, TestReturn
from ms_dsc.results import SetResult, TestResult
from ms_dsc.schema import DataclassSchemaProvider


# ---------------------------------------------------------------------------
# Module-level resource classes (must be importable for dsc-gen)
# ---------------------------------------------------------------------------

@dataclass
class _CliTestSchema:
    name: str = field(metadata={"description": "Name field."})
    _exist: bool = field(default=True, metadata={"description": "Existence flag."})


@dsc_resource(type="Cli/GetOnly", version="1.2.3", description="CLI test get-only resource")
class _CliGetOnlyResource(DscResource[_CliTestSchema]):
    schema_provider = DataclassSchemaProvider(_CliTestSchema)
    def get(self, instance): return instance


@dsc_resource(type="Cli/Full", version="0.9.0",
              set_return=SetReturn.STATE_AND_DIFF, test_return=TestReturn.STATE_AND_DIFF)
class _CliFullResource(DscResource[_CliTestSchema]):
    schema_provider = DataclassSchemaProvider(_CliTestSchema)
    def get(self, i): return i
    def set(self, i): return SetResult(actual_state=i, changed_properties=[])
    def test(self, i): return TestResult(actual_state=i, differing_properties=[])
    def delete(self, i): pass
    def export(self, i): return iter([])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_pyproject(tmp_path: Path, entry_points: dict[str, str]) -> Path:
    ep_lines = "\n".join(f'"{k}" = "{v}"' for k, v in entry_points.items())
    content = f"""\
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "test-pkg"
version = "0.1.0"

[project.entry-points."microsoft.dsc.resources"]
{ep_lines}
"""
    p = tmp_path / "pyproject.toml"
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Tests using _generate_manifest directly (no filesystem import needed)
# ---------------------------------------------------------------------------

class TestDscGenManifest:
    def test_manifest_type(self):
        from ms_dsc.cli import _generate_manifest
        manifest = _generate_manifest("Cli/GetOnly", _CliGetOnlyResource)
        assert manifest["type"] == "Cli/GetOnly"

    def test_manifest_version(self):
        from ms_dsc.cli import _generate_manifest
        manifest = _generate_manifest("Cli/GetOnly", _CliGetOnlyResource)
        assert manifest["version"] == "1.2.3"

    def test_manifest_description(self):
        from ms_dsc.cli import _generate_manifest
        manifest = _generate_manifest("Cli/GetOnly", _CliGetOnlyResource)
        assert manifest["description"] == "CLI test get-only resource"

    def test_manifest_kind(self):
        from ms_dsc.cli import _generate_manifest
        manifest = _generate_manifest("Cli/GetOnly", _CliGetOnlyResource)
        assert manifest["kind"] == "resource"

    def test_manifest_require_adapter(self):
        from ms_dsc.cli import _generate_manifest
        manifest = _generate_manifest("Cli/GetOnly", _CliGetOnlyResource)
        assert manifest["requireAdapter"] == "Microsoft.Adapter/Python"

    def test_manifest_schema_embedded(self):
        from ms_dsc.cli import _generate_manifest
        manifest = _generate_manifest("Cli/GetOnly", _CliGetOnlyResource)
        assert "schema" in manifest
        assert manifest["schema"]["embedded"]["type"] == "object"

    def test_manifest_path_is_string(self):
        from ms_dsc.cli import _generate_manifest
        manifest = _generate_manifest("Cli/GetOnly", _CliGetOnlyResource)
        assert isinstance(manifest["path"], str)
        assert len(manifest["path"]) > 0

    def test_get_only_capabilities(self):
        from ms_dsc.cli import _generate_manifest
        manifest = _generate_manifest("Cli/GetOnly", _CliGetOnlyResource)
        assert manifest["capabilities"] == ["get"]

    def test_full_capabilities(self):
        from ms_dsc.cli import _generate_manifest
        manifest = _generate_manifest("Cli/Full", _CliFullResource)
        for cap in ("get", "set", "test", "delete", "export"):
            assert cap in manifest["capabilities"]

    def test_schema_url(self):
        from ms_dsc.cli import _generate_manifest
        manifest = _generate_manifest("Cli/GetOnly", _CliGetOnlyResource)
        assert "adaptedresource" in manifest["$schema"]

    def test_set_return_not_in_manifest_top_level(self):
        """set_return/test_return are NOT top-level keys in the manifest."""
        from ms_dsc.cli import _generate_manifest
        manifest = _generate_manifest("Cli/Full", _CliFullResource)
        assert "set_return" not in manifest
        assert "test_return" not in manifest


class TestDscGenMainCommand:
    def test_returns_1_when_pyproject_missing(self, tmp_path):
        from ms_dsc.cli import main
        rc = main(["manifest", "--pyproject", str(tmp_path / "does_not_exist.toml")])
        assert rc == 1

    def test_returns_1_when_no_entry_points(self, tmp_path):
        from ms_dsc.cli import main
        p = tmp_path / "pyproject.toml"
        p.write_text("[project]\nname = 'empty'\nversion = '0.0.0'\n")
        rc = main(["manifest", "--out", str(tmp_path / "out"), "--pyproject", str(p)])
        assert rc == 1

    def test_writes_file_for_valid_entry_point(self, tmp_path):
        from ms_dsc.cli import main

        ep = f"unit.test_cli:_CliGetOnlyResource"
        pyproject = _write_pyproject(tmp_path, {"Cli/GetOnly": ep})
        out_dir = tmp_path / "out"
        rc = main(["manifest", "--out", str(out_dir), "--pyproject", str(pyproject)])
        assert rc == 0
        assert (out_dir / "Cli.GetOnly.dsc.adaptedResource.json").exists()

    def test_filename_uses_dot_separator(self, tmp_path):
        from ms_dsc.cli import main

        ep = f"unit.test_cli:_CliGetOnlyResource"
        pyproject = _write_pyproject(tmp_path, {"Cli/GetOnly": ep})
        out_dir = tmp_path / "out"
        main(["manifest", "--out", str(out_dir), "--pyproject", str(pyproject)])
        assert (out_dir / "Cli.GetOnly.dsc.adaptedResource.json").exists()

