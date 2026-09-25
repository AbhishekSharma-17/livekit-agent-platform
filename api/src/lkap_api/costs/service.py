"""Cost computation: prices × real usage -> `session_costs`, and estimate vs actual (COSTS.md §4).

Two write paths call into this module, both idempotent so a retried or
duplicated call never double-charges a session:

* :func:`cost_session` — called once from the session summary: every billable
  field the SDK reports (D-V4-44: realtime text/audio splits, cached tokens,
  token-billed speech, turn-detector requests), the LiveKit Cloud minutes from
  LKAP's own clocks (call minutes with the 10-second minimum, participant
  minutes, phone minutes), avatar minutes and image counts; then
  ``sessions.estimated_usd`` from the creation-time snapshot.
* :func:`add_egress_cost_line` — called from recording finalisation once a
  recording's duration is known (model ``audio``/``video`` from the config).

Only **priced** lines (`cost_usd is not None`) are ever written to
`session_costs` (its price columns are `NOT NULL`); unpriced lines are
recomputed on every read by :func:`render_cost` with `note="no price"`. No
path writes a zero for an unknown price. `sessions.cost_usd` and
`SessionCost.total_usd` are the sum of persisted lines, or `None`.

Prices come from a ``quote`` callable (:class:`lkap_api.costs.prices.PriceBook`):
the workspace's own price, then OpenRouter's cached sheet (an OpenRouter slot is
costed ``price_source="live"`` at the price listed when the session ended), then
the table. ``price_source`` is persisted with every line.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, cast

from lkap_contracts import pricing
from lkap_contracts.agent_config import AgentConfig, PipelineConfig
from lkap_contracts.api_models import (
    Assumption,
    CostDriver,
    CostEstimate,
    CostLine,
    DriverReason,
    EstimateChannel,
    EstimateLine,
    EstimateSlot,
    PriceSource,
)
from lkap_contracts.api_models import SessionCost as SessionCostOut
from lkap_contracts.common import ProviderRef
from lkap_contracts.pricing import Unit
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.costs.estimate import QuoteFn, estimated_usd_for, table_quote
from lkap_api.costs.mapping import resolve_model, slot_for_usage
from lkap_api.costs.prices import PriceBook, load_price_book, pipeline_refs
from lkap_api.db.models import Agent, AgentConfigVersion, PhoneNumber, SessionEvent
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.models import SessionCost as SessionCostRow
from lkap_api.logging import get_logger

log = get_logger(__name__)

_QUANT = Decimal("0.000001")
#: LiveKit meters agent session minutes in 1-second increments with a 10-second minimum.
AGENT_MINIMUM_S = Decimal(10)
#: US toll-free area codes: a LiveKit-hosted number in these is billed at the toll-free rate.
_TOLL_FREE = ("+1800", "+1833", "+1844", "+1855", "+1866", "+1877", "+1888")


@dataclass(slots=True, frozen=True)
class ComputedLine:
    """One usage line, priced or not — the pure output of :func:`compute_usage_lines`."""

    provider_id: str
    model: str | None
    unit: Unit
    quantity: Decimal
    unit_price_usd: Decimal | None = None
    cost_usd: Decimal | None = None
    note: str | None = None
    price_source: PriceSource | None = None
    slot: EstimateSlot | None = None

    @property
    def key(self) -> tuple[str, str | None, str]:
        """Identity used to avoid rendering an unpriced duplicate of a persisted line."""
        return (self.provider_id, self.model, self.unit)


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if value <= 0:
        return None
    return Decimal(str(value))


def _int(entry: Mapping[str, Any], key: str) -> Decimal:
    return _decimal(entry.get(key)) or Decimal(0)


def price_line(
    provider_id: str,
    model: str | None,
    unit: Unit,
    quantity: Decimal,
    *,
    quote: QuoteFn = table_quote,
    slot: EstimateSlot | None = None,
    note: str | None = None,
    priced: bool = True,
) -> ComputedLine:
    """Price one `(provider, model, unit, quantity)` triple.

    Never returns a zero cost for an unknown price: `cost_usd`/`unit_price_usd`
    are `None` with `note="no price"` (or the given note) instead.
    """
    found = quote(provider_id, model, unit) if priced else None
    if found is None:
        return ComputedLine(provider_id, model, unit, quantity, note=note or "no price", slot=slot)
    cost = (quantity * found.usd_per_unit).quantize(_QUANT, rounding=ROUND_HALF_UP)
    return ComputedLine(
        provider_id,
        model,
        unit,
        quantity,
        unit_price_usd=found.usd_per_unit,
        cost_usd=cost,
        note=note,
        price_source=found.source,
        slot=slot,
    )


def _has(quote: QuoteFn, provider_id: str, model: str | None, *units: Unit) -> bool:
    return any(quote(provider_id, model, unit) is not None for unit in units)


def _llm_lines(
    ref: ProviderRef, model: str | None, entry: Mapping[str, Any], quote: QuoteFn, slot: EstimateSlot
) -> list[ComputedLine]:
    """D-V4-44: realtime splits never priced at folded totals; cached tokens only with a cached quote."""
    pid = ref.provider_id
    lines: list[ComputedLine] = []
    input_total = _int(entry, "input_tokens")
    output_total = _int(entry, "output_tokens")
    audio_in = _int(entry, "input_audio_tokens")
    audio_out = _int(entry, "output_audio_tokens")
    text_in = _int(entry, "input_text_tokens")
    text_out = _int(entry, "output_text_tokens")
    cached = _int(entry, "input_cached_tokens")
    has_cached_quote = _has(quote, pid, model, "cached_tokens_in")
    split = _has(quote, pid, model, "text_tokens_in", "audio_tokens_in")

    def add(unit: Unit, qty: Decimal, **kw: Any) -> None:
        if qty > 0:
            lines.append(price_line(pid, model, unit, qty, quote=quote, slot=slot, **kw))

    if split:
        text_in_qty = text_in if text_in > 0 else max(input_total - audio_in, Decimal(0))
        text_out_qty = text_out if text_out > 0 else max(output_total - audio_out, Decimal(0))
        if has_cached_quote and cached > 0:
            add("cached_tokens_in", cached)
            text_in_qty = max(text_in_qty - cached, Decimal(0))
        add("text_tokens_in", text_in_qty)
        add("audio_tokens_in", audio_in)
        add("text_tokens_out", text_out_qty)
        add("audio_tokens_out", audio_out)
        return lines
    if audio_in > 0 or audio_out > 0:
        # Audio usage without an audio-token price: the text part at the text rate, the
        # audio part unpriced — never the folded total at the text rate (R-V4-45).
        add("tokens_in", text_in if text_in > 0 else max(input_total - audio_in, Decimal(0)))
        add("tokens_out", text_out if text_out > 0 else max(output_total - audio_out, Decimal(0)))
        add("audio_tokens_in", audio_in, priced=False, note="audio-token price unknown")
        add("audio_tokens_out", audio_out, priced=False, note="audio-token price unknown")
        return lines
    if cached > 0 and has_cached_quote:
        add("cached_tokens_in", cached)
        add("tokens_in", max(input_total - cached, Decimal(0)))
    else:
        add("tokens_in", input_total, note="cache discount not modelled" if cached > 0 else None)
    add("tokens_out", output_total)
    return lines


def _tts_lines(
    ref: ProviderRef, model: str | None, entry: Mapping[str, Any], quote: QuoteFn
) -> list[ComputedLine]:
    pid = ref.provider_id
    chars = _decimal(entry.get("characters_count"))
    tokens_in = _decimal(entry.get("input_tokens"))
    tokens_out = _decimal(entry.get("output_tokens"))
    audio = _decimal(entry.get("audio_duration"))
    token_billed = (tokens_in or tokens_out) and _has(quote, pid, model, "tokens_in", "tokens_out")
    if token_billed or (chars is None and (tokens_in or tokens_out)):
        out = []
        if tokens_in:
            out.append(price_line(pid, model, "tokens_in", tokens_in, quote=quote, slot="tts"))
        if tokens_out:
            out.append(price_line(pid, model, "tokens_out", tokens_out, quote=quote, slot="tts"))
        return out
    if chars is not None:
        return [price_line(pid, model, "chars", chars, quote=quote, slot="tts")]
    if audio is not None:
        return [price_line(pid, model, "audio_s_out", audio, quote=quote, slot="tts")]
    return []


def _stt_lines(
    ref: ProviderRef, model: str | None, entry: Mapping[str, Any], quote: QuoteFn
) -> list[ComputedLine]:
    pid = ref.provider_id
    audio_tokens = _decimal(entry.get("input_audio_tokens"))
    tokens_in = audio_tokens or _decimal(entry.get("input_tokens"))
    tokens_out = _decimal(entry.get("output_tokens"))
    audio = _decimal(entry.get("audio_duration"))
    if (tokens_in or tokens_out) and _has(quote, pid, model, "audio_tokens_in", "tokens_in"):
        out = []
        if tokens_in:
            unit: Unit = "audio_tokens_in" if _has(quote, pid, model, "audio_tokens_in") else "tokens_in"
            out.append(price_line(pid, model, unit, tokens_in, quote=quote, slot="stt"))
        if tokens_out:
            out.append(price_line(pid, model, "tokens_out", tokens_out, quote=quote, slot="stt"))
        return out
    if audio is not None:
        return [price_line(pid, model, "audio_s_in", audio, quote=quote, slot="stt")]
    return []


def compute_usage_lines(
    pipeline: PipelineConfig, usage: dict[str, Any] | None, *, quote: QuoteFn = table_quote
) -> list[ComputedLine]:
    """Turn a session's `usage` blob into priced (and honestly unpriced) cost lines.

    Args:
        pipeline: The `AgentConfig.pipeline` the session ran with (its pinned version).
        usage: `sessions.usage` — `dataclasses.asdict(AgentSessionUsage)`, or `None`/malformed
            (an empty list, never an exception).
        quote: The price source (default: the table only).

    Returns:
        One line per non-zero billable field of `usage["model_usage"]` (D-V4-44).
        Entries whose kind has no bound pipeline slot are skipped.
    """
    entries = usage.get("model_usage") if isinstance(usage, dict) else None
    if not isinstance(entries, list):
        return []
    lines: list[ComputedLine] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        usage_type = entry.get("type")
        if not isinstance(usage_type, str):
            continue
        found = slot_for_usage(usage_type, entry, pipeline)
        if found is None:
            continue
        slot, ref = found
        model = resolve_model(entry, ref)
        if usage_type == "llm_usage":
            lines.extend(_llm_lines(ref, model, entry, quote, cast(EstimateSlot, slot)))
        elif usage_type == "tts_usage":
            lines.extend(_tts_lines(ref, model, entry, quote))
        elif usage_type == "stt_usage":
            lines.extend(_stt_lines(ref, model, entry, quote))
        elif usage_type in ("eot_usage", "interruption_usage"):
            requests = _decimal(entry.get("total_requests"))
            if requests is not None:
                lines.append(
                    price_line(
                        ref.provider_id,
                        model,
                        "requests",
                        requests,
                        quote=quote,
                        slot=cast(EstimateSlot, slot),
                    )
                )
    return lines


# ------------------------------------------------------------------ LiveKit Cloud minutes
def estimate_channel(channel: str | None) -> EstimateChannel:
    """Map `sessions.channel` onto the estimate's channel (`sip_*` → phone, `text` → text, else web)."""
    if channel in ("sip_in", "sip_out"):
        return "phone"
    if channel == "text":
        return "text"
    return "web"


