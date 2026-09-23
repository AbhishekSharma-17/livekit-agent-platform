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
PRICE_VERSION = "2026-09-19"


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
PRICES: list[Price] = []


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
