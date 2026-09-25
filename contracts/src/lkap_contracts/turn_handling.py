"""Conversation tuning: typed turn handling, presets, detector settings, sounds (V5-07).

``PipelineConfig.turn_handling`` reaches ``AgentSession(turn_handling=...)`` in the
worker. livekit-agents 1.8.3 types it as the ``TurnHandlingOptions`` TypedDict
(``livekit/agents/voice/turn.py:260-286``); the models here mirror that shape so the
console and the MCP tools can show real fields, and every model keeps
``extra="allow"`` so a key a newer SDK adds still passes through untouched.

Two rules keep stored configs byte-for-byte stable:

* Every field is optional and serialises **only when it was set** (plus any extra
  key), so validating a stored dict and dumping it gives back the same dict. Unset
  keys fall back to the SDK's own defaults at session start.
* ``PipelineConfig.turn_handling`` is ``TurnHandlingOptions | dict``: a stored dict
  whose typed keys do not validate is kept as it is (the api warns), never refused.

The four named presets are pinned dicts (:data:`CONVERSATION_PRESETS`). A preset is
never stored expanded: :func:`resolve_turn_handling` expands it when a session is
built, and ``custom`` returns the stored dict unchanged. The ``balanced`` values are
the SDK defaults (``turn.py:135-140`` endpointing ``fixed``/0.5/3.0, ``turn.py:190-198``
interruption ``min_duration=0.5``, ``min_words=0``, ``false_interruption_timeout=2.0``,
``resume_false_interruption=True``), written out so the console can show them.
"""

from __future__ import annotations

import copy
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, SerializerFunctionWrapHandler, model_serializer

__all__ = [
    "AMBIENT_SOUNDS",
    "AMBIENT_SOUND_PATTERN",
    "CONVERSATION_PRESETS",
    "PRESET_KEYS",
    "REALTIME_IGNORED_KEYS",
    "ConversationPreset",
    "EndpointingOptions",
    "InterruptionOptions",
    "PreemptiveGenerationOptions",
    "TurnDetectorSettings",
    "TurnHandlingOptions",
    "UserTurnLimitOptions",
    "resolve_turn_handling",
    "turn_handling_dict",
]

#: ``PipelineConfig.conversation_preset``. ``custom`` means "use ``turn_handling`` as stored".
ConversationPreset = Literal["patient", "balanced", "snappy", "telephony", "custom"]


