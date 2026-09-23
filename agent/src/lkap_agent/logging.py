"""structlog bootstrap for `lkap_agent`.

Call `configure_logging()` once at process startup (before the first log
call). No `print()` anywhere in this codebase — use `structlog.get_logger()`.
Session/job-scoped fields (session_id, agent_id, ...) are bound via
`structlog.contextvars` by `observability.py` (W1-AGENT-CORE), not here.
"""

from __future__ import annotations

import logging
import sys
from typing import cast

import structlog


def configure_logging(*, level: str = "INFO", json_output: bool = False) -> None:
    """Configure stdlib logging + structlog for the process.

    Args:
        level: Python logging level name, e.g. "INFO", "DEBUG".
        json_output: Render structured logs as JSON lines (production/containers)
            instead of a human-readable console format (local dev).
    """
    log_level = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)

    shared_processors: list[structlog.typing.Processor] = [
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
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
    )
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(log_level)
    # V2-21: httpx/httpcore log every request url at INFO/DEBUG, and a tool url can
    # carry a substituted `{{ secret.NAME }}`; keep only their warnings.
    quiet_http_client_loggers()


#: Third-party loggers that print full request urls (query strings included).
HTTP_CLIENT_LOGGERS: tuple[str, ...] = ("httpx", "httpcore", "hpack")


def quiet_http_client_loggers() -> None:
    """Raise the HTTP client libraries' loggers to WARNING, whatever the root level.

    Their INFO/DEBUG lines contain the full url, which is where a secret sits
    when a tool template substitutes one into a query string (V2-21).
    """
    for name in HTTP_CLIENT_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a structlog-bound logger, optionally named (e.g. by module)."""
    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(name))
