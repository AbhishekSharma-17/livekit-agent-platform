"""Job kind → handler registry.

Mirrors the `config_service.VALIDATORS` hook-list pattern already used in this
codebase: a package that owns a job's business logic (`kb.ingest`,
`webhooks.delivery`, `qa.job`, and this package's own `usage_daily_rollup`)
imports :func:`job` and decorates its handler, instead of a central module
importing every package's internals. A kind reserved by another package
(`jobs.kinds.CONNECTION_PROBE` etc.) simply has no handler here until that
package lands and registers one.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from lkap_api.jobs.context import JobContext

JobHandler = Callable[[JobContext, dict[str, object]], Awaitable[None]]

_HANDLERS: dict[str, JobHandler] = {}


def job(kind: str) -> Callable[[JobHandler], JobHandler]:
    """Register the decorated async function as the handler for `kind`.

    Args:
        kind: A job kind name (see `lkap_api.jobs.kinds`).

    Returns:
        A decorator that registers and returns the function unchanged.
    """

    def _decorate(fn: JobHandler) -> JobHandler:
        _HANDLERS[kind] = fn
        return fn

    return _decorate


def get_handler(kind: str) -> JobHandler | None:
    """Return the handler registered for `kind`, or `None` if none is."""
    return _HANDLERS.get(kind)


def registered_kinds() -> frozenset[str]:
    """Return every kind with a registered handler (test/introspection use)."""
    return frozenset(_HANDLERS)


def unregister(kind: str) -> None:
    """Remove one kind's handler (test isolation — leaves every other kind registered)."""
    _HANDLERS.pop(kind, None)


def clear_registry() -> None:
    """Remove every registered handler (test isolation only; rarely what you want —

    most tests should call :func:`unregister` for the one kind they added, so
    other test modules' handlers (`kb_ingest`, `webhook_delivery`, `qa_scoring`,
    `usage_daily_rollup`) registered earlier in the same pytest process stay put.
    """
    _HANDLERS.clear()
