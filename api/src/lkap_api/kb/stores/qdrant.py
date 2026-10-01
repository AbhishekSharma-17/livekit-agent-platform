"""`QdrantStore`: knowledge base vectors in a customer's Qdrant cluster (V5-20, D-V5-16, D-V5-37).

REST on the cluster url (Qdrant Cloud or self-hosted, port 6333), header
``api-key`` when the connection has a key; verified 2026-09-27 against
qdrant.tech/documentation (collections, indexing, hybrid queries) and the
OpenAPI spec. Every call goes through the caller's guarded client
(:mod:`lkap_api.knowledge_connections.http`).

**Layout.** One collection per connection (``settings.collection``), created on
first use with a named dense vector ``dense`` (the knowledge base's width,
``Cosine``) and a sparse vector ``bm25`` (``modifier: idf``), plus keyword
payload indexes on ``kb_id`` (``is_tenant: true``, Qdrant's multitenancy
layout; per-tenant collections are discouraged), ``workspace_id`` and
``document_id``. A point is ``{id: <chunk id as a dashed UUID>, vector,
payload: {kb_id, workspace_id, document_id}}``: no chunk text (D-V5-37; the
search joins the SQL rows). Every query and delete filters on **both**
``kb_id`` and ``workspace_id``. The collection's dense width is fixed: a
knowledge base of another width is refused with a message naming the
collection.

**Hybrid.** When the connection's ``native_hybrid`` is on and a record carries
its text, the point also gets a BM25 sparse vector from fastembed's
``Qdrant/bm25`` (``embed`` for documents, ``query_embed`` for queries; IDF is
applied by Qdrant through the modifier), and ``query(text=...)`` runs one
``points/query`` with a dense and a sparse ``prefetch`` fused by RRF. RRF
scores only order the list, so they are normalised by the best one (the
first hit scores 1.0), like :func:`lkap_api.kb.search.fuse_rrf`. With it off,
the store is dense-only and the search pipeline fuses its own keyword list.

Writes wait for Qdrant (``?wait=true``), treat anything but ``completed`` as a
failure, and retry transient errors; queries never retry.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterator, Mapping, Sequence
from typing import Any, Final, Protocol

import httpx

from lkap_api.kb.stores import (
    StoreCapabilities,
    StoreHealth,
    VectorHit,
    VectorRecord,
    as_uuid,
    check_safe_id,
    document_filter,
    from_uuid,
)
from lkap_api.knowledge_connections.http import ConnectorError, VendorHttp
from lkap_api.knowledge_connections.settings import QdrantSettings
from lkap_api.logging import get_logger

log = get_logger(__name__)

DENSE: Final = "dense"
SPARSE: Final = "bm25"
#: Points per upsert request.
UPSERT_BATCH: Final = 64
#: Retries of a write on a transient failure.
WRITE_RETRIES: Final = 2
#: Candidates each prefetch of a hybrid query returns.
PREFETCH_LIMIT: Final = 20
#: The fastembed sparse model (BM25 term weights; IDF on the server).
BM25_MODEL: Final = "Qdrant/bm25"


class SparseEncoder(Protocol):
    """Turns text into BM25 sparse vectors (``(indices, values)``)."""

    async def embed_documents(self, texts: Sequence[str]) -> list[tuple[list[int], list[float]]]:
        """Document-side vectors (term-frequency weights)."""
        ...

    async def embed_query(self, text: str) -> tuple[list[int], list[float]]:
        """Query-side vector (every token weighted 1.0)."""
        ...


class FastembedBm25:
    """fastembed's ``SparseTextEmbedding("Qdrant/bm25")``, loaded lazily off the event loop."""

    def __init__(self, cache_dir: str) -> None:
        """Create the encoder (nothing is loaded until the first call)."""
        self._cache_dir = cache_dir
        self._model: Any = None
        self._lock = asyncio.Lock()

    async def _get(self) -> Any:
        if self._model is None:
            async with self._lock:
                if self._model is None:
                    from fastembed import SparseTextEmbedding

                    self._model = await asyncio.to_thread(
                        SparseTextEmbedding, model_name=BM25_MODEL, cache_dir=self._cache_dir
                    )
        return self._model

    async def embed_documents(self, texts: Sequence[str]) -> list[tuple[list[int], list[float]]]:
        """BM25 document vectors."""
        model = await self._get()
        items = await asyncio.to_thread(lambda: list(model.embed(list(texts))))
        return [(item.indices.tolist(), item.values.tolist()) for item in items]

    async def embed_query(self, text: str) -> tuple[list[int], list[float]]:
        """The BM25 query vector."""
        model = await self._get()
        items = await asyncio.to_thread(lambda: list(model.query_embed(text)))
        return (items[0].indices.tolist(), items[0].values.tolist()) if items else ([], [])