class _SdkOptions(BaseModel):
    """Base for the SDK mirrors: unknown keys pass through, only set keys serialise."""

    model_config = ConfigDict(extra="allow")

    @model_serializer(mode="wrap")
    def _only_set_keys(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        keep = set(self.model_fields_set) | set(self.model_extra or {})
        return {key: value for key, value in data.items() if key in keep}


class EndpointingOptions(_SdkOptions):
    """When the caller's turn counts as finished (SDK ``EndpointingOptions``, ``turn.py:113``)."""

    mode: Literal["fixed", "dynamic"] | None = None
    """``fixed`` waits the delays below; ``dynamic`` adapts them to the caller. SDK default ``fixed``."""
    min_delay: float | None = Field(None, ge=0)
    """Seconds of silence before the turn may end. SDK default 0.5."""
    max_delay: float | None = Field(None, ge=0)
    """Longest wait, in seconds, when the detector thinks the caller is not done. SDK default 3.0."""
    alpha: float | None = Field(None, ge=0, le=1)
    """Smoothing for ``dynamic`` mode. SDK default 0.9."""


class InterruptionOptions(_SdkOptions):
    """How the caller interrupts the agent (SDK ``InterruptionOptions``, ``turn.py:150``)."""

    enabled: bool | None = None
    """Whether the caller can interrupt. The worker fills it from ``voice.allow_interruptions``."""
    mode: Literal["adaptive", "vad"] | None = None
    """``adaptive`` tells real interruptions from back-channel sounds; ``vad`` reacts to any speech."""
    min_duration: float | None = Field(None, ge=0)
    """Seconds of speech that count as an interruption. SDK default 0.5."""
    min_words: int | None = Field(None, ge=0)
    """Words needed to interrupt (only with a speech-to-text model). SDK default 0."""
    false_interruption_timeout: float | None = Field(None, ge=0)
    """Seconds of silence after which an interruption was a false alarm; ``null`` disables. SDK 2.0."""
    resume_false_interruption: bool | None = None
    """Resume speaking after a false interruption. SDK default true."""


class PreemptiveGenerationOptions(_SdkOptions):
    """Start the reply before the caller's turn is confirmed (SDK ``turn.py:201``)."""

    enabled: bool | None = None
    """SDK default true; the worker turns it off with knowledge auto-inject unless set here."""
    preemptive_tts: bool | None = None
    """Also start speech synthesis early. SDK default false."""
    max_speech_duration: float | None = Field(None, ge=0)
    """Skip it after this many seconds of caller speech. SDK default 10.0."""
    max_retries: int | None = Field(None, ge=0)
    """Attempts per caller turn. SDK default 3."""


class UserTurnLimitOptions(_SdkOptions):
    """Stop a caller who talks too long without a reply (SDK ``turn.py:231``; off by default)."""

    max_words: int | None = Field(None, ge=1)
    max_duration: float | None = Field(None, gt=0)


class TurnHandlingOptions(_SdkOptions):
    """``AgentSession(turn_handling=...)`` as the console edits it (SDK ``turn.py:260``)."""

    turn_detection: Literal["stt", "vad", "realtime_llm", "manual"] | None = None
    """Set by the platform (the api refuses it here); kept so a stored value still validates."""
    endpointing: EndpointingOptions | None = None
    interruption: InterruptionOptions | None = None
    preemptive_generation: PreemptiveGenerationOptions | None = None
    user_turn_limit: UserTurnLimitOptions | None = None


class TurnDetectorSettings(BaseModel):
    """``PipelineConfig.turn_detector``: the end-of-turn model's placement and sensitivity.

    Applies to the LiveKit turn detector (``inference.TurnDetector``, 1.8.3
    ``inference/eot/detector.py:35-47``) and to the local plugin model, on top of the
    connection's ``turn_detector_mode``: a connection without hosted Inference always
    runs the local model, whatever ``mode`` says.
    """

    mode: Literal["hosted", "local"] | None = None
    """``hosted`` lets the SDK use the hosted model where the connection has it; ``local``
    always runs the small model on the worker. Unset = the connection decides (as before)."""
    unlikely_threshold: float | None = Field(None, ge=0, le=1)
    """End-of-turn probability below which the caller is assumed to keep talking. Higher =
    the agent waits more often. Unset = the model's calibrated per-language default."""


#: The pinned value of each named preset (the ``custom`` preset has none).
CONVERSATION_PRESETS: Final[dict[str, dict[str, Any]]] = {
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

#: Top-level ``turn_handling`` keys any named preset sets.
PRESET_KEYS: Final[frozenset[str]] = frozenset(
    key for preset in CONVERSATION_PRESETS.values() for key in preset
)

#: Keys a ``realtime`` pipeline ignores: the model detects turns and interruptions itself
#: (1.8.3 ``agent_activity.py:2319`` skips audio interruptions while the realtime model
#: owns turn detection) and preemptive generation runs only for a text LLM
#: (``agent_activity.py:2574``, ``isinstance(self.llm, llm.LLM)``).
REALTIME_IGNORED_KEYS: Final[tuple[str, ...]] = ("endpointing", "interruption", "preemptive_generation")

#: ``VoiceConfig.ambient_sound`` built-in names: every ``BuiltinAudioClip`` of 1.8.3
#: (``voice/background_audio.py:29-36``), lower-cased.
AMBIENT_SOUNDS: Final[tuple[str, ...]] = (
    "city_ambience",
    "forest_ambience",
    "office_ambience",
    "crowded_room",
    "keyboard_typing",
    "keyboard_typing2",
    "hold_music",
)

#: ``none``, a built-in clip, or ``asset:<id>`` (an uploaded clip; played from a later package).
AMBIENT_SOUND_PATTERN: Final[str] = r"^(none|" + "|".join(AMBIENT_SOUNDS) + r"|asset:[A-Za-z0-9_-]+)$"


def turn_handling_dict(value: TurnHandlingOptions | dict[str, Any]) -> dict[str, Any]:
    """Return ``PipelineConfig.turn_handling`` as a plain dict of the keys that were set.

    ``PipelineConfig`` already stores a dict; this also accepts a model instance (a
    value assigned after construction) so callers never branch on the type.

    Args:
        value: The stored ``turn_handling``.

    Returns:
        A new dict; the input is not modified.
    """
    if isinstance(value, TurnHandlingOptions):
        return value.model_dump()
    return copy.deepcopy(value)


def resolve_turn_handling(
    preset: ConversationPreset, stored: TurnHandlingOptions | dict[str, Any]
) -> dict[str, Any]:
    """Expand a conversation preset into the ``turn_handling`` a session runs with.

    ``custom`` returns the stored dict unchanged. A named preset returns its pinned
    dict and ignores the stored one for the keys it sets (``endpointing``,
    ``interruption``, ``preemptive_generation``); any other stored key (for
    example ``user_turn_limit``, or ``turn_detection``, which the api refuses) is
    kept, so the console can switch presets without losing settings the preset
    does not cover. The worker then layers ``interruption.enabled`` and the turn
    detector on top.

    Args:
        preset: ``PipelineConfig.conversation_preset``.
        stored: ``PipelineConfig.turn_handling`` as saved.

    Returns:
        A new dict; neither argument is modified.
    """
    current = turn_handling_dict(stored)
    if preset == "custom":
        return current
    expanded = {key: value for key, value in current.items() if key not in PRESET_KEYS}
    expanded.update(copy.deepcopy(CONVERSATION_PRESETS[preset]))
    return expanded
