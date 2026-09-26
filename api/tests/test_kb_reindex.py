"""V5-13: the `kb_reindex` job and its CLI, store selection, and LanceDB's v2 surface.

Everything here runs on SQLite with LanceDB and the ``FakeEmbedder`` except
the last test, which moves a knowledge base from LanceDB to pgvector and needs
the CI Postgres job (skipped without ``LKAP_TEST_DATABASE_URL``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import httpx
import pytest
from conftest import postgres_url
from lkap_contracts.api_models import KbHit
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from lkap_api.db.models import Job, KbChunk, KbDocument, KnowledgeBase, new_id
from lkap_api.db.session import Database
from lkap_api.jobs.context import JobContext
from lkap_api.jobs.deps import build_jobs_service
from lkap_api.jobs.handlers import load_all_handlers
from lkap_api.jobs.kinds import KB_REINDEX
from lkap_api.kb import jobs as kb_jobs
from lkap_api.kb.embed import FakeEmbedder
from lkap_api.kb.external import ExternalRetriever, RetrieverInfo
from lkap_api.kb.ingest import ingest_into_session
from lkap_api.kb.search import QUERY_CACHE
from lkap_api.kb.service import KnowledgeService
from lkap_api.kb.store import (
    LanceDBStore,
    PgVectorStore,
    VectorHit,
    VectorRecord,
    VectorStoreConfigError,
    get_lancedb_store,
    resolve_store,
    store_capabilities,
    vector_store_kind,
)
from lkap_api.kb.stores import StoreCapabilities
from lkap_api.settings import Settings, get_settings
from lkap_api.vault import Vault

POLICY = (
    "# Water damage\n\n## Burst pipes\n\nA burst pipe is covered when the water loss was sudden and "
    "accidental.\n\n# Theft\n\nA police report number is required before a theft claim moves on.\n\n"
    "# Towing\n\nRoadside towing is covered up to 50 miles from the breakdown."
)
QUESTIONS = ["Is a burst pipe covered?", "What does a theft claim need?", "How far is towing covered?"]


class WideFakeEmbedder(FakeEmbedder):
    """The hashed bag-of-tokens embedder at another width and model: an embedder change."""

    dimension = 48
    model_id = "fake-hashed-tokens-48"


class RecordingEmbedder(FakeEmbedder):
    """Records every text it embeds."""

    def __init__(self) -> None:
        self.texts: list[str] = []

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.texts.extend(texts)
        return await super().embed(texts)


@pytest.fixture(autouse=True)
def _clean_query_cache() -> None:
    QUERY_CACHE.clear()


@pytest.fixture
async def job_context(database: Database, settings: Settings) -> AsyncIterator[JobContext]:
    load_all_handlers()
    vault = Vault(settings.master_key)
    jobs = build_jobs_service(database, settings, vault)
    async with httpx.AsyncClient() as http:
        yield JobContext(database=database, settings=settings, vault=vault, http=http, jobs=jobs)
    await jobs.aclose()


def _use_embedder(monkeypatch: pytest.MonkeyPatch, embedder: FakeEmbedder) -> None:
    async def resolve(*args: object, **kwargs: object) -> FakeEmbedder:
        return embedder

    monkeypatch.setattr("lkap_api.kb.jobs.resolve_embedder", resolve)


async def _ingested_kb(database: Database, settings: Settings, embedder: FakeEmbedder) -> str:
    """One knowledge base with the policy document ingested through the real ingest path."""
    async with database.session() as session:
        kb = KnowledgeBase(name="Harbor Lane policies")
        session.add(kb)
        await session.flush()
        document = KbDocument(
            kb_id=kb.id, filename="policy.md", mime="text/markdown", bytes=1, status="pending"
        )
        session.add(document)
        await session.flush()
        kb_id, document_id = kb.id, document.id
    async with database.session() as session:
        outcome = await ingest_into_session(
            session,
            store=resolve_store(settings, session),
            embedder=embedder,
            kb_id=kb_id,
            document_id=document_id,
            filename="policy.md",
            mime="text/markdown",
            data=POLICY.encode(),
        )
    assert outcome.status == "ready" and outcome.chunk_count >= 3
    return kb_id


async def _search_ids(
    database: Database, settings: Settings, kb_id: str, embedder: FakeEmbedder, *, mode: str = "vector"
) -> list[list[str]]:
    results: list[list[str]] = []
    async with database.session() as session:
        service = KnowledgeService(session, store=resolve_store(settings, session), embedder=embedder)
        for question in QUESTIONS:
            response = await service.search([kb_id], question, 3, mode=mode)  # type: ignore[arg-type]
            assert response.warnings == [], response.warnings
            results.append([hit.chunk_id for hit in response.hits])
    return results


# --------------------------------------------------------------------------- store selection
SQLITE_URL = "sqlite+aiosqlite:///./data/lkap.db"
POSTGRES_URL = "postgresql+asyncpg://lkap@localhost:5432/lkap"


@pytest.mark.parametrize(
    ("url", "forced", "expected"),
    [
        (SQLITE_URL, None, "lancedb"),
        (SQLITE_URL, "lancedb", "lancedb"),
        (POSTGRES_URL, None, "pgvector"),
        (POSTGRES_URL, "pgvector", "pgvector"),
        (POSTGRES_URL, "lancedb", "lancedb"),
    ],
)
async def test_vector_store_kind_follows_the_database_unless_forced(
    settings: Settings, url: str, forced: str | None, expected: str
) -> None:
    settings.database_url = url
    settings.vector_store = forced  # type: ignore[assignment]
    assert vector_store_kind(settings) == expected

    engine = create_async_engine(url)  # never connected: pgvector only binds the session
    try:
        async with AsyncSession(engine) as session:
            store = resolve_store(settings, session)
    finally:
        await engine.dispose()
    assert isinstance(store, PgVectorStore if expected == "pgvector" else LanceDBStore)


async def test_pgvector_on_sqlite_is_refused(settings: Settings) -> None:
    settings.database_url = SQLITE_URL
    settings.vector_store = "pgvector"
    with pytest.raises(VectorStoreConfigError, match="needs a Postgres") as error:
        resolve_store(settings)
    assert error.value.status_code == 422


async def test_pgvector_needs_the_callers_session(settings: Settings) -> None:
    settings.database_url = POSTGRES_URL
    settings.vector_store = None
    with pytest.raises(ValueError, match="caller's session"):
        resolve_store(settings)


@pytest.mark.parametrize(
    ("raw", "parsed"), [("", None), ("  ", None), ("LanceDB", "lancedb"), ("pgvector", "pgvector")]
)
def test_the_vector_store_setting_reads_the_environment(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, raw: str, parsed: str | None
) -> None:
    monkeypatch.setenv("LKAP_VECTOR_STORE", raw)
    get_settings.cache_clear()
    assert get_settings().vector_store == parsed


def test_an_unknown_vector_store_is_a_settings_error(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LKAP_VECTOR_STORE", "qdrant")
    get_settings.cache_clear()
    with pytest.raises(ValidationError):
        get_settings()


# --------------------------------------------------------------------------- LanceDB's v2 surface
async def test_lancedb_declares_its_capabilities_and_is_healthy(tmp_path: Path) -> None:
    store = LanceDBStore(tmp_path)
    assert store_capabilities(store) == StoreCapabilities(
        hybrid=False, filters=True, stores_text=False, namespaces=True
    )
    await store.ensure_namespace("kb1", 3)  # a no-op: the table appears with its first upsert
    assert await store.query("kb1", [1.0, 0.0, 0.0], 3) == []
    health = await store.health()
    assert health.ok and health.backend == "lancedb"
    with pytest.raises(ValueError, match="unsafe"):
        await store.ensure_namespace("kb'; drop", 3)


async def test_lancedb_filters_by_document(tmp_path: Path) -> None:
    store = LanceDBStore(tmp_path)
    await store.upsert(
        "kb1",
        [
            VectorRecord(id="c1", vector=[1.0, 0.0], document_id="d1"),
            VectorRecord(id="c2", vector=[0.9, 0.1], document_id="d2"),
            VectorRecord(id="c3", vector=[0.0, 1.0], document_id="d3"),
        ],
    )
    only_d2 = await store.query("kb1", [1.0, 0.0], 5, filters={"document_id": "d2"})
    d1_and_d3 = await store.query("kb1", [1.0, 0.0], 5, filters={"document_id": ["d3", "d1"]})
    assert [hit.id for hit in only_d2] == ["c2"]
    assert [hit.id for hit in d1_and_d3] == ["c1", "c3"]
    # `text` is accepted and ignored: LanceDB is not hybrid-capable.
    assert [hit.id for hit in await store.query("kb1", [1.0, 0.0], 1, text="anything")] == ["c1"]
    for bad in ({"page": 1}, {"document_id": []}, {"document_id": "d'1"}, {"document_id": [1]}):
        with pytest.raises(ValueError):
            await store.query("kb1", [1.0, 0.0], 5, filters=bad)


class _FusingStore:
    """A hybrid-capable store double: returns one fused list with tied scores, in fuse_rrf's order."""

    capabilities = StoreCapabilities(hybrid=True)

    def __init__(self, hits: list[VectorHit]) -> None:
        self.hits = hits
        self.texts: list[str | None] = []

    async def query(
        self, kb_id: str, vector: list[float], k: int, *, text: str | None = None
    ) -> list[VectorHit]:
        self.texts.append(text)
        return self.hits if text is not None else [hit for hit in self.hits if hit.vector_score is not None]


