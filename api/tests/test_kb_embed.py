"""Tests for `lkap_api.kb.embed`: the fake embedder and `resolve_embedder`."""

from __future__ import annotations

import math

import httpx
import pytest

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
