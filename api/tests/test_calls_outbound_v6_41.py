"""V6-41: outbound phone calls fail fast when no worker would answer them.

``POST /v1/calls`` resolves the agent's connection before the slot locks and runs
:func:`lkap_api.fleet.readiness.ensure_worker_ready` with the rules of ``connect``
(``LKAP_CALL_START_WORKER_CHECK``, the startup grace, the shared-name exemption,
the builder message). LiveKit is faked at the client factory (``test_telephony``'s
world), so nothing is dispatched or dialed for real.
"""

from __future__ import annotations

import datetime as dt

import httpx
import pytest
import test_telephony as tel
from conftest import captured_text
from fastapi import FastAPI
from sqlalchemy import func, select
from test_telephony import CALLEE, World, _trunk

from lkap_api.db.models import AuditLog, Call, LiveKitConnection, WorkerInstance, new_id, utcnow
from lkap_api.db.session import Database
from lkap_api.settings import Settings

# The telephony world's fixtures, shared with ``test_telephony`` (pytest finds them by name).
fake_lk = tel.fake_lk
fake_factory = tel.fake_factory
dial_policy = tel.dial_policy
world = tel.world

#: Well past :data:`lkap_api.fleet.readiness.STARTUP_GRACE`.
LONG_AGO = dt.timedelta(hours=1)


async def _dial(client: httpx.AsyncClient, world: World) -> httpx.Response:
    await _trunk(client, world, "outbound")
    return await client.post("/v1/calls", json={"agent_id": world.agent_id, "to_e164": CALLEE})


async def _connection(database: Database, connection_id: str) -> LiveKitConnection:
    async with database.session() as session:
        row = await session.get(LiveKitConnection, connection_id)
    assert row is not None
    return row


async def _call_rows(database: Database) -> int:
    async with database.session() as session:
        return int((await session.execute(select(func.count()).select_from(Call))).scalar_one())


async def _add_ready_worker(database: Database, connection_id: str) -> None:
    seen = utcnow() - dt.timedelta(seconds=5)
    async with database.session() as session:
        session.add(
            WorkerInstance(
                id=new_id(),
                connection_id=connection_id,
                instance_key=f"host:{new_id()[:8]}",
                status="ready",
                registered_at=seen,
                last_heartbeat_at=seen,
                managed_by="external",
            )
        )


async def test_place_call_refuses_when_no_worker_is_live(
    app: FastAPI, admin_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    app.state.started_at = utcnow() - LONG_AGO
    connection = await _connection(database, world.connection_id)

    response = await _dial(admin_client, world)

    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "no_worker_running"
    assert error["message"] == (
        f"No worker is running for connection '{connection.name}'. "
        f"Start one from Connections → {connection.name}."
    )
    assert error["details"] == {"connection_id": connection.id, "connection_name": connection.name}
    assert world.lk.named("create_dispatch") == []
    assert world.lk.named("create_sip_participant") == []
    assert await _call_rows(database) == 0
    async with database.session() as session:
        rows = list((await session.execute(select(AuditLog).where(AuditLog.action.like("call.%")))).scalars())
    assert [row.action for row in rows] == ["call.refused"]
    assert rows[0].payload == {
        "agent_id": world.agent_id,
        "to": CALLEE,
        "code": "no_worker_running",
        "status": 409,
    }


async def test_place_call_with_a_ready_worker_dials(
    app: FastAPI, admin_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    app.state.started_at = utcnow() - LONG_AGO
    await _add_ready_worker(database, world.connection_id)

    response = await _dial(admin_client, world)

    assert response.status_code == 201, response.text
    assert [d.agent_name for d in world.lk.named("create_dispatch")] == [world.agent_name]


async def test_place_call_warn_mode_dials_and_logs(
    app: FastAPI,
    admin_client: httpx.AsyncClient,
    world: World,
    database: Database,
    settings: Settings,
    log_capture: pytest.LogCaptureFixture,
) -> None:
    settings.call_start_worker_check = "warn"
    app.state.started_at = utcnow() - LONG_AGO

    response = await _dial(admin_client, world)

    assert response.status_code == 201, response.text
    assert len(world.lk.named("create_sip_participant")) == 1
    assert await _call_rows(database) == 1
    logged = captured_text(log_capture)
    assert "call_start_no_worker" in logged
    assert world.connection_id in logged


async def test_place_call_never_refuses_in_the_startup_grace(
    app: FastAPI, admin_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    app.state.started_at = utcnow()

    response = await _dial(admin_client, world)

    assert response.status_code == 201, response.text
    assert len(world.lk.named("create_sip_participant")) == 1
    assert await _call_rows(database) == 1


async def test_place_call_by_slug_runs_the_check_on_the_resolved_connection(
    app: FastAPI, admin_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    app.state.started_at = utcnow() - LONG_AGO
    await _trunk(admin_client, world, "outbound")
    agent = (await admin_client.get(f"/v1/agents/{world.agent_id}")).json()

    refused = await admin_client.post("/v1/calls", json={"agent_id": agent["slug"], "to_e164": CALLEE})
    await _add_ready_worker(database, world.connection_id)
    placed = await admin_client.post("/v1/calls", json={"agent_id": agent["slug"], "to_e164": CALLEE})

    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "no_worker_running"
    assert placed.status_code == 201, placed.text