async def test_the_service_keeps_a_hybrid_stores_fused_order_and_skips_its_own_keyword_stage(
    database: Database,
) -> None:
    async with database.session() as session:
        kb = KnowledgeBase(
            name="Tied", dimension=FakeEmbedder.dimension, embedder_model=FakeEmbedder.model_id
        )
        session.add(kb)
        await session.flush()
        document = KbDocument(kb_id=kb.id, filename="t.md", mime="text/markdown", bytes=1, status="ready")
        session.add(document)
        await session.flush()
        # Ids sort the other way round, so an id tie-break would swap the first two.
        ids = ["zz" + new_id()[2:], "aa" + new_id()[2:], "mm" + new_id()[2:]]
        session.add_all(
            KbChunk(id=chunk_id, kb_id=kb.id, document_id=document.id, ordinal=i, text=f"chunk {i}", meta={})
            for i, chunk_id in enumerate(ids)
        )
        kb_id, document_id = kb.id, document.id
    store = _FusingStore(
        [
            VectorHit(
                id=ids[0], document_id=document_id, score=0.99, vector_score=0.8, lexical_rank=2, fused=True
            ),
            VectorHit(
                id=ids[1], document_id=document_id, score=0.99, vector_score=0.7, lexical_rank=1, fused=True
            ),
            VectorHit(
                id=ids[2], document_id=document_id, score=0.48, vector_score=None, lexical_rank=3, fused=True
            ),
        ]
    )
    async with database.session() as session:
        response = await KnowledgeService(session, store=store, embedder=FakeEmbedder()).search(  # type: ignore[arg-type]
            [kb_id], "chunk", 3, mode="hybrid"
        )
    assert store.texts == ["chunk"]
    assert [hit.chunk_id for hit in response.hits] == ids
    assert [hit.score_source for hit in response.hits] == ["fused"] * 3
    assert [(hit.vector_score, hit.lexical_rank) for hit in response.hits] == [(0.8, 2), (0.7, 1), (None, 3)]
    assert response.warnings == []


