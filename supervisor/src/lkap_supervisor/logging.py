"""structlog bootstrap for ``lkap_supervisor`` (same shape as the api's).

Nothing in this package ever passes a worker environment, a LiveKit secret or
the service token to a logger; only ids, counts and states.
"""

from __future__ import annotations

import logging
import sys
from typing import cast

import structlog


def configure_logging(*, level: str = "INFO", json_output: bool = False) -> None:
    """Configure stdlib logging + structlog for the process.

    Args:
        level: Python logging level name, e.g. ``INFO``.
        json_output: JSON lines (containers) instead of the console renderer.
    """
    log_level = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
    shared: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer() if json_output else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        cache_logger_on_first_use=False,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
    )
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(log_level)
    # httpx logs every request at INFO (a line per poll); keep only its warnings.
    logging.getLogger("httpx").setLevel(max(log_level, logging.WARNING))


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger, optionally named after the module."""
    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(name))
