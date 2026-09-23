"""Vendor catalog adapters: parsing, auth headers and error handling (V2-06).

`respx` mocks every vendor call — no real key is ever configured (see the
V2-06 card's "no live vendor calls" constraint). Beyond Presence and Simli
get the most thorough coverage: they are the two vendors the operator will
actually supply keys for (V2-05 report).
"""

from __future__ import annotations

import httpx
import pytest
import respx
from lkap_contracts.providers import get

from lkap_api.catalogs.adapters import ADAPTERS, get_adapter
from lkap_api.catalogs.base import (
    CatalogAdapterError,
    UnsupportedCatalogKind,
    normalize_secrets,
    parse_items,
)


# --------------------------------------------------------------------------- parse_items
def test_parse_items_accepts_a_bare_list() -> None:
    items = parse_items([{"id": "a", "name": "Alpha"}, {"id": "b", "name": "Beta"}])

    assert [(i.id, i.label) for i in items] == [("a", "Alpha"), ("b", "Beta")]


@pytest.mark.parametrize("envelope_key", ["data", "items", "voices", "avatars", "faces", "pals", "personas"])
def test_parse_items_unwraps_every_known_envelope_key(envelope_key: str) -> None:
    items = parse_items({envelope_key: [{"id": "x", "name": "X"}]})

    assert [i.id for i in items] == ["x"]


def test_parse_items_falls_back_to_id_when_no_label_field_is_present() -> None:
    items = parse_items([{"id": "solo"}])

    assert items == [items[0]]
    assert items[0].label == "solo"


def test_parse_items_accepts_plain_strings() -> None:
    items = parse_items(["mono", "stereo"])

    assert [(i.id, i.label) for i in items] == [("mono", "mono"), ("stereo", "stereo")]


def test_parse_items_skips_entries_with_no_recognisable_id() -> None:
    items = parse_items([{"description": "no id field at all"}, {"id": "kept"}])

    assert [i.id for i in items] == ["kept"]


def test_parse_items_stashes_the_raw_item_in_meta() -> None:
    raw = {"id": "x", "name": "X", "preview_url": "https://example.com/x.png"}

    items = parse_items([raw])

    assert items[0].meta == raw


def test_parse_items_returns_empty_for_an_unrecognisable_shape() -> None:
    assert parse_items({"unexpected": "shape"}) == []
    assert parse_items(None) == []


# ----------------------------------------------------------------------- normalize_secrets
def test_normalize_secrets_aliases_the_primary_field_to_api_key() -> None:
    spec = get("azure-tts")  # primary secret field is `speech_key`, not `api_key`

    normalized = normalize_secrets(spec, {"speech_key": "azure-secret-1"})

    assert normalized["api_key"] == "azure-secret-1"
    assert normalized["speech_key"] == "azure-secret-1"


def test_normalize_secrets_handles_simlis_nested_dotted_field_name() -> None:
    """Simli's one secret field is named `simli_config.api_key` (CONTRACTS-V2 D-V2-11)."""
    spec = get("simli-avatar")

    normalized = normalize_secrets(spec, {"simli_config.api_key": "simli-secret-1"})

    assert normalized["api_key"] == "simli-secret-1"


def test_normalize_secrets_is_a_no_op_for_plain_api_key_providers() -> None:
    spec = get("openai-llm")

    assert normalize_secrets(spec, {"api_key": "sk-1"})["api_key"] == "sk-1"


# ------------------------------------------------------------------- registered adapters
def test_every_registry_catalog_adapter_name_is_implemented() -> None:
    """Every `catalog=`/`test=` name the registry declares has a real adapter.

    D-ID (confirmed list API, docs/research-v2 §2.2) is intentionally not yet
    referenced by any registry entry — `did_avatars` exists in `ADAPTERS` and
    is tested below, but V2-05 owns wiring it in (docs/v2/_asks.md).
    """
    from lkap_contracts.providers import REGISTRY

    names = {spec.catalog.adapter for spec in REGISTRY if spec.catalog is not None}
    names |= {spec.test for spec in REGISTRY if spec.test is not None}

    missing = names - set(ADAPTERS)
    assert not missing, f"registry names an adapter this package does not implement: {missing}"


