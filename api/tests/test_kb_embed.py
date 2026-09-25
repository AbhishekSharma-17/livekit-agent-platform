"""Tests for `lkap_api.kb.embed`: the fake embedder and `resolve_embedder`."""

from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path

import httpx
import pytest
import respx

from lkap_api.db.models import KnowledgeBase
from lkap_api.db.session import Database
from lkap_api.errors import UnprocessableEntityError
from lkap_api.kb import embed as embed_module
from lkap_api.kb.embed import (
    FAKE_EMBEDDER_MODEL_ID,
    FakeEmbedder,
    FastEmbedEmbedder,
    KbEmbedderMismatchError,
    OpenAIEmbedder,
    approx_token_count,
    check_kb_embedder,
    clear_embedder_cache,
    kb_embedder_mismatch,
    record_kb_embedder,
    resolve_embedder,
    token_counter_for,
    warm_default_embedder,
)
from lkap_api.kb.ingest import warm_embedder
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


# ------------------------------------------------------------------ model names from settings (V5-01)


async def test_resolve_embedder_with_lkap_embed_model_unset_is_bge_small_384(
    settings: Settings, database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LKAP_EMBED_MODEL", raising=False)
    monkeypatch.delenv("LKAP_RERANK_MODEL", raising=False)
    fresh = Settings()  # type: ignore[call-arg]
    assert fresh.embed_model == "BAAI/bge-small-en-v1.5"
    assert fresh.rerank_model == "Xenova/ms-marco-MiniLM-L-6-v2"
    async with database.session() as session:
        embedder = await resolve_embedder(fresh, session, Vault(settings.master_key))
    assert isinstance(embedder, FastEmbedEmbedder)
    assert embedder.dimension == 384
    assert embedder.model_id == "BAAI/bge-small-en-v1.5"


async def test_resolve_embedder_uses_lkap_embed_model(
    settings: Settings, database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LKAP_EMBED_MODEL", "BAAI/bge-base-en-v1.5")
    monkeypatch.setenv("LKAP_RERANK_MODEL", "Xenova/ms-marco-MiniLM-L-12-v2")
    fresh = Settings()  # type: ignore[call-arg]
    assert fresh.rerank_model == "Xenova/ms-marco-MiniLM-L-12-v2"
    async with database.session() as session:
        embedder = await resolve_embedder(fresh, session, Vault(settings.master_key))
    assert embedder.model_id == "BAAI/bge-base-en-v1.5"
    assert embedder.dimension == 768


async def test_resolve_embedder_rejects_an_unknown_fastembed_model(
    settings: Settings, database: Database
) -> None:
    settings.embed_model = "example/not-a-model"
    with pytest.raises(UnprocessableEntityError, match="LKAP_EMBED_MODEL"):
        async with database.session() as session:
            await resolve_embedder(settings, session, Vault(settings.master_key))


async def test_resolve_embedder_openrouter_form_reports_its_model_id(
    settings: Settings, database: Database
) -> None:
    vault = Vault(settings.master_key)
    await _store_credential(database, vault, credential_id="cred-or-model", provider_id="openrouter-llm")
    settings.embedder = "openrouter-embedding:cred-or-model"
    async with database.session() as session:
        embedder = await resolve_embedder(settings, session, vault)
    assert embedder.model_id == "openai/text-embedding-3-small"
    assert embedder.dimension == 1536


async def test_openai_embedder_learns_an_unknown_models_width_from_its_first_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1] * 8}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        embedder = OpenAIEmbedder(api_key="sk-test", model="vendor/custom-embed", client=client)
        assert embedder.dimension is None
        await embedder.embed(["hello"])
    assert embedder.dimension == 8


async def test_token_counter_for_falls_back_to_the_heuristic() -> None:
    counter = await token_counter_for(FakeEmbedder())
    assert counter is approx_token_count
    # Claim · AUTO · - · 11111 · , · please · .
    assert counter("Claim AUTO-11111, please.") == 7


async def test_fastembed_token_counter_subtracts_the_special_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Model:
        def token_count(self, text: str) -> int:
            return 2 + len(text.split())

    async def fake_model() -> object:
        return Model()

    embedder = FastEmbedEmbedder(tmp_path)
    # Stands in for the (network) model load the conftest forbids.
    monkeypatch.setattr(embedder, "_get_model", fake_model)
    counter = await token_counter_for(embedder)
    assert counter("") == 0
    assert counter("three short words") == 3


def _kb(**columns: object) -> KnowledgeBase:
    return KnowledgeBase(id="kb1", name="Policies", **columns)


