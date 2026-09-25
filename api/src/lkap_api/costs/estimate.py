"""The per-minute estimator: config × assumptions × quotes → ``CostEstimate`` (COSTS.md §3).

Pure functions, no I/O: the caller hands in the ``AgentConfig``, the effective
assumptions and a ``quote`` callable (bound to the workspace's prices and any
cached OpenRouter sheet). The same code serves the agent route, the template
gallery, the pickers and the session snapshot.

An estimate is ``Σ quantity_per_minute × usd_per_unit`` over its lines. The
band (``low``/``high``) is the same sum with every assumption at the bound that
lowers/raises cost — every quantity grows with each assumption, so that is
"all lows" and "all highs". Unknown prices stay unpriced lines, never zeros;
a realtime model's audio is never priced at a text rate.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from lkap_contracts import pricing, providers
from lkap_contracts.agent_config import AgentConfig, effective_qa
from lkap_contracts.api_models import (
    Assumption,
    CostEstimate,
    EstimateChannel,
    EstimateLine,
    EstimateSlot,
    MoneyRange,
    PriceSource,
    TemplateEstimate,
)
from lkap_contracts.common import ProviderRef
from lkap_contracts.pricing import PriceQuote, Unit

#: ``quote(provider_id, model, unit)``: a price, or ``None`` when no source knows it.
QuoteFn = Callable[[str, str | None, Unit], PriceQuote | None]

Bound = Literal["low", "mid", "high"]
BOUNDS: tuple[Bound, ...] = ("low", "mid", "high")

_USD = Decimal("0.000001")
_QTY = Decimal("0.001")

#: Plain labels (D-V4-47): no "tokens", "STT/TTS/LLM", "egress", "SIP" in a label.
LABELS: dict[str, str] = {
    "stt": "Caller's speech → text",
    "llm": "Agent's thinking",
    "tts": "Agent's voice",
    "realtime": "Agent's voice and thinking",
    "avatar": "Video avatar",
    "workflow_llm": "Flow routing",
    "image_gen": "Images",
    "embedding": "Knowledge lookups",
    "turn_detection": "Turn detection",
    "vad": "Voice detection",
    "livekit_agent": "Call minutes",
    "livekit_participant": "Participant minutes",
    "livekit_sip": "Phone minutes",
    "livekit_egress": "Recording",
    "qa_judge": "Quality review",
}

#: What each unit counts, in plain words (the unpriced list names it).
UNIT_WORDS: dict[str, str] = {
    "tokens_in": "input",
    "tokens_out": "output",
    "text_tokens_in": "text input",
    "text_tokens_out": "text output",
    "audio_tokens_in": "audio heard",
    "audio_tokens_out": "audio spoken",
    "cached_tokens_in": "cached input",
    "audio_s_in": "seconds heard",
    "audio_s_out": "seconds spoken",
    "chars": "characters",
    "minutes": "minutes",
    "images": "images",
    "requests": "requests",
}

FOOTER_CAVEAT = (
    "List prices at the entry tier; included minutes, volume discounts and enterprise contracts "
    "are not modelled. Estimates are not bills."
)
STREAM_CAVEAT = "Streaming speech-to-text is billed for every second the call is open, silence included."

#: Output tokens of one tool round-trip, and of the quality-review verdict.
TOOL_CALL_OUTPUT_TOKENS = 40
QA_OUTPUT_TOKENS = 200


@dataclass(slots=True)
class _Line:
    slot: EstimateSlot
    provider_id: str
    model: str | None
    unit: Unit
    per_minute: bool
    qty: dict[Bound, Decimal] = field(default_factory=dict)
    quote: PriceQuote | None = None
    note: str | None = None
    label_suffix: str = ""


def _q(value: Decimal, step: Decimal) -> Decimal:
    return value.quantize(step, rounding=ROUND_HALF_UP)


def _num(assumptions: Mapping[str, Assumption], key: str, bound: Bound) -> Decimal:
    a = assumptions[key]
    value: float | str | None = a.value
    if bound == "low" and a.low is not None:
        value = a.low
    elif bound == "high" and a.high is not None:
        value = a.high
    if isinstance(value, str):  # pragma: no cover - numeric keys only
        raise ValueError(f"assumption {key!r} is not numeric")
    return Decimal(str(value))


def _model(ref: ProviderRef) -> str | None:
    if ref.model:
        return ref.model
    try:
        return providers.get(ref.provider_id).default_model
    except KeyError:
        return None


def _vendor_label(provider_id: str) -> str:
    try:
        return providers.get(provider_id).label
    except KeyError:
        return provider_id


class _Builder:
    """Collects lines for one estimate; quantities are computed at all three bounds."""

    def __init__(self, assumptions: Mapping[str, Assumption], quote: QuoteFn) -> None:
        self.a = assumptions
        self.quote = quote
        self.lines: list[_Line] = []

    def n(self, key: str, bound: Bound) -> Decimal:
        return _num(self.a, key, bound)

    def has(self, key: str) -> bool:
        return key in self.a

    def add(
        self,
        slot: EstimateSlot,
        ref_id: str,
        model: str | None,
        unit: Unit,
        qty: Callable[[Bound], Decimal],
        *,
        per_minute: bool = True,
        note: str | None = None,
        priced: bool = True,
        label_suffix: str = "",
    ) -> None:
        found = self.quote(ref_id, model, unit) if priced else None
        line = _Line(slot, ref_id, model, unit, per_minute, quote=found, label_suffix=label_suffix)
        line.qty = {bound: qty(bound) for bound in BOUNDS}
        if found is None:
            line.note = note or "no price"
        elif note:
            line.note = note
        self.lines.append(line)

    # ------------------------------------------------------------ shared quantities
    def turns_per_session(self, b: Bound) -> Decimal:
        return self.n("agent_turns_per_min", b) * self.n("session_minutes", b)

    def llm_input_per_min(self, b: Bound) -> Decimal:
        """Σ input over a session ÷ minutes: fixed prompt per turn + linear history growth + tool calls."""
        minutes = self.n("session_minutes", b)
        turns = self.turns_per_session(b)
        prompt = self.n("prompt_tokens", b)
        history = self.n("history_tokens_per_turn", b)
        total = turns * prompt + history * turns * (turns + 1) / 2
        tools = self.n("tool_calls_per_session", b)
        total += tools * (prompt + history * turns / 2)
        return total / minutes if minutes > 0 else Decimal(0)

    def llm_output_per_min(self, b: Bound) -> Decimal:
        minutes = self.n("session_minutes", b)
        out = self.n("agent_turns_per_min", b) * self.n("output_tokens_per_turn", b)
        if minutes > 0:
            out += self.n("tool_calls_per_session", b) * TOOL_CALL_OUTPUT_TOKENS / minutes
        return out

    def agent_chars_per_min(self, b: Bound) -> Decimal:
        return self.n("speech_wpm", b) * self.n("chars_per_word", b) * self.n("agent_talk_ratio", b)

    def stt_seconds_per_min(self, b: Bound) -> Decimal:
        if self.a["stt_billing"].value == "segments":
            return Decimal(60) * self.n("caller_talk_ratio", b)
        return Decimal(60)


def _stt(b: _Builder, ref: ProviderRef) -> None:
    model = _model(ref)
    b.add("stt", ref.provider_id, model, "audio_s_in", b.stt_seconds_per_min)


def _llm(b: _Builder, ref: ProviderRef, slot: EstimateSlot = "llm") -> None:
    model = _model(ref)
    b.add(slot, ref.provider_id, model, "tokens_in", b.llm_input_per_min, label_suffix=" (reading)")
    b.add(slot, ref.provider_id, model, "tokens_out", b.llm_output_per_min, label_suffix=" (writing)")


def _tts(b: _Builder, ref: ProviderRef) -> None:
    model = _model(ref)
    pid = ref.provider_id
    if b.quote(pid, model, "chars") is not None:
        b.add("tts", pid, model, "chars", b.agent_chars_per_min)
        return
    if b.quote(pid, model, "audio_s_out") is not None:
        b.add("tts", pid, model, "audio_s_out", lambda bd: Decimal(60) * b.n("agent_talk_ratio", bd))
        return
    if b.quote(pid, model, "tokens_out") is not None or b.quote(pid, model, "tokens_in") is not None:
        # Token-billed speech: the text read in, and the audio spoken out.
        b.add(
            "tts",
            pid,
            model,
            "tokens_in",
            lambda bd: b.agent_chars_per_min(bd) / pricing_chars_per_token(),
            label_suffix=" (text read)",
        )
        if b.has("audio_tokens_out_per_s"):
            b.add(
                "tts",
                pid,
                model,
                "tokens_out",
                lambda bd: Decimal(60) * b.n("agent_talk_ratio", bd) * b.n("audio_tokens_out_per_s", bd),
                label_suffix=" (audio spoken)",
            )
        else:
            b.add(
                "tts",
                pid,
                model,
                "tokens_out",
                lambda bd: Decimal(0),
                priced=False,
                note="audio-token rate unknown",
                label_suffix=" (audio spoken)",
            )
        return
    b.add("tts", pid, model, "chars", b.agent_chars_per_min)


def pricing_chars_per_token() -> Decimal:
    """Characters per text token (the usual ≈ 4)."""
    return Decimal(4)


def _realtime(b: _Builder, ref: ProviderRef, *, channel: EstimateChannel, half_cascade: bool) -> None:
    model = _model(ref)
    pid = ref.provider_id
    split = (
        b.quote(pid, model, "text_tokens_in") is not None
        or b.quote(pid, model, "audio_tokens_in") is not None
    )
    text_in: Unit = "text_tokens_in" if split else "tokens_in"
    text_out: Unit = "text_tokens_out" if split else "tokens_out"
    if channel != "text":
        audio_known = b.has("audio_tokens_in_per_s") and b.has("audio_tokens_out_per_s")
        audio_priced = split and audio_known
        note: str | None = None
        if not split and b.quote(pid, model, "tokens_in") is not None:
            note = "audio-token price unknown"  # a text rate exists; audio is never priced at it
        elif split and not audio_known:
            note = "audio-token rate unknown"
        b.add(
            "realtime",
            pid,
            model,
            "audio_tokens_in",
            (lambda bd: Decimal(60) * b.n("caller_talk_ratio", bd) * b.n("audio_tokens_in_per_s", bd))
            if audio_known
            else (lambda bd: Decimal(0)),
            priced=audio_priced,
            note=note,
            label_suffix=" (listening)",
        )
        if not half_cascade:
            b.add(
                "realtime",
                pid,
                model,
                "audio_tokens_out",
                (lambda bd: Decimal(60) * b.n("agent_talk_ratio", bd) * b.n("audio_tokens_out_per_s", bd))
                if audio_known
                else (lambda bd: Decimal(0)),
                priced=audio_priced,
                note=note,
                label_suffix=" (speaking)",
            )
    b.add("realtime", pid, model, text_in, b.llm_input_per_min, label_suffix=" (reading)")
    b.add(
        "realtime",
        pid,
        model,
        text_out,
        lambda bd: b.agent_chars_per_min(bd) / pricing_chars_per_token(),
        label_suffix=" (writing)",
    )


def _per_session_llm_call(
    b: _Builder,
    slot: EstimateSlot,
    ref: ProviderRef,
    calls: Callable[[Bound], Decimal],
    output: int,
    *,
    grow: Decimal,
) -> None:
    model = _model(ref)

    def inputs(bd: Bound) -> Decimal:
        turns = b.turns_per_session(bd)
        return calls(bd) * (b.n("prompt_tokens", bd) + b.n("history_tokens_per_turn", bd) * turns * grow)

    b.add(slot, ref.provider_id, model, "tokens_in", inputs, per_minute=False, label_suffix=" (reading)")
    b.add(
        slot,
        ref.provider_id,
        model,
        "tokens_out",
        lambda bd: calls(bd) * output,
        per_minute=False,
        label_suffix=" (writing)",
    )


def build_estimate(
    config: AgentConfig,
    assumptions: Mapping[str, Assumption],
    quote: QuoteFn,
    *,
    channel: EstimateChannel = "web",
    sip_model: str = "trunk",
    embedding_provider: str | None = "fastembed-embedding",
    price_version: str = pricing.PRICE_VERSION,
) -> CostEstimate:
    """Estimate what ``config`` costs per minute and per session at the given assumptions.

    Args:
        config: The agent configuration (saved, pinned, a template's, or an unsaved draft).
        assumptions: The effective assumptions (defaults, workspace, request), incl. ``prompt_tokens``.
        quote: Resolves a price; ``None`` means unpriced.
        channel: ``web``, ``phone`` (adds phone minutes) or ``text`` (drops every audio line).
        sip_model: ``local-inbound`` for a LiveKit-hosted number, ``trunk`` for a third-party trunk.
        embedding_provider: The knowledge embedder's registry id (``None`` skips the line).
        price_version: Stamped on the estimate.

    Returns:
        The estimate; ``per_minute_usd`` is ``None`` when every line is unpriced.
    """
    b = _Builder(assumptions, quote)
    p = config.pipeline
    audio = channel != "text"

    if p.mode == "cascaded":
        if audio and p.stt is not None:
            _stt(b, p.stt)
        if p.llm is not None:
            _llm(b, p.llm)
        if audio and p.tts is not None:
            _tts(b, p.tts)
    else:
        if p.realtime is not None:
            _realtime(b, p.realtime, channel=channel, half_cascade=p.mode == "half_cascade")
        if p.mode == "half_cascade" and audio and p.tts is not None:
            _tts(b, p.tts)

    if audio:
        detectors: tuple[tuple[EstimateSlot, ProviderRef | None], ...] = (
            ("turn_detection", p.turn_detection),
            ("vad", p.vad),
        )
        for slot, ref in detectors:
            if ref is None:
                continue
            model = _model(ref)
            if quote(ref.provider_id, model, "requests") is not None:
                b.add(slot, ref.provider_id, model, "requests", lambda bd: b.n("agent_turns_per_min", bd))
        if p.avatar is not None:
            b.add("avatar", p.avatar.provider_id, _model(p.avatar), "minutes", lambda bd: Decimal(1))

    b.add("livekit_agent", "livekit-agent", None, "minutes", lambda bd: Decimal(1))
    b.add("livekit_participant", "livekit-participant", None, "minutes", lambda bd: b.n("participants", bd))
    if channel == "phone":
        b.add("livekit_sip", "livekit-sip", sip_model, "minutes", lambda bd: Decimal(1))
    if config.recording.enabled:
        egress = "audio" if config.recording.audio_only else "video"
        b.add("livekit_egress", "livekit-egress", egress, "minutes", lambda bd: Decimal(1))

    if config.knowledge.kb_ids and embedding_provider is not None:
        b.add(
            "embedding",
            embedding_provider,
            None,
            "tokens_in",
            lambda bd: b.n("kb_queries_per_turn", bd) * b.n("agent_turns_per_min", bd) * 30,
            note="not metered per session",
        )

    if config.flow is not None and p.workflow_llm is not None:
        transitions = Decimal(max(1, len(config.flow.nodes) - 1))
        _per_session_llm_call(
            b,
            "workflow_llm",
            p.workflow_llm,
            lambda bd: transitions,
            TOOL_CALL_OUTPUT_TOKENS,
            grow=Decimal("0.5"),
        )

    qa = effective_qa(config)
    judge = qa.model or p.llm or p.realtime
    if qa.enabled and judge is not None:
        _per_session_llm_call(
            b, "qa_judge", judge, lambda bd: Decimal(1), QA_OUTPUT_TOKENS, grow=Decimal("1.2")
        )

    if p.image_gen is not None and b.n("images_per_session", "high") > 0:
        b.add(
            "image_gen",
            p.image_gen.provider_id,
            _model(p.image_gen),
            "images",
            lambda bd: b.n("images_per_session", bd),
            per_minute=False,
        )

    return _finish(b, config, channel, price_version)


def _finish(b: _Builder, config: AgentConfig, channel: EstimateChannel, price_version: str) -> CostEstimate:
    minutes = {bd: b.n("session_minutes", bd) for bd in BOUNDS}
    per_min: dict[Bound, Decimal] = {"low": Decimal(0), "mid": Decimal(0), "high": Decimal(0)}
    per_session_only: dict[Bound, Decimal] = {"low": Decimal(0), "mid": Decimal(0), "high": Decimal(0)}
    out_lines: list[EstimateLine] = []
    unpriced: list[str] = []
    sources: list[PriceSource] = []
    caveats: list[str] = []
    as_of: list[str] = []
    priced_count = 0
    for line in b.lines:
        price = line.quote.usd_per_unit if line.quote is not None else None
        if line.quote is not None:
            priced_count += 1
            as_of.append(line.quote.as_of)
            if line.quote.source not in sources:
                sources.append(line.quote.source)
            for note in (line.quote.tier_note, line.quote.free_tier_note):
                if note and note not in caveats:
                    caveats.append(note)
            if line.quote.stale and "Some prices may be out of date." not in caveats:
                caveats.append("Some prices may be out of date.")
            for bd in BOUNDS:
                cost = line.qty[bd] * line.quote.usd_per_unit
                if line.per_minute:
                    per_min[bd] += cost
                else:
                    per_session_only[bd] += cost
        else:
            unpriced.append(
                f"{LABELS[line.slot]} — {_vendor_label(line.provider_id)}"
                f"{' ' + line.model if line.model else ''} ({UNIT_WORDS.get(line.unit, line.unit)})"
            )
        mid_qty = line.qty["mid"]
        if line.per_minute:
            qty_min: Decimal | None = _q(mid_qty, _QTY)
            qty_session = _q(mid_qty * minutes["mid"], _QTY)
            usd_min = _q(mid_qty * price, _USD) if price is not None else None
            usd_session = _q(mid_qty * minutes["mid"] * price, _USD) if price is not None else None
        else:
            qty_min = None
            qty_session = _q(mid_qty, _QTY)
            usd_min = None
            usd_session = _q(mid_qty * price, _USD) if price is not None else None
        out_lines.append(
            EstimateLine(
                slot=line.slot,
                label=LABELS[line.slot] + line.label_suffix,
                provider_id=line.provider_id,
                model=line.model,
                unit=line.unit,
                quantity_per_min=qty_min,
                quantity_per_session=qty_session,
                quote=line.quote,
                usd_per_min=usd_min,
                usd_per_session=usd_session,
                note=line.note,
            )
        )
    if any(line.slot == "stt" for line in b.lines) and b.a["stt_billing"].value == "stream":
        caveats.append(STREAM_CAVEAT)
    caveats.append(FOOTER_CAVEAT)
    any_priced = priced_count > 0
    per_minute_usd = (
        MoneyRange(low=_q(per_min["low"], _USD), mid=_q(per_min["mid"], _USD), high=_q(per_min["high"], _USD))
        if any_priced
        else None
    )
    per_session_usd = (
        MoneyRange(
            low=_q(per_min["low"] * minutes["low"] + per_session_only["low"], _USD),
            mid=_q(per_min["mid"] * minutes["mid"] + per_session_only["mid"], _USD),
            high=_q(per_min["high"] * minutes["high"] + per_session_only["high"], _USD),
        )
        if any_priced
        else None
    )
    return CostEstimate(
        per_minute_usd=per_minute_usd,
        per_session_usd=per_session_usd,
        session_minutes=float(minutes["mid"]),
        channel=channel,
        lines=out_lines,
        assumptions=list(b.a.values()),
        unpriced=unpriced,
        priced_share=round(priced_count / len(b.lines), 4) if b.lines else 0.0,
        price_version=price_version,
        as_of=min(as_of) if as_of else price_version,
        sources=sources,
        caveats=caveats,
    )


def per_session_lines_usd(estimate: CostEstimate) -> Decimal:
    """The sum of the per-session-only lines (quality review, flow routing, images) at mid."""
    return sum(
        (line.usd_per_session or Decimal(0) for line in estimate.lines if line.quantity_per_min is None),
        Decimal(0),
    )


def estimated_usd_for(estimate: CostEstimate, actual_minutes: Decimal) -> Decimal | None:
    """``per_minute_usd.mid × actual minutes + per-session lines`` (D-V4-43), or ``None`` without a figure."""
    if estimate.per_minute_usd is None:
        return None
    return _q(estimate.per_minute_usd.mid * actual_minutes + per_session_lines_usd(estimate), _USD)


def template_estimate(estimate: CostEstimate) -> TemplateEstimate | None:
    """The gallery pill's figures, or ``None`` when nothing is priced."""
    if estimate.per_minute_usd is None:
        return None
    return TemplateEstimate(
        per_minute_usd_mid=estimate.per_minute_usd.mid,
        per_minute_usd_low=estimate.per_minute_usd.low,
        per_minute_usd_high=estimate.per_minute_usd.high,
        as_of=estimate.as_of,
        unpriced=len(estimate.unpriced),
    )


def slot_per_minute(estimate: CostEstimate, slot: str) -> Decimal | None:
    """One slot's own share of a minute (the pickers' figure), ``None`` when any of its lines is unpriced."""
    lines = [line for line in estimate.lines if line.slot == slot and line.quantity_per_min is not None]
    if not lines or any(line.usd_per_min is None for line in lines):
        return None
    return sum((line.usd_per_min or Decimal(0) for line in lines), Decimal(0))


def table_quote(provider_id: str, model: str | None, unit: Unit) -> PriceQuote | None:
    """A :data:`QuoteFn` over the table only (no workspace prices, no live sheet)."""
    return pricing.quote(provider_id, model, unit)
