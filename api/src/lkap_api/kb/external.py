"""`ExternalRetriever`: knowledge a vendor ingests and searches (K §5.1, V5-13; D-V5-20's hook).

The second plug point beside :class:`~lkap_api.kb.store.VectorStore`. A managed
search service (Ragie, Vertex AI Search, Bedrock Knowledge Bases, Azure AI
Search) or a graph index (LightRAG, Neo4j) owns ingestion and ranking; LKAP
only asks it a question and gets :class:`~lkap_contracts.api_models.KbHit`
rows back, plus a description for the console.

V5-13 ships the Protocol only: no adapter and no branch in the search
pipeline. V5-20 moves this module to ``kb/external/__init__.py``, adds the
first adapter and routes ``kind="external"`` knowledge bases to it in
``kb/service.py``. External scores are not comparable with LKAP's own, so a
merge with managed knowledge bases is by rank (the adapter package decides and
documents it).
"""

from __future__ import annotations

from collections.abc import Mapping
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
