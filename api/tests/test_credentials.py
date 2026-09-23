"""Credential CRUD: encryption at rest, fingerprints and secret hygiene."""

from __future__ import annotations

import httpx
import pytest
from conftest import captured_text, create_agent
from sqlalchemy import select

from lkap_api.db.models import Credential
from lkap_api.db.session import Database
from lkap_api.routers.credentials import seed_bootstrap_credentials
from lkap_api.settings import Settings
from lkap_api.vault import Vault

SECRET = "sk-super-secret-value-9f3a"


async def _create(admin_client: httpx.AsyncClient, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "provider_id": "openai-llm",
        "label": "OpenAI (prod)",
        "secrets": {"api_key": SECRET},
    }
    payload.update(overrides)
    response = await admin_client.post("/v1/credentials", json=payload)
    assert response.status_code == 201, response.text
    body: dict[str, object] = response.json()
    return body


async def test_create_returns_a_fingerprint_and_never_the_secret(
    admin_client: httpx.AsyncClient,
) -> None:
    created = await _create(admin_client)

    assert created["fingerprint"] == "…9f3a"
    assert str(created["created_at"]).endswith("Z"), "CONTRACTS §7: timestamps are ISO-8601 UTC"
    assert SECRET not in str(created)
    assert "secrets" not in created
    assert "ciphertext" not in created


