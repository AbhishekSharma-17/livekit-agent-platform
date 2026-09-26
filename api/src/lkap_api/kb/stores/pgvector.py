"""`PgVectorStore`: vectors in the same Postgres database as the chunk rows (V5-13, D-V5-12/13).

The default store whenever ``LKAP_DATABASE_URL`` is Postgres
(:func:`~lkap_api.kb.store.resolve_store`). One table, ``kb_vectors``
(migration ``v5_003_pgvector``)::

    chunk_id  VARCHAR(32) PRIMARY KEY  -> kb_chunks.id  ON DELETE CASCADE
                                          DEFERRABLE INITIALLY DEFERRED
    kb_id     VARCHAR(32) NOT NULL     (btree index)
    embedding vector      NOT NULL     (no fixed width: knowledge bases differ)

**One transaction with the chunk rows.** The store is bound to the caller's
:class:`~sqlalchemy.ext.asyncio.AsyncSession` and runs every statement on it,
so an ingest's vectors commit or roll back with its ``kb_chunks`` rows: an
aborted ingest leaves no vectors, a restored backup never has orphans, and
``pg_dump`` covers both. That is also why this store is not an ``asyncpg``
pool with ``pgvector.asyncpg.register_vector``: it goes through the session's
own ``asyncpg`` connection and passes vectors in pgvector's text form
(``'[0.1,0.2]'`` cast to ``vector``), which ``asyncpg`` sends as text for a
type it has no binary codec for. Consequences:

* the ingest path upserts vectors *before* it flushes the chunk rows, so the
  foreign key is ``DEFERRABLE INITIALLY DEFERRED`` (checked at commit; the
  ``ON DELETE CASCADE`` action is never deferred, so deleting a document's
  chunks removes its vectors at once);
* every write runs inside a SAVEPOINT, so a failed vector write does not
  poison the caller's transaction (the ingest still records the document as
  ``failed`` on the same session);
* one ``asyncio.Lock`` per store serialises its statements, because the
  search pipeline queries several knowledge bases concurrently and one
  connection runs one statement at a time. A per-knowledge-base timeout that
  cancels a statement mid-flight relies on ``asyncpg``'s cancel request to
  leave the connection usable.

**One HNSW index per knowledge base.** An HNSW index needs a fixed width and
the column has none, so each knowledge base gets a *partial expression*
index, ``(embedding::vector(<d>)) vector_cosine_ops WHERE kb_id = '<id>' AND
vector_dims(embedding) = <d>`` (:func:`partial_predicate`),
named :func:`hnsw_index_name` (the width is in the name, so a re-index at a
new width replaces it). Queries repeat the same expression with the knowledge
base id as a **literal** (a partial index cannot match a bound ``$1`` under a
generic plan), which is safe because every id passes
:func:`~lkap_api.kb.stores.check_safe_id`. Above 2,000 dimensions the index
and the query cast to ``halfvec(<d>)`` (pgvector ≥ 0.7; the index limit is
4,000); above 4,000, or on an older pgvector, the knowledge base has no index
and is searched exactly. ``CREATE INDEX`` runs inside the writer's
transaction and holds a SHARE lock on ``kb_vectors`` until it commits, which
briefly queues other knowledge bases' writes; it only happens on a
knowledge base's first upsert or a width change.

**Filters.** ``filters={"document_id": ...}`` becomes a sub-select on
``kb_chunks``; with pgvector ≥ 0.8 the query first sets
``hnsw.iterative_scan = relaxed_order`` (``SET LOCAL``) so a selective filter
still returns ``k`` rows, and the outer query re-sorts by distance.

**Hybrid.** ``capabilities.hybrid`` is true: ``query(text=...)`` runs the
same keyword query as :func:`lkap_api.kb.lexical.lexical_search` (the
``kb_chunks.tsv`` column from ``v5_001``) and fuses it with the dense list by
:func:`lkap_api.kb.search.fuse_rrf`, exactly as the search pipeline would, so
one knowledge base's hybrid result is the same either way.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Final

from pgvector import Vector
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.kb.embed import KbEmbedderMismatchError
from lkap_api.kb.lexical import lexical_search
from lkap_api.kb.stores import (
    StoreCapabilities,
    StoreHealth,
    VectorHit,
    VectorRecord,
    check_safe_id,
    document_filter,
)
from lkap_api.logging import get_logger

log = get_logger(__name__)

#: The v2 capabilities of :class:`PgVectorStore` (the V5-13 card).
PGVECTOR_CAPABILITIES: Final = StoreCapabilities(
    hybrid=True, filters=True, stores_text=False, namespaces=True
)

#: The table migration ``v5_003_pgvector`` creates.
TABLE: Final = "kb_vectors"
#: pgvector's HNSW limits: ``vector`` indexes up to 2,000 dimensions, ``halfvec`` up to 4,000.
MAX_VECTOR_INDEX_DIM: Final = 2000
MAX_HALFVEC_INDEX_DIM: Final = 4000
#: ``vector`` columns hold at most this many dimensions.
MAX_DIMENSION: Final = 16000
#: ``halfvec`` exists from pgvector 0.7.0, ``hnsw.iterative_scan`` from 0.8.0.
HALFVEC_MIN_VERSION: Final = (0, 7, 0)
ITERATIVE_SCAN_MIN_VERSION: Final = (0, 8, 0)
#: Prefix of every per-knowledge-base HNSW index name.
INDEX_PREFIX: Final = "kbv_hnsw_"

#: The ``vector`` extension's version per database url (one lookup per process).
_VERSION_CACHE: dict[str, tuple[int, ...]] = {}


def parse_version(value: str) -> tuple[int, ...]:
    """``"0.8.0"`` → ``(0, 8, 0)``; any non-numeric part ends the tuple."""
    parts: list[int] = []
    for piece in value.split("."):
        if not piece.isdigit():
            break
        parts.append(int(piece))
    return tuple(parts)


def hnsw_index_name(kb_id: str, dimension: int) -> str:
    """The name of ``kb_id``'s HNSW index at ``dimension`` (≤ 63 characters for a 32-character id)."""
    return f"{INDEX_PREFIX}{check_safe_id(kb_id, what='knowledge base id')}_{dimension}"


