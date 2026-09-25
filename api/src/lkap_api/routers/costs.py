"""Cost estimates, price quotes and workspace prices (docs/v4/COSTS.md D-V4-42).

* ``POST /v1/cost-estimates`` — the per-minute estimate of an agent, a starter
  template or an unsaved draft config (exactly one); ``POST
  /v1/agents/{id}/cost-estimate`` (in ``routers/agents.py``) is the same handler.
* ``POST /v1/pricing/quotes`` — the pickers' batched quotes (≤ 100 items), each
  with that slot's own ≈ $/min share at default assumptions.
* ``GET /v1/cost-estimates/assumptions`` — the workspace's effective assumptions.
* ``GET``/``PUT /v1/workspace/prices`` — the admin-entered prices (USD).

Every figure here is an **estimate at list prices** — never a bill — and every
quote carries its source and date. Reads need ``agents:read``; writing prices
needs ``providers:write`` (admin).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends
from lkap_contracts import pricing, providers
from lkap_contracts.agent_config import AgentConfig, PipelineConfig
from lkap_contracts.api_models import (
    CostAssumptionsOut,
    CostEstimate,
    CostEstimateRequest,
    PriceQuoteItem,
    PriceQuotesRequest,
    PriceQuotesResponse,
    WorkspacePricesIn,
    WorkspacePricesOut,
)
from lkap_contracts.common import ProviderRef
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.auth.deps import WorkspaceContext, require
from lkap_api.costs.assumptions import (
    apply_overrides,
    default_assumptions,
    merge,
    workspace_assumptions,
)
from lkap_api.costs.estimate import build_estimate, slot_per_minute
from lkap_api.costs.prices import (
    WorkspacePriceError,
    load_price_book,
    load_workspace_prices,
    pipeline_refs,
    store_workspace_prices,
)
from lkap_api.costs.service import sip_model_for_number
from lkap_api.costs.snapshot import embedding_provider_id
from lkap_api.db.models import Agent, PhoneNumber
from lkap_api.deps import DbDep, SettingsDep
from lkap_api.errors import NotFoundError, UnprocessableEntityError
from lkap_api.logging import get_logger
from lkap_api.settings import Settings
from lkap_api.templates.router import resolve_template
from lkap_api.templates.seed import seed_from_template

log = get_logger(__name__)

router = APIRouter(tags=["costs"])

CostReaderDep = Annotated[WorkspaceContext, Depends(require("viewer", "agents:read"))]
PriceWriterDep = Annotated[WorkspaceContext, Depends(require("admin", "providers:write"))]

#: The prompt size a picker's per-minute figure assumes (the COSTS.md §3.3 worked example).
PICKER_PROMPT_TOKENS = 1500.0

_SLOT_BY_KIND: dict[str, str] = {
    "stt": "stt",
    "llm": "llm",
    "tts": "tts",
    "realtime": "realtime",
    "avatar": "avatar",
}


async def _agent(db: AsyncSession, ctx: WorkspaceContext, id_or_slug: str) -> Agent:
    row = (
        await db.execute(
            select(Agent)
            .where(
                Agent.workspace_id == ctx.workspace_id, or_(Agent.id == id_or_slug, Agent.slug == id_or_slug)
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"unknown agent '{id_or_slug}'")
    return row


async def _agent_sip_model(db: AsyncSession, ctx: WorkspaceContext, agent_id: str) -> str:
    row = (
        await db.execute(
            select(PhoneNumber.e164, PhoneNumber.source)
            .where(PhoneNumber.workspace_id == ctx.workspace_id, PhoneNumber.inbound_agent_id == agent_id)
            .limit(1)
        )
    ).first()
    return sip_model_for_number(row[0], row[1]) if row is not None else "trunk"


async def estimate_config(
    db: AsyncSession,
    ctx: WorkspaceContext,
    settings: Settings,
    config: AgentConfig,
    request: CostEstimateRequest,
    *,
    sip_model: str = "trunk",
) -> CostEstimate:
    """Estimate one config for the caller's workspace (its prices; its averages when asked)."""
    layers = [default_assumptions(config)]
    if request.workspace_averages:
        layers.append((await workspace_assumptions(db, ctx.workspace_id)).assumptions)
    try:
        assumptions = apply_overrides(merge(*layers), request.assumptions)
    except ValueError as exc:
        raise UnprocessableEntityError(
            str(exc), details={"assumptions": sorted(request.assumptions or {})}
        ) from exc
    book = await load_price_book(db, ctx.workspace_id, pipeline_refs(config.pipeline, [config.qa.model]))
    return build_estimate(
        config,
        assumptions,
        book,
        channel=request.channel,
        sip_model=sip_model,
        embedding_provider=embedding_provider_id(settings.embedder),
    )


async def estimate_for_request(
    db: AsyncSession, ctx: WorkspaceContext, settings: Settings, request: CostEstimateRequest
) -> CostEstimate:
    """Resolve the request's one source (agent, template or draft) and estimate it.

    Raises:
        UnprocessableEntityError: None of the three sources, an unknown template, or a bad assumption.
        NotFoundError: An unknown agent.
    """
    if request.agent_id is not None:
        agent = await _agent(db, ctx, request.agent_id)
        sip_model = await _agent_sip_model(db, ctx, agent.id)
        return await estimate_config(
            db, ctx, settings, AgentConfig.model_validate(agent.config), request, sip_model=sip_model
        )
    if request.template_id is not None:
        template, manifest = resolve_template(settings.packs_list, request.template_id)
        config = seed_from_template(template, manifest, credentials_by_provider={})
        return await estimate_config(db, ctx, settings, config, request)
    if request.config is not None:
        return await estimate_config(db, ctx, settings, request.config, request)
    raise UnprocessableEntityError("give one of agent_id, template_id or config")


