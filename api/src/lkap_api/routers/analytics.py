"""Usage and cost rollups behind the analytics page (PLAN-V2 V2-12, CONTRACTS-V2 §3.4).

Aggregates are computed live from `sessions` (workspace-scoped, non-deleted)
rather than read from `usage_daily`: that rollup (`jobs.rollup`, V2-08) only
runs once per day and would show nothing for "today" or a brand-new
workspace. A full scan of the range's session rows is the correct trade-off
at Phase 1 scale; `usage_daily` stays available for a future longer-range
view that needs to avoid it.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Query
from lkap_contracts.api_models import AnalyticsBucket, AnalyticsSummary
from sqlalchemy import select

from lkap_api.db.models import Agent, utcnow
from lkap_api.db.models import Session as SessionRow
from lkap_api.deps import AdminCtxDep, DbDep

router = APIRouter(prefix="/v1/analytics", tags=["analytics"])

AnalyticsRange = Literal["today", "7d", "30d", "all"]

#: Calendar days a named range covers; `None` means "no lower bound".
_RANGE_DAYS: dict[str, int | None] = {"today": 1, "7d": 7, "30d": 30, "all": None}


def _range_start(range_: str, now: dt.datetime) -> dt.datetime | None:
    """The lower bound of `range_` (an unknown value behaves like `"30d"`)."""
    if range_ == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    days = _RANGE_DAYS.get(range_, 30)
    if days is None:
        return None
    return now - dt.timedelta(days=days)


def _minutes(started_at: dt.datetime | None, ended_at: dt.datetime | None, now: dt.datetime) -> float:
    if started_at is None:
        return 0.0
    return max(0.0, ((ended_at or now) - started_at).total_seconds() / 60.0)


@dataclass(slots=True)
class _Bucket:
    """Accumulator for one `by_day`/`by_agent` row; `cost` stays `None` until a priced session appears."""

    key: str
    sessions: int = 0
    minutes: float = 0.0
    cost: Decimal | None = None
    failed: int = 0

    def add(self, *, minutes: float, cost_usd: float | None, failed: bool) -> None:
        self.sessions += 1
        self.minutes += minutes
        self.failed += 1 if failed else 0
        if cost_usd is not None:
            self.cost = (self.cost or Decimal(0)) + Decimal(str(cost_usd))

    def to_out(self) -> AnalyticsBucket:
        return AnalyticsBucket(
            key=self.key,
            sessions=self.sessions,
            minutes=round(self.minutes, 3),
            cost_usd=self.cost,
            failed=self.failed,
        )


@dataclass(slots=True)
class _Totals:
    sessions: int = 0
    minutes: float = 0.0
    cost: Decimal | None = None
    failed: int = 0
    by_day: dict[str, _Bucket] = field(default_factory=dict)
    by_agent: dict[str, _Bucket] = field(default_factory=dict)

    def add(
        self, *, day_key: str, agent_key: str, minutes: float, cost_usd: float | None, failed: bool
    ) -> None:
        self.sessions += 1
        self.minutes += minutes
        self.failed += 1 if failed else 0
        if cost_usd is not None:
            self.cost = (self.cost or Decimal(0)) + Decimal(str(cost_usd))
        self.by_day.setdefault(day_key, _Bucket(day_key)).add(
            minutes=minutes, cost_usd=cost_usd, failed=failed
        )
        self.by_agent.setdefault(agent_key, _Bucket(agent_key)).add(
            minutes=minutes, cost_usd=cost_usd, failed=failed
        )


@router.get(
    "/summary",
    response_model=AnalyticsSummary,
    summary="Usage and cost summary",
    description="Session/minute/cost/failure totals for a date range, broken down by day and by agent.",
)
async def analytics_summary(
    db: DbDep, ctx: AdminCtxDep, range: str = Query(default="30d", description="today | 7d | 30d | all")
) -> AnalyticsSummary:
    """Aggregate the workspace's non-deleted sessions over `range`.

    `by_agent`'s `key` is the agent's *name* (there is no room in
    `AnalyticsBucket` for a separate id/label pair) — two agents sharing a
    name would merge into one bucket, an accepted, narrow edge case.
    """
    now = utcnow()
    since = _range_start(range, now)
    conditions = [SessionRow.workspace_id == ctx.workspace_id, SessionRow.deleted_at.is_(None)]
    if since is not None:
        conditions.append(SessionRow.created_at >= since)

    rows = (
        await db.execute(
            select(
                Agent.name,
                SessionRow.created_at,
                SessionRow.started_at,
                SessionRow.ended_at,
                SessionRow.status,
                SessionRow.cost_usd,
            )
            .join(Agent, Agent.id == SessionRow.agent_id)
            .where(*conditions)
        )
    ).all()

    totals = _Totals()
    for agent_name, created_at, started_at, ended_at, session_status, cost_usd in rows:
        totals.add(
            day_key=created_at.date().isoformat(),
            agent_key=agent_name,
            minutes=_minutes(started_at, ended_at, now),
            cost_usd=cost_usd,
            failed=session_status == "failed",
        )

    return AnalyticsSummary(
        sessions=totals.sessions,
        minutes=round(totals.minutes, 3),
        cost_usd=totals.cost,
        failed=totals.failed,
        by_day=[b.to_out() for _, b in sorted(totals.by_day.items())],
        by_agent=[
            b.to_out() for b in sorted(totals.by_agent.values(), key=lambda b: b.sessions, reverse=True)
        ],
    )
