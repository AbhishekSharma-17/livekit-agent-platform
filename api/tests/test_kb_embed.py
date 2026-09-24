"""Tests for `lkap_api.kb.embed`: the fake embedder and `resolve_embedder`."""

from __future__ import annotations

import json
import math

import httpx
import pytest
import respx

from lkap_api.db.session import Database
from lkap_api.errors import UnprocessableEntityError
from lkap_api.kb.embed import FakeEmbedder, FastEmbedEmbedder, OpenAIEmbedder, resolve_embedder
from lkap_api.settings import Settings
from lkap_api.vault import Vault


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


async def test_fake_embedder_shares_more_signal_between_related_texts() -> None:
    embedder = FakeEmbedder()
    vectors = await embedder.embed(
        ["flood damage claim intake", "flood damage claim report", "unrelated text about car engines"]
    )
    assert _cosine(vectors[0], vectors[1]) > _cosine(vectors[0], vectors[2])


async def test_fake_embedder_vectors_are_unit_normalised() -> None:
    [vector] = await FakeEmbedder().embed(["hello hello world"])
    norm = math.sqrt(sum(component * component for component in vector))
    assert norm == pytest.approx(1.0)


async def test_fake_embedder_empty_input_returns_empty_list() -> None:
    assert await FakeEmbedder().embed([]) == []


async def test_resolve_embedder_default_is_fastembed_and_does_not_load_the_model(
    settings: Settings, database: Database
) -> None:
    async with database.session() as session:
        embedder = await resolve_embedder(settings, session, Vault(settings.master_key))
    assert isinstance(embedder, FastEmbedEmbedder)


async def test_resolve_embedder_rejects_an_unsupported_scheme(settings: Settings, database: Database) -> None:
    settings.embedder = "pinecone:whatever"
    with pytest.raises(UnprocessableEntityError):
        async with database.session() as session:
            await resolve_embedder(settings, session, Vault(settings.master_key))


async def test_resolve_embedder_rejects_an_unknown_credential(settings: Settings, database: Database) -> None:
    settings.embedder = "openai:does-not-exist"
    with pytest.raises(UnprocessableEntityError):
        async with database.session() as session:
            await resolve_embedder(settings, session, Vault(settings.master_key))


async def test_resolve_embedder_openai_scheme_builds_an_openai_embedder(
    settings: Settings, database: Database
) -> None:
    from lkap_api.db.models import Credential

    vault = Vault(settings.master_key)
    async with database.session() as session:
        session.add(
            Credential(
                id="cred-openai-embed",
                provider_id="openai-embedding",
                label="OpenAI",
                ciphertext=vault.encrypt({"api_key": "sk-test-123"}),
                fingerprint="...1234",
            )
        )
    settings.embedder = "openai:cred-openai-embed"
    async with database.session() as session:
        embedder = await resolve_embedder(settings, session, vault)
    assert isinstance(embedder, OpenAIEmbedder)


async def test_openai_embedder_posts_to_the_embeddings_endpoint() -> None:
    recorded: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.2, 0.3]},
                    {"index": 0, "embedding": [0.1, 0.1]},
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        embedder = OpenAIEmbedder(api_key="sk-test", client=client)
        vectors = await embedder.embed(["first", "second"])

    assert vectors == [[0.1, 0.1], [0.2, 0.3]]  # re-ordered by the `index` OpenAI returns
    assert recorded[0].headers["authorization"] == "Bearer sk-test"


async def test_openai_embedder_empty_input_returns_empty_list() -> None:
    assert await OpenAIEmbedder(api_key="sk-test").embed([]) == []


# ------------------------------------------------------------------ `<provider_id>:<credential_id>` (D-V4-13)


async def _store_credential(
    database: Database, vault: Vault, *, credential_id: str, provider_id: str
) -> None:
    from lkap_api.db.models import Credential

    async with database.session() as session:
        session.add(
            Credential(
                id=credential_id,
                provider_id=provider_id,
                label=provider_id,
                ciphertext=vault.encrypt({"api_key": "sk-or-v1-embed"}),
                fingerprint="...mbed",
            )
        )


async def test_resolve_embedder_openrouter_form_uses_the_openrouter_url_and_model(
    settings: Settings, database: Database
) -> None:
    vault = Vault(settings.master_key)
    await _store_credential(database, vault, credential_id="cred-or-embed", provider_id="openrouter-llm")
    settings.embedder = "openrouter-embedding:cred-or-embed"
    async with database.session() as session:
        embedder = await resolve_embedder(settings, session, vault)
    assert isinstance(embedder, OpenAIEmbedder)
    assert embedder.dimension == 1536

    with respx.mock:
        route = respx.post("https://openrouter.ai/api/v1/embeddings").mock(
            return_value=httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.5, 0.5]}]})
        )
        vectors = await embedder.embed(["hello"])

    assert vectors == [[0.5, 0.5]]
    request = route.calls.last.request
    assert request.headers["authorization"] == "Bearer sk-or-v1-embed"
    assert json.loads(request.content)["model"] == "openai/text-embedding-3-small"


async def test_resolve_embedder_legacy_openai_form_still_calls_openai(
    settings: Settings, database: Database
) -> None:
    vault = Vault(settings.master_key)
    await _store_credential(database, vault, credential_id="cred-legacy", provider_id="openai-llm")
    settings.embedder = "openai:cred-legacy"
    async with database.session() as session:
        embedder = await resolve_embedder(settings, session, vault)

    with respx.mock:
        route = respx.post("https://api.openai.com/v1/embeddings").mock(
            return_value=httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})
        )
        await embedder.embed(["hello"])

    assert json.loads(route.calls.last.request.content)["model"] == "text-embedding-3-small"


@pytest.mark.parametrize(
    "value",
    ["openrouter-stt:cred-or-embed2", "openrouter-llm:cred-or-embed2", "fastembed-embedding:x", "openai"],
)
async def test_resolve_embedder_rejects_non_embedding_or_malformed_values(
    value: str, settings: Settings, database: Database
) -> None:
    vault = Vault(settings.master_key)
    await _store_credential(database, vault, credential_id="cred-or-embed2", provider_id="openrouter-llm")
    settings.embedder = value
    with pytest.raises(UnprocessableEntityError):
        async with database.session() as session:
            await resolve_embedder(settings, session, vault)


async def test_resolve_embedder_rejects_a_credential_of_another_provider(
    settings: Settings, database: Database
) -> None:
    vault = Vault(settings.master_key)
    await _store_credential(database, vault, credential_id="cred-openai-x", provider_id="openai-llm")
    settings.embedder = "openrouter-embedding:cred-openai-x"
    with pytest.raises(UnprocessableEntityError, match="belongs to provider 'openai-llm'"):
        async with database.session() as session:
            await resolve_embedder(settings, session, vault)
