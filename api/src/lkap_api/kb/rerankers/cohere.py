"""Cohere's hosted re-ranker (V5-20, D-V5-19: a knowledge connection, the search tool only).

Wire shape (docs.cohere.com/reference/rerank and /reference/check-api-key,
verified 2026-09-27):

* ``POST https://api.cohere.com/v2/rerank`` with ``Authorization: Bearer <key>``
  and ``{"model", "query", "documents": [str], "top_n"}`` answers
  ``{"results": [{"index", "relevance_score"}], "meta": {"billed_units":
  {"search_units"}}}``; ``relevance_score`` is on ``[0, 1]``. One search is one
  query with up to 100 documents (the search pipeline sends at most 20).
* ``POST https://api.cohere.com/v1/check-api-key`` with ``{}`` answers
  ``{"valid": bool}`` and bills nothing: the key check.

Models: ``rerank-v4.0-fast`` (the default here: lower latency and price),
``rerank-v4.0-pro``, ``rerank-v3.5``. Billing is per search, so a call is
costed as ``search_units`` ``requests``. The host is fixed (no base url
setting), and the client is the caller's guarded one.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

import httpx

from lkap_api.kb.rerankers.base import RerankUsage
from lkap_api.knowledge_connections.http import ConnectorError, VendorHttp

#: The registry entry the calls are priced under.
PROVIDER_ID: Final = "cohere-rerank"
BASE_URL: Final = "https://api.cohere.com"
#: The default model: the fast v4 re-ranker.
DEFAULT_MODEL: Final = "rerank-v4.0-fast"
#: The search path's budget for one re-rank call.
RERANK_TIMEOUT_S: Final = 3.0


def scores_by_index(results: object, count: int, vendor: str) -> list[float]:
    """Turn ``[{"index", "relevance_score"}]`` into one score per passage, in input order.

    Raises:
        ConnectorError: The answer does not cover every passage exactly once.
    """
    scores: list[float | None] = [None] * count
    if isinstance(results, list):
        for item in results:
            if not isinstance(item, dict):
                continue
            index = item.get("index")
            score = item.get("relevance_score")
            if isinstance(index, int) and 0 <= index < count and isinstance(score, int | float):
                scores[index] = float(score)
    if any(score is None for score in scores):
        raise ConnectorError(f"{vendor} did not score every passage")
    return [float(score) for score in scores if score is not None]


class CohereReranker:
    """A :class:`~lkap_api.kb.rerankers.base.HostedReranker` over Cohere's ``/v2/rerank``."""

    normalized = True
    provider_id = PROVIDER_ID

    def __init__(self, client: httpx.AsyncClient, *, api_key: str, model: str = DEFAULT_MODEL) -> None:
        """Bind the re-ranker to a client, a key and a model."""
        self._http = VendorHttp(
            client, vendor="Cohere", base_url=BASE_URL, headers={"Authorization": f"Bearer {api_key}"}
        )
        self._model = model

    @property
    def model_id(self) -> str:
        """The Cohere re-rank model."""
        return self._model

    async def rerank_with_usage(self, query: str, passages: Sequence[str]) -> tuple[list[float], RerankUsage]:
        """Score every passage (relevance on ``[0, 1]``) and report the billed searches."""
        documents = list(passages)
        if not documents:
            return [], RerankUsage(PROVIDER_ID, self._model, "requests", 0.0)
        body = await self._http.call(
            "POST",
            "/v2/rerank",
            json={"model": self._model, "query": query, "documents": documents, "top_n": len(documents)},
            timeout_s=RERANK_TIMEOUT_S,
        )
        scores = scores_by_index(
            body.get("results") if isinstance(body, dict) else None, len(documents), "Cohere"
        )
        billed = 1.0
        meta = body.get("meta") if isinstance(body, dict) else None
        units = meta.get("billed_units") if isinstance(meta, dict) else None
        if isinstance(units, dict) and isinstance(units.get("search_units"), int | float):
            billed = float(units["search_units"])
        return scores, RerankUsage(PROVIDER_ID, self._model, "requests", billed)

    async def rerank(self, query: str, passages: Sequence[str]) -> list[float]:
        """Score every passage (relevance on ``[0, 1]``)."""
        scores, _usage = await self.rerank_with_usage(query, passages)
        return scores

    async def check(self) -> str:
        """Check the key without spending a search.

        Raises:
            ConnectorError: The key is invalid or Cohere cannot be reached.
        """
        body = await self._http.call("POST", "/v1/check-api-key", json={})
        if not (isinstance(body, dict) and body.get("valid") is True):
            raise ConnectorError("Cohere says this key is not valid", auth=True)
        return f"Cohere accepted the key. Re-ranking with {self._model}"
