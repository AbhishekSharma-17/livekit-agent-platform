"""`usage_daily` nightly rollup (CONTRACTS-V2 §1.4) — this package's own job kind.

Unlike `connection_probe` / `catalog_refresh` / `worker_sweep` (reserved names
whose business logic lives in V2-03/V2-06/V2-04's own files), this rollup only
reads `sessions` and writes `usage_daily`, both of which are fully specified
in CONTRACTS-V2 with no other package owning the files that would compute it,
so V2-08 implements it directly.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import select

from lkap_api.db.guard import CROSS_WORKSPACE_OPTION
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.models import UsageDaily, utcnow
from lkap_api.jobs.context import JobContext
from lkap_api.jobs.kinds import USAGE_DAILY_ROLLUP
from lkap_api.jobs.registry import job
from lkap_api.logging import get_logger

log = get_logger(__name__)


def _session_minutes(row: SessionRow) -> float:
    """Wall-clock minutes between `started_at` and `ended_at`, or 0 if either is unset."""
    if row.started_at is None or row.ended_at is None:
        return 0.0
    return max((row.ended_at - row.started_at).total_seconds() / 60.0, 0.0)


async def rollup_day(ctx: JobContext, day: dt.date) -> int:
    """Recompute every `usage_daily` bucket for `day` from `sessions`.

    Args:
        ctx: The job context (uses `ctx.database` only).
        day: The UTC calendar day to roll up (by `sessions.created_at`).

    Returns:
        How many `(workspace_id, agent_id)` buckets were written.
    """
    start = dt.datetime.combine(day, dt.time.min, tzinfo=dt.UTC)
    end = start + dt.timedelta(days=1)
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    async with ctx.database.session() as session:
        rows = (
            (
                await session.execute(
                    # Platform-wide nightly rollup: every workspace's sessions of the day.
                    select(SessionRow)
                    .where(SessionRow.created_at >= start, SessionRow.created_at < end)
                    .execution_options(**{CROSS_WORKSPACE_OPTION: True})
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            bucket = buckets.setdefault(
                (row.workspace_id, row.agent_id),
                {"sessions": 0, "minutes": 0.0, "cost_usd": 0.0, "failed": 0, "estimated_usd": None},
            )
            bucket["sessions"] += 1
            bucket["minutes"] += _session_minutes(row)
            bucket["cost_usd"] += float(row.cost_usd or 0.0)
            if row.estimated_usd is not None:  # `None` until a session of the bucket has a snapshot
                bucket["estimated_usd"] = (bucket["estimated_usd"] or 0.0) + float(row.estimated_usd)
            if row.status == "failed":
                bucket["failed"] += 1

        for (workspace_id, agent_id), values in buckets.items():
            existing = await session.get(
                UsageDaily, {"workspace_id": workspace_id, "day": day, "agent_id": agent_id}
            )
            if existing is None:
                session.add(UsageDaily(workspace_id=workspace_id, day=day, agent_id=agent_id, **values))
            else:
                existing.sessions = values["sessions"]
                existing.minutes = values["minutes"]
                existing.cost_usd = values["cost_usd"]
                existing.failed = values["failed"]
                existing.estimated_usd = values["estimated_usd"]

    log.info("usage_daily_rolled_up", day=day.isoformat(), buckets=len(buckets))
    return len(buckets)


@job(USAGE_DAILY_ROLLUP)
async def handle_usage_daily_rollup(ctx: JobContext, payload: dict[str, Any]) -> None:
    """Job handler: roll up `payload["day"]` (`YYYY-MM-DD`), defaulting to yesterday UTC."""
    day_value = payload.get("day")
    day = (
        dt.date.fromisoformat(day_value)
        if isinstance(day_value, str)
        else (utcnow() - dt.timedelta(days=1)).date()
    )
    await rollup_day(ctx, day)
