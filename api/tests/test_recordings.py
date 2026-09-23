"""Tests for `lkap_api.recordings`: start-recording decision tree and finalisation (PLAN-V2 V2-12)."""

from __future__ import annotations

from typing import Any

import httpx
from conftest import inference_config
from connection_fakes import add_agent, connection_row, fake_livekit
from livekit.protocol.egress import EgressInfo, EgressStatus, FileInfo
from livekit.protocol.webhook import WebhookEvent
from lkap_contracts.agent_config import RecordingConfig
from sqlalchemy import select

from lkap_api.connections.webhooks import WebhookContext, dispatch_webhook
from lkap_api.db.models import Job, StorageConfig
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.session import Database
from lkap_api.jobs.kinds import RECORDING_FINALIZE
from lkap_api.recordings.finalize import schedule_finalize_job, status_from_egress
from lkap_api.recordings.storage import NoEgressStorageError, resolve_egress_target
from lkap_api.settings import Settings
from lkap_api.vault import Vault


async def _seed(
    database: Database,
    settings: Settings,
    *,
    recording: dict[str, Any] | None = None,
    connection_capabilities: dict[str, Any] | None = None,
    storage_config_id: str | None = "__default__",
) -> tuple[str, str, str]:
    """Create a connection (+ optional s3 storage config), an agent and a session; return ids."""
    vault = Vault(settings.master_key)
    async with database.session() as session:
        storage_id = None
        if storage_config_id is not None:
            storage = StorageConfig(
                workspace_id="00000000000000000000000000000001",
                name="Test MinIO",
                kind="s3",
                bucket="lkap-recordings",
                region="us-east-1",
                endpoint_url="http://minio.test:9000",
                access_key_ct=vault.encrypt({"access_key": "AKIA_TEST"}),
                secret_key_ct=vault.encrypt({"secret_key": "secret_test_value"}),
                is_default=1,
            )
            session.add(storage)
            await session.flush()
            storage_id = storage.id
        conn = connection_row(
            vault,
            capabilities=connection_capabilities or {"egress_enabled": True},
            storage_config_id=storage_id,
        )
        session.add(conn)
        await session.flush()
        config = inference_config(recording=RecordingConfig(**(recording or {"enabled": True})))
        agent = await add_agent(session, config, connection_id=conn.id)
        row = SessionRow(
            id="a" * 32,
            agent_id=agent.id,
            connection_id=conn.id,
            config_version=1,
            room_name="lkap-rec-test",
            participant_identity="u",
            participant_name="U",
            status="active",
            pipeline_mode="cascaded",
        )
        session.add(row)
        await session.flush()
    return conn.id, agent.id, row.id


