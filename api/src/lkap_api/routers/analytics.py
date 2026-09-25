"""Usage and cost rollups behind the analytics page (PLAN-V2 V2-12, CONTRACTS-V2 §3.4, COSTS.md §4.4).

Aggregates are computed live from `sessions` (workspace-scoped, non-deleted)
rather than read from `usage_daily`: that rollup (`jobs.rollup`, V2-08) only
runs once per day and would show nothing for "today" or a brand-new
workspace. A full scan of the range's session rows is the correct trade-off
at Phase 1 scale; `usage_daily` stays available for a future longer-range
view that needs to avoid it.

V4-15 adds the estimate side: `estimated_usd` (the creation-time snapshots),
`sessions_estimated`, `accuracy_pct` (actual ÷ estimated × 100 over the
sessions that have **both** figures) per total and bucket, and `top_drivers` —
one grouped query over `session_costs` in the range, the eight largest
(provider, model, unit) groups. `share_pct` is each one's share of the range's
**total** line cost; when the eight do not cover it, a synthetic
`provider_id="other"` row carries the remainder so the shares sum to 100 (R-V4-62).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal, cast

from fastapi import APIRouter, Query
from lkap_contracts.api_models import AnalyticsBucket, AnalyticsDriver, AnalyticsSummary
from lkap_contracts.pricing import Unit
from sqlalchemy import func, select

from lkap_api.db.models import Agent, utcnow
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.models import SessionCost as SessionCostRow
from lkap_api.deps import AdminCtxDep, DbDep

router = APIRouter(prefix="/v1/analytics", tags=["analytics"])

AnalyticsRange = Literal["today", "7d", "30d", "all"]

#: Calendar days a named range covers; `None` means "no lower bound".
_RANGE_DAYS: dict[str, int | None] = {"today": 1, "7d": 7, "30d": 30, "all": None}

#: How many cost drivers the summary lists.
TOP_DRIVERS = 8


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


def _dec(value: float | None) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


def _accuracy(actual: Decimal, estimated: Decimal) -> float | None:
    return round(float(actual / estimated * 100), 1) if estimated > 0 else None


@dataclass(slots=True)
class _Bucket:
    """Accumulator for one `by_day`/`by_agent` row; money stays `None` until a figure appears."""

    key: str
    sessions: int = 0
    minutes: float = 0.0
    cost: Decimal | None = None
    failed: int = 0
    estimated: Decimal | None = None
    sessions_estimated: int = 0
    both_actual: Decimal = Decimal(0)
    both_estimated: Decimal = Decimal(0)

    def add(
        self, *, minutes: float, cost_usd: Decimal | None, estimated_usd: Decimal | None, failed: bool
    ) -> None:
        self.sessions += 1
        self.minutes += minutes
        self.failed += 1 if failed else 0
        if cost_usd is not None:
            self.cost = (self.cost or Decimal(0)) + cost_usd
        if estimated_usd is not None:
            self.estimated = (self.estimated or Decimal(0)) + estimated_usd
            self.sessions_estimated += 1
        if cost_usd is not None and estimated_usd is not None:
            self.both_actual += cost_usd
            self.both_estimated += estimated_usd

    @property
    def accuracy(self) -> float | None:
        return _accuracy(self.both_actual, self.both_estimated)

    def to_out(self) -> AnalyticsBucket:
        return AnalyticsBucket(
            key=self.key,
            sessions=self.sessions,
            minutes=round(self.minutes, 3),
            cost_usd=self.cost,
            failed=self.failed,
            estimated_usd=self.estimated,
            sessions_estimated=self.sessions_estimated,
            accuracy_pct=self.accuracy,
        )


@dataclass(slots=True)
class _Totals:
    all: _Bucket = field(default_factory=lambda: _Bucket("all"))
    by_day: dict[str, _Bucket] = field(default_factory=dict)
    by_agent: dict[str, _Bucket] = field(default_factory=dict)

    def add(
        self,
        *,
        day_key: str,
        agent_key: str,
        minutes: float,
        cost_usd: Decimal | None,
        estimated_usd: Decimal | None,
        failed: bool,
    ) -> None:
        kw: dict[str, Any] = {
            "minutes": minutes,
            "cost_usd": cost_usd,
            "estimated_usd": estimated_usd,
            "failed": failed,
        }
        self.all.add(**kw)
        self.by_day.setdefault(day_key, _Bucket(day_key)).add(**kw)
        self.by_agent.setdefault(agent_key, _Bucket(agent_key)).add(**kw)


def _estimated_by_line(estimate: Any, minutes: float) -> dict[tuple[str, str | None, str], Decimal]:
    """A snapshot's per-(provider, model, unit) estimated USD at the session's actual minutes."""
    out: dict[tuple[str, str | None, str], Decimal] = {}
    lines = estimate.get("lines") if isinstance(estimate, Mapping) else None
    for line in lines if isinstance(lines, list) else []:
        if not isinstance(line, Mapping):
            continue
        per_min, per_session = line.get("usd_per_min"), line.get("usd_per_session")
        if line.get("quantity_per_min") is not None and per_min is not None:
            value = Decimal(str(per_min)) * Decimal(str(minutes))
        elif line.get("quantity_per_min") is None and per_session is not None:
            value = Decimal(str(per_session))
        else:
            continue
        key = (str(line.get("provider_id")), line.get("model"), str(line.get("unit")))
        out[key] = out.get(key, Decimal(0)) + value
    return out


@router.get(
    "/summary",
    response_model=AnalyticsSummary,
    summary="Usage and cost summary",
    description=(
        "Session/minute/cost/failure totals for a date range, broken down by day and by agent, "
        "with the creation-time **estimate** beside the actual cost (`estimated_usd`, "
        "`accuracy_pct` over sessions with both) and the top cost drivers (`share_pct` of the range's "
        "total, with an `other` row for the rest). Actual cost is usage at "
        "list prices (OpenRouter at its live price); vendor invoices may differ."
    ),
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
                SessionRow.estimated_usd,
                SessionRow.estimate,
            )
            .join(Agent, Agent.id == SessionRow.agent_id)
            .where(*conditions)
        )
    ).all()

    totals = _Totals()
    estimated_lines: dict[tuple[str, str | None, str], Decimal] = {}
    for (
        agent_name,
        created_at,
        started_at,
        ended_at,
        session_status,
        cost_usd,
        estimated_usd,
        estimate,
    ) in rows:
        minutes = _minutes(started_at, ended_at, now)
        totals.add(
            day_key=created_at.date().isoformat(),
            agent_key=agent_name,
            minutes=minutes,
            cost_usd=_dec(cost_usd),
            estimated_usd=_dec(estimated_usd),
            failed=session_status == "failed",
        )
        if estimated_usd is not None:
            for key, value in _estimated_by_line(estimate, minutes).items():
                estimated_lines[key] = estimated_lines.get(key, Decimal(0)) + value

    drivers = (
        await db.execute(
            select(
                SessionCostRow.provider_id,
                SessionCostRow.model,
                SessionCostRow.unit,
                func.sum(SessionCostRow.cost_usd),
            )
            .join(SessionRow, SessionRow.id == SessionCostRow.session_id)
            .where(*conditions)
            .group_by(SessionCostRow.provider_id, SessionCostRow.model, SessionCostRow.unit)
            .order_by(func.sum(SessionCostRow.cost_usd).desc())
        )
    ).all()
    groups = [(pid, model or None, unit, Decimal(str(cost or 0))) for pid, model, unit, cost in drivers]
    total = sum((cost for *_, cost in groups), Decimal(0))
    top = groups[:TOP_DRIVERS]
    rest = total - sum((cost for *_, cost in top), Decimal(0))

    def _share(cost: Decimal) -> float:
        return round(float(cost / total * 100), 2) if total > 0 else 0.0

    top_drivers = [
        AnalyticsDriver(
            provider_id=pid,
            model=model,
            unit=cast(Unit, unit),
            cost_usd=cost,
            share_pct=_share(cost),
            estimated_usd=estimated_lines.get((pid, model, unit)),
        )
        for pid, model, unit, cost in top
    ]
    if rest > 0:
        top_drivers.append(AnalyticsDriver(provider_id="other", cost_usd=rest, share_pct=_share(rest)))

    return AnalyticsSummary(
        sessions=totals.all.sessions,
        minutes=round(totals.all.minutes, 3),
        cost_usd=totals.all.cost,
        failed=totals.all.failed,
        by_day=[b.to_out() for _, b in sorted(totals.by_day.items())],
        by_agent=[
            b.to_out() for b in sorted(totals.by_agent.values(), key=lambda b: b.sessions, reverse=True)
        ],
        estimated_usd=totals.all.estimated,
        sessions_estimated=totals.all.sessions_estimated,
        accuracy_pct=totals.all.accuracy,
        top_drivers=top_drivers,
    )