def session_seconds(session: SessionRow) -> Decimal | None:
    """Wall-clock seconds between `started_at` and `ended_at`, or `None` if either is unset."""
    if session.started_at is None or session.ended_at is None:
        return None
    seconds = Decimal(str((session.ended_at - session.started_at).total_seconds()))
    return seconds if seconds > 0 else None


def sip_model_for_number(e164: str | None, source: str | None) -> str:
    """The `livekit-sip` model: a LiveKit-hosted number is local or toll-free inbound, else a trunk."""
    if source == "livekit":
        return "toll-free-inbound" if e164 and e164.startswith(_TOLL_FREE) else "local-inbound"
    return "trunk"


async def session_sip_model(db: AsyncSession, session: SessionRow) -> str:
    """Resolve the SIP model of a phone session from its called number's `phone_numbers` row."""
    caller = session.caller if isinstance(session.caller, dict) else {}
    to = caller.get("to") if session.channel == "sip_in" else None
    if not isinstance(to, str) or not to:
        return "trunk"
    row = (
        await db.execute(
            select(PhoneNumber.e164, PhoneNumber.source).where(
                PhoneNumber.workspace_id == session.workspace_id, PhoneNumber.e164 == to
            )
        )
    ).first()
    return sip_model_for_number(row[0], row[1]) if row is not None else "trunk"