def distance_type(dimension: int, version: tuple[int, ...]) -> str:
    """The type a ``dimension``-wide knowledge base is indexed and queried as.

    ``vector(<d>)`` up to 2,000 dimensions (and above 4,000, unindexed);
    ``halfvec(<d>)`` from 2,001 to 4,000 when pgvector has it.
    """
    if MAX_VECTOR_INDEX_DIM < dimension <= MAX_HALFVEC_INDEX_DIM and version >= HALFVEC_MIN_VERSION:
        return f"halfvec({dimension})"
    return f"vector({dimension})"


def partial_predicate(kb_id: str, dimension: int) -> str:
    """The partial-index predicate, repeated verbatim by every query so the planner can use the index.

    ``vector_dims(embedding) = <d>`` is part of it so the width cast in the
    index expression is only ever evaluated on rows of that width: a
    ``CREATE INDEX`` also reads rows deleted by its own (or a still-open)
    transaction, which is exactly what a re-index at a new width leaves behind.
    """
    safe = check_safe_id(kb_id, what="knowledge base id")
    return f"kb_id = '{safe}' AND vector_dims(embedding) = {int(dimension)}"


def hnsw_index_sql(kb_id: str, dimension: int, version: tuple[int, ...]) -> str | None:
    """``CREATE INDEX`` for ``kb_id``'s HNSW index, or ``None`` when that width cannot be indexed.

    Kept in step with the copy in ``alembic/versions/v5_003_pgvector.py``
    (migrations do not import application code).
    """
    cast = distance_type(dimension, version)
    if cast.startswith("vector") and dimension > MAX_VECTOR_INDEX_DIM:
        return None
    ops = "halfvec_cosine_ops" if cast.startswith("halfvec") else "vector_cosine_ops"
    return (
        f"CREATE INDEX IF NOT EXISTS {hnsw_index_name(kb_id, dimension)} ON {TABLE} "
        f"USING hnsw ((embedding::{cast}) {ops}) WHERE {partial_predicate(kb_id, dimension)}"
    )


