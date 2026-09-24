"""V2-04 api side: worker register/heartbeat, the sweep, fleet/desired and the fleet routes."""

from __future__ import annotations

import datetime as dt
from typing import Any

import httpx
import pytest
from auth_helpers import login, make_api_key, make_user
from connection_fakes import SECRET_B, connection_row
from fastapi import FastAPI
from sqlalchemy import select

from lkap_api.connections import service
from lkap_api.db.models import AuditLog, FleetDesiredState, LiveKitConnection, WorkerInstance, utcnow
from lkap_api.db.session import Database
from lkap_api.fleet import registry
from lkap_api.fleet.registry import STALE_AFTER
from lkap_api.fleet.sweep import PRUNE_GONE_AFTER, sweep_once
from lkap_api.settings import Settings
from lkap_api.vault import Vault


async def _connection(database: Database, settings: Settings, **overrides: Any) -> str:
    vault = Vault(settings.master_key)
    async with database.session() as session:
        row = connection_row(vault, **overrides)
        session.add(row)
        await session.flush()
        if row.deployment_mode == "supervised":
            session.add(FleetDesiredState(connection_id=row.id, desired_replicas=0, desired_hash="h" * 64))
        return row.id


async def _worker(database: Database, key: str) -> WorkerInstance:
    async with database.session() as session:
        query = select(WorkerInstance).where(WorkerInstance.instance_key == key)
        row = (await session.execute(query)).scalar_one()
        session.expunge(row)
        return row


def _register_body(connection_id: str | None, key: str = "host:101", **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "connection_id": connection_id,
        "instance_key": key,
        "image": "slim",
        "sdk_version": "1.8.2",
        "installed_provider_ids": ["openai-llm", "deepgram-stt", "openai-llm"],
        "pack_ids": ["generic"],
        "managed_by": "external",
    }
    body.update(extra)
    return body