@pytest.mark.parametrize(
    ("columns", "mismatch"),
    [
        ({}, False),  # created before V5-01: never refused
        ({"dimension": 32, "embedder_model": FAKE_EMBEDDER_MODEL_ID}, False),
        ({"dimension": 32}, False),
        ({"dimension": 1536}, True),
        ({"embedder_model": "BAAI/bge-small-en-v1.5"}, True),
    ],
)
def test_check_kb_embedder_compares_only_what_the_kb_recorded(
    columns: dict[str, object], mismatch: bool
) -> None:
    kb = _kb(**columns)
    assert (kb_embedder_mismatch(kb, FakeEmbedder()) is not None) is mismatch
    if mismatch:
        with pytest.raises(KbEmbedderMismatchError) as caught:
            check_kb_embedder(kb, FakeEmbedder())
        assert caught.value.status_code == 422
        assert caught.value.code == "kb_embedder_mismatch"
        assert "Policies" in caught.value.message
    else:
        check_kb_embedder(kb, FakeEmbedder())


def test_record_kb_embedder_fills_only_unset_values() -> None:
    kb = _kb()
    record_kb_embedder(kb, FakeEmbedder())
    assert (kb.dimension, kb.embedder_model) == (32, FAKE_EMBEDDER_MODEL_ID)
    kept = _kb(dimension=384, embedder_model="BAAI/bge-small-en-v1.5")
    record_kb_embedder(kept, FakeEmbedder())
    assert (kept.dimension, kept.embedder_model) == (384, "BAAI/bge-small-en-v1.5")


# --------------------------------------------------------------------------- warm-up (V4-18, R-V4-65)
class _RecordingLog:
    """Stands in for the module's structlog logger; keeps ``(level, event, fields)``."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, object]]] = []

    def info(self, event: str, **fields: object) -> None:
        self.events.append(("info", event, fields))

    def warning(self, event: str, **fields: object) -> None:
        self.events.append(("warning", event, fields))


@pytest.fixture
def embed_log(monkeypatch: pytest.MonkeyPatch) -> _RecordingLog:
    recorder = _RecordingLog()
    monkeypatch.setattr(embed_module, "log", recorder)
    return recorder


async def test_fastembed_warm_loads_the_model_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    loads: list[str] = []

    async def _load(self: FastEmbedEmbedder) -> object:
        loads.append(self.model_id)
        return object()

    monkeypatch.setattr(FastEmbedEmbedder, "_get_model", _load)
    embedder = FastEmbedEmbedder(tmp_path)

    await embedder.warm()
    await warm_embedder(embedder)

    assert loads == [embedder.model_id, embedder.model_id]


async def test_warm_embedder_skips_an_embedder_without_a_model() -> None:
    await warm_embedder(FakeEmbedder())  # nothing to load, nothing raised


async def test_warm_default_embedder_logs_a_failed_download_and_never_raises(
    settings: Settings, embed_log: _RecordingLog
) -> None:
    clear_embedder_cache()
    # The session-wide conftest patch makes `_get_model` raise: a download that fails.
    await warm_default_embedder(settings)

    assert [(level, event) for level, event, _ in embed_log.events] == [
        ("warning", "fastembed_warmup_failed")
    ]
    assert embed_log.events[0][2]["error_type"] == "RuntimeError"


async def test_warm_default_embedder_logs_done_once_the_model_is_loaded(
    settings: Settings, embed_log: _RecordingLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _load(self: FastEmbedEmbedder) -> object:
        return object()

    monkeypatch.setattr(FastEmbedEmbedder, "_get_model", _load)
    clear_embedder_cache()

    await warm_default_embedder(settings)

    assert [event for _, event, _ in embed_log.events] == ["fastembed_warmup_done"]


async def test_warm_default_embedder_skips_a_remote_embedder(
    settings: Settings, embed_log: _RecordingLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _never(*_args: object, **_kwargs: object) -> FastEmbedEmbedder:
        raise AssertionError("a remote embedder has no local model to warm")

    monkeypatch.setattr(embed_module, "get_fastembed_embedder", _never)
    settings.embedder = "openai-embedding:cred123"

    await warm_default_embedder(settings)

    assert embed_log.events == []


async def test_the_api_lifespan_starts_the_warmup_once_and_survives_its_failure(
    settings: Settings, database: Database, embed_log: _RecordingLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lkap_api import main as main_module

    calls: list[Settings] = []
    real_warm = main_module.warm_default_embedder

    async def _counting(resolved: Settings) -> None:
        calls.append(resolved)
        await real_warm(resolved)

    monkeypatch.setattr(main_module, "warm_default_embedder", _counting)
    clear_embedder_cache()
    app = main_module.create_app(settings)

    async with app.router.lifespan_context(app):
        task = app.state.embedder_warmup_task
        await asyncio.wait_for(asyncio.shield(task), timeout=10)
        assert task.done() and task.exception() is None

    assert calls == [settings]
    assert ("warning", "fastembed_warmup_failed") in [(level, event) for level, event, _ in embed_log.events]
