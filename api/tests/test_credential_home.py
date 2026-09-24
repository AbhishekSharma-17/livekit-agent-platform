"""R-V4-7: one OpenRouter key, stored under its credential home, serves all five entries (V4-03).

Offline: vendor calls are mocked with `respx`; no real key is used.
"""

from __future__ import annotations

import json

import httpx
import respx
from conftest import inference_config
from lkap_contracts.agent_config import ProviderRef

from lkap_api.config_service import validate_agent_config

KEY_URL = "https://openrouter.ai/api/v1/key"
MODELS_URL = "https://openrouter.ai/api/v1/models"


async def _create(admin_client: httpx.AsyncClient, provider_id: str, api_key: str = "sk-or-v1-test") -> dict:
    response = await admin_client.post(
        "/v1/credentials",
        json={"provider_id": provider_id, "label": provider_id, "secrets": {"api_key": api_key}},
    )
    assert response.status_code == 201, response.text
    body: dict = response.json()
    return body


def _openrouter_config(credential_id: str) -> dict:
    config = inference_config()
    config.pipeline.stt = ProviderRef(provider_id="openrouter-stt", credential_id=credential_id)
    config.pipeline.llm = ProviderRef(provider_id="openrouter-llm", credential_id=credential_id)
    config.pipeline.tts = ProviderRef(provider_id="openrouter-tts", credential_id=credential_id)
    body: dict = json.loads(config.model_dump_json())
    return body


# ------------------------------------------------------------------------------ provider keys
async def test_a_key_added_from_an_alias_is_stored_under_the_home(admin_client: httpx.AsyncClient) -> None:
    created = await _create(admin_client, "openrouter-tts")

    assert created["provider_id"] == "openrouter-llm"
    fetched = await admin_client.get(f"/v1/credentials/{created['id']}")
    assert fetched.json()["provider_id"] == "openrouter-llm"


async def test_the_shared_key_is_listed_under_every_openrouter_id(admin_client: httpx.AsyncClient) -> None:
    created = await _create(admin_client, "openrouter-tts")
    await _create(admin_client, "openai-llm", api_key="sk-openai-test")

    for provider_id in ("openrouter-tts", "openrouter-stt", "openrouter-llm", "openrouter-image-gen"):
        page = (await admin_client.get("/v1/credentials", params={"provider_id": provider_id})).json()
        assert [item["id"] for item in page["items"]] == [created["id"]], provider_id
        assert page["total"] == 1

    openai_page = (await admin_client.get("/v1/credentials", params={"provider_id": "openai-llm"})).json()
    assert created["id"] not in {item["id"] for item in openai_page["items"]}
    unknown = (await admin_client.get("/v1/credentials", params={"provider_id": "not-a-provider"})).json()
    assert unknown == {"items": [], "total": 0}


async def test_an_alias_secret_bag_is_still_checked_against_its_own_fields(
    admin_client: httpx.AsyncClient,
) -> None:
    response = await admin_client.post(
        "/v1/credentials",
        json={"provider_id": "openrouter-stt", "label": "bad", "secrets": {"token": "x"}},
    )

    assert response.status_code == 422


async def test_updating_the_shared_key_with_an_alias_provider_id_is_allowed(
    admin_client: httpx.AsyncClient,
) -> None:
    created = await _create(admin_client, "openrouter-llm")

    response = await admin_client.put(
        f"/v1/credentials/{created['id']}", json={"provider_id": "openrouter-stt", "label": "renamed"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["label"] == "renamed"

    other = await admin_client.put(f"/v1/credentials/{created['id']}", json={"provider_id": "openai-llm"})
    assert other.status_code == 422


# ------------------------------------------------------------------------------ validation
def test_three_openrouter_slots_on_one_credential_have_no_credential_issues() -> None:
    config = inference_config()
    config.pipeline.stt = ProviderRef(provider_id="openrouter-stt", credential_id="cred-or")
    config.pipeline.llm = ProviderRef(provider_id="openrouter-llm", credential_id="cred-or")
    config.pipeline.tts = ProviderRef(provider_id="openrouter-tts", credential_id="cred-or")

    result = validate_agent_config(config, credential_providers={"cred-or": "openrouter-llm"})

    assert result.ok is True, result.errors
    assert not [issue for issue in result.issues if "credential" in issue.message]


def test_an_openai_key_on_an_openrouter_slot_still_fails() -> None:
    config = inference_config()
    config.pipeline.llm = ProviderRef(provider_id="openrouter-llm", credential_id="cred-openai")

    result = validate_agent_config(config, credential_providers={"cred-openai": "openai-llm"})

    assert result.ok is False
    assert any("belongs to provider 'openai-llm'" in error for error in result.errors)


async def test_an_agent_on_three_openrouter_slots_and_one_key_is_created_and_validates(
    admin_client: httpx.AsyncClient,
) -> None:
    credential = await _create(admin_client, "openrouter-llm")

    response = await admin_client.post(
        "/v1/agents", json={"name": "OpenRouter", "config": _openrouter_config(credential["id"])}
    )
    assert response.status_code == 201, response.text

    validation = (await admin_client.post(f"/v1/agents/{response.json()['id']}/validate")).json()
    assert validation["ok"] is True, validation
    assert not [error for error in validation["errors"] if "credential" in error]


# ------------------------------------------------------------------------------ providers router
async def test_the_stt_catalog_authenticates_with_the_home_key(admin_client: httpx.AsyncClient) -> None:
    await _create(admin_client, "openrouter-llm", api_key="sk-or-v1-home")

    with respx.mock:
        key_route = respx.get(KEY_URL).mock(return_value=httpx.Response(200, json={"data": {}}))
        respx.get(url__startswith=MODELS_URL).mock(
            return_value=httpx.Response(
                200, json={"data": [{"id": "openai/gpt-4o-mini-transcribe", "name": "Mini Transcribe"}]}
            )
        )
        response = await admin_client.get("/v1/providers/openrouter-stt/catalog")

    body = response.json()
    assert response.status_code == 200, response.text
    assert body["source"] == "vendor"
    assert [item["id"] for item in body["items"]] == ["openai/gpt-4o-mini-transcribe"]
    assert key_route.calls.last.request.headers["Authorization"] == "Bearer sk-or-v1-home"


async def test_an_alias_can_take_the_home_row_as_its_default_credential(
    admin_client: httpx.AsyncClient,
) -> None:
    credential = await _create(admin_client, "openrouter-llm")

    response = await admin_client.put(
        "/v1/providers/openrouter-tts/settings", json={"default_credential_id": credential["id"]}
    )

    assert response.status_code == 200, response.text
    assert response.json()["default_credential_id"] == credential["id"]


async def test_an_alias_rejects_another_vendors_row_as_its_default(admin_client: httpx.AsyncClient) -> None:
    credential = await _create(admin_client, "openai-llm", api_key="sk-openai-test")

    response = await admin_client.put(
        "/v1/providers/openrouter-tts/settings", json={"default_credential_id": credential["id"]}
    )

    assert response.status_code == 422
