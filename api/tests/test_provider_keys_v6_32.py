"""V6-32: one vendor key per family, the key list's test and use times.

Offline: vendor calls go to the ``mock_http`` transport; every key value is a placeholder.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

import httpx
import pytest
from conftest import inference_config
from lkap_contracts.agent_config import ProviderRef, ResolvedAgentConfig
from sqlalchemy import select, update

from lkap_api.config_service import validate_agent_config
from lkap_api.custom_models.records import records_for_workspace
from lkap_api.db.constants import DEFAULT_WORKSPACE_ID
from lkap_api.db.models import Credential, LiveKitConnection, ProviderModel, new_id, utcnow
from lkap_api.db.session import Database
from lkap_api.key_usage import THROTTLE_S, mark_used
from lkap_api.settings import Settings
from lkap_api.vault import Vault, fingerprint

DEEPGRAM_KEY = "dg-placeholder-key-0001"


async def _create(
    admin_client: httpx.AsyncClient, provider_id: str, api_key: str = DEEPGRAM_KEY
) -> dict[str, Any]:
    response = await admin_client.post(
        "/v1/credentials",
        json={"provider_id": provider_id, "label": provider_id, "secrets": {"api_key": api_key}},
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def _stored_row(database: Database, settings: Settings, provider_id: str, api_key: str) -> str:
    """A row stored with ``provider_id`` as-is (the shape of a key added before V6-32)."""
    secrets = {"api_key": api_key}
    async with database.session() as session:
        row = Credential(
            workspace_id=DEFAULT_WORKSPACE_ID,
            provider_id=provider_id,
            label=f"{provider_id} (before V6-32)",
            ciphertext=Vault(settings.master_key).encrypt(secrets),
            fingerprint=fingerprint(secrets, primary_field="api_key"),
        )
        session.add(row)
        await session.commit()
        return row.id


@pytest.fixture
async def full_image(database: Database) -> None:
    """Deepgram's voice is a full-image entry: run the workspace's connection on that image."""
    async with database.session() as session:
        await session.execute(update(LiveKitConnection).values(worker_image="full"))


async def _agent(admin_client: httpx.AsyncClient, config: dict[str, Any]) -> dict[str, Any]:
    response = await admin_client.post("/v1/agents", json={"name": "Deepgram voice", "config": config})
    assert response.status_code == 201, response.text
    agent: dict[str, Any] = response.json()
    return agent


async def _errors(admin_client: httpx.AsyncClient, agent_id: str) -> list[str]:
    response = await admin_client.post(f"/v1/agents/{agent_id}/validate")
    assert response.status_code == 200, response.text
    errors: list[str] = response.json()["errors"]
    return errors


async def _session(admin_client: httpx.AsyncClient, config: dict[str, Any]) -> str:
    agent = await _agent(admin_client, config)
    await admin_client.put(f"/v1/agents/{agent['id']}", json={"published": True})
    response = await admin_client.post(f"/v1/agents/{agent['id']}/connect", json={})
    return str(response.json()["sessionId"])


def _deepgram_voice_config(stt_credential: str, tts_credential: str) -> dict[str, Any]:
    config = inference_config()
    config.pipeline.stt = ProviderRef(
        provider_id="deepgram-stt", credential_id=stt_credential, model="nova-3"
    )
    config.pipeline.tts = ProviderRef(provider_id="deepgram-tts", credential_id=tts_credential)
    body: dict[str, Any] = json.loads(config.model_dump_json())
    return body


# ------------------------------------------------------------------------------ problem 1
async def test_a_deepgram_stt_key_validates_for_the_deepgram_voice(
    admin_client: httpx.AsyncClient, full_image: None
) -> None:
    created = await _create(admin_client, "deepgram-stt")

    agent = await _agent(admin_client, _deepgram_voice_config(created["id"], created["id"]))

    assert await _errors(admin_client, agent["id"]) == []


async def test_a_key_added_from_the_deepgram_voice_is_stored_under_the_deepgram_home(
    admin_client: httpx.AsyncClient,
) -> None:
    created = await _create(admin_client, "deepgram-tts")

    assert created["provider_id"] == "deepgram-stt"


async def test_a_deepgram_stt_key_reaches_the_voice_as_its_api_key(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient, full_image: None
) -> None:
    created = await _create(admin_client, "deepgram-stt")
    session_id = await _session(admin_client, _deepgram_voice_config(created["id"], created["id"]))

    resolved = ResolvedAgentConfig.model_validate(
        (await service_client.get(f"/internal/v1/sessions/{session_id}/resolved")).json()
    )

    tts = resolved.resolved["tts"]
    assert tts.provider_id == "deepgram-tts"
    assert tts.python_class == "livekit.plugins.deepgram.TTS"
    assert tts.kwargs["api_key"] == DEEPGRAM_KEY


async def test_a_key_stored_under_the_voice_before_v6_32_still_serves_the_whole_family(
    admin_client: httpx.AsyncClient, database: Database, settings: Settings, full_image: None
) -> None:
    legacy = await _stored_row(database, settings, "deepgram-tts", DEEPGRAM_KEY)

    for provider_id in ("deepgram-stt", "deepgram-flux-stt", "deepgram-tts"):
        page = (await admin_client.get("/v1/credentials", params={"provider_id": provider_id})).json()
        assert [item["id"] for item in page["items"]] == [legacy], provider_id
    agent = await _agent(admin_client, _deepgram_voice_config(legacy, legacy))
    assert await _errors(admin_client, agent["id"]) == []


@pytest.mark.parametrize(
    ("stored_under", "used_by"),
    [
        ("deepgram-stt", "cartesia-tts"),
        ("cartesia-tts", "deepgram-tts"),
        ("openrouter-llm", "openai-llm"),
        ("openai-llm", "azure-openai-realtime"),
    ],
)
def test_a_key_of_another_vendor_is_still_refused(stored_under: str, used_by: str) -> None:
    config = inference_config()
    kind = "realtime" if used_by.endswith("realtime") else ("llm" if used_by.endswith("llm") else "tts")
    ref = ProviderRef(provider_id=used_by, credential_id="key-1")
    if kind == "realtime":
        config.pipeline.mode = "realtime"
        config.pipeline.realtime = ref
    else:
        setattr(config.pipeline, kind, ref)

    result = validate_agent_config(config, credential_providers={"key-1": stored_under})

    assert any("belongs to provider" in message for message in result.errors), result.errors


def test_an_openai_voice_key_stored_before_v6_32_still_counts_as_an_openai_key() -> None:
    config = inference_config()
    config.pipeline.llm = ProviderRef(provider_id="openai-llm", credential_id="key-1", model="gpt-4.1")

    result = validate_agent_config(config, credential_providers={"key-1": "openai-tts"})

    assert [issue for issue in result.issues if issue.severity == "error"] == []


async def test_a_model_tested_under_a_family_member_counts_for_the_home(database: Database) -> None:
    now = utcnow()
    async with database.session() as session:
        session.add(
            ProviderModel(
                id=new_id(),
                workspace_id=DEFAULT_WORKSPACE_ID,
                provider_id="openai-responses-llm",
                provider_home="openai-responses-llm",
                kind="llm",
                model_id="gpt-custom-1",
                last_test_at=now,
                last_test_ok=True,
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()
    async with database.session() as session:
        records = await records_for_workspace(session, workspace_id=DEFAULT_WORKSPACE_ID)

    assert records[("openai-llm", "llm", "gpt-custom-1")].last_test_ok is True


# ------------------------------------------------------------------------------ problem 3
async def test_the_key_list_returns_the_recorded_test_result(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    """The root cause of "Not tested": `CredentialOut` never carried the recorded test."""
    created = await _create(admin_client, "bey-avatar", api_key="bey-placeholder-0001")
    tested_at = dt.datetime(2026, 9, 29, 10, 0, tzinfo=dt.UTC)
    async with database.session() as session:
        row = await session.get(Credential, created["id"])
        assert row is not None
        row.last_test_at, row.last_test_ok, row.last_test_message = tested_at, True, "Beyond responded"
        await session.commit()

    (item,) = (await admin_client.get("/v1/credentials", params={"provider_id": "bey-avatar"})).json()[
        "items"
    ]
    assert item["last_test_ok"] is True
    assert item["last_test_at"] == "2026-09-29T10:00:00Z"
    assert item["last_test_message"] == "Beyond responded"
    assert item["last_used_at"] is None
    one = (await admin_client.get(f"/v1/credentials/{created['id']}")).json()
    assert one["last_test_ok"] is True


async def test_a_test_through_the_vendor_table_is_recorded_like_an_adapter_test(
    admin_client: httpx.AsyncClient, mock_http: list[httpx.Request]
) -> None:
    """The add flow's "Test key" for Deepgram went through `_TEST_CALLS`, which never persisted."""
    created = await _create(admin_client, "deepgram-stt")

    result = (await admin_client.post(f"/v1/credentials/{created['id']}/test")).json()

    assert result["ok"] is True
    assert mock_http[0].url.host == "api.deepgram.com"
    assert mock_http[0].headers["Authorization"] == f"Token {DEEPGRAM_KEY}"
    (item,) = (await admin_client.get("/v1/credentials", params={"provider_id": "deepgram-stt"})).json()[
        "items"
    ]
    assert item["last_test_ok"] is True
    assert item["last_test_at"] is not None
    assert item["last_test_message"] == "Deepgram responded with HTTP 200"


