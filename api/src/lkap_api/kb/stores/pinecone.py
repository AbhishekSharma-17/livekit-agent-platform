"""`PineconeStore`: knowledge base vectors in a customer's Pinecone serverless index (V5-20, D-V5-16).

REST, headers ``Api-Key`` and ``X-Pinecone-Api-Version: 2026-04`` (verified
2026-09-27 on docs.pinecone.io: the newest stable version that keeps the
``dimension``/``metric``/``spec`` index shape; ``2026-07`` replaced it with a
schema document). Every call goes through the caller's guarded client.

**Layout.** One index per connection (``settings.index``), one **namespace per
knowledge base** (``kb_<id>``; Pinecone's "one namespace per customer"
layout; the free Starter plan allows 100 per index). The index is created on
first use as a dense serverless index (``cosine``, the knowledge base's width,
``settings.cloud``/``settings.region``); an existing index of another width or
metric is refused with a message naming it. Records are ``{id: <chunk id>,
values, metadata: {kb_id, document_id}}``: no chunk text (D-V5-37).

**The data-plane host** comes from the index description. It is vendor data,
so it must be ``https`` and end in ``.pinecone.io`` before anything is sent to
it (the MCP discovery rule, §4 row 8); the guarded client then checks the
resolved address like every other call.

**Hybrid.** Not native here: a Pinecone lexical index needs a separate sparse
index (or a ``dotproduct`` dense one), so this store is dense-only and the
search pipeline fuses its own keyword list (the text is in SQL) by rank.

Limits honoured: at most 1,000 records and 2 MB per upsert (batches sized by
width), delete by metadata filter per document, ``DELETE
/namespaces/{namespace}`` for a whole knowledge base (a namespace that does
not exist answers 404, which counts as done).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, Final
from urllib.parse import urlsplit

import httpx

from lkap_api.kb.stores import (
    StoreCapabilities,
    StoreHealth,
    VectorHit,
    VectorRecord,
    check_safe_id,
    document_filter,
)
from lkap_api.knowledge_connections.http import ConnectorError, VendorHttp
from lkap_api.knowledge_connections.settings import PineconeSettings, kb_namespace
from lkap_api.logging import get_logger

log = get_logger(__name__)

CONTROL_URL: Final = "https://api.pinecone.io"
API_VERSION: Final = "2026-04"
#: The only host suffix a data-plane host may have.
HOST_SUFFIX: Final = ".pinecone.io"
#: Pinecone's per-request caps.
MAX_UPSERT_RECORDS: Final = 1000
MAX_UPSERT_BYTES: Final = 2_000_000
#: Retries of a write on a transient failure.
WRITE_RETRIES: Final = 2
#: How long a newly created index may take to become ready.
READY_TIMEOUT_S: Final = 60.0
READY_POLL_S: Final = 1.0

PINECONE_CAPABILITIES: Final = StoreCapabilities(
    hybrid=False, filters=True, stores_text=False, namespaces=True
)


def batch_size(dimension: int) -> int:
    """Records per upsert so a request stays well under 2 MB (about 12 bytes per float in JSON)."""
    return max(1, min(MAX_UPSERT_RECORDS, 100, (MAX_UPSERT_BYTES // 2) // max(1, dimension * 12)))


def check_host(host: object) -> str:
    """The data-plane base url for a described ``host``.

    Raises:
        ConnectorError: The host is missing or not a Pinecone host.
    """
    if not isinstance(host, str) or not host:
        raise ConnectorError("Pinecone did not return the index's address yet")
    candidate = host if "://" in host else f"https://{host}"
    parsed = urlsplit(candidate)
    name = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not name.endswith(HOST_SUFFIX) or parsed.path not in ("", "/"):
        raise ConnectorError("Pinecone returned an index address outside pinecone.io. Refusing to use it")
    return f"https://{name}" + (f":{parsed.port}" if parsed.port else "")


class PineconeStore:
    """:class:`~lkap_api.kb.store.VectorStore` over one Pinecone index (see the module docstring)."""

    capabilities: StoreCapabilities = PINECONE_CAPABILITIES

    def __init__(self, client: httpx.AsyncClient, settings: PineconeSettings, *, api_key: str) -> None:
        """Bind the store to an index; nothing is called until first use."""
        self._client = client
        self._settings = settings
        self._headers = {"Api-Key": api_key, "X-Pinecone-Api-Version": API_VERSION}
        self._control = VendorHttp(client, vendor="Pinecone", base_url=CONTROL_URL, headers=self._headers)
        self._data: VendorHttp | None = None
        self._dimension: int | None = None

    @property
    def index(self) -> str:
        """The index this store writes to."""
        return self._settings.index

    # ------------------------------------------------------------------ index
    async def describe(self) -> Mapping[str, Any] | None:
        """``GET /indexes/{name}``, or ``None`` when the index does not exist."""
        try:
            body = await self._control.call("GET", f"/indexes/{self.index}")
        except ConnectorError as exc:
            if exc.not_found:
                return None
            raise
        return body if isinstance(body, Mapping) else {}

    async def list_indexes(self) -> list[Mapping[str, Any]]:
        """Every index of the key's project (name, dimension, metric)."""
        body = await self._control.call("GET", "/indexes")
        items = body.get("indexes") if isinstance(body, Mapping) else None
        return [item for item in items or [] if isinstance(item, Mapping)]

    def _check(self, description: Mapping[str, Any], dimension: int | None) -> None:
        width = description.get("dimension")
        metric = description.get("metric")
        if dimension is not None and isinstance(width, int) and width != dimension:
            raise ConnectorError(
                f"Pinecone index '{self.index}' holds {width}-dimension vectors, but this knowledge base's "
                f"embedder makes {dimension}-dimension vectors"
            )
        if isinstance(metric, str) and metric != "cosine":
            raise ConnectorError(
                f"Pinecone index '{self.index}' uses the {metric} metric. It must use cosine"
            )

    def _bind(self, description: Mapping[str, Any]) -> VendorHttp:
        base = check_host(description.get("host"))
        self._data = VendorHttp(self._client, vendor="Pinecone", base_url=base, headers=self._headers)
        width = description.get("dimension")
        self._dimension = width if isinstance(width, int) else None
        return self._data

    async def _data_plane(self, *, create_with: int | None = None) -> VendorHttp | None:
        """The data-plane helper; the index is created at ``create_with`` dimensions when missing."""
        if self._data is not None:
            if create_with is not None and self._dimension is not None and self._dimension != create_with:
                self._check({"dimension": self._dimension}, create_with)
            return self._data
        description = await self.describe()
        if description is None:
            if create_with is None:
                return None
            description = await self._create(create_with)
        self._check(description, create_with)
        return self._bind(description)

    async def _create(self, dimension: int) -> Mapping[str, Any]:
        await self._control.call(
            "POST",
            "/indexes",
            json={
                "name": self.index,
                "dimension": dimension,
                "metric": "cosine",
                "spec": {"serverless": {"cloud": self._settings.cloud, "region": self._settings.region}},
                "deletion_protection": "disabled",
            },
            retries=WRITE_RETRIES,
        )
        log.info("pinecone_index_created", dimension=dimension)
        waited = 0.0
        while True:
            description = await self.describe()
            status = description.get("status") if description else None
            if isinstance(status, Mapping) and status.get("ready") is True and description is not None:
                return description
            if waited >= READY_TIMEOUT_S:
                raise ConnectorError(f"Pinecone index '{self.index}' is not ready yet. Try again in a minute")
            await asyncio.sleep(READY_POLL_S)
            waited += READY_POLL_S

    async def ensure_namespace(self, kb_id: str, dimension: int) -> None:
        """Create the index at ``dimension`` when missing, or check its width (namespaces need no setup)."""
        check_safe_id(kb_id, what="knowledge base id")
        await self._data_plane(create_with=dimension)

    # ------------------------------------------------------------------ writes
    async def upsert(self, kb_id: str, records: list[VectorRecord]) -> None:
        """Upsert the records into ``kb_id``'s namespace."""
        if not records:
            return
        data = await self._data_plane(create_with=len(records[0].vector))
        assert data is not None
        namespace = kb_namespace(check_safe_id(kb_id, what="knowledge base id"))
        size = batch_size(len(records[0].vector))
        for start in range(0, len(records), size):
            vectors = [
                {
                    "id": check_safe_id(record.id),
                    "values": record.vector,
                    "metadata": {
                        "kb_id": kb_id,
                        "document_id": check_safe_id(record.document_id, what="document id"),
                    },
                }
                for record in records[start : start + size]
            ]
            await data.call(
                "POST",
                "/vectors/upsert",
                json={"vectors": vectors, "namespace": namespace},
                retries=WRITE_RETRIES,
            )

    async def delete_document(self, kb_id: str, document_id: str) -> None:
        """Delete one document's vectors (a metadata-filter delete in the namespace)."""
        data = await self._data_plane()
        if data is None:
            return
        await data.call(
            "POST",
            "/vectors/delete",
            json={
                "namespace": kb_namespace(check_safe_id(kb_id, what="knowledge base id")),
                "filter": {"document_id": {"$eq": check_safe_id(document_id, what="document id")}},
            },
            retries=WRITE_RETRIES,
            ok_statuses=frozenset({404}),
        )

    async def delete_kb(self, kb_id: str) -> None:
        """Delete ``kb_id``'s namespace (the index stays: other knowledge bases share it)."""
        data = await self._data_plane()
        if data is None:
            return
        namespace = kb_namespace(check_safe_id(kb_id, what="knowledge base id"))
        await data.call(
            "DELETE", f"/namespaces/{namespace}", retries=WRITE_RETRIES, ok_statuses=frozenset({404})
        )

    async def optimize(self, kb_id: str) -> None:
        """A no-op: serverless indexes need no maintenance."""

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
        """The ``k`` nearest records of ``kb_id``'s namespace (cosine similarity; ``text`` is not used)."""
        documents = document_filter(filters)
        data = await self._data_plane()
        if data is None:
            return []
        body_json: dict[str, Any] = {
            "namespace": kb_namespace(check_safe_id(kb_id, what="knowledge base id")),
            "vector": vector,
            "topK": max(k, 1),
            "includeMetadata": True,
            "includeValues": False,
        }
        if documents is not None:
            body_json["filter"] = {"document_id": {"$in": documents}}
        body = await data.call("POST", "/query", json=body_json)
        matches = body.get("matches") if isinstance(body, Mapping) else None
        hits: list[VectorHit] = []
        for match in matches if isinstance(matches, list) else []:
            if not isinstance(match, Mapping):
                continue
            metadata = match.get("metadata") if isinstance(match.get("metadata"), Mapping) else {}
            assert isinstance(metadata, Mapping)
            if metadata.get("kb_id") not in (None, kb_id):
                continue
            hits.append(
                VectorHit(
                    id=str(match.get("id")),
                    document_id=str(metadata.get("document_id", "")),
                    score=float(match.get("score") or 0.0),
                )
            )
        return hits

    async def health(self) -> StoreHealth:
        """Whether the key can list the project's indexes."""
        try:
            await self.list_indexes()
        except ConnectorError as exc:
            return StoreHealth(ok=False, backend="pinecone", detail=exc.message)
        return StoreHealth(ok=True, backend="pinecone", version=API_VERSION)
