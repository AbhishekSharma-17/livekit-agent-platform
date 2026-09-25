"""`lkap_contracts.pricing`: units, the table rules and `quote()`'s resolution order (COSTS.md §7)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from lkap_contracts import pricing, providers
from lkap_contracts.api_models import CostEstimate, CostEstimateRequest, MoneyRange
from lkap_contracts.pricing import (
    INFRA_PRICES,
    PRICE_VERSION,
    PRICES,
    UNITS,
    Price,
    PriceQuote,
    WorkspacePrice,
    quote,
)

NOW = dt.datetime(2026, 9, 25, 12, 0, tzinfo=dt.UTC)

#: Providers whose pricing page shows more than one tier, a volume discount or an included quota.
TIERED_PROVIDERS = {
    "openai-llm",
    "openai-realtime",
    "openai-tts",
    "openai-stt",
    "anthropic-llm",
    "google-llm",
    "google-realtime",
    "google-tts",
    "deepgram-stt",
    "deepgram-tts",
    "assemblyai-stt",
    "rime-tts",
    "inworld-tts",
    "livekit-inference-stt",
    "livekit-inference-llm",
    "livekit-inference-tts",
    *pricing.PSEUDO_PROVIDER_IDS,
}

ALL_ROWS = [*PRICES, *INFRA_PRICES]


# ------------------------------------------------------------------------ units and rows
@pytest.mark.parametrize("unit", UNITS)
def test_unit_literal_fits_the_session_costs_column(unit: str) -> None:
    assert len(unit) <= 16


def test_price_version_is_not_older_than_any_row() -> None:
    assert max(row.as_of for row in ALL_ROWS) <= PRICE_VERSION


@pytest.mark.parametrize("row", ALL_ROWS, ids=lambda r: f"{r.provider_id}:{r.model}:{r.unit}")
def test_every_row_has_a_source_and_a_date(row: Price) -> None:
    assert row.source_url.startswith("https://")
    dt.date.fromisoformat(row.as_of)


@pytest.mark.parametrize(
    "row",
    [r for r in ALL_ROWS if r.provider_id in TIERED_PROVIDERS],
    ids=lambda r: f"{r.provider_id}:{r.model}:{r.unit}",
)
def test_every_tiered_row_says_which_tier(row: Price) -> None:
    assert row.tier_note


def test_row_ids_are_registry_or_pseudo_ids() -> None:
    known = {spec.id for spec in providers.REGISTRY}
    for row in PRICES:
        assert row.provider_id in known
    for row in INFRA_PRICES:
        assert row.provider_id in pricing.PSEUDO_PROVIDER_IDS


def test_no_row_is_listed_twice() -> None:
    keys = [(r.provider_id, r.model, r.unit) for r in ALL_ROWS]
    assert len(keys) == len(set(keys))


def test_only_known_zeros_are_zero() -> None:
    zeros = [r for r in ALL_ROWS if r.usd_per_unit == 0]
    assert [(r.provider_id, r.unit) for r in zeros] == [("fastembed-embedding", "tokens_in")]
    assert zeros[0].tier_note == "runs on the worker; no vendor charge"


@pytest.mark.parametrize(
    ("unit", "per_million"),
    [("tokens_in", "0.15"), ("cached_tokens_in", "0.075"), ("tokens_out", "0.60")],
)
def test_two_route_model_is_priced_at_the_higher_route_with_a_note(unit: str, per_million: str) -> None:
    """R-V4-58: LiveKit's gpt-oss-120b routes to Groq or Baseten; the Groq (higher) route is priced."""
    q = quote("livekit-inference-llm", "openai/gpt-oss-120b", unit, now=NOW)  # type: ignore[arg-type]
    assert q is not None
    assert q.usd_per_unit == Decimal(per_million) / Decimal(1_000_000)
    assert q.tier_note and "Groq" in q.tier_note and "Baseten" in q.tier_note


def test_realtime_rows_are_split_and_never_folded() -> None:
    for model in ("gpt-realtime", "gpt-realtime-mini"):
        assert quote("openai-realtime", model, "audio_tokens_in", now=NOW) is not None
        assert quote("openai-realtime", model, "text_tokens_in", now=NOW) is not None
        assert quote("openai-realtime", model, "tokens_in", now=NOW) is None


# ------------------------------------------------------------------------ models
def test_price_quote_round_trips() -> None:
    q = PriceQuote(
        provider_id="openai-llm",
        model="gpt-4.1",
        unit="tokens_in",
        usd_per_unit=Decimal("0.000002"),
        source="table",
        source_url="https://example.test/pricing",
        as_of="2026-09-25",
    )
    assert PriceQuote.model_validate_json(q.model_dump_json()) == q
    assert q.currency == "USD"