_ENCODERS: dict[str, SparseEncoder] = {}
_ENCODER_OVERRIDE: list[SparseEncoder] = []


def sparse_encoder(cache_dir: str) -> SparseEncoder:
    """The process-wide BM25 encoder for ``cache_dir`` (``LKAP_DATA_DIR/models``)."""
    if _ENCODER_OVERRIDE:
        return _ENCODER_OVERRIDE[-1]
    if cache_dir not in _ENCODERS:
        _ENCODERS[cache_dir] = FastembedBm25(cache_dir)
    return _ENCODERS[cache_dir]


@contextlib.contextmanager
def use_sparse_encoder(encoder: SparseEncoder) -> Iterator[None]:
    """Use ``encoder`` for every Qdrant store built meanwhile (tests: no model download)."""
    _ENCODER_OVERRIDE.append(encoder)
    try:
        yield
    finally:
        _ENCODER_OVERRIDE.pop()


def _must(kb_id: str, workspace_id: str, documents: list[str] | None = None) -> dict[str, Any]:
    conditions: list[dict[str, Any]] = [
        {"key": "kb_id", "match": {"value": check_safe_id(kb_id, what="knowledge base id")}},
        {"key": "workspace_id", "match": {"value": check_safe_id(workspace_id, what="workspace id")}},
    ]
    if documents is not None:
        conditions.append({"key": "document_id", "match": {"any": documents}})
    return {"must": conditions}


def dense_config(collection_info: Mapping[str, Any]) -> tuple[int | None, str | None]:
    """The dense width and distance of a ``GET /collections/{name}`` result (named or unnamed vector)."""
    config = collection_info.get("config")
    params = config.get("params") if isinstance(config, Mapping) else None
    vectors = params.get("vectors") if isinstance(params, Mapping) else None
    if not isinstance(vectors, Mapping):
        return None, None
    named = vectors.get(DENSE)
    spec = named if isinstance(named, Mapping) else vectors
    size = spec.get("size")
    distance = spec.get("distance")
    return (size if isinstance(size, int) else None), (distance if isinstance(distance, str) else None)


