# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""Python DSC adapter entry point.

When the adapter is bundled with DSC, it is invoked via ``python -m pyadapter.cli``
(see Microsoft.Adapter.Python.dsc.resource.json).  This file enables the alternate
invocation ``python -m pyadapter`` and handles the legacy script path
``python pyadapter/__main__.py``.

When invoked as a script (python pyadapter/__main__.py …) Python inserts the
script's directory (pyadapter/) as sys.path[0].  This shadows the standard-library
'logging' module with our own pyadapter/logging.py.  Remove the script directory
and replace it with the adapter root so that ``from pyadapter.*`` package imports
work correctly.
"""
import sys
from pathlib import Path

_script_dir = Path(__file__).parent.resolve()
_adapter_root = _script_dir.parent.resolve()

if str(_script_dir) in sys.path:
    sys.path.remove(str(_script_dir))
if str(_adapter_root) not in sys.path:
    sys.path.insert(0, str(_adapter_root))

from pyadapter.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
