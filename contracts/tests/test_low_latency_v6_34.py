"""V6-34 low-latency turn taking: the fast preset, per-model end of turn, VAD and routing fields."""

from typing import Any

import pytest

from lkap_contracts.agent_config import PipelineConfig, stt_turns_opted_in, transcriber_ends_turns
from lkap_contracts.pricing import PRICES
from lkap_contracts.providers import (
    FLUX_OPTION_FIELDS,
    STICKY_ROUTING_FIELD,
    VAD_MIN_SILENCE_WITH_TURN_DETECTOR,
    get,
    stt_end_of_turn,
)
from lkap_contracts.turn_handling import (
    CONVERSATION_PRESETS,
    FAST_PRESETS,
    PRESET_KEYS,
    TurnDetectorSettings,
    TurnHandlingOptions,
    preset_values,
    resolve_turn_handling,
)

#: The fast preset's pinned values, spelled out (a change here changes every agent on it).
PINNED_FAST: dict[str, dict[str, Any]] = {
    "stt": {
        "endpointing": {"mode": "fixed", "min_delay": 0.1},
        "interruption": {
            "min_duration": 0.5,
            "min_words": 0,
            "false_interruption_timeout": 2.0,
            "resume_false_interruption": True,
        },
        "preemptive_generation": {"enabled": True, "preemptive_tts": True},
    },
    "detector": {
        "endpointing": {"mode": "fixed", "min_delay": 0.3, "max_delay": 2.5},
        "interruption": {
            "min_duration": 0.5,
            "min_words": 0,
            "false_interruption_timeout": 2.0,
            "resume_false_interruption": True,
        },
        "preemptive_generation": {"enabled": True},
    },
}

INFERENCE_FLUX = {"provider_id": "livekit-inference-stt", "model": "deepgram/flux-general-en"}
INFERENCE_NOVA = {"provider_id": "livekit-inference-stt", "model": "deepgram/nova-3"}
DIRECT_FLUX = {"provider_id": "deepgram-flux-stt", "model": "flux-general-en"}


def _pipeline(**overrides: Any) -> PipelineConfig:
    return PipelineConfig.model_validate({"mode": "cascaded", **overrides})


@pytest.mark.parametrize(("stt_turns", "key"), [(True, "stt"), (False, "detector")])
def test_resolve_turn_handling_fast_preset_picks_the_dict_for_the_pipeline(stt_turns: bool, key: str) -> None:
    assert resolve_turn_handling("fast", {}, stt_turns=stt_turns) == PINNED_FAST[key]
    assert FAST_PRESETS[key] == PINNED_FAST[key]  # type: ignore[index]


@pytest.mark.parametrize("key", ["stt", "detector"])
def test_fast_preset_validates_as_typed_turn_handling(key: str) -> None:
    typed = TurnHandlingOptions.model_validate(FAST_PRESETS[key])  # type: ignore[index]

    assert typed.model_extra == {}
    assert typed.model_dump() == FAST_PRESETS[key]  # type: ignore[index]


def test_fast_preset_replaces_the_same_keys_as_the_other_presets() -> None:
    stored = {"interruption": {"min_duration": 2.0}, "user_turn_limit": {"max_words": 40}}

    resolved = resolve_turn_handling("fast", stored, stt_turns=True)

    assert resolved == {**PINNED_FAST["stt"], "user_turn_limit": {"max_words": 40}}
    assert frozenset({"endpointing", "interruption", "preemptive_generation"}) == PRESET_KEYS


@pytest.mark.parametrize("preset", sorted(CONVERSATION_PRESETS))
def test_other_presets_ignore_stt_turns(preset: str) -> None:
    assert resolve_turn_handling(preset, {}, stt_turns=True) == CONVERSATION_PRESETS[preset]  # type: ignore[arg-type]
    assert preset_values(preset, stt_turns=True) == CONVERSATION_PRESETS[preset]


def test_preset_values_custom_is_empty_and_never_the_table() -> None:
    assert preset_values("custom") == {}
    values = preset_values("fast", stt_turns=True)
    values["endpointing"]["min_delay"] = 9.0
    assert FAST_PRESETS["stt"]["endpointing"]["min_delay"] == 0.1


def test_fast_preset_is_stored_by_name_never_expanded() -> None:
    pipeline = _pipeline(conversation_preset="fast")

    dumped = pipeline.model_dump(mode="json")

    assert dumped["conversation_preset"] == "fast"
    assert dumped["turn_handling"] == {}


