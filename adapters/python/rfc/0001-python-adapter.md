# RFC 0001 — Microsoft.Adapter/Python: Python DSC Resource Adapter

| Field | Value |
|-------|-------|
| Status | Draft |
| Created | 2026-07-02 |
| Component | `adapters/python/` |

---

## Summary

This RFC describes the design of the `Microsoft.Adapter/Python` DSC v3 adapter,
the `ms-dsc` Python SDK for resource authors, and the `Microsoft.Python/Discover`
discovery extension.  Together these components allow DSC resources to be written
in Python and discovered/invoked through the standard DSC engine pipeline.

---

## Motivation

DSC v3 has a well-defined adapter contract that enables resources implemented in
arbitrary languages to participate in the DSC configuration lifecycle.  Python is
a widely-used language for system automation, and a first-class Python adapter
lowers the barrier to writing portable DSC resources.  Key goals are:

1. **Zero friction** — resource authors install one package (`ms-dsc`) and follow
   familiar Python patterns (dataclasses, typing, logging).
2. **No mandatory Rust or .NET dependency** — the entire adapter runtime is pure
   Python, stdlib only; it ships alongside the DSC binary.
3. **Discoverable by default** — resources are auto-discovered without needing to
   maintain hand-written manifest files.
4. **Idiomatic Python** — the SDK leverages dataclasses, type hints, structural
   protocols, and entry points.

---

## Design

### Component overview

```
adapters/python/
├── pyadapter/                   # Adapter runtime — stdlib only, ships with DSC
│   ├── __main__.py              # Entry: python -m pyadapter <verb>
│   ├── cli.py                   # Argument parser + __main__ guard for -m invocation
│   ├── router.py                # Operation dispatch (get/set/test/delete/export)
│   ├── discovery.py             # list + discover commands
│   ├── cache.py                 # Fingerprint-keyed disk cache
│   ├── schema.py                # JSON Schema generation (delegates to ms_dsc.schema)
│   └── logging.py               # DscLogHandler → DSC JSON stderr
├── ms-dsc/                      # SDK — published to PyPI, installed by resource authors
│   ├── ms_dsc/
│   │   ├── resource.py          # DscResource[T] base class
│   │   ├── protocols.py         # Gettable / Settable / Testable / Deletable / Exportable
│   │   ├── metadata.py          # @dsc_resource decorator, SetReturn, TestReturn
│   │   ├── results.py           # SetResult[T], TestResult[T]
│   │   ├── schema/              # DataclassSchemaProvider, PydanticSchemaProvider
│   │   ├── build/hatchling.py   # Hatchling build hook (dsc-gen at wheel-build time)
│   │   ├── cli.py               # dsc-gen manifest command
│   │   └── logging.py           # DscLogHandler for SDK users
│   └── pyproject.toml
├── Microsoft.Adapter.Python.dsc.resource.json   # Adapter manifest (Windows)
├── Microsoft.Adapter.Python3.dsc.resource.json  # Adapter manifest (Linux/macOS)
└── tests/
    ├── unit/                    # 160+ pytest tests (no DSC required)
    ├── fixture/                 # dsc-test-resource: installable test package
    └── integration/             # Pester component + DSC CLI tests
```

```
extensions/python/
├── python.dsc.extension.json    # Discovery extension manifest
└── python.discover.py           # Scans Python distributions for manifests
```

### Separation of concerns

| Layer | Dependency | Purpose |
|-------|------------|---------|
| `pyadapter` | **ms-dsc** (bundled) | Invoked by DSC engine per operation |
| `ms-dsc` SDK | **zero mandatory runtime deps** | Used by resource authors |
| `Microsoft.Python/Discover` | **stdlib only** | Scans distributions at DSC startup |

The adapter runtime (`pyadapter`) requires the bundled `ms-dsc` SDK, which ships
alongside it in the DSC package. This simplifies the adapter code (no defensive
fallback patterns) and guarantees access to protocol introspection for capability
detection.

### Adapter invocation — module vs script

DSC invokes the adapter as a Python **module**, not a script path:

```
python -m pyadapter.cli <verb> [--resource TYPE]    # Windows
python3 -m pyadapter.cli <verb> [--resource TYPE]   # Linux/macOS
```