@router.post(
    "/v1/cost-estimates",
    response_model=CostEstimate,
    summary="Estimate an agent's cost per minute",
    description=(
        "An **estimate** at list prices (never a bill) of what an agent, a starter template or an "
        "unsaved draft config costs per minute and per call: a breakdown by part of the agent, the "
        "assumptions used (overridable), a low-high band, the unpriced parts, and the source and "
        "date of every price."
    ),
)
async def create_cost_estimate(
    payload: CostEstimateRequest, db: DbDep, ctx: CostReaderDep, settings: SettingsDep
) -> CostEstimate:
    """Estimate the cost of the requested agent, template or draft."""
    return await estimate_for_request(db, ctx, settings, payload)


@router.get(
    "/v1/cost-estimates/assumptions",
    response_model=CostAssumptionsOut,
    summary="The assumptions behind a cost estimate",
    description=(
        "The usage model the **estimate** uses: every assumption (call length, talk ratios, replies "
        "per minute, ...) with its low-high band and source — the default, or the workspace's own "
        "session averages once it has at least 10 ended sessions."
    ),
)
async def get_cost_assumptions(db: DbDep, ctx: CostReaderDep) -> CostAssumptionsOut:
    """Return the workspace's effective assumptions."""
    averages = await workspace_assumptions(db, ctx.workspace_id)
    merged = merge(default_assumptions(None), averages.assumptions)
    return CostAssumptionsOut(assumptions=list(merged.values()), sessions_sampled=averages.sessions_sampled)


def _picker_config(kind: str, ref: ProviderRef) -> AgentConfig | None:
    slot = _SLOT_BY_KIND.get(kind)
    if slot is None:
        return None
    mode = "realtime" if slot == "realtime" else "cascaded"
    return AgentConfig(instructions="", pipeline=PipelineConfig(mode=mode, **{slot: ref}))


@router.post(
    "/v1/pricing/quotes",
    response_model=PriceQuotesResponse,
    summary="Price quotes for model pickers",
    description=(
        "Quotes (workspace price, else OpenRouter's live sheet, else the list-price table) for up to "
        "100 provider/model pairs, each with that slot's own **estimate** per minute at default "
        "assumptions. The body carries `price_version` and `as_of`; cache it client-side."
    ),
)
async def create_price_quotes(
    payload: PriceQuotesRequest, db: DbDep, ctx: CostReaderDep
) -> PriceQuotesResponse:
    """Quote every requested provider/model."""
    pairs = [(item.provider_id, item.model) for item in payload.items]
    book = await load_price_book(db, ctx.workspace_id, pairs)
    items: list[PriceQuoteItem] = []
    dates: list[str] = []
    for item in payload.items:
        try:
            spec = providers.get(item.provider_id)
        except KeyError:
            items.append(
                PriceQuoteItem(provider_id=item.provider_id, model=item.model, note="unknown provider")
            )
            continue
        model = item.model or spec.default_model
        quotes = [q for unit in pricing.UNITS if (q := book(item.provider_id, model, unit)) is not None]  # type: ignore[arg-type]
        dates.extend(q.as_of for q in quotes)
        per_minute: Decimal | None = None
        config = _picker_config(spec.kind, ProviderRef(provider_id=item.provider_id, model=model))
        if config is not None:
            assumptions = apply_overrides(
                default_assumptions(config),
                {"prompt_tokens": PICKER_PROMPT_TOKENS, "tool_calls_per_session": 0},
            )
            estimate = build_estimate(config, assumptions, book, embedding_provider=None)
            per_minute = slot_per_minute(estimate, _SLOT_BY_KIND[spec.kind])
        items.append(
            PriceQuoteItem(
                provider_id=item.provider_id,
                model=model,
                kind=spec.kind,
                quotes=quotes,
                per_minute_usd=per_minute,
                note=None if quotes else "no price",
            )
        )
    return PriceQuotesResponse(
        items=items, price_version=pricing.PRICE_VERSION, as_of=min(dates) if dates else pricing.PRICE_VERSION
    )


@router.get(
    "/v1/workspace/prices",
    response_model=WorkspacePricesOut,
    summary="The workspace's own prices",
    description=(
        "Prices an admin entered for plan-based vendors (USD per unit, dated). Every **estimate** "
        "and cost line uses them before OpenRouter's live sheet and the list-price table."
    ),
)
async def get_workspace_prices(db: DbDep, ctx: CostReaderDep) -> WorkspacePricesOut:
    """Return the workspace's prices."""
    return WorkspacePricesOut(prices=await load_workspace_prices(db, ctx.workspace_id))


@router.put(
    "/v1/workspace/prices",
    response_model=WorkspacePricesOut,
    summary="Replace the workspace's own prices",
    description=(
        "Admin only. Replaces the full list (at most 100 rows; `provider_id` a registry id or a "
        "LiveKit Cloud pseudo id; USD). Unchanged rows keep their date; new or edited rows are "
        "dated today. Used by every later **estimate** and cost line; audited as "
        "`workspace.prices_updated`."
    ),
)
async def put_workspace_prices(
    payload: WorkspacePricesIn, db: DbDep, ctx: PriceWriterDep
) -> WorkspacePricesOut:
    """Validate and store the workspace's prices."""
    try:
        stored = await store_workspace_prices(
            db, ctx.workspace_id, payload.prices, actor_type=ctx.actor.actor_type, actor_id=ctx.actor.id
        )
    except WorkspacePriceError as exc:
        raise UnprocessableEntityError(str(exc)) from exc
    log.info("workspace_prices_updated", workspace_id=ctx.workspace_id, count=len(stored))
    return WorkspacePricesOut(prices=stored)
