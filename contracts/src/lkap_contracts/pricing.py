"""Vendor prices and the one interface that resolves them (CONTRACTS-V2 §4.2, docs/v4/COSTS.md D-V4-39).

Three sources sit behind :func:`quote`, tried in this order:

1. **workspace** — a :class:`WorkspacePrice` an admin typed in (stored in
   ``workspaces.settings["cost"]["prices"]``). This is how plan-based vendors
   (ElevenLabs, Cartesia direct, Hume, every avatar vendor) get a real number.
2. **live** — a vendor's own machine-readable price sheet. Only OpenRouter
   publishes one (``GET https://openrouter.ai/api/v1/models``, USD per single
   unit); the caller passes the cached catalog item's ``meta`` mapping.
3. **table** — :data:`PRICES` (and :data:`INFRA_PRICES`), rows read off each
   vendor's own pricing page, every one with ``source_url`` and ``as_of``.

Nothing found means ``None``. **An unknown price is never zero**: the caller
records the line with ``cost_usd = None`` and ``note = "no price"``. The only
zero-priced rows are known zeros (a local model, an OpenRouter ``:free`` model).
Free tiers are a ``free_tier_note`` on the paid-rate row, never a zero line.

Rules for the table:

* A row is added only when its figure was read off the vendor's page that day;
  ``as_of`` is that day. **Any row edit bumps** :data:`PRICE_VERSION` **to that
  day** (``contracts/tests/test_pricing.py`` fails when a row's ``as_of`` is newer
  than ``PRICE_VERSION``). ``PRICE_VERSION`` is stored on every costed line so an
  old session keeps the price it was costed with.
* Prices are USD. Tiered and volume pricing is the **entry tier's list price,
  said out loud**: ``tier_note`` is mandatory on a row whose page shows more
  than one tier, a volume discount or an included quota. Plan-included minutes
  are never converted into a rate here; that is the admin's call, as a
  workspace price.
* A row older than :data:`PRICE_STALE_DAYS` is quoted with ``stale=True``.

Pseudo provider ids (not registry entries; priced from LKAP's own clocks, rows
in :data:`INFRA_PRICES`):

* ``livekit-agent`` — LiveKit Cloud agent session minutes (``minutes``).
* ``livekit-participant`` — WebRTC participant minutes (``minutes``; two per
  minute of a two-party call).
* ``livekit-sip`` — SIP minutes; model ``local-inbound``, ``toll-free-inbound``
  (a LiveKit-hosted number) or ``trunk`` (a third-party trunk).
* ``livekit-egress`` — recording (egress) minutes; model ``audio`` or ``video``.

``price_ref`` on a registry entry means "price this entry with that entry's
rows" (``openai-responses-llm`` → ``openai-llm``); it is never a self-reference.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, cast, get_args

from pydantic import BaseModel, Field

from lkap_contracts import providers

#: What a priced quantity is measured in (mirrors ``session_costs.unit``, ``String(16)``).
Unit = Literal[
    "tokens_in",
    "tokens_out",
    "audio_s_in",
    "audio_s_out",
    "chars",
    "minutes",
    "images",
    "text_tokens_in",
    "text_tokens_out",
    "audio_tokens_in",
    "audio_tokens_out",
    "cached_tokens_in",
    "requests",
]

#: Every :data:`Unit`, in declaration order.
UNITS: tuple[str, ...] = get_args(Unit)

#: Where a :class:`PriceQuote` came from (D-V4-39).
PriceSource = Literal["workspace", "live", "table"]

#: Bumped to the day of any :data:`PRICES`/:data:`INFRA_PRICES` edit.
PRICE_VERSION = "2026-09-25"

#: A table row older than this many days is quoted ``stale``.
PRICE_STALE_DAYS = 90

#: The endpoint the live source reads (keyless).
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"

#: LiveKit Cloud usage that has no registry entry (see the module docstring).
PSEUDO_PROVIDER_IDS: tuple[str, ...] = (
    "livekit-agent",
    "livekit-participant",
    "livekit-sip",
    "livekit-egress",
)


class Price(BaseModel):
    """One vendor price point.

    ``model`` is ``None`` for providers that price per unit regardless of model
    (most TTS and avatar vendors).
    """

    provider_id: str
    model: str | None = None
    unit: Unit
    usd_per_unit: Decimal
    source_url: str
    as_of: str
    tier_note: str | None = None
    free_tier_note: str | None = None


class PriceQuote(BaseModel):
    """A resolved price with its provenance (D-V4-39): what every estimate and cost line carries."""

    provider_id: str = Field(description="The registry entry priced (after `price_ref` aliasing).")
    model: str | None = None
    unit: Unit
    usd_per_unit: Decimal
    source: PriceSource
    source_url: str | None = Field(
        default=None,
        description="The vendor page (table), the API endpoint (live), or null (a workspace price).",
    )
    as_of: str = Field(
        description="ISO date: the row's `as_of` (table), the fetch date (live), the edit date (workspace)."
    )
    fetched_at: dt.datetime | None = Field(
        default=None, description="live: the catalog cache row's fetch time."
    )
    tier_note: str | None = None
    free_tier_note: str | None = None
    note: str | None = Field(default=None, description="A workspace price's own note.")
    stale: bool = Field(
        default=False,
        description="A table row older than PRICE_STALE_DAYS, or a live sheet past its cache TTL.",
    )
    currency: Literal["USD"] = "USD"


class WorkspacePrice(BaseModel):
    """A price an admin typed in for the workspace (the ``workspace`` source), in USD."""

    provider_id: str = Field(min_length=1, max_length=64)
    model: str | None = Field(default=None, max_length=128)
    unit: Unit
    usd_per_unit: Decimal = Field(ge=0, le=1000)
    note: str | None = Field(default=None, max_length=200)
    as_of: str = Field(description="ISO date the price was set.")


_M = Decimal(1_000_000)
_K = Decimal(1_000)
_HOUR = Decimal(3_600)
_MIN = Decimal(60)

_READ = "2026-09-25"
_OPENAI = "https://developers.openai.com/api/docs/pricing"
_ANTHROPIC = "https://platform.claude.com/docs/en/about-claude/pricing"
_GOOGLE = "https://ai.google.dev/gemini-api/docs/pricing"
_GROQ = "https://console.groq.com/docs/models"
_DEEPGRAM = "https://deepgram.com/pricing"
_ASSEMBLYAI = "https://www.assemblyai.com/pricing"
_RIME = "https://www.rime.ai/pricing"
_INWORLD = "https://inworld.ai/pricing"
_LK_INFERENCE = "https://www.livekit.com/pricing/inference"
_LK_CLOUD = "https://www.livekit.com/pricing"
_FASTEMBED = "https://github.com/qdrant/fastembed"

_OPENAI_TIER = "Standard tier (Batch, Flex and Priority are priced differently)"
_GOOGLE_TIER = "Standard tier, paid (Batch, Flex and Priority are priced differently)"
_GOOGLE_FREE = (
    "Google's free tier is free of charge within its limits (content may be used to improve "
    "Google's products); the paid rate is shown"
)
_ANTHROPIC_TIER = "standard rate (the Batch API is half price)"
_LK_TIER = "LiveKit Build tier list price (Ship/Scale can be cheaper for speech models)"
_LK_FREE = "each LiveKit plan includes monthly Inference credit ($2.50 on Build)"
_DEEPGRAM_TIER = "Pay As You Go rate (the Growth plan is cheaper)"


def _row(
    provider_id: str,
    model: str | None,
    unit: Unit,
    usd_per_unit: Decimal,
    source_url: str,
    *,
    as_of: str = _READ,
    tier_note: str | None = None,
    free_tier_note: str | None = None,
) -> Price:
    return Price(
        provider_id=provider_id,
        model=model,
        unit=unit,
        usd_per_unit=usd_per_unit,
        source_url=source_url,
        as_of=as_of,
        tier_note=tier_note,
        free_tier_note=free_tier_note,
    )


def _openai(model: str, unit: Unit, per_million: str, provider_id: str = "openai-llm") -> Price:
    return _row(provider_id, model, unit, Decimal(per_million) / _M, _OPENAI, tier_note=_OPENAI_TIER)


def _google(model: str, unit: Unit, per_million: str, provider_id: str = "google-llm", **kw: Any) -> Price:
    return _row(
        provider_id,
        model,
        unit,
        Decimal(per_million) / _M,
        _GOOGLE,
        tier_note=kw.get("tier_note", _GOOGLE_TIER),
        free_tier_note=_GOOGLE_FREE,
    )


def _lk(provider_id: str, model: str, unit: Unit, usd_per_unit: Decimal) -> Price:
    return _row(
        provider_id, model, unit, usd_per_unit, _LK_INFERENCE, tier_note=_LK_TIER, free_tier_note=_LK_FREE
    )


#: Every known vendor price, keyed by registry id. See the module docstring for the rules.
PRICES: list[Price] = [
    # ---------------------------------------------------------------- OpenAI LLM (per 1M tokens)
    _openai("gpt-4.1", "tokens_in", "2.00"),
    _openai("gpt-4.1", "cached_tokens_in", "0.50"),
    _openai("gpt-4.1", "tokens_out", "8.00"),
    _openai("gpt-4.1-mini", "tokens_in", "0.40"),
    _openai("gpt-4.1-mini", "cached_tokens_in", "0.10"),
    _openai("gpt-4.1-mini", "tokens_out", "1.60"),
    _openai("gpt-4o", "tokens_in", "2.50"),
    _openai("gpt-4o", "cached_tokens_in", "1.25"),
    _openai("gpt-4o", "tokens_out", "10.00"),
    _openai("gpt-4o-mini", "tokens_in", "0.15"),
    _openai("gpt-4o-mini", "cached_tokens_in", "0.075"),
    _openai("gpt-4o-mini", "tokens_out", "0.60"),
    # ---------------------------------------------------------------- OpenAI Realtime (text/audio splits)
    _openai("gpt-realtime", "text_tokens_in", "4.00", "openai-realtime"),
    _openai("gpt-realtime", "cached_tokens_in", "0.40", "openai-realtime"),
    _openai("gpt-realtime", "text_tokens_out", "16.00", "openai-realtime"),
    _openai("gpt-realtime", "audio_tokens_in", "32.00", "openai-realtime"),
    _openai("gpt-realtime", "audio_tokens_out", "64.00", "openai-realtime"),
    _openai("gpt-realtime-mini", "text_tokens_in", "0.60", "openai-realtime"),
    _openai("gpt-realtime-mini", "cached_tokens_in", "0.06", "openai-realtime"),
    _openai("gpt-realtime-mini", "text_tokens_out", "2.40", "openai-realtime"),
    _openai("gpt-realtime-mini", "audio_tokens_in", "10.00", "openai-realtime"),
    _openai("gpt-realtime-mini", "audio_tokens_out", "20.00", "openai-realtime"),
    # ---------------------------------------------------------------- OpenAI TTS
    # `tts-1` per character; `gpt-4o-mini-tts` per token (text in, audio out) — the
    # SDK reports `TTSModelUsage.input_tokens/output_tokens` for it.
    _row("openai-tts", "tts-1", "chars", Decimal("15.00") / _M, _OPENAI, tier_note=_OPENAI_TIER),
    _openai("gpt-4o-mini-tts", "tokens_in", "0.60", "openai-tts"),
    _openai("gpt-4o-mini-tts", "tokens_out", "12.00", "openai-tts"),
    # ---------------------------------------------------------------- OpenAI STT (token-billed)
    # The model page heads the input price "Audio tokens" (ask #92): audio in, text out.
    # The `audio_s_in` rows are OpenAI's own per-minute estimate from the same page; they
    # serve the estimator only — actual cost prices the reported tokens when there are any.
    _openai("gpt-4o-transcribe", "audio_tokens_in", "2.50", "openai-stt"),
    _openai("gpt-4o-transcribe", "tokens_out", "10.00", "openai-stt"),
    _row(
        "openai-stt",
        "gpt-4o-transcribe",
        "audio_s_in",
        Decimal("0.006") / _MIN,
        _OPENAI,
        tier_note="OpenAI's own per-minute estimate ($0.006/min); billed per token",
    ),
    _openai("gpt-4o-mini-transcribe", "audio_tokens_in", "1.25", "openai-stt"),
    _openai("gpt-4o-mini-transcribe", "tokens_out", "5.00", "openai-stt"),
    _row(
        "openai-stt",
        "gpt-4o-mini-transcribe",
        "audio_s_in",
        Decimal("0.003") / _MIN,
        _OPENAI,
        tier_note="OpenAI's own per-minute estimate ($0.003/min); billed per token",
    ),
    # -------------------------------------------- Anthropic (model ids as the registry lists them)
    _row(
        "anthropic-llm",
        "claude-sonnet-4-6",
        "tokens_in",
        Decimal("3") / _M,
        _ANTHROPIC,
        tier_note=_ANTHROPIC_TIER,
    ),
    _row(
        "anthropic-llm",
        "claude-sonnet-4-6",
        "cached_tokens_in",
        Decimal("0.30") / _M,
        _ANTHROPIC,
        tier_note=_ANTHROPIC_TIER,
    ),
    _row(
        "anthropic-llm",
        "claude-sonnet-4-6",
        "tokens_out",
        Decimal("15") / _M,
        _ANTHROPIC,
        tier_note=_ANTHROPIC_TIER,
    ),
    _row(
        "anthropic-llm",
        "claude-haiku-4-5",
        "tokens_in",
        Decimal("1") / _M,
        _ANTHROPIC,
        tier_note=_ANTHROPIC_TIER,
    ),
    _row(
        "anthropic-llm",
        "claude-haiku-4-5",
        "cached_tokens_in",
        Decimal("0.10") / _M,
        _ANTHROPIC,
        tier_note=_ANTHROPIC_TIER,
    ),
    _row(
        "anthropic-llm",
        "claude-haiku-4-5",
        "tokens_out",
        Decimal("5") / _M,
        _ANTHROPIC,
        tier_note=_ANTHROPIC_TIER,
    ),
    # ---------------------------------------------------------------- Google Gemini LLM
    _google("gemini-2.5-flash", "tokens_in", "0.30"),
    _google("gemini-2.5-flash", "cached_tokens_in", "0.03"),
    _google("gemini-2.5-flash", "tokens_out", "2.50"),
    _google("gemini-3.5-flash", "tokens_in", "1.50"),
    _google("gemini-3.5-flash", "cached_tokens_in", "0.15"),
    _google("gemini-3.5-flash", "tokens_out", "9.00"),
    # -------------------------------------------- Google Live (realtime); audio is 25 tokens/s
    _google("gemini-2.5-flash-native-audio-preview-12-2025", "text_tokens_in", "0.50", "google-realtime"),
    _google("gemini-2.5-flash-native-audio-preview-12-2025", "audio_tokens_in", "3.00", "google-realtime"),
    _google("gemini-2.5-flash-native-audio-preview-12-2025", "text_tokens_out", "2.00", "google-realtime"),
    _google("gemini-2.5-flash-native-audio-preview-12-2025", "audio_tokens_out", "12.00", "google-realtime"),
    *(
        _google(model, unit, figure, "google-realtime")
        for model in ("gemini-3.8-live", "gemini-3.8-live-extended-thinking", "gemini-3.1-flash-live-preview")
        for unit, figure in cast(
            "tuple[tuple[Unit, str], ...]",
            (
                ("text_tokens_in", "0.75"),
                ("audio_tokens_in", "3.00"),
                ("text_tokens_out", "4.50"),
                ("audio_tokens_out", "12.00"),
            ),
        )
    ),
    # ---------------------------------------------------------------- Google TTS (token-billed)
    _google(
        "gemini-3.8-flash-tts",
        "tokens_in",
        "0.50",
        "google-tts",
        tier_note="rate through 2026-12-31 ($1.00 from 2027-01-01); Standard tier",
    ),
    _google(
        "gemini-3.8-flash-tts",
        "tokens_out",
        "9.00",
        "google-tts",
        tier_note="rate through 2026-12-31 ($18.00 from 2027-01-01); Standard tier",
    ),
    # ---------------------------------------------------------------- Groq
    _row("groq-llm", "openai/gpt-oss-120b", "tokens_in", Decimal("0.15") / _M, _GROQ),
    _row("groq-llm", "openai/gpt-oss-120b", "tokens_out", Decimal("0.60") / _M, _GROQ),
    _row("groq-llm", "openai/gpt-oss-20b", "tokens_in", Decimal("0.075") / _M, _GROQ),
    _row("groq-llm", "openai/gpt-oss-20b", "tokens_out", Decimal("0.30") / _M, _GROQ),
    _row("groq-stt", "whisper-large-v3-turbo", "audio_s_in", Decimal("0.04") / _HOUR, _GROQ),
    _row("groq-stt", "whisper-large-v3", "audio_s_in", Decimal("0.111") / _HOUR, _GROQ),
    _row("groq-tts", "canopylabs/orpheus-v1-english", "chars", Decimal("22.00") / _M, _GROQ),
    # ---------------------------------------------------------------- Deepgram
    # Streaming STT is under a limited-time promotion on the page read; the regular rate is in the note.
    _row(
        "deepgram-stt",
        "nova-3",
        "audio_s_in",
        Decimal("0.0048") / _MIN,
        _DEEPGRAM,
        tier_note="limited-time promotional streaming rate (regular $0.0077/min); " + _DEEPGRAM_TIER,
    ),
    _row(
        "deepgram-stt",
        "flux-general-en",
        "audio_s_in",
        Decimal("0.0065") / _MIN,
        _DEEPGRAM,
        tier_note="limited-time promotional streaming rate (regular $0.0077/min); " + _DEEPGRAM_TIER,
    ),
    _row("deepgram-tts", None, "chars", Decimal("0.030") / _K, _DEEPGRAM, tier_note=_DEEPGRAM_TIER),
    _row("deepgram-tts", "aura-2", "chars", Decimal("0.030") / _K, _DEEPGRAM, tier_note=_DEEPGRAM_TIER),
    _row("deepgram-tts", "aura-1", "chars", Decimal("0.015") / _K, _DEEPGRAM, tier_note=_DEEPGRAM_TIER),
    # ---------------------------------------------------------------- AssemblyAI, Rime, Inworld
    _row(
        "assemblyai-stt",
        "universal-streaming",
        "audio_s_in",
        Decimal("0.15") / _HOUR,
        _ASSEMBLYAI,
        tier_note="billed on WebSocket session time, not audio sent",
    ),
    _row("rime-tts", "mistv3", "chars", Decimal("0.03") / _K, _RIME, tier_note="Starter plan rate"),
    _row(
        "inworld-tts",
        "inworld-tts-2",
        "chars",
        Decimal("25") / _M,
        _INWORLD,
        tier_note="on-demand rate (Growth plan is cheaper)",
    ),
    _row(
        "inworld-tts",
        "inworld-tts-2-flash",
        "chars",
        Decimal("15") / _M,
        _INWORLD,
        tier_note="on-demand rate (Growth plan is cheaper)",
    ),
    # ---------------------------------------------------------------- LiveKit Inference (Build tier)
    # STT: $/min ÷ 60 (LiveKit meters STT on seconds of connection time).
    _lk("livekit-inference-stt", "deepgram/nova-3", "audio_s_in", Decimal("0.0048") / _MIN),
    _lk("livekit-inference-stt", "deepgram/flux-general-en", "audio_s_in", Decimal("0.0065") / _MIN),
    _lk("livekit-inference-stt", "assemblyai/universal-streaming", "audio_s_in", Decimal("0.0025") / _MIN),
    _lk("livekit-inference-stt", "cartesia/ink-whisper", "audio_s_in", Decimal("0.0030") / _MIN),
    _lk("livekit-inference-stt", "google/gemini-3.5-transcribe-live", "audio_s_in", Decimal("0.0095") / _MIN),
    # LLM: $/1M tokens ÷ 1e6. `openai/gpt-oss-120b` stays unpriced: two routes, default unknown (ask #93).
    _lk("livekit-inference-llm", "google/gemma-4-31b-it", "tokens_in", Decimal("0.400") / _M),
    _lk("livekit-inference-llm", "google/gemma-4-31b-it", "cached_tokens_in", Decimal("0.200") / _M),
    _lk("livekit-inference-llm", "google/gemma-4-31b-it", "tokens_out", Decimal("1.200") / _M),
    _lk("livekit-inference-llm", "google/gemini-3.5-flash", "tokens_in", Decimal("1.500") / _M),
    _lk("livekit-inference-llm", "google/gemini-3.5-flash", "cached_tokens_in", Decimal("0.150") / _M),
    _lk("livekit-inference-llm", "google/gemini-3.5-flash", "tokens_out", Decimal("9.000") / _M),
    _lk("livekit-inference-llm", "openai/gpt-4.1", "tokens_in", Decimal("2.000") / _M),
    _lk("livekit-inference-llm", "openai/gpt-4.1", "cached_tokens_in", Decimal("0.500") / _M),
    _lk("livekit-inference-llm", "openai/gpt-4.1", "tokens_out", Decimal("8.000") / _M),
    _lk("livekit-inference-llm", "openai/gpt-4o-mini", "tokens_in", Decimal("0.150") / _M),
    _lk("livekit-inference-llm", "openai/gpt-4o-mini", "cached_tokens_in", Decimal("0.075") / _M),
    _lk("livekit-inference-llm", "openai/gpt-4o-mini", "tokens_out", Decimal("0.600") / _M),
    # TTS: $/1M characters ÷ 1e6.
    _lk("livekit-inference-tts", "cartesia/sonic-3", "chars", Decimal("50.00") / _M),
    _lk("livekit-inference-tts", "deepgram/aura-2", "chars", Decimal("30.00") / _M),
    _lk("livekit-inference-tts", "rime/mistv3", "chars", Decimal("30.00") / _M),
    _lk("livekit-inference-tts", "inworld/inworld-tts-2", "chars", Decimal("25.00") / _M),
    # ---------------------------------------------------------------- known zeros
    _row(
        "fastembed-embedding",
        None,
        "tokens_in",
        Decimal(0),
        _FASTEMBED,
        tier_note="runs on the worker; no vendor charge",
    ),
]

#: LiveKit Cloud usage under the pseudo ids of the module docstring (Ship/Scale overage rates).
INFRA_PRICES: list[Price] = [
    _row(
        "livekit-agent",
        None,
        "minutes",
        Decimal("0.01"),
        _LK_CLOUD,
        tier_note="Ship/Scale overage rate; Build includes 1,000 agent minutes/month",
    ),
    _row(
        "livekit-participant",
        None,
        "minutes",
        Decimal("0.0005"),
        _LK_CLOUD,
        tier_note="Ship overage rate; Build includes 5,000 and Ship 150,000 participant minutes/month",
    ),
    _row(
        "livekit-sip",
        "local-inbound",
        "minutes",
        Decimal("0.01"),
        _LK_CLOUD,
        tier_note="US local inbound, Ship/Scale overage rate; Build includes 50 minutes/month",
    ),
    _row(
        "livekit-sip",
        "toll-free-inbound",
        "minutes",
        Decimal("0.02"),
        _LK_CLOUD,
        tier_note="US toll-free inbound, Ship/Scale rate",
    ),
    _row(
        "livekit-sip",
        "trunk",
        "minutes",
        Decimal("0.004"),
        _LK_CLOUD,
        tier_note="third-party trunk, Ship overage rate; Build includes 1,000 minutes/month",
    ),
    _row(
        "livekit-egress",
        "audio",
        "minutes",
        Decimal("0.005"),
        _LK_CLOUD,
        tier_note="audio-only composite egress, Ship overage rate; Build includes 60 minutes/month",
    ),
    _row(
        "livekit-egress",
        "video",
        "minutes",
        Decimal("0.02"),
        _LK_CLOUD,
        tier_note="composite egress, Ship overage rate; Build includes 60 minutes/month",
    ),
]

#: Table rows with an OpenRouter twin (the drift job compares them, D-V4-40):
#: ``(provider_id, model) -> OpenRouter model id``.
OPENROUTER_TWINS: dict[tuple[str, str], str] = {
    ("openai-llm", "gpt-4.1"): "openai/gpt-4.1",
    ("openai-llm", "gpt-4.1-mini"): "openai/gpt-4.1-mini",
    ("openai-llm", "gpt-4o"): "openai/gpt-4o",
    ("openai-llm", "gpt-4o-mini"): "openai/gpt-4o-mini",
    ("anthropic-llm", "claude-sonnet-4-6"): "anthropic/claude-sonnet-4.6",
    ("anthropic-llm", "claude-haiku-4-5"): "anthropic/claude-haiku-4.5",
    ("google-llm", "gemini-2.5-flash"): "google/gemini-2.5-flash",
    ("google-llm", "gemini-3.5-flash"): "google/gemini-3.5-flash",
    ("deepgram-stt", "nova-3"): "deepgram/nova-3",
    ("deepgram-tts", "aura-2"): "deepgram/aura-2",
    ("livekit-inference-llm", "openai/gpt-4.1"): "openai/gpt-4.1",
    ("livekit-inference-llm", "openai/gpt-4o-mini"): "openai/gpt-4o-mini",
    ("livekit-inference-llm", "google/gemini-3.5-flash"): "google/gemini-3.5-flash",
}


def _all_rows() -> Iterable[Price]:
    # Read the module globals at call time (tests monkeypatch `PRICES`).
    yield from PRICES
    yield from INFRA_PRICES


def lookup(provider_id: str, model: str | None, unit: Unit) -> Price | None:
    """Return the table price for a provider/model/unit triple, or ``None`` if unknown.

    The table half of :func:`quote`. A model-specific entry wins over a
    model-agnostic one for the same provider and unit. No ``price_ref``
    aliasing happens here.

    Args:
        provider_id: A registry id such as ``"openai-llm"``, or a pseudo id.
        model: The model the session actually used, if the provider is priced per model.
        unit: The unit the quantity is measured in.

    Returns:
        The matching :class:`Price`, or ``None`` when the table has no entry —
        callers must record "no price", never a zero cost.
    """
    fallback: Price | None = None
    for price in _all_rows():
        if price.provider_id != provider_id or price.unit != unit:
            continue
        if price.model is not None and price.model == model:
            return price
        if price.model is None:
            fallback = fallback or price
    return fallback


def price_alias(provider_id: str) -> str:
    """The id whose table rows price ``provider_id`` (its ``price_ref``, else itself)."""
    try:
        spec = providers.get(provider_id)
    except KeyError:
        return provider_id
    return spec.price_ref or provider_id


def _kind(provider_id: str) -> str | None:
    try:
        return providers.get(provider_id).kind
    except KeyError:
        return None


#: OpenRouter ``pricing`` keys per unit, by entry kind (all USD per single unit).
#: The STT/TTS ``prompt`` unit is undocumented: per audio second (STT) and per
#: character (TTS) were derived against Deepgram's own page (COSTS.md §1.2).
_OPENROUTER_KEYS: dict[str, dict[str, str]] = {
    "llm": {
        "tokens_in": "prompt",
        "tokens_out": "completion",
        "cached_tokens_in": "input_cache_read",
        "audio_tokens_in": "audio",
        "audio_tokens_out": "audio_output",
        "images": "image",
        "requests": "request",
    },
    "stt": {"audio_s_in": "prompt", "tokens_out": "completion", "requests": "request"},
    "tts": {"chars": "prompt", "tokens_out": "completion", "requests": "request"},
    "embedding": {"tokens_in": "prompt", "requests": "request"},
    "image_gen": {
        "tokens_in": "prompt",
        "tokens_out": "completion",
        "images": "image_output",
        "requests": "request",
    },
}


def openrouter_key(provider_id: str, unit: Unit) -> str | None:
    """The OpenRouter ``pricing`` key that prices ``unit`` for this entry's kind, if any."""
    kind = _kind(provider_id) or "llm"
    return _OPENROUTER_KEYS.get(kind, _OPENROUTER_KEYS["llm"]).get(unit)


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite() or number < 0:  # OpenRouter uses "-1" for variable pricing
        return None
    return number


