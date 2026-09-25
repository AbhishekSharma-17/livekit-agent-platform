"""The creation-time estimate snapshot (COSTS.md D-V4-43, §4.2).

:func:`attach_estimate` runs as a FastAPI background task **after** the
session-creation response is committed, at the three creation sites
(``routers/connect.py``, ``routers/text_sessions.py``, ``routers/internal.py``).
It opens its own session on the process-wide :class:`Database`, re-reads the
row, resolves the config the row pins (``config_version``, like
:func:`~lkap_api.costs.service.config_for_session`) and stores the estimate
with the workspace's (10-minute-cached) averages and prices. Idempotent: a row
that already carries an estimate is left alone. A failure is logged and never
reaches the caller — a session is never refused for want of an estimate, and a
sub-second session whose summary lands first simply shows "no estimate".
"""

from __future__ import annotations

from lkap_contracts.api_models import CostEstimate
from sqlalchemy import select

from lkap_api.costs.assumptions import default_assumptions, merge, workspace_assumptions
from lkap_api.costs.estimate import build_estimate
from lkap_api.costs.prices import load_price_book, pipeline_refs
from lkap_api.costs.service import config_for_session, estimate_channel, session_sip_model
from lkap_api.db.guard import CROSS_WORKSPACE_OPTION
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.session import Database
from lkap_api.logging import get_logger

log = get_logger(__name__)


def trim(estimate: CostEstimate) -> dict[str, object]:
    """The stored form: per-minute figures, lines, assumptions, ``price_version``, ``as_of``.

    Quote provenance on each line is kept (the comparison needs the unit price),
    minus the long caveat sentences, which the read path does not use.
    """
    return estimate.model_dump(mode="json", exclude={"caveats"})


def embedding_provider_id(embedder: str) -> str:
    """The registry id behind ``LKAP_EMBEDDER`` (``fastembed`` → ``fastembed-embedding``)."""
    head = embedder.split(":", 1)[0].strip()
    if head in ("", "fastembed"):
        return "fastembed-embedding"
    if head == "openai":
        return "openai-embedding"
    return head


async def attach_estimate(database: Database, session_id: str, *, embedder: str = "fastembed") -> None:
    """Snapshot the session's estimate at its pinned config version (never raises).

    Args:
        database: The process-wide database (the request's session is closed by now).
        session_id: The session just created.
        embedder: `Settings.embedder` (`LKAP_EMBEDDER`), naming the knowledge embedder to price.
    """
    try:
        async with database.session() as db:
            row = (
                await db.execute(
                    select(SessionRow)
                    .where(SessionRow.id == session_id)
                    .execution_options(**{CROSS_WORKSPACE_OPTION: True})
                )
            ).scalar_one_or_none()
            if row is None or row.estimate is not None:
                return
            config = await config_for_session(db, row)
            if config is None:
                log.warning("session_estimate_no_config", session_id=session_id)
                return
            averages = await workspace_assumptions(db, row.workspace_id)
            assumptions = merge(default_assumptions(config), averages.assumptions)
            book = await load_price_book(
                db, row.workspace_id, pipeline_refs(config.pipeline, [config.qa.model])
            )
            channel = estimate_channel(row.channel)
            estimate = build_estimate(
                config,
                assumptions,
                book,
                channel=channel,
                sip_model=await session_sip_model(db, row) if channel == "phone" else "trunk",
                embedding_provider=embedding_provider_id(embedder),
            )
            row.estimate = trim(estimate)
        log.info(
            "session_estimate_attached",
            session_id=session_id,
            per_minute_usd=str(estimate.per_minute_usd.mid) if estimate.per_minute_usd else None,
        )
    except Exception as exc:  # noqa: BLE001 - a snapshot must never break a session
        log.warning("session_estimate_failed", session_id=session_id, error=type(exc).__name__)
