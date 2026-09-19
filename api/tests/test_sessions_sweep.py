"""Stale-session sweep — DECISIONS-W2 D-W2-2(b)."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select

from lkap_api.db.models import Agent
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.session import Database
from lkap_api.sessions_sweep import (
    NEVER_STARTED,
    SUMMARY_NEVER_RECEIVED,
    sweep_once,
)
from lkap_api.settings import Settings

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


@pytest.fixture
def sweep_settings(settings: Settings) -> Settings:
    settings.session_stale_created_s = 600
    settings.session_stale_active_s = 21600
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