def infra_lines(
    session: SessionRow, config: AgentConfig, *, quote: QuoteFn = table_quote, sip_model: str = "trunk"
) -> list[ComputedLine]:
    """Call, participant and phone minutes from LKAP's own clock, plus avatar minutes (D-V4-44)."""
    seconds = session_seconds(session)
    if seconds is None:
        return []
    billed = max(seconds, AGENT_MINIMUM_S) / Decimal(60)
    minutes = seconds / Decimal(60)
    lines = [
        price_line("livekit-agent", None, "minutes", billed, quote=quote, slot="livekit_agent"),
        price_line(
            "livekit-participant", None, "minutes", minutes * 2, quote=quote, slot="livekit_participant"
        ),
    ]
    channel = estimate_channel(session.channel)
    if channel == "phone":
        lines.append(
            price_line("livekit-sip", sip_model, "minutes", minutes, quote=quote, slot="livekit_sip")
        )
    avatar = config.pipeline.avatar
    if avatar is not None and channel != "text":
        lines.append(
            price_line(avatar.provider_id, avatar.model, "minutes", minutes, quote=quote, slot="avatar")
        )
    return lines


def egress_line(
    session: SessionRow, config: AgentConfig | None, *, quote: QuoteFn = table_quote
) -> ComputedLine | None:
    """The recording line (`livekit-egress`, model `audio`/`video`) once its duration is known."""
    if not session.recording_duration_s:
        return None
    minutes = Decimal(str(session.recording_duration_s)) / Decimal(60)
    if minutes <= 0:
        return None
    model = "audio" if config is None or config.recording.audio_only else "video"
    return price_line("livekit-egress", model, "minutes", minutes, quote=quote, slot="livekit_egress")


