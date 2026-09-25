"""V5-04 knowledge search: fan-out, hybrid (lexical + vector by RRF), rerank, score floor, LRU.

The suite builds its schema with ``create_all``, which knows nothing of the
lexical index migration ``v5_001`` creates outside the ORM, so the hybrid
tests create it from the migration's own DDL (:func:`create_lexical_index`).
Vectors are chosen per chunk (:class:`StubEmbedder`), so each test decides
exactly where a chunk ranks in the vector list; the cross-encoder is a fake
(:class:`KeywordReranker`) except in the opt-in model test at the end.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import time
from collections.abc import Iterator, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text, update

from lkap_api.db.models import KbChunk, KbDocument, KnowledgeBase, new_id
from lkap_api.db.session import Database
from lkap_api.kb.embed import FakeEmbedder
from lkap_api.kb.lexical import fts5_match, lexical_search, query_tokens, tsquery_text
from lkap_api.kb.rerank import LocalReranker, sigmoid
from lkap_api.kb.search import QUERY_CACHE, QueryEmbeddingCache, fuse_rrf, normalise_query, search_kbs
from lkap_api.kb.service import KnowledgeService
from lkap_api.kb.store import LanceDBStore, VectorHit, VectorRecord, get_lancedb_store
from lkap_api.routers.knowledge import get_embedder, get_reranker
from lkap_api.settings import Settings

API_ROOT = Path(__file__).resolve().parents[1]
STUB_DIMENSION = 4
STUB_MODEL = "stub-4"


# --------------------------------------------------------------------------- helpers
def _migration() -> ModuleType:
    path = API_ROOT / "alembic" / "versions" / "v5_001_knowledge_p0.py"
    spec = importlib.util.spec_from_file_location("v5_001_knowledge_p0", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def create_lexical_index(database: Database) -> None:
    """Create the lexical index exactly as migration ``v5_001`` does (SQLite FTS5 or Postgres tsv)."""
    migration = _migration()
    async with database.engine.begin() as conn:
        if conn.dialect.name == "sqlite":
            for statement in migration._SQLITE_FTS_UP:
                await conn.execute(text(statement))
        else:
            await conn.execute(
                text(
                    "ALTER TABLE kb_chunks ADD COLUMN tsv tsvector "
                    "GENERATED ALWAYS AS (to_tsvector('english', text)) STORED"
                )
            )
            await conn.execute(text(f"CREATE INDEX {migration.PG_TSV_INDEX} ON kb_chunks USING gin (tsv)"))


class StubEmbedder:
    """Returns a fixed vector per (normalised) text; anything else maps to the last axis."""

    dimension = STUB_DIMENSION
    model_id = STUB_MODEL

    def __init__(self, vectors: dict[str, list[float]] | None = None) -> None:
        self.vectors = {normalise_query(key): value for key, value in (vectors or {}).items()}
        self.calls: list[list[str]] = []

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self.vectors.get(normalise_query(t), [0.0, 0.0, 0.0, 1.0]) for t in texts]


class KeywordReranker:
    """A fake cross-encoder: logit +4 for a passage containing the keyword, -4 otherwise."""

    model_id = "keyword-fake"

    def __init__(self, keyword: str) -> None:
        self.keyword = keyword.casefold()
        self.calls = 0

    async def rerank(self, query: str, passages: Sequence[str]) -> list[float]:
        self.calls += 1
        return [4.0 if self.keyword in passage.casefold() else -4.0 for passage in passages]


class DelayedStore:
    """A vector store whose per-KB query sleeps (or raises) as configured; no hits unless given."""

    def __init__(
        self,
        delays: dict[str, float],
        *,
        hits: dict[str, list[VectorHit]] | None = None,
        failing: set[str] | None = None,
    ) -> None:
        self.delays = delays
        self.hits = hits or {}
        self.failing = failing or set()

    async def upsert(self, kb_id: str, records: list[VectorRecord]) -> None:
        raise AssertionError("search never writes")

    async def query(self, kb_id: str, vector: list[float], k: int) -> list[VectorHit]:
        await asyncio.sleep(self.delays.get(kb_id, 0.0))
        if kb_id in self.failing:
            raise RuntimeError("store unavailable")
        return self.hits.get(kb_id, [])[:k]

    async def delete_document(self, kb_id: str, document_id: str) -> None:
        raise AssertionError("search never writes")

    async def delete_kb(self, kb_id: str) -> None:
        raise AssertionError("search never writes")

    async def optimize(self, kb_id: str) -> None:
        raise AssertionError("search never writes")


async def seed_kb(
    database: Database,
    store: LanceDBStore,
    chunks: list[tuple[str, list[float]]],
    *,
    name: str = "Harbor Lane policies",
    meta: dict[str, Any] | None = None,
) -> tuple[str, list[str]]:
    """One knowledge base with one document whose chunks have the given text and vectors."""
    kb_id, document_id = new_id(), new_id()
    chunk_ids = [new_id() for _ in chunks]
    async with database.session() as session:
        session.add(KnowledgeBase(id=kb_id, name=name, dimension=STUB_DIMENSION, embedder_model=STUB_MODEL))
        await session.flush()
        session.add(
            KbDocument(
                id=document_id,
                kb_id=kb_id,
                filename="policies.md",
                mime="text/markdown",
                bytes=1,
                status="ready",
            )
        )
        await session.flush()
        for ordinal, (chunk_id, (chunk_text, _)) in enumerate(zip(chunk_ids, chunks, strict=True)):
            session.add(
                KbChunk(
                    id=chunk_id,
                    kb_id=kb_id,
                    document_id=document_id,
                    ordinal=ordinal,
                    text=chunk_text,
                    meta=meta or {"filename": "policies.md"},
                )
            )
    await store.upsert(
        kb_id,
        [
            VectorRecord(id=chunk_id, vector=vector, document_id=document_id)
            for chunk_id, (_, vector) in zip(chunk_ids, chunks, strict=True)
        ],
    )
    return kb_id, chunk_ids


@pytest.fixture(autouse=True)
def _fresh_query_cache() -> Iterator[None]:
    QUERY_CACHE.clear()
    yield
    QUERY_CACHE.clear()


@pytest.fixture
def store(settings: Settings) -> LanceDBStore:
    return get_lancedb_store(settings.data_dir)


# The hybrid fixture: the vector list ranks the identifier's chunk third; only
# the keyword list finds it.
QUERY = "AUTO-11111"
DECOY_COLLISION = "Collision cover pays for damage to your own car."
DECOY_THEFT = "Comprehensive cover includes theft and fire."
TARGET = "Policy AUTO-11111 lists two named drivers."
UNRELATED = "Home policy HOME-22222 covers burst pipes."
HYBRID_CHUNKS: list[tuple[str, list[float]]] = [
    (DECOY_COLLISION, [0.95, 0.31, 0.0, 0.0]),
    (DECOY_THEFT, [0.90, 0.43, 0.0, 0.0]),
    (TARGET, [0.50, 0.86, 0.0, 0.0]),
    (UNRELATED, [0.0, 1.0, 0.0, 0.0]),
]
HYBRID_EMBEDDER_VECTORS = {QUERY: [1.0, 0.0, 0.0, 0.0]}


# --------------------------------------------------------------------------- units: fusion, tokens, cache
def test_fuse_rrf_three_hit_example_is_pinned() -> None:
    # vector: a, b, c; lexical: c, a.  a = 1/61 + 1/62, c = 1/63 + 1/61, b = 1/62.
    fused = fuse_rrf([["a", "b", "c"], ["c", "a"]])
    assert [item.id for item in fused] == ["a", "c", "b"]
    assert [item.ranks for item in fused] == [(1, 2), (3, 1), (2, None)]
    assert fused[0].rrf == pytest.approx(1 / 61 + 1 / 62)
    assert fused[1].rrf == pytest.approx(1 / 63 + 1 / 61)
    assert fused[2].rrf == pytest.approx(1 / 62)
    # Normalised by the maximum (first in both lists = 2/61).
    assert fused[0].score == pytest.approx((1 / 61 + 1 / 62) / (2 / 61))
    assert all(0.0 < item.score <= 1.0 for item in fused)


def test_fuse_rrf_breaks_ties_by_the_earlier_list_then_id() -> None:
    fused = fuse_rrf([["x", "y"], ["y", "x"]])
    assert [item.id for item in fused] == ["x", "y"]
    assert fused[0].rrf == fused[1].rrf
    assert fuse_rrf([["solo"]])[0].score == pytest.approx(1.0)
    assert fuse_rrf([]) == []


@pytest.mark.parametrize(
    ("query", "tokens"),
    [
        ("AUTO-11111", ["AUTO", "11111"]),
        ('what* is NEAR "the" -deductible: AND or', ["what", "is", "NEAR", "the", "deductible", "AND", "or"]),
        ("  ___ --- ", []),
        ("Haan ji, haan", ["Haan", "ji"]),
        # Devanagari vowel signs are marks, not letters: the word must stay whole.
        ("बीमा पॉलिसी कवर?", ["बीमा", "पॉलिसी", "कवर"]),
    ],
)
def test_query_tokens_keep_letters_and_digits_only(query: str, tokens: list[str]) -> None:
    assert query_tokens(query) == tokens


def test_index_query_builders_quote_every_token() -> None:
    assert fts5_match(["AUTO", "11111"]) == 'text:("AUTO" OR "11111")'
    assert tsquery_text(["AUTO", "11111"]) == "AUTO | 11111"


def test_sigmoid_maps_logits_into_zero_one_monotonically() -> None:
    values = [sigmoid(x) for x in (-1000.0, -4.0, 0.0, 4.0, 1000.0)]
    assert values == sorted(values)
    assert values[2] == 0.5
    assert all(0.0 <= v <= 1.0 for v in values)


async def test_query_cache_hits_on_a_repeated_normalised_query() -> None:
    cache = QueryEmbeddingCache(maxsize=2)
    embedder = StubEmbedder({"what's covered?": [1.0, 0.0, 0.0, 0.0]})

    first = await cache.embed(embedder, "What's   covered?")
    second = await cache.embed(embedder, "  what's covered?  ")
    assert first == second == [1.0, 0.0, 0.0, 0.0]
    assert embedder.calls == [["what's covered?"]]
    assert (cache.stats.hits, cache.stats.misses) == (1, 1)

    # Another model never gets this model's vector.
    other = FakeEmbedder()
    await cache.embed(other, "what's covered?")
    assert cache.stats.misses == 2

    # LRU: a third entry evicts the least recently used.
    await cache.embed(embedder, "deductible")
    assert len(cache) == 2
    await cache.embed(embedder, "what's covered?")
    assert cache.stats.misses == 4


# --------------------------------------------------------------------------- the service
async def test_hybrid_ranks_the_exact_identifier_first_and_vector_does_not(
    database: Database, store: LanceDBStore
) -> None:
    await create_lexical_index(database)
    kb_id, _ = await seed_kb(database, store, HYBRID_CHUNKS)
    embedder = StubEmbedder(HYBRID_EMBEDDER_VECTORS)

    async with database.session() as session:
        service = KnowledgeService(session, store=store, embedder=embedder)
        vector = await service.search([kb_id], QUERY, 4)
        hybrid = await service.search([kb_id], QUERY, 4, mode="hybrid")

    assert [hit.text for hit in vector.hits][:3] == [DECOY_COLLISION, DECOY_THEFT, TARGET]
    assert hybrid.hits[0].text == TARGET
    top = hybrid.hits[0]
    assert (top.score_source, top.lexical_rank) == ("fused", 1)
    assert top.vector_score == pytest.approx(vector.hits[2].score)
    assert top.fused_score == pytest.approx((1 / 63 + 1 / 61) / (2 / 61))
    assert top.score == top.fused_score
    assert hybrid.warnings == []
    # The query was embedded once for both searches.
    assert len(embedder.calls) == 1


async def test_vector_mode_defaults_match_the_pre_v5_04_search(
    database: Database, store: LanceDBStore
) -> None:
    kb_id, _ = await seed_kb(database, store, HYBRID_CHUNKS, meta={"filename": "policies.md", "page": 3})
    embedder = StubEmbedder(HYBRID_EMBEDDER_VECTORS)

    async with database.session() as session:
        legacy = await search_kbs(session, store, embedder, kb_ids=[kb_id], query=QUERY, k=2)
        result = await KnowledgeService(session, store=store, embedder=embedder).search([kb_id], QUERY, 2)

    assert [hit.chunk_id for hit in legacy] == [hit.chunk_id for hit in result.hits]
    hit = result.hits[0]
    assert (hit.text, hit.filename, hit.kb_id) == (DECOY_COLLISION, "policies.md", kb_id)
    assert hit.score == pytest.approx(0.95 / (0.95**2 + 0.31**2) ** 0.5, abs=1e-4)
    assert (hit.score_source, hit.vector_score, hit.lexical_rank, hit.fused_score, hit.rerank_score) == (
        "vector",
        hit.score,
        None,
        None,
        None,
    )
    assert hit.meta == {"filename": "policies.md", "page": 3}
    assert (result.mode, result.rerank, result.min_score, result.dropped) == ("vector", "none", None, 0)


async def test_rerank_reorders_by_the_cross_encoder(database: Database, store: LanceDBStore) -> None:
    kb_id, _ = await seed_kb(database, store, HYBRID_CHUNKS)
    embedder = StubEmbedder(HYBRID_EMBEDDER_VECTORS)
    reranker = KeywordReranker("theft")

    async with database.session() as session:
        service = KnowledgeService(session, store=store, embedder=embedder, reranker=reranker)
        plain = await service.search([kb_id], QUERY, 3)
        reranked = await service.search([kb_id], QUERY, 3, rerank="local")

    assert plain.hits[0].text == DECOY_COLLISION
    assert reranked.hits[0].text == DECOY_THEFT
    top = reranked.hits[0]
    assert (top.score_source, top.score) == ("rerank", pytest.approx(sigmoid(4.0)))
    assert top.vector_score is not None
    assert all(hit.rerank_score is not None and 0.0 < hit.rerank_score < 1.0 for hit in reranked.hits)
    assert "rerank" in reranked.timings_ms
    assert reranker.calls == 1


async def test_rerank_failure_keeps_the_order_with_a_warning(database: Database, store: LanceDBStore) -> None:
    class Broken:
        model_id = "broken"

        async def rerank(self, query: str, passages: Sequence[str]) -> list[float]:
            raise RuntimeError("model file missing")

    kb_id, _ = await seed_kb(database, store, HYBRID_CHUNKS)
    async with database.session() as session:
        service = KnowledgeService(
            session, store=store, embedder=StubEmbedder(HYBRID_EMBEDDER_VECTORS), reranker=Broken()
        )
        result = await service.search([kb_id], QUERY, 2, rerank="local")

    assert result.hits[0].text == DECOY_COLLISION
    assert result.hits[0].score_source == "vector"
    assert [w.code for w in result.warnings] == ["rerank_failed"]


async def test_min_score_drops_weaker_hits_and_counts_them(database: Database, store: LanceDBStore) -> None:
    kb_id, _ = await seed_kb(database, store, HYBRID_CHUNKS)
    async with database.session() as session:
        service = KnowledgeService(session, store=store, embedder=StubEmbedder(HYBRID_EMBEDDER_VECTORS))
        result = await service.search([kb_id], QUERY, 4, min_score=0.8)

    # Cosines: 0.95, 0.90, 0.50, 0.0 (normalised vectors) -> two survive.
    assert [hit.text for hit in result.hits] == [DECOY_COLLISION, DECOY_THEFT]
    assert result.dropped == 2
    assert all(hit.score >= 0.8 for hit in result.hits)


async def test_fan_out_over_three_kbs_takes_the_slowest_not_the_sum(database: Database) -> None:
    ids = []
    async with database.session() as session:
        for name in ("A", "B", "C"):
            row = KnowledgeBase(name=name, dimension=STUB_DIMENSION, embedder_model=STUB_MODEL)
            session.add(row)
            await session.flush()
            ids.append(row.id)
    delays = dict(zip(ids, (0.05, 0.10, 0.15), strict=True))
    fake = DelayedStore(delays)

    async with database.session() as session:
        service = KnowledgeService(session, store=fake, embedder=StubEmbedder())
        result = await service.search(ids, "anything", 4)

    slowest_ms = max(delays.values()) * 1000.0
    assert result.warnings == []
    assert result.timings_ms["retrieve"] <= slowest_ms + 10.0, result.timings_ms


async def test_slow_and_failing_kbs_are_skipped_with_warnings(
    database: Database, store: LanceDBStore
) -> None:
    good_id, chunk_ids = await seed_kb(database, store, HYBRID_CHUNKS[:1])
    async with database.session() as session:
        slow = KnowledgeBase(name="Slow", dimension=STUB_DIMENSION, embedder_model=STUB_MODEL)
        failing = KnowledgeBase(name="Failing", dimension=STUB_DIMENSION, embedder_model=STUB_MODEL)
        session.add_all([slow, failing])
        await session.flush()
        slow_id, failing_id = slow.id, failing.id
    fake = DelayedStore(
        {slow_id: 5.0},
        hits={good_id: [VectorHit(id=chunk_ids[0], document_id="d", score=0.9)]},
        failing={failing_id},
    )

    async with database.session() as session:
        service = KnowledgeService(session, store=fake, embedder=StubEmbedder(), per_kb_timeout_s=0.05)
        started = time.perf_counter()
        result = await service.search([good_id, slow_id, failing_id, "no-such-kb"], "anything", 4)
        elapsed = time.perf_counter() - started

    assert [hit.chunk_id for hit in result.hits] == chunk_ids
    assert {(w.code, w.kb_id) for w in result.warnings} == {
        ("kb_timeout", slow_id),
        ("kb_error", failing_id),
        ("kb_not_found", "no-such-kb"),
    }
    assert elapsed < 1.0


async def test_a_kb_with_another_dimension_is_skipped_with_a_warning(
    database: Database, store: LanceDBStore
) -> None:
    good_id, _ = await seed_kb(database, store, HYBRID_CHUNKS)
    wide_id, _ = await seed_kb(database, store, [("Wide chunk", [1.0, 0.0, 0.0, 0.0])], name="Wide vectors")
    async with database.session() as session:
        await session.execute(update(KnowledgeBase).where(KnowledgeBase.id == wide_id).values(dimension=1536))

    async with database.session() as session:
        service = KnowledgeService(session, store=store, embedder=StubEmbedder(HYBRID_EMBEDDER_VECTORS))
        result = await service.search([wide_id, good_id], QUERY, 2)

    assert [hit.kb_id for hit in result.hits] == [good_id, good_id]
    [warning] = result.warnings
    assert (warning.code, warning.kb_id) == ("kb_embedder_mismatch", wide_id)
    assert "Wide vectors" in warning.message


async def test_hybrid_without_the_lexical_index_degrades_to_vector_with_a_warning(
    database: Database, store: LanceDBStore
) -> None:
    kb_id, _ = await seed_kb(database, store, HYBRID_CHUNKS)
    async with database.session() as session:
        service = KnowledgeService(session, store=store, embedder=StubEmbedder(HYBRID_EMBEDDER_VECTORS))
        result = await service.search([kb_id], QUERY, 2, mode="hybrid")

    assert [w.code for w in result.warnings] == ["lexical_unavailable"]
    assert result.hits[0].text == DECOY_COLLISION
    assert result.hits[0].score_source == "fused"
    assert result.hits[0].score == pytest.approx(1.0)


async def test_lexical_search_is_scoped_to_the_listed_kbs(database: Database, store: LanceDBStore) -> None:
    await create_lexical_index(database)
    first, first_chunks = await seed_kb(database, store, HYBRID_CHUNKS)
    second, _ = await seed_kb(database, store, [("Another AUTO-11111 mention.", [0.0, 0.0, 1.0, 0.0])])

    async with database.session() as session:
        scoped = await lexical_search(session, kb_ids=[first], query=QUERY, limit=20)
        both = await lexical_search(session, kb_ids=[first, second], query=QUERY, limit=20)
        nothing = await lexical_search(session, kb_ids=[first], query="--- ***", limit=20)

    assert scoped.available
    assert [hit.chunk_id for hit in scoped.hits] == [first_chunks[2]]
    assert [hit.rank for hit in both.hits] == [1, 2]
    assert nothing.hits == []


async def test_a_vector_without_its_chunk_row_is_never_returned(
    database: Database, store: LanceDBStore
) -> None:
    kb_id, _ = await seed_kb(database, store, HYBRID_CHUNKS[:2])
    await store.upsert(kb_id, [VectorRecord(id=new_id(), vector=[1.0, 0.0, 0.0, 0.0], document_id="gone")])

    async with database.session() as session:
        service = KnowledgeService(session, store=store, embedder=StubEmbedder(HYBRID_EMBEDDER_VECTORS))
        result = await service.search([kb_id], QUERY, 4)

    assert [hit.text for hit in result.hits] == [DECOY_COLLISION, DECOY_THEFT]


# --------------------------------------------------------------------------- the routes
@pytest.fixture
def route_fakes(app: FastAPI) -> Iterator[KeywordReranker]:
    reranker = KeywordReranker("theft")
    app.dependency_overrides[get_embedder] = lambda: StubEmbedder(HYBRID_EMBEDDER_VECTORS)
    app.dependency_overrides[get_reranker] = lambda: reranker
    yield reranker
    app.dependency_overrides.pop(get_embedder, None)
    app.dependency_overrides.pop(get_reranker, None)


async def test_admin_test_search_returns_every_stage_column(
    admin_client: httpx.AsyncClient, database: Database, store: LanceDBStore, route_fakes: KeywordReranker
) -> None:
    await create_lexical_index(database)
    kb_id, _ = await seed_kb(database, store, HYBRID_CHUNKS)

    response = await admin_client.post(
        f"/v1/knowledge-bases/{kb_id}/search",
        json={"query": QUERY, "k": 4, "mode": "hybrid", "rerank": "local", "min_score": 0.5},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["mode"], body["rerank"], body["min_score"]) == ("hybrid", "local", 0.5)
    top = body["hits"][0]
    assert top["text"] == DECOY_THEFT
    assert top["score_source"] == "rerank"
    assert top["vector_score"] == pytest.approx(0.90 / (0.90**2 + 0.43**2) ** 0.5, abs=1e-4)
    assert top["lexical_rank"] is None
    assert 0.0 < top["fused_score"] < 1.0
    assert top["rerank_score"] == pytest.approx(sigmoid(4.0))
    # Only the one "theft" passage clears 0.5 after the sigmoid; the rest are counted.
    assert len(body["hits"]) == 1
    assert body["dropped"] == 3
    assert set(body["timings_ms"]) >= {"embed", "retrieve", "rerank", "total"}


async def test_internal_search_accepts_the_options_and_keeps_the_old_shape(
    service_client: httpx.AsyncClient, database: Database, store: LanceDBStore, route_fakes: KeywordReranker
) -> None:
    await create_lexical_index(database)
    kb_id, _ = await seed_kb(database, store, HYBRID_CHUNKS)

    default = await service_client.post(
        "/internal/v1/kb/search", json={"kb_ids": [kb_id], "query": QUERY, "k": 2}
    )
    hybrid = await service_client.post(
        "/internal/v1/kb/search", json={"kb_ids": [kb_id], "query": QUERY, "k": 2, "mode": "hybrid"}
    )
    assert default.status_code == hybrid.status_code == 200
    old_hit = default.json()["hits"][0]
    assert {"chunk_id", "document_id", "filename", "score", "text"} <= set(old_hit)
    assert old_hit["text"] == DECOY_COLLISION
    assert hybrid.json()["hits"][0]["text"] == TARGET
    assert route_fakes.calls == 0


@pytest.mark.parametrize(
    "extra",
    [{"mode": "semantic"}, {"rerank": "cohere"}, {"min_score": 1.5}, {"min_score": -0.1}],
)
async def test_search_rejects_unknown_options(
    service_client: httpx.AsyncClient, route_fakes: KeywordReranker, extra: dict[str, object]
) -> None:
    response = await service_client.post(
        "/internal/v1/kb/search", json={"kb_ids": ["x"], "query": "q", "k": 2, **extra}
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------- the real cross-encoder (opt-in)
@pytest.mark.slow
@pytest.mark.skipif(
    not os.environ.get("LKAP_TEST_RERANK_MODEL_DIR"),
    reason="downloads the local cross-encoder (~80 MB); set LKAP_TEST_RERANK_MODEL_DIR to a cache directory",
)
async def test_local_cross_encoder_disagrees_with_cosine_on_the_fixture(
    database: Database, store: LanceDBStore, settings: Settings
) -> None:
    question = "Is a stolen car covered?"
    wrong = "Collision cover pays for damage to your own car after an accident with another vehicle."
    right = "Comprehensive cover pays out when your car is stolen or damaged by fire."
    kb_id, _ = await seed_kb(
        database, store, [(wrong, [0.95, 0.31, 0.0, 0.0]), (right, [0.60, 0.80, 0.0, 0.0])]
    )
    reranker = LocalReranker(os.environ["LKAP_TEST_RERANK_MODEL_DIR"], model_name=settings.rerank_model)
    embedder = StubEmbedder({question: [1.0, 0.0, 0.0, 0.0]})

    async with database.session() as session:
        service = KnowledgeService(session, store=store, embedder=embedder, reranker=reranker)
        plain = await service.search([kb_id], question, 2)
        reranked = await service.search([kb_id], question, 2, rerank="local")

    assert [hit.text for hit in plain.hits] == [wrong, right]
    assert [hit.text for hit in reranked.hits] == [right, wrong]
    assert reranked.hits[0].score_source == "rerank"
