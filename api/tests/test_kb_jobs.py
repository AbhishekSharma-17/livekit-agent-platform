"""V5-04 single writer (D-V5-12): deletes go through the ``kb_delete`` job; ingest ends with ``optimize``.

The delete routes remove the SQL rows (and with them the lexical index rows,
through the ``v5_001`` triggers) in the request and enqueue ``kb_delete``,
which removes the vectors. With the ``inline`` backend that job has run by the
time the response returns; with ``arq`` (simulated here by recording the
enqueue instead of running it) the api never writes the vector store.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select, text
from test_kb_search import create_lexical_index

from lkap_api.db.models import Job, KbChunk, KbDocument, KnowledgeBase
from lkap_api.db.session import Database
from lkap_api.jobs.context import JobContext
from lkap_api.jobs.deps import build_jobs_service
from lkap_api.jobs.kinds import KB_DELETE
from lkap_api.jobs.registry import get_handler
from lkap_api.jobs.service import JobsService
from lkap_api.kb import store as store_module
from lkap_api.kb.embed import FakeEmbedder
from lkap_api.kb.jobs import kb_delete_payload, run_kb_delete_job
from lkap_api.kb.store import LanceDBStore, VectorRecord, get_lancedb_store
from lkap_api.routers.knowledge import get_embedder
from lkap_api.settings import Settings
from lkap_api.vault import Vault


async def _fake_resolve_embedder(*args: object, **kwargs: object) -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture(autouse=True)
def _fake_embedder(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    app.dependency_overrides[get_embedder] = lambda: FakeEmbedder()
    monkeypatch.setattr("lkap_api.kb.ingest.resolve_embedder", _fake_resolve_embedder)
    yield
    app.dependency_overrides.pop(get_embedder, None)


@pytest.fixture
def store(settings: Settings) -> LanceDBStore:
    return get_lancedb_store(settings.data_dir)


@pytest.fixture
async def job_context(database: Database, settings: Settings) -> AsyncIterator[JobContext]:
    vault = Vault(settings.master_key)
    jobs = build_jobs_service(database, settings, vault)
    async with httpx.AsyncClient() as http:
        yield JobContext(database=database, settings=settings, vault=vault, http=http, jobs=jobs)
    await jobs.aclose()


@pytest.fixture
def recorded_enqueues(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    """Record every `kb_delete` enqueue without running it (what the `arq` backend does in the api)."""
    recorded: list[tuple[str, dict[str, Any]]] = []
    original = JobsService.enqueue

    async def enqueue(self: JobsService, kind: str, payload: dict[str, Any], **kwargs: Any) -> str:
        if kind != KB_DELETE:
            return await original(self, kind, payload, **kwargs)
        recorded.append((kind, payload))
        return "recorded"

    monkeypatch.setattr(JobsService, "enqueue", enqueue)
    return recorded


async def _create_kb(admin_client: httpx.AsyncClient) -> str:
    response = await admin_client.post("/v1/knowledge-bases", json={"name": "Harbor Lane policies"})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def _upload(admin_client: httpx.AsyncClient, kb_id: str, name: str, content: bytes) -> str:
    response = await admin_client.post(
        f"/v1/knowledge-bases/{kb_id}/documents", files={"file": (name, content, "text/markdown")}
    )
    assert response.status_code == 202, response.text
    return str(response.json()["id"])


def _vector_ids(store: LanceDBStore, kb_id: str) -> set[str]:
    db = store._connect()
    name = f"kb_{kb_id}"
    if name not in db.list_tables().tables:
        return set()
    return {row["id"] for row in db.open_table(name).to_arrow().to_pylist()}


async def _fts_rows(database: Database) -> int:
    async with database.session() as session:
        if database.engine.dialect.name == "sqlite":
            count = await session.scalar(text("SELECT count(*) FROM kb_chunks_fts"))
        else:  # Postgres: the generated `tsv` column is the index, one per chunk row
            count = await session.scalar(text("SELECT count(*) FROM kb_chunks WHERE tsv IS NOT NULL"))
    return int(count or 0)


async def _count(database: Database, model: Any, *where: Any) -> int:
    async with database.session() as session:
        value = await session.scalar(select(func.count()).select_from(model).where(*where))
    return int(value or 0)


# --------------------------------------------------------------------------- inline (dev)
async def test_delete_document_inline_removes_chunks_fts_rows_and_vectors_before_returning(
    admin_client: httpx.AsyncClient, database: Database, store: LanceDBStore
) -> None:
    await create_lexical_index(database)
    kb_id = await _create_kb(admin_client)
    kept = await _upload(admin_client, kb_id, "kept.md", b"# Kept\n\nThe deductible is five hundred dollars.")
    gone = await _upload(
        admin_client, kb_id, "gone.md", b"# Gone\n\nFlood damage under AUTO-11111 is excluded."
    )
    gone_chunks = await _count(database, KbChunk, KbChunk.document_id == gone)
    assert gone_chunks > 0
    fts_before = await _fts_rows(database)
    vectors_before = _vector_ids(store, kb_id)

    response = await admin_client.delete(f"/v1/knowledge-bases/{kb_id}/documents/{gone}")
    assert response.status_code == 204

    assert await _count(database, KbChunk, KbChunk.document_id == gone) == 0
    assert await _count(database, KbDocument, KbDocument.id == gone) == 0
    assert await _fts_rows(database) == fts_before - gone_chunks
    vectors_after = _vector_ids(store, kb_id)
    assert len(vectors_after) == len(vectors_before) - gone_chunks
    async with database.session() as session:
        kept_ids = set(
            (await session.execute(select(KbChunk.id).where(KbChunk.document_id == kept))).scalars()
        )
    assert vectors_after == kept_ids
    async with database.session() as session:
        job = (await session.execute(select(Job).where(Job.kind == KB_DELETE))).scalar_one()
    assert (job.status, job.payload) == ("done", kb_delete_payload(kb_id, gone))


async def test_delete_kb_inline_drops_its_rows_and_its_vector_table(
    admin_client: httpx.AsyncClient, database: Database, store: LanceDBStore
) -> None:
    await create_lexical_index(database)
    kb_id = await _create_kb(admin_client)
    await _upload(admin_client, kb_id, "a.md", b"# A\n\nSome searchable content about deductibles.")
    assert _vector_ids(store, kb_id)

    assert (await admin_client.delete(f"/v1/knowledge-bases/{kb_id}")).status_code == 204

    assert _vector_ids(store, kb_id) == set()
    assert await _count(database, KbChunk, KbChunk.kb_id == kb_id) == 0
    assert await _fts_rows(database) == 0
    async with database.session() as session:
        assert await session.get(KnowledgeBase, kb_id) is None
        job = (await session.execute(select(Job).where(Job.kind == KB_DELETE))).scalar_one()
    assert (job.status, job.payload) == ("done", kb_delete_payload(kb_id))


# --------------------------------------------------------------------------- queued (prod shape)
async def test_queued_delete_never_writes_the_store_from_the_api_and_search_hides_orphans(
    admin_client: httpx.AsyncClient,
    service_client: httpx.AsyncClient,
    database: Database,
    store: LanceDBStore,
    job_context: JobContext,
    recorded_enqueues: list[tuple[str, dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kb_id = await _create_kb(admin_client)
    document_id = await _upload(
        admin_client, kb_id, "a.md", b"# A\n\nThe deductible is five hundred dollars."
    )
    vectors = _vector_ids(store, kb_id)
    assert vectors

    writes: list[str] = []

    async def refuse(self: LanceDBStore, *args: object, **kwargs: object) -> None:
        writes.append("write")

    for name in ("delete_document", "delete_kb", "upsert", "optimize"):
        monkeypatch.setattr(LanceDBStore, name, refuse)
    response = await admin_client.delete(f"/v1/knowledge-bases/{kb_id}/documents/{document_id}")
    assert response.status_code == 204
    assert writes == []
    assert recorded_enqueues == [(KB_DELETE, kb_delete_payload(kb_id, document_id))]
    monkeypatch.undo()

    # The rows are gone at once; the vectors wait for the job and are never returned.
    assert _vector_ids(store, kb_id) == vectors
    search = await service_client.post(
        "/internal/v1/kb/search", json={"kb_ids": [kb_id], "query": "deductible", "k": 4}
    )
    assert search.status_code == 200
    assert search.json()["hits"] == []

    handler = get_handler(KB_DELETE)
    assert handler is run_kb_delete_job
    await handler(job_context, recorded_enqueues[0][1])
    assert _vector_ids(store, kb_id) == set()


async def test_kb_delete_job_is_idempotent_and_sweeps_leftover_chunks(
    database: Database, store: LanceDBStore, job_context: JobContext
) -> None:
    async with database.session() as session:
        kb = KnowledgeBase(name="Race")
        session.add(kb)
        await session.flush()
        document = KbDocument(kb_id=kb.id, filename="a.md", mime="text/markdown", bytes=1, status="pending")
        session.add(document)
        await session.flush()
        chunk = KbChunk(kb_id=kb.id, document_id=document.id, ordinal=0, text="late chunk", meta={})
        session.add(chunk)
        await session.flush()
        kb_id, document_id, chunk_id = kb.id, document.id, chunk.id
    await store.upsert(kb_id, [VectorRecord(id=chunk_id, vector=[1.0] * 32, document_id=document_id)])

    payload = kb_delete_payload(kb_id, document_id)
    await run_kb_delete_job(job_context, payload)
    await run_kb_delete_job(job_context, payload)
    await run_kb_delete_job(job_context, kb_delete_payload(kb_id))

    assert await _count(database, KbChunk, KbChunk.document_id == document_id) == 0
    assert _vector_ids(store, kb_id) == set()


# --------------------------------------------------------------------------- optimize
async def test_every_ingest_job_ends_with_optimize(
    admin_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    optimized: list[str] = []
    original = LanceDBStore.optimize

    async def spy(self: LanceDBStore, kb_id: str) -> None:
        optimized.append(kb_id)
        await original(self, kb_id)

    monkeypatch.setattr(LanceDBStore, "optimize", spy)
    kb_id = await _create_kb(admin_client)
    await _upload(admin_client, kb_id, "a.md", b"# A\n\nSome text.")
    await _upload(admin_client, kb_id, "b.md", b"# B\n\nMore text.")
    assert optimized == [kb_id, kb_id]


async def test_optimize_compacts_and_indexes_only_above_the_threshold(
    store: LanceDBStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    await store.optimize("no-table-yet")

    small = [
        VectorRecord(id=f"s{i}", vector=[float(i % 7), 1.0, 0.5, 0.25], document_id="d") for i in range(10)
    ]
    for record in small:
        await store.upsert("small", [record])
    await store.optimize("small")
    table = store._connect().open_table("kb_small")
    assert list(table.list_indices()) == []
    assert table.count_rows() == 10

    monkeypatch.setattr(store_module, "ANN_INDEX_MIN_ROWS", 300)
    rows = [
        VectorRecord(
            id=f"r{i}", vector=[((i * 37) % 101) / 101.0 + j / 7.0 for j in range(8)], document_id="d"
        )
        for i in range(300)
    ]
    await store.upsert("large", rows)
    await store.optimize("large")
    table = store._connect().open_table("kb_large")
    assert [index.columns for index in table.list_indices()] == [["vector"]]
    hits = await store.query("large", rows[5].vector, 3)
    assert len(hits) == 3
    await store.optimize("large")  # an existing index is not rebuilt
    assert len(list(table.list_indices())) == 1
