"""V6-34 validator findings: VAD silence floor, speech-to-text turn ends, Flux options, the fast tip."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import inference_config
from lkap_contracts.agent_config import AgentConfig, KnowledgeConfig, PipelineConfig
from lkap_contracts.common import Issue

from lkap_api.config_service import ValidationContext, validate, validate_agent_config
from lkap_api.turn_latency_checks import (
    FAST_PRESET_FOR_FLUX_TIP,
    turn_latency_issues,
)

INFERENCE_FLUX = {"provider_id": "livekit-inference-stt", "model": "deepgram/flux-general-en"}
INFERENCE_NOVA = {"provider_id": "livekit-inference-stt", "model": "deepgram/nova-3"}
DIRECT_FLUX = {"provider_id": "deepgram-flux-stt", "model": "flux-general-en"}


def _config(**pipeline: Any) -> AgentConfig:
    base = inference_config().pipeline.model_dump(mode="json")
    return inference_config(pipeline=PipelineConfig.model_validate({**base, **pipeline}))


def _issues(config: AgentConfig, path: str | None = None) -> list[Issue]:
    found = turn_latency_issues(ValidationContext(config=config))
    return [issue for issue in found if path is None or issue.path == path]


# ------------------------------------------------------------------ VAD silence
@pytest.mark.parametrize("provider_id", ["silero-vad", "inference-vad"])
def test_vad_silence_below_the_floor_warns_with_the_turn_detector(provider_id: str) -> None:
    config = _config(
        stt=INFERENCE_NOVA, vad={"provider_id": provider_id, "fields": {"min_silence_duration": 0.1}}
    )

    (issue,) = _issues(config, "pipeline.vad.fields.min_silence_duration")

    assert issue.severity == "warning"
    assert "0.25" in issue.message and "0.1" in issue.message


@pytest.mark.parametrize(
    "pipeline",
    [
        {"stt": DIRECT_FLUX},
        {"stt": INFERENCE_FLUX, "conversation_preset": "fast"},
        {
            "stt": INFERENCE_NOVA,
            "vad": {"provider_id": "silero-vad", "fields": {"min_silence_duration": 0.3}},
        },
    ],
)
def test_vad_silence_is_fine_when_the_transcriber_ends_turns_or_it_is_high_enough(
    pipeline: dict[str, Any],
) -> None:
    pipeline.setdefault("vad", {"provider_id": "silero-vad", "fields": {"min_silence_duration": 0.1}})

    assert _issues(_config(**pipeline), "pipeline.vad.fields.min_silence_duration") == []


# ------------------------------------------------------------------ turn_detector.mode "stt"
def test_stt_mode_on_a_model_that_cannot_end_turns_warns() -> None:
    (issue,) = _issues(
        _config(stt=INFERENCE_NOVA, turn_detector={"mode": "stt"}), "pipeline.turn_detector.mode"
    )

    assert "deepgram/nova-3" in issue.message and "turn detector decides" in issue.message


@pytest.mark.parametrize("stt", [INFERENCE_FLUX, DIRECT_FLUX])
def test_stt_mode_on_flux_is_quiet(stt: dict[str, Any]) -> None:
    config = _config(stt=stt, turn_detector={"mode": "stt"}, conversation_preset="fast")

    assert _issues(config) == []


def test_stt_mode_with_an_explicit_detector_slot_says_the_detector_decides() -> None:
    config = _config(
        stt=INFERENCE_FLUX,
        turn_detector={"mode": "stt"},
        turn_detection={"provider_id": "inference-turn-detector"},
    )

    (issue,) = _issues(config, "pipeline.turn_detector.mode")
    assert "A turn detector is chosen" in issue.message


# ------------------------------------------------------------------ Flux options on Inference
def test_flux_options_on_a_non_flux_inference_model_warn_per_field() -> None:
    stt = {**INFERENCE_NOVA, "fields": {"eot_threshold": 0.8, "eager_eot_threshold": 0.4}}

    issues = _issues(_config(stt=stt))

    paths = sorted(issue.path for issue in issues if issue.path.startswith("pipeline.stt.fields."))
    assert paths == ["pipeline.stt.fields.eager_eot_threshold", "pipeline.stt.fields.eot_threshold"]
    assert all("only by Deepgram Flux" in issue.message for issue in issues if issue.path in paths)


def test_flux_options_on_inference_flux_are_quiet() -> None:
    stt = {
        **INFERENCE_FLUX,
        "fields": {"eot_threshold": 0.8, "eager_eot_threshold": 0.4, "eot_timeout_ms": 3000},
    }

    assert _issues(_config(stt=stt, conversation_preset="fast")) == []


# ------------------------------------------------------------------ the fast preset tip
@pytest.mark.parametrize(
    ("pipeline", "delay"),
    [
        ({"conversation_preset": "balanced"}, 0.5),
        ({"conversation_preset": "snappy"}, 0.3),
        ({"conversation_preset": "custom"}, 0.5),
    ],
)
def test_direct_flux_waiting_after_its_own_end_of_turn_gets_the_fast_tip(
    pipeline: dict[str, Any], delay: float
) -> None:
    (issue,) = _issues(_config(stt=DIRECT_FLUX, **pipeline), "pipeline.conversation_preset")

    assert issue.message == FAST_PRESET_FOR_FLUX_TIP.format(delay=delay)
    assert issue.message.startswith("Tip:")


@pytest.mark.parametrize(
    "pipeline",
    [
        {"stt": DIRECT_FLUX, "conversation_preset": "fast"},
        {"stt": DIRECT_FLUX, "conversation_preset": "patient"},
        {"stt": DIRECT_FLUX, "conversation_preset": "telephony"},
        {"stt": DIRECT_FLUX, "turn_handling": {"endpointing": {"min_delay": 0.1}}},
        {"stt": INFERENCE_NOVA, "conversation_preset": "balanced"},
        {"stt": INFERENCE_FLUX, "turn_detector": {"mode": "stt"}},
    ],
)
def test_no_fast_tip_where_it_would_not_help(pipeline: dict[str, Any]) -> None:
    assert _issues(_config(**pipeline), "pipeline.conversation_preset") == []


def test_inference_flux_not_opted_in_gets_the_opt_in_tip() -> None:
    (issue,) = _issues(
        _config(stt=INFERENCE_FLUX, conversation_preset="balanced"), "pipeline.conversation_preset"
    )

    assert "deepgram/flux-general-en" in issue.message
    assert "Fast conversation preset" in issue.message


# ------------------------------------------------------------------ the fast preset through validate()
def test_fast_preset_saves_without_errors_on_every_transcriber() -> None:
    """Only the direct Deepgram entry's missing key is an error here; nothing about the preset is."""
    for stt in (INFERENCE_FLUX, INFERENCE_NOVA, DIRECT_FLUX):
        result = validate(ValidationContext(config=_config(stt=stt, conversation_preset="fast")))
        errors = [issue for issue in result.issues if issue.severity == "error"]
        assert all(issue.path.startswith("pipeline.stt") for issue in errors), (stt, errors)
        assert not [issue for issue in result.issues if issue.path == "pipeline.conversation_preset"], stt


def test_fast_preset_with_auto_inject_gets_the_discarded_reply_tip() -> None:
    config = _config(conversation_preset="fast").model_copy(
        update={"knowledge": KnowledgeConfig(kb_ids=["kb-1"], auto_inject=True)}
    )

    (issue,) = [
        i
        for i in validate_agent_config(config, credential_providers={}).issues
        if i.path == "knowledge.auto_inject"
    ]
    assert "discards the preemptive reply" in issue.message


def test_a_stored_inference_agent_gets_no_new_finding() -> None:
    """The default Inference pipeline (Nova 3) on `custom`: nothing from V6-34."""
    assert _issues(inference_config()) == []