def _is_stale(as_of: str, now: dt.datetime) -> bool:
    try:
        day = dt.date.fromisoformat(as_of)
    except ValueError:
        return True
    return (now.date() - day).days > PRICE_STALE_DAYS


def _workspace_quote(
    provider_id: str, alias: str, model: str | None, unit: Unit, workspace_prices: Iterable[WorkspacePrice]
) -> PriceQuote | None:
    agnostic: WorkspacePrice | None = None
    for wp in workspace_prices:
        if wp.unit != unit or wp.provider_id not in (provider_id, alias):
            continue
        if wp.model is not None and wp.model == model:
            return _from_workspace(wp, model)
        if wp.model is None and agnostic is None:
            agnostic = wp
    return _from_workspace(agnostic, model) if agnostic is not None else None


def _from_workspace(wp: WorkspacePrice, model: str | None) -> PriceQuote:
    return PriceQuote(
        provider_id=wp.provider_id,
        model=model,
        unit=wp.unit,
        usd_per_unit=wp.usd_per_unit,
        source="workspace",
        source_url=None,
        as_of=wp.as_of,
        note=wp.note,
    )


def _live_quote(
    provider_id: str,
    model: str | None,
    unit: Unit,
    catalog_meta: Mapping[str, Any],
    fetched_at: dt.datetime | None,
    ttl_s: int,
    now: dt.datetime,
) -> PriceQuote | None:
    pricing = catalog_meta.get("pricing")
    key = openrouter_key(provider_id, unit)
    if not isinstance(pricing, Mapping) or key is None:
        return None
    value = _decimal(pricing.get(key))
    if value is None:
        return None
    free = value == 0 and bool(model) and str(model).endswith(":free")
    stale = False
    if fetched_at is not None:
        aware = fetched_at if fetched_at.tzinfo else fetched_at.replace(tzinfo=dt.UTC)
        stale = (now - aware).total_seconds() > ttl_s
    return PriceQuote(
        provider_id=provider_id,
        model=model,
        unit=unit,
        usd_per_unit=value,
        source="live",
        source_url=OPENROUTER_MODELS_URL,
        as_of=(fetched_at or now).date().isoformat(),
        fetched_at=fetched_at,
        free_tier_note="an OpenRouter free model: no charge within OpenRouter's free-model limits"
        if free
        else None,
        stale=stale,
    )


