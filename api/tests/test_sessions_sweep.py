"""Stale-session sweep — DECISIONS-W2 D-W2-2(b)."""

from __future__ import annotations

import datetime as dt

import httpx
import pytest
from sqlalchemy import select

from lkap_api.db.models import Agent, Job, SessionEvent, WebhookDelivery
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.session import Database
from lkap_api.jobs.context import JobContext
from lkap_api.sessions_sweep import (
    NEVER_STARTED,
    ORPHAN_MESSAGE,
    ORPHANED,
    SESSION_ORPHAN_EVENT_JOB,
    SUMMARY_NEVER_RECEIVED,
    _emit_session_orphan_event,
    sweep_once,
    sweep_orphaned_sessions,
)
from lkap_api.settings import Settings
from lkap_api.vault import Vault
from lkap_api.webhooks.events import SESSION_ENDED

NOW = dt.datetime(2026, 9, 19, 12, 0, 0, tzinfo=dt.UTC)


async def _agent(database: Database) -> str:
    async with database.session() as session:
        agent = Agent(
            slug="sweep-test-agent",
            name="Sweep test agent",
            config={"instructions": "hi", "pipeline": {"mode": "cascaded"}},
        )
        session.add(agent)
        await session.flush()
        return agent.id


async def _session_row(
    database: Database,
    agent_id: str,
    *,
    room_name: str,
    status: str,
    created_at: dt.datetime,
    started_at: dt.datetime | None = None,
) -> str:
    async with database.session() as session:
        row = SessionRow(
            agent_id=agent_id,
            config_version=1,
            room_name=room_name,
            participant_identity="visitor",
            participant_name="Visitor",
            status=status,
            pipeline_mode="cascaded",
            created_at=created_at,
            started_at=started_at,
        )
        session.add(row)
        await session.flush()
        return row.id


async def _reload(database: Database, session_id: str) -> SessionRow:
    async with database.session() as session:
        result = await session.execute(select(SessionRow).where(SessionRow.id == session_id))
        row = result.scalar_one()
        session.expunge(row)
        return row


async def _add_event(
    database: Database, session_id: str, *, ts: dt.datetime, type_: str = "agent_state"
) -> None:
    async with database.session() as session:
        session.add(SessionEvent(session_id=session_id, ts=ts, type=type_, payload={}))


@pytest.fixture
def sweep_settings(settings: Settings) -> Settings:
    settings.session_stale_created_s = 600
    settings.session_stale_active_s = 21600
    return settings


@pytest.fixture
def orphan_settings(settings: Settings) -> Settings:
    settings.session_orphan_timeout_s = 600
    return settings


async def test_stale_created_row_is_marked_failed_never_started(
    database: Database, sweep_settings: Settings
) -> None:
    agent_id = await _agent(database)
    stale = await _session_row(
        database,
        agent_id,
        room_name="room-stale-created",
        status="created",
        created_at=NOW - dt.timedelta(seconds=601),
    )

    created_swept, active_swept = await sweep_once(database, sweep_settings, now=NOW)

    assert (created_swept, active_swept) == (1, 0)
    row = await _reload(database, stale)
    assert row.status == "failed"
    assert row.error == NEVER_STARTED
    assert row.ended_at == NOW


async def test_fresh_created_row_is_untouched(database: Database, sweep_settings: Settings) -> None:
    agent_id = await _agent(database)
    fresh = await _session_row(
        database,
        agent_id,
        room_name="room-fresh-created",
        status="created",
        created_at=NOW - dt.timedelta(seconds=599),
    )

    created_swept, active_swept = await sweep_once(database, sweep_settings, now=NOW)

    assert (created_swept, active_swept) == (0, 0)
    row = await _reload(database, fresh)
    assert row.status == "created"
    assert row.error is None


