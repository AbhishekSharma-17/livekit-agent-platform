"""Ragie as the first :class:`~lkap_api.kb.external.ExternalRetriever` (V5-45, K §7).

Ragie ingests and ranks its own documents (uploaded there or synced from the
user's apps); a knowledge base of ``kind="external"`` names one Ragie
**partition** (``knowledge_bases.external_ref``) and is searched there. LKAP
stores none of the documents.

Wire shape (checked 2026-09-27 against the ``ragie-python`` SDK reference,
which is generated from Ragie's OpenAPI document; ``docs.ragie.ai`` itself
refused the TLS handshake from the build machine):

* ``POST https://api.ragie.ai/retrievals`` with ``Authorization: Bearer <key>``
  and ``{"query", "top_k", "rerank", "recency_bias", "partition"}`` (``top_k``
  defaults to 8 at Ragie; ``filter`` and the beta ``max_chunks_per_document``
  are not sent) answers ``{"scored_chunks": [{"text", "score", "id", "index",
  "metadata", "document_id", "document_name", "document_metadata", "links"}]}``.
  ``score`` is relative to that one retrieval ("should not be compared across
  retrievals"); with ``rerank`` on, ``top_k`` is a ceiling and Ragie drops the
  chunks it judges irrelevant. ``links`` are Ragie API links that need the key,
  so they are never shown; ``meta.url`` comes from ``document_metadata``
  (``source_url`` or ``url``) and only when it is an ``https`` address.
* ``GET /partitions?page_size=100`` answers ``{"pagination": {"next_cursor",
  "total_count"}, "partitions": [{"name", "is_default", "description", …}]}``:
  the connection test's partition list.
* ``GET /partitions/{name}`` answers the partition with ``stats.document_count``:
  :meth:`RagieRetriever.describe`. Ragie reports no sync time for a partition.
* Errors: 401, 402 (plan limit), 429 and 500 carry ``{"detail": str}``; 422 a
  validation list. :mod:`~lkap_api.knowledge_connections.http` scrubs them.
  Partition names are lower-case letters, digits, ``_`` and ``-``.

Every call goes through :class:`~lkap_api.knowledge_connections.http.VendorHttp`
on the caller's guarded client (``net_guard``: no private destination, no
redirect). Searches never retry (they must answer inside the per-knowledge-base
budget). The retrieved text is untrusted: it is cleaned of control characters
and capped here, and the worker wraps every knowledge hit in ``fence()``
(``<untrusted source="knowledge">``) before it reaches a prompt.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from typing import Any, Final
from urllib.parse import quote, urlsplit

import httpx
from lkap_contracts.api_models import KbHit

from lkap_api.kb.external import RetrieverInfo
from lkap_api.knowledge_connections.http import ConnectorError, VendorHttp
from lkap_api.knowledge_connections.settings import RAGIE_PARTITION_PATTERN

#: The connection kind and the registry entry.
KIND: Final = "ragie"
VENDOR: Final = "Ragie"
BASE_URL: Final = "https://api.ragie.ai"
#: One search's budget (the service's own wait is a little longer).
SEARCH_TIMEOUT_S: Final = 3.0
#: The most partitions the connection test lists (one page).
MAX_PARTITIONS: Final = 100
#: The longest passage handed on (a Ragie chunk is usually far shorter).
MAX_CHUNK_CHARS: Final = 4000
#: The longest document name kept.
MAX_NAME_CHARS: Final = 255
#: A partition name as Ragie accepts it (length is not documented; the cap is LKAP's).
PARTITION_PATTERN: Final = RAGIE_PARTITION_PATTERN

#: Control characters removed from passages (newlines and tabs are kept).
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
#: Unicode categories dropped from a document name (bidi overrides, zero-width); control
#: characters (``Cc``: newlines, escapes) become spaces.
_FORMAT_CATEGORIES: Final = frozenset({"Cf"})


def valid_partition(name: str) -> bool:
    """Whether ``name`` is a partition name Ragie accepts."""
    return re.fullmatch(PARTITION_PATTERN, name) is not None


def clean_text(text: object, *, limit: int = MAX_CHUNK_CHARS) -> str:
    """A retrieved passage without control characters, capped at ``limit``."""
    return _CONTROL_RE.sub("", str(text or ""))[:limit]


def clean_name(name: object) -> str:
    """A document name on one line, without control or format characters, capped."""
    kept = "".join(
        " " if category == "Cc" else char
        for char in str(name or "")
        if (category := unicodedata.category(char)) not in _FORMAT_CATEGORIES
    )
    return " ".join(kept.split())[:MAX_NAME_CHARS] or "document"


def _https_url(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 2048:
        return None
    parts = urlsplit(value.strip())
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        return None
    return value.strip()


def _source_url(document_metadata: object) -> str | None:
    if not isinstance(document_metadata, Mapping):
        return None
    for key in ("source_url", "url"):
        url = _https_url(document_metadata.get(key))
        if url is not None:
            return url
    return None


class RagieApi:
    """The three Ragie calls LKAP makes (see the module docstring)."""

    def __init__(
        self, client: httpx.AsyncClient, *, api_key: str, timeout_s: float = SEARCH_TIMEOUT_S
    ) -> None:
        """Bind the calls to a guarded client and a key (the key lives in a header, never a log)."""
        self._http = VendorHttp(
            client,
            vendor=VENDOR,
            base_url=BASE_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout_s=timeout_s,
        )

    async def retrieve(
        self,
        query: str,
        *,
        top_k: int,
        partition: str,
        rerank: bool,
        recency_bias: bool,
        filters: Mapping[str, object] | None = None,
    ) -> list[dict[str, Any]]:
        """``POST /retrievals``: the scored chunks, best first.

        Raises:
            ConnectorError: The call failed or the answer has no ``scored_chunks`` list.
        """
        body: dict[str, Any] = {
            "query": query,
            "top_k": top_k,
            "rerank": rerank,
            "recency_bias": recency_bias,
            "partition": partition,
        }
        if filters:
            body["filter"] = dict(filters)
        answer = await self._http.call("POST", "/retrievals", json=body)
        chunks = answer.get("scored_chunks") if isinstance(answer, dict) else None
        if not isinstance(chunks, list):
            raise ConnectorError("Ragie answered without a list of passages")
        return [chunk for chunk in chunks if isinstance(chunk, dict)]

    async def list_partitions(
        self, *, limit: int = MAX_PARTITIONS
    ) -> tuple[list[dict[str, Any]], int | None]:
        """``GET /partitions``: the first page (up to ``limit``) and Ragie's total count."""
        answer = await self._http.call("GET", "/partitions", params={"page_size": limit})
        if not isinstance(answer, dict) or not isinstance(answer.get("partitions"), list):
            raise ConnectorError("Ragie answered without a list of partitions")
        partitions = [item for item in answer["partitions"] if isinstance(item, dict)]
        pagination = answer.get("pagination")
        total = pagination.get("total_count") if isinstance(pagination, dict) else None
        return partitions, total if isinstance(total, int) else None

    async def partition(self, name: str) -> dict[str, Any]:
        """``GET /partitions/{name}``: the partition with its ``stats``.

        Raises:
            ConnectorError: The call failed (``not_found`` for an unknown partition).
        """
        answer = await self._http.call("GET", f"/partitions/{quote(name, safe='')}")
        if not isinstance(answer, dict):
            raise ConnectorError("Ragie answered with something that is not a partition")
        return answer