async def image_count(db: AsyncSession, session_id: str) -> int:
    """How many `asset {kind: "image"}` events the session recorded (the image-generation proxy)."""
    rows = (
        await db.execute(
            select(SessionEvent.payload).where(
                SessionEvent.session_id == session_id, SessionEvent.type == "asset"
            )
        )
    ).scalars()
    return sum(1 for payload in rows if isinstance(payload, dict) and payload.get("kind") == "image")


def image_lines(config: AgentConfig, count: int, *, quote: QuoteFn = table_quote) -> list[ComputedLine]:
    """Price `count` generated images on `pipeline.image_gen` (a stated approximation)."""
    ref = config.pipeline.image_gen
    if ref is None or count <= 0:
        return []
    return [price_line(ref.provider_id, ref.model, "images", Decimal(count), quote=quote, slot="image_gen")]


# ------------------------------------------------------------------ persistence
async def config_for_session(db: AsyncSession, session: SessionRow) -> AgentConfig | None:
    """The `AgentConfig` the session actually ran (its pinned version, else the agent's current one)."""
    version_row = await db.scalar(
        select(AgentConfigVersion).where(
            AgentConfigVersion.agent_id == session.agent_id,
            AgentConfigVersion.config_version == session.config_version,
        )
    )
    if version_row is not None:
        return AgentConfig.model_validate(version_row.config)
    agent = (
        await db.execute(
            select(Agent).where(Agent.id == session.agent_id, Agent.workspace_id == session.workspace_id)
        )
    ).scalar_one_or_none()
    if agent is None:
        return None
    return AgentConfig.model_validate(agent.config)