async def test_stale_active_row_is_marked_failed_summary_never_received(
    database: Database, sweep_settings: Settings
) -> None:
    agent_id = await _agent(database)
    stale = await _session_row(
        database,
        agent_id,
        room_name="room-stale-active",
        status="active",
        created_at=NOW - dt.timedelta(seconds=30000),
        started_at=NOW - dt.timedelta(seconds=21601),
    )

    created_swept, active_swept = await sweep_once(database, sweep_settings, now=NOW)

    assert (created_swept, active_swept) == (0, 1)
    row = await _reload(database, stale)
    assert row.status == "failed"
    assert row.error == SUMMARY_NEVER_RECEIVED
    assert row.ended_at == NOW


async def test_fresh_active_row_is_untouched(database: Database, sweep_settings: Settings) -> None:
    agent_id = await _agent(database)
    fresh = await _session_row(
        database,
        agent_id,
        room_name="room-fresh-active",
        status="active",
        created_at=NOW - dt.timedelta(seconds=30000),
        started_at=NOW - dt.timedelta(seconds=100),
    )

    created_swept, active_swept = await sweep_once(database, sweep_settings, now=NOW)

    assert (created_swept, active_swept) == (0, 0)
    row = await _reload(database, fresh)
    assert row.status == "active"


async def test_ended_row_is_never_touched(database: Database, sweep_settings: Settings) -> None:
    agent_id = await _agent(database)
    ended = await _session_row(
        database,
        agent_id,
        room_name="room-ended",
        status="ended",
        created_at=NOW - dt.timedelta(seconds=100000),
    )

    await sweep_once(database, sweep_settings, now=NOW)

    row = await _reload(database, ended)
    assert row.status == "ended"
    assert row.error is None


async def test_active_row_with_no_started_at_is_left_alone(
    database: Database, sweep_settings: Settings
) -> None:
    """An `active` row should always carry `started_at` (set by `resolved_config`),
    but the sweep must not crash or misfire if one somehow doesn't."""
    agent_id = await _agent(database)
    odd = await _session_row(
        database,
        agent_id,
        room_name="room-active-no-started",
        status="active",
        created_at=NOW - dt.timedelta(seconds=100000),
        started_at=None,
    )

    created_swept, active_swept = await sweep_once(database, sweep_settings, now=NOW)

    assert (created_swept, active_swept) == (0, 0)
    row = await _reload(database, odd)
    assert row.status == "active"


async def test_a_late_summary_still_overwrites_a_swept_row(
    admin_client, service_client, database: Database, sweep_settings: Settings
) -> None:
    """DECISIONS-W2 D-W2-2(b): the worker is the authority when it does show up."""
    from conftest import create_agent

    agent = await create_agent(admin_client)
    response = await admin_client.post(f"/v1/agents/{agent['id']}/connect", json={})
    session_id = str(response.json()["sessionId"])

    async with database.session() as session:
        row = await session.get(SessionRow, session_id)
        assert row is not None
        row.created_at = NOW - dt.timedelta(seconds=601)

    created_swept, _ = await sweep_once(database, sweep_settings, now=NOW)
    assert created_swept == 1
    swept = await _reload(database, session_id)
    assert swept.status == "failed"
    assert swept.error == NEVER_STARTED

    summary_response = await service_client.put(
        f"/internal/v1/sessions/{session_id}/summary",
        json={
            "status": "ended",
            "usage": {"llm_completion_tokens": 1},
            "transcript": [],
            "final_ui_state": None,
        },
    )
    assert summary_response.status_code == 204

    final = await _reload(database, session_id)
    assert final.status == "ended"
    assert final.error is None


async def test_sweep_is_idempotent_across_multiple_passes(
    database: Database, sweep_settings: Settings
) -> None:
    agent_id = await _agent(database)
    stale = await _session_row(
        database,
        agent_id,
        room_name="room-idempotent",
        status="created",
        created_at=NOW - dt.timedelta(seconds=601),
    )

    first = await sweep_once(database, sweep_settings, now=NOW)
    second = await sweep_once(database, sweep_settings, now=NOW + dt.timedelta(seconds=60))

    assert first == (1, 0)
    assert second == (0, 0)
    row = await _reload(database, stale)
    assert row.status == "failed"


