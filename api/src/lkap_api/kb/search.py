"""Search building blocks: the query-embedding cache and reciprocal-rank fusion (V5-04).

:class:`~lkap_api.kb.service.KnowledgeService` runs the whole pipeline (fan
out, fuse, rerank, floor, join); this module holds the pieces it is made of,
so each is testable on its own, plus :func:`search_kbs`, the pre-V5-04 entry
point kept as a thin wrapper (vector mode, no rerank, no floor).
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from lkap_contracts.api_models import KbHit
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.kb.embed import Embedder
from lkap_api.kb.store import VectorStore
from lkap_api.logging import get_logger

log = get_logger(__name__)

#: Reciprocal-rank-fusion constant (Cormack et al.; the research's k=60).
RRF_K: Final = 60
#: How many normalised queries the process keeps embeddings for.
QUERY_CACHE_SIZE: Final = 512


# --------------------------------------------------------------------------- query-embedding cache
def normalise_query(query: str) -> str:
    """The cache key form of a query: whitespace collapsed, case folded.

    It is also the text that is embedded, so a cached vector is exactly the
    vector the query would get (the default ``bge-small`` model is uncased).
    """
    return " ".join(query.split()).casefold()


@dataclass(slots=True)
class CacheStats:
    """Counters for :class:`QueryEmbeddingCache` (tests and the debug log)."""

    hits: int = 0
    misses: int = 0


class QueryEmbeddingCache:
    """An LRU of query vectors keyed by ``(embedder model, width, normalised query)``.

    Keyed by model as well as text, so switching ``LKAP_EMBEDDER`` (or a test
    embedder) never returns another model's vector. Spoken questions repeat
    ("what's covered?"), and with a remote embedder a hit saves a network call.
    """

    def __init__(self, maxsize: int = QUERY_CACHE_SIZE) -> None:
        """Create an empty cache holding at most ``maxsize`` vectors."""
        self._maxsize = maxsize
        self._entries: OrderedDict[tuple[str, int | None, str], list[float]] = OrderedDict()
        self.stats = CacheStats()

    def __len__(self) -> int:
        """How many vectors are cached."""
        return len(self._entries)

    async def embed(self, embedder: Embedder, query: str) -> list[float] | None:
        """Return the vector for ``query`` (normalised), embedding it only on a miss.

        Returns:
            The vector, or ``None`` when the embedder returned nothing.
        """
        normalised = normalise_query(query)
        key = (embedder.model_id, embedder.dimension, normalised)
        cached = self._entries.get(key)
        if cached is not None:
            self._entries.move_to_end(key)
            self.stats.hits += 1
            return cached
        self.stats.misses += 1
        vectors = await embedder.embed([normalised])
        if not vectors:
            return None
        vector = vectors[0]
        self._entries[key] = vector
        if len(self._entries) > self._maxsize:
            self._entries.popitem(last=False)
        return vector

    def clear(self) -> None:
        """Forget every cached vector and reset the counters (tests)."""
        self._entries.clear()
        self.stats = CacheStats()


#: The process-wide cache every search uses.
QUERY_CACHE = QueryEmbeddingCache()


# --------------------------------------------------------------------------- reciprocal-rank fusion
@dataclass(slots=True, frozen=True)
class FusedItem:
    """One id after fusion: its raw and normalised RRF score and its rank in each list."""

    id: str
    rrf: float
    score: float
    ranks: tuple[int | None, ...]


def fuse_rrf(rankings: Sequence[Sequence[str]], *, k: int = RRF_K) -> list[FusedItem]:
    """Fuse ranked id lists by reciprocal rank: ``rrf(d) = Σ 1 / (k + rank_i(d))``.

    ``score`` is ``rrf`` divided by its maximum (an id ranked first in every
    list), so it lies in ``(0, 1]`` and a ``min_score`` floor means the same
    thing for any number of lists. Ties are broken by the best rank in the
    earlier lists, then by id, so the order is deterministic.

    Args:
        rankings: Each list best first; an id missing from a list contributes nothing for it.
        k: The RRF constant.

    Returns:
        Every id that appears in any list, best first.
    """
    lists = [list(ranking) for ranking in rankings]
    if not lists:
        return []
    ceiling = len(lists) / (k + 1)
    positions: dict[str, list[int | None]] = {}
    for index, ranking in enumerate(lists):
        for rank, item_id in enumerate(ranking, start=1):
            slots = positions.setdefault(item_id, [None] * len(lists))
            if slots[index] is None:
                slots[index] = rank
    fused = [
        FusedItem(
            id=item_id,
            rrf=(raw := sum(1.0 / (k + rank) for rank in ranks if rank is not None)),
            score=raw / ceiling,
            ranks=tuple(ranks),
        )
        for item_id, ranks in positions.items()
    ]
    missing = max(len(ranking) for ranking in lists) + 1
    fused.sort(
        key=lambda item: (
            -item.rrf,
            tuple(rank if rank is not None else missing for rank in item.ranks),
            item.id,
        )
    )
    return fused


# --------------------------------------------------------------------------- pre-V5-04 entry point
async def search_kbs(
    session: AsyncSession, store: VectorStore, embedder: Embedder, *, kb_ids: list[str], query: str, k: int
) -> list[KbHit]:
    """Return the top ``k`` chunks across ``kb_ids`` for ``query``, best first (vector mode).

    Kept for callers written before V5-04; equivalent to
    ``KnowledgeService.search(kb_ids, query, k)`` with every option at its
    default. Knowledge bases built by another embedder are skipped.

    Args:
        session: Used to join vector hits back to their chunk text and filename.
        store: The vector store queried per knowledge base.
        embedder: Embeds the query text.
        kb_ids: Knowledge bases to search; an empty list returns no hits.
        query: The search text.
        k: Overall result count across all `kb_ids` combined.

    Returns:
        Hits sorted by score descending, truncated to `k`.
    """
    from lkap_api.kb.service import KnowledgeService

    result = await KnowledgeService(session, store=store, embedder=embedder).search(kb_ids, query, k)
    return list(result.hits)