async def session_price_book(db: AsyncSession, session: SessionRow, config: AgentConfig) -> PriceBook:
    """The workspace's prices plus the cached OpenRouter sheets for the session's slots."""
    return await load_price_book(db, session.workspace_id, pipeline_refs(config.pipeline, [config.qa.model]))


async def _persisted_lines(db: AsyncSession, session_id: str) -> list[SessionCostRow]:
    return list(
        (await db.execute(select(SessionCostRow).where(SessionCostRow.session_id == session_id)))
        .scalars()
        .all()
    )


def _persist(db: AsyncSession, session: SessionRow, line: ComputedLine) -> None:
    assert line.cost_usd is not None and line.unit_price_usd is not None  # noqa: S101 - internal invariant
    db.add(
        SessionCostRow(
            session_id=session.id,
            provider_id=line.provider_id,
            model=line.model or "",
            unit=line.unit,
            quantity=float(line.quantity),
            unit_price_usd=float(line.unit_price_usd),
            cost_usd=float(line.cost_usd),
            price_version=pricing.PRICE_VERSION,
            price_source=line.price_source or "table",
        )
    )


async def _resync_total(db: AsyncSession, session: SessionRow) -> None:
    # `Database.sessionmaker` sets `autoflush=False` (see `db/session.py`), so
    # a just-`db.add()`-ed `SessionCostRow` is invisible to a `SELECT` on this
    # same session until explicitly flushed.
    await db.flush()
    rows = await _persisted_lines(db, session.id)
    session.cost_usd = float(sum((Decimal(str(r.cost_usd)) for r in rows), Decimal(0))) if rows else None


def snapshot_of(session: SessionRow) -> CostEstimate | None:
    """The creation-time estimate stored on the row, or `None` (none taken, or unreadable)."""
    if not isinstance(session.estimate, dict):
        return None
    try:
        return CostEstimate.model_validate(session.estimate)
    except ValueError:
        log.warning("session_estimate_unreadable", session_id=session.id)
        return None


def _set_estimated_usd(session: SessionRow) -> None:
    snapshot = snapshot_of(session)
    seconds = session_seconds(session)
    if snapshot is None or seconds is None:
        return
    value = estimated_usd_for(snapshot, seconds / Decimal(60))
    session.estimated_usd = float(value) if value is not None else None


async def cost_session(db: AsyncSession, session: SessionRow, *, quote: QuoteFn | None = None) -> None:
    """Price a finished session into `session_costs`/`sessions.cost_usd`, and set `estimated_usd`.

    Idempotent: a session that already has `session_costs` rows keeps them (a
    retried summary never double-charges). No-ops when `session.usage` was
    never posted.
    """
    if not session.usage:
        return
    if await db.scalar(select(SessionCostRow.id).where(SessionCostRow.session_id == session.id).limit(1)):
        return
    config = await config_for_session(db, session)
    if config is None:
        log.warning("cost_session_no_config", session_id=session.id)
        return
    book = quote or await session_price_book(db, session, config)
    sip_model = await session_sip_model(db, session)
    lines = [
        *compute_usage_lines(config.pipeline, session.usage, quote=book),
        *infra_lines(session, config, quote=book, sip_model=sip_model),
        *image_lines(config, await image_count(db, session.id), quote=book),
    ]
    for line in lines:
        if line.cost_usd is not None:
            _persist(db, session, line)
    _set_estimated_usd(session)
    await _resync_total(db, session)
    await db.flush()
    log.info(
        "session_costed",
        session_id=session.id,
        cost_usd=session.cost_usd,
        estimated_usd=session.estimated_usd,
    )


