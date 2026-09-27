"""OpenRouter speech prices per model (D-V6-9 defect B): the unit, the sanity bound, the keys.

The catalogue items below are copied from OpenRouter's public ``/models`` listing
(``output_modalities=transcription`` / ``speech``, 2026-09-28): ``pricing`` and
``architecture`` only, the shape the catalogue adapter caches in ``CatalogItem.meta``.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from lkap_contracts import pricing
from lkap_contracts.pricing import UNIT_UNKNOWN_NOTE, openrouter_unit, quote

NOW = dt.datetime(2026, 9, 28, 12, 0, tzinfo=dt.UTC)


def _stt(prompt: str, completion: str, tokenizer: str = "Other") -> dict[str, Any]:
    return {
        "pricing": {"prompt": prompt, "completion": completion},
        "architecture": {
            "modality": "audio->transcription",
            "input_modalities": ["audio"],
            "output_modalities": ["transcription"],
            "tokenizer": tokenizer,
            "instruct_type": None,
        },
    }


def _tts(prompt: str, completion: str, tokenizer: str = "Other") -> dict[str, Any]:
    return {
        "pricing": {"prompt": prompt, "completion": completion},
        "architecture": {
            "modality": "text->speech",
            "input_modalities": ["text"],
            "output_modalities": ["speech"],
            "tokenizer": tokenizer,
            "instruct_type": None,
        },
    }


NOVA_3 = _stt("0.0000716666666667", "0")
GPT_4O_MINI_TRANSCRIBE = _stt("0.00000125", "0.000005", "GPT")
WHISPER_1 = _stt("0.0001", "0", "GPT")
VOXTRAL_MINI = _stt("0.00005", "0", "Mistral")
MAI_TRANSCRIBE_2 = _stt("0.1", "0")
GEMINI_TRANSCRIBE = _stt("0.000002", "0.000012", "Gemini")
AURA_2 = _tts("0.00003", "0")
GEMINI_FLASH_TTS = _tts("0.0000005", "0.000009", "Gemini")
SEED_AUDIO = _tts("0", "0.0025")
FLUX_TTS_FREE = _tts("0", "0")


@pytest.mark.parametrize(
    ("meta", "kind", "unit"),
    [
        (NOVA_3, "stt", "per_second"),
        (WHISPER_1, "stt", "per_second"),  # a GPT tokenizer, still billed per second
        (VOXTRAL_MINI, "stt", "per_second"),
        (GPT_4O_MINI_TRANSCRIBE, "stt", "per_token"),
        (GEMINI_TRANSCRIBE, "stt", "per_token"),
        (MAI_TRANSCRIBE_2, "stt", "unknown"),  # 0.1 a second would be $6 a minute
        (AURA_2, "tts", "per_char"),
        (FLUX_TTS_FREE, "tts", "per_char"),
        (GEMINI_FLASH_TTS, "tts", "per_token"),
        (SEED_AUDIO, "tts", "unknown"),  # 0.0025 per audio token is ~$4 per 1,000 characters
        ({"pricing": {}}, "stt", "unknown"),
        ({"pricing": {"prompt": "-1", "completion": "0"}}, "tts", "unknown"),  # variable pricing
        (AURA_2, "stt", "unknown"),  # a speech model in the transcription slot
        (NOVA_3, "tts", "unknown"),
        (GPT_4O_MINI_TRANSCRIBE, "llm", "per_token"),
    ],
)
def test_openrouter_unit_classifies_real_catalogue_items(meta: dict[str, Any], kind: str, unit: str) -> None:
    assert openrouter_unit(meta, kind) == unit


def test_openrouter_unit_without_architecture_uses_the_pricing_keys() -> None:
    assert openrouter_unit({"pricing": NOVA_3["pricing"]}, "stt") == "per_second"
    assert openrouter_unit({"pricing": GEMINI_FLASH_TTS["pricing"]}, "tts") == "per_token"


@pytest.mark.parametrize(
    ("prompt", "unit"),
    [
        ("0.0166666", "per_second"),  # $0.999996 a minute: just under the bound
        (str(Decimal(1) / 60), "per_second"),  # exactly $1 a minute is still a price
        ("0.0166667", "unknown"),  # $1.000002 a minute: just over
    ],
)
def test_the_stt_per_second_bound_at_both_edges(prompt: str, unit: str) -> None:
    assert openrouter_unit(_stt(prompt, "0"), "stt") == unit


@pytest.mark.parametrize(("prompt", "unit"), [("0.001", "per_char"), ("0.0010001", "unknown")])
def test_the_tts_per_character_bound_at_both_edges(prompt: str, unit: str) -> None:
    assert openrouter_unit(_tts(prompt, "0"), "tts") == unit


@pytest.mark.parametrize(
    ("audio_in", "unit"),
    [("0.000666", "per_token"), ("0.000667", "unknown")],  # x 25 tokens/s x 60 s = $0.999 / $1.0005
)
def test_the_stt_per_token_bound_at_both_edges(audio_in: str, unit: str) -> None:
    meta = {"pricing": {"prompt": audio_in, "completion": "0.0000001"}}
    assert openrouter_unit(meta, "stt") == unit


@pytest.mark.parametrize(
    ("spoken", "unit"),
    [("0.000599", "per_token"), ("0.000601", "unknown")],  # x 1,666.7 audio tokens per 1,000 characters
)
def test_the_tts_per_token_bound_at_both_edges(spoken: str, unit: str) -> None:
    assert openrouter_unit(_tts("0", spoken), "tts") == unit


def test_deepgram_nova_3_is_priced_per_audio_second() -> None:
    q = quote("openrouter-stt", "deepgram/nova-3", "audio_s_in", catalog_meta=NOVA_3, now=NOW)
    assert q is not None and q.source == "live"
    assert (q.usd_per_unit * 60).quantize(Decimal("0.0001")) == Decimal("0.0043")
    for unit in ("audio_tokens_in", "tokens_in", "tokens_out"):
        assert quote("openrouter-stt", "deepgram/nova-3", unit, catalog_meta=NOVA_3, now=NOW) is None


def test_gpt_4o_mini_transcribe_is_priced_per_token_never_per_second() -> None:
    model = "openai/gpt-4o-mini-transcribe"
    meta = GPT_4O_MINI_TRANSCRIBE
    assert quote("openrouter-stt", model, "audio_s_in", catalog_meta=meta, now=NOW) is None
    audio = quote("openrouter-stt", model, "audio_tokens_in", catalog_meta=meta, now=NOW)
    text = quote("openrouter-stt", model, "tokens_out", catalog_meta=meta, now=NOW)
    assert audio is not None and audio.usd_per_unit == Decimal("0.00000125")
    assert text is not None and text.usd_per_unit == Decimal("0.000005")


def test_an_audio_price_wins_over_prompt_for_audio_tokens_heard() -> None:
    meta = {"pricing": {"prompt": "0.00000125", "completion": "0.000005", "audio": "0.000003"}}
    q = quote(
        "openrouter-stt", "openai/gpt-4o-mini-transcribe", "audio_tokens_in", catalog_meta=meta, now=NOW
    )
    assert q is not None and q.usd_per_unit == Decimal("0.000003")


def test_mai_transcribe_2_is_refused_as_unit_unknown() -> None:
    model = "microsoft/mai-transcribe-2"
    for unit in pricing.UNITS:
        assert quote("openrouter-stt", model, unit, catalog_meta=MAI_TRANSCRIBE_2, now=NOW) is None  # type: ignore[arg-type]
    assert pricing.live_unpriced_note("openrouter-stt", MAI_TRANSCRIBE_2) == UNIT_UNKNOWN_NOTE
    assert pricing.live_unpriced_note("openrouter-stt", NOVA_3) is None
    assert pricing.live_unpriced_note("openrouter-llm", MAI_TRANSCRIBE_2) is None
    assert pricing.live_unpriced_note("openrouter-stt", None) is None


def test_gemini_tts_prices_text_read_and_audio_spoken() -> None:
    model = "google/gemini-3.8-flash-tts"
    meta = GEMINI_FLASH_TTS
    assert quote("openrouter-tts", model, "chars", catalog_meta=meta, now=NOW) is None
    text = quote("openrouter-tts", model, "tokens_in", catalog_meta=meta, now=NOW)
    spoken = quote("openrouter-tts", model, "tokens_out", catalog_meta=meta, now=NOW)
    assert text is not None and text.usd_per_unit == Decimal("0.0000005")
    assert spoken is not None and spoken.usd_per_unit == Decimal("0.000009")  # listed as `completion`


def test_an_audio_output_price_wins_for_audio_spoken() -> None:
    meta = {"pricing": {"prompt": "0.0000005", "completion": "0.000001", "audio_output": "0.000009"}}
    spoken = quote("openrouter-tts", "google/gemini-3.8-flash-tts", "tokens_out", catalog_meta=meta, now=NOW)
    assert spoken is not None and spoken.usd_per_unit == Decimal("0.000009")


def test_a_per_character_tts_has_no_token_quote() -> None:
    """A `completion` of "0" is not a known-zero audio-token price (it would make costing token-billed)."""
    assert quote("openrouter-tts", "deepgram/aura-2", "tokens_out", catalog_meta=AURA_2, now=NOW) is None
    chars = quote("openrouter-tts", "deepgram/aura-2", "chars", catalog_meta=AURA_2, now=NOW)
    assert chars is not None and chars.usd_per_unit == Decimal("0.00003")


def test_the_meta_less_key_map_is_unchanged_for_the_drift_job() -> None:
    assert pricing.openrouter_key("openrouter-stt", "audio_s_in") == "prompt"
    assert pricing.openrouter_key("openrouter-tts", "chars") == "prompt"
    assert pricing.openrouter_key("openrouter-stt", "audio_s_in", GPT_4O_MINI_TRANSCRIBE) is None
    assert pricing.openrouter_key("openrouter-llm", "tokens_in", MAI_TRANSCRIBE_2) == "prompt"