# ============================================= ask #55, B-13: orphaned-session sweep


class _FakeJobsService:
    """Stands in for `JobsService`: records the webhook deliveries `emit` enqueues."""

    def __init__(self) -> None:
        self.enqueued: list[tuple[str, dict[str, object]]] = []

    async def enqueue(self, kind: str, payload: dict[str, object], **_: object) -> None:
        self.enqueued.append((kind, payload))


async def test_orphan_older_than_threshold_is_closed_failed(
    database: Database, orphan_settings: Settings
) -> None:
    """A job that crashed before the observer attached: `active`, no events, past the cutoff."""
    agent_id = await _agent(database)
    orphan = await _session_row(
        database,
        agent_id,
        room_name="room-orphan",
        status="active",
        created_at=NOW - dt.timedelta(seconds=700),
        started_at=NOW - dt.timedelta(seconds=700),
    )

    closed = await sweep_orphaned_sessions(database, orphan_settings, now=NOW)

    assert closed == 1
    row = await _reload(database, orphan)
    assert row.status == "failed"
    assert row.error == ORPHANED
    assert row.ended_at == NOW


async def test_young_orphan_candidate_is_kept(database: Database, orphan_settings: Settings) -> None:
    """A session that only just started (no events yet) must not be swept."""
    agent_id = await _agent(database)
    young = await _session_row(
        database,
        agent_id,
        room_name="room-young",
        status="active",
        created_at=NOW - dt.timedelta(seconds=100),
        started_at=NOW - dt.timedelta(seconds=100),
    )

    closed = await sweep_orphaned_sessions(database, orphan_settings, now=NOW)

    assert closed == 0
    row = await _reload(database, young)
    assert row.status == "active"
    assert row.error is None


async def test_session_with_a_recent_event_is_kept(database: Database, orphan_settings: Settings) -> None:
    """A genuinely long live call: old `created_at`/`started_at`, but a recent event."""
    agent_id = await _agent(database)
    long_lived = await _session_row(
        database,
        agent_id,
        room_name="room-long-lived",
        status="active",
        created_at=NOW - dt.timedelta(seconds=700),
        started_at=NOW - dt.timedelta(seconds=700),
    )
    await _add_event(database, long_lived, ts=NOW - dt.timedelta(seconds=30))

    closed = await sweep_orphaned_sessions(database, orphan_settings, now=NOW)

    assert closed == 0
    row = await _reload(database, long_lived)
    assert row.status == "active"
    assert row.error is None


async def test_session_with_only_a_stale_event_is_still_kept(
    database: Database, orphan_settings: Settings
) -> None:
    """The discriminating case: it proved it started, so it is never this sweep's business —

    even though its one event is itself older than the orphan cutoff, a
    session that has posted at least one event must be left to the coarser
    `session_stale_active_s` rule (six hours by default), never closed here as
    "no worker activity". This is what distinguishes "no events, ever" from a
    weaker "no *recent* events" reading of the rule.
    """
    agent_id = await _agent(database)
    quiet = await _session_row(
        database,
        agent_id,
        room_name="room-quiet-but-started",
        status="active",
        created_at=NOW - dt.timedelta(seconds=700),
        started_at=NOW - dt.timedelta(seconds=700),
    )
    await _add_event(database, quiet, ts=NOW - dt.timedelta(seconds=700))

    closed = await sweep_orphaned_sessions(database, orphan_settings, now=NOW)

    assert closed == 0
    row = await _reload(database, quiet)
    assert row.status == "active"
    assert row.error is None


async def test_ended_session_is_untouched_by_orphan_sweep(
    database: Database, orphan_settings: Settings
) -> None:
    agent_id = await _agent(database)
    ended = await _session_row(
        database,
        agent_id,
        room_name="room-orphan-ended",
        status="ended",
        created_at=NOW - dt.timedelta(seconds=100000),
    )

    closed = await sweep_orphaned_sessions(database, orphan_settings, now=NOW)

    assert closed == 0
    row = await _reload(database, ended)
    assert row.status == "ended"
    assert row.error is None