# --------------------------------------------------------------------- Beyond Presence
class TestBeyondPresence:
    """Bey is one of the two vendors the operator will supply a real key for."""

    async def test_fetch_parses_avatars_and_sends_the_x_api_key_header(self) -> None:
        spec = get("bey-avatar")
        adapter = get_adapter("bey_avatars")
        assert adapter is not None
        secrets = normalize_secrets(spec, {"api_key": "bey-secret-1"})

        with respx.mock:
            route = respx.get("https://api.bey.dev/v1/avatars").mock(
                return_value=httpx.Response(
                    200,
                    json=[
                        {"id": "b9be11b8-89fb-4227-8f86-4a881393cbdb", "name": "Default"},
                        {"id": "avatar-2", "name": "Second"},
                    ],
                )
            )
            async with httpx.AsyncClient() as client:
                items = await adapter.fetch(client=client, secrets=secrets, kind="avatars")

        assert route.calls.last.request.headers["x-api-key"] == "bey-secret-1"
        assert [(i.id, i.label) for i in items] == [
            ("b9be11b8-89fb-4227-8f86-4a881393cbdb", "Default"),
            ("avatar-2", "Second"),
        ]

    async def test_fetch_also_accepts_a_data_envelope(self) -> None:
        """Response shape is UNVERIFIED against a real key; both plausible shapes must parse."""
        adapter = get_adapter("bey_avatars")
        assert adapter is not None

        with respx.mock:
            respx.get("https://api.bey.dev/v1/avatars").mock(
                return_value=httpx.Response(200, json={"data": [{"id": "x", "name": "X"}]})
            )
            async with httpx.AsyncClient() as client:
                items = await adapter.fetch(client=client, secrets={"api_key": "k"}, kind="avatars")

        assert [i.id for i in items] == ["x"]

    async def test_fetch_raises_on_an_unauthorized_key(self) -> None:
        adapter = get_adapter("bey_avatars")
        assert adapter is not None

        with respx.mock:
            respx.get("https://api.bey.dev/v1/avatars").mock(return_value=httpx.Response(401))
            async with httpx.AsyncClient() as client:
                with pytest.raises(CatalogAdapterError):
                    await adapter.fetch(client=client, secrets={"api_key": "bad"}, kind="avatars")

    async def test_fetch_raises_on_a_network_error(self) -> None:
        adapter = get_adapter("bey_avatars")
        assert adapter is not None

        with respx.mock:
            respx.get("https://api.bey.dev/v1/avatars").mock(side_effect=httpx.ConnectTimeout("timed out"))
            async with httpx.AsyncClient() as client:
                with pytest.raises(CatalogAdapterError):
                    await adapter.fetch(client=client, secrets={"api_key": "k"}, kind="avatars")

    def test_has_no_endpoint_for_unrelated_kinds(self) -> None:
        adapter = ADAPTERS["bey_avatars"]
        assert "personas" not in adapter.endpoints
        assert "models" not in adapter.endpoints


# ------------------------------------------------------------------------------- Simli
class TestSimli:
    """Simli is the other vendor the operator will supply a real key for."""

    async def test_fetch_parses_faces_and_sends_the_x_simli_api_key_header(self) -> None:
        spec = get("simli-avatar")
        adapter = get_adapter("simli_faces")
        assert adapter is not None
        # The stored bag's key is the dotted field name, exactly as an admin's
        # `POST /v1/credentials` payload for `simli-avatar` would store it.
        secrets = normalize_secrets(spec, {"simli_config.api_key": "simli-secret-1"})

        with respx.mock:
            route = respx.get("https://api.simli.ai/faces").mock(
                return_value=httpx.Response(
                    200, json=[{"face_id": "face-1", "face_name": "Aria"}, {"face_id": "face-2"}]
                )
            )
            async with httpx.AsyncClient() as client:
                items = await adapter.fetch(client=client, secrets=secrets, kind="avatars")

        assert route.calls.last.request.headers["x-simli-api-key"] == "simli-secret-1"
        assert "Authorization" not in route.calls.last.request.headers
        assert [(i.id, i.label) for i in items] == [("face-1", "Aria"), ("face-2", "face-2")]

    async def test_fetch_raises_on_timeout(self) -> None:
        adapter = get_adapter("simli_faces")
        assert adapter is not None

        with respx.mock:
            respx.get("https://api.simli.ai/faces").mock(side_effect=httpx.ReadTimeout("timed out"))
            async with httpx.AsyncClient() as client:
                with pytest.raises(CatalogAdapterError):
                    await adapter.fetch(client=client, secrets={"api_key": "k"}, kind="avatars")

    async def test_fetch_has_no_personas_endpoint(self) -> None:
        adapter = get_adapter("simli_faces")
        assert adapter is not None

        with pytest.raises(UnsupportedCatalogKind):
            async with httpx.AsyncClient() as client:
                await adapter.fetch(client=client, secrets={"api_key": "k"}, kind="personas")


# --------------------------------------------------------------------------- other vendors
async def test_tavus_fetches_faces_and_pals_from_two_endpoints() -> None:
    adapter = get_adapter("tavus_faces_pals")
    assert adapter is not None

    with respx.mock:
        respx.get("https://tavusapi.com/v2/faces").mock(
            return_value=httpx.Response(200, json={"data": [{"face_id": "f1", "face_name": "Face One"}]})
        )
        respx.get("https://tavusapi.com/v2/pals").mock(
            return_value=httpx.Response(200, json={"data": [{"pal_id": "p1", "pal_name": "Pal One"}]})
        )
        async with httpx.AsyncClient() as client:
            faces = await adapter.fetch(client=client, secrets={"api_key": "k"}, kind="avatars")
            pals = await adapter.fetch(client=client, secrets={"api_key": "k"}, kind="personas")

    assert [(i.id, i.label) for i in faces] == [("f1", "Face One")]
    assert [(i.id, i.label) for i in pals] == [("p1", "Pal One")]


