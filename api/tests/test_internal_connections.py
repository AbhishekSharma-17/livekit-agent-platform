"""Worker/supervisor routes of V2-03 and the admin worker-env / deploy-bundle views."""

from __future__ import annotations

import io
import zipfile
from typing import Any

import httpx
import pytest
from conftest import captured_text, inference_config
from connection_fakes import KEY_B, SECRET_B, add_agent, connection_row
from sqlalchemy import select

from lkap_api.db.models import Session as SessionRow
from lkap_api.db.models import WorkerInstance
from lkap_api.db.session import Database
from lkap_api.settings import Settings
from lkap_api.vault import Vault


async def _connection_and_agent(database: Database, settings: Settings, **connection: Any) -> tuple[str, str]:
    vault = Vault(settings.master_key)
    async with database.session() as session:
        conn = connection_row(vault, **connection)
        session.add(conn)
        await session.flush()
        agent = await add_agent(session, inference_config(), connection_id=conn.id)
    return conn.id, agent.id


async def test_sessions_start_creates_a_sip_session_on_the_agent_connection(
    service_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    connection_id, agent_id = await _connection_and_agent(database, settings, deployment_type="self_hosted")
    payload = {
        "agent_id": agent_id,
        "room_name": "sip-room-1",
        "channel": "sip_in",
        "participant_identity": "sip_+15550100",
        "caller": {"from": "+15550100", "to": "+15550199"},
    }

    response = await service_client.post("/internal/v1/sessions/start", json=payload)

    body = response.json()
    assert response.status_code == 201, response.text
    assert body["channel"] == "sip_in"
    assert body["connection"]["connection_id"] == connection_id
    assert body["connection"]["deployment_type"] == "self_hosted"
    assert body["connection"]["capabilities"]["inference_available"] is False
    assert body["connection"]["capabilities"]["turn_detector_mode"] == "local"
    assert body["installed_provider_ids"] is None
    async with database.session() as session:
        row = (
            await session.execute(select(SessionRow).where(SessionRow.room_name == "sip-room-1"))
        ).scalar_one()
    assert (row.channel, row.connection_id, row.status) == ("sip_in", connection_id, "active")
    assert row.caller == {"from": "+15550100", "to": "+15550199"}
    assert row.id == body["session_id"]


async def test_sessions_start_duplicate_room_is_409(
    service_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    _connection_id, agent_id = await _connection_and_agent(database, settings)
    payload = {"agent_id": agent_id, "room_name": "dup-room", "channel": "sip_in"}

    first = await service_client.post("/internal/v1/sessions/start", json=payload)
    second = await service_client.post("/internal/v1/sessions/start", json=payload)

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["error"]["details"]["session_id"] == first.json()["session_id"]


async def test_sessions_start_unknown_agent_is_404_and_needs_service_token(
    service_client: httpx.AsyncClient, client: httpx.AsyncClient
) -> None:
    payload = {"agent_id": "missing", "room_name": "r", "channel": "sip_in"}

    assert (await service_client.post("/internal/v1/sessions/start", json=payload)).status_code == 404
    assert (await client.post("/internal/v1/sessions/start", json=payload)).status_code == 401


async def test_resolved_reports_the_connection_and_installed_providers(
    service_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    connection_id, agent_id = await _connection_and_agent(database, settings)
    async with database.session() as session:
        session.add(
            WorkerInstance(
                connection_id=connection_id,
                instance_key="host:1",
                status="ready",
                installed_provider_ids=["livekit-inference-llm", "deepgram-stt"],
            )
        )
        session.add(
            WorkerInstance(
                connection_id=connection_id,
                instance_key="host:2",
                status="gone",
                installed_provider_ids=["x"],
            )
        )
        session.add(
            SessionRow(
                id="a" * 32,
                agent_id=agent_id,
                connection_id=connection_id,
                config_version=1,
                room_name="lkap-aaaaaaaa",
                participant_identity="user-1",
                participant_name="U",
                status="created",
                pipeline_mode="cascaded",
            )
        )

    response = await service_client.get(f"/internal/v1/sessions/{'a' * 32}/resolved")

    body = response.json()
    assert response.status_code == 200, response.text
    assert body["connection"]["connection_id"] == connection_id
    assert body["connection"]["capabilities"]["inference_available"] is True
    assert body["installed_provider_ids"] == ["deepgram-stt", "livekit-inference-llm"]
    assert body["workspace_id"] == "00000000000000000000000000000001"
    assert body["panel"]["panel_id"] == "composite"


async def test_internal_worker_env_is_decrypted_and_service_only(
    service_client: httpx.AsyncClient, admin_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    connection_id, _agent_id = await _connection_and_agent(database, settings, worker_image="full")

    response = await service_client.get(f"/internal/v1/connections/{connection_id}/worker-env")
    denied = await admin_client.get(f"/internal/v1/connections/{connection_id}/worker-env")

    env = response.json()["env"]
    assert response.status_code == 200, response.text
    assert response.json()["image"] == "full"
    assert (env["LIVEKIT_API_KEY"], env["LIVEKIT_API_SECRET"]) == (KEY_B, SECRET_B)
    assert env["LIVEKIT_URL"] == "wss://project-b.livekit.cloud"
    assert env["LKAP_AGENT_NAME"] == env["LIVEKIT_AGENT_NAME"] == "agent-conn-b"
    assert env["LKAP_CONNECTION_ID"] == connection_id
    assert env["LKAP_SERVICE_TOKEN"] == settings.service_token
    assert env["LKAP_PACKS"] == ",".join(settings.packs_list)
    assert env["LKAP_API_BASE_URL"].startswith("http")
    assert denied.status_code == 401


# --------------------------------------------------------- V2-20-2: worker callback url
async def test_worker_env_honours_an_explicit_api_base_url_over_the_port_guess(
    service_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    connection_id, _agent_id = await _connection_and_agent(database, settings, worker_image="slim")
    settings.api_base_url = "http://scratch-api.internal:8096"
    settings.port = 8080  # a different, wrong value: proves it's never consulted once set

    response = await service_client.get(f"/internal/v1/connections/{connection_id}/worker-env")

    assert response.status_code == 200, response.text
    assert response.json()["env"]["LKAP_API_BASE_URL"] == "http://scratch-api.internal:8096"


async def test_worker_env_warns_when_the_callback_url_is_derived_from_port(
    service_client: httpx.AsyncClient,
    database: Database,
    settings: Settings,
    log_capture: pytest.LogCaptureFixture,
) -> None:
    connection_id, _agent_id = await _connection_and_agent(database, settings, worker_image="slim")
    settings.api_base_url = None
    settings.public_base_url = None
    settings.port = 8096

    response = await service_client.get(f"/internal/v1/connections/{connection_id}/worker-env")

    assert response.status_code == 200, response.text
    assert response.json()["env"]["LKAP_API_BASE_URL"] == "http://127.0.0.1:8096"
    text = captured_text(log_capture)
    assert "worker_callback_url_derived_from_port" in text


async def test_recording_start_conflicts_when_the_agent_has_no_recording_configured(
    service_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    """The `lkap_api.recordings` package (V2-12) replaced the ask-#23 501 stub.

    `_connection_and_agent` seeds `inference_config()`, whose `recording`
    block defaults to `enabled=False`, so `start_recording` now runs its own
    business logic and reports a real, specific conflict instead of "not
    implemented".
    """
    connection_id, agent_id = await _connection_and_agent(database, settings)
    async with database.session() as session:
        session.add(
            SessionRow(
                id="b" * 32,
                agent_id=agent_id,
                connection_id=connection_id,
                config_version=1,
                room_name="lkap-bbbbbbbb",
                participant_identity="u",
                participant_name="U",
                status="active",
                pipeline_mode="cascaded",
            )
        )

    response = await service_client.post(f"/internal/v1/sessions/{'b' * 32}/recording/start")

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "conflict"


# --------------------------------------------------------- V2-20-3: failed recording surfaced
async def test_post_recording_failed_status_persists_the_reason(
    service_client: httpx.AsyncClient, admin_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    """A `recording/start` that never got an egress id still reports `status=failed` here.

    Mirrors `_Recording._start`'s new fallback in `agent/src/lkap_agent/main.py`:
    on `RecordingUnavailableError` it posts `SessionRecordingIn(egress_id="",
    status="failed", error=...)` to this same route.
    """
    connection_id, agent_id = await _connection_and_agent(database, settings)
    async with database.session() as session:
        session.add(
            SessionRow(
                id="c" * 32,
                agent_id=agent_id,
                connection_id=connection_id,
                config_version=1,
                room_name="lkap-cccccccc",
                participant_identity="u",
                participant_name="U",
                status="active",
                pipeline_mode="cascaded",
            )
        )

    response = await service_client.post(
        f"/internal/v1/sessions/{'c' * 32}/recording",
        json={"egress_id": "", "status": "failed", "error": "recording/start answered HTTP 422"},
    )
    assert response.status_code == 204, response.text

    detail = (await admin_client.get(f"/v1/sessions/{'c' * 32}")).json()
    assert detail["recording"]["status"] == "failed"
    assert detail["recording"]["error"] == "recording/start answered HTTP 422"
    assert detail["recording_status"] == "failed"

    listed = (await admin_client.get("/v1/sessions")).json()
    (row,) = [item for item in listed["items"] if item["id"] == "c" * 32]
    assert row["recording_status"] == "failed"


async def test_post_recording_success_clears_a_previous_failure_reason(
    service_client: httpx.AsyncClient, admin_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    connection_id, agent_id = await _connection_and_agent(database, settings)
    async with database.session() as session:
        session.add(
            SessionRow(
                id="d" * 32,
                agent_id=agent_id,
                connection_id=connection_id,
                config_version=1,
                room_name="lkap-dddddddd",
                participant_identity="u",
                participant_name="U",
                status="active",
                pipeline_mode="cascaded",
                recording_status="failed",
                recording_error="an earlier attempt failed",
            )
        )

    response = await service_client.post(
        f"/internal/v1/sessions/{'d' * 32}/recording",
        json={"egress_id": "EG_retry", "status": "active"},
    )
    assert response.status_code == 204, response.text

    detail = (await admin_client.get(f"/v1/sessions/{'d' * 32}")).json()
    assert detail["recording"]["status"] == "active"
    assert detail["recording"]["error"] is None


@pytest.mark.parametrize(
    ("fmt", "marker"), [("env", "LKAP_CONNECTION_ID="), ("compose", "services:"), ("lk", "lk project add")]
)
async def test_admin_worker_env_template_is_redacted(
    admin_client: httpx.AsyncClient, database: Database, settings: Settings, fmt: str, marker: str
) -> None:
    connection_id, _agent_id = await _connection_and_agent(database, settings)

    response = await admin_client.get(f"/v1/connections/{connection_id}/worker-env", params={"format": fmt})

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/plain")
    assert marker in response.text
    assert connection_id in response.text
    assert SECRET_B not in response.text and KEY_B not in response.text
    assert settings.service_token not in response.text
    assert "<LIVEKIT_API_SECRET>" in response.text


async def test_admin_worker_env_unknown_format_is_422(
    admin_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    connection_id, _agent_id = await _connection_and_agent(database, settings)

    response = await admin_client.get(f"/v1/connections/{connection_id}/worker-env", params={"format": "xml"})

    assert response.status_code == 422


async def test_deploy_bundle_requires_cloud_and_public_https(
    admin_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    cloud_id, _ = await _connection_and_agent(database, settings)
    self_hosted_id, _ = await _connection_and_agent(
        database, settings, slug="self-a", url="ws://localhost:7880", deployment_type="self_hosted"
    )

    no_public_url = await admin_client.post(f"/v1/connections/{cloud_id}/deploy-bundle")
    self_hosted = await admin_client.post(f"/v1/connections/{self_hosted_id}/deploy-bundle")

    assert no_public_url.status_code == 409
    assert no_public_url.json()["error"]["details"]["setting"] == "LKAP_PUBLIC_BASE_URL"
    assert self_hosted.status_code == 409


async def test_deploy_bundle_zip_holds_toml_secrets_and_readme_without_secrets(
    admin_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    cloud_id, _ = await _connection_and_agent(database, settings)
    settings.public_base_url = "https://lkap.example.com"

    response = await admin_client.post(f"/v1/connections/{cloud_id}/deploy-bundle")

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = sorted(archive.namelist())
    assert names == ["lkap-conn-b/README.md", "lkap-conn-b/livekit.toml", "lkap-conn-b/secrets.env"]
    toml = archive.read("lkap-conn-b/livekit.toml").decode()
    secrets = archive.read("lkap-conn-b/secrets.env").decode()
    assert 'subdomain = "project-b"' in toml
    assert "LKAP_API_BASE_URL=https://lkap.example.com" in secrets
    assert "LKAP_SERVICE_TOKEN=<LKAP_SERVICE_TOKEN>" in secrets
    everything = "".join(archive.read(name).decode() for name in names)
    assert SECRET_B not in everything and KEY_B not in everything
    assert settings.service_token not in everything
