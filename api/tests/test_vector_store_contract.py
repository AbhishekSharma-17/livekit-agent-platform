"""The `VectorStore` contract every implementation passes (V5-20, the card's shared test).

Parametrised over the platform stores (LanceDB always; pgvector on the
Postgres CI job) and the three knowledge-connection stores (Qdrant, Pinecone,
Weaviate), each running its real client code against the in-memory vendor
fakes (``tests/fakes/vector_stores.py``) through an ``httpx.MockTransport``.
Every case runs inside one database session with the chunk rows written, so
pgvector's deferred foreign key is satisfied like at ingest.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import postgres_url
from fakes.vector_stores import QDRANT_HOST, WEAVIATE_HOST, FakeVendors
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.db.models import KbChunk, KbDocument, KnowledgeBase, new_id
from lkap_api.db.session import Database
from lkap_api.kb.store import StoreCapabilities, VectorRecord, VectorStore, get_lancedb_store
from lkap_api.kb.stores.pgvector import PgVectorStore
from lkap_api.kb.stores.pinecone import PineconeStore
from lkap_api.kb.stores.qdrant import QdrantStore
from lkap_api.kb.stores.weaviate import WeaviateStore
from lkap_api.knowledge_connections.settings import PineconeSettings, QdrantSettings, WeaviateSettings

DIM = 4
AXES = {
    "north": [1.0, 0.0, 0.0, 0.0],
    "east": [0.0, 1.0, 0.0, 0.0],
    "south": [0.0, 0.0, 1.0, 0.0],
    "west": [0.0, 0.0, 0.0, 1.0],
}

StoreFactory = Callable[[AsyncSession, httpx.AsyncClient, Path], VectorStore]


def _lancedb(_session: AsyncSession, _client: httpx.AsyncClient, data_dir: Path) -> VectorStore:
    return get_lancedb_store(data_dir)


def _pgvector(session: AsyncSession, _client: httpx.AsyncClient, _data_dir: Path) -> VectorStore:
    return PgVectorStore(session)


def _qdrant(_session: AsyncSession, client: httpx.AsyncClient, _data_dir: Path) -> VectorStore:
    settings = QdrantSettings(url=f"https://{QDRANT_HOST}", collection="contract")
    return QdrantStore(client, settings, api_key="qdrant-test-key", workspace_id="ws1")


def _pinecone(_session: AsyncSession, client: httpx.AsyncClient, _data_dir: Path) -> VectorStore:
    return PineconeStore(client, PineconeSettings(index="contract"), api_key="pinecone-test-key")


def _weaviate(_session: AsyncSession, client: httpx.AsyncClient, _data_dir: Path) -> VectorStore:
    settings = WeaviateSettings(url=f"https://{WEAVIATE_HOST}", collection="Contract")
    return WeaviateStore(client, settings, api_key="weaviate-test-key")


BACKENDS: list[Any] = [
    pytest.param(_lancedb, id="lancedb"),
    pytest.param(
        _pgvector,
        id="pgvector",
        marks=pytest.mark.skipif(postgres_url() is None, reason="needs Postgres with pgvector (the CI job)"),
    ),
    pytest.param(_qdrant, id="qdrant"),
    pytest.param(_pinecone, id="pinecone"),
    pytest.param(_weaviate, id="weaviate"),
]


class World:
    """One knowledge base (or two), its documents and chunk rows, and the store under test."""

    def __init__(self, session: AsyncSession, store: VectorStore) -> None:
        self.session = session
        self.store = store

    async def kb(self) -> str:
        kb = KnowledgeBase(name="Contract", dimension=DIM, embedder_model="stub-4")
        self.session.add(kb)
        await self.session.flush()
        return kb.id

    async def document(self, kb_id: str, names: list[str]) -> tuple[str, list[VectorRecord]]:
        document = KbDocument(kb_id=kb_id, filename="d.md", mime="text/markdown", bytes=1, status="ready")
        self.session.add(document)
        await self.session.flush()
        records = [
            VectorRecord(id=new_id(), vector=AXES[name], document_id=document.id, text=name) for name in names
        ]
        self.session.add_all(
            [
                KbChunk(id=r.id, kb_id=kb_id, document_id=document.id, ordinal=i, text=r.text or "", meta={})
                for i, r in enumerate(records)
            ]
        )
        await self.session.flush()
        return document.id, records


@pytest.fixture
async def world_factory(database: Database, data_dir: Path) -> AsyncIterator[Callable[[StoreFactory], Any]]:
    if postgres_url() is not None:
        async with database.engine.connect() as conn:
            if not await conn.scalar(text("SELECT to_regclass('kb_vectors') IS NOT NULL")):
                pytest.skip("the Postgres server has no pgvector extension")
    vendors = FakeVendors()
    async with vendors.client() as client, database.session() as session:

        def make(factory: StoreFactory) -> World:
            return World(session, factory(session, client, data_dir))

        yield make


@pytest.mark.parametrize("factory", BACKENDS)
async def test_nearest_first_with_ids_and_documents(world_factory: Any, factory: StoreFactory) -> None:
    world: World = world_factory(factory)
    kb_id = await world.kb()
    document_id, records = await world.document(kb_id, ["north", "east", "south"])
    await world.store.ensure_namespace(kb_id, DIM)
    await world.store.upsert(kb_id, records)

    hits = await world.store.query(kb_id, [0.9, 0.1, 0.0, 0.0], 3)

    assert [hit.id for hit in hits][:2] == [records[0].id, records[1].id]
    assert all(hit.document_id == document_id for hit in hits)
    assert [hit.score for hit in hits] == sorted((hit.score for hit in hits), reverse=True)
    assert hits[0].score == pytest.approx(0.9 / (0.81 + 0.01) ** 0.5, abs=1e-3)


@pytest.mark.parametrize("factory", BACKENDS)
async def test_a_document_filter_restricts_the_query(world_factory: Any, factory: StoreFactory) -> None:
    world: World = world_factory(factory)
    kb_id = await world.kb()
    first, first_records = await world.document(kb_id, ["north"])
    second, second_records = await world.document(kb_id, ["east"])
    await world.store.upsert(kb_id, first_records + second_records)

    hits = await world.store.query(kb_id, AXES["north"], 5, filters={"document_id": [second]})

    assert [hit.id for hit in hits] == [second_records[0].id]


@pytest.mark.parametrize("factory", BACKENDS)
async def test_knowledge_bases_never_see_each_other(world_factory: Any, factory: StoreFactory) -> None:
    world: World = world_factory(factory)
    one, two = await world.kb(), await world.kb()
    _doc_one, records_one = await world.document(one, ["north"])
    _doc_two, records_two = await world.document(two, ["north"])
    await world.store.upsert(one, records_one)
    await world.store.upsert(two, records_two)

    assert [hit.id for hit in await world.store.query(one, AXES["north"], 5)] == [records_one[0].id]
    assert [hit.id for hit in await world.store.query(two, AXES["north"], 5)] == [records_two[0].id]


@pytest.mark.parametrize("factory", BACKENDS)
async def test_upserting_the_same_id_replaces_it(world_factory: Any, factory: StoreFactory) -> None:
    world: World = world_factory(factory)
    kb_id = await world.kb()
    document_id, records = await world.document(kb_id, ["north"])
    await world.store.upsert(kb_id, records)
    moved = VectorRecord(id=records[0].id, vector=AXES["west"], document_id=document_id)
    await world.store.upsert(kb_id, [moved])

    hits = await world.store.query(kb_id, AXES["west"], 5)

    assert [hit.id for hit in hits] == [records[0].id]
    assert hits[0].score == pytest.approx(1.0, abs=1e-3)


@pytest.mark.parametrize("factory", BACKENDS)
async def test_deleting_a_document_then_the_knowledge_base(world_factory: Any, factory: StoreFactory) -> None:
    world: World = world_factory(factory)
    kb_id = await world.kb()
    first, first_records = await world.document(kb_id, ["north", "east"])
    _second, second_records = await world.document(kb_id, ["south"])
    await world.store.upsert(kb_id, first_records + second_records)

    await world.store.delete_document(kb_id, first)
    await world.store.optimize(kb_id)
    assert [hit.id for hit in await world.store.query(kb_id, AXES["north"], 5)] == [second_records[0].id]

    await world.store.delete_kb(kb_id)
    assert await world.store.query(kb_id, AXES["south"], 5) == []


@pytest.mark.parametrize("factory", BACKENDS)
async def test_an_empty_knowledge_base_answers_nothing(world_factory: Any, factory: StoreFactory) -> None:
    world: World = world_factory(factory)
    kb_id = await world.kb()

    assert await world.store.query(kb_id, AXES["north"], 5) == []
    await world.store.delete_kb(kb_id)  # deleting what was never written is fine


@pytest.mark.parametrize("factory", BACKENDS)
async def test_capabilities_and_health(world_factory: Any, factory: StoreFactory) -> None:
    world: World = world_factory(factory)

    assert isinstance(world.store.capabilities, StoreCapabilities)
    assert world.store.capabilities.filters is True
    health = await world.store.health()
    assert health.ok, health.detail
