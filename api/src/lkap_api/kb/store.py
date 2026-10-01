"""Vector storage behind a swappable :class:`VectorStore` Protocol (v2 since V5-13).

Two built-in implementations (``kb/stores/``), chosen by :func:`resolve_store`
("the default store follows the database", D-V5-12/D-V5-13):

* :class:`~lkap_api.kb.stores.lancedb.LanceDBStore` — file-based, no server
  (docs/ARCHITECTURE.md §D6); the default when ``LKAP_DATABASE_URL`` is SQLite
  (dev, laptop, single container), and whenever ``LKAP_VECTOR_STORE=lancedb``.
* :class:`~lkap_api.kb.stores.pgvector.PgVectorStore` — the ``kb_vectors``
  table in the same Postgres database as the chunk rows (migration
  ``v5_003_pgvector``); the default when ``LKAP_DATABASE_URL`` is Postgres.
  It runs on the caller's session, so its writes share the chunk rows'
  transaction, and there is no second writer and no shared ``/data`` volume
  to coordinate.

Neither store is the source of truth for chunk text (D-V5-37): the search
pipeline joins every hit back to ``kb_chunks``. Moving a knowledge base
between stores, or onto a new embedder, is the ``kb_reindex`` job
(:mod:`lkap_api.kb.jobs`), which re-embeds the chunk rows.

The Protocol, per K §5.1, with one deliberate difference: ``query`` keeps
``k`` as its third positional argument (``query(kb_id, vector, k, *, text,
filters)``), so every caller and test double written against v1 keeps
working; ``text`` and ``filters`` are keyword-only. A store that declares no
``capabilities`` (a v1 test double) is treated as :data:`NO_CAPABILITIES`
(:func:`store_capabilities`).

**Per-knowledge-base stores (V5-20).** A knowledge base created with a
``connection_id`` keeps its vectors in that knowledge connection's store
(Qdrant, Pinecone or Weaviate, ``kb/stores/{qdrant,pinecone,weaviate}.py``).
Given a session, :func:`resolve_store` returns a :class:`KbStoreRouter`: every
Protocol call is routed by its ``kb_id`` to the knowledge base's connection
store, or to the platform's store (above) when it has none. So ingest, search,
evaluation, delete and re-index callers need no change. The router's own
``capabilities`` and ``health`` are the platform store's, so a platform-default
knowledge base behaves exactly as before; :func:`kb_capabilities` answers per
knowledge base. A knowledge base bound to a connection that cannot be used
**fails closed** (the call raises; nothing falls back to the platform store).
Without a session there is no router (and no connection can be resolved):
the platform store is returned as before.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import TYPE_CHECKING, Final, Literal, Protocol, runtime_checkable

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.errors import UnprocessableEntityError
from lkap_api.kb.stores import (
    NO_CAPABILITIES,
    SAFE_ID,
    StoreCapabilities,
    StoreHealth,
    VectorHit,
    VectorRecord,
)
from lkap_api.kb.stores.lancedb import LanceDBStore, clear_store_cache, get_lancedb_store
from lkap_api.kb.stores.pgvector import PgVectorStore
from lkap_api.settings import Settings

if TYPE_CHECKING:
    from lkap_api.knowledge_connections.runtime import ConnectionRuntime
    from lkap_api.vault import Vault

__all__ = [
    "ANN_INDEX_MIN_ROWS",
    "NO_CAPABILITIES",
    "KbStoreRouter",
    "OPTIMIZE_KEEP_VERSIONS_FOR",
    "LanceDBStore",
    "PgVectorStore",
    "StoreCapabilities",
    "StoreHealth",
    "StoreKind",
    "VectorHit",
    "VectorRecord",
    "VectorStore",
    "VectorStoreConfigError",
    "clear_store_cache",
    "get_lancedb_store",
    "kb_capabilities",
    "platform_store",
    "resolve_store",
    "store_capabilities",
    "vector_store_kind",
]

#: Kept for callers that validated ids against it before V5-13.
_SAFE_ID = SAFE_ID

#: A LanceDB table with at least this many rows gets an ANN (IVF-PQ, cosine)
#: index; a smaller one is searched exhaustively, which is exact and fast enough.
ANN_INDEX_MIN_ROWS = 50_000
#: Old LanceDB table versions `optimize` keeps (a reader mid-query still sees its version).
OPTIMIZE_KEEP_VERSIONS_FOR: Final = timedelta(minutes=10)

#: The two built-in stores (``LKAP_VECTOR_STORE``).
StoreKind = Literal["lancedb", "pgvector"]


@runtime_checkable
class VectorStore(Protocol):
    """Per-knowledge-base vector storage (v2, K §5.1)."""

    @property
    def capabilities(self) -> StoreCapabilities:
        """What the store does natively (hybrid, filters, text, namespaces)."""
        ...

    async def ensure_namespace(self, kb_id: str, dimension: int) -> None:
        """Prepare the knowledge base's namespace (table, index) for ``dimension``-wide vectors."""
        ...

    async def upsert(self, kb_id: str, records: list[VectorRecord]) -> None:
        """Insert or replace vectors for the given chunk ids."""
        ...

    async def query(
        self,
        kb_id: str,
        vector: list[float],
        k: int,
        *,
        text: str | None = None,
        filters: Mapping[str, object] | None = None,
    ) -> list[VectorHit]:
        """Return the ``k`` best chunks (best first); ``text`` is used only by hybrid-capable stores."""
        ...

    async def delete_document(self, kb_id: str, document_id: str) -> None:
        """Remove every vector belonging to one document."""
        ...

    async def delete_kb(self, kb_id: str) -> None:
        """Remove the whole knowledge base's vectors (and its namespace)."""
        ...

    async def optimize(self, kb_id: str) -> None:
        """Compact the knowledge base's storage and (re)build its index when it is large enough."""
        ...

    async def health(self) -> StoreHealth:
        """Whether the store is usable, and why not."""
        ...


