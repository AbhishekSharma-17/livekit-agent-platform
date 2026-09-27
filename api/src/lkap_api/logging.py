"""structlog bootstrap for `lkap_api`.

Call `configure_logging()` once at process startup (before the first log
call). No `print()` anywhere in this codebase — use `structlog.get_logger()`.
"""

from __future__ import annotations

import logging
import sys
from typing import cast

import structlog

from lkap_api.mcp_oauth.logsafe import redact_oauth_fields


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
        # S5-32: the backstop for a future log call that passes a token, code or state.
        redact_oauth_fields,
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
    # V2-21: httpx/httpcore log every request url at INFO/DEBUG (webhook urls and
    # tool dry-run urls can carry tokens); keep only their warnings.
    quiet_http_client_loggers()
    install_access_log_filter()


#: Paths whose query string carries a single-use or signing value (S5-32): the MCP
#: sign-in callback (``code``, ``state``), the Composio callback (``flow``,
#: ``connected_account_id``). Matched anywhere in the path, so a ``--root-path`` prefix
#: does not hide them. Signed session-file links (``.../assets/{id}/content?exp&sig``)
#: are matched by :func:`sensitive_query_path` too.
SENSITIVE_QUERY_PATHS: tuple[str, ...] = ("/v1/oauth/mcp/callback", "/v1/tool-providers/composio/callback")


def sensitive_query_path(path: str) -> bool:
    """Whether an access-log path's query must be dropped (see :data:`SENSITIVE_QUERY_PATHS`)."""
    bare = path.split("?", 1)[0]
    if any(marker in bare for marker in SENSITIVE_QUERY_PATHS):
        return True
    return "/assets/" in bare and bare.rstrip("/").endswith("/content")


class AccessLogQueryFilter(logging.Filter):
    """Replaces the query string of sensitive paths in uvicorn access-log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Rewrite the path argument in place; never drops the record."""
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            path = args[2]
            if "?" in path and sensitive_query_path(path):
                record.args = (*args[:2], path.split("?", 1)[0] + "?[redacted]", *args[3:])
        return True


def install_access_log_filter() -> None:
    """Attach :class:`AccessLogQueryFilter` to ``uvicorn.access`` (idempotent)."""
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(existing, AccessLogQueryFilter) for existing in logger.filters):
        logger.addFilter(AccessLogQueryFilter())


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
