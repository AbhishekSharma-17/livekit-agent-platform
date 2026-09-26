"""V5-13: `PgVectorStore` against a real Postgres with pgvector (skipped without one).

Runs only when ``LKAP_TEST_DATABASE_URL`` points at Postgres (the CI
``test-postgres`` job, whose service image is ``pgvector/pgvector:pg16``) and
the server has the ``vector`` extension; locally, with SQLite, every test here
is skipped. The ``database`` fixture builds the schema with ``create_all``, and
``lkap_api.db.models`` hooks that to create the extension and ``kb_vectors``
on Postgres. The CI job pins ``LKAP_VECTOR_STORE=lancedb`` for the older kb
tests (they seed LanceDB directly); every test here clears it, so the store is
chosen the default way: by the database.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
from conftest import postgres_url
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from test_kb_search import (
    HYBRID_CHUNKS,
    HYBRID_EMBEDDER_VECTORS,
    QUERY,
    STUB_DIMENSION,
    STUB_MODEL,
    TARGET,
    StubEmbedder,
    create_lexical_index,
)

from lkap_api.db.models import KbChunk, KbDocument, KnowledgeBase, new_id
from lkap_api.db.session import Database
from lkap_api.kb.embed import FakeEmbedder, KbEmbedderMismatchError
from lkap_api.kb.search import QUERY_CACHE
from lkap_api.kb.service import KnowledgeService
from lkap_api.kb.store import (
    PgVectorStore,
    VectorHit,
    VectorRecord,
    get_lancedb_store,
    resolve_store,
    store_capabilities,
    vector_store_kind,
)
from lkap_api.kb.stores import StoreCapabilities
from lkap_api.kb.stores.pgvector import HALFVEC_MIN_VERSION, hnsw_index_name, parse_version, partial_predicate
from lkap_api.routers.knowledge import get_embedder
from lkap_api.settings import Settings

pytestmark = pytest.mark.skipif(
    postgres_url() is None, reason="needs Postgres with pgvector (LKAP_TEST_DATABASE_URL, the CI job)"
)


@pytest.fixture
async def pg(database: Database, settings: Settings) -> AsyncIterator[Database]:
    """The per-test Postgres database, with the store left to follow it (pgvector)."""
    settings.vector_store = None
    async with database.engine.connect() as conn:
        present = await conn.scalar(text("SELECT to_regclass('kb_vectors') IS NOT NULL"))
    if not present:
        pytest.skip("the Postgres server has no pgvector extension")
    QUERY_CACHE.clear()
    yield database
    QUERY_CACHE.clear()


async def _version(database: Database) -> tuple[int, ...]:
    async with database.engine.connect() as conn:
        raw = await conn.scalar(text("SELECT extversion FROM pg_extension WHERE extname = 'vector'"))
    return parse_version(str(raw))


def _rows(kb_id: str, document_id: str, texts: list[str]) -> list[Any]:
    return [
        KbChunk(id=new_id(), kb_id=kb_id, document_id=document_id, ordinal=i, text=chunk, meta={})
        for i, chunk in enumerate(texts)
    ]


async def _kb_and_document(
    session: AsyncSession, *, dimension: int | None = STUB_DIMENSION
) -> tuple[str, str]:
    kb = KnowledgeBase(name="Harbor Lane policies", dimension=dimension, embedder_model=STUB_MODEL)
    session.add(kb)
    await session.flush()
    document = KbDocument(kb_id=kb.id, filename="policies.md", mime="text/markdown", bytes=1, status="ready")
    session.add(document)
    await session.flush()
    return kb.id, document.id


async def _count(database: Database, kb_id: str) -> int:
    async with database.engine.connect() as conn:
        value = await conn.scalar(text("SELECT count(*) FROM kb_vectors WHERE kb_id = :kb"), {"kb": kb_id})
    return int(value or 0)


async def _indexes(database: Database, kb_id: str) -> list[str]:
    async with database.engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT indexname FROM pg_indexes WHERE tablename = 'kb_vectors' AND indexname LIKE :p"),
            {"p": f"kbv_hnsw_{kb_id}_%"},
        )
    return sorted(str(name) for name in rows.scalars())


# --------------------------------------------------------------------------- selection and health
async def test_the_store_follows_the_database_and_reports_healthy(pg: Database, settings: Settings) -> None:
    assert vector_store_kind(settings) == "pgvector"
    async with pg.session() as session:
        store = resolve_store(settings, session)
        assert isinstance(store, PgVectorStore)
        assert store_capabilities(store) == StoreCapabilities(
            hybrid=True, filters=True, stores_text=False, namespaces=True
        )
        health = await store.health()
    assert health.ok and health.backend == "pgvector" and health.version


async def test_lancedb_can_still_be_forced_on_postgres(pg: Database, settings: Settings) -> None:
    settings.vector_store = "lancedb"
    assert resolve_store(settings) is get_lancedb_store(settings.data_dir)


# --------------------------------------------------------------------------- one transaction with the chunks
async def test_upsert_query_delete_round_trip_inside_the_chunk_transaction(pg: Database) -> None:
    async with pg.session() as session:
        kb_id, document_id = await _kb_and_document(session)
        rows = _rows(kb_id, document_id, ["east", "north"])
        store = PgVectorStore(session)
        # Vectors first, chunk rows after: the ingest order (the foreign key is deferred).
        await store.upsert(
            kb_id,
            [
                VectorRecord(id=rows[0].id, vector=[1.0, 0.0, 0.0, 0.0], document_id=document_id),
                VectorRecord(id=rows[1].id, vector=[0.0, 1.0, 0.0, 0.0], document_id=document_id),
            ],
        )
        session.add_all(rows)
        await session.flush()
        hits = await store.query(kb_id, [0.9, 0.1, 0.0, 0.0], 5)
        assert [hit.id for hit in hits] == [rows[0].id, rows[1].id]
        assert hits[0].document_id == document_id
        assert hits[0].score == pytest.approx(0.9 / (0.9**2 + 0.1**2) ** 0.5, abs=1e-5)
        # An upsert replaces by chunk id.
        await store.upsert(
            kb_id, [VectorRecord(id=rows[0].id, vector=[0.0, 0.0, 1.0, 0.0], document_id=document_id)]
        )
        assert (await store.query(kb_id, [0.0, 0.0, 1.0, 0.0], 1))[0].id == rows[0].id
    assert await _count(pg, kb_id) == 2

    async with pg.session() as session:
        await PgVectorStore(session).delete_document(kb_id, document_id)
    assert await _count(pg, kb_id) == 0


async def test_an_aborted_ingest_leaves_no_vectors(pg: Database) -> None:
    async with pg.session() as session:
        kb_id, document_id = await _kb_and_document(session)
    with pytest.raises(RuntimeError, match="ingest failed"):
        async with pg.session() as session:
            rows = _rows(kb_id, document_id, ["east"])
            await PgVectorStore(session).upsert(
                kb_id, [VectorRecord(id=rows[0].id, vector=[1.0, 0.0, 0.0, 0.0], document_id=document_id)]
            )
            session.add_all(rows)
            await session.flush()
            raise RuntimeError("ingest failed")
    assert await _count(pg, kb_id) == 0


async def test_deleting_chunk_rows_cascades_to_their_vectors(pg: Database) -> None:
    async with pg.session() as session:
        kb_id, document_id = await _kb_and_document(session)
        rows = _rows(kb_id, document_id, ["east"])
        session.add_all(rows)
        await session.flush()
        await PgVectorStore(session).upsert(
            kb_id, [VectorRecord(id=rows[0].id, vector=[1.0, 0.0, 0.0, 0.0], document_id=document_id)]
        )
    async with pg.session() as session:
        await session.execute(text("DELETE FROM kb_documents WHERE id = :d"), {"d": document_id})
    assert await _count(pg, kb_id) == 0


async def test_a_vector_without_a_chunk_row_fails_at_commit(pg: Database) -> None:
    async with pg.session() as session:
        kb_id, document_id = await _kb_and_document(session)
    with pytest.raises(Exception, match="kb_vectors"):
        async with pg.session() as session:
            await PgVectorStore(session).upsert(
                kb_id, [VectorRecord(id=new_id(), vector=[1.0, 0.0, 0.0, 0.0], document_id=document_id)]
            )
    assert await _count(pg, kb_id) == 0


# --------------------------------------------------------------------------- widths and indexes
async def test_another_width_is_the_422_embedder_mismatch(pg: Database) -> None:
    async with pg.session() as session:
        kb_id, document_id = await _kb_and_document(session, dimension=None)
        rows = _rows(kb_id, document_id, ["east"])
        session.add_all(rows)
        store = PgVectorStore(session)
        await store.upsert(
            kb_id, [VectorRecord(id=rows[0].id, vector=[1.0, 0.0, 0.0, 0.0], document_id=document_id)]
        )
        with pytest.raises(KbEmbedderMismatchError) as query_error:
            await store.query(kb_id, [1.0, 0.0, 0.0], 3)
        with pytest.raises(KbEmbedderMismatchError):
            await store.upsert(
                kb_id, [VectorRecord(id=rows[0].id, vector=[1.0] * 8, document_id=document_id)]
            )
        # The failed write was rolled back to its savepoint; the session is still usable.
        assert len(await store.query(kb_id, [1.0, 0.0, 0.0, 0.0], 3)) == 1

        # The search pipeline turns it into a warning for that knowledge base (the row records no width).
        response = await KnowledgeService(
            session, store=store, embedder=StubEmbedder({"east": [1.0, 0.0, 0.0]})
        ).search([kb_id], "east", 3)
    assert query_error.value.status_code == 422
    assert query_error.value.code == "kb_embedder_mismatch"
    assert response.hits == []
    assert [warning.code for warning in response.warnings] == ["kb_embedder_mismatch"]


async def test_each_kb_gets_its_own_hnsw_index_replaced_on_a_width_change(pg: Database) -> None:
    async with pg.session() as session:
        kb_id, document_id = await _kb_and_document(session)
        rows = _rows(kb_id, document_id, ["east"])
        session.add_all(rows)
        await PgVectorStore(session).upsert(
            kb_id, [VectorRecord(id=rows[0].id, vector=[1.0, 0.0, 0.0, 0.0], document_id=document_id)]
        )
    assert await _indexes(pg, kb_id) == [hnsw_index_name(kb_id, 4)]

    async with pg.session() as session:
        store = PgVectorStore(session)
        await store.delete_kb(kb_id)
        await store.ensure_namespace(kb_id, 8)
    assert await _indexes(pg, kb_id) == [hnsw_index_name(kb_id, 8)]
    assert await _count(pg, kb_id) == 0

    async with pg.session() as session:
        await PgVectorStore(session).delete_kb(kb_id)
    assert await _indexes(pg, kb_id) == []


async def test_the_query_uses_the_partial_hnsw_index(pg: Database) -> None:
    async with pg.session() as session:
        kb_id, document_id = await _kb_and_document(session)
        rows = _rows(kb_id, document_id, ["east"])
        session.add_all(rows)
        await PgVectorStore(session).upsert(
            kb_id, [VectorRecord(id=rows[0].id, vector=[1.0, 0.0, 0.0, 0.0], document_id=document_id)]
        )
    # The store inlines the (validated) knowledge base id: a bound one cannot prove the partial predicate.
    async with pg.engine.connect() as conn:
        await conn.execute(text("SET enable_seqscan = off"))
        plan = await conn.execute(
            text(
                f"EXPLAIN SELECT chunk_id FROM kb_vectors WHERE {partial_predicate(kb_id, 4)} "
                "ORDER BY embedding::vector(4) <=> CAST('[1,0,0,0]' AS vector(4)) LIMIT 5"
            )
        )
        inlined_plan = "\n".join(str(row[0]) for row in plan.all())
    assert hnsw_index_name(kb_id, 4) in inlined_plan


async def test_above_2000_dimensions_the_index_is_halfvec(pg: Database) -> None:
    if await _version(pg) < HALFVEC_MIN_VERSION:
        pytest.skip("halfvec needs pgvector >= 0.7")
    width = 2048
    async with pg.session() as session:
        kb_id, document_id = await _kb_and_document(session, dimension=width)
        rows = _rows(kb_id, document_id, ["east", "north"])
        session.add_all(rows)
        store = PgVectorStore(session)
        east, north = [0.0] * width, [0.0] * width
        east[0], north[1] = 1.0, 1.0
        await store.upsert(
            kb_id,
            [
                VectorRecord(id=rows[0].id, vector=east, document_id=document_id),
                VectorRecord(id=rows[1].id, vector=north, document_id=document_id),
            ],
        )
        hits = await store.query(kb_id, east, 2)
    assert [hit.id for hit in hits] == [rows[0].id, rows[1].id]
    async with pg.engine.connect() as conn:
        definition = await conn.scalar(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = :n"), {"n": hnsw_index_name(kb_id, width)}
        )
    assert "halfvec(2048)" in str(definition)


# --------------------------------------------------------------------------- filters and hybrid
async def test_filters_restrict_the_query_to_documents(pg: Database) -> None:
    async with pg.session() as session:
        kb_id, first = await _kb_and_document(session)
        second = KbDocument(kb_id=kb_id, filename="b.md", mime="text/markdown", bytes=1, status="ready")
        session.add(second)
        await session.flush()
        rows = _rows(kb_id, first, ["east"]) + _rows(kb_id, second.id, ["north"])
        session.add_all(rows)
        store = PgVectorStore(session)
        await store.upsert(
            kb_id, [VectorRecord(id=rows[0].id, vector=[1.0, 0.0, 0.0, 0.0], document_id=first)]
        )
        await store.upsert(
            kb_id, [VectorRecord(id=rows[1].id, vector=[0.9, 0.1, 0.0, 0.0], document_id=second.id)]
        )
        only_second = await store.query(kb_id, [1.0, 0.0, 0.0, 0.0], 5, filters={"document_id": second.id})
        both = await store.query(kb_id, [1.0, 0.0, 0.0, 0.0], 5, filters={"document_id": [first, second.id]})
        with pytest.raises(ValueError, match="unsupported"):
            await store.query(kb_id, [1.0, 0.0, 0.0, 0.0], 5, filters={"page": 1})
    assert [hit.id for hit in only_second] == [rows[1].id]
    assert [hit.id for hit in both] == [rows[0].id, rows[1].id]


class _DenseOnly:
    """A pgvector store with its hybrid capability hidden: the service fuses (the SQLite path)."""

    capabilities = StoreCapabilities(filters=True, namespaces=True)

    def __init__(self, inner: PgVectorStore) -> None:
        self.inner = inner

    async def query(self, kb_id: str, vector: list[float], k: int) -> list[VectorHit]:
        return await self.inner.query(kb_id, vector, k)


async def test_native_hybrid_puts_the_identifier_first_like_the_service_fusion(pg: Database) -> None:
    await create_lexical_index(pg)
    async with pg.session() as session:
        kb_id, document_id = await _kb_and_document(session)
        rows = _rows(kb_id, document_id, [chunk for chunk, _ in HYBRID_CHUNKS])
        session.add_all(rows)
        await session.flush()
        store = PgVectorStore(session)
        await store.upsert(
            kb_id,
            [
                VectorRecord(id=row.id, vector=vector, document_id=document_id)
                for row, (_, vector) in zip(rows, HYBRID_CHUNKS, strict=True)
            ],
        )
    embedder = StubEmbedder(HYBRID_EMBEDDER_VECTORS)
    async with pg.session() as session:
        native = await KnowledgeService(session, store=PgVectorStore(session), embedder=embedder).search(
            [kb_id], QUERY, 4, mode="hybrid"
        )
    QUERY_CACHE.clear()
    async with pg.session() as session:
        fused_by_service = await KnowledgeService(
            session,
            store=_DenseOnly(PgVectorStore(session)),  # type: ignore[arg-type]
            embedder=embedder,
        ).search([kb_id], QUERY, 4, mode="hybrid")
        vector_only = await KnowledgeService(session, store=PgVectorStore(session), embedder=embedder).search(
            [kb_id], QUERY, 4
        )

    assert native.hits[0].text == TARGET
    assert native.hits[0].score_source == "fused"
    assert native.hits[0].lexical_rank == 1
    assert [hit.chunk_id for hit in native.hits] == [hit.chunk_id for hit in fused_by_service.hits]
    assert [hit.fused_score for hit in native.hits] == pytest.approx(
        [hit.fused_score for hit in fused_by_service.hits]
    )
    assert vector_only.hits[0].text != TARGET
    assert native.warnings == []


# --------------------------------------------------------------------------- kb_reindex on pgvector
async def test_reindex_at_a_new_width_swaps_vectors_and_index_in_one_transaction(
    pg: Database, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test_kb_reindex import WideFakeEmbedder, _ingested_kb, _search_ids, _use_embedder

    from lkap_api.jobs.deps import build_jobs_service
    from lkap_api.jobs.handlers import load_all_handlers
    from lkap_api.kb import jobs as kb_jobs
    from lkap_api.vault import Vault

    kb_id = await _ingested_kb(pg, settings, FakeEmbedder())
    assert await _indexes(pg, kb_id) == [hnsw_index_name(kb_id, FakeEmbedder.dimension)]
    chunks = await _count(pg, kb_id)

    load_all_handlers()
    _use_embedder(monkeypatch, WideFakeEmbedder())
    vault = Vault(settings.master_key)
    jobs = build_jobs_service(pg, settings, vault)
    try:
        job_id = await kb_jobs.enqueue_kb_reindex(jobs, kb_id)
    finally:
        await jobs.aclose()

    async with pg.session() as session:
        kb = await session.get(KnowledgeBase, kb_id)
        job_row = await session.execute(text("SELECT status FROM jobs WHERE id = :id"), {"id": job_id})
    assert job_row.scalar() == "done"
    assert kb is not None and (kb.dimension, kb.embedder_model) == (48, WideFakeEmbedder.model_id)
    assert await _count(pg, kb_id) == chunks
    assert await _indexes(pg, kb_id) == [hnsw_index_name(kb_id, 48)]
    assert all(await _search_ids(pg, settings, kb_id, WideFakeEmbedder()))


# --------------------------------------------------------------------------- through the routes
POLICY = (
    "# Water damage\n\nA burst pipe is covered when the water loss was sudden and accidental.\n\n"
    "# Theft\n\nA police report number is required before a theft claim moves on."
)


async def _fake_resolve_embedder(*args: object, **kwargs: object) -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def fake_embedder(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    app.dependency_overrides[get_embedder] = lambda: FakeEmbedder()
    monkeypatch.setattr("lkap_api.kb.ingest.resolve_embedder", _fake_resolve_embedder)
    yield
    app.dependency_overrides.pop(get_embedder, None)


async def test_upload_search_and_delete_go_through_pgvector(
    pg: Database,
    settings: Settings,
    admin_client: httpx.AsyncClient,
    service_client: httpx.AsyncClient,
    fake_embedder: None,
) -> None:
    kb = (await admin_client.post("/v1/knowledge-bases", json={"name": "Harbor Lane policies"})).json()
    upload = await admin_client.post(
        f"/v1/knowledge-bases/{kb['id']}/documents",
        files={"file": ("policy.md", POLICY.encode(), "text/markdown")},
    )
    assert upload.status_code == 202, upload.text
    listing = (await admin_client.get(f"/v1/knowledge-bases/{kb['id']}/documents")).json()
    document = next(item for item in listing["items"] if item["id"] == upload.json()["id"])
    assert document["status"] == "ready", document
    assert await _count(pg, kb["id"]) == document["chunk_count"] > 0
    lancedb_tables = get_lancedb_store(settings.data_dir)._connect().table_names()
    assert f"kb_{kb['id']}" not in lancedb_tables

    search = await service_client.post(
        "/internal/v1/kb/search", json={"kb_ids": [kb["id"]], "query": "police report theft", "k": 2}
    )
    assert search.status_code == 200, search.text
    assert "police report" in search.json()["hits"][0]["text"]

    response = await admin_client.delete(f"/v1/knowledge-bases/{kb['id']}/documents/{document['id']}")
    assert response.status_code == 204
    assert await _count(pg, kb["id"]) == 0
    assert (await admin_client.delete(f"/v1/knowledge-bases/{kb['id']}")).status_code == 204
    assert await _indexes(pg, kb["id"]) == []
