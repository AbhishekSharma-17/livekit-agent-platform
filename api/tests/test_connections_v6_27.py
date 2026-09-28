"""V6-27: agent-name clashes on one LiveKit server, and which worker serves a connection.

* 409 ``agent_name_in_use`` on create, on an update that changes the url or the agent
  name, and in the unsaved-details test; never leaking another workspace's connection.
* ``ready_workers`` on list/get, ``ready_workers``/``shared_agent_name_workers`` on the
  fleet status, ``warnings`` on a saved connection's test.
* The call-start check (409 ``no_worker_running``) and its grace rules.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import httpx
import pytest
from conftest import inference_config
from connection_fakes import KEY_B, SECRET_B, add_agent, connection_row, fake_livekit
from fastapi import FastAPI
from lkap_contracts.api_models import ConnectionCreate, ConnectionUpdate

from lkap_api.connections import service
from lkap_api.db.constants import DEFAULT_WORKSPACE_ID
from lkap_api.db.models import LiveKitConnection, WorkerInstance, Workspace, new_id, utcnow
from lkap_api.db.session import Database
from lkap_api.fleet import readiness
from lkap_api.settings import Settings
from lkap_api.vault import Vault

PACKS = ["packs.generic"]
OTHER_WS_ID = "e" * 32
OTHER_WS_SLUG = "acme-private"
OTHER_WS_NAME = "Acme Private Workspace"
OTHER_CONN_SLUG = "acme-secret-conn"
SERVER = "wss://shared-project.livekit.cloud"


def _payload(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "slug": "shared-a",
        "name": "Shared A",
        "deployment_type": "cloud",
        "url": SERVER,
        "api_key": KEY_B,
        "api_secret": SECRET_B,
        "agent_name": "support-agent",
    }
    body.update(overrides)
    return body


async def _other_workspace_connection(
    database: Database, settings: Settings, **overrides: Any
) -> LiveKitConnection:
    """A connection of another workspace on :data:`SERVER` under ``support-agent``."""
    async with database.session() as session:
        session.add(Workspace(id=OTHER_WS_ID, slug=OTHER_WS_SLUG, name=OTHER_WS_NAME))
        await session.flush()
        values: dict[str, Any] = {"url": SERVER, "agent_name": "support-agent", "name": "Acme Hidden Name"}
        values.update(overrides)
        row = connection_row(
            Vault(settings.master_key), slug=OTHER_CONN_SLUG, workspace_id=OTHER_WS_ID, **values
        )
        session.add(row)
    return row


async def _own_connection(database: Database, settings: Settings, **overrides: Any) -> LiveKitConnection:
    async with database.session() as session:
        row = connection_row(Vault(settings.master_key), slug="own-legacy", **overrides)
        session.add(row)
    return row


async def _add_worker(
    database: Database,
    connection_id: str,
    *,
    status: str = "ready",
    seen_ago: dt.timedelta = dt.timedelta(seconds=5),
) -> None:
    seen = utcnow() - seen_ago
    async with database.session() as session:
        session.add(
            WorkerInstance(
                id=new_id(),
                connection_id=connection_id,
                instance_key=f"host:{new_id()[:8]}",
                status=status,
                registered_at=seen,
                last_heartbeat_at=seen,
                managed_by="external",
            )
        )


# ------------------------------------------------------------------ url normalisation
@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("wss://Project.LiveKit.cloud", "https://project.livekit.cloud"),
        ("ws://lk.example.com:80", "http://lk.example.com"),
        ("wss://lk.example.com:443/", "wss://lk.example.com"),
        ("https://lk.example.com/", "ws://lk.example.com"),
        ("  wss://lk.example.com  ", "wss://lk.example.com"),
        ("ws://[::1]:7880", "http://[::1]:7880/"),
    ],
)
def test_normalize_server_url_equivalent_forms_compare_equal(left: str, right: str) -> None:
    assert service.normalize_server_url(left) == service.normalize_server_url(right)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("ws://lk.example.com:7880", "ws://lk.example.com"),
        ("wss://a.livekit.cloud", "wss://b.livekit.cloud"),
        ("ws://127.0.0.1:7880", "ws://127.0.0.1:7881"),
    ],
)
def test_normalize_server_url_different_servers_stay_different(left: str, right: str) -> None:
    assert service.normalize_server_url(left) != service.normalize_server_url(right)


# ---------------------------------------------------------------------- create path
async def test_create_connection_same_workspace_clash_is_409_naming_the_connection(
    admin_client: httpx.AsyncClient,
) -> None:
    first = await admin_client.post("/v1/connections", json=_payload())
    assert first.status_code == 201, first.text

    response = await admin_client.post(
        "/v1/connections",
        json=_payload(slug="shared-b", name="Shared B", url="https://Shared-Project.livekit.cloud:443/"),
    )

    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "agent_name_in_use"
    assert error["details"]["field"] == "agent_name"
    assert error["details"]["connection_name"] == "Shared A"
    assert error["message"].startswith(
        "Another connection already uses the agent name 'support-agent' on this LiveKit server. "
        "Calls would be split between them. Pick a different agent name."
    )
    assert "Shared A" in error["message"]


async def test_create_connection_cross_workspace_clash_is_409_without_leaking_the_other_workspace(
    admin_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    other = await _other_workspace_connection(database, settings)

    response = await admin_client.post("/v1/connections", json=_payload())

    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "agent_name_in_use"
    assert error["details"] == {"field": "agent_name"}
    assert error["message"] == service.AGENT_NAME_IN_USE_MESSAGE.format(name="support-agent")
    for secret in (OTHER_WS_ID, OTHER_WS_SLUG, OTHER_WS_NAME, other.id, OTHER_CONN_SLUG, "Acme Hidden Name"):
        assert secret not in response.text


@pytest.mark.parametrize(
    "overrides",
    [
        {"agent_name": "support-agent-2"},
        {"url": "wss://another-project.livekit.cloud"},
        {"url": "wss://shared-project.livekit.cloud:7880"},
    ],
)
async def test_create_connection_other_server_or_name_is_created(
    admin_client: httpx.AsyncClient, database: Database, settings: Settings, overrides: dict[str, Any]
) -> None:
    await _other_workspace_connection(database, settings)

    response = await admin_client.post("/v1/connections", json=_payload(**overrides))

    assert response.status_code == 201, response.text


async def test_create_connection_bad_agent_name_is_still_422_before_the_clash_check(
    admin_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    await _other_workspace_connection(database, settings, agent_name="has space")

    response = await admin_client.post("/v1/connections", json=_payload(agent_name="has space"))

    assert response.status_code == 422
    assert response.json()["error"]["details"]["field"] == "agent_name"


# ---------------------------------------------------------------------- update path
async def test_update_connection_changing_agent_name_into_a_clash_is_409(
    admin_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    await _other_workspace_connection(database, settings)
    created = (await admin_client.post("/v1/connections", json=_payload(agent_name="free-name"))).json()

    response = await admin_client.put(
        f"/v1/connections/{created['id']}", json={"agent_name": "support-agent"}
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "agent_name_in_use"
    assert OTHER_WS_NAME not in response.text


async def test_update_connection_changing_url_into_a_clash_is_409(
    admin_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    await _other_workspace_connection(database, settings)
    created = (
        await admin_client.post("/v1/connections", json=_payload(url="wss://elsewhere.livekit.cloud"))
    ).json()

    response = await admin_client.put(
        f"/v1/connections/{created['id']}", json={"url": "https://shared-project.livekit.cloud/"}
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["field"] == "agent_name"


async def test_update_connection_unchanged_url_and_name_never_self_clash(
    admin_client: httpx.AsyncClient,
) -> None:
    created = (await admin_client.post("/v1/connections", json=_payload())).json()

    response = await admin_client.put(
        f"/v1/connections/{created['id']}",
        json={"name": "Renamed", "agent_name": "support-agent", "url": "wss://shared-project.livekit.cloud/"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Renamed"


async def test_update_connection_rename_of_a_legacy_clash_is_not_blocked(
    admin_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    # Two rows that predate the check already share a server and a name.
    await _other_workspace_connection(database, settings)
    legacy = await _own_connection(database, settings, url=SERVER, agent_name="support-agent")

    response = await admin_client.put(
        f"/v1/connections/{legacy.id}", json={"name": "Still here", "agent_name": "support-agent"}
    )

    assert response.status_code == 200, response.text


async def test_update_connection_service_is_workspace_scoped_apart_from_the_marked_lookup(
    database: Database, settings: Settings, tenant_guard: None
) -> None:
    vault = Vault(settings.master_key)
    async with database.session() as session:
        row = await service.create_connection(
            session, vault, DEFAULT_WORKSPACE_ID, ConnectionCreate(**_payload()), PACKS
        )
        await service.update_connection(
            session, DEFAULT_WORKSPACE_ID, row.id, ConnectionUpdate(agent_name="other-name"), PACKS
        )


# --------------------------------------------------------------- unsaved-details test
async def test_test_unsaved_connection_clash_is_409_before_any_probe(
    admin_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    await _other_workspace_connection(database, settings)

    response = await admin_client.post("/v1/connections/test", json=_payload())

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "agent_name_in_use"
    assert response.json()["error"]["details"] == {"field": "agent_name"}
    assert OTHER_CONN_SLUG not in response.text


async def test_test_unsaved_connection_free_name_probes_as_before(admin_client: httpx.AsyncClient) -> None:
    async with fake_livekit() as (_fake, url):
        response = await admin_client.post("/v1/connections/test", json=_payload(url=url))

    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True
    assert response.json()["warnings"] == []


# ------------------------------------------------------------- saved test warnings
async def test_test_connection_legacy_clash_and_shared_workers_are_warnings_not_failures(
    admin_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    async with fake_livekit() as (_fake, url):
        other = await _other_workspace_connection(database, settings, url=url)
        legacy = await _own_connection(database, settings, url=url, agent_name="support-agent")
        await _add_worker(database, other.id)

        response = await admin_client.post(f"/v1/connections/{legacy.id}/test")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert len(body["warnings"]) == 2
    assert "agent name 'support-agent'" in body["warnings"][0]
    assert body["warnings"][1].startswith("1 worker started for another connection is running")
    for secret in (OTHER_WS_ID, OTHER_WS_NAME, other.id, OTHER_CONN_SLUG):
        assert secret not in response.text


# ------------------------------------------------------------------ worker counts
async def test_list_and_get_connection_ready_workers_count_only_live_ready_rows(
    admin_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    own = await _own_connection(database, settings)
    await _add_worker(database, own.id)
    await _add_worker(database, own.id)
    await _add_worker(database, own.id, status="starting")
    await _add_worker(database, own.id, seen_ago=dt.timedelta(minutes=5))  # silent: not live
    await _add_worker(database, own.id, status="draining")

    listing = (await admin_client.get("/v1/connections")).json()["items"]
    one = (await admin_client.get(f"/v1/connections/{own.id}")).json()

    counts = {item["id"]: item["ready_workers"] for item in listing}
    assert counts[own.id] == 2
    assert all(value == 0 for key, value in counts.items() if key != own.id)
    assert one["ready_workers"] == 2


async def test_get_fleet_reports_ready_and_shared_agent_name_workers(
    admin_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    other = await _other_workspace_connection(database, settings)
    legacy = await _own_connection(database, settings, url=SERVER, agent_name="support-agent")
    await _add_worker(database, other.id)
    await _add_worker(database, other.id)
    await _add_worker(database, legacy.id)

    fleet = await admin_client.get(f"/v1/connections/{legacy.id}/fleet")

    assert fleet.status_code == 200, fleet.text
    assert fleet.json()["ready_workers"] == 1
    assert fleet.json()["shared_agent_name_workers"] == 2
    assert other.id not in fleet.text and OTHER_WS_NAME not in fleet.text


# ---------------------------------------------------------------- call-start check
LONG_AGO = dt.timedelta(hours=1)


@pytest.mark.parametrize(
    ("mode", "deployment_mode", "started_ago", "worker", "refused"),
    [
        ("block", "external", LONG_AGO, None, True),
        ("block", "supervised", LONG_AGO, None, True),
        ("block", "cloud_hosted", LONG_AGO, None, False),
        ("block", "external", dt.timedelta(seconds=30), None, False),
        ("block", "external", LONG_AGO, "ready", False),
        ("block", "supervised", LONG_AGO, "starting", False),
        ("block", "external", LONG_AGO, "stale", True),
        ("warn", "external", LONG_AGO, None, False),
        ("off", "external", LONG_AGO, None, False),
    ],
)
async def test_ensure_worker_ready_refuses_only_when_the_worker_table_is_authoritative(
    database: Database,
    settings: Settings,
    mode: readiness.WorkerCheckMode,
    deployment_mode: str,
    started_ago: dt.timedelta,
    worker: str | None,
    refused: bool,
) -> None:
    row = await _own_connection(database, settings, deployment_mode=deployment_mode)
    if worker == "stale":
        await _add_worker(database, row.id, seen_ago=dt.timedelta(minutes=10))
    elif worker is not None:
        await _add_worker(database, row.id, status=worker)
    now = utcnow()

    async with database.session() as session:
        call = readiness.ensure_worker_ready(
            session, row, mode=mode, privileged=True, api_started_at=now - started_ago, now=now
        )
        if refused:
            with pytest.raises(readiness.NoWorkerRunningError) as caught:
                await call
            assert caught.value.message == (
                "No worker is running for connection 'OWN-LEGACY'. Start one from Connections → OWN-LEGACY."
            )
        else:
            await call


async def test_ensure_worker_ready_other_connections_live_worker_under_the_same_name_serves(
    database: Database, settings: Settings
) -> None:
    other = await _other_workspace_connection(database, settings)
    row = await _own_connection(database, settings, url=SERVER, agent_name="support-agent")
    await _add_worker(database, other.id)
    now = utcnow()

    async with database.session() as session:
        checked = await readiness.ensure_worker_ready(
            session, row, mode="block", privileged=True, api_started_at=now - LONG_AGO, now=now
        )

    assert checked is not None and checked.shared == 1


async def test_ensure_worker_ready_unknown_start_time_never_refuses(
    database: Database, settings: Settings
) -> None:
    row = await _own_connection(database, settings)

    async with database.session() as session:
        checked = await readiness.ensure_worker_ready(
            session, row, mode="block", privileged=True, api_started_at=None
        )

    assert checked is not None and not checked.serving


async def _bound_agent(database: Database, settings: Settings, **conn: Any) -> tuple[str, LiveKitConnection]:
    row = await _own_connection(database, settings, **conn)
    async with database.session() as session:
        agent = await add_agent(session, inference_config(), connection_id=row.id)
        agent.allowed_origins = ["*"]
    return agent.id, row


async def test_connect_no_worker_is_409_with_the_connection_name_for_a_builder(
    app: FastAPI, admin_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    agent_id, _row = await _bound_agent(database, settings)
    app.state.started_at = utcnow() - LONG_AGO

    response = await admin_client.post(f"/v1/agents/{agent_id}/connect", json={"participant_name": "Ada"})

    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "no_worker_running"
    assert error["message"] == (
        "No worker is running for connection 'OWN-LEGACY'. Start one from Connections → OWN-LEGACY."
    )


async def test_connect_no_worker_public_caller_gets_a_generic_message(
    app: FastAPI, client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    agent_id, _row = await _bound_agent(database, settings)
    app.state.started_at = utcnow() - LONG_AGO

    response = await client.post(
        f"/v1/agents/{agent_id}/connect",
        json={"participant_name": "Ada"},
        headers={"Origin": "https://embed.example.com"},
    )

    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "no_worker_running"
    assert error["message"] == readiness.PUBLIC_NO_WORKER_MESSAGE
    assert "OWN-LEGACY" not in response.text and error["details"] is None


async def test_connect_with_a_ready_worker_mints_a_token(
    app: FastAPI, admin_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    agent_id, row = await _bound_agent(database, settings)
    await _add_worker(database, row.id)
    app.state.started_at = utcnow() - LONG_AGO

    response = await admin_client.post(f"/v1/agents/{agent_id}/connect", json={"participant_name": "Ada"})

    assert response.status_code == 200, response.text


async def test_connect_right_after_an_api_start_is_not_refused(
    app: FastAPI, admin_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    agent_id, _row = await _bound_agent(database, settings)
    app.state.started_at = utcnow()

    response = await admin_client.post(f"/v1/agents/{agent_id}/connect", json={"participant_name": "Ada"})

    assert response.status_code == 200, response.text


async def test_text_session_no_worker_is_409(
    app: FastAPI, admin_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    agent_id, _row = await _bound_agent(database, settings)
    app.state.started_at = utcnow() - LONG_AGO

    response = await admin_client.post(
        f"/v1/agents/{agent_id}/text-sessions", json={"participant_name": "Ada"}
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "no_worker_running"