def test_workspace_price_round_trips_and_is_bounded() -> None:
    wp = WorkspacePrice(
        provider_id="bey-avatar", unit="minutes", usd_per_unit=Decimal("0.10"), as_of="2026-09-25"
    )
    assert WorkspacePrice.model_validate_json(wp.model_dump_json()) == wp
    with pytest.raises(ValidationError):
        WorkspacePrice(
            provider_id="bey-avatar", unit="minutes", usd_per_unit=Decimal("-1"), as_of="2026-09-25"
        )
    with pytest.raises(ValidationError):
        WorkspacePrice(
            provider_id="bey-avatar", unit="minutes", usd_per_unit=Decimal("1001"), as_of="2026-09-25"
        )
    with pytest.raises(ValidationError):
        WorkspacePrice.model_validate(
            {"provider_id": "bey-avatar", "unit": "seconds", "usd_per_unit": "1", "as_of": "2026-09-25"}
        )


def test_cost_estimate_request_takes_at_most_one_source() -> None:
    assert CostEstimateRequest().agent_id is None
    assert CostEstimateRequest(agent_id="a1").agent_id == "a1"
    with pytest.raises(ValidationError):
        CostEstimateRequest(agent_id="a1", template_id="t1")


def test_cost_estimate_round_trips() -> None:
    est = CostEstimate(
        per_minute_usd=MoneyRange(low=Decimal("0.02"), mid=Decimal("0.03"), high=Decimal("0.05")),
        session_minutes=5,
        channel="web",
        price_version=PRICE_VERSION,
        as_of="2026-09-25",
    )
    assert CostEstimate.model_validate_json(est.model_dump_json()) == est


# ------------------------------------------------------------------------ quote() precedence
def _wp(**kw: Any) -> WorkspacePrice:
    base: dict[str, Any] = {"unit": "tokens_in", "usd_per_unit": Decimal("0.5"), "as_of": "2026-09-20"}
    return WorkspacePrice.model_validate({**base, **kw})


def test_quote_prefers_workspace_over_live_over_table() -> None:
    meta = {"pricing": {"prompt": "0.000009"}}
    table = quote("openai-llm", "gpt-4.1", "tokens_in", now=NOW)
    live = quote("openai-llm", "gpt-4.1", "tokens_in", catalog_meta=meta, now=NOW)
    ws = quote(
        "openai-llm",
        "gpt-4.1",
        "tokens_in",
        workspace_prices=[_wp(provider_id="openai-llm", model="gpt-4.1")],
        catalog_meta=meta,
        now=NOW,
    )
    assert table is not None and table.source == "table" and table.usd_per_unit == Decimal("0.000002")
    assert table.source_url == "https://developers.openai.com/api/docs/pricing"
    assert live is not None and live.source == "live" and live.usd_per_unit == Decimal("0.000009")
    assert live.source_url == pricing.OPENROUTER_MODELS_URL
    assert ws is not None and ws.source == "workspace" and ws.usd_per_unit == Decimal("0.5")
    assert ws.source_url is None and ws.as_of == "2026-09-20"


def test_quote_returns_none_when_no_source_knows() -> None:
    assert quote("elevenlabs-tts", "eleven_flash_v2_5", "chars", now=NOW) is None
    assert quote("openai-llm", "gpt-x", "tokens_in", now=NOW) is None


def test_workspace_model_specific_beats_model_agnostic() -> None:
    prices = [
        _wp(provider_id="bey-avatar", unit="minutes", usd_per_unit=Decimal("0.2")),
        _wp(provider_id="bey-avatar", model="face-1", unit="minutes", usd_per_unit=Decimal("0.1")),
    ]
    specific = quote("bey-avatar", "face-1", "minutes", workspace_prices=prices, now=NOW)
    agnostic = quote("bey-avatar", "other", "minutes", workspace_prices=prices, now=NOW)
    assert specific is not None and specific.usd_per_unit == Decimal("0.1")
    assert agnostic is not None and agnostic.usd_per_unit == Decimal("0.2")


def _agnostic_deepgram_tts() -> Price:
    return next(r for r in PRICES if r.provider_id == "deepgram-tts" and r.model is None)


