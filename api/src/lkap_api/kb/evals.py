"""The retrieval eval harness: score a knowledge base's golden questions (V5-05, K §2 P0-9).

A knowledge base's evaluation set (``kb_evals``, V5-01) holds golden
questions, each naming the document a correct hit comes from
(``expected_document_id``), a passage a correct hit contains
(``expected_text``), or both. ``POST /v1/knowledge-bases/{id}/evaluate``
enqueues :data:`~lkap_api.jobs.kinds.KB_EVALUATE`; the job runs every question
through :meth:`~lkap_api.kb.service.KnowledgeService.search` with the chosen
options and scores the run:

* **found** — one of the top ``k`` hits is the expected document **or**
  contains the expected text (whitespace-collapsed, case-insensitive
  substring, so a passage that wraps across lines still matches);
  ``rank`` is the position of the first such hit.
* **missed** — no top-``k`` hit matches (``reciprocal_rank`` 0).
* **skipped** — the expected document was deleted after the set was put
  (the column has no foreign key on purpose). Skipped questions are left out
  of every average.

``recall_at_k`` is found / scored, ``recall_at_1`` the share found first and
``mrr`` the mean reciprocal rank over the scored questions; ``by_tag`` repeats
the three per tag (how D-V5-15 measures transliterated Hindi separately).

**Where a run is kept.** No table: the result is written into the job row's
own ``payload`` under ``"result"`` (the ``jobs`` table has no result column and
:class:`~lkap_api.jobs.context.JobContext` carries no job id, so the payload
also carries a ``run_id`` the handler finds its row by). The routes
``GET .../evaluate/{job_id}`` and ``GET .../evaluate/latest`` read it back.
"""

from __future__ import annotations

import re
import statistics
import time
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Literal, Protocol, cast, get_args

from fastapi import BackgroundTasks
from lkap_contracts.api_models import KbRerankMode, KbSearchMode, KbSearchResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.db.models import Job, KbDocument, KbEval, KnowledgeBase, new_id, utcnow
from lkap_api.jobs.context import JobContext
from lkap_api.jobs.kinds import KB_EVALUATE
from lkap_api.jobs.registry import job
from lkap_api.jobs.service import JobsService
from lkap_api.kb.embed import resolve_embedder
from lkap_api.kb.ingest import warm_embedder
from lkap_api.kb.rerank import get_local_reranker
from lkap_api.kb.service import KnowledgeService
from lkap_api.kb.store import resolve_store
from lkap_api.logging import get_logger

log = get_logger(__name__)

#: How many of each question's top hits the stored result keeps (ids and scores only, never text).
TOP_HITS_KEPT = 3

EvalStatus = Literal["found", "missed", "skipped"]
EvalSkipReason = Literal["expected_document_deleted"]
EvalRunStatus = Literal["pending", "running", "done", "failed", "dead"]
_RUN_STATUSES: frozenset[str] = frozenset(get_args(EvalRunStatus))


# --------------------------------------------------------------------------- models
class KbEvaluateIn(BaseModel):
    """``POST /v1/knowledge-bases/{id}/evaluate``: the search options every question runs with.

    The defaults are an agent's knowledge defaults (``KnowledgeConfig``), so a
    bare call measures what a new agent retrieves.
    """

    model_config = ConfigDict(extra="forbid")

    mode: KbSearchMode = Field(
        default="hybrid",
        description="`hybrid` (keyword matches fused with embedding similarity) or `vector`.",
    )
    rerank: KbRerankMode = Field(
        default="none", description="`local` rescores the top candidates with the local cross-encoder."
    )
    min_score: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Drop hits whose `score` is below this (0-1)."
    )
    k: int = Field(default=4, ge=1, le=20, description="A question is found when a match is in the top `k`.")


class KbEvalHitRef(BaseModel):
    """One of a question's top hits, by reference (no chunk text is stored)."""

    chunk_id: str
    document_id: str
    filename: str
    score: float


