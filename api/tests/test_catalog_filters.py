"""`GET /v1/providers/{id}/catalog`: filters, public lists, search and paging (V4-07, D-V4-25, R-V4-28).

Offline (`respx`). No real key is used; every "key" below is a placeholder.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx
from lkap_contracts.providers import CatalogSpec, get

import lkap_api.credential_tests as credential_tests
from lkap_api.catalogs import cache
from lkap_api.catalogs.vendors import DEEPGRAM_MODELS_URL, GEMINI_MODELS_URL, RIME_VOICES_URL
from lkap_api.db.session import Database

OPENAI_MODELS_URL = "https://api.openai.com/v1/models"
OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
FIXTURES = Path(__file__).parent / "fixtures" / "catalogs"

_OPENAI_BODY = {
    "data": [
        {"id": "gpt-4.1", "owned_by": "openai"},
        {"id": "tts-1", "owned_by": "openai"},
        {"id": "whisper-1", "owned_by": "openai"},
        {"id": "text-embedding-3-small", "owned_by": "openai"},
        {"id": "gpt-realtime", "owned_by": "openai"},
    ]
}

_GEMINI_BODY = {
    "models": [
        {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-3.5-flash", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-3.8-live", "supportedGenerationMethods": ["bidiGenerateContent"]},
        {"name": "models/gemini-2.5-flash-preview-tts", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-3.1-flash-image", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
    ]
}


async def _default_key(
    admin_client: httpx.AsyncClient, provider_id: str, api_key: str = "placeholder-key"
) -> str:
    created = await admin_client.post(
        "/v1/credentials",
        json={"provider_id": provider_id, "label": provider_id, "secrets": {"api_key": api_key}},
    )
    assert created.status_code == 201, created.text
    credential_id: str = created.json()["id"]
    home = created.json()["provider_id"]
    settings = await admin_client.put(
        f"/v1/providers/{home}/settings", json={"default_credential_id": credential_id}
    )
    assert settings.status_code == 200, settings.text
    return credential_id


# ------------------------------------------------------------------------------ filters
@pytest.mark.parametrize(
    ("provider_id", "expected"),
    [
        ("openai-llm", ["gpt-4.1"]),
        ("openai-tts", ["tts-1"]),
        ("openai-stt", ["whisper-1"]),
        ("openai-embedding", ["text-embedding-3-small"]),
        ("openai-realtime", ["gpt-realtime"]),
    ],
)
async def test_one_openai_list_is_sliced_per_entry(
    admin_client: httpx.AsyncClient, provider_id: str, expected: list[str]
) -> None:
    await _default_key(admin_client, provider_id)
    with respx.mock:
        respx.get(OPENAI_MODELS_URL).mock(return_value=httpx.Response(200, json=_OPENAI_BODY))
        response = await admin_client.get(f"/v1/providers/{provider_id}/catalog")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "vendor"
    assert [i["id"] for i in body["items"]] == expected
    assert body["total"] == len(expected)


async def test_google_realtime_lists_only_bidi_generate_content_models(
    admin_client: httpx.AsyncClient,
) -> None:
    await _default_key(admin_client, "google-realtime")
    with respx.mock:
        respx.get(GEMINI_MODELS_URL).mock(return_value=httpx.Response(200, json=_GEMINI_BODY))
        response = await admin_client.get("/v1/providers/google-realtime/catalog")

    assert [i["id"] for i in response.json()["items"]] == ["gemini-3.8-live"]


async def test_google_llm_keeps_generate_content_chat_models_only(admin_client: httpx.AsyncClient) -> None:
    await _default_key(admin_client, "google-llm")
    with respx.mock:
        respx.get(GEMINI_MODELS_URL).mock(return_value=httpx.Response(200, json=_GEMINI_BODY))
        response = await admin_client.get("/v1/providers/google-llm/catalog")

    assert [i["id"] for i in response.json()["items"]] == ["gemini-2.5-flash", "gemini-3.5-flash"]


async def test_a_row_cached_before_the_filter_existed_is_filtered_on_read(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    credential_id = await _default_key(admin_client, "openai-tts")
    from lkap_contracts.api_models import CatalogItem

    async with database.session() as session:
        await cache.set_(
            session,
            provider_id="openai-tts",
            credential_id=credential_id,
            kind="models",
            items=[CatalogItem(id=i["id"], label=i["id"]) for i in _OPENAI_BODY["data"]],
            ttl_s=3600,
        )
        await session.commit()

    with respx.mock:
        route = respx.get(OPENAI_MODELS_URL)
        response = await admin_client.get("/v1/providers/openai-tts/catalog")

    assert route.call_count == 0, "served from the fresh cache row"
    assert [i["id"] for i in response.json()["items"]] == ["tts-1"]


# ------------------------------------------------------------------ public vs keyed lists
async def test_a_public_list_with_no_credential_answers_from_the_vendor(
    admin_client: httpx.AsyncClient,
) -> None:
    body = json.loads((FIXTURES / "deepgram_models.json").read_text(encoding="utf-8"))
    with respx.mock:
        route = respx.get(DEEPGRAM_MODELS_URL).mock(return_value=httpx.Response(200, json=body))
        response = await admin_client.get("/v1/providers/deepgram-tts/catalog", params={"limit": 1000})

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["source"] == "vendor"
    assert result["error"] is None
    assert result["total"] == 102
    assert "aura-2-andromeda-en" in {i["id"] for i in result["items"]}
    assert "Authorization" not in route.calls.last.request.headers


async def test_a_public_list_ignores_a_stored_key_and_shares_one_cache_row(
    admin_client: httpx.AsyncClient,
) -> None:
    await _default_key(admin_client, "deepgram-stt", api_key="dg-placeholder")
    body = json.loads((FIXTURES / "deepgram_models.json").read_text(encoding="utf-8"))
    with respx.mock:
        route = respx.get(DEEPGRAM_MODELS_URL).mock(return_value=httpx.Response(200, json=body))
        first = await admin_client.get("/v1/providers/deepgram-stt/catalog")
        second = await admin_client.get("/v1/providers/deepgram-stt/catalog")

    assert first.json()["source"] == second.json()["source"] == "vendor"
    assert route.call_count == 1, "cached under credential_id=None, whichever key the caller holds"
    assert "Authorization" not in route.calls.last.request.headers
    assert "dg-placeholder" not in route.calls.last.request.url.query.decode()


async def test_a_keyed_list_with_no_credential_still_answers_static(admin_client: httpx.AsyncClient) -> None:
    with respx.mock:
        route = respx.get(GEMINI_MODELS_URL)
        response = await admin_client.get("/v1/providers/google-llm/catalog")

    assert route.call_count == 0
    body = response.json()
    assert body["source"] == "static"
    assert [i["id"] for i in body["items"]] == ["gemini-2.5-flash", "gemini-3.5-flash"]
    assert body["total"] == 2


async def test_credential_tests_still_resolve_the_adapter_by_spec_test() -> None:
    # A spec whose catalog names a public list but whose `test` names a keyed one:
    # the credential test must call the keyed endpoint, never the public list.
    spec = get("openai-llm").model_copy(
        update={
            "catalog": CatalogSpec(adapter="deepgram_stt_models", kinds=["models"]),
            "test": "openai_models",
        }
    )
    with respx.mock:
        openai = respx.get(OPENAI_MODELS_URL).mock(return_value=httpx.Response(200, json={"data": []}))
        public = respx.get(DEEPGRAM_MODELS_URL)
        async with httpx.AsyncClient() as client:
            result = await credential_tests.run(spec, {"api_key": "placeholder"}, client)

    assert result is not None and result.ok is True
    assert openai.call_count == 1
    assert public.call_count == 0


def test_public_list_entries_have_no_credential_test() -> None:
    for provider_id in ("deepgram-stt", "deepgram-tts", "rime-tts"):
        assert credential_tests.has_adapter(get(provider_id)) is False


# --------------------------------------------------------------------- search and paging
async def test_q_limit_and_offset_page_the_cached_list_and_set_total(admin_client: httpx.AsyncClient) -> None:
    await _default_key(admin_client, "google-llm")
    with respx.mock:
        route = respx.get(GEMINI_MODELS_URL).mock(return_value=httpx.Response(200, json=_GEMINI_BODY))
        response = await admin_client.get(
            "/v1/providers/google-llm/catalog", params={"q": "GEM", "limit": 1, "offset": 1}
        )
        again = await admin_client.get("/v1/providers/google-llm/catalog", params={"q": "3.5"})

    assert route.call_count == 1, "the second search reads the cache"
    body = response.json()
    assert body["total"] == 2
    assert [i["id"] for i in body["items"]] == ["gemini-3.5-flash"]
    assert [i["id"] for i in again.json()["items"]] == ["gemini-3.5-flash"]
    assert "q" not in route.calls.last.request.url.params, "a cached search never reaches the vendor"


async def test_the_default_page_is_200_items(admin_client: httpx.AsyncClient) -> None:
    await _default_key(admin_client, "openai-llm")
    body = {"data": [{"id": f"gpt-{n:03d}"} for n in range(250)]}
    with respx.mock:
        respx.get(OPENAI_MODELS_URL).mock(return_value=httpx.Response(200, json=body))
        response = await admin_client.get("/v1/providers/openai-llm/catalog")

    result = response.json()
    assert result["total"] == 250
    assert len(result["items"]) == 200


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 1001}, {"offset": -1}])
async def test_out_of_range_paging_is_a_422(admin_client: httpx.AsyncClient, params: dict[str, int]) -> None:
    response = await admin_client.get("/v1/providers/google-llm/catalog", params=params)
    assert response.status_code == 422


async def test_voices_filter_by_meta_model(admin_client: httpx.AsyncClient) -> None:
    body = json.loads((FIXTURES / "rime_voices_all_v2.json").read_text(encoding="utf-8"))
    with respx.mock:
        respx.get(RIME_VOICES_URL).mock(return_value=httpx.Response(200, json=body))
        response = await admin_client.get(
            "/v1/providers/rime-tts/catalog", params={"kind": "voices", "model": "mistv3", "limit": 1000}
        )

    result = response.json()
    expected = {name for names in body["mistv3"].values() for name in names}
    assert result["total"] == len(expected)
    assert {i["id"] for i in result["items"]} == expected
    assert {i["meta"]["model"] for i in result["items"]} == {"mistv3"}


async def test_search_vendor_reaches_openrouter_with_q(admin_client: httpx.AsyncClient) -> None:
    await _default_key(admin_client, "openrouter-llm", api_key="sk-or-v1-placeholder")
    with respx.mock:
        respx.get(OPENROUTER_KEY_URL).mock(return_value=httpx.Response(200, json={"data": {}}))
        models = respx.get(url__startswith=OPENROUTER_MODELS_URL).mock(
            return_value=httpx.Response(
                200, json={"data": [{"id": "anthropic/claude-x", "name": "Claude X"}]}
            )
        )
        response = await admin_client.get(
            "/v1/providers/openrouter-llm/catalog",
            params={"q": "claude", "search_vendor": "true", "limit": 5},
        )
        cached = await admin_client.get("/v1/providers/openrouter-llm/catalog")

    assert response.status_code == 200, response.text
    assert [i["id"] for i in response.json()["items"]] == ["anthropic/claude-x"]
    sent = models.calls[0].request.url.params
    assert sent["q"] == "claude"
    assert sent["supported_parameters"] == "tools", "the registration's filter rides along"
    # The vendor search was not cached: the plain request fetched the whole list.
    assert models.call_count == 2
    assert "q" not in models.calls[1].request.url.params
    assert cached.json()["total"] == 1


async def test_search_vendor_is_ignored_for_other_vendors(admin_client: httpx.AsyncClient) -> None:
    await _default_key(admin_client, "openai-llm")
    with respx.mock:
        route = respx.get(OPENAI_MODELS_URL).mock(return_value=httpx.Response(200, json=_OPENAI_BODY))
        response = await admin_client.get(
            "/v1/providers/openai-llm/catalog", params={"q": "gpt", "search_vendor": "true"}
        )

    assert "q" not in route.calls.last.request.url.params
    assert [i["id"] for i in response.json()["items"]] == ["gpt-4.1"]