def vector_text(values: list[float]) -> str:
    """pgvector's text form of ``values`` (``'[0.1,0.2]'``)."""
    return str(Vector(values).to_text())


class PgVectorStore:
    """A :class:`~lkap_api.kb.store.VectorStore` on the caller's Postgres session (module docstring)."""

    capabilities: StoreCapabilities = PGVECTOR_CAPABILITIES

    def __init__(self, session: AsyncSession) -> None:
        """Bind the store to ``session``; every statement runs in its transaction."""
        self._session = session
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------- helpers
    async def _version(self) -> tuple[int, ...]:
        key = self._session.get_bind().engine.url.render_as_string(hide_password=True)
        cached = _VERSION_CACHE.get(key)
        if cached is not None:
            return cached
        raw = await self._session.scalar(text("SELECT extversion FROM pg_extension WHERE extname = 'vector'"))
        version = parse_version(str(raw)) if raw is not None else ()
        if version:
            _VERSION_CACHE[key] = version
        return version

    async def _stored_dimension(self, kb_id: str) -> int | None:
        value = await self._session.scalar(
            text(f"SELECT vector_dims(embedding) FROM {TABLE} WHERE kb_id = :kb_id LIMIT 1"),
            {"kb_id": kb_id},
        )
        return int(value) if value is not None else None

    async def _kb_indexes(self, kb_id: str) -> list[str]:
        rows = await self._session.execute(
            text(
                "SELECT indexname FROM pg_indexes WHERE schemaname = current_schema() "
                "AND tablename = :table AND starts_with(indexname, :prefix)"
            ),
            {"table": TABLE, "prefix": f"{INDEX_PREFIX}{kb_id}_"},
        )
        return [str(name) for name in rows.scalars()]

    async def _drop_indexes(self, kb_id: str, names: list[str]) -> None:
        for name in names:
            await self._session.execute(text(f'DROP INDEX IF EXISTS "{name}"'))
            log.info("kb_vector_index_dropped", kb_id=kb_id, index=name)

    @staticmethod
    def _mismatch(kb_id: str, stored: int, given: int) -> KbEmbedderMismatchError:
        return KbEmbedderMismatchError(
            f"knowledge base '{kb_id}' holds {stored}-dimension vectors but these vectors have "
            f"{given}; re-index the knowledge base with the configured embedder",
            details={"kb_id": kb_id, "kb_dimension": stored, "embedder_dimension": given},
        )

    async def _ensure_namespace(self, kb_id: str, dimension: int) -> None:
        stored = await self._stored_dimension(kb_id)
        if stored is not None and stored != dimension:
            raise self._mismatch(kb_id, stored, dimension)
        version = await self._version()
        create = hnsw_index_sql(kb_id, dimension, version)
        wanted = hnsw_index_name(kb_id, dimension) if create is not None else None
        existing = await self._kb_indexes(kb_id)
        await self._drop_indexes(kb_id, [name for name in existing if name != wanted])
        if create is not None and wanted not in existing:
            await self._session.execute(text(create))
            log.info("kb_vector_index_created", kb_id=kb_id, dimension=dimension, index=wanted)

    # ------------------------------------------------------------------- the Protocol
    async def ensure_namespace(self, kb_id: str, dimension: int) -> None:
        """Create ``kb_id``'s HNSW index at ``dimension`` (replacing one at another width).

        Raises:
            ValueError: An unsafe id or a dimension outside ``1..16000``.
            KbEmbedderMismatchError: The knowledge base already holds vectors of another width.
        """
        check_safe_id(kb_id, what="knowledge base id")
        if not 1 <= dimension <= MAX_DIMENSION:
            raise ValueError(f"a pgvector dimension must be between 1 and {MAX_DIMENSION}, not {dimension}")
        async with self._lock, self._session.begin_nested():
            await self._ensure_namespace(kb_id, dimension)

    async def upsert(self, kb_id: str, records: list[VectorRecord]) -> None:
        """Insert or replace vectors for the given chunk ids (in the caller's transaction).

        Raises:
            ValueError: An unsafe id, or records of different widths.
            KbEmbedderMismatchError: The knowledge base holds vectors of another width (422).
        """
        if not records:
            return
        check_safe_id(kb_id, what="knowledge base id")
        for record in records:
            check_safe_id(record.id)
            check_safe_id(record.document_id, what="document id")
        widths = {len(record.vector) for record in records}
        if len(widths) != 1:
            raise ValueError(f"records of one upsert must share a width, got {sorted(widths)}")
        dimension = widths.pop()
        if not 1 <= dimension <= MAX_DIMENSION:
            raise ValueError(f"a pgvector dimension must be between 1 and {MAX_DIMENSION}, not {dimension}")
        rows = [{"chunk_id": r.id, "kb_id": kb_id, "embedding": vector_text(r.vector)} for r in records]
        async with self._lock, self._session.begin_nested():
            await self._ensure_namespace(kb_id, dimension)
            await self._session.execute(
                text(
                    f"INSERT INTO {TABLE} (chunk_id, kb_id, embedding) "
                    "VALUES (:chunk_id, :kb_id, CAST(:embedding AS vector)) "
                    "ON CONFLICT (chunk_id) DO UPDATE "
                    "SET kb_id = EXCLUDED.kb_id, embedding = EXCLUDED.embedding"
                ),
                rows,
            )
        log.debug("kb_vectors_upserted", kb_id=kb_id, count=len(records), backend="pgvector")

    async def query(
        self,
        kb_id: str,
        vector: list[float],
        k: int,
        *,
        text: str | None = None,
        filters: Mapping[str, object] | None = None,
    ) -> list[VectorHit]:
        """Return the ``k`` best chunks of ``kb_id``: dense, or fused with keywords when ``text`` is given.

        Args:
            kb_id: The knowledge base.
            vector: The query vector (its width must be the knowledge base's).
            k: How many hits (per list, and in the fused result).
            text: The query text; when given, the hits are the reciprocal-rank
                fusion of the dense list and the keyword list (``fused=True``).
            filters: ``{"document_id": id | [ids]}`` restricts both lists.

        Raises:
            ValueError: An unsafe id or an unsupported filter.
            KbEmbedderMismatchError: ``vector``'s width is not the knowledge base's (422).
        """
        check_safe_id(kb_id, what="knowledge base id")
        documents = document_filter(filters)
        if k <= 0:
            return []
        async with self._lock:
            dense = await self._dense(kb_id, vector, k, documents)
            if text is None:
                return dense
            return await self._fuse(kb_id, dense, text, k, documents)

    async def _dense(
        self, kb_id: str, vector: list[float], k: int, documents: list[str] | None
    ) -> list[VectorHit]:
        stored = await self._stored_dimension(kb_id)
        if stored is None:
            return []
        if stored != len(vector):
            raise self._mismatch(kb_id, stored, len(vector))
        version = await self._version()
        cast = distance_type(stored, version)
        params: dict[str, object] = {"q": vector_text(vector), "k": k}
        scope = ""
        if documents is not None:
            placeholders = ", ".join(f":d{i}" for i in range(len(documents)))
            params.update({f"d{i}": document_id for i, document_id in enumerate(documents)})
            scope = f" AND chunk_id IN (SELECT id FROM kb_chunks WHERE document_id IN ({placeholders}))"
            if version >= ITERATIVE_SCAN_MIN_VERSION:
                await self._session.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
        distance = f"embedding::{cast} <=> CAST(:q AS {cast})"
        statement = text(
            "SELECT nn.chunk_id, c.document_id, nn.distance FROM ("
            f"SELECT chunk_id, {distance} AS distance FROM {TABLE} "
            f"WHERE {partial_predicate(kb_id, stored)}{scope} ORDER BY {distance} LIMIT :k"
            ") AS nn JOIN kb_chunks AS c ON c.id = nn.chunk_id ORDER BY nn.distance, nn.chunk_id"
        )
        rows = (await self._session.execute(statement, params)).all()
        return [
            VectorHit(id=str(chunk_id), document_id=str(document_id), score=1.0 - float(dist))
            for chunk_id, document_id, dist in rows
        ]

    async def _fuse(
        self, kb_id: str, dense: list[VectorHit], query: str, k: int, documents: list[str] | None
    ) -> list[VectorHit]:
        from lkap_api.kb.search import fuse_rrf

        lexical = await lexical_search(self._session, kb_ids=[kb_id], query=query, limit=k)
        if not lexical.available:
            log.warning("kb_pgvector_lexical_unavailable", kb_id=kb_id)
        lexical_ids = [hit.chunk_id for hit in lexical.hits]
        owners = {hit.id: hit.document_id for hit in dense}
        missing = [chunk_id for chunk_id in lexical_ids if chunk_id not in owners]
        if missing:
            placeholders = ", ".join(f":c{i}" for i in range(len(missing)))
            rows = await self._session.execute(
                text(f"SELECT id, document_id FROM kb_chunks WHERE id IN ({placeholders})"),
                {f"c{i}": chunk_id for i, chunk_id in enumerate(missing)},
            )
            owners.update({str(chunk_id): str(document_id) for chunk_id, document_id in rows.all()})
        if documents is not None:
            allowed = set(documents)
            lexical_ids = [chunk_id for chunk_id in lexical_ids if owners.get(chunk_id) in allowed]
        lexical_ids = [chunk_id for chunk_id in lexical_ids if chunk_id in owners]
        dense_ids = [hit.id for hit in dense]
        scores = {hit.id: hit.score for hit in dense}
        rankings = [dense_ids, lexical_ids] if lexical.available else [dense_ids]
        fused: list[VectorHit] = []
        for item in fuse_rrf(rankings)[:k]:
            lexical_rank = item.ranks[1] if len(item.ranks) > 1 else None
            fused.append(
                VectorHit(
                    id=item.id,
                    document_id=owners[item.id],
                    score=item.score,
                    vector_score=scores.get(item.id),
                    lexical_rank=lexical_rank,
                    fused=True,
                )
            )
        return fused

    async def delete_document(self, kb_id: str, document_id: str) -> None:
        """Remove every vector of one document (a no-op once its chunk rows are gone: the FK cascades)."""
        check_safe_id(kb_id, what="knowledge base id")
        check_safe_id(document_id, what="document id")
        async with self._lock, self._session.begin_nested():
            await self._session.execute(
                text(
                    f"DELETE FROM {TABLE} WHERE kb_id = :kb_id "
                    "AND chunk_id IN (SELECT id FROM kb_chunks WHERE document_id = :document_id)"
                ),
                {"kb_id": kb_id, "document_id": document_id},
            )

    async def delete_kb(self, kb_id: str) -> None:
        """Remove every vector of the knowledge base and drop its HNSW index."""
        check_safe_id(kb_id, what="knowledge base id")
        async with self._lock, self._session.begin_nested():
            await self._session.execute(text(f"DELETE FROM {TABLE} WHERE kb_id = :kb_id"), {"kb_id": kb_id})
            await self._drop_indexes(kb_id, await self._kb_indexes(kb_id))

    async def optimize(self, kb_id: str) -> None:
        """A no-op: Postgres maintains the HNSW index on every write and autovacuum reclaims deletes."""
        check_safe_id(kb_id, what="knowledge base id")

    async def health(self) -> StoreHealth:
        """Whether the ``vector`` extension and the ``kb_vectors`` table exist."""
        try:
            async with self._lock:
                raw = await self._session.scalar(
                    text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
                )
                table = await self._session.scalar(text(f"SELECT to_regclass('{TABLE}') IS NOT NULL"))
        except Exception as exc:  # noqa: BLE001 - reported, never raised
            return StoreHealth(
                ok=False, backend="pgvector", detail=f"cannot query the database ({type(exc).__name__})"
            )
        version = str(raw) if raw is not None else None
        if version is None:
            return StoreHealth(
                ok=False,
                backend="pgvector",
                detail="the Postgres 'vector' extension is not installed; run the migrations on a server "
                "that has pgvector (the pgvector/pgvector image) or set LKAP_VECTOR_STORE=lancedb",
            )
        if not table:
            return StoreHealth(
                ok=False,
                backend="pgvector",
                version=version,
                detail="the kb_vectors table is missing; run the migrations",
            )
        return StoreHealth(ok=True, backend="pgvector", version=version)
