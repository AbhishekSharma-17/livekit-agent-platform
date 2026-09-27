"""`ExternalRetriever`: knowledge a vendor ingests and searches (K §5.1, V5-13; D-V5-20's hook).

The second plug point beside :class:`~lkap_api.kb.store.VectorStore`. A managed
search service (Ragie, Vertex AI Search, Bedrock Knowledge Bases, Azure AI
Search) or a graph index (LightRAG, Neo4j) owns ingestion and ranking; LKAP
only asks it a question and gets :class:`~lkap_contracts.api_models.KbHit`
rows back, plus a description for the console.

V5-13 shipped the Protocol only. V5-45 moved this module to
``kb/external/__init__.py``, added the first adapter
(:class:`~lkap_api.kb.external.ragie.RagieRetriever`) and routes
``kind="external"`` knowledge bases to it in ``kb/service.py``.

**Merging with managed knowledge bases (V5-45).** An external service's score
is relative to one retrieval (Ragie says so: "should not be compared across
retrievals"), so it is not comparable with LKAP's cosine, fused or re-ranked
scores, nor with another service's. :func:`merge_by_rank` therefore merges
by **rank**, not by raw score: every list is normalised to its positions (the
first hit of each list ranks equal, then the second hits, and so on), which is
a round-robin across lists; ties go to the list given first (the managed
list). Each hit keeps its own ``score`` and ``score_source`` (``external`` for
a vendor hit), so a merged list is not sorted by ``score``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from lkap_contracts.api_models import KbHit
from pydantic import BaseModel, ConfigDict, Field


class RetrieverInfo(BaseModel):
    """What an external retriever reports about itself (the knowledge base detail in the console)."""

    model_config = ConfigDict(frozen=True)

    #: The adapter kind (``ragie``, ``vertex_ai_search``, …).
    kind: str
    #: How many documents the vendor holds for this knowledge base, when it says.
    document_count: int | None = None
    #: When the vendor last synced its sources, when it says.
    last_synced_at: datetime | None = None
    #: Human-readable source names (buckets, sites, data stores).
    sources: list[str] = Field(default_factory=list)


@runtime_checkable
class ExternalRetriever(Protocol):
    """A knowledge source whose ingestion and ranking happen outside LKAP."""

    async def search(self, query: str, *, k: int, filters: Mapping[str, object] | None = None) -> list[KbHit]:
        """Return up to ``k`` hits for ``query``, best first (text included: there is no SQL join)."""
        ...

    async def describe(self) -> RetrieverInfo:
        """Describe the source for the console (counts and sync time where the vendor exposes them)."""
        ...


def merge_by_rank(lists: Sequence[Sequence[KbHit]], k: int) -> list[KbHit]:
    """Merge several best-first hit lists by rank (see the module docstring) and cut to ``k``.

    A hit whose ``(kb_id, chunk_id)`` already appeared keeps its first place.

    Args:
        lists: Best-first lists; the earlier list wins a tie at the same rank.
        k: The most hits to return.

    Returns:
        Up to ``k`` hits: every list's first hit, then every list's second, and so on.
    """
    merged: list[KbHit] = []
    seen: set[tuple[str | None, str]] = set()
    depth = max((len(hits) for hits in lists), default=0)
    for position in range(depth):
        for hits in lists:
            if position >= len(hits):
                continue
            hit = hits[position]
            key = (hit.kb_id, hit.chunk_id)
            if key in seen:
                continue
            seen.add(key)
            merged.append(hit)
            if len(merged) >= k:
                return merged
    return merged
