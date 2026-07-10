# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""
Python DSC adapter — logging support.

Installs a logging.Handler that writes structured DSC JSON trace messages to
stderr. Resource authors use the standard logging module; the handler translates
log records into the format DSC expects:

    {"info": "message"}
    {"error": "message"}
    {"warn": "message"}
    {"debug": "message"}

Usage (resource authors):

    import logging
    logger = logging.getLogger(__name__)
    logger.info("Fetching current state for %s", name)
"""
from __future__ import annotations

import json
import logging
import os
import sys

_DSC_TO_PYTHON: dict[str, int] = {
    "trace": logging.DEBUG,
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "warn": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}

_PYTHON_TO_DSC: dict[int, str] = {
    logging.DEBUG: "debug",
    logging.INFO: "info",
    logging.WARNING: "warn",
    logging.ERROR: "error",
    logging.CRITICAL: "error",
}


class DscLogHandler(logging.Handler):
    """Formats log records as DSC JSON trace messages written to stderr."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = _PYTHON_TO_DSC.get(record.levelno, "info")
            message = self.format(record)
            print(json.dumps({level: message}), file=sys.stderr, flush=True)
        except Exception:
            self.handleError(record)


def configure_logging() -> None:
    """
    Configure the root logger to emit DSC-formatted JSON on stderr.

    Reads DSC_TRACE_LEVEL from the environment (trace/debug/info/warn/error/critical).
    Defaults to info if the variable is absent or unrecognised.
    """
    level_str = os.environ.get("DSC_TRACE_LEVEL", "info").lower()
    level = _DSC_TO_PYTHON.get(level_str, logging.INFO)

    handler = DscLogHandler()
    handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
    logging.basicConfig(handlers=[handler], level=level, force=True)
