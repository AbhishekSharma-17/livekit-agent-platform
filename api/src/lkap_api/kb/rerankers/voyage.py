"""Voyage AI's hosted re-ranker (V5-20, D-V5-19: a knowledge connection, the search tool only).

Wire shape (docs.voyageai.com/reference/reranker-api, verified 2026-09-27):
``POST https://api.voyageai.com/v1/rerank`` with ``Authorization: Bearer <key>``
and ``{"query", "documents": [str], "model", "top_k", "truncation": true}``
answers ``{"object": "list", "data": [{"index", "relevance_score"}], "model",
"usage": {"total_tokens"}}`` (the REST shape; the Python client renames it).
At most 1,000 documents per request.

Models: ``rerank-2.5-lite`` (the default here: the lower-latency current
model, $0.02 per million tokens), ``rerank-2.5`` ($0.05), and the preview
``rerank-3`` / ``rerank-3-lite`` (the only ones with free tokens, 200 million
each, per Voyage's pricing page on 2026-09-27). Billing is per token, so a call
is costed as ``total_tokens`` ``tokens_in``. Voyage has no key-check endpoint:
:meth:`VoyageReranker.check` re-ranks one one-word document (a few tokens).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

import httpx

from lkap_api.kb.rerankers.base import RerankUsage
from lkap_api.kb.rerankers.cohere import scores_by_index
from lkap_api.knowledge_connections.http import VendorHttp

#: The registry entry the calls are priced under.
PROVIDER_ID: Final = "voyage-rerank"
BASE_URL: Final = "https://api.voyageai.com"
DEFAULT_MODEL: Final = "rerank-2.5-lite"
#: The search path's budget for one re-rank call.
RERANK_TIMEOUT_S: Final = 3.0


class VoyageReranker:
    """A :class:`~lkap_api.kb.rerankers.base.HostedReranker` over Voyage's ``/v1/rerank``."""

    normalized = True
    provider_id = PROVIDER_ID

    def __init__(self, client: httpx.AsyncClient, *, api_key: str, model: str = DEFAULT_MODEL) -> None:
        """Bind the re-ranker to a client, a key and a model."""
        self._http = VendorHttp(
            client, vendor="Voyage AI", base_url=BASE_URL, headers={"Authorization": f"Bearer {api_key}"}
        )
        self._model = model

    @property
    def model_id(self) -> str:
        """The Voyage re-rank model."""
        return self._model

    async def _call(
        self, query: str, documents: list[str], *, timeout_s: float | None
    ) -> tuple[list[float], float]:
        body = await self._http.call(
            "POST",
            "/v1/rerank",
            json={
                "query": query,
                "documents": documents,
                "model": self._model,
                "top_k": len(documents),
                "truncation": True,
            },
            timeout_s=timeout_s,
        )
        scores = scores_by_index(
            body.get("data") if isinstance(body, dict) else None, len(documents), "Voyage AI"
        )
        usage = body.get("usage") if isinstance(body, dict) else None
        tokens = usage.get("total_tokens") if isinstance(usage, dict) else None
        return scores, float(tokens) if isinstance(tokens, int | float) else 0.0

    async def rerank_with_usage(self, query: str, passages: Sequence[str]) -> tuple[list[float], RerankUsage]:
        """Score every passage (relevance on ``[0, 1]``) and report the tokens used."""
        documents = list(passages)
        if not documents:
            return [], RerankUsage(PROVIDER_ID, self._model, "tokens_in", 0.0)
        scores, tokens = await self._call(query, documents, timeout_s=RERANK_TIMEOUT_S)
        return scores, RerankUsage(PROVIDER_ID, self._model, "tokens_in", tokens)

    async def rerank(self, query: str, passages: Sequence[str]) -> list[float]:
        """Score every passage (relevance on ``[0, 1]``)."""
        scores, _usage = await self.rerank_with_usage(query, passages)
        return scores

    async def check(self) -> str:
        """Check the key with the smallest possible re-rank call.

        Raises:
            ConnectorError: The key is invalid or Voyage AI cannot be reached.
        """
        await self._call("test", ["test"], timeout_s=None)
        return f"Voyage AI accepted the key; re-ranking with {self._model}"
