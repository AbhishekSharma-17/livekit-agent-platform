"""Tests for `lkap_api.jobs.rollup`: the `usage_daily` nightly rollup and its scheduling."""

from __future__ import annotations

import datetime as dt
import json

import httpx
from conftest import inference_config
from sqlalchemy import func, select

from lkap_api.db.constants import DEFAULT_WORKSPACE_ID
from lkap_api.db.models import Agent, Job, UsageDaily
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.session import Database
from lkap_api.jobs.context import JobContext
from lkap_api.jobs.kinds import USAGE_DAILY_ROLLUP
from lkap_api.jobs.rollup import rollup_day
from lkap_api.jobs.service import JobsService
from lkap_api.settings import Settings
from lkap_api.vault import Vault


async def _make_agent(database: Database, name: str) -> str:
    async with database.session() as session:
        agent = Agent(
            slug=f"rollup-{name}", name=name, config=json.loads(inference_config().model_dump_json())
        )
        session.add(agent)
        await session.flush()
        return agent.id


async def _make_ended_session(
    database: Database,
    agent_id: str,
    *,
    room: str,
    created_at: dt.datetime,
    started_at: dt.datetime | None,
    ended_at: dt.datetime | None,
    status: str = "ended",
    cost_usd: float | None = None,
) -> None:
    async with database.session() as session:
        session.add(
            SessionRow(
                agent_id=agent_id,
                config_version=1,
                room_name=room,
                participant_identity="caller",
                participant_name="Caller",
                status=status,
                pipeline_mode="cascaded",
                created_at=created_at,
                started_at=started_at,
                ended_at=ended_at,
                cost_usd=cost_usd,
            )
        )


def _ctx(database: Database, settings: Settings, vault: Vault) -> JobContext:
    jobs = JobsService(database=database, settings=settings, vault=vault)
    return JobContext(database=database, settings=settings, vault=vault, http=httpx.AsyncClient(), jobs=jobs)


async def test_rollup_day_buckets_sessions_by_agent_and_counts_failed(
    database: Database, settings: Settings
) -> None:
    vault = Vault(settings.master_key)
    agent_a = await _make_agent(database, "agent-a")
    agent_b = await _make_agent(database, "agent-b")
    day = dt.date(2026, 1, 15)
    day_start = dt.datetime.combine(day, dt.time(12, 0), tzinfo=dt.UTC)

    await _make_ended_session(
        database,
        agent_a,
        room="r1",
        created_at=day_start,
        started_at=day_start,
        ended_at=day_start + dt.timedelta(minutes=2),
        cost_usd=0.05,
    )
    await _make_ended_session(
        database,
        agent_a,
        room="r2",
        created_at=day_start + dt.timedelta(hours=1),
        started_at=day_start + dt.timedelta(hours=1),
        ended_at=day_start + dt.timedelta(hours=1, minutes=3),
        cost_usd=0.10,
    )
    await _make_ended_session(
        database,
        agent_b,
        room="r3",
        created_at=day_start,
        started_at=None,
        ended_at=None,
        status="failed",
    )
    # A session on a different day must not be counted.
    await _make_ended_session(
        database,
        agent_a,
        room="r4",
        created_at=day_start - dt.timedelta(days=1),
        started_at=day_start - dt.timedelta(days=1),
        ended_at=day_start - dt.timedelta(days=1) + dt.timedelta(minutes=1),
    )

    buckets_written = await rollup_day(_ctx(database, settings, vault), day)
    assert buckets_written == 2

    async with database.session() as session:
        bucket_a = await session.get(
            UsageDaily, {"workspace_id": DEFAULT_WORKSPACE_ID, "day": day, "agent_id": agent_a}
        )
        bucket_b = await session.get(
            UsageDaily, {"workspace_id": DEFAULT_WORKSPACE_ID, "day": day, "agent_id": agent_b}
        )

    assert bucket_a is not None
    assert bucket_a.sessions == 2
    assert bucket_a.minutes == 5.0
    assert round(bucket_a.cost_usd, 2) == 0.15
    assert bucket_a.failed == 0

    assert bucket_b is not None
    assert bucket_b.sessions == 1
    assert bucket_b.minutes == 0.0
    assert bucket_b.failed == 1


async def test_rollup_day_is_idempotent(database: Database, settings: Settings) -> None:
    vault = Vault(settings.master_key)
    agent_id = await _make_agent(database, "agent-idempotent")
    day = dt.date(2026, 1, 16)
    day_start = dt.datetime.combine(day, dt.time(9, 0), tzinfo=dt.UTC)
    await _make_ended_session(
        database,
        agent_id,
        room="idem-1",
        created_at=day_start,
        started_at=day_start,
        ended_at=day_start + dt.timedelta(minutes=1),
        cost_usd=0.01,
    )

    ctx = _ctx(database, settings, vault)
    await rollup_day(ctx, day)
    await rollup_day(ctx, day)

    async with database.session() as session:
        bucket = await session.get(
            UsageDaily, {"workspace_id": DEFAULT_WORKSPACE_ID, "day": day, "agent_id": agent_id}
        )
    assert bucket is not None
    assert bucket.sessions == 1  # not 2: recomputed, not accumulated


async def test_poller_schedules_exactly_one_rollup_job_per_day(
    database: Database, settings: Settings
) -> None:
    vault = Vault(settings.master_key)
    service = JobsService(database=database, settings=settings, vault=vault)
    try:
        await service._ensure_usage_rollup_scheduled()  # exercising the scheduler directly
        await service._ensure_usage_rollup_scheduled()  # second call must be a no-op

        async with database.session() as session:
            count = (
                await session.execute(
                    select(func.count()).select_from(Job).where(Job.kind == USAGE_DAILY_ROLLUP)
                )
            ).scalar_one()
        assert count == 1
    finally:
        await service.aclose()