This is the standard Python `-m` invocation.  Python sets `sys.path[0] = ''`
(the current working directory) for module invocations.  DSC always sets the
working directory to the manifest's containing directory (the DSC install dir),
so both `pyadapter/` and the bundled `ms_dsc/` are importable via `sys.path[0]`
without any PYTHONPATH manipulation.

The legacy `python ./pyadapter/__main__.py <verb>` form is supported for
development and backward compatibility via `__main__.py`, but the DSC manifests
use the `-m` form.

### Platform manifests

Two adapter manifests cover cross-platform Python executable differences:

| Manifest | Platform(s) | Executable | Condition |
|----------|-------------|-----------|-----------|
| `Microsoft.Adapter.Python.dsc.resource.json` | Windows | `python` | `python` on PATH |
| `Microsoft.Adapter.Python3.dsc.resource.json` | Linux, macOS | `python3` | `python3` on PATH |

Both manifests declare the same resource type (`Microsoft.Adapter/Python`) and
identical argument structures.  The build system (`data.build.json`) includes the
appropriate manifest in each platform's package — they never appear together in a
single installation.

### ms_dsc bundling

The `ms_dsc` SDK is bundled into the DSC package alongside `pyadapter/` so that
user resources can `import ms_dsc` out-of-the-box, without a separate
`pip install ms-dsc`.

**Build-time copy** — `Copy-PythonAdapterSdk` in `helpers.build.psm1` copies
`adapters/python/ms-dsc/ms_dsc/` to `<artifact-bin>/ms_dsc/` at build time,
excluding:
- `build/` — the Hatchling build hook (not needed at adapter runtime)
- `__pycache__/` — compiled bytecache (regenerated on first run)

**Version source of truth** — `adapters/python/ms-dsc/` remains the canonical
source; the bundled copy is always generated from it.

**sys.path resolution** — because DSC sets CWD to the manifest directory when
invoking the adapter, Python's `-m` invocation places `''` (CWD) at `sys.path[0]`.
The bundled `ms_dsc/` is in that same directory, so `import ms_dsc` resolves to
the bundled version automatically.  If a user has a different version installed
via pip, the bundled version takes precedence (CWD precedes site-packages in
`sys.path`).

**Plugin pattern — resource dependencies** — Because DSC pre-loads bundled ms_dsc
before discovering resources, resource packages do NOT declare `ms-dsc` as a
runtime dependency.  Instead, `ms-dsc` is declared only in `[build-system] requires`
for schema generation, decorators, and IDE support:

```toml
[build-system]
requires = ["hatchling", "ms-dsc"]  # Build-time only

[project]
dependencies = []                    # Runtime: empty (ms-dsc provided by DSC)
```

This follows the plugin pattern used by pytest, Flask, and Jupyter: the framework
provides the SDK at runtime, and plugins don't declare it as a dependency.  Resources
can be safely installed outside DSC without forcing users to install the DSC SDK.

**Version compatibility** — The bundled ms_dsc version is recorded in the DSC
release notes.  Resource authors should ensure their code is compatible with the
bundled ms_dsc version shipped with DSC.  Breaking API changes require a MAJOR
version bump in ms-dsc and corresponding DSC release notes.

### Discovery pipeline

```
DSC engine startup
    │
    ├─► Microsoft.Python/Discover (extension)
    │       python extensions/python/python.discover.py
    │       • Uses importlib.resources to locate <pkg>.dsc/*.dsc.adaptedResource.json
    │       • Normalises distribution names (hyphens→underscores) for package lookup
    │       • Emits {"manifestPath": "<abs_path>"} per manifest found
    │       • No caching — importlib.resources lookup is fast at startup
    │
    └─► Microsoft.Adapter/Python → adapter.list (fallback)
            python -m pyadapter.cli list
            • Enumerates importlib.metadata entry_points(group="microsoft.dsc.resources")
            • Returns list entries for resources WITHOUT pre-built manifests
            • Results cached in ~/.dsc/PythonListCache.json
```

### Pre-loading ms_dsc for the plugin pattern

To enable the plugin pattern (resources without ms-dsc runtime dependencies), the
adapter must pre-load ms_dsc before discovering and loading resource entry points.

