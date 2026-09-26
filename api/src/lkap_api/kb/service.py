"""`KnowledgeService`: the knowledge search pipeline (V5-04, K §2 P0-4 … P0-6, P1-3(a)).

One call, :meth:`KnowledgeService.search`, runs these stages:

1. **Scope.** The listed knowledge bases are loaded; an unknown id is skipped
   and one built by another embedder than the configured one is skipped too
   (``kb_embedder_mismatch``), each with a warning in the response. One bad
   knowledge base never fails the search.
2. **Embed once.** The query is normalised and embedded through the
   process-wide LRU (:data:`~lkap_api.kb.search.QUERY_CACHE`, 512 entries).
3. **Fan out.** Every knowledge base's vector table is queried concurrently
   (``asyncio.gather``) with a per-knowledge-base timeout; a slow or failing
   one is skipped with a warning. In ``hybrid`` mode the lexical index
   (:mod:`lkap_api.kb.lexical`) is queried at the same time.
4. **Join.** Candidate ids are joined to ``kb_chunks`` / ``kb_documents``
   (the SQL rows are the source of truth, so a vector whose chunk row was
   deleted is dropped here).
5. **Fuse** (``hybrid``): the vector list and the lexical list, 20 each, are
   fused by reciprocal rank (k = 60) into a candidate list of 20.
6. **Rerank** (``rerank="local"``): the candidates are rescored by the local
   cross-encoder (:mod:`lkap_api.kb.rerank`) and reordered.
7. **Floor.** The top ``k`` are cut at ``min_score``; the response says how
   many were dropped.

**What ``score`` means.** Every hit reports each stage that ran for it
(``vector_score``, ``lexical_rank``, ``fused_score``, ``rerank_score``) and
``score`` is the one that decided its position (``score_source``). All three
deciding scores are on ``[0, 1]`` so one ``min_score`` works in every mode:

* ``vector``: cosine similarity (``1 - cosine distance``), exactly as before V5-04;
* ``fused``: the RRF sum divided by its maximum (first in every non-empty
  list = 1.0). This is rank-derived: the best hit of any query scores near
  1.0, so in ``hybrid`` mode without rerank ``min_score`` trims the tail of
  the list rather than judging relevance;
* ``rerank``: the cross-encoder's logit through a sigmoid.

The defaults (``mode="vector"``, ``rerank="none"``, ``min_score=None``) return
what the pre-V5-04 search returned, so the worker is unchanged until V5-06.

**Native hybrid (V5-13).** When the vector store says ``capabilities.hybrid``
(pgvector: the ``tsv`` keyword index lives in the same database), stage 3
asks the store for the fused list (``store.query(..., text=query)``) and the
service skips its own lexical stage and fusion: each knowledge base's list is
fused inside the store by the same RRF over the same keyword query, so one
knowledge base's result is identical either way. Across several knowledge
bases the per-knowledge-base fused lists are merged by fused score (the
service's own path fuses one global keyword list instead). A store that
reports a knowledge base built at another width
(:class:`~lkap_api.kb.embed.KbEmbedderMismatchError`) gets that knowledge base
skipped with a ``kb_embedder_mismatch`` warning, like stage 1 does.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from lkap_contracts.api_models import (
    KbHit,
    KbRerankMode,
    KbScoreSource,
    KbSearchMode,
    KbSearchResponse,
    KbSearchWarning,
    KbSearchWarningCode,
)
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.db.guard import CROSS_WORKSPACE_OPTION
from lkap_api.db.models import KbChunk, KbDocument, KnowledgeBase
from lkap_api.kb.embed import Embedder, KbEmbedderMismatchError, kb_embedder_mismatch
from lkap_api.kb.lexical import LexicalResult, lexical_search
from lkap_api.kb.rerank import Reranker, sigmoid
from lkap_api.kb.search import QUERY_CACHE, QueryEmbeddingCache, fuse_rrf
from lkap_api.kb.store import VectorHit, VectorStore, store_capabilities
from lkap_api.logging import get_logger

log = get_logger(__name__)

#: The search vocabulary lives in contracts (asks #12/#27, moved by V5-06); the names stay here too.
SearchMode = KbSearchMode
RerankMode = KbRerankMode
ScoreSource = KbScoreSource
WarningCode = KbSearchWarningCode

#: Each list (vector, lexical) and the fused list are cut to this many candidates.
CANDIDATES: Final = 20
#: How many fused candidates the cross-encoder rescores.
RERANK_POOL: Final = 20
#: How long one knowledge base's vector query may take before it is skipped.
PER_KB_TIMEOUT_S: Final = 2.0


# --------------------------------------------------------------------------- response models
# The contracts models since V5-06 moved them (asks #12/#27); the api-local names are aliases.
KnowledgeHit = KbHit
KnowledgeSearchWarning = KbSearchWarning
KnowledgeSearchResponse = KbSearchResponse


# --------------------------------------------------------------------------- the service
@dataclass(slots=True)
class _Candidate:
    """A chunk on its way through the stages."""

    chunk: KbChunk
    filename: str
    vector_score: float | None = None
    lexical_rank: int | None = None
    fused_score: float | None = None
    rerank_score: float | None = None
    source: ScoreSource = "vector"

    @property
    def score(self) -> float:
        match self.source:
            case "rerank":
                return self.rerank_score if self.rerank_score is not None else 0.0
            case "fused":
                return self.fused_score if self.fused_score is not None else 0.0
            case _:
                return self.vector_score if self.vector_score is not None else 0.0

    def hit(self) -> KnowledgeHit:
        return KnowledgeHit(
            chunk_id=self.chunk.id,
            document_id=self.chunk.document_id,
            filename=self.filename,
            score=self.score,
            text=self.chunk.text,
            kb_id=self.chunk.kb_id,
            meta=dict(self.chunk.meta or {}),
            vector_score=self.vector_score,
            lexical_rank=self.lexical_rank,
            fused_score=self.fused_score,
            rerank_score=self.rerank_score,
            score_source=self.source,
        )


class KnowledgeService:
    """Knowledge search over one request's session (see the module docstring for the stages)."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        store: VectorStore,
        embedder: Embedder,
        reranker: Reranker | None = None,
        query_cache: QueryEmbeddingCache | None = None,
        per_kb_timeout_s: float = PER_KB_TIMEOUT_S,
    ) -> None:
        """Bind the service to a session and its collaborators.

        Args:
            session: Used for the knowledge-base rows, the lexical index and the chunk join.
            store: The vector store queried per knowledge base.
            embedder: The configured embedder (the query is embedded once).
            reranker: Used only when a search asks for ``rerank="local"``.
            query_cache: The query-embedding LRU; defaults to the process-wide one.
            per_kb_timeout_s: The per-knowledge-base vector query timeout.
        """
        self._session = session
        self._store = store
        self._embedder = embedder
        self._reranker = reranker
        self._cache = query_cache if query_cache is not None else QUERY_CACHE
        self._timeout = per_kb_timeout_s

    async def search(
        self,
        kb_ids: Sequence[str],
        query: str,
        k: int,
        *,
        min_score: float | None = None,
        rerank: RerankMode = "none",
        mode: SearchMode = "vector",
        workspace_id: str | None = None,
    ) -> KnowledgeSearchResponse:
        """Return the top ``k`` chunks across ``kb_ids`` for ``query``, best first.

        Args:
            kb_ids: Knowledge bases to search; duplicates are ignored.
            query: The search text.
            k: The most hits to return across all knowledge bases.
            min_score: Drop hits whose ``score`` is below this (``None`` = keep all).
            rerank: ``"local"`` rescores the candidates with the cross-encoder.
            mode: ``"hybrid"`` fuses keyword matches with the vector list.
            workspace_id: Restrict to this workspace's knowledge bases (admin
                path); ``None`` reads across workspaces (the worker's ids come
                from a resolved agent config).

        Returns:
            The hits plus the dropped count, the warnings and stage timings.
        """
        started = time.perf_counter()
        response = KnowledgeSearchResponse(hits=[], mode=mode, rerank=rerank, min_score=min_score)
        ids = list(dict.fromkeys(kb_ids))
        if not ids or not query.strip() or k <= 0:
            return response

        searchable = await self._searchable(ids, workspace_id, response.warnings)
        if not searchable:
            return self._finish(response, started)

        vector = await self._cache.embed(self._embedder, query)
        response.timings_ms["embed"] = _ms(started)
        if vector is None:
            return self._finish(response, started)

        retrieve_started = time.perf_counter()
        native_hybrid = mode == "hybrid" and store_capabilities(self._store).hybrid
        lexical_task = (
            self._lexical(searchable, query, response.warnings)
            if mode == "hybrid" and not native_hybrid
            else _no_lexical()
        )
        vector_hits, lexical = await asyncio.gather(
            self._vector_fan_out(
                searchable, vector, response.warnings, text=query if native_hybrid else None
            ),
            lexical_task,
        )
        candidates = await self._join(vector_hits, lexical, set(searchable))
        response.timings_ms["retrieve"] = _ms(retrieve_started)

        ranked = self._rank(candidates, vector_hits, lexical, mode)
        if rerank == "local":
            rerank_started = time.perf_counter()
            ranked = await self._rerank(query, ranked, response.warnings)
            response.timings_ms["rerank"] = _ms(rerank_started)

        top = ranked[:k]
        kept = [c for c in top if min_score is None or c.score >= min_score]
        response.dropped = len(top) - len(kept)
        response.hits = [c.hit() for c in kept]
        return self._finish(response, started)

    # ------------------------------------------------------------------- stages
    async def _searchable(
        self, ids: list[str], workspace_id: str | None, warnings: list[KnowledgeSearchWarning]
    ) -> list[str]:
        statement = select(KnowledgeBase).where(KnowledgeBase.id.in_(ids))
        if workspace_id is not None:
            statement = statement.where(KnowledgeBase.workspace_id == workspace_id)
        else:
            statement = statement.execution_options(**{CROSS_WORKSPACE_OPTION: True})
        rows = {row.id: row for row in (await self._session.execute(statement)).scalars()}
        searchable: list[str] = []
        for kb_id in ids:
            row = rows.get(kb_id)
            if row is None:
                warnings.append(
                    KnowledgeSearchWarning(
                        code="kb_not_found", kb_id=kb_id, message=f"unknown knowledge base '{kb_id}'"
                    )
                )
                continue
            reason = kb_embedder_mismatch(row, self._embedder)
            if reason is not None:
                log.warning("kb_search_skipped_embedder_mismatch", kb_id=kb_id)
                warnings.append(
                    KnowledgeSearchWarning(code="kb_embedder_mismatch", kb_id=kb_id, message=reason)
                )
                continue
            searchable.append(kb_id)
        return searchable

    async def _vector_fan_out(
        self,
        kb_ids: list[str],
        vector: list[float],
        warnings: list[KnowledgeSearchWarning],
        *,
        text: str | None = None,
    ) -> list[VectorHit]:
        """Query every knowledge base at once; merge by score, best first, cut to :data:`CANDIDATES`.

        ``text`` is passed only to a hybrid-capable store; its hits then carry fused scores.
        """

        async def one(kb_id: str) -> list[VectorHit]:
            try:
                search = (
                    self._store.query(kb_id, vector, CANDIDATES)
                    if text is None
                    else self._store.query(kb_id, vector, CANDIDATES, text=text)
                )
                return await asyncio.wait_for(search, self._timeout)
            except KbEmbedderMismatchError as exc:
                log.warning("kb_search_skipped_embedder_mismatch", kb_id=kb_id, source="store")
                warnings.append(
                    KnowledgeSearchWarning(code="kb_embedder_mismatch", kb_id=kb_id, message=exc.message)
                )
            except TimeoutError:
                log.warning("kb_search_timeout", kb_id=kb_id, timeout_s=self._timeout)
                warnings.append(
                    KnowledgeSearchWarning(
                        code="kb_timeout",
                        kb_id=kb_id,
                        message=f"knowledge base '{kb_id}' did not answer within {self._timeout:g} s",
                    )
                )
            except Exception as exc:  # noqa: BLE001 - one knowledge base must not fail the search
                log.warning("kb_search_failed", kb_id=kb_id, error_type=type(exc).__name__)
                warnings.append(
                    KnowledgeSearchWarning(
                        code="kb_error",
                        kb_id=kb_id,
                        message=f"knowledge base '{kb_id}' could not be searched ({type(exc).__name__})",
                    )
                )
            return []

        per_kb = await asyncio.gather(*(one(kb_id) for kb_id in kb_ids))
        merged: dict[str, VectorHit] = {}
        position: dict[str, int] = {}
        for hits in per_kb:
            for index, hit in enumerate(hits):
                if hit.id not in merged or hit.score > merged[hit.id].score:
                    merged[hit.id] = hit
                    position[hit.id] = index

        def order(hit: VectorHit) -> tuple[float, int, str]:
            # A store-fused list is already ordered with fuse_rrf's tie-break (equal
            # RRF sums are common); keep that order among equal scores.
            return (-hit.score, position[hit.id] if hit.fused else 0, hit.id)

        return sorted(merged.values(), key=order)[:CANDIDATES]

    async def _lexical(
        self, kb_ids: list[str], query: str, warnings: list[KnowledgeSearchWarning]
    ) -> LexicalResult:
        try:
            result = await lexical_search(self._session, kb_ids=kb_ids, query=query, limit=CANDIDATES)
        except SQLAlchemyError as exc:
            log.warning("kb_lexical_search_failed", error_type=type(exc).__name__)
            result = LexicalResult(hits=[], available=False)
        if not result.available:
            warnings.append(
                KnowledgeSearchWarning(
                    code="lexical_unavailable",
                    message="keyword search is unavailable (the database migrations have not been applied); "
                    "results are vector-only",
                )
            )
        return result

    async def _join(
        self, vector_hits: list[VectorHit], lexical: LexicalResult, kb_ids: set[str]
    ) -> dict[str, _Candidate]:
        ids = list(dict.fromkeys([hit.id for hit in vector_hits] + [hit.chunk_id for hit in lexical.hits]))
        if not ids:
            return {}
        chunks = {
            row.id: row
            for row in (await self._session.execute(select(KbChunk).where(KbChunk.id.in_(ids)))).scalars()
            if row.kb_id in kb_ids
        }
        document_ids = {row.document_id for row in chunks.values()}
        filenames: dict[str, str] = {}
        if document_ids:
            rows = await self._session.execute(
                select(KbDocument.id, KbDocument.filename).where(KbDocument.id.in_(document_ids))
            )
            filenames = {str(document_id): str(filename) for document_id, filename in rows.all()}
        return {
            chunk_id: _Candidate(chunk=chunk, filename=filenames.get(chunk.document_id, ""))
            for chunk_id, chunk in chunks.items()
        }

    @staticmethod
    def _rank(
        candidates: dict[str, _Candidate],
        vector_hits: list[VectorHit],
        lexical: LexicalResult,
        mode: SearchMode,
    ) -> list[_Candidate]:
        if mode == "hybrid" and any(hit.fused for hit in vector_hits):
            return KnowledgeService._rank_native_hybrid(candidates, vector_hits)
        vector_ids = [hit.id for hit in vector_hits if hit.id in candidates]
        for hit in vector_hits:
            if hit.id in candidates:
                candidates[hit.id].vector_score = hit.score
        if mode == "vector":
            return [candidates[chunk_id] for chunk_id in vector_ids]

        lexical_ids = [hit.chunk_id for hit in lexical.hits if hit.chunk_id in candidates]
        for rank, chunk_id in enumerate(lexical_ids, start=1):
            candidates[chunk_id].lexical_rank = rank
        rankings = [vector_ids, lexical_ids] if lexical.available else [vector_ids]
        ranked: list[_Candidate] = []
        for item in fuse_rrf(rankings)[:CANDIDATES]:
            candidate = candidates[item.id]
            candidate.fused_score = item.score
            candidate.source = "fused"
            ranked.append(candidate)
        return ranked

    @staticmethod
    def _rank_native_hybrid(
        candidates: dict[str, _Candidate], fused_hits: list[VectorHit]
    ) -> list[_Candidate]:
        """The store already fused each knowledge base's lists; keep its scores and the merged order."""
        ranked: list[_Candidate] = []
        for hit in fused_hits:
            candidate = candidates.get(hit.id)
            if candidate is None:
                continue
            candidate.vector_score = hit.vector_score
            candidate.lexical_rank = hit.lexical_rank
            candidate.fused_score = hit.score
            candidate.source = "fused"
            ranked.append(candidate)
        return ranked[:CANDIDATES]

    async def _rerank(
        self, query: str, ranked: list[_Candidate], warnings: list[KnowledgeSearchWarning]
    ) -> list[_Candidate]:
        pool = ranked[:RERANK_POOL]
        if not pool:
            return ranked
        if self._reranker is None:
            warnings.append(KnowledgeSearchWarning(code="rerank_failed", message="no reranker is configured"))
            return ranked
        try:
            scores = await self._reranker.rerank(query, [c.chunk.text for c in pool])
        except Exception as exc:  # noqa: BLE001 - rerank is an optional stage; keep the fused order
            log.warning("kb_rerank_failed", error_type=type(exc).__name__)
            warnings.append(
                KnowledgeSearchWarning(
                    code="rerank_failed",
                    message=f"reranking failed ({type(exc).__name__}); order not reranked",
                )
            )
            return ranked
        if len(scores) != len(pool):
            warnings.append(
                KnowledgeSearchWarning(code="rerank_failed", message="the reranker returned the wrong count")
            )
            return ranked
        for candidate, logit in zip(pool, scores, strict=True):
            candidate.rerank_score = sigmoid(logit)
            candidate.source = "rerank"
        return sorted(pool, key=lambda c: -(c.rerank_score or 0.0))

    def _finish(self, response: KnowledgeSearchResponse, started: float) -> KnowledgeSearchResponse:
        response.timings_ms["total"] = _ms(started)
        log.debug(
            "kb_search",
            mode=response.mode,
            rerank=response.rerank,
            hits=len(response.hits),
            dropped=response.dropped,
            warnings=[warning.code for warning in response.warnings],
            **{f"{stage}_ms": value for stage, value in response.timings_ms.items()},
        )
        return response


async def _no_lexical() -> LexicalResult:
    return LexicalResult(hits=[], available=True)


def _ms(since: float) -> float:
    return round((time.perf_counter() - since) * 1000.0, 2)
