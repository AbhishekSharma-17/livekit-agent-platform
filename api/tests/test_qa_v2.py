"""R-V2-5/R-V2-6 follow-up: `PUT /internal/v1/sessions/{id}/qa` and the `qa_llm` resolve slot.

Kept in its own file (rather than extending `test_internal.py`) since that
file's `PUT .../summary` neighbourhood was being edited concurrently by
another package while this follow-up landed.
"""

from __future__ import annotations

import httpx
import respx
from lkap_contracts.agent_config import ResolvedAgentConfig
from sqlalchemy import select
from test_internal import API_KEY, _credential, _openai_config, _session_for

from lkap_api.db.models import WebhookDelivery
from lkap_api.db.session import Database


async def test_put_qa_requires_the_service_token(admin_client: httpx.AsyncClient) -> None:
    response = await admin_client.put("/internal/v1/sessions/does-not-exist/qa", json={"status": "skipped"})
    assert response.status_code == 401


async def test_put_qa_unknown_session_is_404(service_client: httpx.AsyncClient) -> None:
    response = await service_client.put("/internal/v1/sessions/does-not-exist/qa", json={"status": "skipped"})
    assert response.status_code == 404


async def test_put_qa_done_verdict_is_stored_with_scored_by_worker(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient
) -> None:
    session_id, _ = await _session_for(admin_client)

    response = await service_client.put(
        f"/internal/v1/sessions/{session_id}/qa",
        json={
            "status": "done",
            "score": 8,
            "sentiment": "positive",
            "tags": ["DEAD_AIR"],
            "summary": "Handled well.",
            "raw": {"score": 8, "sentiment": "positive", "tags": ["DEAD_AIR"], "summary": "Handled well."},
            "model": "livekit-inference-llm:google/gemma-4-31b-it",
        },
    )
    assert response.status_code == 204, response.text

    detail = await admin_client.get(f"/v1/sessions/{session_id}")
    qa = detail.json()["qa"]
    assert qa["status"] == "done"
    assert qa["score"] == 8
    assert qa["sentiment"] == "positive"
    assert qa["tags"] == ["DEAD_AIR"]
    assert qa["model"] == "livekit-inference-llm:google/gemma-4-31b-it"
    assert qa.get("scored_by") == "worker"


async def test_put_qa_skipped_verdict_is_stored(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient
) -> None:
    session_id, _ = await _session_for(admin_client)

    response = await service_client.put(f"/internal/v1/sessions/{session_id}/qa", json={"status": "skipped"})
    assert response.status_code == 204

    detail = await admin_client.get(f"/v1/sessions/{session_id}")
    assert detail.json()["qa"]["status"] == "skipped"


async def test_put_qa_failed_verdict_carries_the_error(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient
) -> None:
    session_id, _ = await _session_for(admin_client)

    response = await service_client.put(
        f"/internal/v1/sessions/{session_id}/qa", json={"status": "failed", "error": "qa_llm not resolved"}
    )
    assert response.status_code == 204

    detail = await admin_client.get(f"/v1/sessions/{session_id}")
    qa = detail.json()["qa"]
    assert qa["status"] == "failed"


async def test_put_qa_upserts_a_second_report(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient
) -> None:
    session_id, _ = await _session_for(admin_client)

    await service_client.put(f"/internal/v1/sessions/{session_id}/qa", json={"status": "failed"})
    second = await service_client.put(
        f"/internal/v1/sessions/{session_id}/qa", json={"status": "done", "score": 5, "sentiment": "neutral"}
    )
    assert second.status_code == 204

    detail = await admin_client.get(f"/v1/sessions/{session_id}")
    assert detail.json()["qa"]["status"] == "done"
    assert detail.json()["qa"]["score"] == 5


# --------------------------------------------------------------------------- V2-20-1: session.qa_completed
async def test_put_qa_emits_session_qa_completed(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient, database: Database
) -> None:
    created = await admin_client.post(
        "/v1/webhooks",
        json={"url": "https://hooks.example.com/qa-worker", "events": ["session.qa_completed"]},
    )
    assert created.status_code == 201, created.text
    session_id, _ = await _session_for(admin_client)

    with respx.mock:
        respx.post("https://hooks.example.com/qa-worker").mock(return_value=httpx.Response(200))
        response = await service_client.put(
            f"/internal/v1/sessions/{session_id}/qa",
            json={"status": "done", "score": 9, "sentiment": "positive"},
        )
    assert response.status_code == 204

    async with database.session() as session:
        deliveries = (await session.execute(select(WebhookDelivery))).scalars().all()
    (delivery,) = [d for d in deliveries if d.event_type == "session.qa_completed"]
    assert delivery.payload["data"]["session_id"] == session_id
    assert delivery.payload["data"]["score"] == 9
    assert delivery.payload["data"]["scored_by"] == "worker"