async def test_anam_fetches_avatars_and_personas() -> None:
    adapter = get_adapter("anam_avatars")
    assert adapter is not None

    with respx.mock:
        respx.get("https://api.anam.ai/v1/avatars").mock(
            return_value=httpx.Response(200, json=[{"avatarId": "a1", "name": "Ava"}])
        )
        respx.get("https://api.anam.ai/v1/personas").mock(return_value=httpx.Response(200, json=[]))
        async with httpx.AsyncClient() as client:
            avatars = await adapter.fetch(client=client, secrets={"api_key": "k"}, kind="avatars")
            personas = await adapter.fetch(client=client, secrets={"api_key": "k"}, kind="personas")

    assert [(i.id, i.label) for i in avatars] == [("a1", "Ava")]
    assert personas == []


async def test_openai_models_uses_bearer_auth() -> None:
    adapter = get_adapter("openai_models")
    assert adapter is not None

    with respx.mock:
        route = respx.get("https://api.openai.com/v1/models").mock(
            return_value=httpx.Response(200, json={"data": [{"id": "gpt-4.1"}]})
        )
        async with httpx.AsyncClient() as client:
            items = await adapter.fetch(client=client, secrets={"api_key": "sk-1"}, kind="models")

    assert route.calls.last.request.headers["Authorization"] == "Bearer sk-1"
    assert [i.id for i in items] == ["gpt-4.1"]


async def test_anthropic_models_sends_x_api_key_and_version() -> None:
    adapter = get_adapter("anthropic_models")
    assert adapter is not None

    with respx.mock:
        route = respx.get("https://api.anthropic.com/v1/models").mock(
            return_value=httpx.Response(200, json={"data": [{"id": "claude-sonnet-4-6"}]})
        )
        async with httpx.AsyncClient() as client:
            await adapter.fetch(client=client, secrets={"api_key": "ak-1"}, kind="models")

    sent = route.calls.last.request.headers
    assert sent["x-api-key"] == "ak-1"
    assert sent["anthropic-version"] == "2023-06-01"


async def test_cartesia_voices_sends_the_cartesia_version_header() -> None:
    adapter = get_adapter("cartesia_voices")
    assert adapter is not None

    with respx.mock:
        route = respx.get("https://api.cartesia.ai/voices").mock(
            return_value=httpx.Response(200, json=[{"id": "v1", "name": "Voice One"}])
        )
        async with httpx.AsyncClient() as client:
            await adapter.fetch(client=client, secrets={"api_key": "ck-1"}, kind="voices")

    sent = route.calls.last.request.headers
    assert sent["X-API-Key"] == "ck-1"
    assert sent["Cartesia-Version"] == "2024-06-10"


async def test_elevenlabs_voices_and_models_use_the_xi_api_key_header() -> None:
    adapter = get_adapter("elevenlabs_voices")
    assert adapter is not None

    with respx.mock:
        respx.get("https://api.elevenlabs.io/v1/voices").mock(
            return_value=httpx.Response(200, json={"voices": [{"voice_id": "v1", "name": "V"}]})
        )
        respx.get("https://api.elevenlabs.io/v1/models").mock(
            return_value=httpx.Response(200, json={"models": [{"model_id": "m1"}]})
        )
        async with httpx.AsyncClient() as client:
            voices = await adapter.fetch(client=client, secrets={"api_key": "el-1"}, kind="voices")
            models = await adapter.fetch(client=client, secrets={"api_key": "el-1"}, kind="models")

    assert [i.id for i in voices] == ["v1"]
    assert [i.id for i in models] == ["m1"]


async def test_did_avatars_adapter_exists_though_unwired() -> None:
    """`did-avatar` has a confirmed list API (research §2.2) but no `catalog=`/`test=` yet."""
    adapter = get_adapter("did_avatars")
    assert adapter is not None

    with respx.mock:
        respx.get("https://api.d-id.com/clips/presenters").mock(
            return_value=httpx.Response(200, json={"presenters": [{"presenter_id": "p1", "name": "P"}]})
        )
        async with httpx.AsyncClient() as client:
            items = await adapter.fetch(client=client, secrets={"api_key": "d-1"}, kind="avatars")

    assert [i.id for i in items] == ["p1"]
    spec = get("did-avatar")
    assert spec.catalog is None, "still V2-05's file to wire — see docs/v2/_asks.md"