When the adapter's `list` command runs (`python -m pyadapter.cli list`):

1. The adapter immediately executes `import ms_dsc` (catching ImportError if the
   bundled copy is unavailable, which only occurs in non-DSC contexts).
2. This populates `sys.modules['ms_dsc']`, making it available to all subsequently
   loaded resource modules.
3. Only then does the adapter enumerate `importlib.metadata.entry_points(group="microsoft.dsc.resources")`
   and load resource entry points via `ep.load()`.
4. Each resource module (e.g., `from ms_dsc import DscResource, SetResult`)
   imports from the already-populated `sys.modules['ms_dsc']` without declaring
   a runtime dependency.

This early import ensures that resource classes can use `from ms_dsc import ...`
at module load time, even though `ms-dsc` is not in their `dependencies`.

### Operation dispatch (content-based resolution)

When DSC invokes the adapter for a resource operation, it injects the `content`
field from the adapted resource manifest via `adaptedContentArg`:

```
python -m pyadapter.cli get --resource TYPE --content '{"module":"pkg.res","class":"Cls"}'
```

The adapter's `_resolve_class` uses `importlib.import_module(module)` + `getattr(mod, class_name)`
for direct, fast resolution — no entry-point scanning required.  For resources discovered
via `adapter.list` (no pre-built manifest), the `--content` argument is absent and the
adapter falls back to `importlib.metadata.entry_points` lookup.

This two-tier approach means:

| Resource type | Discovery | Dispatch |
|---|---|---|
| Pre-built manifest (pip wheel or bundled) | `importlib.resources` / DSC bin scan | `importlib.import_module` via `adaptedContentArg` |
| Dev install, no manifest | `adapter.list` (entry_points) | entry_points fallback |

### Operation dispatch

For each DSC operation, the adapter:

1. Reads `--resource TYPE` from argv.
2. Resolves the resource class from `importlib.metadata.entry_points(group="microsoft.dsc.resources")`, case-insensitively.
3. Reads JSON from stdin.
4. Constructs the schema instance (dataclass or Pydantic model) from JSON, discarding unknown fields.
5. Dispatches to the matching Protocol method (`get`, `set`, `test`, `delete`, `export`).
6. Serialises the result to stdout following the DSC adapter contract.

```
stdin (JSON)  →  schema instance (T)  →  resource.method(instance)  →  stdout (NDJSON)
```

stdout contract:

| Operation | stdout lines |
|-----------|-------------|
| `get` | 1 — actual state JSON |
| `set` (state) | 1 — actual state JSON |
| `set` (stateAndDiff) | 2 — actual state, then `["prop", ...]` |
| `test` (state) | 1 — actual state JSON |
| `test` (stateAndDiff) | 2 — actual state, then `["prop", ...]` |
| `delete` | 0 |
| `export` | 0..N — one JSON line per instance |

### SDK design

Resource authors inherit from `DscResource[T]` and implement capability Protocols:

```python
from dataclasses import dataclass, field
from collections.abc import Iterator
from ms_dsc import DscResource, dsc_resource, SetResult, TestResult
from ms_dsc.metadata import SetReturn, TestReturn
from ms_dsc.schema import DataclassSchemaProvider

@dataclass
class GreetingSchema:
    name: str = field(metadata={"description": "Name to greet."})
    message: str = field(default="", metadata={"description": "Greeting message."})

@dsc_resource(
    type="Example/Greeting",
    version="1.0.0",
    set_return=SetReturn.STATE_AND_DIFF,
    test_return=TestReturn.STATE_AND_DIFF,
)
class GreetingResource(DscResource[GreetingSchema]):
    schema_provider = DataclassSchemaProvider(GreetingSchema)

    def get(self, instance: GreetingSchema) -> GreetingSchema:
        return GreetingSchema(name=instance.name, message=f"Hello, {instance.name}!")

    def set(self, instance: GreetingSchema) -> SetResult[GreetingSchema]:
        actual = self.get(instance)
        changed = [f for f in ("message",) if getattr(actual, f) != getattr(instance, f)]
        return SetResult(actual_state=actual, changed_properties=changed)

    def test(self, instance: GreetingSchema) -> TestResult[GreetingSchema]:
        actual = self.get(instance)
        diffs = [f for f in ("message",) if getattr(actual, f) != getattr(instance, f)]
        return TestResult(actual_state=actual, differing_properties=diffs)

    def export(self, instance: GreetingSchema | None) -> Iterator[GreetingSchema]:
        for name in ("Alice", "Bob"):
            yield self.get(GreetingSchema(name=name))
```