async def add_egress_cost_line(db: AsyncSession, session: SessionRow) -> None:
    """Add the recording line once `recording_duration_s` is known (finalisation time).

    Idempotent per session (checks for an existing `livekit-egress` row first).
    The model (`audio`/`video`) follows the pinned config's `recording.audio_only`.
    """
    if not session.recording_duration_s:
        return
    exists = await db.scalar(
        select(SessionCostRow.id)
        .where(SessionCostRow.session_id == session.id, SessionCostRow.provider_id == "livekit-egress")
        .limit(1)
    )
    if exists:
        return
    config = await config_for_session(db, session)
    book = await load_price_book(db, session.workspace_id)
    line = egress_line(session, config, quote=book)
    if line is not None and line.cost_usd is not None:
        _persist(db, session, line)
        await _resync_total(db, session)
        await db.flush()


# ------------------------------------------------------------------ the read path
@dataclass(slots=True, frozen=True)
class CostContext:
    """What :func:`render_cost` needs beyond the row: prices, the SIP model, image and turn counts."""

    quote: QuoteFn = table_quote
    sip_model: str = "trunk"
    images: int = 0


async def cost_context(db: AsyncSession, session: SessionRow, config: AgentConfig | None) -> CostContext:
    """Load the read-path context for one session."""
    if config is None:
        return CostContext()
    return CostContext(
        quote=await session_price_book(db, session, config),
        sip_model=await session_sip_model(db, session),
        images=await image_count(db, session.id),
    )


def actual_lines(session: SessionRow, config: AgentConfig, ctx: CostContext) -> list[ComputedLine]:
    """Every actual line the session would be costed with now (usage, minutes, recording, images)."""
    lines = [
        *compute_usage_lines(config.pipeline, session.usage, quote=ctx.quote),
        *infra_lines(session, config, quote=ctx.quote, sip_model=ctx.sip_model),
        *image_lines(config, ctx.images, quote=ctx.quote),
    ]
    egress = egress_line(session, config, quote=ctx.quote)
    if egress is not None:
        lines.append(egress)
    return lines


def render_cost(
    session: SessionRow,
    persisted: Sequence[SessionCostRow],
    config: AgentConfig | None,
    ctx: CostContext | None = None,
) -> SessionCostOut:
    """Build the `SessionCost` block: persisted priced lines, live unpriced ones, and the comparison.

    `total_usd` is the sum of the persisted (priced) rows only, exactly what
    `sessions.cost_usd` holds — `None` when there are none, never `0`.
    """
    context = ctx or CostContext()
    lines = [
        CostLine(
            provider_id=row.provider_id,
            model=row.model or None,
            unit=cast(Unit, row.unit),
            quantity=Decimal(str(row.quantity)),
            unit_price_usd=Decimal(str(row.unit_price_usd)),
            cost_usd=Decimal(str(row.cost_usd)),
            price_source=cast(PriceSource, row.price_source or "table"),
            vendor_usd=Decimal(str(row.vendor_usd)) if row.vendor_usd is not None else None,
            vendor_ref=row.vendor_ref,
        )
        for row in persisted
    ]
    seen = {(row.provider_id, row.model or None, row.unit) for row in persisted}
    computed: list[ComputedLine] = actual_lines(session, config, context) if config is not None else []
    for line in computed:
        if line.cost_usd is not None or line.key in seen:
            continue
        seen.add(line.key)
        lines.append(
            CostLine(
                provider_id=line.provider_id,
                model=line.model,
                unit=line.unit,
                quantity=line.quantity,
                note=line.note,
            )
        )
    total = sum((Decimal(str(row.cost_usd)) for row in persisted), Decimal(0)) if persisted else None
    out = SessionCostOut(total_usd=total, lines=lines)
    snapshot = snapshot_of(session)
    if snapshot is None:
        return out
    seconds = session_seconds(session)
    minutes = seconds / Decimal(60) if seconds is not None else None
    estimated = (
        Decimal(str(session.estimated_usd))
        if session.estimated_usd is not None
        else (estimated_usd_for(snapshot, minutes) if minutes is not None else None)
    )
    out.estimated_usd = estimated
    out.estimate_per_minute_usd = snapshot.per_minute_usd.mid if snapshot.per_minute_usd else None
    out.estimate_as_of = snapshot.as_of
    out.price_version = snapshot.price_version
    out.reconciled_usd = Decimal(str(session.reconciled_usd)) if session.reconciled_usd is not None else None
    if total is not None and estimated is not None:
        out.variance_usd = (total - estimated).quantize(_QUANT, rounding=ROUND_HALF_UP)
        out.variance_pct = round(float((total - estimated) / estimated * 100), 2) if estimated else None
    if minutes is not None:
        persisted_by_key = {(row.provider_id, row.model or None, row.unit): row for row in persisted}
        out.drivers = drivers(snapshot, computed, persisted_by_key, minutes=minutes, turns=_turns(session))
    return out


