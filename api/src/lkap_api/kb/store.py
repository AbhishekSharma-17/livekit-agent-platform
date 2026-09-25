"""Vector storage behind a swappable :class:`VectorStore` Protocol.

:class:`LanceDBStore` is the concrete implementation (file-based, no server,
per docs/ARCHITECTURE.md §D6): one LanceDB table per knowledge base, named
``kb_{kb_id}``, cosine distance. The table stores only the vector and enough
ids to join back to :class:`~lkap_api.db.models.KbChunk` for the text —
LanceDB is not the source of truth, SQLite is (docs/CONTRACTS.md §5).

LanceDB's Python client is synchronous; every call here runs on a worker
thread via :func:`asyncio.to_thread` so the event loop is never blocked, and
writes are serialised with a lock (LanceDB is single-writer).

V5-04 (D-V5-12, single writer): in production (``LKAP_JOBS_BACKEND=arq``)
only the ``jobs`` process writes here — ingestion and deletes are both jobs
(``kb_ingest``, ``kb_delete``); the api process only queries. Every ingest
job ends with :meth:`LanceDBStore.optimize`, which compacts the table's
fragments and, above :data:`ANN_INDEX_MIN_ROWS` rows, builds an ANN index.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Protocol, cast, runtime_checkable

import lancedb
from lancedb.query import LanceVectorQueryBuilder

from lkap_api.logging import get_logger

log = get_logger(__name__)

#: Ids are our own uuid4 hex values; this guards the hand-built SQL `IN (...)`
#: clauses against anything else ever reaching them.
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")

#: A table with at least this many rows gets an ANN (IVF-PQ, cosine) index; a
#: smaller one is searched exhaustively, which is exact and fast enough.
ANN_INDEX_MIN_ROWS = 50_000
#: Old table versions `optimize` keeps (a reader mid-query still sees its version).
OPTIMIZE_KEEP_VERSIONS_FOR = timedelta(minutes=10)


def _quoted_ids(ids: list[str]) -> str:
    for value in ids:
        if not _SAFE_ID.match(value):
            raise ValueError(f"unsafe id for a LanceDB filter: {value!r}")
    return ", ".join(f"'{value}'" for value in ids)


@dataclass(slots=True, frozen=True)
class VectorRecord:
    """One chunk's vector, keyed by its `KbChunk.id`."""

    id: str
    vector: list[float]
    document_id: str


@dataclass(slots=True, frozen=True)
class VectorHit:
    """One nearest-neighbour result: a chunk id, its document and a similarity score."""

    id: str
    document_id: str
    score: float


@runtime_checkable
class VectorStore(Protocol):
    """Per-knowledge-base vector upsert/query/delete."""

    async def upsert(self, kb_id: str, records: list[VectorRecord]) -> None:
        """Insert or replace vectors for the given chunk ids."""
        ...

    async def query(self, kb_id: str, vector: list[float], k: int) -> list[VectorHit]:
        """Return the ``k`` nearest chunks to ``vector`` (best first)."""
        ...

    async def delete_document(self, kb_id: str, document_id: str) -> None:
        """Remove every vector belonging to one document."""
        ...

    async def delete_kb(self, kb_id: str) -> None:
        """Drop the whole knowledge base's table."""
        ...

    async def optimize(self, kb_id: str) -> None:
        """Compact the knowledge base's storage and (re)build its index when it is large enough."""
        ...