async def test_start_recording_conflicts_when_recording_not_enabled(
    service_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    _connection_id, _agent_id, session_id = await _seed(database, settings, recording={"enabled": False})

    response = await service_client.post(f"/internal/v1/sessions/{session_id}/recording/start")

    assert response.status_code == 409, response.text


async def test_start_recording_422_when_egress_not_enabled(
    service_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    _connection_id, _agent_id, session_id = await _seed(
        database, settings, connection_capabilities={"egress_enabled": False}
    )

    response = await service_client.post(f"/internal/v1/sessions/{session_id}/recording/start")

    assert response.status_code == 422, response.text


async def test_start_recording_422_when_no_s3_storage_configured(
    service_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    _connection_id, _agent_id, session_id = await _seed(database, settings, storage_config_id=None)

    response = await service_client.post(f"/internal/v1/sessions/{session_id}/recording/start")

    assert response.status_code == 422, response.text
    assert "S3" in response.json()["error"]["message"]


async def test_resolve_egress_target_raises_for_a_local_storage_config(
    database: Database, settings: Settings
) -> None:
    vault = Vault(settings.master_key)
    async with database.session() as session:
        storage = StorageConfig(
            workspace_id="00000000000000000000000000000001", name="Local", kind="local", bucket=""
        )
        session.add(storage)
        await session.flush()
        conn = connection_row(vault, storage_config_id=storage.id)
        session.add(conn)
        await session.flush()

        try:
            await resolve_egress_target(
                session, vault, workspace_id=conn.workspace_id, storage_config_id=None, connection=conn
            )
        except NoEgressStorageError:
            pass
        else:
            raise AssertionError("expected NoEgressStorageError")


async def test_start_recording_succeeds_and_is_idempotent_on_retry(
    service_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    async with fake_livekit() as (fake, url):
        vault = Vault(settings.master_key)
        async with database.session() as session:
            storage = StorageConfig(
                workspace_id="00000000000000000000000000000001",
                name="MinIO",
                kind="s3",
                bucket="lkap-recordings",
                access_key_ct=vault.encrypt({"access_key": "AKIA_TEST"}),
                secret_key_ct=vault.encrypt({"secret_key": "secret_test_value"}),
                is_default=1,
            )
            session.add(storage)
            await session.flush()
            conn = connection_row(
                vault, url=url, capabilities={"egress_enabled": True}, storage_config_id=storage.id
            )
            session.add(conn)
            await session.flush()
            config = inference_config(recording=RecordingConfig(enabled=True))
            agent = await add_agent(session, config, connection_id=conn.id)
            row = SessionRow(
                id="b" * 32,
                agent_id=agent.id,
                connection_id=conn.id,
                config_version=1,
                room_name="lkap-rec-ok",
                participant_identity="u",
                participant_name="U",
                status="active",
                pipeline_mode="cascaded",
            )
            session.add(row)
            await session.flush()
            session_id = row.id

        first = await service_client.post(f"/internal/v1/sessions/{session_id}/recording/start")
        assert first.status_code == 200, first.text
        # The in-process Twirp fake (`connection_fakes.FakeLiveKit`) answers
        # every call with an empty protobuf body, which parses as a valid
        # `EgressInfo` with `egress_id=""` — a real LiveKit server always
        # returns a real id, so this asserts the field is *present and typed*
        # rather than truthy.
        assert "egress_id" in first.json()
        egress_id = first.json()["egress_id"]

        async with database.session() as session:
            reloaded = await session.get(SessionRow, session_id)
            assert reloaded is not None
            assert reloaded.recording_status == "active"
            assert reloaded.recording_egress_id == egress_id
            assert reloaded.recording_object_key == f"recordings/{session_id}.ogg"

        calls_before = len(fake.calls)
        second = await service_client.post(f"/internal/v1/sessions/{session_id}/recording/start")
        assert second.status_code == 200, second.text
        assert second.json()["egress_id"] == egress_id
        # A retried `recording/start` must not fire a second Egress request.
        assert len(fake.calls) == calls_before


def test_status_from_egress_maps_every_known_status() -> None:
    assert status_from_egress(int(EgressStatus.EGRESS_STARTING)) == "active"
    assert status_from_egress(int(EgressStatus.EGRESS_COMPLETE)) == "ready"
    assert status_from_egress(int(EgressStatus.EGRESS_FAILED)) == "failed"
    assert status_from_egress(int(EgressStatus.EGRESS_ABORTED)) == "failed"
    assert status_from_egress(int(EgressStatus.EGRESS_LIMIT_REACHED)) == "ready"
    assert status_from_egress(999) == "active"


async def test_egress_ended_webhook_sets_duration_and_schedules_finalize_job(
    database: Database, settings: Settings
) -> None:
    vault = Vault(settings.master_key)
    async with database.session() as session:
        conn = connection_row(vault)
        session.add(conn)
        await session.flush()
        agent = await add_agent(session, inference_config(), connection_id=conn.id)
        row = SessionRow(
            id="c" * 32,
            agent_id=agent.id,
            connection_id=conn.id,
            config_version=1,
            room_name="lkap-egress-webhook",
            participant_identity="u",
            participant_name="U",
            status="ended",
            pipeline_mode="cascaded",
            recording_egress_id="EG_XYZ",
            recording_status="active",
            recording_object_key="recordings/c" + "c" * 31 + ".ogg",
        )
        session.add(row)
        await session.flush()

        info = EgressInfo(
            egress_id="EG_XYZ",
            status=EgressStatus.EGRESS_COMPLETE,
            file_results=[FileInfo(duration=90_000_000_000)],  # 90s, nanoseconds
        )
        event = WebhookEvent(event="egress_ended", egress_info=info)
        ran = await dispatch_webhook(WebhookContext(db=session, connection=conn, event=event))
        await session.flush()

    assert ran >= 1
    async with database.session() as verify:
        reloaded = await verify.get(SessionRow, "c" * 32)
        assert reloaded is not None
        assert reloaded.recording_status == "ready"
        assert reloaded.recording_duration_s == 90.0

        job_row = (await verify.execute(select(Job).where(Job.kind == RECORDING_FINALIZE))).scalars().first()
        assert job_row is not None
        assert job_row.payload["session_id"] == "c" * 32


async def test_schedule_finalize_job_inserts_a_pending_row(database: Database) -> None:
    async with database.session() as session:
        schedule_finalize_job(session, "some-session-id")
        await session.flush()
        row = (await session.execute(select(Job).where(Job.kind == RECORDING_FINALIZE))).scalars().first()
        assert row is not None
        assert row.status == "pending"
        assert row.payload == {"session_id": "some-session-id"}
