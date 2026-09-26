"""`LanceDBStore`: the file-based vector store (docs/ARCHITECTURE.md §D6), the SQLite default.

One LanceDB table per knowledge base, named ``kb_{kb_id}``, cosine distance.
The table stores only the vector and enough ids to join back to
:class:`~lkap_api.db.models.KbChunk` for the text — LanceDB is not the source
of truth, the SQL database is (docs/CONTRACTS.md §5, D-V5-37).

LanceDB's Python client is synchronous; every call here runs on a worker
thread via :func:`asyncio.to_thread` so the event loop is never blocked, and
writes are serialised with a lock (LanceDB is single-writer).

V5-04 (D-V5-12, single writer): in production (``LKAP_JOBS_BACKEND=arq``)
only the ``jobs`` process writes here — ingestion and deletes are both jobs
(``kb_ingest``, ``kb_delete``); the api process only queries. Every ingest
job ends with :meth:`LanceDBStore.optimize`, which compacts the table's
fragments and, above :data:`~lkap_api.kb.store.ANN_INDEX_MIN_ROWS` rows,
builds an ANN index.

V5-13: moved here from ``kb/store.py`` (which re-exports it) and adapted to
the v2 Protocol: capabilities ``hybrid=False, filters=True, stores_text=False,
namespaces=True``; ``query`` takes ``filters={"document_id": ...}`` as a
prefilter; ``ensure_namespace`` is a no-op (a table is created by its first
upsert, at the width of its vectors); ``health`` opens the directory. Since
V5-13 this store is chosen only when the database is SQLite or
``LKAP_VECTOR_STORE=lancedb`` (:func:`~lkap_api.kb.store.resolve_store`).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Final, cast

import lancedb
from lancedb.query import LanceVectorQueryBuilder

from lkap_api.kb.stores import (
    SAFE_ID,
    StoreCapabilities,
    StoreHealth,
    VectorHit,
    VectorRecord,
    document_filter,
)
from lkap_api.logging import get_logger

log = get_logger(__name__)

#: The v2 capabilities of :class:`LanceDBStore` (the V5-13 card).
LANCEDB_CAPABILITIES: Final = StoreCapabilities(
    hybrid=False, filters=True, stores_text=False, namespaces=True
)


def _quoted_ids(ids: list[str]) -> str:
    for value in ids:
        if not SAFE_ID.match(value):
            raise ValueError(f"unsafe id for a LanceDB filter: {value!r}")
    return ", ".join(f"'{value}'" for value in ids)


def _ann_index_min_rows() -> int:
    # Read through `lkap_api.kb.store` at call time: that module is the
    # threshold's public home (and where tests patch it).
    from lkap_api.kb import store

    return store.ANN_INDEX_MIN_ROWS


class LanceDBStore:
    """LanceDB-backed :class:`~lkap_api.kb.store.VectorStore`, rooted at ``{data_dir}/lancedb``."""

    capabilities: StoreCapabilities = LANCEDB_CAPABILITIES

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

    async def ensure_namespace(self, kb_id: str, dimension: int) -> None:
        """A no-op: a knowledge base's table is created by its first upsert, at its vectors' width."""
        _quoted_ids([kb_id])

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

    async def query(
        self,
        kb_id: str,
        vector: list[float],
        k: int,
        *,
        text: str | None = None,
        filters: Mapping[str, object] | None = None,
    ) -> list[VectorHit]:
        """Return the ``k`` nearest chunks to ``vector`` (best first).

        Args:
            kb_id: The knowledge base.
            vector: The query vector.
            k: How many hits.
            text: Ignored: LanceDB has no native hybrid here (``capabilities.hybrid``
                is false), so the search pipeline runs the lexical stage itself.
            filters: ``{"document_id": id | [ids]}`` restricts the search (a prefilter).

        Raises:
            ValueError: An unsupported filter or an unsafe id in one.
        """
        documents = document_filter(filters)
        return await asyncio.to_thread(self._query_sync, kb_id, vector, k, documents)

    def _query_sync(
        self, kb_id: str, vector: list[float], k: int, documents: list[str] | None
    ) -> list[VectorHit]:
        db = self._connect()
        table_name = self._table_name(kb_id)
        if table_name not in db.table_names():
            return []
        table = db.open_table(table_name)
        if table.count_rows() == 0:
            return []
        query = cast(LanceVectorQueryBuilder, table.search(vector)).metric("cosine")
        if documents is not None:
            query = query.where(f"document_id IN ({_quoted_ids(documents)})", prefilter=True)
        rows = query.limit(max(k, 1)).to_list()
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
        from lkap_api.kb.store import OPTIMIZE_KEEP_VERSIONS_FOR

        db = self._connect()
        table_name = self._table_name(kb_id)
        if table_name not in db.table_names():
            return
        table = db.open_table(table_name)
        rows = table.count_rows()
        if rows >= _ann_index_min_rows() and not any(
            index.columns == ["vector"] for index in table.list_indices()
        ):
            table.create_index(metric="cosine", vector_column_name="vector", index_type="IVF_PQ")
            log.info("kb_vector_index_created", kb_id=kb_id, rows=rows)
        table.optimize(cleanup_older_than=OPTIMIZE_KEEP_VERSIONS_FOR)
        log.debug("kb_vectors_optimized", kb_id=kb_id, rows=rows)

    async def health(self) -> StoreHealth:
        """Whether the LanceDB directory can be opened and listed."""
        try:
            await asyncio.to_thread(lambda: self._connect().table_names())
        except Exception as exc:  # noqa: BLE001 - reported, never raised
            return StoreHealth(
                ok=False, backend="lancedb", detail=f"cannot open the store ({type(exc).__name__})"
            )
        return StoreHealth(ok=True, backend="lancedb", version=getattr(lancedb, "__version__", None))


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
