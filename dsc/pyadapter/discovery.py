# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""
Adapter list operation and cache management for the Python DSC adapter.

Two discovery paths:

  1. Pre-built manifests (fast, preferred)
     The discovery *extension* (extensions/python/python.discover.py) handles
     this path by scanning installed distribution data files.  The adapter list
     command covers only resources that have NO pre-built manifest — packages
     that registered entry points but did not run `dsc-gen manifest`.

  2. Runtime entry-point enumeration (fallback)
     Iterates importlib.metadata entry_points(group="microsoft.dsc.resources"),
     loads each class, introspects capabilities and schema, then emits a DSC
     list entry.  Results are cached by distribution fingerprint.
"""
from __future__ import annotations

import importlib.metadata
import json
import logging
from pathlib import Path

from pyadapter.cache import DISCOVER_CACHE, LIST_CACHE, dist_fingerprint

_logger = logging.getLogger(__name__)

_MANIFEST_SUFFIXES = (
    ".dsc.adaptedresource.json",
    ".dsc.resource.json",
)

_ADAPTER_TYPE = "Microsoft.Adapter/Python"


def _covered_types() -> set[str]:
    """
    Return the set of resource type names (lower-cased) that already have a
    pre-built adapted resource manifest on disk.
    """
    covered: set[str] = set()
    fingerprint = dist_fingerprint()
    manifests = DISCOVER_CACHE.load(fingerprint)

    if manifests is None:
        manifests = _scan_distributions()
        DISCOVER_CACHE.save(fingerprint, manifests)

    for entry in manifests:
        path = Path(entry["manifestPath"])
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if resource_type := data.get("type"):
                covered.add(resource_type.casefold())
        except Exception as exc:
            _logger.debug("Could not read manifest %s: %s", path, exc)
    return covered


def _scan_distributions() -> list[dict]:
    """Scan distributions for DSC manifest data files (regular and editable installs)."""
    results: list[dict] = []
    for dist in importlib.metadata.distributions():
        results.extend(_scan_single_dist(dist))
    return results


def _scan_single_dist(dist) -> list[dict]:
    """Return manifest entries from a single distribution."""
    found: list[dict] = []

    # Regular install: check dist.files (tracked in RECORD)
    if dist.files:
        for f in dist.files:
            if any(str(f).lower().endswith(s) for s in _MANIFEST_SUFFIXES):
                try:
                    abs_path = dist.locate_file(f).resolve()
                    if abs_path.exists():
                        found.append({"manifestPath": str(abs_path)})
                except Exception as exc:
                    _logger.debug("Failed to locate %s: %s", f, exc)

    # Editable install: scan source directory via direct_url.json
    if not found:
        found.extend(_scan_editable_source(dist))

    return found


def _scan_editable_source(dist) -> list[dict]:
    """For editable installs, locate the source directory and scan for manifests."""
    try:
        import json as _json
        import urllib.request
        from pathlib import Path as _Path

        if not dist.files:
            return []
        url_file = next((f for f in dist.files if "direct_url.json" in str(f)), None)
        if url_file is None:
            return []

        data = _json.loads(dist.locate_file(url_file).read_text(encoding="utf-8"))
        if not data.get("dir_info", {}).get("editable", False):
            return []

        url: str = data.get("url", "")
        if url.startswith("file:///"):
            source_dir = _Path(urllib.request.url2pathname(url[7:]))
        elif url.startswith("file://"):
            source_dir = _Path(url[7:])
        else:
            return []

        if not source_dir.is_dir():
            return []

        results = []
        for suffix in _MANIFEST_SUFFIXES:
            for manifest_path in source_dir.rglob("*" + suffix):
                results.append({"manifestPath": str(manifest_path.resolve())})
        return results
    except Exception as exc:
        _logger.debug("Editable scan failed for %s: %s", getattr(dist, "name", "?"), exc)
        return []


def _capabilities_for(cls: type) -> list[str]:
    """Determine DSC capabilities by checking Protocol membership."""
    caps: list[str] = []
    try:
        from ms_dsc.protocols import Deletable, Exportable, Gettable, Settable, Testable

        probe = object.__new__(cls)
        if isinstance(probe, Gettable):
            caps.append("get")
        if isinstance(probe, Settable):
            caps.append("set")
        if isinstance(probe, Testable):
            caps.append("test")
        if isinstance(probe, Deletable):
            caps.append("delete")
        if isinstance(probe, Exportable):
            caps.append("export")
    except ImportError:
        _logger.debug("ms-dsc not available; falling back to hasattr capability detection")
        probe = object.__new__(cls)
        for cap in ("get", "set", "test", "delete", "export"):
            if callable(getattr(probe, cap, None)):
                caps.append(cap)
    return caps


def _build_list_entry(type_name: str, cls: type) -> dict:
    """Generate a DSC adapter list entry from a resource class."""
    metadata = getattr(cls, "__dsc_metadata__", None)

    version = metadata.version if metadata else "0.0.0"
    description = metadata.description if metadata else ""
    capabilities = _capabilities_for(cls)

    # Build content metadata (module + class) for portable resource resolution
    content: dict = {
        "module": cls.__module__,
        "class": cls.__qualname__,
    }

    entry: dict = {
        "type": type_name,
        "kind": "resource",
        "version": version,
        "capabilities": capabilities,
        "requireAdapter": _ADAPTER_TYPE,
        "content": content,
    }
    if description:
        entry["description"] = description

    if hasattr(cls, "get_schema") and callable(cls.get_schema):
        try:
            schema = cls.get_schema()
            if schema:
                entry["schema"] = {"embedded": schema}
        except Exception as exc:
            _logger.debug("Could not generate schema for %s: %s", type_name, exc)

    return entry


def _list_from_entry_points(covered: set[str]) -> list[dict]:
    """Enumerate entry points and return list entries for uncovered resources."""
    results: list[dict] = []
    try:
        eps = importlib.metadata.entry_points(group="microsoft.dsc.resources")
    except Exception as exc:
        _logger.error("Failed to enumerate entry points: %s", exc)
        return results

    for ep in eps:
        if ep.name.casefold() in covered:
            _logger.debug("Skipping %s — covered by pre-built manifest", ep.name)
            continue
        try:
            cls = ep.load()
            results.append(_build_list_entry(ep.name, cls))
            _logger.debug("Listed %s from entry point", ep.name)
        except Exception as exc:
            _logger.warning("Failed to load entry point %s: %s", ep.name, exc)
    return results


def cmd_discover() -> int:
    """
    Emit pre-built manifest paths as NDJSON ({"manifestPath": "..."}).
    This mirrors what extensions/python/python.discover.py does but is
    available directly through the adapter for convenience.
    """
    fingerprint = dist_fingerprint()
    manifests = DISCOVER_CACHE.load(fingerprint)
    if manifests is None:
        manifests = _scan_distributions()
        DISCOVER_CACHE.save(fingerprint, manifests)

    for entry in manifests:
        print(json.dumps(entry), flush=True)
    return 0


def cmd_list() -> int:
    """
    Emit list entries for resources NOT covered by pre-built manifests.
    Results are cached; cache is invalidated when the installed package set changes.
    """
    fingerprint = dist_fingerprint()
    entries = LIST_CACHE.load(fingerprint)
    if entries is None:
        covered = _covered_types()
        entries = _list_from_entry_points(covered)
        LIST_CACHE.save(fingerprint, entries)

    for entry in entries:
        print(json.dumps(entry), flush=True)
    return 0


def cmd_clear_cache() -> int:
    DISCOVER_CACHE.clear()
    LIST_CACHE.clear()
    _logger.info("Python DSC adapter caches cleared")
    return 0
