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


#: Every known price, in registry order. Filled by V2-05.
#:
#: Only prices read directly off a vendor's own current pricing page this pass
#: (2026-09-23) are listed; every other provider intentionally has no entry —
#: :func:`lookup` returning ``None`` and the caller recording ``note="no
#: price"`` is the designed safe path (module docstring), so an unverifiable
#: number is never guessed in to avoid a "no price" line. Notably absent
#: despite being named in PLAN-V2's V2-05 card: LiveKit Inference (per-model
#: per-minute pricing exists at livekit.com/pricing/inference but varies
#: 0.0002-0.0676 $/min per model with no single verifiable figure fetched this
#: pass), Cartesia and ElevenLabs (both plan/credit-based on their public pages,
#: no disclosed pay-as-you-go per-unit USD rate), Beyond Presence and Tavus
#: (tiered monthly plans, no per-minute API rate disclosed) — all four stay
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
