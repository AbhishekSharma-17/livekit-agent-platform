"""The ``cost_reconcile`` job: the vendor's own charge for a finished session (V4-17, D-V4-45).

Enqueued by ``PUT /internal/v1/sessions/{id}/summary`` **after** its commit
(ask #40's rule: :meth:`JobsService.enqueue` opens its own connection) when
:func:`lkap_api.costs.service.reconcile_due` says so: the workspace opted in
(``settings["cost"]["reconcile"]``) and the worker posted a
``metrics {kind: "provider_requests"}`` event.

One run, OpenRouter only (Deepgram is designed in
:mod:`lkap_api.costs.vendors.deepgram`, waiting for ask #94):

1. Read the session, its pinned config and the event's ``llm`` ids; the LLM slot
   must be an OpenRouter entry (the ids are matched by **slot**, never by the
   worker's display ``provider`` string). Reads close before any HTTP call.
2. :func:`~lkap_api.costs.vendors.openrouter.fetch_generations` through the
   job's ``net_guard`` client.
3. Any id answered ``404`` (or still failing) on the first attempt: the job
   re-enqueues itself once, :data:`RETRY_DELAY_S` later, and writes nothing yet.
4. Write: the summed ``total_cost`` per model goes on that model's input-token
   line (``vendor_usd``; ``vendor_ref`` = ``"<n> generations"``, the whole
   charge — OpenRouter does not split it into input and output) and the
   total on ``sessions.reconciled_usd``. Every earlier vendor figure on the
   slot's lines is cleared first, so a rerun is idempotent. An audit row
   ``cost_reconciled`` is written when ``reconciled_usd`` changed.

The key is never logged; log lines carry counts only.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Final

from fastapi import BackgroundTasks
from lkap_contracts import providers
from lkap_contracts.common import ProviderRef
from lkap_contracts.providers import OPENROUTER_CREDENTIAL_HOME, credential_home
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api import net_guard
from lkap_api.auth.audit import record
from lkap_api.costs.service import config_for_session
from lkap_api.costs.vendors import provider_requests_of, request_ids, workspace_reconcile_vendors
from lkap_api.costs.vendors.openrouter import (
    GenerationLookup,
    OpenRouterLookupError,
    fetch_generations,
    openrouter_api_key,
)
from lkap_api.db.guard import CROSS_WORKSPACE_OPTION
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.models import SessionCost as SessionCostRow
from lkap_api.db.models import utcnow
from lkap_api.jobs.context import JobContext
from lkap_api.jobs.kinds import COST_RECONCILE
from lkap_api.jobs.registry import job
from lkap_api.jobs.service import JobsService
from lkap_api.logging import get_logger

__all__ = ["RETRY_DELAY_S", "enqueue_reconcile", "handle_cost_reconcile", "reconcile_session"]

log = get_logger(__name__)

#: D-V4-45: a 404 is retried once, this many seconds later (OpenRouter does not document the delay).
RETRY_DELAY_S: Final = 30
_QUANT = Decimal("0.000001")

Sleep = Callable[[float], Awaitable[None]]

#: The pacing/backoff sleep the job handler uses (a module attribute so tests can replace it).
SLEEP: Sleep = asyncio.sleep


@dataclass(frozen=True, slots=True)
class _Plan:
    workspace_id: str
    ref: ProviderRef
    ids: list[str]
    api_key: str


async def _load_session(db: AsyncSession, session_id: str) -> SessionRow | None:
    # A job carries only the session id; the row decides the workspace (as in `qa/scorer.py`).
    row: SessionRow | None = (
        await db.execute(
            select(SessionRow)
            .where(SessionRow.id == session_id)
            .execution_options(**{CROSS_WORKSPACE_OPTION: True})
        )
    ).scalar_one_or_none()
    return row


def _is_openrouter_llm(ref: ProviderRef | None) -> bool:
    if ref is None:
        return False
    try:
        spec = providers.get(ref.provider_id)
    except KeyError:
        return False
    return spec.kind == "llm" and credential_home(spec) == OPENROUTER_CREDENTIAL_HOME


async def _plan(ctx: JobContext, session_id: str) -> _Plan | None:
    """Everything the lookup needs, or ``None`` (logged) when there is nothing to reconcile."""
    async with ctx.database.session() as db:
        session = await _load_session(db, session_id)
        if session is None:
            log.info("cost_reconcile_skipped", session_id=session_id, reason="no session")
            return None
        if "openrouter" not in await workspace_reconcile_vendors(db, session.workspace_id):
            log.info("cost_reconcile_skipped", session_id=session_id, reason="not opted in")
            return None
        ids = request_ids(await provider_requests_of(db, session_id), "llm")
        if not ids:
            log.info("cost_reconcile_skipped", session_id=session_id, reason="no llm request ids")
            return None
        config = await config_for_session(db, session)
        ref = config.pipeline.llm if config is not None else None
        if ref is None or not _is_openrouter_llm(ref):
            log.info("cost_reconcile_skipped", session_id=session_id, reason="llm slot is not OpenRouter")
            return None
        api_key = await openrouter_api_key(db, ctx.vault, workspace_id=session.workspace_id, ref=ref)
        if api_key is None:
            log.warning("cost_reconcile_skipped", session_id=session_id, reason="no OpenRouter credential")
            return None
        return _Plan(workspace_id=session.workspace_id, ref=ref, ids=ids, api_key=api_key)


def _usd(value: Decimal) -> float:
    return float(value.quantize(_QUANT, rounding=ROUND_HALF_UP))


async def _write(ctx: JobContext, session_id: str, plan: _Plan, lookup: GenerationLookup) -> None:
    """Put the vendor figures on the slot's lines and the session (idempotent)."""
    by_model: dict[str | None, list[Decimal]] = {}
    for generation in lookup.found.values():
        by_model.setdefault(generation.model, []).append(generation.total_cost)
    total = sum((cost for costs in by_model.values() for cost in costs), Decimal(0))
    unresolved = len(lookup.missing) + len(lookup.failed)

    async with ctx.database.session() as db:
        session = await _load_session(db, session_id)
        if session is None:  # pragma: no cover - deleted between the two reads
            return
        lines = list(
            (
                await db.execute(
                    select(SessionCostRow)
                    .where(
                        SessionCostRow.session_id == session_id,
                        SessionCostRow.provider_id == plan.ref.provider_id,
                    )
                    .order_by(SessionCostRow.id)
                )
            )
            .scalars()
            .all()
        )
        for line in lines:
            line.vendor_usd = None
            line.vendor_ref = None
        groups: dict[str, list[SessionCostRow]] = {}
        for line in lines:
            groups.setdefault(line.model, []).append(line)
        fallback_model = plan.ref.model or (next(iter(groups)) if groups else None)
        charged: dict[str, tuple[Decimal, int]] = {}
        for model, costs in by_model.items():
            target = model if model in groups else fallback_model
            if target is None or target not in groups:
                continue  # no persisted line to annotate (an unpriced model); still in the total
            amount, count = charged.get(target, (Decimal(0), 0))
            charged[target] = (amount + sum(costs, Decimal(0)), count + len(costs))
        for model, (amount, count) in charged.items():
            group = groups[model]
            anchor = next((line for line in group if line.unit == "tokens_in"), group[0])
            anchor.vendor_usd = _usd(amount)
            ref = f"{count} generations"
            if unresolved:
                ref += f", {unresolved} not found"
            anchor.vendor_ref = ref[:64]

        reconciled = _usd(total) if lookup.found else None
        changed = session.reconciled_usd != reconciled
        session.reconciled_usd = reconciled
        if changed:
            record(
                db,
                workspace_id=plan.workspace_id,
                actor_type="system",
                actor_id=None,
                action="cost_reconciled",
                target_type="sessions",
                target_id=session_id,
                payload={
                    "vendor": "openrouter",
                    "generations": len(lookup.found),
                    "not_found": unresolved,
                    "reconciled_usd": str(reconciled) if reconciled is not None else None,
                },
            )
    log.info(
        "cost_reconciled",
        session_id=session_id,
        vendor="openrouter",
        generations=len(lookup.found),
        not_found=unresolved,
        reconciled_usd=_usd(total) if lookup.found else None,
    )


