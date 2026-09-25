"""Conversation tuning in the agent config (V5-07): typed turn handling, presets, sounds."""

from typing import Any

import pytest
from pydantic import ValidationError

from lkap_contracts.agent_config import AgentConfig, PipelineConfig, VoiceConfig
from lkap_contracts.turn_handling import (
    AMBIENT_SOUNDS,
    CONVERSATION_PRESETS,
    PRESET_KEYS,
    TurnDetectorSettings,
    TurnHandlingOptions,
    resolve_turn_handling,
    turn_handling_dict,
)

#: The pinned value of each named preset. A change here is a behaviour change for every
#: agent on that preset, so it is spelled out rather than read back from the module.
PINNED_PRESETS: dict[str, dict[str, Any]] = {
    "balanced": {
        "endpointing": {"mode": "fixed", "min_delay": 0.5, "max_delay": 3.0},
        "interruption": {
            "min_duration": 0.5,
            "min_words": 0,
            "false_interruption_timeout": 2.0,
            "resume_false_interruption": True,
        },
    },
    "patient": {
        "endpointing": {"mode": "fixed", "min_delay": 1.0, "max_delay": 5.0},
        "interruption": {
            "min_duration": 0.5,
            "min_words": 0,
            "false_interruption_timeout": 2.0,
            "resume_false_interruption": True,
        },
    },
    "snappy": {
        "endpointing": {"mode": "fixed", "min_delay": 0.3, "max_delay": 2.0},
        "interruption": {
            "min_duration": 0.5,
            "min_words": 0,
            "false_interruption_timeout": 2.0,
            "resume_false_interruption": True,
        },
        "preemptive_generation": {"enabled": True},
    },
    "telephony": {
        "endpointing": {"mode": "fixed", "min_delay": 0.5, "max_delay": 4.0},
        "interruption": {
            "min_duration": 0.8,
            "min_words": 0,
            "false_interruption_timeout": 2.0,
            "resume_false_interruption": True,
        },
    },
}

#: Shapes a stored ``turn_handling`` has taken since v1 (console JSON field, tests, docs).
STORED_TURN_HANDLING: list[dict[str, Any]] = [
    {},
    {"endpointing": {"min_delay": 0.3}},
    {"turn_detection": "vad", "endpointing": {"min_delay": 0.3}},
    {"preemptive_generation": {"enabled": False}},
    {"preemptive_generation": {"enabled": True}},
    {"interruption": {"enabled": False}},
    {"interruption": {"false_interruption_timeout": None, "backchannel_boundary": [1.0, 0.5]}},
    {"user_turn_limit": {"max_words": 50}},
    {"nonsense": True},
    {"endpointing": {"mode": "dynamic", "alpha": 0.8, "future_key": 1}},
]


def _pipeline(**kwargs: Any) -> PipelineConfig:
    return PipelineConfig.model_validate(kwargs)


@pytest.mark.parametrize("stored", STORED_TURN_HANDLING)
def test_turn_handling_stored_dict_round_trips_unchanged(stored: dict[str, Any]) -> None:
    pipeline = _pipeline(turn_handling=stored)

    assert pipeline.turn_handling == stored
    assert isinstance(pipeline.turn_handling, dict)
    assert pipeline.model_dump(mode="json")["turn_handling"] == stored
    assert PipelineConfig.model_validate_json(pipeline.model_dump_json()).turn_handling == stored


@pytest.mark.parametrize(
    "stored",
    [
        {"endpointing": "fast"},
        {"endpointing": {"min_delay": "soon"}},
        {"interruption": {"min_duration": -1}},
        {"turn_detection": "telepathy"},
    ],
)
def test_turn_handling_with_invalid_typed_keys_is_kept_as_stored(stored: dict[str, Any]) -> None:
    assert _pipeline(turn_handling=stored).turn_handling == stored


def test_turn_handling_accepts_a_model_instance_and_stores_a_dict() -> None:
    typed = TurnHandlingOptions.model_validate({"endpointing": {"min_delay": 0.4}})

    pipeline = PipelineConfig(turn_handling=typed)

    assert pipeline.turn_handling == {"endpointing": {"min_delay": 0.4}}
    assert isinstance(pipeline.turn_handling, dict)


def test_turn_handling_dict_is_a_copy_for_both_shapes() -> None:
    stored = {"endpointing": {"min_delay": 0.4}}
    copied = turn_handling_dict(stored)
    copied["endpointing"]["min_delay"] = 9

    assert stored == {"endpointing": {"min_delay": 0.4}}
    assert turn_handling_dict(TurnHandlingOptions.model_validate(stored)) == stored


def test_pipeline_config_defaults_change_nothing_for_an_agent_saved_before_v5_07() -> None:
    before = {"mode": "cascaded", "turn_handling": {"endpointing": {"min_delay": 0.3}}}

    pipeline = PipelineConfig.model_validate(before)

    assert pipeline.conversation_preset == "custom"
    assert pipeline.turn_detector is None
    assert (
        resolve_turn_handling(pipeline.conversation_preset, pipeline.turn_handling) == before["turn_handling"]
    )
    assert VoiceConfig().ambient_sound == "none"


