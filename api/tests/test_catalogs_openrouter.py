"""`lkap_api.catalogs.openrouter`: probe `/key`, then list filtered `/models` (V4-03, R-V4-9).

Offline: every vendor call is mocked with `respx`. No real key is used.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from lkap_contracts.providers import get

import lkap_api.credential_tests as credential_tests
from lkap_api.catalogs import UnsupportedCatalogKind, get_adapter
from lkap_api.catalogs.base import CatalogAdapterError
from lkap_api.catalogs.openrouter import OPENROUTER_ADAPTERS, OpenRouterCatalogAdapter

KEY_URL = "https://openrouter.ai/api/v1/key"
MODELS_URL = "https://openrouter.ai/api/v1/models"

_KEY_BODY = {"data": {"label": "test key", "limit": None, "usage": 0, "is_free_tier": True}}

_SPEECH_MODELS = {
    "data": [
        {
            "id": "google/gemini-3.8-flash-tts",
            "name": "Gemini 3.8 Flash TTS",
            "description": "long prose the catalog does not need",
            "pricing": {"prompt": "0.0000005", "completion": "0"},
            "context_length": 8192,
            "architecture": {"input_modalities": ["text"], "output_modalities": ["speech"]},
            "supported_voices": ["Kore", "Puck"],
        },
        {
            "id": "x-ai/grok-voice-tts-1.0",
            "name": "Grok Voice TTS",
            "pricing": {"prompt": "0.000001"},
            "supported_voices": ["eve"],
        },
    ]
}

_OPENROUTER_IDS = [
    "openrouter-llm",
    "openrouter-stt",
    "openrouter-tts",
    "openrouter-embedding",
    "openrouter-image-gen",
]

_EXPECTED_FILTERS = {
    "openrouter-llm": "supported_parameters=tools",
    "openrouter-stt": "output_modalities=transcription",
    "openrouter-tts": "output_modalities=speech",
    "openrouter-embedding": "output_modalities=embeddings",
    "openrouter-image-gen": "output_modalities=image",
}


@pytest.mark.parametrize("provider_id", _OPENROUTER_IDS)
def test_every_openrouter_entry_is_wired_to_an_openrouter_adapter(provider_id: str) -> None:
    spec = get(provider_id)
    assert spec.test is not None
    assert isinstance(get_adapter(spec.test), OpenRouterCatalogAdapter)
    assert credential_tests.has_adapter(spec) is True


async def test_a_rejected_key_fails_the_credential_test_without_listing_models() -> None:
    with respx.mock:
        key_route = respx.get(KEY_URL).mock(
            return_value=httpx.Response(401, json={"error": {"message": "User not found.", "code": 401}})
        )
        models_route = respx.get(url__startswith=MODELS_URL).mock(
            return_value=httpx.Response(200, json={"data": [{"id": "openai/gpt-4.1-mini"}]})
        )
        async with httpx.AsyncClient() as client:
            result = await credential_tests.run(get("openrouter-llm"), {"api_key": "sk-or-v1-bogus"}, client)

    assert result is not None
    assert result.ok is False
    assert key_route.call_count == 1
    assert key_route.calls.last.request.headers["Authorization"] == "Bearer sk-or-v1-bogus"
    assert models_route.call_count == 0, "a rejected key must never reach the public /models list"
    assert "sk-or-v1-bogus" not in result.message


async def test_a_network_error_on_the_key_probe_is_a_catalog_error() -> None:
    adapter = OPENROUTER_ADAPTERS["openrouter_llm_models"]
    with respx.mock:
        respx.get(KEY_URL).mock(side_effect=httpx.ConnectError("boom"))
        async with httpx.AsyncClient() as client:
            with pytest.raises(CatalogAdapterError):
                await adapter.fetch(client=client, secrets={"api_key": "k"}, kind="models")


async def test_a_good_key_lists_models_with_pricing_and_voices_in_meta() -> None:
    adapter = OPENROUTER_ADAPTERS["openrouter_tts_models"]
    with respx.mock:
        respx.get(KEY_URL).mock(return_value=httpx.Response(200, json=_KEY_BODY))
        respx.get(url__startswith=MODELS_URL).mock(return_value=httpx.Response(200, json=_SPEECH_MODELS))
        async with httpx.AsyncClient() as client:
            items = await adapter.fetch(client=client, secrets={"api_key": "sk-or-v1-good"}, kind="models")

    assert [(i.id, i.label) for i in items] == [
        ("google/gemini-3.8-flash-tts", "Gemini 3.8 Flash TTS"),
        ("x-ai/grok-voice-tts-1.0", "Grok Voice TTS"),
    ]
    first = items[0].meta
    assert first["pricing"] == {"prompt": "0.0000005", "completion": "0"}
    assert first["supported_voices"] == ["Kore", "Puck"]
    assert first["context_length"] == 8192
    assert "description" not in first, "meta keeps only the catalog fields, not vendor prose"


async def test_the_voices_kind_flattens_supported_voices_with_model_qualified_labels() -> None:
    adapter = OPENROUTER_ADAPTERS["openrouter_tts_models"]
    with respx.mock:
        respx.get(KEY_URL).mock(return_value=httpx.Response(200, json=_KEY_BODY))
        respx.get(url__startswith=MODELS_URL).mock(return_value=httpx.Response(200, json=_SPEECH_MODELS))
        async with httpx.AsyncClient() as client:
            items = await adapter.fetch(client=client, secrets={"api_key": "k"}, kind="voices")

    assert [(i.id, i.label, i.meta) for i in items] == [
        ("Kore", "Kore · Gemini 3.8 Flash TTS", {"model": "google/gemini-3.8-flash-tts"}),
        ("Puck", "Puck · Gemini 3.8 Flash TTS", {"model": "google/gemini-3.8-flash-tts"}),
        ("eve", "eve · Grok Voice TTS", {"model": "x-ai/grok-voice-tts-1.0"}),
    ]


@pytest.mark.parametrize("provider_id", _OPENROUTER_IDS)
async def test_each_adapter_requests_its_own_filter(provider_id: str) -> None:
    spec = get(provider_id)
    assert spec.test is not None
    adapter = get_adapter(spec.test)
    assert adapter is not None
    with respx.mock:
        respx.get(KEY_URL).mock(return_value=httpx.Response(200, json=_KEY_BODY))
        models_route = respx.get(url__startswith=MODELS_URL).mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        async with httpx.AsyncClient() as client:
            await adapter.fetch(client=client, secrets={"api_key": "k"}, kind="models")

    assert models_route.call_count == 1
    assert models_route.calls.last.request.url.query.decode() == _EXPECTED_FILTERS[provider_id]


async def test_voices_are_only_served_by_the_tts_registration() -> None:
    async with httpx.AsyncClient() as client:
        with pytest.raises(UnsupportedCatalogKind):
            await OPENROUTER_ADAPTERS["openrouter_llm_models"].fetch(
                client=client, secrets={"api_key": "k"}, kind="voices"
            )


async def test_the_credential_test_route_reports_a_wrong_key(admin_client: httpx.AsyncClient) -> None:
    created = await admin_client.post(
        "/v1/credentials",
        json={"provider_id": "openrouter-llm", "label": "OpenRouter", "secrets": {"api_key": "sk-or-v1-x"}},
    )
    assert created.status_code == 201, created.text

    with respx.mock:
        respx.get(KEY_URL).mock(return_value=httpx.Response(401, json={"error": {"code": 401}}))
        models_route = respx.get(url__startswith=MODELS_URL).mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        response = await admin_client.post(f"/v1/credentials/{created.json()['id']}/test")

    assert response.status_code == 200, response.text
    assert response.json()["ok"] is False
    assert models_route.call_count == 0


async def test_the_credential_test_route_previews_tool_capable_models(
    admin_client: httpx.AsyncClient,
) -> None:
    created = await admin_client.post(
        "/v1/credentials",
        json={"provider_id": "openrouter-llm", "label": "OpenRouter", "secrets": {"api_key": "sk-or-v1-y"}},
    )
    body = {"data": [{"id": f"openai/model-{n}", "name": f"Model {n}"} for n in range(7)]}

    with respx.mock:
        respx.get(KEY_URL).mock(return_value=httpx.Response(200, json=_KEY_BODY))
        respx.get(url__startswith=MODELS_URL).mock(return_value=httpx.Response(200, json=body))
        response = await admin_client.post(f"/v1/credentials/{created.json()['id']}/test")

    result = response.json()
    assert result["ok"] is True
    assert result["message"] == "OpenRouter responded with 7 item(s)"
    assert [i["id"] for i in result["catalog_preview"]] == [f"openai/model-{n}" for n in range(5)]