class QdrantStore:
    """:class:`~lkap_api.kb.store.VectorStore` over one Qdrant collection (see the module docstring)."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        settings: QdrantSettings,
        *,
        api_key: str | None,
        workspace_id: str,
        sparse: SparseEncoder | None = None,
    ) -> None:
        """Bind the store to a cluster, a collection and the connection's workspace."""
        headers = {"api-key": api_key} if api_key else {}
        self._http = VendorHttp(client, vendor="Qdrant", base_url=settings.url, headers=headers)
        self._settings = settings
        self._collection = settings.collection
        self._workspace_id = workspace_id
        self._sparse = sparse if settings.native_hybrid else None
        self._ready_width: int | None = None
        self.capabilities = StoreCapabilities(
            hybrid=settings.native_hybrid, filters=True, stores_text=False, namespaces=False
        )

    @property
    def collection(self) -> str:
        """The collection this store writes to."""
        return self._collection

    # ------------------------------------------------------------------ collection
    async def collection_info(self) -> Mapping[str, Any] | None:
        """``GET /collections/{name}``'s result, or ``None`` when the collection does not exist."""
        try:
            body = await self._http.call("GET", f"/collections/{self._collection}")
        except ConnectorError as exc:
            if exc.not_found:
                return None
            raise
        result = body.get("result") if isinstance(body, Mapping) else None
        return result if isinstance(result, Mapping) else {}

    async def list_collections(self) -> list[str]:
        """Every collection the key can see."""
        body = await self._http.call("GET", "/collections")
        result = body.get("result") if isinstance(body, Mapping) else None
        items = result.get("collections") if isinstance(result, Mapping) else None
        return sorted(
            str(item["name"]) for item in items or [] if isinstance(item, Mapping) and "name" in item
        )

    async def version(self) -> str | None:
        """The cluster's version (``GET /``)."""
        body = await self._http.call("GET", "/")
        value = body.get("version") if isinstance(body, Mapping) else None
        return str(value) if value is not None else None

    async def ensure_namespace(self, kb_id: str, dimension: int) -> None:
        """Create the collection (and its payload indexes) at ``dimension``, or check its width.

        Raises:
            ConnectorError: The collection holds vectors of another width or distance.
        """
        check_safe_id(kb_id, what="knowledge base id")
        if self._ready_width == dimension:
            return
        info = await self.collection_info()
        if info is None:
            await self._http.call(
                "PUT",
                f"/collections/{self._collection}",
                json={
                    "vectors": {DENSE: {"size": dimension, "distance": "Cosine"}},
                    "sparse_vectors": {SPARSE: {"modifier": "idf"}},
                },
                retries=WRITE_RETRIES,
            )
            for field, schema in (
                ("kb_id", {"type": "keyword", "is_tenant": True}),
                ("workspace_id", {"type": "keyword"}),
                ("document_id", {"type": "keyword"}),
            ):
                await self._http.call(
                    "PUT",
                    f"/collections/{self._collection}/index",
                    params={"wait": "true"},
                    json={"field_name": field, "field_schema": schema},
                    retries=WRITE_RETRIES,
                )
            log.info("qdrant_collection_created", dimension=dimension)
        else:
            width, distance = dense_config(info)
            if width != dimension:
                raise ConnectorError(
                    f"Qdrant collection '{self._collection}' holds {width}-dimension vectors, but this "
                    f"knowledge base's embedder makes {dimension}-dimension vectors"
                )
            if distance is not None and distance != "Cosine":
                raise ConnectorError(
                    f"Qdrant collection '{self._collection}' uses {distance} distance. It must use Cosine"
                )
        self._ready_width = dimension

    # ------------------------------------------------------------------ writes
    async def upsert(self, kb_id: str, records: list[VectorRecord]) -> None:
        """Upsert the records as points of ``kb_id`` (the collection is created on first use)."""
        if not records:
            return
        await self.ensure_namespace(kb_id, len(records[0].vector))
        for start in range(0, len(records), UPSERT_BATCH):
            batch = records[start : start + UPSERT_BATCH]
            sparse: list[tuple[list[int], list[float]] | None] = [None] * len(batch)
            texted = [index for index, record in enumerate(batch) if record.text]
            if self._sparse is not None and texted:
                vectors = await self._sparse.embed_documents([batch[i].text or "" for i in texted])
                for index, pair in zip(texted, vectors, strict=True):
                    sparse[index] = pair
            points = []
            for record, sparse_vector in zip(batch, sparse, strict=True):
                named: dict[str, Any] = {DENSE: record.vector}
                if sparse_vector is not None and sparse_vector[0]:
                    named[SPARSE] = {"indices": sparse_vector[0], "values": sparse_vector[1]}
                points.append(
                    {
                        "id": as_uuid(record.id),
                        "vector": named,
                        "payload": {
                            "kb_id": kb_id,
                            "workspace_id": self._workspace_id,
                            "document_id": check_safe_id(record.document_id, what="document id"),
                        },
                    }
                )
            body = await self._http.call(
                "PUT",
                f"/collections/{self._collection}/points",
                params={"wait": "true"},
                json={"points": points},
                retries=WRITE_RETRIES,
            )
            self._check_completed(body)

    @staticmethod
    def _check_completed(body: Any) -> None:
        result = body.get("result") if isinstance(body, Mapping) else None
        status = result.get("status") if isinstance(result, Mapping) else None
        if status is not None and status != "completed":
            raise ConnectorError(f"Qdrant did not complete the write in time ({status})", retryable=True)

    async def _delete(self, selector: dict[str, Any]) -> None:
        try:
            body = await self._http.call(
                "POST",
                f"/collections/{self._collection}/points/delete",
                params={"wait": "true"},
                json={"filter": selector},
                retries=WRITE_RETRIES,
            )
        except ConnectorError as exc:
            if exc.not_found:  # no collection: nothing to delete
                return
            raise
        self._check_completed(body)

    async def delete_document(self, kb_id: str, document_id: str) -> None:
        """Delete one document's points."""
        await self._delete(_must(kb_id, self._workspace_id, [check_safe_id(document_id, what="document id")]))

    async def delete_kb(self, kb_id: str) -> None:
        """Delete every point of ``kb_id`` (the collection stays: other knowledge bases share it)."""
        await self._delete(_must(kb_id, self._workspace_id))

    async def optimize(self, kb_id: str) -> None:
        """A no-op: Qdrant optimizes its segments itself."""

    # ------------------------------------------------------------------ reads
    async def query(
        self,
        kb_id: str,
        vector: list[float],
        k: int,
        *,
        text: str | None = None,
        filters: Mapping[str, object] | None = None,
    ) -> list[VectorHit]:
        """The ``k`` best points of ``kb_id``: dense, or dense + BM25 fused by RRF (native hybrid)."""
        selector = _must(kb_id, self._workspace_id, document_filter(filters))
        limit = max(k, 1)
        hybrid = text is not None and text.strip() != "" and self._sparse is not None
        if hybrid and self._sparse is not None and text is not None:
            indices, values = await self._sparse.embed_query(text)
            body_json: dict[str, Any] = {
                "prefetch": [
                    {
                        "query": vector,
                        "using": DENSE,
                        "limit": max(limit, PREFETCH_LIMIT),
                        "filter": selector,
                    },
                    {
                        "query": {"indices": indices, "values": values},
                        "using": SPARSE,
                        "limit": max(limit, PREFETCH_LIMIT),
                        "filter": selector,
                    },
                ],
                "query": {"fusion": "rrf"},
                "filter": selector,
                "limit": limit,
                "with_payload": True,
            }
        else:
            body_json = {
                "query": vector,
                "using": DENSE,
                "filter": selector,
                "limit": limit,
                "with_payload": True,
            }
        try:
            body = await self._http.call(
                "POST", f"/collections/{self._collection}/points/query", json=body_json
            )
        except ConnectorError as exc:
            if exc.not_found:  # nothing ingested yet
                return []
            raise
        result = body.get("result") if isinstance(body, Mapping) else None
        points = result.get("points") if isinstance(result, Mapping) else result
        hits: list[VectorHit] = []
        for point in points if isinstance(points, list) else []:
            if not isinstance(point, Mapping):
                continue
            payload = point.get("payload") if isinstance(point.get("payload"), Mapping) else {}
            assert isinstance(payload, Mapping)
            if payload.get("kb_id") != kb_id or payload.get("workspace_id") != self._workspace_id:
                continue  # defence in depth: the filter already guarantees it
            hits.append(
                VectorHit(
                    id=from_uuid(str(point.get("id"))),
                    document_id=str(payload.get("document_id", "")),
                    score=float(point.get("score") or 0.0),
                    fused=hybrid,
                )
            )
        if hybrid and hits:
            top = max(hit.score for hit in hits) or 1.0
            hits = [
                VectorHit(id=hit.id, document_id=hit.document_id, score=hit.score / top, fused=True)
                for hit in hits
            ]
        return hits

    async def health(self) -> StoreHealth:
        """Whether the cluster answers (``GET /``)."""
        try:
            version = await self.version()
        except ConnectorError as exc:
            return StoreHealth(ok=False, backend="qdrant", detail=exc.message)
        return StoreHealth(ok=True, backend="qdrant", version=version)