def test_table_model_specific_beats_model_agnostic() -> None:
    specific = quote("deepgram-tts", "aura-1", "chars", now=NOW)
    agnostic = quote("deepgram-tts", "some-new-voice", "chars", now=NOW)
    assert specific is not None and specific.usd_per_unit == Decimal("0.000015")
    assert agnostic is not None and agnostic.usd_per_unit == _agnostic_deepgram_tts().usd_per_unit


def test_a_voice_id_is_priced_by_its_family_prefix() -> None:
    """R-V4-61: `aura-2-andromeda-en` resolves to the `aura-2` row, not the agnostic one."""
    family = next(r for r in PRICES if (r.provider_id, r.model) == ("deepgram-tts", "aura-2"))
    assert pricing.lookup("deepgram-tts", "aura-2-andromeda-en", "chars") is family


def test_an_aura_1_voice_gets_the_aura_1_price() -> None:
    q = quote("deepgram-tts", "aura-asteria-en", "chars", now=NOW)
    assert q is not None and q.usd_per_unit == Decimal("0.015") / Decimal(1_000)


@pytest.mark.parametrize("model", ["aura-20", "aura-2_andromeda", "aura", "xaura-2-andromeda-en", None])
def test_the_family_prefix_needs_a_dash_and_never_matches_a_substring(model: str | None) -> None:
    """`aura-2` never prices `aura-20`, `aura-2_x` or an id that merely contains it: the agnostic row does."""
    assert pricing.lookup("deepgram-tts", model, "chars") is _agnostic_deepgram_tts()


def test_an_llm_variant_is_not_priced_at_its_familys_rate() -> None:
    """The family step is for voice-shaped TTS ids: `gpt-4.1-nano-2026` is not `gpt-4.1` (20x the price)."""
    assert pricing.lookup("openai-llm", "gpt-4.1-nano-2026", "tokens_in") is None


def test_a_livekit_inference_tts_voice_takes_the_family_step() -> None:
    q = quote("livekit-inference-tts", "deepgram/aura-2-thalia-en", "chars", now=NOW)
    assert q is not None and q.usd_per_unit == Decimal("30.00") / Decimal(1_000_000)


def test_the_longest_family_prefix_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        Price(
            provider_id="rime-tts",
            model="fam",
            unit="chars",
            usd_per_unit=Decimal(1),
            source_url="u",
            as_of="2026-09-25",
        ),
        Price(
            provider_id="rime-tts",
            model="fam-2",
            unit="chars",
            usd_per_unit=Decimal(2),
            source_url="u",
            as_of="2026-09-25",
        ),
        Price(
            provider_id="rime-tts",
            model=None,
            unit="chars",
            usd_per_unit=Decimal(3),
            source_url="u",
            as_of="2026-09-25",
        ),
    ]
    monkeypatch.setattr(pricing, "PRICES", rows)
    hit = pricing.lookup("rime-tts", "fam-2-voice", "chars")
    assert hit is not None and hit.usd_per_unit == Decimal(2)
    exact = pricing.lookup("rime-tts", "fam", "chars")
    assert exact is not None and exact.usd_per_unit == Decimal(1)


def test_price_ref_aliases_to_another_entrys_rows() -> None:
    q = quote("openai-responses-llm", "gpt-4.1", "tokens_in", now=NOW)
    assert q is not None
    assert q.provider_id == "openai-llm"
    assert q.usd_per_unit == Decimal("0.000002")


def test_workspace_price_matches_the_alias_too() -> None:
    q = quote(
        "openai-responses-llm",
        "gpt-4.1",
        "tokens_in",
        workspace_prices=[_wp(provider_id="openai-llm", model="gpt-4.1")],
        now=NOW,
    )
    assert q is not None and q.source == "workspace"


def test_no_price_ref_is_a_self_alias_and_every_one_names_a_registry_id() -> None:
    ids = {spec.id for spec in providers.REGISTRY}
    refs = [(spec.id, spec.price_ref) for spec in providers.REGISTRY if spec.price_ref is not None]
    assert refs, "at least one alias (openai-responses-llm -> openai-llm)"
    for spec_id, ref in refs:
        assert ref != spec_id
        assert ref in ids


def test_a_stale_table_row_is_flagged() -> None:
    later = NOW + dt.timedelta(days=pricing.PRICE_STALE_DAYS + 1)
    q = quote("openai-llm", "gpt-4.1", "tokens_in", now=later)
    assert q is not None and q.stale is True
    fresh = quote("openai-llm", "gpt-4.1", "tokens_in", now=NOW)
    assert fresh is not None and fresh.stale is False