class KbEvalItemResult(BaseModel):
    """How one golden question scored."""

    eval_id: str
    question: str
    expected_document_id: str | None = None
    expected_text: str | None = None
    tags: list[str] = Field(default_factory=list)
    status: EvalStatus
    skip_reason: EvalSkipReason | None = None
    rank: int | None = Field(default=None, description="1-based position of the first matching hit.")
    reciprocal_rank: float = 0.0
    latency_ms: float | None = None
    top_hits: list[KbEvalHitRef] = Field(default_factory=list)


class KbEvalTagScore(BaseModel):
    """The scores of the questions carrying one tag."""

    tag: str
    scored: int
    found: int
    recall_at_k: float | None
    recall_at_1: float | None
    mrr: float | None


class KbEvalResult(BaseModel):
    """One evaluation run: the options, the totals, the scores and every question's outcome."""

    kb_id: str
    mode: KbSearchMode
    rerank: KbRerankMode
    min_score: float | None
    k: int
    embedder_model: str | None = None
    total: int
    scored: int = Field(description="Questions that were run (total minus skipped).")
    found: int
    skipped: int
    recall_at_k: float | None = Field(description="found / scored; null when nothing could be scored.")
    recall_at_1: float | None = Field(description="Share of scored questions whose first hit matched.")
    mrr: float | None = Field(description="Mean reciprocal rank over the scored questions.")
    latency_ms_p50: float | None = None
    by_tag: list[KbEvalTagScore] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list, description="Distinct search warnings seen in the run.")
    items: list[KbEvalItemResult] = Field(default_factory=list)
    started_at: datetime
    finished_at: datetime


class KbEvalRunOut(BaseModel):
    """An evaluation job: its status and, once ``done``, its result."""

    job_id: str
    kb_id: str
    status: EvalRunStatus
    error: str | None = None
    options: KbEvaluateIn
    result: KbEvalResult | None = None
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------------------------------- scoring
class EvalSearch(Protocol):
    """Runs one question; the job binds it to :meth:`KnowledgeService.search` with the run's options."""

    async def __call__(self, query: str) -> KbSearchResponse:
        """Return the search response for ``query``."""
        ...


_WHITESPACE = re.compile(r"\s+")


def normalise_text(text: str) -> str:
    """Collapse whitespace and casefold, so a passage that wraps across lines still matches."""
    return _WHITESPACE.sub(" ", text).strip().casefold()


