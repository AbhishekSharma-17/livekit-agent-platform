"""Logging for the MCP process: stderr only (stdout is the stdio wire), never a secret.

The handler scrubs every seen secret value from the rendered line and drops
``extra`` fields whose names look secret (``secret*``, ``*_key``, ``*_secret``,
``password``, ``token``, ``authorization``); the code never logs a value in the
first place, so this is defence in depth (D-V3-4).
"""

from __future__ import annotations

import logging
import re
import sys

from lkap_mcp.secrets import scrub_text

_SECRETISH = re.compile(r"^(secret.*|.*_key|.*_secret|password|token|authorization)$", re.IGNORECASE)
_STANDARD = frozenset(logging.makeLogRecord({}).__dict__) | {"message", "asctime", "taskName"}


class ScrubbingFormatter(logging.Formatter):
    """``time level logger event key=value …`` with secret-looking fields dropped and values scrubbed."""

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        extras = [
            f"{key}={value}"
            for key, value in record.__dict__.items()
            if key not in _STANDARD and not key.startswith("_") and not _SECRETISH.match(key)
        ]
        if extras:
            line = f"{line} {' '.join(extras)}"
        return scrub_text(line)


def configure_logging(level: str = "INFO") -> None:
    """Send every log record to stderr through :class:`ScrubbingFormatter`."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(ScrubbingFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # httpx logs full request urls at INFO; the client logs method/path/status itself.
    logging.getLogger("httpx").setLevel(logging.WARNING)