async def test_orphan_sweep_records_an_error_event(database: Database, orphan_settings: Settings) -> None:
    agent_id = await _agent(database)
    orphan = await _session_row(
        database,
        agent_id,
        room_name="room-orphan-event",
        status="active",
        created_at=NOW - dt.timedelta(seconds=700),
        started_at=NOW - dt.timedelta(seconds=700),
    )

    await sweep_orphaned_sessions(database, orphan_settings, now=NOW)

    async with database.session() as session:
        events = list(
            (await session.execute(select(SessionEvent).where(SessionEvent.session_id == orphan))).scalars()
        )
    assert len(events) == 1
    assert events[0].type == "error"
    assert events[0].payload == {"message": ORPHAN_MESSAGE}


async def test_orphan_sweep_emits_the_session_ended_webhook(
    admin_client: httpx.AsyncClient, database: Database, settings: Settings, orphan_settings: Settings
) -> None:
    """The sweep queues a `session_orphan_event` outbox job; running it fans `session.ended` out."""
    hook = await admin_client.post(
        "/v1/webhooks", json={"url": "https://hooks.example/lkap", "events": [SESSION_ENDED]}
    )
    assert hook.status_code == 201, hook.text

    agent_id = await _agent(database)
    orphan = await _session_row(
        database,
        agent_id,
        room_name="room-orphan-webhook",
        status="active",
        created_at=NOW - dt.timedelta(seconds=700),
        started_at=NOW - dt.timedelta(seconds=700),
    )

    closed = await sweep_orphaned_sessions(database, orphan_settings, now=NOW)
    assert closed == 1

    async with database.session() as session:
        jobs = list(
            (await session.execute(select(Job).where(Job.kind == SESSION_ORPHAN_EVENT_JOB))).scalars()
        )
    assert len(jobs) == 1
    assert jobs[0].payload["data"]["session_id"] == orphan
    assert jobs[0].payload["data"]["status"] == "failed"

    fake_jobs = _FakeJobsService()
    ctx = JobContext(
        database=database,
        settings=settings,
        vault=Vault(settings.master_key),
        http=httpx.AsyncClient(),
        jobs=fake_jobs,  # type: ignore[arg-type]
    )
    for job_row in jobs:
        await _emit_session_orphan_event(ctx, dict(job_row.payload))
    await ctx.http.aclose()

    async with database.session() as session:
        deliveries = list(
            (
                await session.execute(
                    select(WebhookDelivery).where(WebhookDelivery.event_type == SESSION_ENDED)
                )
            )
            .scalars()
            .all()
        )
    assert len(deliveries) == 1
    assert deliveries[0].payload["data"]["session_id"] == orphan


def test_session_orphan_timeout_default_is_ten_minutes(settings: Settings) -> None:
    assert settings.session_orphan_timeout_s == 600


async def test_orphan_with_no_started_at_falls_back_to_created_at(
    database: Database, orphan_settings: Settings
) -> None:
    """`started_at` should always be set on an `active` row (`_build_resolved`), but the
    `coalesce(started_at, created_at)` cutoff must still close a row that somehow lacks
    it, rather than treating a missing `started_at` as "never expires" (`sweep_once`'s
    own `test_active_row_with_no_started_at_is_left_alone` deliberately leaves that odd
    row alone; this sweep does not, since it never needs `started_at` for anything but
    the liveness clock)."""
    agent_id = await _agent(database)
    odd = await _session_row(
        database,
        agent_id,
        room_name="room-orphan-no-started-at",
        status="active",
        created_at=NOW - dt.timedelta(seconds=700),
        started_at=None,
    )

    closed = await sweep_orphaned_sessions(database, orphan_settings, now=NOW)

    assert closed == 1
    row = await _reload(database, odd)
    assert row.status == "failed"
    assert row.error == ORPHANED
