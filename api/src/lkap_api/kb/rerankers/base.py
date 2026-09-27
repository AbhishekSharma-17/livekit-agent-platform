"""The :class:`Reranker` Protocol every re-ranker implements (V5-04; hosted ones since V5-20).

A re-ranker scores ``(query, passage)`` pairs; the knowledge search rescores
its top candidates with one and orders the final hits by that score.

Two kinds of score come back:

* a **logit** (the local cross-encoder): unbounded, mapped to ``(0, 1)`` by
  :func:`sigmoid` in the search pipeline;
* a **relevance** already on ``[0, 1]`` (Cohere's and Voyage's
  ``relevance_score``): used as is. Such a re-ranker says so with
  ``normalized = True`` (:func:`scores_are_normalized` reads it, defaulting to
  ``False`` for a re-ranker that does not declare it).

A hosted re-ranker also reports what the call used (:class:`RerankUsage`), so
the search can attach a cost line (D-V5-19).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from lkap_contracts.pricing import Unit


@runtime_checkable
class Reranker(Protocol):
    """Scores passages against a query (higher = more relevant)."""

    @property
    def model_id(self) -> str:
        """The reranking model's id."""
        ...

    async def rerank(self, query: str, passages: Sequence[str]) -> list[float]:
        """Return one relevance score per passage, in input order."""
        ...


@dataclass(slots=True, frozen=True)
class RerankUsage:
    """What one hosted re-rank call used, in the vendor's billing unit."""

    provider_id: str
    model: str
    unit: Unit
    quantity: float


def sigmoid(value: float) -> float:
    """Map a relevance logit to ``(0, 1)`` (numerically safe for large magnitudes)."""
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exp = math.exp(value)
    return exp / (1.0 + exp)


def scores_are_normalized(reranker: object) -> bool:
    """Whether ``reranker`` returns relevance on ``[0, 1]`` (hosted) rather than logits (local)."""
    return getattr(reranker, "normalized", False) is True


@runtime_checkable
class HostedReranker(Reranker, Protocol):
    """A re-ranking service: relevance on ``[0, 1]``, and the usage of each call."""

    @property
    def normalized(self) -> bool:
        """Always ``True``: the scores are relevance on ``[0, 1]``."""
        ...

    @property
    def provider_id(self) -> str:
        """The registry entry the call is priced under."""
        ...

    async def rerank_with_usage(self, query: str, passages: Sequence[str]) -> tuple[list[float], RerankUsage]:
        """Score every passage and report what the call used."""
        ...
