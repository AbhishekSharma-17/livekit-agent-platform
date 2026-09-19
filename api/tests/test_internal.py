"""The worker-only surface: resolved configuration, events and summaries.

These tests carry the two security acceptance criteria of W1-API-CORE: the
resolved payload is the *only* place a decrypted secret appears, and it is
never written to a log line.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from conftest import captured_text, create_agent, inference_config
from lkap_contracts.agent_config import ResolvedAgentConfig
from sqlalchemy import select

from lkap_api.db.models import Session as SessionRow
from lkap_api.db.models import SessionEvent
from lkap_api.db.session import Database

API_KEY = "sk-resolved-only-3c7f"
TOOL_SECRET = "tool-bearer-token-55aa"


async def _credential(client: httpx.AsyncClient, provider_id: str, secrets: dict[str, str]) -> str:
    response = await client.post(
        "/v1/credentials", json={"provider_id": provider_id, "label": provider_id, "secrets": secrets}
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def _session_for(
    admin_client: httpx.AsyncClient, config: dict[str, Any] | None = None
) -> tuple[str, dict[str, Any]]:
    agent = await create_agent(
        admin_client, name="Worker agent", config=config or json.loads(inference_config().model_dump_json())
    )
    response = await admin_client.post(f"/v1/agents/{agent['id']}/connect", json={})
    return str(response.json()["sessionId"]), agent


def _openai_config(credential_id: str) -> dict[str, Any]:
    return {
        "v": 1,
        "instructions": "Be brief.",
        "pipeline": {
            "mode": "cascaded",
            "stt": {"provider_id": "livekit-inference-stt"},
            "llm": {
                "provider_id": "openai-llm",
                "credential_id": credential_id,
                "model": "gpt-4.1",
                "fields": {"temperature": 0.2},
            },
            "tts": {"provider_id": "livekit-inference-tts"},
        },
    }


# ------------------------------------------------------------------------------- auth
async def test_resolved_requires_the_service_token(
    client: httpx.AsyncClient, admin_client: httpx.AsyncClient
) -> None:
    session_id, _ = await _session_for(admin_client)

    unauthenticated = await client.get(f"/internal/v1/sessions/{session_id}/resolved")
    as_admin = await admin_client.get(f"/internal/v1/sessions/{session_id}/resolved")

    assert unauthenticated.status_code == 401
    assert as_admin.status_code == 401
    assert as_admin.json()["error"]["code"] == "unauthorized"


async def test_an_admin_token_never_reaches_the_resolved_payload(admin_client: httpx.AsyncClient) -> None:
    credential_id = await _credential(admin_client, "openai-llm", {"api_key": API_KEY})
    session_id, _ = await _session_for(admin_client, _openai_config(credential_id))

    response = await admin_client.get(f"/internal/v1/sessions/{session_id}/resolved")

    assert response.status_code == 401
    assert API_KEY not in response.text


# --------------------------------------------------------------------------- resolving
async def test_resolved_contains_the_decrypted_credential(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient
) -> None:
    credential_id = await _credential(admin_client, "openai-llm", {"api_key": API_KEY})
    session_id, agent = await _session_for(admin_client, _openai_config(credential_id))

    response = await service_client.get(f"/internal/v1/sessions/{session_id}/resolved")

    assert response.status_code == 200, response.text
    resolved = ResolvedAgentConfig.model_validate(response.json())
    assert resolved.session_id == session_id
    assert resolved.agent_id == agent["id"]
    assert resolved.agent_slug == agent["slug"]
    assert resolved.config_version == agent["config_version"]
    llm = resolved.resolved["llm"]
    assert llm.python_class == "livekit.plugins.openai.LLM"
    assert llm.model == "gpt-4.1"
    assert llm.kwargs["api_key"] == API_KEY
    assert llm.kwargs["temperature"] == 0.2


async def test_the_decrypted_secret_is_never_logged(
    admin_client: httpx.AsyncClient,
    service_client: httpx.AsyncClient,
    log_capture: pytest.LogCaptureFixture,
) -> None:
    credential_id = await _credential(admin_client, "openai-llm", {"api_key": API_KEY})
    session_id, _ = await _session_for(admin_client, _openai_config(credential_id))

    response = await service_client.get(f"/internal/v1/sessions/{session_id}/resolved")

    assert API_KEY in response.text
    assert API_KEY not in captured_text(log_capture)
    assert API_KEY not in captured_text(log_capture, scope=None)


async def test_inference_providers_are_resolved_without_a_key(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient
) -> None:
    session_id, _ = await _session_for(admin_client)

    resolved = ResolvedAgentConfig.model_validate(
        (await service_client.get(f"/internal/v1/sessions/{session_id}/resolved")).json()
    )

    assert set(resolved.resolved) == {"stt", "llm", "tts", "workflow_llm"}
    assert "api_key" not in resolved.resolved["stt"].kwargs
    assert resolved.resolved["stt"].model == "deepgram/nova-3"
    assert resolved.resolved["tts"].kwargs == {"voice": "Ashley", "language": "en"}
    assert resolved.resolved["workflow_llm"].provider_id == "livekit-inference-llm"


async def test_realtime_mode_resolves_only_the_realtime_slot(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient
) -> None:
    credential_id = await _credential(admin_client, "google-realtime", {"api_key": API_KEY})
    config = {
        "v": 1,
        "instructions": "Be brief.",
        "pipeline": {
            "mode": "realtime",
            "realtime": {
                "provider_id": "google-realtime",
                "credential_id": credential_id,
                "fields": {"voice": "Puck"},
            },
        },
    }
    session_id, _ = await _session_for(admin_client, config)

    resolved = ResolvedAgentConfig.model_validate(
        (await service_client.get(f"/internal/v1/sessions/{session_id}/resolved")).json()
    )

    assert set(resolved.resolved) == {"realtime", "workflow_llm"}
    realtime = resolved.resolved["realtime"]
    assert realtime.kwargs["voice"] == "Puck"
    assert realtime.kwargs["api_key"] == API_KEY
    assert realtime.kwargs["tool_behavior"] == "NON_BLOCKING"
    assert resolved.resolved["workflow_llm"].provider_id == "livekit-inference-llm"


async def test_tool_secrets_are_substituted_and_credential_ids_dropped(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient
) -> None:
    credential_id = await _credential(admin_client, "http-tool-secret", {"WEATHER_KEY": TOOL_SECRET})
    tool = (
        await admin_client.post(
            "/v1/tools",
            json={
                "kind": "http",
                "name": "get_weather",
                "definition": {
                    "kind": "http",
                    "name": "get_weather",
                    "description": "Current weather",
                    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
                    "method": "GET",
                    "url": "https://api.example.com/weather/{{ city }}",
                    "headers": {"Authorization": "Bearer {{ secret.WEATHER_KEY }}"},
                    "credential_id": credential_id,
                    "allowed_hosts": ["api.example.com"],
                },
            },
        )
    ).json()
    config = json.loads(inference_config().model_dump_json())
    config["tools"]["tool_ids"] = [tool["id"]]
    session_id, _ = await _session_for(admin_client, config)

    resolved = ResolvedAgentConfig.model_validate(
        (await service_client.get(f"/internal/v1/sessions/{session_id}/resolved")).json()
    )

    assert len(resolved.tools) == 1
    definition = resolved.tools[0]
    assert definition.headers["Authorization"] == f"Bearer {TOOL_SECRET}"
    assert definition.credential_id is None
    assert "{{ city }}" in definition.url


async def test_disabled_tools_are_not_resolved(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient
) -> None:
    tool = (
        await admin_client.post(
            "/v1/tools",
            json={
                "kind": "mcp",
                "name": "docs",
                "enabled": False,
                "definition": {"kind": "mcp", "name": "docs", "url": "https://mcp.example.com"},
            },
        )
    ).json()
    config = json.loads(inference_config().model_dump_json())
    config["tools"]["tool_ids"] = [tool["id"]]
    session_id, _ = await _session_for(admin_client, config)

    resolved = ResolvedAgentConfig.model_validate(
        (await service_client.get(f"/internal/v1/sessions/{session_id}/resolved")).json()
    )

    assert resolved.tools == []


async def test_resolving_marks_the_session_active(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient, database: Database
) -> None:
    session_id, _ = await _session_for(admin_client)

    await service_client.get(f"/internal/v1/sessions/{session_id}/resolved")

    async with database.session() as session:
        row = (await session.execute(select(SessionRow))).scalar_one()
    assert row.status == "active"
    assert row.started_at is not None


async def test_resolving_an_unknown_session_is_404(service_client: httpx.AsyncClient) -> None:
    response = await service_client.get("/internal/v1/sessions/ghost/resolved")

    assert response.status_code == 404


async def test_resolving_a_finished_session_is_409(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient
) -> None:
    session_id, _ = await _session_for(admin_client)
    await service_client.put(
        f"/internal/v1/sessions/{session_id}/summary",
        json={"status": "ended", "usage": {}, "transcript": []},
    )

    response = await service_client.get(f"/internal/v1/sessions/{session_id}/resolved")

    assert response.status_code == 409


# ------------------------------------------------------------------- events + summary
async def test_events_are_appended_in_order(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient, database: Database
) -> None:
    session_id, _ = await _session_for(admin_client)

    response = await service_client.post(
        f"/internal/v1/sessions/{session_id}/events",
        json={
            "events": [
                {"ts": 1758000000.0, "type": "session_started", "payload": {}},
                {"ts": 1758000001.5, "type": "user_turn", "payload": {"text": "hello"}},
            ]
        },
    )

    assert response.status_code == 202
    async with database.session() as session:
        rows = (await session.execute(select(SessionEvent).order_by(SessionEvent.id))).scalars().all()
    assert [r.type for r in rows] == ["session_started", "user_turn"]
    assert rows[1].payload == {"text": "hello"}


async def test_events_require_the_service_token(
    admin_client: httpx.AsyncClient, client: httpx.AsyncClient
) -> None:
    session_id, _ = await _session_for(admin_client)

    response = await client.post(f"/internal/v1/sessions/{session_id}/events", json={"events": []})

    assert response.status_code == 401


async def test_summary_closes_the_session(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient, database: Database
) -> None:
    session_id, _ = await _session_for(admin_client)

    response = await service_client.put(
        f"/internal/v1/sessions/{session_id}/summary",
        json={
            "status": "ended",
            "usage": {"llm_prompt_tokens": 120},
            "transcript": [{"role": "user", "text": "hello", "ts": 1758000000.0}],
            "final_ui_state": {"v": 1, "status": {"label": "Done", "tone": "success"}},
        },
    )

    assert response.status_code == 204
    async with database.session() as session:
        row = (await session.execute(select(SessionRow))).scalar_one()
    assert row.status == "ended"
    assert row.ended_at is not None
    assert row.usage == {"llm_prompt_tokens": 120}
    assert row.transcript is not None and row.transcript[0]["text"] == "hello"
    assert row.final_ui_state is not None and row.final_ui_state["status"]["label"] == "Done"


async def test_summary_records_a_failure(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient, database: Database
) -> None:
    session_id, _ = await _session_for(admin_client)

    await service_client.put(
        f"/internal/v1/sessions/{session_id}/summary",
        json={"status": "failed", "usage": {}, "transcript": [], "error": "provider unavailable"},
    )

    async with database.session() as session:
        row = (await session.execute(select(SessionRow))).scalar_one()
    assert (row.status, row.error) == ("failed", "provider unavailable")