def store_capabilities(store: object) -> StoreCapabilities:
    """``store.capabilities``, or :data:`NO_CAPABILITIES` for a store that declares none."""
    capabilities = getattr(store, "capabilities", None)
    return capabilities if isinstance(capabilities, StoreCapabilities) else NO_CAPABILITIES


class VectorStoreConfigError(UnprocessableEntityError):
    """``LKAP_VECTOR_STORE`` names a store the configured database cannot serve."""

    code = "vector_store_misconfigured"


def _is_postgres(url: str) -> bool:
    return make_url(url).get_backend_name() == "postgresql"


def vector_store_kind(settings: Settings) -> StoreKind:
    """Which store serves this process: ``LKAP_VECTOR_STORE``, else the database's default.

    Unset (the default): ``pgvector`` when ``LKAP_DATABASE_URL`` is Postgres,
    ``lancedb`` otherwise. ``lancedb`` forces LanceDB on any database.

    Raises:
        VectorStoreConfigError: ``LKAP_VECTOR_STORE=pgvector`` with a database that is not Postgres.
    """
    postgres = _is_postgres(settings.resolved_database_url)
    match settings.vector_store:
        case "lancedb":
            return "lancedb"
        case "pgvector":
            if not postgres:
                raise VectorStoreConfigError(
                    "LKAP_VECTOR_STORE=pgvector needs a Postgres LKAP_DATABASE_URL. "
                    "Unset it (or set lancedb) to use the file-based store"
                )
            return "pgvector"
        case _:
            return "pgvector" if postgres else "lancedb"


def _platform_store(settings: Settings, session: AsyncSession | None) -> VectorStore:
    if vector_store_kind(settings) == "pgvector":
        if session is None:
            raise ValueError("the pgvector store runs on the caller's session; pass one to resolve_store")
        return PgVectorStore(session)
    return get_lancedb_store(settings.data_dir)