@pytest.mark.parametrize("stored", STORED_TURN_HANDLING)
def test_resolve_turn_handling_custom_returns_exactly_the_stored_dict(stored: dict[str, Any]) -> None:
    resolved = resolve_turn_handling("custom", stored)

    assert resolved == stored
    assert resolved is not stored


@pytest.mark.parametrize("preset", sorted(PINNED_PRESETS))
def test_resolve_turn_handling_named_preset_resolves_to_the_pinned_dict(preset: str) -> None:
    assert resolve_turn_handling(preset, {}) == PINNED_PRESETS[preset]  # type: ignore[arg-type]
    assert CONVERSATION_PRESETS[preset] == PINNED_PRESETS[preset]


def test_conversation_presets_are_exactly_the_four_named_presets() -> None:
    assert set(CONVERSATION_PRESETS) == {"patient", "balanced", "snappy", "telephony"}
    assert frozenset({"endpointing", "interruption", "preemptive_generation"}) == PRESET_KEYS


@pytest.mark.parametrize("preset", sorted(PINNED_PRESETS))
def test_every_preset_validates_as_typed_turn_handling(preset: str) -> None:
    typed = TurnHandlingOptions.model_validate(CONVERSATION_PRESETS[preset])

    assert typed.model_extra == {}
    assert typed.model_dump() == CONVERSATION_PRESETS[preset]


def test_resolve_turn_handling_named_preset_replaces_its_keys_and_keeps_the_others() -> None:
    stored = {
        "endpointing": {"min_delay": 1.7},
        "preemptive_generation": {"enabled": False},
        "user_turn_limit": {"max_words": 40},
    }

    resolved = resolve_turn_handling("balanced", stored)

    assert resolved == {**PINNED_PRESETS["balanced"], "user_turn_limit": {"max_words": 40}}
    assert stored["endpointing"] == {"min_delay": 1.7}


def test_resolve_turn_handling_never_hands_out_the_preset_table() -> None:
    resolved = resolve_turn_handling("snappy", {})
    resolved["endpointing"]["min_delay"] = 9.0

    assert CONVERSATION_PRESETS["snappy"]["endpointing"]["min_delay"] == 0.3


def test_preset_is_stored_by_name_never_expanded() -> None:
    pipeline = _pipeline(conversation_preset="snappy", turn_handling={"user_turn_limit": {"max_words": 40}})

    dumped = pipeline.model_dump(mode="json")

    assert dumped["conversation_preset"] == "snappy"
    assert dumped["turn_handling"] == {"user_turn_limit": {"max_words": 40}}


def test_conversation_preset_rejects_an_unknown_name() -> None:
    with pytest.raises(ValidationError):
        _pipeline(conversation_preset="sleepy")


@pytest.mark.parametrize(
    ("settings", "valid"),
    [
        ({}, True),
        ({"mode": "hosted"}, True),
        ({"mode": "local", "unlikely_threshold": 0.2}, True),
        ({"unlikely_threshold": 0.0}, True),
        ({"unlikely_threshold": 1.0}, True),
        ({"unlikely_threshold": 1.5}, False),
        ({"unlikely_threshold": -0.1}, False),
        ({"mode": "cloud"}, False),
    ],
)
def test_turn_detector_settings_validation(settings: dict[str, Any], valid: bool) -> None:
    if valid:
        assert _pipeline(turn_detector=settings).turn_detector == TurnDetectorSettings.model_validate(
            settings
        )
    else:
        with pytest.raises(ValidationError):
            _pipeline(turn_detector=settings)


@pytest.mark.parametrize("sound", ["none", *AMBIENT_SOUNDS, "asset:clip_01", "asset:a-b"])
def test_ambient_sound_accepts_none_builtin_clips_and_assets(sound: str) -> None:
    assert VoiceConfig(ambient_sound=sound).ambient_sound == sound


@pytest.mark.parametrize("sound", ["", "office", "OFFICE_AMBIENCE", "asset:", "asset:../x", "https://x"])
def test_ambient_sound_rejects_anything_else(sound: str) -> None:
    with pytest.raises(ValidationError):
        VoiceConfig(ambient_sound=sound)


def test_agent_config_carries_the_new_fields_through_json() -> None:
    config = AgentConfig.model_validate(
        {
            "instructions": "Help.",
            "pipeline": {
                "conversation_preset": "telephony",
                "turn_detector": {"mode": "local", "unlikely_threshold": 0.3},
            },
            "voice": {"ambient_sound": "office_ambience"},
        }
    )

    again = AgentConfig.model_validate_json(config.model_dump_json())

    assert again == config
    assert again.pipeline.turn_detector == TurnDetectorSettings(mode="local", unlikely_threshold=0.3)