@pytest.mark.parametrize(
    ("provider_id", "model", "expected"),
    [
        ("deepgram-flux-stt", "flux-general-en", "entry"),
        ("deepgram-flux-stt", None, "entry"),
        ("livekit-inference-stt", "deepgram/flux-general-en", "model"),
        ("livekit-inference-stt", "deepgram/flux-general-multi", "model"),
        ("livekit-inference-stt", "deepgram/flux-general-en:en", "model"),
        ("livekit-inference-stt", "deepgram/nova-3", None),
        ("livekit-inference-stt", None, None),
        ("livekit-inference-stt", "cartesia/ink-2", None),
        ("deepgram-stt", "nova-3", None),
        ("openai-llm", "gpt-4.1", None),
        ("no-such-entry", None, None),
    ],
)
def test_stt_end_of_turn_by_entry_and_model(
    provider_id: str, model: str | None, expected: str | None
) -> None:
    assert stt_end_of_turn(provider_id, model) == expected


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        # Direct Flux ends turns whatever the preset (V6-02, unchanged).
        ({"stt": DIRECT_FLUX}, True),
        ({"stt": DIRECT_FLUX, "conversation_preset": "balanced"}, True),
        # Inference Flux only when the agent opts in.
        ({"stt": INFERENCE_FLUX}, False),
        ({"stt": INFERENCE_FLUX, "conversation_preset": "snappy"}, False),
        ({"stt": INFERENCE_FLUX, "turn_detector": {"mode": "hosted"}}, False),
        ({"stt": INFERENCE_FLUX, "conversation_preset": "fast"}, True),
        ({"stt": INFERENCE_FLUX, "turn_detector": {"mode": "stt"}}, True),
        # A model that cannot never does.
        ({"stt": INFERENCE_NOVA, "conversation_preset": "fast"}, False),
        ({"stt": INFERENCE_NOVA, "turn_detector": {"mode": "stt"}}, False),
        # An explicit detector slot, or a pipeline without the cascaded transcriber, never does.
        (
            {
                "stt": INFERENCE_FLUX,
                "conversation_preset": "fast",
                "turn_detection": {"provider_id": "inference-turn-detector"},
            },
            False,
        ),
        ({"stt": DIRECT_FLUX, "turn_detection": {"provider_id": "inference-turn-detector"}}, False),
        ({"mode": "half_cascade", "stt": DIRECT_FLUX}, False),
        ({}, False),
    ],
)
def test_transcriber_ends_turns_follows_the_opt_in_rule(overrides: dict[str, Any], expected: bool) -> None:
    assert transcriber_ends_turns(_pipeline(**overrides)) is expected


def test_transcriber_ends_turns_reads_an_explicit_slot_over_the_stored_ref() -> None:
    pipeline = _pipeline(stt=INFERENCE_NOVA, conversation_preset="fast")

    assert transcriber_ends_turns(
        pipeline, stt_provider_id="livekit-inference-stt", stt_model="deepgram/flux-general-multi"
    )
    assert not transcriber_ends_turns(pipeline)


def test_stt_turns_opt_in_is_the_fast_preset_or_the_stt_detector_mode() -> None:
    assert stt_turns_opted_in(_pipeline(conversation_preset="fast"))
    assert stt_turns_opted_in(_pipeline(turn_detector={"mode": "stt"}))
    assert not stt_turns_opted_in(_pipeline(conversation_preset="snappy", turn_detector={"mode": "local"}))


def test_turn_detector_mode_accepts_stt_and_keeps_the_old_values() -> None:
    for mode in ("hosted", "local", "stt", None):
        assert TurnDetectorSettings.model_validate({"mode": mode}).mode == mode


def test_inference_stt_offers_the_flux_options_with_the_direct_entry_names() -> None:
    inference = [field.name for field in get("livekit-inference-stt").fields]
    direct = [field.name for field in get("deepgram-flux-stt").fields]

    assert tuple(direct) == FLUX_OPTION_FIELDS
    assert set(FLUX_OPTION_FIELDS) <= set(inference)
    assert all(field.default is None for field in get("livekit-inference-stt").fields if field.name in direct)


def test_direct_flux_fields_are_worded_as_before() -> None:
    fields = {field.name: field for field in get("deepgram-flux-stt").fields}

    assert fields["eager_eot_threshold"].placeholder == "off"
    assert fields["eot_timeout_ms"].placeholder == "3000"
    assert fields["eot_threshold"].help == (
        "How sure Flux must be that the caller finished (0.5-0.9). Higher waits longer."
    )


@pytest.mark.parametrize(("provider_id", "placeholder"), [("silero-vad", "0.55"), ("inference-vad", "0.25")])
def test_vad_entries_expose_the_silence_and_threshold_fields_without_defaults(
    provider_id: str, placeholder: str
) -> None:
    fields = {field.name: field for field in get(provider_id).fields}

    assert {"min_silence_duration", "activation_threshold", "prefix_padding_duration"} <= set(fields)
    assert fields["min_silence_duration"].placeholder == placeholder
    assert all(field.default is None for field in fields.values())
    assert VAD_MIN_SILENCE_WITH_TURN_DETECTOR == 0.25


def test_openrouter_sticky_routing_is_off_for_stored_agents_and_recommended_for_new_ones() -> None:
    field = next(field for field in get("openrouter-llm").fields if field.name == STICKY_ROUTING_FIELD)

    assert field.type == "boolean"
    assert field.default is False
    assert field.recommended is True


def test_openrouter_provider_help_names_latency_sorting_and_order() -> None:
    field = next(field for field in get("openrouter-llm").fields if field.name == "provider")

    assert field.help is not None
    assert '{"sort": "latency"}' in field.help
    assert '"order"' in field.help


def test_inference_lists_inworld_flash_and_prices_it_on_its_own_row() -> None:
    assert "inworld/inworld-tts-2-flash" in {model.id for model in get("livekit-inference-tts").models}
    rows = [
        price
        for price in PRICES
        if price.provider_id == "livekit-inference-tts" and price.model == "inworld/inworld-tts-2-flash"
    ]
    assert len(rows) == 1


@pytest.mark.parametrize("model", ["openai/gpt-4.1-mini", "openai/gpt-4.1-nano"])
def test_inference_lists_small_openai_models_as_not_reasoning(model: str) -> None:
    listed = next(item for item in get("livekit-inference-llm").models if item.id == model)

    assert listed.reasoning is False
    assert any(price.model == model for price in PRICES if price.provider_id == "livekit-inference-llm")
