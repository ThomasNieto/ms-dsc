# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""
Python DSC discovery extension.

Scans installed Python distributions for DSC resource manifest files and emits
their absolute paths as NDJSON to stdout for the DSC engine to load.

Each output line is: {"manifestPath": "/absolute/path/to/manifest.json"}

Cache: ~/.dsc/PythonDiscoverCache.json (Linux/macOS)
       %LocalAppData%\\dsc\\PythonDiscoverCache.json (Windows)
"""
from __future__ import annotations

import json
import os
import sys
from importlib.metadata import distributions
from pathlib import Path

_MANIFEST_SUFFIXES = (
    ".dsc.adaptedresource.json",
    ".dsc.resource.json",
)


def _cache_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LocalAppData") or Path.home())
    else:
        base = Path.home()
    return base / ".dsc" / "PythonDiscoverCache.json"


def _dist_fingerprint() -> str:
    """Fingerprint of all installed distributions as sorted name==version pairs."""
    return "|".join(sorted(f"{d.name}=={d.version}" for d in distributions()))


def _scan_distributions() -> list[dict[str, str]]:
    """Scan installed distributions for DSC manifest data files."""
    results: list[dict[str, str]] = []
    for dist in distributions():
        results.extend(_scan_single_dist(dist))
    return results


def _scan_single_dist(dist) -> list[dict[str, str]]:
    """Return manifest paths from a single distribution (regular or editable)."""
    found: list[dict[str, str]] = []

    # --- Regular install: check dist.files (tracked in RECORD) ---
    if dist.files:
        for f in dist.files:
            if any(str(f).lower().endswith(s) for s in _MANIFEST_SUFFIXES):
                try:
                    abs_path = dist.locate_file(f).resolve()
                    if abs_path.exists():
                        found.append({"manifestPath": str(abs_path)})
                except Exception:
                    pass

    # --- Editable install: scan source directory via direct_url.json ---
    if not found:
        found.extend(_scan_editable_source(dist))

    return found


def _scan_editable_source(dist) -> list[dict[str, str]]:
    """For editable installs, find the source dir and scan it for manifests."""
    try:
        import json as _json

        # Locate direct_url.json in the dist-info files list.
        if not dist.files:
            return []
        url_file = next(
            (f for f in dist.files if "direct_url.json" in str(f)),
            None,
        )
        if url_file is None:
            return []

        data = _json.loads(dist.locate_file(url_file).read_text(encoding="utf-8"))
        if not data.get("dir_info", {}).get("editable", False):
            return []

        url: str = data.get("url", "")
        # Convert file:/// URL to a filesystem path (Windows and POSIX).
        if url.startswith("file:///"):
            import urllib.request
            source_dir = Path(urllib.request.url2pathname(url[7:]))
        elif url.startswith("file://"):
            source_dir = Path(url[7:])
        else:
            return []

        if not source_dir.is_dir():
            return []

        results = []
        for suffix in _MANIFEST_SUFFIXES:
            # Use rglob to find manifests anywhere under the source tree.
            for manifest_path in source_dir.rglob("*" + suffix):
                results.append({"manifestPath": str(manifest_path.resolve())})
        return results
    except Exception:
        return []


def _load_cache(cache_path: Path, fingerprint: str) -> list[dict[str, str]] | None:
    """Return cached manifests if valid, otherwise None."""
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        if data.get("fingerprint") != fingerprint:
            return None
        manifests: list[dict[str, str]] = data.get("manifests", [])
        # Verify every cached path still exists on disk.
        if all(Path(m["manifestPath"]).exists() for m in manifests):
            return manifests
    except Exception:
        pass
    return None


def _save_cache(cache_path: Path, fingerprint: str, manifests: list[dict[str, str]]) -> None:
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps({"fingerprint": fingerprint, "manifests": manifests}),
            encoding="utf-8",
        )
    except Exception:
        pass


def main() -> int:
    cache_path = _cache_path()
    fingerprint = _dist_fingerprint()

    manifests = _load_cache(cache_path, fingerprint)
    if manifests is None:
        manifests = _scan_distributions()
        _save_cache(cache_path, fingerprint, manifests)

    for entry in manifests:
        print(json.dumps(entry), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