# ------------------------------------------------------------------ register / heartbeat
async def test_register_creates_a_ready_row_and_returns_the_agent_name(
    service_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    connection_id = await _connection(database, settings, slug="pool-a")

    response = await service_client.post("/internal/v1/workers/register", json=_register_body(connection_id))

    assert response.status_code == 200, response.text
    assert response.json() == {"connection_id": connection_id, "agent_name": "agent-pool-a"}
    row = await _worker(database, "host:101")
    assert (row.connection_id, row.status, row.managed_by) == (connection_id, "ready", "external")
    assert row.installed_provider_ids == ["deepgram-stt", "openai-llm"]
    assert row.last_heartbeat_at is not None


class _RecordingLog:
    def __init__(self) -> None:
        self.warnings: list[tuple[str, dict[str, Any]]] = []

    def warning(self, event: str, **kwargs: Any) -> None:
        self.warnings.append((event, kwargs))

    def info(self, event: str, **kwargs: Any) -> None:
        pass

    def debug(self, event: str, **kwargs: Any) -> None:
        pass


def test_pack_mismatch_names_what_each_side_lacks() -> None:
    assert registry.pack_mismatch(["generic", "insurance_claim"], ["insurance_claim", "generic"]) == ([], [])
    assert registry.pack_mismatch(["generic", "insurance_claim"], ["generic", "acme"]) == (
        ["insurance_claim"],
        ["acme"],
    )


@pytest.mark.parametrize(
    ("worker_packs", "warned"),
    [
        (["generic", "insurance_claim"], False),
        (["generic"], True),
        ([], False),  # the supervisor pre-registers a replica with no packs yet
    ],
)
async def test_register_warns_when_the_worker_packs_differ_from_the_api(
    service_client: httpx.AsyncClient,
    database: Database,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    worker_packs: list[str],
    warned: bool,
) -> None:
    """F-17: the api's `LKAP_PACKS` (conftest: generic + insurance_claim) vs the worker's report."""
    recorder = _RecordingLog()
    monkeypatch.setattr(registry, "log", recorder)
    connection_id = await _connection(database, settings, slug="pool-packs")

    response = await service_client.post(
        "/internal/v1/workers/register", json=_register_body(connection_id, pack_ids=worker_packs)
    )

    assert response.status_code == 200, response.text
    mismatches = [kwargs for event, kwargs in recorder.warnings if event == "worker_pack_mismatch"]
    if warned:
        assert mismatches == [
            {
                "instance_key": "host:101",
                "connection_id": connection_id,
                "missing_on_worker": ["insurance_claim"],
                "unknown_to_api": [],
                "hint": "set the same LKAP_PACKS for the api and the worker, then restart both",
            }
        ]
    else:
        assert mismatches == []


async def test_register_without_connection_id_uses_the_default_connection(
    service_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    async with database.session() as session:
        default_id = (
            await session.execute(select(LiveKitConnection.id).where(LiveKitConnection.is_default.is_(True)))
        ).scalar_one()

    response = await service_client.post("/internal/v1/workers/register", json=_register_body(None))

    assert response.status_code == 200, response.text
    assert response.json()["connection_id"] == default_id


async def test_register_unknown_connection_is_404_and_requires_the_service_token(
    service_client: httpx.AsyncClient, client: httpx.AsyncClient
) -> None:
    missing = await service_client.post("/internal/v1/workers/register", json=_register_body("nope"))
    unauthenticated = await client.post("/internal/v1/workers/register", json=_register_body("nope"))

    assert missing.status_code == 404
    assert unauthenticated.status_code == 401


async def test_supervisor_ownership_is_sticky_when_the_worker_registers_as_external(
    service_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    connection_id = await _connection(database, settings)
    await service_client.post(
        "/internal/v1/workers/register", json=_register_body(connection_id, managed_by="supervisor")
    )

    await service_client.post("/internal/v1/workers/register", json=_register_body(connection_id))

    assert (await _worker(database, "host:101")).managed_by == "supervisor"


async def test_heartbeat_updates_status_and_draining_only_moves_to_gone(
    service_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    connection_id = await _connection(database, settings)
    await service_client.post("/internal/v1/workers/register", json=_register_body(connection_id))
    url = "/internal/v1/workers/host:101/heartbeat"

    draining = await service_client.post(url, json={"status": "draining", "active_jobs": 1})
    back_to_ready = await service_client.post(url, json={"status": "ready", "active_jobs": 0})
    status_after_ready = (await _worker(database, "host:101")).status
    gone = await service_client.post(url, json={"status": "gone", "active_jobs": 0})

    assert (draining.status_code, back_to_ready.status_code, gone.status_code) == (204, 204, 204)
    assert status_after_ready == "draining"
    assert (await _worker(database, "host:101")).status == "gone"


async def test_heartbeat_for_an_unknown_instance_is_404(service_client: httpx.AsyncClient) -> None:
    response = await service_client.post("/internal/v1/workers/ghost:1/heartbeat", json={"status": "ready"})

    assert response.status_code == 404


# ------------------------------------------------------------------------------ sweep
async def test_sweep_marks_silent_workers_gone_and_prunes_old_gone_rows(
    service_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    connection_id = await _connection(database, settings)
    for key in ("fresh:1", "silent:2", "ancient:3"):
        await service_client.post("/internal/v1/workers/register", json=_register_body(connection_id, key))
    now = utcnow()
    async with database.session() as session:
        rows = {r.instance_key: r for r in (await session.execute(select(WorkerInstance))).scalars()}
        rows["silent:2"].last_heartbeat_at = now - STALE_AFTER - dt.timedelta(seconds=1)
        rows["ancient:3"].status = "gone"
        rows["ancient:3"].last_heartbeat_at = now - PRUNE_GONE_AFTER - dt.timedelta(seconds=1)

    marked, pruned = await sweep_once(database, now=now)

    assert (marked, pruned) == (1, 1)
    async with database.session() as session:
        left = {r.instance_key: r.status for r in (await session.execute(select(WorkerInstance))).scalars()}
    assert left == {"fresh:1": "ready", "silent:2": "gone"}


# ----------------------------------------------------------------------- fleet/desired
async def test_fleet_desired_lists_supervised_connections_only(
    service_client: httpx.AsyncClient, client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    supervised = await _connection(database, settings, slug="sup", deployment_mode="supervised", replicas=2)
    await _connection(database, settings, slug="ext")

    response = await service_client.get("/internal/v1/fleet/desired")
    unauthenticated = await client.get("/internal/v1/fleet/desired")

    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["connection_id"] for item in body] == [supervised]
    assert body[0]["agent_name"] == "agent-sup"
    assert body[0]["deployment_mode"] == "supervised"
    assert len(body[0]["desired_hash"]) == 64
    assert SECRET_B not in response.text
    assert unauthenticated.status_code == 401


# ----------------------------------------------------------------------- admin routes
async def test_get_fleet_shows_instances_and_installed_providers(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    connection_id = await _connection(database, settings, deployment_mode="supervised")
    await service_client.post("/internal/v1/workers/register", json=_register_body(connection_id))

    response = await admin_client.get(f"/v1/connections/{connection_id}/fleet")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["image"] == "slim"
    assert body["installed_provider_ids"] == ["deepgram-stt", "openai-llm"]
    assert [(i["instance_key"], i["status"]) for i in body["instances"]] == [("host:101", "ready")]


async def test_fleet_start_and_stop_set_the_desired_replicas_and_audit(
    admin_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    connection_id = await _connection(database, settings, deployment_mode="supervised", replicas=2)
    url = f"/v1/connections/{connection_id}/fleet"

    started = await admin_client.post(url, json={"action": "start"})
    scaled = await admin_client.post(url, json={"action": "start", "replicas": 3})
    stopped = await admin_client.post(url, json={"action": "stop"})

    assert [r.status_code for r in (started, scaled, stopped)] == [200, 200, 200]
    assert [r.json()["desired_replicas"] for r in (started, scaled, stopped)] == [2, 3, 0]
    async with database.session() as session:
        actions = (
            await session.execute(
                select(AuditLog.action).where(AuditLog.target_id == connection_id).order_by(AuditLog.id)
            )
        ).scalars()
        assert list(actions) == ["fleet.start", "fleet.start", "fleet.stop"]


@pytest.mark.parametrize(
    ("mode", "payload", "expected"),
    [
        ("external", {"action": "start"}, 409),
        ("supervised", {"action": "start", "replicas": 0}, 422),
        ("external", {"action": "restart"}, 409),
    ],
)
async def test_fleet_action_rejections(
    admin_client: httpx.AsyncClient,
    database: Database,
    settings: Settings,
    mode: str,
    payload: dict[str, Any],
    expected: int,
) -> None:
    connection_id = await _connection(database, settings, deployment_mode=mode)

    response = await admin_client.post(f"/v1/connections/{connection_id}/fleet", json=payload)

    assert response.status_code == expected, response.text


async def test_viewer_reads_the_fleet_but_cannot_act_and_other_workspaces_are_404(
    app: FastAPI, database: Database, settings: Settings
) -> None:
    connection_id = await _connection(database, settings, deployment_mode="supervised")
    await make_user(database, "viewer@example.com", role="viewer")
    _key_id, raw = await make_api_key(database, ["connections:read"])

    async with await login(app, "viewer@example.com") as viewer:
        read = await viewer.get(f"/v1/connections/{connection_id}/fleet")
        act = await viewer.post(f"/v1/connections/{connection_id}/fleet", json={"action": "stop"})
        missing = await viewer.get("/v1/connections/not-a-connection/fleet")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://api.test",
        headers={"Authorization": f"Bearer {raw}"},
    ) as key_client:
        key_act = await key_client.post(f"/v1/connections/{connection_id}/fleet", json={"action": "stop"})

    assert read.status_code == 200
    assert act.status_code == 403
    assert missing.status_code == 404
    assert key_act.status_code == 403


async def test_the_fleet_internal_router_lifespan_runs_the_sweep(
    service_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    import asyncio

    from lkap_api.routers.fleet_internal import router

    connection_id = await _connection(database, settings)
    await service_client.post("/internal/v1/workers/register", json=_register_body(connection_id, "stale:1"))
    async with database.session() as session:
        row = (await session.execute(select(WorkerInstance))).scalar_one()
        row.last_heartbeat_at = utcnow() - STALE_AFTER - dt.timedelta(seconds=5)
    host = FastAPI()
    host.state.db = database

    async with router.lifespan_context(host):
        for _ in range(100):
            if (await _worker(database, "stale:1")).status == "gone":
                break
            await asyncio.sleep(0.02)

    assert (await _worker(database, "stale:1")).status == "gone"


def test_the_worker_sweep_job_kind_has_a_handler() -> None:
    from lkap_api.fleet.sweep import run_worker_sweep_job
    from lkap_api.jobs.kinds import WORKER_SWEEP
    from lkap_api.jobs.registry import get_handler

    assert get_handler(WORKER_SWEEP) is run_worker_sweep_job


# ------------------------------------------------------------------- restart (R-V2-4)
async def test_restart_bumps_the_generation_and_changes_the_desired_hash(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    connection_id = await _connection(database, settings, deployment_mode="supervised", replicas=2)
    url = f"/v1/connections/{connection_id}/fleet"
    await admin_client.post(url, json={"action": "start"})
    before = (await service_client.get("/internal/v1/fleet/desired")).json()[0]

    first = await admin_client.post(url, json={"action": "restart"})
    second = await admin_client.post(url, json={"action": "restart"})
    after = (await service_client.get("/internal/v1/fleet/desired")).json()[0]

    assert (first.status_code, second.status_code) == (200, 200), first.text
    assert (first.json()["restart_generation"], second.json()["restart_generation"]) == (1, 2)
    assert second.json()["restart_requested_at"] is not None
    assert second.json()["desired_replicas"] == 2  # restart never resizes the pool
    assert before["restart_generation"] == 0 and after["restart_generation"] == 2
    assert after["desired_hash"] != before["desired_hash"]
    assert after["desired_replicas"] == 2
    async with database.session() as session:
        state = await session.get(FleetDesiredState, connection_id)
        assert state is not None and state.restart_generation == 2
        actions = (
            await session.execute(select(AuditLog.action).where(AuditLog.target_id == connection_id))
        ).scalars()
        assert list(actions).count("fleet.restart") == 2


async def test_the_restart_generation_survives_rotation_stop_and_start(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    connection_id = await _connection(database, settings, deployment_mode="supervised", replicas=2)
    url = f"/v1/connections/{connection_id}/fleet"
    await admin_client.post(url, json={"action": "restart"})

    await admin_client.post(
        f"/v1/connections/{connection_id}/rotate",
        json={"api_key": "APInew12345", "api_secret": "new-secret-xyz"},
    )
    stopped = await admin_client.post(url, json={"action": "stop"})
    started = await admin_client.post(url, json={"action": "start"})
    desired = (await service_client.get("/internal/v1/fleet/desired")).json()[0]

    assert stopped.json()["desired_replicas"] == 0
    assert started.json()["desired_replicas"] == 2  # restores connections.replicas
    assert started.json()["restart_generation"] == 1
    assert desired["restart_generation"] == 1
    async with database.session() as session:
        row = await session.get(LiveKitConnection, connection_id)
        assert row is not None
        assert desired["desired_hash"] == service.desired_hash_of(row, settings.packs_list, 1)


def test_compute_desired_hash_includes_the_restart_generation() -> None:
    base = {
        "url": "wss://x.livekit.cloud",
        "credentials_version": 1,
        "agent_name": "lkap-agent",
        "image": "slim",
        "packs": ["packs.generic"],
    }

    assert service.compute_desired_hash(**base) == service.compute_desired_hash(**base, restart_generation=0)
    assert service.compute_desired_hash(**base, restart_generation=1) != service.compute_desired_hash(**base)
    assert service.compute_desired_hash(**base, restart_generation=1) != service.compute_desired_hash(
        **base, restart_generation=2
    )