def _ratio(numerator: float, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _tag_scores(items: Sequence[KbEvalItemResult]) -> list[KbEvalTagScore]:
    tags = sorted({tag for item in items for tag in item.tags})
    scores: list[KbEvalTagScore] = []
    for tag in tags:
        scored = [item for item in items if tag in item.tags and item.status != "skipped"]
        found = [item for item in scored if item.status == "found"]
        scores.append(
            KbEvalTagScore(
                tag=tag,
                scored=len(scored),
                found=len(found),
                recall_at_k=_ratio(len(found), len(scored)),
                recall_at_1=_ratio(sum(1 for item in found if item.rank == 1), len(scored)),
                mrr=_ratio(sum(item.reciprocal_rank for item in scored), len(scored)),
            )
        )
    return scores


async def score_evals(
    evals: Sequence[KbEval],
    *,
    kb_id: str,
    present_document_ids: set[str],
    search: EvalSearch,
    options: KbEvaluateIn,
    embedder_model: str | None = None,
) -> KbEvalResult:
    """Run every question through ``search`` and score the run.

    Args:
        evals: The evaluation set, in its stored order.
        kb_id: The knowledge base evaluated (recorded on the result).
        present_document_ids: The knowledge base's current documents; a
            question whose ``expected_document_id`` is not among them is skipped.
        search: Runs one question with the run's options.
        options: The run's options (``k`` cuts the hits scored).
        embedder_model: Recorded on the result so runs from different embedders are told apart.

    Returns:
        The totals, recall@k, recall@1, MRR, the per-tag scores and every question's outcome.
    """
    started_at = utcnow()
    items: list[KbEvalItemResult] = []
    warnings: list[str] = []
    latencies: list[float] = []
    for row in evals:
        item = KbEvalItemResult(
            eval_id=row.id,
            question=row.question,
            expected_document_id=row.expected_document_id,
            expected_text=row.expected_text,
            tags=list(row.tags or []),
            status="missed",
        )
        if row.expected_document_id is not None and row.expected_document_id not in present_document_ids:
            item.status = "skipped"
            item.skip_reason = "expected_document_deleted"
            items.append(item)
            continue
        began = time.perf_counter()
        response = await search(row.question)
        item.latency_ms = round((time.perf_counter() - began) * 1000.0, 2)
        latencies.append(item.latency_ms)
        for warning in response.warnings:
            if warning.message not in warnings:
                warnings.append(warning.message)
        hits = response.hits[: options.k]
        expected_text = normalise_text(row.expected_text) if row.expected_text else None
        for position, hit in enumerate(hits, start=1):
            by_document = row.expected_document_id is not None and hit.document_id == row.expected_document_id
            by_text = expected_text is not None and expected_text in normalise_text(hit.text)
            if by_document or by_text:
                item.status = "found"
                item.rank = position
                item.reciprocal_rank = 1.0 / position
                break
        item.top_hits = [
            KbEvalHitRef(
                chunk_id=hit.chunk_id, document_id=hit.document_id, filename=hit.filename, score=hit.score
            )
            for hit in hits[:TOP_HITS_KEPT]
        ]
        items.append(item)

    scored = [item for item in items if item.status != "skipped"]
    found = [item for item in scored if item.status == "found"]
    return KbEvalResult(
        kb_id=kb_id,
        mode=options.mode,
        rerank=options.rerank,
        min_score=options.min_score,
        k=options.k,
        embedder_model=embedder_model,
        total=len(items),
        scored=len(scored),
        found=len(found),
        skipped=len(items) - len(scored),
        recall_at_k=_ratio(len(found), len(scored)),
        recall_at_1=_ratio(sum(1 for item in found if item.rank == 1), len(scored)),
        mrr=_ratio(sum(item.reciprocal_rank for item in scored), len(scored)),
        latency_ms_p50=round(statistics.median(latencies), 2) if latencies else None,
        by_tag=_tag_scores(items),
        warnings=warnings,
        items=items,
        started_at=started_at,
        finished_at=utcnow(),
    )


# --------------------------------------------------------------------------- storage
async def load_evals(db: AsyncSession, kb_id: str) -> list[KbEval]:
    """The knowledge base's evaluation set in the order it was put (``ordinal``)."""
    return list(
        (
            await db.execute(
                select(KbEval).where(KbEval.kb_id == kb_id).order_by(KbEval.ordinal, KbEval.created_at)
            )
        )
        .scalars()
        .all()
    )


def kb_evaluate_payload(
    *, kb_id: str, workspace_id: str, options: KbEvaluateIn, run_id: str | None = None
) -> dict[str, Any]:
    """The ``kb_evaluate`` job payload; the handler adds ``result`` to it when the run finishes."""
    return {
        "run_id": run_id or new_id(),
        "kb_id": kb_id,
        "workspace_id": workspace_id,
        "options": options.model_dump(),
    }


async def enqueue_kb_evaluate(
    jobs: JobsService,
    *,
    kb_id: str,
    workspace_id: str,
    options: KbEvaluateIn,
    background_tasks: BackgroundTasks | None = None,
) -> str:
    """Enqueue an evaluation run of ``kb_id``; returns the job id."""
    payload = kb_evaluate_payload(kb_id=kb_id, workspace_id=workspace_id, options=options)
    return await jobs.enqueue(KB_EVALUATE, payload, background_tasks=background_tasks)


def run_out(row: Job) -> KbEvalRunOut:
    """A ``kb_evaluate`` job row as the routes return it."""
    payload = dict(row.payload or {})
    raw_result = payload.get("result")
    result: KbEvalResult | None = None
    if isinstance(raw_result, dict):
        try:
            result = KbEvalResult.model_validate(raw_result)
        except ValidationError:
            log.warning("kb_evaluate_result_unreadable", job_id=row.id)
    status = cast(EvalRunStatus, row.status if row.status in _RUN_STATUSES else "failed")
    return KbEvalRunOut(
        job_id=row.id,
        kb_id=str(payload.get("kb_id", "")),
        status=status,
        error=row.last_error,
        options=KbEvaluateIn.model_validate(payload.get("options") or {}),
        result=result,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def get_run(db: AsyncSession, kb_id: str, job_id: str) -> Job | None:
    """The ``kb_evaluate`` job ``job_id`` of ``kb_id``, or ``None`` (another kind or knowledge base too)."""
    row = await db.get(Job, job_id)
    if row is None or row.kind != KB_EVALUATE or (row.payload or {}).get("kb_id") != kb_id:
        return None
    return row


async def latest_run(db: AsyncSession, kb_id: str) -> Job | None:
    """The most recently finished ``done`` evaluation of ``kb_id``, or ``None``."""
    return (
        (
            await db.execute(
                select(Job)
                .where(
                    Job.kind == KB_EVALUATE,
                    Job.status == "done",
                    Job.payload["kb_id"].as_string() == kb_id,
                )
                .order_by(Job.updated_at.desc(), Job.created_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )


async def _store_result(ctx: JobContext, run_id: str, result: KbEvalResult) -> None:
    async with ctx.database.session() as session:
        row = (
            (
                await session.execute(
                    select(Job).where(Job.kind == KB_EVALUATE, Job.payload["run_id"].as_string() == run_id)
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            raise LookupError(f"kb_evaluate job for run '{run_id}' not found")
        # Reassigned, never mutated in place: a plain JSON column does not track nested changes.
        row.payload = {**dict(row.payload or {}), "result": result.model_dump(mode="json")}


# --------------------------------------------------------------------------- the job
@job(KB_EVALUATE)
async def run_kb_evaluate_job(ctx: JobContext, payload: dict[str, Any]) -> None:
    """Job handler: score a knowledge base's evaluation set and store the result on the job row.

    Args:
        ctx: The job context (``database``, ``settings``, ``vault``).
        payload: :func:`kb_evaluate_payload`'s ``{"run_id", "kb_id", "workspace_id", "options"}``.

    Raises:
        LookupError: The knowledge base was deleted before the run started (the
            job is retried, then marked ``dead`` with this message).
    """
    run_id = str(payload["run_id"])
    kb_id = str(payload["kb_id"])
    workspace_id = str(payload["workspace_id"])
    options = KbEvaluateIn.model_validate(payload.get("options") or {})

    async with ctx.database.session() as session:
        kb = await session.scalar(
            select(KnowledgeBase).where(KnowledgeBase.id == kb_id, KnowledgeBase.workspace_id == workspace_id)
        )
        if kb is None:
            raise LookupError(f"knowledge base '{kb_id}' no longer exists")
        embedder = await resolve_embedder(ctx.settings, session, ctx.vault)
    # Loaded before the search session opens (a first model load can take long).
    await warm_embedder(embedder)

    local = options.rerank == "local"
    reranker = get_local_reranker(ctx.settings.data_dir, ctx.settings.rerank_model) if local else None
    async with ctx.database.session() as session:
        evals = await load_evals(session, kb_id)
        documents = await session.execute(select(KbDocument.id).where(KbDocument.kb_id == kb_id))
        present = set(documents.scalars())
        # V5-13: resolved per session (pgvector queries run on it).
        store = resolve_store(ctx.settings, session)
        service = KnowledgeService(session, store=store, embedder=embedder, reranker=reranker)

        async def search(query: str) -> KbSearchResponse:
            return await service.search(
                [kb_id],
                query,
                options.k,
                min_score=options.min_score,
                rerank=options.rerank,
                mode=options.mode,
                workspace_id=workspace_id,
            )

        result = await score_evals(
            evals,
            kb_id=kb_id,
            present_document_ids=present,
            search=search,
            options=options,
            embedder_model=embedder.model_id,
        )
    await _store_result(ctx, run_id, result)
    log.info(
        "kb_evaluate_done",
        kb_id=kb_id,
        mode=options.mode,
        rerank=options.rerank,
        k=options.k,
        scored=result.scored,
        skipped=result.skipped,
        recall_at_k=result.recall_at_k,
        mrr=result.mrr,
    )
