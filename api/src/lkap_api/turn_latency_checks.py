"""Validator findings for low-latency turn taking (V6-34). Warnings and tips only.

Each finding says what the session will actually do (``lkap_contracts.agent_config.
transcriber_ends_turns`` is the rule the worker follows):

* a VAD ``min_silence_duration`` below 0.25 s while the LiveKit turn detector runs: livekit-agents
  1.8.3 refuses it (``_check_vad_silence_requirement``), so the worker uses 0.25 s;
* ``turn_detector.mode: "stt"`` with a transcriber that cannot end turns: the detector decides;
* Deepgram Flux options on a LiveKit Inference model that is not Flux: ignored;
* a tip when a transcriber that can end turns is left waiting: Deepgram Flux direct on a preset that
  still waits half a second after Flux has decided (``docs/research-v6/low-latency-stack.md`` F1),
  or LiveKit Inference Flux not yet asked to decide (the ``fast`` preset opts it in).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from lkap_contracts.agent_config import PipelineConfig, transcriber_ends_turns
from lkap_contracts.common import Issue, ProviderRef
from lkap_contracts.providers import (
    FLUX_OPTION_FIELDS,
    VAD_MIN_SILENCE_WITH_TURN_DETECTOR,
    ProviderSpec,
    get,
    stt_end_of_turn,
    validate_model_id,
)
from lkap_contracts.turn_handling import turn_handling_dict

if TYPE_CHECKING:
    from lkap_api.config_service import ValidationContext

__all__ = [
    "FAST_PRESET_FOR_FLUX_TIP",
    "FAST_PRESET_FOR_INFERENCE_FLUX_TIP",
    "FLUX_OPTION_IGNORED_MESSAGE",
    "STT_MODE_UNSUPPORTED_MESSAGE",
    "VAD_SILENCE_FLOOR_MESSAGE",
    "turn_latency_issues",
]

#: The VAD entries whose ``min_silence_duration`` the worker passes on (``silero-vad``, ``inference-vad``).
_VAD_IDS: Final[frozenset[str]] = frozenset({"silero-vad", "inference-vad"})

#: LiveKit Inference's transcriber, whose Flux options apply to Flux models only.
_INFERENCE_STT_ID: Final[str] = "livekit-inference-stt"

#: The SDK's ``min_delay`` when nothing sets it (livekit-agents 1.8.3 ``voice/turn.py`` legacy defaults,
#: which a plain ``turn_detection="stt"`` session gets).
_SDK_MIN_DELAY: Final[float] = 0.5

#: Presets the Flux tip speaks about: ``patient`` and ``telephony`` wait on purpose.
_TIP_PRESETS: Final[frozenset[str]] = frozenset({"balanced", "snappy"})

VAD_SILENCE_FLOOR_MESSAGE: Final[str] = (
    "The turn detector needs at least {floor} seconds of silence, so the agent uses {floor} instead of "
    "{value}. A shorter silence only helps when the speech-to-text model decides when the caller has "
    "finished (Deepgram Flux)"
)
STT_MODE_UNSUPPORTED_MESSAGE: Final[str] = (
    "'{name}' cannot decide by itself when the caller has finished, so the turn detector decides, as if "
    "this were not set. Pick a Deepgram Flux model to let speech-to-text decide"
)
FLUX_OPTION_IGNORED_MESSAGE: Final[str] = (
    "'{label}' is used only by Deepgram Flux models. With '{model}' it is ignored"
)
FAST_PRESET_FOR_FLUX_TIP: Final[str] = (
    "Tip: Deepgram Flux already decides when the caller has finished, and this agent then waits another "
    "{delay} seconds before it replies. The Fast conversation preset waits 0.1 seconds and starts "
    "preparing the reply while the caller finishes"
)
FAST_PRESET_FOR_INFERENCE_FLUX_TIP: Final[str] = (
    "Tip: '{name}' can decide by itself when the caller has finished, which is quicker than the turn "
    "detector. Choose the Fast conversation preset, or set the turn detector to let speech-to-text decide"
)


def _spec(provider_id: str) -> ProviderSpec | None:
    try:
        return get(provider_id)
    except KeyError:
        return None


def _stt_name(ref: ProviderRef) -> str:
    """A plain name for the transcriber: its model id, or the entry's label for an id that fails the id rule.

    A model id that fails the rule is never quoted (R-V4-21).
    """
    spec = _spec(ref.provider_id)
    model = ref.model or (spec.default_model if spec is not None else None)
    if model and validate_model_id(model) is None:
        return model
    return spec.label if spec is not None else "The speech-to-text model"


def _vad_silence_issues(pipeline: PipelineConfig) -> list[Issue]:
    ref = pipeline.vad
    if ref is None or ref.provider_id not in _VAD_IDS or pipeline.mode == "realtime":
        return []
    value = ref.fields.get("min_silence_duration")
    if isinstance(value, bool) or not isinstance(value, int | float):
        return []
    if value >= VAD_MIN_SILENCE_WITH_TURN_DETECTOR or transcriber_ends_turns(pipeline):
        return []
    return [
        Issue(
            path="pipeline.vad.fields.min_silence_duration",
            message=VAD_SILENCE_FLOOR_MESSAGE.format(floor=VAD_MIN_SILENCE_WITH_TURN_DETECTOR, value=value),
            severity="warning",
        )
    ]


def _stt_mode_issues(pipeline: PipelineConfig) -> list[Issue]:
    settings = pipeline.turn_detector
    if settings is None or settings.mode != "stt" or pipeline.mode == "realtime":
        return []
    if transcriber_ends_turns(pipeline):
        return []
    stt = pipeline.stt
    name = _stt_name(stt) if stt is not None and pipeline.mode == "cascaded" else "This pipeline"
    if pipeline.turn_detection is not None:
        message = "A turn detector is chosen for this agent, so it decides when the caller has finished"
    else:
        message = STT_MODE_UNSUPPORTED_MESSAGE.format(name=name)
    return [Issue(path="pipeline.turn_detector.mode", message=message, severity="warning")]


def _flux_option_issues(pipeline: PipelineConfig) -> list[Issue]:
    ref = pipeline.stt
    if ref is None or ref.provider_id != _INFERENCE_STT_ID or pipeline.mode != "cascaded":
        return []
    set_fields = [name for name in FLUX_OPTION_FIELDS if ref.fields.get(name) not in (None, "")]
    if not set_fields or stt_end_of_turn(ref.provider_id, ref.model) is not None:
        return []
    spec = _spec(ref.provider_id)
    labels = {field.name: field.label for field in spec.fields} if spec is not None else {}
    model = _stt_name(ref)
    return [
        Issue(
            path=f"pipeline.stt.fields.{name}",
            message=FLUX_OPTION_IGNORED_MESSAGE.format(label=labels.get(name, name), model=model),
            severity="warning",
        )
        for name in set_fields
    ]


def _fast_preset_tips(pipeline: PipelineConfig) -> list[Issue]:
    ref = pipeline.stt
    if ref is None or pipeline.mode != "cascaded" or pipeline.turn_detection is not None:
        return []
    ability = stt_end_of_turn(ref.provider_id, ref.model)
    preset = pipeline.conversation_preset
    if ability == "model" and not transcriber_ends_turns(pipeline):
        message = FAST_PRESET_FOR_INFERENCE_FLUX_TIP.format(name=_stt_name(ref))
        return [Issue(path="pipeline.conversation_preset", message=message, severity="warning")]
    if ability != "entry":
        return []
    if preset in _TIP_PRESETS:
        delay = {"balanced": 0.5, "snappy": 0.3}[preset]
    elif preset == "custom":
        endpointing = turn_handling_dict(pipeline.turn_handling).get("endpointing")
        stored = endpointing.get("min_delay") if isinstance(endpointing, dict) else None
        if stored is not None:
            return []  # the builder chose a wait; say nothing
        delay = _SDK_MIN_DELAY
    else:
        return []
    message = FAST_PRESET_FOR_FLUX_TIP.format(delay=delay)
    return [Issue(path="pipeline.conversation_preset", message=message, severity="warning")]


def turn_latency_issues(ctx: ValidationContext) -> list[Issue]:
    """The V6-34 turn-taking findings for an agent's pipeline (warnings and tips only).

    Args:
        ctx: The validation context.

    Returns:
        The findings, each at the field it is about.
    """
    pipeline = ctx.config.pipeline
    return [
        *_vad_silence_issues(pipeline),
        *_stt_mode_issues(pipeline),
        *_flux_option_issues(pipeline),
        *_fast_preset_tips(pipeline),
    ]