async def reconcile_session(
    ctx: JobContext, session_id: str, *, attempt: int = 1, sleep: Sleep | None = None
) -> None:
    """Reconcile one session with OpenRouter's charge per generation.

    Args:
        ctx: The job context (database, vault, the ``net_guard`` HTTP client, jobs).
        session_id: The finished session.
        attempt: ``1`` for the first run, ``2`` for the one delayed retry.
        sleep: Request pacing and transient backoff; defaults to :data:`SLEEP`.
    """
    plan = await _plan(ctx, session_id)
    if plan is None:
        return
    try:
        lookup = await fetch_generations(
            ctx.http,
            api_key=plan.api_key,
            ids=plan.ids,
            policy=net_guard.policy_from_settings(ctx.settings),
            sleep=sleep or SLEEP,
        )
    except OpenRouterLookupError as exc:
        # A refused key or url does not get better on retry; nothing is written.
        log.warning("cost_reconcile_failed", session_id=session_id, reason=str(exc))
        return
    if (lookup.missing or lookup.failed) and attempt < 2:
        await ctx.jobs.enqueue(
            COST_RECONCILE,
            {"session_id": session_id, "attempt": attempt + 1},
            run_at=utcnow() + dt.timedelta(seconds=RETRY_DELAY_S),
        )
        log.info(
            "cost_reconcile_retry_scheduled",
            session_id=session_id,
            found=len(lookup.found),
            not_found=len(lookup.missing) + len(lookup.failed),
            delay_s=RETRY_DELAY_S,
        )
        return
    await _write(ctx, session_id, plan, lookup)


@job(COST_RECONCILE)
async def handle_cost_reconcile(ctx: JobContext, payload: dict[str, Any]) -> None:
    """Job handler: ``payload = {"session_id": ..., "attempt": 1 | 2}``."""
    attempt = payload.get("attempt", 1)
    await reconcile_session(
        ctx, str(payload["session_id"]), attempt=attempt if isinstance(attempt, int) else 1
    )


async def enqueue_reconcile(
    jobs: JobsService, session_id: str, *, background_tasks: BackgroundTasks | None = None
) -> str:
    """Schedule reconciliation for a finished session; call it only after the summary committed."""
    return await jobs.enqueue(
        COST_RECONCILE, {"session_id": session_id, "attempt": 1}, background_tasks=background_tasks
    )
