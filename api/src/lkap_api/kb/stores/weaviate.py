"""`WeaviateStore`: knowledge base vectors in a customer's Weaviate cluster (V5-20, D-V5-16).

REST and GraphQL on the cluster url, ``Authorization: Bearer <key>`` when the
connection has a key; verified 2026-09-27 against docs.weaviate.io (schema,
multi-tenancy, batch, GraphQL search operators, distances) and the OpenAPI
spec. Every call goes through the caller's guarded client.

**Layout.** One collection per connection (``settings.collection``, capitalised),
created on first use with ``vectorizer: none`` (the platform supplies the
vectors; HNSW with cosine distance, the default) and multi-tenancy on
(``autoTenantCreation`` too); **one tenant per knowledge base** (``kb_<id>``:
Weaviate's native per-tenant shard, so one knowledge base's objects are never
in another's index). Objects are ``{id: <chunk id as a dashed UUID>, vector,
properties: {chunk_id, kb_id, document_id}}``; with ``native_hybrid`` on, the
embedded ``text`` is stored too, for BM25 only (the SQL row stays the source
of truth, D-V5-37). Weaviate fixes no width per collection, so the dimension
check is "not enforced" and a wrong width fails at insert.

**Queries** are GraphQL. Weaviate documents no GraphQL variables for search
operator arguments, so values are written into the query text: every string
through ``json.dumps`` (valid GraphQL string syntax), floats through
``json.dumps``, tenant and property names only from a checked alphabet, and
enums (``rankedFusion``, ``Equal``, ``Or``) as fixed literals. ``nearVector``
answers ``_additional.distance`` (cosine distance, ``1 - similarity``);
``hybrid`` (``fusionType: rankedFusion``, ``alpha: 0.5``) answers
``_additional.score`` as a **string** whose order is all that matters, so it is
normalised by the best one. GraphQL and batch calls answer HTTP 200 even when
they fail: the ``errors`` arrays are checked.

Deletes: a document is a batch delete with a ``where`` filter and
``?tenant=`` (at most 10,000 objects per call, so it loops); a knowledge base
is its tenant's deletion.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any, Final

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
from lkap_api.knowledge_connections.http import ConnectorError, VendorHttp, scrub
from lkap_api.knowledge_connections.settings import WeaviateSettings, kb_namespace
from lkap_api.logging import get_logger

log = get_logger(__name__)

#: Objects per batch insert.
INSERT_BATCH: Final = 100
#: Retries of a write on a transient failure.
WRITE_RETRIES: Final = 2
#: Batch-delete rounds for one document (each removes up to 10,000 objects).
MAX_DELETE_ROUNDS: Final = 20
#: The weight of the vector list in a hybrid query (0.5 = keyword and vector equally).
HYBRID_ALPHA: Final = 0.5
_TENANT_RE: Final = re.compile(r"^[A-Za-z0-9_-]{4,64}$")

PROPERTIES: Final = [
    {"name": "chunk_id", "dataType": ["text"], "tokenization": "field", "indexSearchable": False},
    {"name": "kb_id", "dataType": ["text"], "tokenization": "field", "indexSearchable": False},
    {"name": "document_id", "dataType": ["text"], "tokenization": "field", "indexSearchable": False},
    {"name": "text", "dataType": ["text"], "tokenization": "word", "indexFilterable": False},
]


def tenant_for(kb_id: str) -> str:
    """The tenant of a knowledge base (checked against Weaviate's tenant-name rule)."""
    tenant = kb_namespace(check_safe_id(kb_id, what="knowledge base id"))
    if not _TENANT_RE.match(tenant):
        raise ValueError(f"unusable Weaviate tenant name: {tenant!r}")
    return tenant


def _graphql_errors(body: Any) -> list[str]:
    errors = body.get("errors") if isinstance(body, Mapping) else None
    return [str(error.get("message", "")) for error in errors or [] if isinstance(error, Mapping)]


class WeaviateStore:
    """:class:`~lkap_api.kb.store.VectorStore` over one multi-tenant Weaviate collection."""

    def __init__(self, client: httpx.AsyncClient, settings: WeaviateSettings, *, api_key: str | None) -> None:
        """Bind the store to a cluster and a collection; nothing is called until first use."""
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._http = VendorHttp(client, vendor="Weaviate", base_url=settings.url, headers=headers)
        self._settings = settings
        self._collection = settings.collection
        self._native_hybrid = settings.native_hybrid
        self._collection_ready = False
        self._tenants: set[str] = set()
        self.capabilities = StoreCapabilities(
            hybrid=settings.native_hybrid, filters=True, stores_text=False, namespaces=True
        )

    @property
    def collection(self) -> str:
        """The collection this store writes to."""
        return self._collection

    # ------------------------------------------------------------------ schema
    async def schema(self) -> Mapping[str, Any] | None:
        """``GET /v1/schema/{Class}``, or ``None`` when the collection does not exist."""
        try:
            body = await self._http.call("GET", f"/v1/schema/{self._collection}")
        except ConnectorError as exc:
            if exc.not_found:
                return None
            raise
        return body if isinstance(body, Mapping) else {}

    async def list_collections(self) -> list[str]:
        """Every collection the key can see."""
        body = await self._http.call("GET", "/v1/schema")
        classes = body.get("classes") if isinstance(body, Mapping) else None
        return sorted(
            str(item["class"]) for item in classes or [] if isinstance(item, Mapping) and "class" in item
        )

    async def version(self) -> str | None:
        """The cluster's version (``GET /v1/meta``)."""
        body = await self._http.call("GET", "/v1/meta")
        value = body.get("version") if isinstance(body, Mapping) else None
        return str(value) if value is not None else None

    async def _ensure_collection(self) -> None:
        if self._collection_ready:
            return
        schema = await self.schema()
        if schema is None:
            await self._http.call(
                "POST",
                "/v1/schema",
                json={
                    "class": self._collection,
                    "vectorizer": "none",
                    "multiTenancyConfig": {"enabled": True, "autoTenantCreation": True},
                    "properties": PROPERTIES,
                },
                retries=WRITE_RETRIES,
            )
            log.info("weaviate_collection_created")
        else:
            config = schema.get("multiTenancyConfig")
            if not (isinstance(config, Mapping) and config.get("enabled") is True):
                raise ConnectorError(
                    f"Weaviate collection '{self._collection}' does not have multi-tenancy enabled; "
                    "use a new collection name and the platform creates it"
                )
        self._collection_ready = True

    async def ensure_namespace(self, kb_id: str, dimension: int) -> None:
        """Create the collection (multi-tenant) and ``kb_id``'s tenant when missing."""
        tenant = tenant_for(kb_id)
        await self._ensure_collection()
        if tenant in self._tenants:
            return
        try:
            await self._http.call(
                "POST",
                f"/v1/schema/{self._collection}/tenants",
                json=[{"name": tenant}],
                retries=WRITE_RETRIES,
            )
        except ConnectorError as exc:
            if exc.status != 422:  # 422: the tenant already exists
                raise
        self._tenants.add(tenant)

    # ------------------------------------------------------------------ writes
    async def upsert(self, kb_id: str, records: list[VectorRecord]) -> None:
        """Insert or replace the records as objects of ``kb_id``'s tenant."""
        if not records:
            return
        tenant = tenant_for(kb_id)
        await self.ensure_namespace(kb_id, len(records[0].vector))
        for start in range(0, len(records), INSERT_BATCH):
            objects = []
            for record in records[start : start + INSERT_BATCH]:
                properties: dict[str, Any] = {
                    "chunk_id": check_safe_id(record.id),
                    "kb_id": kb_id,
                    "document_id": check_safe_id(record.document_id, what="document id"),
                }
                if self._native_hybrid and record.text:
                    properties["text"] = record.text
                objects.append(
                    {
                        "class": self._collection,
                        "id": as_uuid(record.id),
                        "vector": record.vector,
                        "properties": properties,
                        "tenant": tenant,
                    }
                )
            body = await self._http.call(
                "POST", "/v1/batch/objects", json={"objects": objects}, retries=WRITE_RETRIES
            )
            failures = [
                error.get("message", "")
                for item in (body if isinstance(body, list) else [])
                if isinstance(item, Mapping)
                for error in (((item.get("result") or {}).get("errors") or {}).get("error") or [])
                if isinstance(error, Mapping)
            ]
            if failures:
                raise ConnectorError(f"Weaviate refused {len(failures)} object(s): {scrub(failures[0])}")

    async def delete_document(self, kb_id: str, document_id: str) -> None:
        """Delete one document's objects from ``kb_id``'s tenant."""
        tenant = tenant_for(kb_id)
        where = {
            "path": ["document_id"],
            "operator": "Equal",
            "valueText": check_safe_id(document_id, what="document id"),
        }
        for _round in range(MAX_DELETE_ROUNDS):
            try:
                body = await self._http.call(
                    "DELETE",
                    "/v1/batch/objects",
                    params={"tenant": tenant},
                    json={"match": {"class": self._collection, "where": where}, "output": "minimal"},
                    retries=WRITE_RETRIES,
                )
            except ConnectorError as exc:
                if exc.not_found or exc.status == 422:  # no collection or no tenant: nothing to delete
                    return
                raise
            results = body.get("results") if isinstance(body, Mapping) else None
            matches = results.get("matches") if isinstance(results, Mapping) else 0
            limit = results.get("limit") if isinstance(results, Mapping) else None
            if not isinstance(matches, int) or matches == 0 or (isinstance(limit, int) and matches < limit):
                return

    async def delete_kb(self, kb_id: str) -> None:
        """Delete ``kb_id``'s tenant with its objects (the collection stays)."""
        tenant = tenant_for(kb_id)
        await self._http.call(
            "DELETE",
            f"/v1/schema/{self._collection}/tenants",
            json=[tenant],
            retries=WRITE_RETRIES,
            ok_statuses=frozenset({404, 422}),
        )
        self._tenants.discard(tenant)

    async def optimize(self, kb_id: str) -> None:
        """A no-op: Weaviate maintains its indexes itself."""

    # ------------------------------------------------------------------ reads
    def _graphql(
        self, tenant: str, vector: list[float], k: int, *, text: str | None, documents: list[str] | None
    ) -> str:
        vector_literal = json.dumps([float(value) for value in vector])
        if text is not None:
            search = (
                f"hybrid: {{query: {json.dumps(text, ensure_ascii=False)}, vector: {vector_literal}, "
                f"alpha: {HYBRID_ALPHA}, fusionType: rankedFusion}}"
            )
            extra = "score"
        else:
            search = f"nearVector: {{vector: {vector_literal}}}"
            extra = "distance"
        where = ""
        if documents is not None:
            operands = ", ".join(
                f'{{path: ["document_id"], operator: Equal, valueText: {json.dumps(document)}}}'
                for document in documents
            )
            where = f", where: {{operator: Or, operands: [{operands}]}}"
        return (
            f"{{ Get {{ {self._collection}(tenant: {json.dumps(tenant)}, {search}{where}, "
            f"limit: {max(k, 1)}) "
            f"{{ chunk_id kb_id document_id _additional {{ id {extra} }} }} }} }}"
        )

    async def query(
        self,
        kb_id: str,
        vector: list[float],
        k: int,
        *,
        text: str | None = None,
        filters: Mapping[str, object] | None = None,
    ) -> list[VectorHit]:
        """The ``k`` best objects of ``kb_id``'s tenant: ``nearVector``, or ``hybrid`` (native hybrid)."""
        documents = document_filter(filters)
        tenant = tenant_for(kb_id)
        hybrid = text is not None and text.strip() != "" and self._native_hybrid
        query = self._graphql(tenant, vector, k, text=text if hybrid else None, documents=documents)
        try:
            body = await self._http.call("POST", "/v1/graphql", json={"query": query})
        except ConnectorError as exc:
            if exc.not_found:
                return []
            raise
        errors = _graphql_errors(body)
        if errors:
            lowered = " ".join(errors).lower()
            if ("tenant" in lowered and "not found" in lowered) or "could not find class" in lowered:
                return []  # nothing ingested into this knowledge base yet
            raise ConnectorError(f"Weaviate refused the search: {scrub(errors[0])}")
        data = body.get("data") if isinstance(body, Mapping) else None
        get = data.get("Get") if isinstance(data, Mapping) else None
        rows = get.get(self._collection) if isinstance(get, Mapping) else None
        hits: list[VectorHit] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, Mapping) or row.get("kb_id") not in (None, kb_id):
                continue
            additional = row.get("_additional") if isinstance(row.get("_additional"), Mapping) else {}
            assert isinstance(additional, Mapping)
            chunk_id = row.get("chunk_id") or from_uuid(str(additional.get("id", "")))
            if hybrid:
                score = float(additional.get("score") or 0.0)
            else:
                score = 1.0 - float(additional.get("distance") or 0.0)
            hits.append(
                VectorHit(
                    id=str(chunk_id), document_id=str(row.get("document_id", "")), score=score, fused=hybrid
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
        """Whether the cluster answers (``GET /v1/meta``)."""
        try:
            version = await self.version()
        except ConnectorError as exc:
            return StoreHealth(ok=False, backend="weaviate", detail=exc.message)
        return StoreHealth(ok=True, backend="weaviate", version=version)