async def test_put_qa_skipped_still_emits_session_qa_completed(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient, database: Database
) -> None:
    created = await admin_client.post(
        "/v1/webhooks", json={"url": "https://hooks.example.com/qa-skip", "events": []}
    )
    assert created.status_code == 201, created.text
    session_id, _ = await _session_for(admin_client)

    with respx.mock:
        respx.post("https://hooks.example.com/qa-skip").mock(return_value=httpx.Response(200))
        response = await service_client.put(
            f"/internal/v1/sessions/{session_id}/qa", json={"status": "skipped"}
        )
    assert response.status_code == 204

    async with database.session() as session:
        deliveries = (await session.execute(select(WebhookDelivery))).scalars().all()
    (delivery,) = [d for d in deliveries if d.event_type == "session.qa_completed"]
    assert delivery.payload["data"]["status"] == "skipped"


# --------------------------------------------------------------------------- qa_llm resolve slot (R-V2-6)
async def test_qa_llm_slot_carries_the_decrypted_vendor_key(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient
) -> None:
    """R-V2-6 named test: a vendor-key judge carries the decrypted key."""
    credential_id = await _credential(admin_client, "openai-llm", {"api_key": API_KEY})
    config = _openai_config(credential_id)
    config["qa"] = {
        "enabled": True,
        "model": {"provider_id": "openai-llm", "credential_id": credential_id, "model": "gpt-4.1"},
    }
    session_id, _ = await _session_for(admin_client, config)

    resolved = ResolvedAgentConfig.model_validate(
        (await service_client.get(f"/internal/v1/sessions/{session_id}/resolved")).json()
    )

    qa_llm = resolved.resolved["qa_llm"]
    assert qa_llm.provider_id == "openai-llm"
    assert qa_llm.kwargs["api_key"] == API_KEY
    assert qa_llm.model == "gpt-4.1"


async def test_qa_llm_falls_back_to_workflow_llm_when_qa_model_is_unset(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient
) -> None:
    config = _openai_config(await _credential(admin_client, "openai-llm", {"api_key": API_KEY}))
    config["qa"] = {"enabled": True}
    session_id, _ = await _session_for(admin_client, config)

    resolved = ResolvedAgentConfig.model_validate(
        (await service_client.get(f"/internal/v1/sessions/{session_id}/resolved")).json()
    )

    assert resolved.resolved["qa_llm"].provider_id == resolved.resolved["workflow_llm"].provider_id


async def test_qa_llm_in_half_cascade_never_resolves_to_the_realtime_provider(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient
) -> None:
    """R-V2-6 named test: half-cascade never resolves `qa_llm` to the realtime provider."""
    credential_id = await _credential(admin_client, "google-realtime", {"api_key": API_KEY})
    config = {
        "v": 1,
        "instructions": "Be brief.",
        "qa": {"enabled": True},
        "pipeline": {
            "mode": "half_cascade",
            "realtime": {
                "provider_id": "google-realtime",
                "credential_id": credential_id,
                "fields": {"voice": "Puck"},
            },
            "tts": {"provider_id": "livekit-inference-tts"},
        },
    }
    session_id, _ = await _session_for(admin_client, config)

    resolved = ResolvedAgentConfig.model_validate(
        (await service_client.get(f"/internal/v1/sessions/{session_id}/resolved")).json()
    )

    qa_llm = resolved.resolved["qa_llm"]
    assert qa_llm.provider_id != "google-realtime"
    assert qa_llm.provider_id == "livekit-inference-llm"


async def test_qa_disabled_produces_no_qa_llm_slot(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient
) -> None:
    """R-V2-6 named test: `qa.enabled=false` produces no slot."""
    session_id, _ = await _session_for(admin_client)  # default config: qa.enabled is False

    resolved = ResolvedAgentConfig.model_validate(
        (await service_client.get(f"/internal/v1/sessions/{session_id}/resolved")).json()
    )

    assert "qa_llm" not in resolved.resolved