def _turns(session: SessionRow) -> int | None:
    for source in (session.latency, session.usage):
        value = source.get("turns") if isinstance(source, dict) else None
        if isinstance(value, int) and value > 0:
            return value
    return None


# ------------------------------------------------------------------ drivers (COSTS.md §4.3)
_TALK_UNITS = {"chars", "audio_s_in", "audio_s_out", "audio_tokens_in", "audio_tokens_out"}
_PROMPT_UNITS = {"tokens_in", "text_tokens_in"}
_TOKEN_UNITS = {"tokens_in", "tokens_out", "text_tokens_in", "text_tokens_out", "cached_tokens_in"}


def _band(assumptions: Mapping[str, Assumption], key: str) -> tuple[Decimal, Decimal, Decimal] | None:
    a = assumptions.get(key)
    if a is None or isinstance(a.value, str):
        return None
    mid = Decimal(str(a.value))
    low = Decimal(str(a.low)) if a.low is not None else mid
    high = Decimal(str(a.high)) if a.high is not None else mid
    return low, mid, high


def _talk_key(line: EstimateLine) -> str:
    if line.slot == "stt" or line.unit in ("audio_s_in", "audio_tokens_in"):
        return "caller_talk_ratio"
    return "agent_talk_ratio"


def _reason(
    est: EstimateLine | None,
    actual: ComputedLine | None,
    persisted: SessionCostRow | None,
    snapshot: CostEstimate,
    assumptions: Mapping[str, Assumption],
    *,
    minutes: Decimal,
    turns: int | None,
) -> DriverReason:
    if est is None:
        return "not estimated"
    actual_priced = actual is not None and actual.cost_usd is not None
    if est.quote is None or (actual is not None and not actual_priced):
        return "unpriced line"
    if persisted is not None and persisted.price_version != snapshot.price_version:
        if Decimal(str(persisted.unit_price_usd)) != est.quote.usd_per_unit:
            return "price changed"
    session_band = _band(assumptions, "session_minutes")
    if session_band is not None:
        if minutes > session_band[2]:
            return "more minutes"
        if minutes < session_band[0]:
            return "fewer minutes"
    actual_qty = actual.quantity if actual is not None else Decimal(0)
    per_min = actual_qty / minutes if minutes > 0 else Decimal(0)
    est_per_min = est.quantity_per_min
    if est_per_min is not None and est.unit in _TALK_UNITS:
        talk = _band(assumptions, _talk_key(est))
        if talk is not None and talk[1] > 0:
            high = est_per_min * talk[2] / talk[1]
            low = est_per_min * talk[0] / talk[1]
            if per_min > high:
                return "more talk"
            if per_min < low:
                return "less talk"
    turn_band = _band(assumptions, "agent_turns_per_min")
    if (
        est_per_min is not None
        and est.unit in _PROMPT_UNITS
        and turns
        and turn_band is not None
        and turn_band[1] > 0
    ):
        prompt = _band(assumptions, "prompt_tokens")
        est_per_turn = est_per_min / turn_band[1]
        factor = prompt[2] / prompt[1] if prompt is not None and prompt[1] > 0 else Decimal("1.3")
        if actual_qty / turns > est_per_turn * factor:
            return "longer prompts"
    if est.unit in _TOKEN_UNITS and turns and turn_band is not None and turns / minutes > turn_band[2]:
        return "more turns"
    return "as estimated"