def _table_quote(alias: str, model: str | None, unit: Unit, now: dt.datetime) -> PriceQuote | None:
    row = lookup(alias, model, unit)
    if row is None:
        return None
    return PriceQuote(
        provider_id=alias,
        model=model,
        unit=unit,
        usd_per_unit=row.usd_per_unit,
        source="table",
        source_url=row.source_url,
        as_of=row.as_of,
        tier_note=row.tier_note,
        free_tier_note=row.free_tier_note,
        stale=_is_stale(row.as_of, now),
    )


def quote(
    provider_id: str,
    model: str | None,
    unit: Unit,
    *,
    workspace_prices: Iterable[WorkspacePrice] = (),
    catalog_meta: Mapping[str, Any] | None = None,
    catalog_fetched_at: dt.datetime | None = None,
    catalog_ttl_s: int = providers.TTL_OPENROUTER_S,
    now: dt.datetime | None = None,
) -> PriceQuote | None:
    """Resolve one price: workspace → live → table → ``None`` (D-V4-39).

    Args:
        provider_id: The registry id (or pseudo id) being priced.
        model: The model id, if the provider prices per model.
        unit: The unit the quantity is measured in.
        workspace_prices: The workspace's own prices (model-specific beats model-agnostic;
            matched on the id or its ``price_ref`` alias).
        catalog_meta: The cached catalog item's raw ``meta`` for an OpenRouter entry
            (its ``"pricing"`` mapping is USD per single unit); ``None`` skips the live source.
        catalog_fetched_at: When that catalog row was fetched (the live quote's ``as_of``).
        catalog_ttl_s: The catalog TTL past which a live quote is ``stale``.
        now: The clock (defaults to the current UTC time).

    Returns:
        The quote, or ``None`` when no source knows the price — callers record
        "no price", never zero.
    """
    clock = now or dt.datetime.now(dt.UTC)
    alias = price_alias(provider_id)
    found = _workspace_quote(provider_id, alias, model, unit, workspace_prices)
    if found is not None:
        return found
    if catalog_meta is not None:
        found = _live_quote(provider_id, model, unit, catalog_meta, catalog_fetched_at, catalog_ttl_s, clock)
        if found is not None:
            return found
    return _table_quote(alias, model, unit, clock)


def quote_all(
    provider_id: str,
    model: str | None,
    *,
    units: Iterable[Unit] | None = None,
    **kwargs: Any,
) -> list[PriceQuote]:
    """Every unit :func:`quote` can price for one provider/model (the pickers' view).

    Args:
        provider_id: The registry id.
        model: The model id, if any.
        units: Restrict to these units (default: every :data:`Unit`).
        **kwargs: Passed through to :func:`quote`.

    Returns:
        The quotes found, in :data:`UNITS` order.
    """
    wanted = tuple(units) if units is not None else UNITS
    out: list[PriceQuote] = []
    for unit in wanted:
        found = quote(provider_id, model, unit, **kwargs)  # type: ignore[arg-type]
        if found is not None:
            out.append(found)
    return out