#### Schema type: dataclass vs Pydantic

Both are supported.  Dataclasses are preferred for resources that want zero
dependencies (beyond `ms-dsc` itself).  Pydantic is supported for resources that
already use it or need advanced validation.

| | Dataclass | Pydantic |
|--|-----------|---------|
| Extra deps | None | `pydantic` |
| Provider | `DataclassSchemaProvider` | `PydanticSchemaProvider` |
| Schema draft | JSON Schema draft-07 | JSON Schema draft-07 via Pydantic |
| Validation | Adapter silently ignores unknown fields | Pydantic validates at construction |

#### Capability Protocols

Capabilities are declared structurally — no explicit inheritance needed:

```python
class MyResource(DscResource[MySchema]):
    # Implementing get() makes this instance of Gettable
    def get(self, instance): ...
    # Implementing set() makes this instance of Settable
    def set(self, instance): ...
```

The `@runtime_checkable` Protocols allow `isinstance()` checks without inheritance,
which the adapter and `dsc-gen` use to determine which capabilities to advertise.

#### Metadata and return modes

`@dsc_resource(set_return=SetReturn.STATE_AND_DIFF)` controls whether `set()` emits
one or two stdout lines.  This maps to DSC's `return: stateAndDiff` manifest field.

When `set_return=SetReturn.STATE` (default), the adapter emits only the actual
state.  When `STATE_AND_DIFF`, the adapter additionally emits a JSON array of
changed property names.  `set()` MUST populate `SetResult.changed_properties`
with a list (possibly empty) when `STATE_AND_DIFF` is active.

### `dsc-gen manifest` — manifest generation

The `dsc-gen manifest` CLI reads `pyproject.toml`, imports each resource class,
and writes one `*.dsc.adaptedResource.json` per resource.  The files are included
in the wheel as package data, enabling the discovery extension to locate them
without importing Python.

**At build time** (Hatchling build hook):

```toml
[build-system]
requires = ["hatchling", "ms-dsc"]
build-backend = "hatchling.build"

[tool.hatch.build.hooks.dsc]
# output_dir = "my_package/dsc"  # defaults to <package_name>/dsc/
```

**Manually:**

```bash
cd my-resource-package/
dsc-gen manifest
```

### Logging

The DSC engine reads structured JSON from adapter stderr:

```json
{"info": "file_presence.resource: Getting /tmp/hello.txt"}
```

Resource authors use Python's standard `logging` module — no DSC-specific API:

```python
import logging
logger = logging.getLogger(__name__)
logger.info("Getting %s", path)
```

The adapter installs `DscLogHandler` on the root logger before dispatching any
operation, translating Python log records to DSC's JSON format.  Log verbosity is
controlled by `DSC_TRACE_LEVEL` (`trace`/`debug`/`info`/`warn`/`error`).

### Cache design

The adapter's `list` fallback uses a JSON cache keyed by a fingerprint of all
installed distributions (`name==version|...` sorted).  When the fingerprint
changes (package installed/upgraded/removed), the cache is automatically
invalidated and rebuilt on the next invocation.  An additional path-existence
check ensures stale entries from removed packages are evicted.

Cache location:
- Windows: `%LOCALAPPDATA%\.dsc\PythonListCache.json`
- Linux/macOS: `~/.dsc/PythonListCache.json`

---

## Alternatives considered

### Alternative A: Single Python file adapter (no package structure)

A single-file adapter is simpler to ship but limits testability and extensibility.
Rejected in favour of the package-based `pyadapter/` structure which:
- Enables unit testing of individual components.
- Keeps cache, discovery, router, and logging concerns separate.
- Allows the discovery extension (`python.discover.py`) to share cache logic.

### Alternative B: Require Pydantic for all resources

Pydantic provides excellent runtime validation.  Rejected as a hard requirement
because many resources are simple and don't need Pydantic's overhead.  Pydantic
remains an optional, fully-supported schema backend.
