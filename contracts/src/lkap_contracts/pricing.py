"""Vendor price table used to cost a session (CONTRACTS-V2 §4.2).

The table is deliberately empty in V2-00: entries (with a mandatory
``source_url`` and ``as_of``) are added by V2-05 alongside the registry breadth
work. An unknown price must never be treated as free — :func:`lookup` returns
``None`` and the caller records a cost line with ``cost_usd = None`` and
``note = "no price"``.
"""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

#: What a priced quantity is measured in (mirrors ``session_costs.unit``).
Unit = Literal[
    "tokens_in",
    "tokens_out",
    "audio_s_in",
    "audio_s_out",
    "chars",
    "minutes",
    "images",
]

#: Bumped whenever :data:`PRICES` changes; stored on every costed line so old
#: sessions keep the price they were costed with.
PRICE_VERSION = "2026-09-23"


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


#: Every known price, in registry order. Filled by V2-05; LiveKit Inference
#: entries added by V2-20F (docs/v2/_asks.md V2-20-4).
#:
#: Only prices read directly off a vendor's own current pricing page this pass
#: (2026-09-23) are listed; every other provider intentionally has no entry —
#: :func:`lookup` returning ``None`` and the caller recording ``note="no
#: price"`` is the designed safe path (module docstring), so an unverifiable
#: number is never guessed in to avoid a "no price" line. Still absent:
#: Cartesia and ElevenLabs direct (both plan/credit-based on their public
#: pages, no disclosed pay-as-you-go per-unit USD rate), Beyond Presence and
#: Tavus (tiered monthly plans, no per-minute API rate disclosed) — these stay
#: unpriced rather than estimated from a subscription tier.
PRICES: list[Price] = [
    # ---------------------------------------------------------------- OpenAI LLM
    Price(
        provider_id="openai-llm",
        model="gpt-4.1",
        unit="tokens_in",
        usd_per_unit=Decimal("0.000002"),
        source_url="https://developers.openai.com/api/docs/pricing",
        as_of="2026-09-23",
    ),
    Price(
        provider_id="openai-llm",
        model="gpt-4.1",
        unit="tokens_out",
        usd_per_unit=Decimal("0.000008"),
        source_url="https://developers.openai.com/api/docs/pricing",
        as_of="2026-09-23",
    ),
    Price(
        provider_id="openai-llm",
        model="gpt-4o",
        unit="tokens_in",
        usd_per_unit=Decimal("0.0000025"),
        source_url="https://developers.openai.com/api/docs/pricing",
        as_of="2026-09-23",
    ),
    Price(
        provider_id="openai-llm",
        model="gpt-4o",
        unit="tokens_out",
        usd_per_unit=Decimal("0.00001"),
        source_url="https://developers.openai.com/api/docs/pricing",
        as_of="2026-09-23",
    ),
    Price(
        provider_id="openai-llm",
        model="gpt-4o-mini",
        unit="tokens_in",
        usd_per_unit=Decimal("0.00000015"),
        source_url="https://developers.openai.com/api/docs/pricing",
        as_of="2026-09-23",
    ),
    Price(
        provider_id="openai-llm",
        model="gpt-4o-mini",
        unit="tokens_out",
        usd_per_unit=Decimal("0.0000006"),
        source_url="https://developers.openai.com/api/docs/pricing",
        as_of="2026-09-23",
    ),
    # ---------------------------------------------------------------- OpenAI TTS
    # `tts-1` is priced per character on the vendor page; `gpt-4o-mini-tts` (the
    # registry default) is priced per audio *token* there instead, a different
    # unit our `Unit` literal has no slot for — left unpriced rather than
    # converted with an assumed tokens-per-character ratio.
    Price(
        provider_id="openai-tts",
        model="tts-1",
        unit="chars",
        usd_per_unit=Decimal("0.000015"),
        source_url="https://developers.openai.com/api/docs/pricing",
        as_of="2026-09-23",
    ),
    # ---------------------------------------------------------------- Google Gemini LLM
    Price(
        provider_id="google-llm",
        model="gemini-2.5-flash",
        unit="tokens_in",
        usd_per_unit=Decimal("0.0000003"),
        source_url="https://ai.google.dev/gemini-api/docs/pricing",
        as_of="2026-09-23",
    ),
    Price(
        provider_id="google-llm",
        model="gemini-2.5-flash",
        unit="tokens_out",
        usd_per_unit=Decimal("0.0000025"),
        source_url="https://ai.google.dev/gemini-api/docs/pricing",
        as_of="2026-09-23",
    ),
    # ---------------------------------------------------------------- Deepgram
    Price(
        provider_id="deepgram-stt",
        model="nova-3",
        unit="audio_s_in",
        usd_per_unit=Decimal("0.00008"),
        source_url="https://deepgram.com/pricing",
        as_of="2026-09-23",
    ),
    Price(
        provider_id="deepgram-tts",
        model=None,
        unit="chars",
        usd_per_unit=Decimal("0.00003"),
        source_url="https://deepgram.com/pricing",
        as_of="2026-09-23",
    ),
    # ---------------------------------------------------------------- LiveKit Inference
    # Source: https://www.livekit.com/pricing/inference (fetched 2026-09-23). LiveKit
    # quotes each Inference model at its "Build" tier rate (the entry-level, no-volume-
    # discount tier every new workspace starts on); "Ship"/"Scale" are cheaper at
    # higher volume and are not modeled here. Only the models this registry actually
    # exposes (`contracts/src/lkap_contracts/providers.py`, `livekit-inference-{stt,
    # llm,tts}`) are priced; each `usd_per_unit` is computed from the page's own
    # per-minute / per-million figure so the source number stays visible in the diff.
    #
    # `openai/gpt-oss-120b` (LLM) and `inworld/inworld-tts-2` (TTS) are deliberately
    # left unpriced: the pricing page lists two different rates for GPT-OSS-120B
    # depending on the backend LiveKit routes to (Baseten $0.10/$0.50 per 1M tokens vs.
    # Groq $0.15/$0.60), and lists no "Inworld TTS 2" line item at all (only "Realtime
    # TTS 1.5/2.0" variants) — neither can be attributed to our registry's model id with
    # confidence, so per this module's own rule an unverifiable number is not guessed in.
    # ---- STT ($/audio_s_in, from the page's $/minute figure ÷ 60)
    Price(
        provider_id="livekit-inference-stt",
        model="deepgram/nova-3",
        unit="audio_s_in",
        usd_per_unit=Decimal("0.0048") / Decimal(60),  # Nova-3 (Monolingual): $0.0048/min
        source_url="https://www.livekit.com/pricing/inference",
        as_of="2026-09-23",
    ),
    Price(
        provider_id="livekit-inference-stt",
        model="deepgram/flux-general-en",
        unit="audio_s_in",
        usd_per_unit=Decimal("0.0065") / Decimal(60),  # Flux: $0.0065/min
        source_url="https://www.livekit.com/pricing/inference",
        as_of="2026-09-23",
    ),
    Price(
        provider_id="livekit-inference-stt",
        model="assemblyai/universal-streaming",
        unit="audio_s_in",
        usd_per_unit=Decimal("0.0025") / Decimal(60),  # Universal-Streaming: $0.0025/min
        source_url="https://www.livekit.com/pricing/inference",
        as_of="2026-09-23",
    ),
    Price(
        provider_id="livekit-inference-stt",
        model="cartesia/ink-whisper",
        unit="audio_s_in",
        usd_per_unit=Decimal("0.0030") / Decimal(60),  # Ink Whisper: $0.0030/min
        source_url="https://www.livekit.com/pricing/inference",
        as_of="2026-09-23",
    ),
    Price(
        provider_id="livekit-inference-stt",
        model="google/gemini-3.5-transcribe-live",
        unit="audio_s_in",
        usd_per_unit=Decimal("0.0095") / Decimal(60),  # Gemini 3.5 Transcribe Live: $0.0095/min
        source_url="https://www.livekit.com/pricing/inference",
        as_of="2026-09-23",
    ),
    # ---- LLM ($/token, from the page's $/million-tokens figure ÷ 1e6)
    Price(
        provider_id="livekit-inference-llm",
        model="google/gemma-4-31b-it",
        unit="tokens_in",
        usd_per_unit=Decimal("0.400") / Decimal(1_000_000),  # Gemma 4 31B: $0.400/1M in
        source_url="https://www.livekit.com/pricing/inference",
        as_of="2026-09-23",
    ),
    Price(
        provider_id="livekit-inference-llm",
        model="google/gemma-4-31b-it",
        unit="tokens_out",
        usd_per_unit=Decimal("1.200") / Decimal(1_000_000),  # Gemma 4 31B: $1.200/1M out
        source_url="https://www.livekit.com/pricing/inference",
        as_of="2026-09-23",
    ),
    Price(
        provider_id="livekit-inference-llm",
        model="google/gemini-3.5-flash",
        unit="tokens_in",
        usd_per_unit=Decimal("1.500") / Decimal(1_000_000),  # Gemini 3.5 Flash: $1.500/1M in
        source_url="https://www.livekit.com/pricing/inference",
        as_of="2026-09-23",
    ),
    Price(
        provider_id="livekit-inference-llm",
        model="google/gemini-3.5-flash",
        unit="tokens_out",
        usd_per_unit=Decimal("9.000") / Decimal(1_000_000),  # Gemini 3.5 Flash: $9.000/1M out
        source_url="https://www.livekit.com/pricing/inference",
        as_of="2026-09-23",
    ),
    Price(
        provider_id="livekit-inference-llm",
        model="openai/gpt-4.1",
        unit="tokens_in",
        usd_per_unit=Decimal("2.000") / Decimal(1_000_000),  # GPT-4.1 (Azure/OpenAI): $2.000/1M in
        source_url="https://www.livekit.com/pricing/inference",
        as_of="2026-09-23",
    ),
    Price(
        provider_id="livekit-inference-llm",
        model="openai/gpt-4.1",
        unit="tokens_out",
        usd_per_unit=Decimal("8.000") / Decimal(1_000_000),  # GPT-4.1 (Azure/OpenAI): $8.000/1M out
        source_url="https://www.livekit.com/pricing/inference",
        as_of="2026-09-23",
    ),
    Price(
        provider_id="livekit-inference-llm",
        model="openai/gpt-4o-mini",
        unit="tokens_in",
        usd_per_unit=Decimal("0.150") / Decimal(1_000_000),  # GPT-4o mini (Azure/OpenAI): $0.150/1M in
        source_url="https://www.livekit.com/pricing/inference",
        as_of="2026-09-23",
    ),
    Price(
        provider_id="livekit-inference-llm",
        model="openai/gpt-4o-mini",
        unit="tokens_out",
        usd_per_unit=Decimal("0.600") / Decimal(1_000_000),  # GPT-4o mini (Azure/OpenAI): $0.600/1M out
        source_url="https://www.livekit.com/pricing/inference",
        as_of="2026-09-23",
    ),
    # ---- TTS ($/char, from the page's $/million-characters figure ÷ 1e6)
    Price(
        provider_id="livekit-inference-tts",
        model="cartesia/sonic-3",
        unit="chars",
        usd_per_unit=Decimal("50.00") / Decimal(1_000_000),  # Sonic 3: $50.00/1M chars
        source_url="https://www.livekit.com/pricing/inference",
        as_of="2026-09-23",
    ),
    Price(
        provider_id="livekit-inference-tts",
        model="deepgram/aura-2",
        unit="chars",
        usd_per_unit=Decimal("30.00") / Decimal(1_000_000),  # Aura-2: $30.00/1M chars
        source_url="https://www.livekit.com/pricing/inference",
        as_of="2026-09-23",
    ),
    Price(
        provider_id="livekit-inference-tts",
        model="rime/mistv3",
        unit="chars",
        usd_per_unit=Decimal("30.00") / Decimal(1_000_000),  # Mist v3: $30.00/1M chars
        source_url="https://www.livekit.com/pricing/inference",
        as_of="2026-09-23",
    ),
]


def lookup(provider_id: str, model: str | None, unit: Unit) -> Price | None:
    """Return the price for a provider/model/unit triple, or ``None`` if unknown.

    A model-specific entry wins over a model-agnostic one for the same provider
    and unit.

    Args:
        provider_id: A registry id such as ``"openai-llm"``.
        model: The model the session actually used, if the provider is priced per model.
        unit: The unit the quantity is measured in.

    Returns:
        The matching :class:`Price`, or ``None`` when the table has no entry —
        callers must record "no price", never a zero cost.
    """
    fallback: Price | None = None
    for price in PRICES:
        if price.provider_id != provider_id or price.unit != unit:
            continue
        if price.model is not None and price.model == model:
            return price
        if price.model is None:
            fallback = fallback or price
    return fallback