async def test_secret_is_encrypted_at_rest(
    admin_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    created = await _create(admin_client)

    async with database.session() as session:
        row = (await session.execute(select(Credential))).scalar_one()
    assert SECRET.encode() not in row.ciphertext
    assert Vault(settings.master_key).decrypt(row.ciphertext) == {"api_key": SECRET}
    assert str(created["id"]) == row.id


async def test_no_admin_route_ever_returns_the_secret(admin_client: httpx.AsyncClient) -> None:
    created = await _create(admin_client)
    credential_id = created["id"]

    responses = [
        await admin_client.get("/v1/credentials"),
        await admin_client.get(f"/v1/credentials/{credential_id}"),
        await admin_client.put(f"/v1/credentials/{credential_id}", json={"label": "Renamed"}),
    ]

    for response in responses:
        assert response.status_code == 200, response.text
        assert SECRET not in response.text


async def test_create_requires_an_admin_token(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/v1/credentials",
        json={"provider_id": "openai-llm", "label": "x", "secrets": {"api_key": SECRET}},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_a_service_token_is_not_an_admin_token(client: httpx.AsyncClient, settings: Settings) -> None:
    response = await client.get("/v1/credentials", headers={"X-Service-Token": settings.service_token})

    assert response.status_code == 401


async def test_validation_errors_never_echo_the_submitted_secret(
    admin_client: httpx.AsyncClient, log_capture: pytest.LogCaptureFixture
) -> None:
    response = await admin_client.post(
        "/v1/credentials", json={"provider_id": "openai-llm", "secrets": {"api_key": SECRET}}
    )

    assert response.status_code == 422
    assert SECRET not in response.text
    assert SECRET not in captured_text(log_capture)


async def test_unknown_provider_is_rejected(admin_client: httpx.AsyncClient) -> None:
    response = await admin_client.post(
        "/v1/credentials",
        json={"provider_id": "not-a-provider", "label": "x", "secrets": {"api_key": "y"}},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unprocessable_entity"


async def test_required_secret_fields_are_enforced(admin_client: httpx.AsyncClient) -> None:
    response = await admin_client.post(
        "/v1/credentials", json={"provider_id": "openai-llm", "label": "x", "secrets": {}}
    )

    assert response.status_code == 422


async def test_free_form_secret_bags_are_accepted(admin_client: httpx.AsyncClient) -> None:
    """`http-tool-secret` requires a credential but declares no secret_fields."""
    created = await _create(
        admin_client,
        provider_id="http-tool-secret",
        label="Weather API",
        secrets={"WEATHER_KEY": "abcd-1234"},
    )

    assert created["fingerprint"] == "…1234"


async def test_update_without_secrets_keeps_the_stored_values(
    admin_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    created = await _create(admin_client)

    response = await admin_client.put(f"/v1/credentials/{created['id']}", json={"label": "Renamed"})

    assert response.status_code == 200
    assert response.json()["label"] == "Renamed"
    async with database.session() as session:
        row = (await session.execute(select(Credential))).scalar_one()
    assert Vault(settings.master_key).decrypt(row.ciphertext) == {"api_key": SECRET}


async def test_update_rotates_secrets_and_the_fingerprint(admin_client: httpx.AsyncClient) -> None:
    created = await _create(admin_client)

    response = await admin_client.put(
        f"/v1/credentials/{created['id']}", json={"secrets": {"api_key": "sk-rotated-0000"}}
    )

    assert response.json()["fingerprint"] == "…0000"


async def test_provider_cannot_be_changed(admin_client: httpx.AsyncClient) -> None:
    created = await _create(admin_client)

    response = await admin_client.put(f"/v1/credentials/{created['id']}", json={"provider_id": "google-llm"})

    assert response.status_code == 422


async def test_list_filters_by_provider(admin_client: httpx.AsyncClient) -> None:
    await _create(admin_client)
    await _create(admin_client, provider_id="google-llm", label="Google", secrets={"api_key": "g-1"})

    response = await admin_client.get("/v1/credentials", params={"provider_id": "google-llm"})

    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["provider_id"] == "google-llm"


async def test_delete_removes_an_unreferenced_credential(admin_client: httpx.AsyncClient) -> None:
    created = await _create(admin_client)

    assert (await admin_client.delete(f"/v1/credentials/{created['id']}")).status_code == 204
    assert (await admin_client.get(f"/v1/credentials/{created['id']}")).status_code == 404


async def test_delete_is_refused_while_an_agent_references_it(
    admin_client: httpx.AsyncClient,
) -> None:
    created = await _create(admin_client)
    config = {
        "v": 1,
        "instructions": "hi",
        "pipeline": {
            "mode": "cascaded",
            "stt": {"provider_id": "livekit-inference-stt"},
            "llm": {"provider_id": "openai-llm", "credential_id": created["id"]},
            "tts": {"provider_id": "livekit-inference-tts"},
        },
    }
    await create_agent(admin_client, name="Uses OpenAI", config=config)

    response = await admin_client.delete(f"/v1/credentials/{created['id']}")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


async def test_credential_test_calls_the_vendor_once(
    admin_client: httpx.AsyncClient, mock_http: list[httpx.Request]
) -> None:
    created = await _create(admin_client)

    response = await admin_client.post(f"/v1/credentials/{created['id']}/test")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert len(mock_http) == 1
    assert mock_http[0].headers["Authorization"] == f"Bearer {SECRET}"


async def test_credential_test_reports_not_implemented_providers(
    admin_client: httpx.AsyncClient, mock_http: list[httpx.Request]
) -> None:
    created = await _create(
        admin_client, provider_id="bey-avatar", label="Beyond", secrets={"api_key": "bey-1"}
    )

    response = await admin_client.post(f"/v1/credentials/{created['id']}/test")

    body = response.json()
    # Subset comparison: `CredentialTestResult` keeps gaining optional fields
    # (v2 added `checked_at`/`catalog_preview`), and this test is about the message.
    assert body["ok"] is True
    assert body["message"] == "no automated test implemented for 'bey-avatar'"
    assert mock_http == []


async def test_bootstrap_seeding_is_idempotent(
    database: Database, settings: Settings, log_capture: pytest.LogCaptureFixture
) -> None:
    vault = Vault(settings.master_key)
    raw = '{"google-llm": {"api_key": "boot-strap-key-1234"}, "nope-llm": {"api_key": "x"}}'

    async with database.session() as session:
        first = await seed_bootstrap_credentials(session, vault, raw)
    async with database.session() as session:
        second = await seed_bootstrap_credentials(session, vault, raw)
        rows = (await session.execute(select(Credential))).scalars().all()

    assert (first, second) == (1, 0)
    assert [r.provider_id for r in rows] == ["google-llm"]
    assert rows[0].fingerprint == "…1234"
    assert "boot-strap-key-1234" not in captured_text(log_capture)


async def test_bootstrap_seeding_is_a_no_op_without_config(database: Database, settings: Settings) -> None:
    async with database.session() as session:
        assert await seed_bootstrap_credentials(session, Vault(settings.master_key), None) == 0
