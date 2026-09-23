"""Worker-instance sweep: silent workers become ``gone`` (CONTRACTS-V2 §1.2).

A row whose last heartbeat (or registration, if it never heartbeated) is older
than :data:`~lkap_api.fleet.registry.STALE_AFTER` — three missed heartbeats —
is marked ``gone``. ``gone`` rows older than :data:`PRUNE_GONE_AFTER` are
deleted, so pid churn in dev does not grow the table without bound.

:func:`sweep_loop` runs in the api process. It is started by the lifespan of
:mod:`lkap_api.routers.fleet_internal`, which FastAPI nests inside the
application's own lifespan (the database exists by the time it starts).
The same pass is also registered as the ``worker_sweep`` job kind
(:data:`lkap_api.jobs.kinds.WORKER_SWEEP`) for deployments that schedule it
through the jobs backend instead; it is idempotent, so both may run.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import Any, cast

from sqlalchemy import and_, delete, or_, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.db.models import WorkerInstance, utcnow
from lkap_api.db.session import Database
from lkap_api.fleet.registry import STALE_AFTER
from lkap_api.jobs.context import JobContext
from lkap_api.jobs.kinds import WORKER_SWEEP
from lkap_api.jobs.registry import job
from lkap_api.logging import get_logger

log = get_logger(__name__)

#: ``gone`` rows are deleted after this long.
PRUNE_GONE_AFTER = dt.timedelta(hours=24)

#: Seconds between two sweep passes.
SWEEP_INTERVAL_S = 30.0


def _last_seen_before(cutoff: dt.datetime) -> Any:
    return or_(
        and_(WorkerInstance.last_heartbeat_at.is_not(None), WorkerInstance.last_heartbeat_at < cutoff),
        and_(WorkerInstance.last_heartbeat_at.is_(None), WorkerInstance.registered_at < cutoff),
    )


async def sweep_workers(session: AsyncSession, *, now: dt.datetime | None = None) -> tuple[int, int]:
    """Mark stale rows ``gone`` and prune old ``gone`` rows.

    Args:
        session: Open session; the caller commits.
        now: Injected clock for tests.

    Returns:
        ``(marked_gone, pruned)``.
    """
    ts = now or utcnow()
    marked = await session.execute(
        update(WorkerInstance)
        .where(WorkerInstance.status != "gone", _last_seen_before(ts - STALE_AFTER))
        .values(status="gone")
    )
    pruned = await session.execute(
        delete(WorkerInstance).where(
            WorkerInstance.status == "gone", _last_seen_before(ts - PRUNE_GONE_AFTER)
        )
    )
    marked_n = cast(CursorResult[Any], marked).rowcount or 0
    pruned_n = cast(CursorResult[Any], pruned).rowcount or 0
    if marked_n or pruned_n:
        log.info("worker_instances_swept", marked_gone=marked_n, pruned=pruned_n)
    return marked_n, pruned_n


async def sweep_once(db: Database, *, now: dt.datetime | None = None) -> tuple[int, int]:
    """Run :func:`sweep_workers` in its own committed transaction."""
    async with db.session() as session:
        return await sweep_workers(session, now=now)


@job(WORKER_SWEEP)
async def run_worker_sweep_job(ctx: JobContext, payload: dict[str, Any]) -> None:
    """``worker_sweep`` job handler: one :func:`sweep_once` pass (the payload is ignored)."""
    await sweep_once(ctx.database)


async def sweep_loop(db: Database, interval_s: float = SWEEP_INTERVAL_S) -> None:
    """Sweep forever; a failed pass is logged and never ends the loop."""
    while True:
        try:
            await sweep_once(db)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a sweep failure must never kill the loop
            log.exception("worker_instances_sweep_failed")
        await asyncio.sleep(interval_s)