class LanceDBStore:
    """LanceDB-backed :class:`VectorStore`, rooted at ``{data_dir}/lancedb``."""

    def __init__(self, data_dir: str | Path) -> None:
        """Create the store (does not open a connection until first use)."""
        self._uri = str(Path(data_dir) / "lancedb")
        self._db: lancedb.DBConnection | None = None
        self._write_lock = asyncio.Lock()

    @staticmethod
    def _table_name(kb_id: str) -> str:
        return f"kb_{kb_id}"

    def _connect(self) -> lancedb.DBConnection:
        if self._db is None:
            self._db = lancedb.connect(self._uri)
        return self._db

    async def upsert(self, kb_id: str, records: list[VectorRecord]) -> None:
        """Insert or replace vectors for the given chunk ids.

        Raises:
            ValueError: If any record or document id contains characters
                outside `[A-Za-z0-9_-]` (our ids are always uuid4 hex; this
                guards the hand-built SQL filters below).
        """
        if not records:
            return
        ids = _quoted_ids([r.id for r in records])  # validates before touching the table
        async with self._write_lock:
            await asyncio.to_thread(self._upsert_sync, kb_id, records, ids)

    def _upsert_sync(self, kb_id: str, records: list[VectorRecord], quoted_ids: str) -> None:
        db = self._connect()
        table_name = self._table_name(kb_id)
        rows = [{"id": r.id, "vector": r.vector, "document_id": r.document_id} for r in records]
        if table_name in db.table_names():
            table = db.open_table(table_name)
            table.delete(f"id IN ({quoted_ids})")
            table.add(rows)
        else:
            db.create_table(table_name, data=rows)
        log.debug("kb_vectors_upserted", kb_id=kb_id, count=len(records))

    async def query(self, kb_id: str, vector: list[float], k: int) -> list[VectorHit]:
        """Return the ``k`` nearest chunks to ``vector`` (best first)."""
        return await asyncio.to_thread(self._query_sync, kb_id, vector, k)

    def _query_sync(self, kb_id: str, vector: list[float], k: int) -> list[VectorHit]:
        db = self._connect()
        table_name = self._table_name(kb_id)
        if table_name not in db.table_names():
            return []
        table = db.open_table(table_name)
        if table.count_rows() == 0:
            return []
        query = cast(LanceVectorQueryBuilder, table.search(vector))
        rows = query.metric("cosine").limit(max(k, 1)).to_list()
        return [
            VectorHit(id=row["id"], document_id=row["document_id"], score=1.0 - float(row["_distance"]))
            for row in rows
        ]

    async def delete_document(self, kb_id: str, document_id: str) -> None:
        """Remove every vector belonging to one document."""
        async with self._write_lock:
            await asyncio.to_thread(self._delete_document_sync, kb_id, document_id)

    def _delete_document_sync(self, kb_id: str, document_id: str) -> None:
        db = self._connect()
        table_name = self._table_name(kb_id)
        if table_name in db.table_names():
            db.open_table(table_name).delete(f"document_id IN ({_quoted_ids([document_id])})")

    async def delete_kb(self, kb_id: str) -> None:
        """Drop the whole knowledge base's table."""
        async with self._write_lock:
            await asyncio.to_thread(self._delete_kb_sync, kb_id)

    def _delete_kb_sync(self, kb_id: str) -> None:
        db = self._connect()
        table_name = self._table_name(kb_id)
        if table_name in db.table_names():
            db.drop_table(table_name)

    async def optimize(self, kb_id: str) -> None:
        """Compact the table, prune old versions and build the ANN index above the threshold.

        Called at the end of every ingest and delete job (V5-04). Each ingest
        appends a fragment and each delete a deletion file, so without this a
        busy knowledge base's queries slow down over time. A no-op for a
        knowledge base with no table.
        """
        async with self._write_lock:
            await asyncio.to_thread(self._optimize_sync, kb_id)

    def _optimize_sync(self, kb_id: str) -> None:
        db = self._connect()
        table_name = self._table_name(kb_id)
        if table_name not in db.table_names():
            return
        table = db.open_table(table_name)
        rows = table.count_rows()
        if rows >= ANN_INDEX_MIN_ROWS and not any(
            index.columns == ["vector"] for index in table.list_indices()
        ):
            table.create_index(metric="cosine", vector_column_name="vector", index_type="IVF_PQ")
            log.info("kb_vector_index_created", kb_id=kb_id, rows=rows)
        table.optimize(cleanup_older_than=OPTIMIZE_KEEP_VERSIONS_FOR)
        log.debug("kb_vectors_optimized", kb_id=kb_id, rows=rows)


_STORE_CACHE: dict[str, LanceDBStore] = {}


def get_lancedb_store(data_dir: str | Path) -> LanceDBStore:
    """Return the process-wide :class:`LanceDBStore`, cached by data dir."""
    key = str(Path(data_dir))
    if key not in _STORE_CACHE:
        _STORE_CACHE[key] = LanceDBStore(data_dir)
    return _STORE_CACHE[key]


def clear_store_cache() -> None:
    """Drop cached store instances (tests only)."""
    _STORE_CACHE.clear()