async def test_a_legacy_voice_key_is_tested_with_the_deepgram_check(
    admin_client: httpx.AsyncClient, database: Database, settings: Settings, mock_http: list[httpx.Request]
) -> None:
    legacy = await _stored_row(database, settings, "deepgram-tts", DEEPGRAM_KEY)

    result = (await admin_client.post(f"/v1/credentials/{legacy}/test")).json()

    assert result["ok"] is True
    assert [request.url.host for request in mock_http] == ["api.deepgram.com"]


async def test_a_provider_without_an_automatic_test_is_recorded_as_untested(
    admin_client: httpx.AsyncClient, mock_http: list[httpx.Request]
) -> None:
    created = await _create(admin_client, "did-avatar", api_key="did-placeholder-0001")

    await admin_client.post(f"/v1/credentials/{created['id']}/test")

    one = (await admin_client.get(f"/v1/credentials/{created['id']}")).json()
    assert one["last_test_ok"] is None
    assert one["last_test_at"] is not None
    assert "no automated test" in one["last_test_message"]
    assert mock_http == []


async def test_a_session_resolve_stamps_last_used(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient, full_image: None
) -> None:
    created = await _create(admin_client, "deepgram-stt")
    session_id = await _session(admin_client, _deepgram_voice_config(created["id"], created["id"]))

    await service_client.get(f"/internal/v1/sessions/{session_id}/resolved")

    one = (await admin_client.get(f"/v1/credentials/{created['id']}")).json()
    assert one["last_used_at"] is not None
    assert one["updated_at"] == created["updated_at"], "a use is not an edit"


async def test_mark_used_skips_a_key_used_within_the_throttle_window(
    database: Database, settings: Settings
) -> None:
    credential_id = await _stored_row(database, settings, "deepgram-stt", DEEPGRAM_KEY)
    first = dt.datetime(2026, 9, 30, 12, 0, tzinfo=dt.UTC)

    async with database.session() as session:
        await mark_used(session, [credential_id, None], now=first)
        await session.commit()
    async with database.session() as session:
        await mark_used(session, [credential_id], now=first + dt.timedelta(seconds=THROTTLE_S - 1))
        await session.commit()
        stamped = await session.scalar(select(Credential.last_used_at).where(Credential.id == credential_id))
    assert stamped == first

    async with database.session() as session:
        later = first + dt.timedelta(seconds=THROTTLE_S + 1)
        await mark_used(session, [credential_id], now=later)
        await session.commit()
        stamped = await session.scalar(select(Credential.last_used_at).where(Credential.id == credential_id))
    assert stamped == later