def test_table_quote_carries_the_row_notes() -> None:
    q = quote("livekit-inference-tts", "cartesia/sonic-3", "chars", now=NOW)
    assert q is not None
    assert q.tier_note and "Build" in q.tier_note
    assert q.free_tier_note


def test_infra_rows_are_quotable_by_model() -> None:
    assert quote("livekit-agent", None, "minutes", now=NOW) is not None
    sip = quote("livekit-sip", "trunk", "minutes", now=NOW)
    assert sip is not None and sip.usd_per_unit == Decimal("0.004")
    egress = quote("livekit-egress", "video", "minutes", now=NOW)
    assert egress is not None and egress.usd_per_unit == Decimal("0.02")
    assert quote("livekit-egress", None, "minutes", now=NOW) is None


# ------------------------------------------------------------------------ the OpenRouter unit map
@pytest.mark.parametrize(
    ("provider_id", "unit", "key"),
    [
        ("openrouter-llm", "tokens_in", "prompt"),
        ("openrouter-llm", "tokens_out", "completion"),
        ("openrouter-llm", "cached_tokens_in", "input_cache_read"),
        ("openrouter-llm", "audio_tokens_in", "audio"),
        ("openrouter-llm", "audio_tokens_out", "audio_output"),
        ("openrouter-llm", "requests", "request"),
        ("openrouter-stt", "audio_s_in", "prompt"),
        ("openrouter-tts", "chars", "prompt"),
        ("openrouter-embedding", "tokens_in", "prompt"),
        ("openrouter-image-gen", "images", "image_output"),
    ],
)
def test_openrouter_unit_map_per_entry_kind(provider_id: str, unit: str, key: str) -> None:
    assert pricing.openrouter_key(provider_id, unit) == key  # type: ignore[arg-type]


def test_openrouter_stt_prompt_prices_audio_seconds() -> None:
    meta = {"pricing": {"prompt": "0.0000716667", "completion": "0"}}
    q = quote("openrouter-stt", "deepgram/nova-3", "audio_s_in", catalog_meta=meta, now=NOW)
    assert q is not None and q.source == "live"
    assert q.usd_per_unit == Decimal("0.0000716667")
    assert quote("openrouter-stt", "deepgram/nova-3", "tokens_in", catalog_meta=meta, now=NOW) is None


def test_openrouter_tts_prompt_prices_characters() -> None:
    meta = {"pricing": {"prompt": "0.00003"}}
    q = quote("openrouter-tts", "deepgram/aura-2", "chars", catalog_meta=meta, now=NOW)
    assert q is not None and q.usd_per_unit == Decimal("0.00003")


def test_a_free_openrouter_model_is_a_known_zero_with_a_note() -> None:
    meta = {"pricing": {"prompt": "0", "completion": "0"}}
    q = quote(
        "openrouter-llm", "meta-llama/llama-3.3-8b-instruct:free", "tokens_in", catalog_meta=meta, now=NOW
    )
    assert q is not None
    assert q.usd_per_unit == 0
    assert q.free_tier_note


@pytest.mark.parametrize("value", ["-1", "abc", None, "NaN"])
def test_an_unusable_live_value_falls_through_to_the_table(value: str | None) -> None:
    meta = {"pricing": {"prompt": value}}
    q = quote("openrouter-llm", "openai/gpt-4.1", "tokens_in", catalog_meta=meta, now=NOW)
    assert q is None  # no table rows for openrouter-llm: unknown, never zero


def test_a_live_quote_past_its_ttl_is_stale_and_dated_by_the_fetch() -> None:
    fetched = NOW - dt.timedelta(hours=7)
    meta = {"pricing": {"prompt": "0.000001"}}
    q = quote(
        "openrouter-llm",
        "openai/gpt-4.1",
        "tokens_in",
        catalog_meta=meta,
        catalog_fetched_at=fetched,
        now=NOW,
    )
    assert q is not None
    assert q.stale is True
    assert q.as_of == fetched.date().isoformat()
    assert q.fetched_at == fetched


def test_quote_all_lists_every_priced_unit() -> None:
    quotes = pricing.quote_all("openai-llm", "gpt-4.1", now=NOW)
    assert {q.unit for q in quotes} == {"tokens_in", "tokens_out", "cached_tokens_in"}


def test_lookup_is_the_table_half_without_aliasing() -> None:
    assert pricing.lookup("openai-responses-llm", "gpt-4.1", "tokens_in") is None
    assert pricing.lookup("openai-llm", "gpt-4.1", "tokens_in") is not None