class KbStoreRouter:
    """Routes each call by ``kb_id``: the knowledge base's connection store, else the platform store (V5-20).

    ``capabilities`` and ``health`` are the platform store's (so the search
    pipeline treats platform-default knowledge bases exactly as before); ask
    :func:`kb_capabilities` for one knowledge base's.
    """

    def __init__(self, platform: VectorStore, runtime: ConnectionRuntime) -> None:
        """Wrap the platform store; ``runtime`` resolves knowledge bases to connection stores."""
        self._platform = platform
        self._runtime = runtime

    @property
    def platform(self) -> VectorStore:
        """The platform's own store (LanceDB or pgvector)."""
        return self._platform

    @property
    def runtime(self) -> ConnectionRuntime:
        """The session's knowledge-connection runtime (hosted re-rankers are built from it too)."""
        return self._runtime

    @property
    def capabilities(self) -> StoreCapabilities:
        """The platform store's capabilities."""
        return store_capabilities(self._platform)

    async def store_for(self, kb_id: str) -> VectorStore:
        """The store holding ``kb_id`` (raises when its connection cannot be used)."""
        store = await self._runtime.store_for_kb(kb_id)
        return store if store is not None else self._platform

    async def ensure_namespace(self, kb_id: str, dimension: int) -> None:
        """Prepare ``kb_id``'s namespace in its store."""
        await (await self.store_for(kb_id)).ensure_namespace(kb_id, dimension)

    async def upsert(self, kb_id: str, records: list[VectorRecord]) -> None:
        """Upsert into ``kb_id``'s store."""
        await (await self.store_for(kb_id)).upsert(kb_id, records)

    async def query(
        self,
        kb_id: str,
        vector: list[float],
        k: int,
        *,
        text: str | None = None,
        filters: Mapping[str, object] | None = None,
    ) -> list[VectorHit]:
        """Query ``kb_id``'s store."""
        store = await self.store_for(kb_id)
        if text is None and filters is None:
            return await store.query(kb_id, vector, k)
        return await store.query(kb_id, vector, k, text=text, filters=filters)

    async def delete_document(self, kb_id: str, document_id: str) -> None:
        """Delete one document's vectors from ``kb_id``'s store."""
        await (await self.store_for(kb_id)).delete_document(kb_id, document_id)

    async def delete_kb(self, kb_id: str) -> None:
        """Delete ``kb_id``'s vectors from its store."""
        await (await self.store_for(kb_id)).delete_kb(kb_id)

    async def optimize(self, kb_id: str) -> None:
        """Compact ``kb_id``'s store (a no-op for the connection stores)."""
        await (await self.store_for(kb_id)).optimize(kb_id)

    async def health(self) -> StoreHealth:
        """The platform store's health (a connection's is its ``Test connection``)."""
        return await self._platform.health()


def platform_store(store: VectorStore) -> VectorStore:
    """The platform store behind a :class:`KbStoreRouter` (any other store is returned as is)."""
    return store.platform if isinstance(store, KbStoreRouter) else store


async def kb_capabilities(store: VectorStore, kb_id: str) -> StoreCapabilities:
    """What the store holding ``kb_id`` does natively.

    For a router, the connection store's capabilities (a knowledge connection
    declares native hybrid only when it is switched on for it); otherwise the
    store's own. A knowledge base whose connection cannot be used reports
    :data:`NO_CAPABILITIES` here (the query itself then fails for it).
    """
    if not isinstance(store, KbStoreRouter):
        return store_capabilities(store)
    try:
        return store_capabilities(await store.store_for(kb_id))
    except Exception:  # noqa: BLE001 - reported by the query that follows
        return NO_CAPABILITIES


def resolve_store(
    settings: Settings, session: AsyncSession | None = None, *, vault: Vault | None = None
) -> VectorStore:
    """The vector store for this process's configuration.

    Args:
        settings: Supplies ``LKAP_VECTOR_STORE``, the database url and the data dir.
        session: The caller's session. Required for pgvector, whose statements
            run in that session's transaction (so vectors commit with the chunk
            rows). With a session the result is a :class:`KbStoreRouter` that
            also serves knowledge bases stored through a knowledge connection
            (V5-20). Build one store per session.
        vault: Decrypts connection keys; defaults to one over ``LKAP_MASTER_KEY``.

    Returns:
        A :class:`KbStoreRouter` over the platform store when ``session`` is
        given; the process-wide :class:`LanceDBStore` otherwise.

    Raises:
        VectorStoreConfigError: See :func:`vector_store_kind`.
        ValueError: pgvector was chosen but no session was given (a programming error).
    """
    platform = _platform_store(settings, session)
    if session is None:
        return platform
    from lkap_api.knowledge_connections.runtime import ConnectionRuntime

    return KbStoreRouter(platform, ConnectionRuntime(settings, session, vault))