def drivers(
    snapshot: CostEstimate,
    actual: Sequence[ComputedLine],
    persisted: Mapping[tuple[str, str | None, str], SessionCostRow],
    *,
    minutes: Decimal,
    turns: int | None,
) -> list[CostDriver]:
    """Join the snapshot's lines to the actual lines on `(slot, unit)` and explain each difference.

    Args:
        snapshot: The creation-time estimate.
        actual: The actual lines (priced or not), each with its slot.
        persisted: The persisted rows by `(provider_id, model, unit)` (their `price_version`).
        minutes: The session's actual minutes.
        turns: The session's agent turns, when known.

    Returns:
        One driver per `(slot, unit)`, sorted by `|delta_usd|` (unpriced last).
    """
    assumptions = {a.key: a for a in snapshot.assumptions}
    est_by_key: dict[tuple[str, str], EstimateLine] = {}
    for est_line in snapshot.lines:
        est_by_key.setdefault((est_line.slot, est_line.unit), est_line)
    act_by_key: dict[tuple[str, str], ComputedLine] = {}
    for act_line in actual:
        act_key = (act_line.slot or "", act_line.unit)
        prior = act_by_key.get(act_key)
        if prior is None:
            act_by_key[act_key] = act_line
        else:  # two usage entries on one slot/unit: sum them
            cost = (
                None
                if prior.cost_usd is None or act_line.cost_usd is None
                else prior.cost_usd + act_line.cost_usd
            )
            act_by_key[act_key] = replace(prior, quantity=prior.quantity + act_line.quantity, cost_usd=cost)
    out: list[CostDriver] = []
    for key in list(dict.fromkeys([*est_by_key, *act_by_key])):
        est = est_by_key.get(key)
        act = act_by_key.get(key)
        ref = est or act
        assert ref is not None  # noqa: S101 - one of the two exists by construction
        row = persisted.get((act.provider_id, act.model, act.unit)) if act is not None else None
        reason = _reason(est, act, row, snapshot, assumptions, minutes=minutes, turns=turns)
        est_qty: Decimal | None = None
        est_usd: Decimal | None = None
        if est is not None:
            if est.quantity_per_min is not None:
                est_qty = (est.quantity_per_min * minutes).quantize(Decimal("0.001"))
                est_usd = (
                    (est.usd_per_min * minutes).quantize(_QUANT) if est.usd_per_min is not None else None
                )
            else:
                est_qty = est.quantity_per_session
                est_usd = est.usd_per_session
        act_qty = act.quantity if act is not None else (Decimal(0) if est is not None else None)
        act_usd = act.cost_usd if act is not None else (Decimal(0) if est is not None and est.quote else None)
        delta = None
        if reason != "unpriced line" and est_usd is not None and act_usd is not None:
            delta = (act_usd - est_usd).quantize(_QUANT, rounding=ROUND_HALF_UP)
        out.append(
            CostDriver(
                slot=cast(EstimateSlot, ref.slot),
                provider_id=ref.provider_id,
                model=ref.model,
                unit=ref.unit,
                estimated_quantity=est_qty,
                actual_quantity=act_qty,
                estimated_usd=est_usd,
                actual_usd=act_usd,
                delta_usd=delta,
                reason=reason,
            )
        )
    out.sort(key=lambda d: (d.delta_usd is None, -abs(d.delta_usd or Decimal(0))))
    return out
