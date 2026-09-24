"""V4-07 vendor catalog adapters and paging (docs/v4/CUSTOM-MODELS.md D-V4-25, R-V4-28).

Offline: `respx` mocks every vendor call. The Deepgram and Rime bodies are the
real public lists (probed keylessly on 2026-09-25), trimmed to the fields the
adapters read, under ``tests/fixtures/catalogs/``. The keyed vendors' bodies
follow their documented shapes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from lkap_contracts.providers import PUBLIC_CATALOG_ADAPTERS, REGISTRY, PageSpec, get

import lkap_api.credential_tests as credential_tests
from lkap_api.catalogs.adapters import ADAPTERS, get_adapter
from lkap_api.catalogs.base import CatalogAdapterError, UnsupportedCatalogKind, fetch_pages
from lkap_api.catalogs.vendors import (
    DEEPGRAM_MODELS_URL,
    GEMINI_MODELS_URL,
    INWORLD_VOICES_URL,
    RIME_VOICES_URL,
    XAI_MODELS_URL,
)

FIXTURES = Path(__file__).parent / "fixtures" / "catalogs"


def _fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# ------------------------------------------------------------------------ registrations
def test_the_public_adapters_are_exactly_the_contracts_public_list() -> None:
    public = {name for name, adapter in ADAPTERS.items() if adapter.public}
    assert public == PUBLIC_CATALOG_ADAPTERS


def test_every_catalog_adapter_the_registry_names_is_registered() -> None:
    names = {spec.catalog.adapter for spec in REGISTRY if spec.catalog is not None}
    assert names <= set(ADAPTERS)


def test_no_azure_voices_adapter_the_region_is_a_slot_field() -> None:
    assert "azure_voices" not in ADAPTERS


# ------------------------------------------------------------------------------- Gemini
_GEMINI_BODY = {
    "models": [
        {
            "name": "models/gemini-2.5-flash",
            "displayName": "Gemini 2.5 Flash",
            "inputTokenLimit": 1048576,
            "supportedGenerationMethods": ["generateContent", "countTokens"],
        },
        {
            "name": "models/gemini-3.8-live",
            "displayName": "Gemini 3.8 Live",
            "supportedGenerationMethods": ["bidiGenerateContent"],
        },
        {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
    ]
}


async def test_gemini_models_strips_the_models_prefix_and_sends_the_goog_header() -> None:
    adapter = get_adapter("gemini_models")
    assert adapter is not None and adapter.public is False
    with respx.mock:
        route = respx.get(GEMINI_MODELS_URL).mock(return_value=httpx.Response(200, json=_GEMINI_BODY))
        async with httpx.AsyncClient() as client:
            items = await adapter.fetch(client=client, secrets={"api_key": "g-key"}, kind="models")

    assert [(i.id, i.label) for i in items] == [
        ("gemini-2.5-flash", "Gemini 2.5 Flash"),
        ("gemini-3.8-live", "Gemini 3.8 Live"),
        ("text-embedding-004", "text-embedding-004"),
    ]
    assert items[0].meta["supportedGenerationMethods"] == ["generateContent", "countTokens"]
    assert route.calls.last.request.headers["x-goog-api-key"] == "g-key"
    assert "key" not in route.calls.last.request.url.params, "the key rides in a header, never the url"


async def test_gemini_follows_next_page_token_with_the_registry_page_size() -> None:
    spec = get("google-llm")
    adapter = get_adapter("gemini_models")
    assert adapter is not None and spec.catalog is not None and spec.catalog.page is not None
    pages = [
        {"models": [{"name": "models/a"}], "nextPageToken": "p2"},
        {"models": [{"name": "models/b"}]},
    ]
    with respx.mock:
        route = respx.get(GEMINI_MODELS_URL).mock(side_effect=[httpx.Response(200, json=b) for b in pages])
        async with httpx.AsyncClient() as client:
            items = await adapter.fetch(
                client=client, secrets={"api_key": "k"}, kind="models", page=spec.catalog.page
            )

    assert [i.id for i in items] == ["a", "b"]
    assert route.calls[0].request.url.params["pageSize"] == "1000"
    assert route.calls[1].request.url.params["pageToken"] == "p2"


# ----------------------------------------------------------------------------- Deepgram
async def test_deepgram_models_are_public_and_split_the_stt_and_tts_envelopes() -> None:
    body = _fixture("deepgram_models.json")
    assert (len(body["stt"]), len(body["tts"])) == (449, 102), "the recorded public list"
    stt = get_adapter("deepgram_stt_models")
    tts = get_adapter("deepgram_tts_models")
    assert stt is not None and tts is not None and stt.public and tts.public

    with respx.mock:
        route = respx.get(DEEPGRAM_MODELS_URL).mock(return_value=httpx.Response(200, json=body))
        async with httpx.AsyncClient() as client:
            stt_items = await stt.fetch(client=client, secrets={}, kind="models")
            tts_items = await tts.fetch(client=client, secrets={}, kind="models")

    # 449 STT entries are every version of 42 canonical models: the catalog lists each id once.
    assert len(stt_items) == 42
    assert len({i.id for i in stt_items}) == 42
    assert "nova-3-general" in {i.id for i in stt_items}
    assert len(tts_items) == 102
    labels = {i.id: i.label for i in tts_items}
    assert labels["aura-2-agathe-fr"] == "Agathe (fr)"  # metadata.display_name
    assert labels["aura-2-thalia-en"] == "thalia (en)"  # no display_name upstream: the voice name
    assert "Authorization" not in route.calls.last.request.headers, "a public list is fetched keyless"


async def test_deepgram_models_have_no_voices_kind() -> None:
    adapter = get_adapter("deepgram_tts_models")
    assert adapter is not None
    async with httpx.AsyncClient() as client:
        with pytest.raises(UnsupportedCatalogKind):
            await adapter.fetch(client=client, secrets={}, kind="voices")


# --------------------------------------------------------------------------------- Rime
async def test_rime_voices_yield_model_voice_pairs_with_meta_model() -> None:
    body = _fixture("rime_voices_all_v2.json")
    adapter = get_adapter("rime_voices")
    assert adapter is not None and adapter.public

    with respx.mock:
        respx.get(RIME_VOICES_URL).mock(return_value=httpx.Response(200, json=body))
        async with httpx.AsyncClient() as client:
            voices = await adapter.fetch(client=client, secrets={}, kind="voices")
            models = await adapter.fetch(client=client, secrets={}, kind="models")

    assert [m.id for m in models] == sorted(body)  # the fixture is key-sorted
    assert {v.meta["model"] for v in voices} == set(body)
    mistv3 = [v for v in voices if v.meta["model"] == "mistv3"]
    expected = {name for names in body["mistv3"].values() for name in names}
    assert {v.id for v in mistv3} == expected
    assert all(v.label == f"{v.id} · mistv3" for v in mistv3)
    assert all(isinstance(v.meta["languages"], list) and v.meta["languages"] for v in mistv3)
    # The same voice name under two models is two items, one per model.
    assert len(voices) == sum(len({n for names in langs.values() for n in names}) for langs in body.values())


# ------------------------------------------------------------------- xAI, Cerebras, Together
async def test_xai_lists_language_models_with_modalities_in_meta() -> None:
    adapter = get_adapter("xai_models")
    assert adapter is not None
    body = {
        "models": [
            {
                "id": "grok-4-1-fast-non-reasoning",
                "aliases": ["grok-4-1-fast"],
                "input_modalities": ["text", "image"],
                "output_modalities": ["text"],
            }
        ]
    }
    with respx.mock:
        route = respx.get(XAI_MODELS_URL).mock(return_value=httpx.Response(200, json=body))
        async with httpx.AsyncClient() as client:
            items = await adapter.fetch(client=client, secrets={"api_key": "xk"}, kind="models")

    assert [i.id for i in items] == ["grok-4-1-fast-non-reasoning"]
    assert items[0].meta["input_modalities"] == ["text", "image"]
    assert route.calls.last.request.headers["Authorization"] == "Bearer xk"


@pytest.mark.parametrize(
    ("name", "url", "body", "expected"),
    [
        (
            "cerebras_models",
            "https://api.cerebras.ai/v1/models",
            {"object": "list", "data": [{"id": "gpt-oss-120b", "object": "model"}]},
            [("gpt-oss-120b", "gpt-oss-120b")],
        ),
        (
            "together_models",
            "https://api.together.xyz/v1/models",
            [
                {
                    "id": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
                    "display_name": "Llama 3.3 70B",
                    "type": "chat",
                }
            ],
            [("meta-llama/Llama-3.3-70B-Instruct-Turbo", "Llama 3.3 70B")],
        ),
    ],
)
async def test_openai_shaped_lists_parse_ids_and_labels(
    name: str, url: str, body: object, expected: list[tuple[str, str]]
) -> None:
    adapter = get_adapter(name)
    assert adapter is not None
    with respx.mock:
        respx.get(url).mock(return_value=httpx.Response(200, json=body))
        async with httpx.AsyncClient() as client:
            items = await adapter.fetch(client=client, secrets={"api_key": "k"}, kind="models")

    assert [(i.id, i.label) for i in items] == expected


# ------------------------------------------------------------------------------ Inworld
async def test_inworld_voices_send_basic_auth_as_issued_and_page_by_token() -> None:
    spec = get("inworld-tts")
    adapter = get_adapter("inworld_voices")
    assert adapter is not None and spec.catalog is not None
    pages = [
        {
            "voices": [{"voiceId": "Ashley", "displayName": "Ashley", "langCode": "EN_US"}],
            "nextPageToken": "n",
        },
        {"voices": [{"voiceId": "Dennis", "displayName": "Dennis"}]},
    ]
    with respx.mock:
        route = respx.get(INWORLD_VOICES_URL).mock(side_effect=[httpx.Response(200, json=b) for b in pages])
        async with httpx.AsyncClient() as client:
            items = await adapter.fetch(
                client=client, secrets={"api_key": "aW53b3JsZA=="}, kind="voices", page=spec.catalog.page
            )

    assert [(i.id, i.label) for i in items] == [("Ashley", "Ashley"), ("Dennis", "Dennis")]
    assert route.calls[0].request.headers["Authorization"] == "Basic aW53b3JsZA=="
    assert route.calls[1].request.url.params["pageToken"] == "n"


# ------------------------------------------------------------------------------- paging
async def test_the_anthropic_adapter_follows_last_id() -> None:
    spec = get("anthropic-llm")
    adapter = get_adapter("anthropic_models")
    assert adapter is not None and spec.catalog is not None and spec.catalog.page is not None
    pages = [
        {"data": [{"id": "claude-a"}, {"id": "claude-b"}], "has_more": True, "last_id": "claude-b"},
        {"data": [{"id": "claude-c"}], "has_more": False, "last_id": "claude-c"},
    ]
    with respx.mock:
        route = respx.get("https://api.anthropic.com/v1/models").mock(
            side_effect=[httpx.Response(200, json=b) for b in pages]
        )
        async with httpx.AsyncClient() as client:
            items = await adapter.fetch(
                client=client, secrets={"api_key": "ak"}, kind="models", page=spec.catalog.page
            )

    assert [i.id for i in items] == ["claude-a", "claude-b", "claude-c"]
    assert route.call_count == 2, "has_more=false stops the loop before a third request"
    assert "after_id" not in route.calls[0].request.url.params
    assert route.calls[1].request.url.params["after_id"] == "claude-b"
    assert route.calls[1].request.url.params["limit"] == "1000"


async def test_paging_stops_at_max_pages() -> None:
    page = PageSpec(kind="token", param="t", next_path="next", max_pages=3)
    with respx.mock:
        route = respx.get("https://vendor.example/list").mock(
            return_value=httpx.Response(200, json={"data": [{"id": "x"}], "next": "again"})
        )
        async with httpx.AsyncClient() as client:
            bodies = await fetch_pages(
                client, "https://vendor.example/list", vendor="V", headers={}, page=page
            )

    assert len(bodies) == 3
    assert route.call_count == 3


async def test_paging_stops_on_a_missing_next() -> None:
    page = PageSpec(kind="cursor", param="after", next_path="cursor", max_pages=10)
    with respx.mock:
        route = respx.get("https://vendor.example/list").mock(
            side_effect=[
                httpx.Response(200, json={"data": [{"id": "a"}], "cursor": "c1"}),
                httpx.Response(200, json={"data": [{"id": "b"}]}),
            ]
        )
        async with httpx.AsyncClient() as client:
            bodies = await fetch_pages(
                client, "https://vendor.example/list", vendor="V", headers={}, page=page
            )

    assert len(bodies) == 2
    assert route.call_count == 2


async def test_offset_and_page_kinds_advance_and_stop() -> None:
    offset = PageSpec(kind="offset", param="offset", size_param="limit", size=2)
    numbered = PageSpec(kind="page", param="page_number", next_path="total_pages")
    with respx.mock:
        off = respx.get("https://vendor.example/off").mock(
            side_effect=[
                httpx.Response(200, json={"data": [{"id": "a"}, {"id": "b"}]}),
                httpx.Response(200, json={"data": [{"id": "c"}]}),
            ]
        )
        num = respx.get("https://vendor.example/num").mock(
            side_effect=[
                httpx.Response(200, json={"voices_page": [{"id": "a"}], "total_pages": 2}),
                httpx.Response(200, json={"voices_page": [{"id": "b"}], "total_pages": 2}),
            ]
        )
        async with httpx.AsyncClient() as client:
            assert (
                len(
                    await fetch_pages(
                        client, "https://vendor.example/off", vendor="V", headers={}, page=offset
                    )
                )
                == 2
            )
            assert (
                len(
                    await fetch_pages(
                        client, "https://vendor.example/num", vendor="V", headers={}, page=numbered
                    )
                )
                == 2
            )

    assert off.calls[1].request.url.params["offset"] == "2"
    assert num.calls[1].request.url.params["page_number"] == "1"


async def test_a_later_page_failing_keeps_the_pages_already_read() -> None:
    page = PageSpec(kind="token", param="t", next_path="next")
    with respx.mock:
        respx.get("https://vendor.example/list").mock(
            side_effect=[httpx.Response(200, json={"data": [{"id": "a"}], "next": "n"}), httpx.Response(500)]
        )
        async with httpx.AsyncClient() as client:
            bodies = await fetch_pages(
                client, "https://vendor.example/list", vendor="V", headers={}, page=page
            )

    assert len(bodies) == 1


async def test_the_first_page_failing_raises_a_secret_free_error() -> None:
    with respx.mock:
        respx.get("https://vendor.example/list").mock(
            return_value=httpx.Response(401, text="bad key sk-secret")
        )
        async with httpx.AsyncClient() as client:
            with pytest.raises(CatalogAdapterError) as info:
                await fetch_pages(client, "https://vendor.example/list", vendor="V", headers={})

    assert "sk-secret" not in str(info.value)


async def test_a_credential_test_reads_page_one_only() -> None:
    spec = get("anthropic-llm")
    with respx.mock:
        route = respx.get("https://api.anthropic.com/v1/models").mock(
            return_value=httpx.Response(
                200, json={"data": [{"id": "claude-a"}], "has_more": True, "last_id": "a"}
            )
        )
        async with httpx.AsyncClient() as client:
            result = await credential_tests.run(spec, {"api_key": "ak"}, client)

    assert result is not None and result.ok is True
    assert route.call_count == 1