# --------------------------------------------------------------------------- kb_reindex on LanceDB
async def test_reindex_follows_an_embedder_change_and_records_it(
    database: Database, settings: Settings, job_context: JobContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.vector_store = "lancedb"  # also on the Postgres job: this is the LanceDB path
    kb_id = await _ingested_kb(database, settings, FakeEmbedder())
    async with database.session() as session:
        kb = await session.get(KnowledgeBase, kb_id)
        assert kb is not None and (kb.dimension, kb.embedder_model) == (32, FakeEmbedder.model_id)
        chunk_count = len((await session.execute(select(KbChunk.id).where(KbChunk.kb_id == kb_id))).all())

    # The new embedder is refused by the old knowledge base...
    async with database.session() as session:
        before = await KnowledgeService(
            session, store=get_lancedb_store(settings.data_dir), embedder=WideFakeEmbedder()
        ).search([kb_id], QUESTIONS[0], 3)
    assert [warning.code for warning in before.warnings] == ["kb_embedder_mismatch"]

    # ...until it is re-indexed with it.
    _use_embedder(monkeypatch, WideFakeEmbedder())
    job_id = await kb_jobs.enqueue_kb_reindex(job_context.jobs, kb_id)

    async with database.session() as session:
        kb = await session.get(KnowledgeBase, kb_id)
        job = await session.get(Job, job_id)
    assert kb is not None and (kb.dimension, kb.embedder_model) == (48, WideFakeEmbedder.model_id)
    assert job is not None and job.status == "done", job.last_error if job else None
    assert job.payload["progress"] == {"done": chunk_count, "total": chunk_count}
    assert job.payload["result"] == {
        "chunks": chunk_count,
        "dimension": 48,
        "embedder_model": WideFakeEmbedder.model_id,
        "store": "lancedb",
    }
    table = get_lancedb_store(settings.data_dir)._connect().open_table(f"kb_{kb_id}")
    assert table.count_rows() == chunk_count
    after = await _search_ids(database, settings, kb_id, WideFakeEmbedder())
    assert all(after)


async def test_reindex_embeds_the_heading_path_with_the_chunk_text(
    database: Database, settings: Settings, job_context: JobContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    kb_id = await _ingested_kb(database, settings, FakeEmbedder())
    recorder = RecordingEmbedder()
    _use_embedder(monkeypatch, recorder)
    await kb_jobs.enqueue_kb_reindex(job_context.jobs, kb_id)
    # Exactly what ingest embeds (`Chunk.embed_text`): the heading path, a blank line, the chunk.
    assert any(
        text.startswith("Water damage › Burst pipes\n\n## Burst pipes\n\nA burst pipe")
        for text in recorder.texts
    )


async def test_reindex_keeps_the_hits_of_an_unchanged_embedder(
    database: Database, settings: Settings, job_context: JobContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    kb_id = await _ingested_kb(database, settings, FakeEmbedder())
    before = await _search_ids(database, settings, kb_id, FakeEmbedder())
    _use_embedder(monkeypatch, FakeEmbedder())
    await kb_jobs.enqueue_kb_reindex(job_context.jobs, kb_id)
    QUERY_CACHE.clear()
    assert await _search_ids(database, settings, kb_id, FakeEmbedder()) == before


async def test_reindex_of_a_knowledge_base_without_chunks_records_the_embedder(
    database: Database, job_context: JobContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with database.session() as session:
        kb = KnowledgeBase(name="Empty")
        session.add(kb)
        await session.flush()
        kb_id = kb.id
    _use_embedder(monkeypatch, WideFakeEmbedder())
    await kb_jobs.run_kb_reindex_job(job_context, kb_jobs.kb_reindex_payload(kb_id))
    async with database.session() as session:
        row = await session.get(KnowledgeBase, kb_id)
    assert row is not None and (row.dimension, row.embedder_model) == (48, WideFakeEmbedder.model_id)


async def test_reindex_of_a_missing_knowledge_base_fails_the_job(
    job_context: JobContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_embedder(monkeypatch, FakeEmbedder())
    with pytest.raises(LookupError, match="no longer exists"):
        await kb_jobs.run_kb_reindex_job(job_context, kb_jobs.kb_reindex_payload("missing"))


def test_kb_reindex_is_registered_for_both_processes() -> None:
    assert KB_REINDEX in load_all_handlers()


# --------------------------------------------------------------------------- the CLI
async def test_cli_reindexes_every_knowledge_base(
    database: Database,
    settings: Settings,
    job_context: JobContext,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings.vector_store = "lancedb"
    first = await _ingested_kb(database, settings, FakeEmbedder())
    second = await _ingested_kb(database, settings, FakeEmbedder())
    _use_embedder(monkeypatch, WideFakeEmbedder())

    code = await kb_jobs.reindex_command(None, settings=settings, database=database, jobs=job_context.jobs)

    assert code == 0
    out = capsys.readouterr().out
    assert f"kb_reindex {first}" in out and f"kb_reindex {second}" in out
    assert "2 knowledge base(s) queued" in out and "store lancedb" in out
    async with database.session() as session:
        statuses = (await session.execute(select(Job.status).where(Job.kind == KB_REINDEX))).scalars().all()
        widths = (
            await session.execute(
                select(KnowledgeBase.dimension).where(KnowledgeBase.id.in_([first, second]))
            )
        ).scalars()
    assert sorted(statuses) == ["done", "done"]
    assert set(widths) == {48}


async def test_cli_refuses_an_unknown_id_and_needs_a_target(
    database: Database, settings: Settings, job_context: JobContext, capsys: pytest.CaptureFixture[str]
) -> None:
    code = await kb_jobs.reindex_command(
        ["nope"], settings=settings, database=database, jobs=job_context.jobs
    )
    assert code == 1
    assert "unknown knowledge base id(s): nope" in capsys.readouterr().err
    assert kb_jobs.main(["reindex"]) == 2
    assert kb_jobs.main(["reindex", "--all", "--kb", "x"]) == 2


# --------------------------------------------------------------------------- LanceDB -> pgvector (Postgres)
@pytest.mark.skipif(postgres_url() is None, reason="needs Postgres with pgvector (LKAP_TEST_DATABASE_URL)")
@pytest.mark.parametrize("mode", ["vector", "hybrid"])
async def test_reindex_moves_a_knowledge_base_from_lancedb_to_pgvector_with_equal_hits(
    database: Database,
    settings: Settings,
    job_context: JobContext,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    from test_kb_search import create_lexical_index

    async with database.engine.connect() as conn:
        if not await conn.scalar(text("SELECT to_regclass('kb_vectors') IS NOT NULL")):
            pytest.skip("the Postgres server has no pgvector extension")
    await create_lexical_index(database)

    settings.vector_store = "lancedb"  # the deployment before V5-13
    kb_id = await _ingested_kb(database, settings, FakeEmbedder())
    on_lancedb = await _search_ids(database, settings, kb_id, FakeEmbedder(), mode=mode)

    settings.vector_store = None  # V5-13: the store follows the database
    assert vector_store_kind(settings) == "pgvector"
    _use_embedder(monkeypatch, FakeEmbedder())
    job_id = await kb_jobs.enqueue_kb_reindex(job_context.jobs, kb_id)
    QUERY_CACHE.clear()
    on_pgvector = await _search_ids(database, settings, kb_id, FakeEmbedder(), mode=mode)

    async with database.session() as session:
        job = await session.get(Job, job_id)
        count = await session.scalar(text("SELECT count(*) FROM kb_vectors WHERE kb_id = :kb"), {"kb": kb_id})
    assert job is not None and job.status == "done" and job.payload["result"]["store"] == "pgvector"
    assert count == job.payload["result"]["chunks"]
    assert all(on_lancedb)
    assert on_pgvector == on_lancedb


# --------------------------------------------------------------------------- the ExternalRetriever Protocol
class _StaticRetriever:
    async def search(self, query: str, *, k: int, filters: object = None) -> list[KbHit]:
        return [KbHit(chunk_id="c1", document_id="d1", filename="faq.md", score=0.5, text=query)][:k]

    async def describe(self) -> RetrieverInfo:
        return RetrieverInfo(kind="static", document_count=1, sources=["faq"])


async def test_an_external_retriever_satisfies_the_protocol() -> None:
    retriever = _StaticRetriever()
    assert isinstance(retriever, ExternalRetriever)
    assert [hit.text for hit in await retriever.search("hello", k=1)] == ["hello"]
    assert (await retriever.describe()).document_count == 1
    assert not isinstance(object(), ExternalRetriever)