class RagieRetriever:
    """One knowledge base's Ragie partition as an :class:`~lkap_api.kb.external.ExternalRetriever`."""

    def __init__(
        self,
        api: RagieApi,
        *,
        kb_id: str,
        partition: str,
        rerank: bool = False,
        recency_bias: bool = False,
    ) -> None:
        """Bind the retriever to a knowledge base and its partition.

        Args:
            api: The Ragie calls (a guarded client and the connection's key).
            kb_id: Stamped on every hit.
            partition: The Ragie partition searched (``knowledge_bases.external_ref``).
            rerank: Ragie's own re-rank (the connection's setting).
            recency_bias: Prefer newer documents (the connection's setting).
        """
        self._api = api
        self.kb_id = kb_id
        self.partition = partition
        self._rerank = rerank
        self._recency_bias = recency_bias

    async def search(self, query: str, *, k: int, filters: Mapping[str, object] | None = None) -> list[KbHit]:
        """Up to ``k`` passages from the partition, Ragie's order (best first)."""
        chunks = await self._api.retrieve(
            query,
            top_k=k,
            partition=self.partition,
            rerank=self._rerank,
            recency_bias=self._recency_bias,
            filters=filters,
        )
        hits = [hit for chunk in chunks if (hit := self._hit(chunk)) is not None]
        return hits[:k]

    async def describe(self) -> RetrieverInfo:
        """The partition's document count (Ragie reports no sync time for a partition)."""
        detail = await self._api.partition(self.partition)
        stats = detail.get("stats")
        count = stats.get("document_count") if isinstance(stats, dict) else None
        return RetrieverInfo(
            kind=KIND,
            document_count=count if isinstance(count, int) and not isinstance(count, bool) else None,
            sources=[str(detail.get("name") or self.partition)],
        )

    def _hit(self, chunk: Mapping[str, Any]) -> KbHit | None:
        text = clean_text(chunk.get("text"))
        chunk_id = chunk.get("id")
        if not text.strip() or not isinstance(chunk_id, str) or not chunk_id:
            return None
        name = clean_name(chunk.get("document_name"))
        raw_score = chunk.get("score")
        score = (
            float(raw_score)
            if isinstance(raw_score, int | float) and not isinstance(raw_score, bool)
            else 0.0
        )
        meta: dict[str, Any] = {
            "filename": name,
            "document_name": name,
            "source": KIND,
            "partition": self.partition,
        }
        index = chunk.get("index")
        if isinstance(index, int) and not isinstance(index, bool):
            meta["chunk_index"] = index
        url = _source_url(chunk.get("document_metadata"))
        if url is not None:
            meta["url"] = url
        return KbHit(
            chunk_id=chunk_id[:128],
            document_id=str(chunk.get("document_id") or "")[:128],
            filename=name,
            score=min(1.0, max(0.0, score)),
            text=text,
            kb_id=self.kb_id,
            meta=meta,
            score_source="external",
        )
