"""Assemble an `AgentSession` and its `RoomOptions` from a resolved config.

This is the one place that knows how `AgentConfig` maps onto the LiveKit
runtime (docs/ARCHITECTURE.md §4/§6/§8, ARCHITECTURE-V2 D-V2-10). Verified
against livekit-agents 1.8.3:

* `turn_handling` is a `TurnHandlingOptions` TypedDict; `allow_interruptions`
  lives at `turn_handling["interruption"]["enabled"]`.
* Preemptive generation is **on by default** (`voice/turn.py`,
  `_PREEMPTIVE_GENERATION_DEFAULTS["enabled"] = True`) and is switched off with
  `turn_handling["preemptive_generation"] = {"enabled": False}` (the
  `AgentSession(preemptive_generation=...)` kwarg is deprecated in 1.8.2). The
  SDK discards the speculative reply whenever `on_user_turn_completed` changes
  the chat context, which knowledge auto-inject does on every turn with a hit,
  so an agent with auto-inject on and a knowledge base attached runs without
  it unless its config sets `preemptive_generation.enabled` explicitly
  (research-v4 knowledge-and-memory P0-0). Agent-level `turn_handling` keys
  override the session's (`AgentActivity.preemptive_generation_opts`), and
  `Agent` stores its `turn_handling` as passed; the flow runtime sets only
  `interruption` there, so the session-level setting holds for flow nodes.
* `vad=NOT_GIVEN` makes `AgentSession` construct `inference.VAD` itself, which
  needs network. The builder therefore always passes `vad` explicitly: the
  `vad` slot's plugin when one is configured, else the prewarmed Silero
  instance from `proc.userdata`, else `None`.
* Three pipeline modes (CONTRACTS-V2 §4.3):

  - ``cascaded``: STT → LLM → TTS, VAD + turn detector (the `turn_detection`
    slot, else `inference.TurnDetector`).
  - ``realtime``: the realtime model hears and speaks; it detects turns
    server-side, so it gets **no** VAD and **no** turn detector (D-W2-9o).
  - ``half_cascade``: the realtime model hears and answers in *text* (the
    factory builds it with its text modality) and the `tts` slot speaks. It
    gets the VAD, and a turn detector when the model can hand turn-taking to
    the client (`capabilities.can_disable_turn_detection`, e.g. OpenAI
    Realtime without an explicit `turn_detection`): `AgentActivity.
    _resolve_rt_turn_detection_enabled` then turns the model's server-side
    detection off. Gemini Live sets `can_disable_turn_detection=False`
    (livekit-plugins-google 1.8.2, `realtime_api.py`), so it keeps its own
    server-side turns and the builder passes no detector (the SDK would
    ignore it and warn on every session).

* `RoomOptions.video_input` defaults to `False`; only realtime models (both
  realtime modes) receive frames through it (cascaded vision is injected per
  turn by `PlatformAgent`).
* `noise_cancellation` goes to `AudioInputOptions.noise_cancellation`, which
  accepts an `rtc.FrameProcessor` such as Krisp's.
* `close_on_disconnect` is always `False`: the worker's reconnect grace
  (`main._ReconnectGrace`, REVIEW-FINAL F-33) decides when a departed caller
  ends the job.
* A ``channel="text"`` session (CONTRACTS-V2 D-V2-15 scaffolding; rewind is
  V2-18) runs with audio input and output off and typed input on; the audio
  slots are dropped before anything is constructed.

Conversation tuning (V5-07, docs/v5/PLAN-V5.md):

* ``pipeline.conversation_preset`` is expanded here, at build time, by
  `lkap_contracts.turn_handling.resolve_turn_handling` (``custom`` = the stored
  `turn_handling` unchanged); the stored config never holds the expanded dict.
* ``pipeline.turn_detector`` becomes constructor kwargs of the LiveKit turn
  detector in :func:`prepare_resolved` (a synthesized `inference-turn-detector`
  slot when none is configured), so `unlikely_threshold` reaches
  `inference.TurnDetector(...)` without a second construction path.
* The ``telephony`` preset on a phone call swaps the noise filter for its
  telephony variant (the registry's ``telephony_variant``: `BVCTelephony()`,
  `krisp.voice_isolation_telephony()`), and turns the LiveKit Cloud filter on
  when the agent has none and one is offered (D-V5-30). A web session keeps
  the configured filter as it is.
* ``voice.ambient_sound`` plays through the same `BackgroundAudioPlayer` as the
  thinking sound (:func:`start_background_audio`), only with audio output.

Languages and captions (V5-31, docs/v5/PLAN-V5.md):

* ``voice.auto_detect`` builds the STT slot with the registry's detection value
  (`lkap_agent.languages.apply_stt_detection`, in :func:`prepare_resolved`); the
  stored config keeps its own `language` field.
* A panel with a ``captions`` block gets a :class:`~lkap_agent.ui.channel.CaptionsTextOutput`
  after RoomIO's transcription output (`RoomOptions.text_output =
  TextOutputOptions(next_in_chain=tap)`, livekit-agents 1.8.3 `room_io/types.py`), so
  the agent's captions are timed like the browser's transcription. The agent binds
  it to the session's caption stream (`caption_tap_for`). No tap on the text channel.

Caller memory (V5-40, docs/v5/PLAN-V5.md):

* :func:`apply_memory_recall` asks the api once, before the session is built, what
  the memory knows about the caller (``POST /internal/v1/memory/recall``; the api
  resolves the caller and the pseudonymous id, the worker never sees either). It is
  a no-op, with no call at all, while ``memory.enabled`` is off.
* The memories are **appended** to ``AgentConfig.instructions`` (like the languages
  rule and the compliance notes, so a prompt agent and every flow node compose
  them, and the instructions' own prefix stays byte-identical for provider prompt
  caching) as a fixed heading plus the memories inside the ``<untrusted
  source="memory">`` fence (R-V5-15), bounded by ``memory.max_recall_tokens``
  (about four characters a token) and cut at whole memories.
* ``memory.consent_line``, when set and the session will be remembered, becomes an
  instruction to say it early in the call.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Final, Protocol, cast

from livekit.agents import NOT_GIVEN, AgentSession, TurnHandlingOptions
from livekit.agents.voice.room_io import AudioInputOptions, RoomOptions, TextOutputOptions
from livekit.agents.voice.tool_executor import AsyncToolOptions
from lkap_contracts.agent_config import (
    AgentConfig,
    AvatarOptions,
    PipelineMode,
    ResolvedAgentConfig,
    ResolvedCompliance,
    ResolvedProvider,
    ThinkingSound,
    effective_languages,
    transcriber_ends_turns,
)
from lkap_contracts.api_models import MemoryRecallIn, MemoryRecallOut
from lkap_contracts.providers import (
    STICKY_ROUTING_FIELD,
    VAD_MIN_SILENCE_FIELD,
    VAD_MIN_SILENCE_WITH_TURN_DETECTOR,
    ModelCapabilities,
    ProviderSpec,
    by_kind,
    stt_end_of_turn,
)
from lkap_contracts.providers import get as get_spec
from lkap_contracts.turn_handling import resolve_turn_handling

from lkap_agent.languages import apply_stt_detection, language_rule
from lkap_agent.logging import get_logger
from lkap_agent.providers.factory import BuiltProviders, telephony_noise_cancellation, turn_detector_kwargs
from lkap_agent.telephony import is_sip_channel
from lkap_agent.tools.untrusted import fence, fence_overhead
from lkap_agent.ui.channel import CaptionsTextOutput, register_caption_tap

__all__ = [
    "AMBIENT_SOUND_VOLUME",
    "ASYNC_TOOL_OPTIONS",
    "AVATAR_OPTION_KWARGS",
    "SessionBuilder",
    "DISCLOSURE_PLACEHOLDER",
    "MEMORY_HEADING",
    "MEMORY_SOURCE",
    "MemoryRecallApi",
    "STT_TURN_DETECTION",
    "SessionPlan",
    "apply_compliance",
    "stt_decides_turns",
    "apply_memory_recall",
    "auto_inject_active",
    "build_turn_handling",
    "has_captions_block",
    "factory_view",
    "is_text_channel",
    "llm_capabilities_of",
    "memory_block",
    "prepare_resolved",
    "recording_needs_consent",
    "start_background_audio",
    "start_thinking_sound",
    "with_language_rule",
    "with_memory",
]

logger = get_logger(__name__)

#: `TurnHandlingOptions` keys a config may set; anything else is dropped so a
#: stale row cannot raise a TypeError inside `AgentSession.__init__`.
_TURN_HANDLING_KEYS: frozenset[str] = frozenset(
    {"turn_detection", "endpointing", "interruption", "preemptive_generation", "user_turn_limit"}
)

#: The `TurnDetectionMode` that lets the transcriber end the caller's turn (livekit-agents 1.8.3
#: `voice/turn.py:95`; `audio_recognition.py` commits the turn on the STT's end-of-speech event).
STT_TURN_DETECTION: Final[str] = "stt"

#: Slots that only make sense with audio; a text-channel session drops them.
_AUDIO_SLOTS: Final[tuple[str, ...]] = ("stt", "tts", "vad", "turn_detection", "noise_cancellation", "avatar")

#: `AvatarOptions` field → constructor kwarg, for every avatar plugin whose
#: 1.8.2 constructor takes one (AST-verified in `tests/fixtures/plugin_signatures.json`;
#: a unit test re-checks every entry). `participant_name` maps to
#: `avatar_participant_name`, which every registered avatar plugin accepts.
AVATAR_OPTION_KWARGS: Final[dict[str, dict[str, str]]] = {
    "liveavatar-avatar": {"video_quality": "video_quality"},
    "lemonslice-avatar": {"idle_timeout_s": "idle_timeout"},
    "spatius-avatar": {"idle_timeout_s": "idle_timeout_seconds"},
    "protoface-avatar": {"max_duration_s": "max_duration_seconds"},
    "runway-avatar": {"max_duration_s": "max_duration"},
}

_AVATAR_PARTICIPANT_NAME_KWARG: Final[str] = "avatar_participant_name"

#: The Inference turn detector whose `version` the connection flags may force.
_INFERENCE_TURN_DETECTOR_ID: Final[str] = "inference-turn-detector"

#: The local plugin detector (`MultilingualModel(unlikely_threshold=...)`, the only kwarg it
#: takes: `tests/fixtures/plugin_signatures.json`).
_PLUGIN_TURN_DETECTOR_ID: Final[str] = "turn-detector-plugin"

#: The OpenRouter language-model entry whose `sticky_routing` field :func:`_apply_sticky_routing` reads.
_OPENROUTER_LLM_ID: Final[str] = "openrouter-llm"

# Voice-safe prompt templates for the SDK's async-tool executor (docs/v4/BACKGROUND-TOOLS.md
# D-V4-35). livekit-agents 1.8.2 (`voice/tool_executor.py`) renders each with `str.format`
# and a fixed argument set: `update` gets {function_name, call_id, message}, the duplicate
# templates {function_name, fnc_calls_json, fnc_calls_text}, the reply templates {call_ids};
# any other brace would raise at runtime. The SDK defaults speak tool names and call ids;
# these instruct the model instead, so it paraphrases and never reads an id aloud.
_UPDATE_TEMPLATE: Final[str] = (
    "Background work for `{function_name}` reports: {message}\n"
    "Tell the user briefly, in one "
    "clause, and continue; do not invent anything the message does not say."
)
_REPLY_AT_TAIL_TEMPLATE: Final[str] = (
    "A background task just finished. Say what it found in one or two sentences, naturally, then continue."
)
_REPLY_MAYBE_COVERED_TEMPLATE: Final[str] = (
    "A background task just finished. If you already told the user everything it found, answer with "
    "nothing at all. Otherwise say only what you have not said yet, in one or two sentences, naturally."
)
_DUPLICATE_REJECT_TEMPLATE: Final[str] = "That is already being looked up; tell the user it is on its way."
_DUPLICATE_CONFIRM_TEMPLATE: Final[str] = (
    "That is already running; tell the user it is on its way. Call it again with "
    "`lk_agents_confirm_duplicate` set to true only if the user asks for it to run a second time."
)
#: The realtime model voices each update itself (Gemini: `NON_BLOCKING`/`WHEN_IDLE`), so its
#: update asks for the spoken acknowledgement directly.
_REALTIME_UPDATE_TEMPLATE: Final[str] = (
    "Background work for `{function_name}` reports: {message}\n"
    "Say so to the user in one short "
    "clause and carry on; do not invent anything the message does not say."
)


def _async_tool_options(update_template: str) -> AsyncToolOptions:
    return AsyncToolOptions(
        update_template=update_template,
        duplicate_reject_template=_DUPLICATE_REJECT_TEMPLATE,
        duplicate_confirm_template=_DUPLICATE_CONFIRM_TEMPLATE,
        reply_at_tail_template=_REPLY_AT_TAIL_TEMPLATE,
        reply_maybe_covered_template=_REPLY_MAYBE_COVERED_TEMPLATE,
    )


#: `AgentSession(tool_handling={"async_options": ASYNC_TOOL_OPTIONS[mode]})`, one per pipeline mode.
ASYNC_TOOL_OPTIONS: Final[dict[PipelineMode, AsyncToolOptions]] = {
    "cascaded": _async_tool_options(_UPDATE_TEMPLATE),
    "half_cascade": _async_tool_options(_UPDATE_TEMPLATE),
    "realtime": _async_tool_options(_REALTIME_UPDATE_TEMPLATE),
}


def is_text_channel(resolved: ResolvedAgentConfig) -> bool:
    """Whether the session runs on the typed `text` channel (audio off)."""
    return resolved.channel == "text"


def _with_avatar_options(provider: ResolvedProvider, options: AvatarOptions) -> ResolvedProvider:
    """Merge `AvatarOptions` into an avatar's kwargs; explicit provider fields win."""
    kwargs = dict(provider.kwargs)
    kwargs.setdefault(_AVATAR_PARTICIPANT_NAME_KWARG, options.participant_name)
    for option, kwarg in AVATAR_OPTION_KWARGS.get(provider.provider_id, {}).items():
        value = getattr(options, option)
        if value is not None:
            kwargs.setdefault(kwarg, value)
    return provider.model_copy(update={"kwargs": kwargs})


def prepare_resolved(resolved: ResolvedAgentConfig) -> ResolvedAgentConfig:
    """Apply what the worker adds to the api's resolution before anything is built.

    * **Text channel**: the audio-only slots are dropped, so no STT/TTS/VAD/
      avatar vendor connection is ever opened for a typed session.
    * **Avatar options** (`PipelineConfig.avatar_options`) become constructor
      kwargs where the vendor supports them (:data:`AVATAR_OPTION_KWARGS`).
    * **Connection flags**: on a connection whose `turn_detector_mode` is
      `local` (no hosted Inference), an explicitly configured Inference turn
      detector without a `version` is pinned to the local `v1-mini` model
      (ARCHITECTURE-V2 D-V2-4), avoiding a failed hosted attempt first.

    * **Turn detector settings** (`PipelineConfig.turn_detector`, V5-07): see
      :func:`_apply_turn_detector_settings`.
    * **VAD silence floor** (V6-34): see :func:`_apply_vad_silence_floor`.
    * **OpenRouter sticky routing** (V6-34): see :func:`_apply_sticky_routing`.
    * **Telephony noise cancellation** (the `telephony` preset on a phone call,
      V5-07): see :func:`_apply_telephony_noise_cancellation`.
    * **Language detection** (`voice.auto_detect`, V5-31): the STT slot is built
      with its detection value (`languages.apply_stt_detection`).
    * **Languages rule** (V5-31): see :func:`with_language_rule`.

    Args:
        resolved: The config fetched from the api.

    Returns:
        A copy; `resolved` itself is not modified.
    """
    slots = dict(resolved.resolved)
    if is_text_channel(resolved):
        for slot in _AUDIO_SLOTS:
            slots.pop(slot, None)  # type: ignore[call-overload]

    avatar = slots.get("avatar")
    if avatar is not None:
        slots["avatar"] = _with_avatar_options(avatar, resolved.config.pipeline.avatar_options)

    detector = slots.get("turn_detection")
    if (
        detector is not None
        and detector.provider_id == _INFERENCE_TURN_DETECTOR_ID
        and resolved.connection.capabilities.turn_detector_mode == "local"
        and "version" not in detector.kwargs
    ):
        slots["turn_detection"] = detector.model_copy(
            update={"kwargs": {**detector.kwargs, "version": "v1-mini"}}
        )
    _apply_turn_detector_settings(resolved, slots)
    _apply_vad_silence_floor(resolved, slots)
    _apply_sticky_routing(resolved, slots)
    _apply_telephony_noise_cancellation(resolved, slots)
    apply_stt_detection(resolved, slots)
    return resolved.model_copy(update={"resolved": slots, "config": with_language_rule(resolved.config)})


def with_language_rule(config: AgentConfig) -> AgentConfig:
    """`config` with the fixed languages rule appended to its instructions (V5-31).

    Appended to `AgentConfig.instructions` (like the compliance notes) so a
    prompt agent and every flow node compose it; one-language agents without
    detection are returned unchanged.
    """
    rule = language_rule(effective_languages(config.voice), auto_detect=config.voice.auto_detect)
    if not rule:
        return config
    base = config.instructions.rstrip()
    instructions = f"{base}\n\n{rule}" if base else rule
    return config.model_copy(update={"instructions": instructions})


def has_captions_block(resolved: ResolvedAgentConfig) -> bool:
    """Whether the session's panel shows a `captions` block (the agent's config or the effective layout)."""
    blocks = [*resolved.config.panel.blocks, *resolved.panel.blocks]
    return any(spec.type == "captions" for spec in blocks)


def stt_decides_turns(resolved: ResolvedAgentConfig) -> bool:
    """Whether the session's transcriber ends the caller's turns itself (V6-02 D-V6-5, V6-34).

    `lkap_contracts.agent_config.transcriber_ends_turns` on the resolved `stt`
    slot: a cascaded pipeline without a stored `turn_detection` slot whose
    transcriber's entry always ends turns (Deepgram Flux direct, as since V6-02),
    or whose model can and the agent opted in (LiveKit Inference Flux with the
    `fast` preset or `turn_detector.mode: "stt"`). Never on the text channel.
    `SessionBuilder.build` then runs the session with `turn_detection="stt"`.
    """
    stt = resolved.resolved.get("stt")
    if stt is None or is_text_channel(resolved):
        return False
    return transcriber_ends_turns(
        resolved.config.pipeline, stt_provider_id=stt.provider_id, stt_model=stt.model
    )


def _stt_can_end_turns(resolved: ResolvedAgentConfig) -> bool:
    """Whether the resolved transcriber could end turns at all (to explain why it does not)."""
    stt = resolved.resolved.get("stt")
    return stt is not None and stt_end_of_turn(stt.provider_id, stt.model) is not None


def _client_side_turns(resolved: ResolvedAgentConfig) -> bool:
    """Whether the session runs its own turn detection (every audio mode except `realtime`)."""
    return resolved.config.pipeline.mode != "realtime" and not is_text_channel(resolved)


def _livekit_detector_runs(resolved: ResolvedAgentConfig, slots: dict[Any, ResolvedProvider]) -> bool:
    """Whether the session will run a LiveKit turn detector after VAD (V6-34).

    Client-side turns that the transcriber does not end, with either no
    `turn_detection` slot (the worker's default `inference.TurnDetector`) or an
    Inference/plugin detector slot. A realtime model in `half_cascade` may still
    drop it (`SessionBuilder._keeps_server_side_turns`); counting it here only
    errs towards the SDK's VAD floor.
    """
    if not _client_side_turns(resolved) or stt_decides_turns(resolved):
        return False
    detector = slots.get("turn_detection")
    return detector is None or detector.provider_id in (_INFERENCE_TURN_DETECTOR_ID, _PLUGIN_TURN_DETECTOR_ID)


def _apply_vad_silence_floor(resolved: ResolvedAgentConfig, slots: dict[Any, ResolvedProvider]) -> None:
    """Raise a configured VAD's `min_silence_duration` to the turn detector's floor (in place, V6-34).

    livekit-agents 1.8.3 refuses a VAD whose `min_silence_duration` is below
    0.25 s next to its audio turn detector (`voice/audio_recognition.py`
    `_check_vad_silence_requirement` raises `ValueError` when the session
    starts), so a value the api already warned about is lifted to the floor
    here instead of failing the call. With the transcriber ending turns the VAD
    only notices barge-in, and any value is kept.
    """
    vad = slots.get("vad")
    if vad is None:
        return
    value = vad.kwargs.get(VAD_MIN_SILENCE_FIELD)
    if not isinstance(value, int | float) or value >= VAD_MIN_SILENCE_WITH_TURN_DETECTOR:
        return
    if not _livekit_detector_runs(resolved, slots):
        return
    logger.warning(
        "the VAD's silence is below what the turn detector needs; using the minimum",
        configured=value,
        used=VAD_MIN_SILENCE_WITH_TURN_DETECTOR,
    )
    kwargs = {**vad.kwargs, VAD_MIN_SILENCE_FIELD: VAD_MIN_SILENCE_WITH_TURN_DETECTOR}
    slots["vad"] = vad.model_copy(update={"kwargs": kwargs})


def _apply_sticky_routing(resolved: ResolvedAgentConfig, slots: dict[Any, ResolvedProvider]) -> None:
    """Turn `openrouter-llm`'s `sticky_routing` into OpenRouter's routing hints (in place, V6-34).

    With the field on, every OpenRouter language-model slot sends the agent's id
    as `user` and as `prompt_cache_key` (`LLM.with_openrouter(user=...,
    prompt_cache_key=...)`, livekit-plugins-openai 1.8.3 `llm.py:454,460`).
    OpenRouter keys sticky routing on `prompt_cache_key` when no `session_id`
    is sent, so the agent's calls keep reaching the host whose prompt cache is
    warm. The agent id is an opaque platform id: nothing about the caller is
    sent. The field itself never reaches the constructor (the factory drops it).
    """
    routing_id = resolved.agent_id
    for slot, provider in list(slots.items()):
        if (
            provider.provider_id != _OPENROUTER_LLM_ID
            or provider.kwargs.get(STICKY_ROUTING_FIELD) is not True
        ):
            continue
        kwargs = {k: v for k, v in provider.kwargs.items() if k != STICKY_ROUTING_FIELD}
        if routing_id:
            kwargs.setdefault("user", routing_id)
            kwargs.setdefault("prompt_cache_key", routing_id)
        slots[slot] = provider.model_copy(update={"kwargs": kwargs})


def _apply_turn_detector_settings(resolved: ResolvedAgentConfig, slots: dict[Any, ResolvedProvider]) -> None:
    """Carry `PipelineConfig.turn_detector` into the `turn_detection` slot's kwargs (in place).

    * No slot: an `inference-turn-detector` slot is synthesized with
      :func:`~lkap_agent.providers.factory.turn_detector_kwargs`, so the factory
      builds `inference.TurnDetector(version=..., unlikely_threshold=...)` and
      `main._assemble` skips its default detector (it builds one only while
      `providers.turn_detection is None`). Settings that change nothing (for
      example `mode="hosted"` on a hosted connection) synthesize nothing.
    * An Inference slot: the settings fill the kwargs the slot does not set itself.
    * The local plugin slot: only `unlikely_threshold` (its one kwarg).

    Nothing happens without settings, in `realtime` mode or on the text channel
    (no client-side turns; `SessionBuilder.build` would ignore the slot).
    `mode="stt"` (V6-34) places no detector: when the transcriber ends the turn
    nothing is synthesized, and with a model that cannot it counts as unset.
    """
    settings = resolved.config.pipeline.turn_detector
    if settings is None or not _client_side_turns(resolved):
        return
    if settings.mode == "stt" and stt_decides_turns(resolved):
        return
    wanted = turn_detector_kwargs(
        mode=None if settings.mode == "stt" else settings.mode,
        unlikely_threshold=settings.unlikely_threshold,
        connection_mode=resolved.connection.capabilities.turn_detector_mode,
    )
    detector = slots.get("turn_detection")
    if detector is None:
        if wanted:
            spec = get_spec(_INFERENCE_TURN_DETECTOR_ID)
            slots["turn_detection"] = ResolvedProvider(
                provider_id=spec.id, python_class=spec.python_class, model=None, kwargs=wanted
            )
        return
    if detector.provider_id == _INFERENCE_TURN_DETECTOR_ID:
        kwargs = {**wanted, **{k: v for k, v in detector.kwargs.items() if v is not None}}
    elif detector.provider_id == _PLUGIN_TURN_DETECTOR_ID and settings.unlikely_threshold is not None:
        kwargs = dict(detector.kwargs)
        if kwargs.get("unlikely_threshold") is None:
            kwargs["unlikely_threshold"] = settings.unlikely_threshold
    else:
        return
    slots["turn_detection"] = detector.model_copy(update={"kwargs": kwargs})


def _noise_cancellation_specs() -> list[ProviderSpec]:
    """Every registry noise filter, in registry order (a test seam)."""
    return by_kind("noise_cancellation", status=None)


def _offered_telephony_filter(resolved: ResolvedAgentConfig) -> ResolvedProvider | None:
    """The noise filter the `telephony` preset turns on when the agent configured none.

    The first registry entry with a telephony variant that is offered, needs no
    key, is installed on the worker pool (when the pool reported its list) and,
    when Cloud-only, runs on a connection with Cloud noise cancellation.
    """
    installed = resolved.installed_provider_ids
    tier = resolved.connection.capabilities.noise_cancellation_tier
    for spec in _noise_cancellation_specs():
        if (
            spec.telephony_variant is not None
            and spec.availability == "available"
            and not spec.requires_credential
            and (installed is None or spec.id in installed)
            and (not spec.capabilities.cloud_only or tier == "krisp")
        ):
            return ResolvedProvider(
                provider_id=spec.id, python_class=spec.python_class, model=None, kwargs={}
            )
    return None


def _apply_telephony_noise_cancellation(
    resolved: ResolvedAgentConfig, slots: dict[Any, ResolvedProvider]
) -> None:
    """Use the phone-tuned noise filter for the `telephony` preset on a phone call (in place).

    D-V5-30: the preset is the per-agent opt-in, so an agent without a filter gets
    the offered LiveKit Cloud one (:func:`_offered_telephony_filter`); a filter
    without a telephony variant is kept as it is. A web session is never changed.
    """
    pipeline = resolved.config.pipeline
    if pipeline.conversation_preset != "telephony" or not is_sip_channel(resolved.channel):
        return
    current = slots.get("noise_cancellation") or _offered_telephony_filter(resolved)
    if current is None:
        logger.info("telephony preset: no noise filter with a phone variant is available on this connection")
        return
    variant = telephony_noise_cancellation(current)
    if variant is None:
        logger.info(
            "telephony preset: the noise filter has no phone variant; keeping it",
            provider_id=current.provider_id,
        )
        return
    slots["noise_cancellation"] = variant


def llm_capabilities_of(resolved: ResolvedAgentConfig) -> ModelCapabilities | None:
    """The resolved ``llm`` slot's capabilities (``None`` from an older api or without an llm slot)."""
    slot = resolved.resolved.get("llm")
    return slot.capabilities if slot is not None else None


def factory_view(resolved: ResolvedAgentConfig) -> ResolvedAgentConfig:
    """What `ProviderFactory.build_all` should see for this session.

    A realtime model on the text channel must answer in text, which is exactly
    the construction the factory performs for `half_cascade`; every other
    session is built as configured.
    """
    if is_text_channel(resolved) and resolved.config.pipeline.mode == "realtime":
        pipeline = resolved.config.pipeline.model_copy(update={"mode": "half_cascade"})
        config = resolved.config.model_copy(update={"pipeline": pipeline})
        return resolved.model_copy(update={"config": config})
    return resolved


@dataclass(slots=True)
class SessionPlan:
    """A constructed session plus everything `run_session` needs to start it."""

    session: AgentSession[Any]
    room_options: RoomOptions
    avatar: Any | None
    has_tts: bool
    #: `pipeline.mode == "realtime"`: the model owns turn-taking and audio out.
    is_realtime: bool
    mode: PipelineMode = "cascaded"
    #: A `channel="text"` session: no audio in or out.
    text_only: bool = False
    #: What the api resolved about the cascaded LLM (`ResolvedProvider.capabilities`, V4-08);
    #: the worker hands it to `SessionContext.llm_capabilities`.
    llm_capabilities: ModelCapabilities | None = None
    #: `voice.thinking_sound`, or `"none"` on the text channel (no audio out, R-V4-38);
    #: the worker plays it through a `BackgroundAudioPlayer` while the agent is thinking.
    thinking_sound: ThinkingSound = "none"
    #: `voice.ambient_sound` (V5-07), or `"none"` on the text channel; played for the whole call
    #: by the same `BackgroundAudioPlayer` as the thinking sound.
    ambient_sound: str = "none"
    #: `pipeline.conversation_preset` the session was built with (for logs and tests).
    conversation_preset: str = "custom"
    #: V5-31: the text output after RoomIO's transcription that feeds the agent's captions
    #: (a panel with a `captions` block, never on the text channel).
    captions_tap: CaptionsTextOutput | None = None

    @property
    def needs_generate_reply_greeting(self) -> bool:
        """Whether `say()` is unavailable for the greeting (ARCHITECTURE §15.9).

        `AgentActivity.say` raises without a TTS unless the realtime model sets
        `capabilities.supports_say`. Neither `livekit-plugins-google` nor
        `livekit-plugins-openai` 1.8.2 does, so a realtime session with no TTS
        must greet via `generate_reply(instructions=...)`.
        """
        return not self.has_tts


def auto_inject_active(config: AgentConfig) -> bool:
    """Whether knowledge auto-inject can change the chat context on a turn.

    `ResolvedAgentConfig.kb_ids` is `config.knowledge.kb_ids` (the api's
    `/resolved` route), and a flow node only ever searches a subset of it, so
    an empty list means auto-inject never adds anything.
    """
    return config.knowledge.auto_inject and bool(config.knowledge.kb_ids)


def build_turn_handling(
    configured: dict[str, Any],
    *,
    allow_interruptions: bool,
    turn_detector: Any | None,
    disable_preemptive: bool = False,
) -> TurnHandlingOptions:
    """Merge the agent's `pipeline.turn_handling` with the platform's own keys.

    Args:
        configured: `AgentConfig.pipeline.turn_handling` as stored by the console.
        allow_interruptions: `AgentConfig.voice.allow_interruptions`.
        turn_detector: The session's turn detector (cascaded / half-cascade), else `None`.
        disable_preemptive: Turn preemptive generation off unless `configured`
            sets `preemptive_generation.enabled` itself (knowledge auto-inject,
            see :func:`auto_inject_active`).

    Returns:
        A `TurnHandlingOptions` dict safe to pass to `AgentSession`.
    """
    options: dict[str, Any] = {k: v for k, v in configured.items() if k in _TURN_HANDLING_KEYS}
    dropped = sorted(set(configured) - _TURN_HANDLING_KEYS)
    if dropped:
        logger.warning("dropping unknown turn_handling keys", keys=dropped)

    interruption: dict[str, Any] = dict(options.get("interruption") or {})
    interruption.setdefault("enabled", allow_interruptions)
    options["interruption"] = interruption

    if disable_preemptive:
        preemptive: dict[str, Any] = dict(options.get("preemptive_generation") or {})
        preemptive.setdefault("enabled", False)
        options["preemptive_generation"] = preemptive

    if turn_detector is not None and "turn_detection" not in options:
        options["turn_detection"] = turn_detector
    return cast(TurnHandlingOptions, options)


class SessionBuilder:
    """Turns `(ResolvedAgentConfig, BuiltProviders)` into a startable session."""

    def build(
        self,
        resolved: ResolvedAgentConfig,
        providers: BuiltProviders,
        *,
        vad: Any | None = None,
        turn_detector: Any | None = None,
    ) -> SessionPlan:
        """Construct the `AgentSession` for one job.

        Args:
            resolved: The config fetched from the api (after :func:`prepare_resolved`).
            providers: Already-constructed plugin objects, one per slot.
            vad: The default VAD (the prewarmed Silero from `proc.userdata`), used
                when the `vad` slot is empty; `None` to run without one.
            turn_detector: The default turn detector, used when the
                `turn_detection` slot is empty.

        Returns:
            A :class:`SessionPlan`.

        Raises:
            ValueError: If the pipeline mode has no model in a slot it requires.
        """
        config = resolved.config
        mode = config.pipeline.mode
        text_only = is_text_channel(resolved)
        uses_realtime_model = mode in ("realtime", "half_cascade")
        # Only the audio-native realtime mode leaves turn-taking to the model.
        client_side_turns = mode != "realtime" and not text_only

        if uses_realtime_model:
            model = providers.realtime
            if model is None:
                raise ValueError(f"pipeline.mode is {mode!r} but no realtime provider was resolved")
        else:
            model = providers.llm
            if model is None:
                raise ValueError("pipeline.mode is 'cascaded' but no llm provider was resolved")

        stt = None if uses_realtime_model or text_only else providers.stt
        tts = None if text_only else providers.tts
        if mode == "half_cascade" and not text_only and tts is None:
            raise ValueError("pipeline.mode is 'half_cascade' but no tts provider was resolved")

        if client_side_turns:
            session_vad = providers.vad if providers.vad is not None else vad
            detector = providers.turn_detection if providers.turn_detection is not None else turn_detector
            if uses_realtime_model and detector is not None and self._keeps_server_side_turns(model):
                logger.info("realtime model keeps server-side turn detection; no client turn detector")
                detector = None
            if stt is not None and _stt_can_end_turns(resolved):
                # V6-02 (D-V6-5): the transcriber's end-of-turn replaces the platform's detector
                # unless the admin configured a `turn_detection` slot. The check reads the stored
                # config, not `providers.turn_detection`: `prepare_resolved` synthesizes a detector
                # slot from `pipeline.turn_detector` settings (always on a `local` connection).
                # V6-34: a model that can end turns (Inference Flux) does so only when opted in.
                if stt_decides_turns(resolved):
                    logger.info("the transcriber decides when the caller's turn ends", turn_detection="stt")
                    detector = STT_TURN_DETECTION
                elif config.pipeline.turn_detection is not None:
                    logger.info("the configured turn detector overrides the transcriber's end-of-turn")
                else:
                    logger.info(
                        "the transcriber can end turns but the agent did not ask for it; "
                        "the turn detector decides (the fast preset or turn_detector.mode 'stt' opt in)"
                    )
        else:
            if providers.vad is not None or providers.turn_detection is not None:
                logger.warning(
                    "ignoring the vad/turn_detection slots: this session has no client-side turn-taking",
                    mode=mode,
                    text_only=text_only,
                )
            session_vad, detector = None, None

        auto_inject = auto_inject_active(config)
        turn_handling = build_turn_handling(
            # V6-34: the `fast` preset resolves by who ends the turn in this session.
            resolve_turn_handling(
                config.pipeline.conversation_preset,
                config.pipeline.turn_handling,
                stt_turns=detector == STT_TURN_DETECTION,
            ),
            allow_interruptions=config.voice.allow_interruptions,
            turn_detector=detector,
            disable_preemptive=auto_inject,
        )
        preemptive_enabled = bool(turn_handling.get("preemptive_generation", {}).get("enabled", True))
        if auto_inject:
            if preemptive_enabled:
                logger.info(
                    "knowledge auto-inject is on but the config keeps preemptive generation enabled; "
                    "turns with a knowledge hit discard the preemptive reply",
                    kb_count=len(config.knowledge.kb_ids),
                )
            else:
                logger.info(
                    "preemptive generation disabled: knowledge auto-inject changes the chat context "
                    "on every turn with a hit, which discards the preemptive reply",
                    kb_count=len(config.knowledge.kb_ids),
                )

        # The plugin objects come back from the factory as `Any`; `AgentSession`'s
        # overloads do not admit `None` for stt/tts even though the runtime does
        # (it is how a realtime-only or text-only pipeline is expressed).
        session: AgentSession[Any] = AgentSession(
            llm=cast(Any, model),
            stt=cast(Any, stt),
            tts=cast(Any, tts),
            vad=cast(Any, session_vad),
            turn_handling=turn_handling,
            max_tool_steps=config.tools.max_tool_steps,
            user_away_timeout=config.voice.user_away_timeout_s,
            tool_handling={"async_options": ASYNC_TOOL_OPTIONS[mode]},
        )

        room_options = RoomOptions(
            # An empty identity (a worker-created session whose dispatch named no
            # participant) links to the first caller, as the SDK does by default.
            participant_identity=resolved.participant_identity or NOT_GIVEN,
            video_input=self._wants_video_input(resolved, realtime_model=uses_realtime_model)
            and not text_only,
            # D-W2-9p: a voice-only agent refuses typed input at the worker, not just
            # in the UI; a text-channel session always takes typed input.
            text_input=NOT_GIVEN if config.capabilities.chat_input or text_only else False,
            audio_input=self._audio_input(providers, text_only=text_only),
            audio_output=False if text_only else NOT_GIVEN,
            close_on_disconnect=False,
        )
        captions_tap: CaptionsTextOutput | None = None
        if not text_only and has_captions_block(resolved):
            captions_tap = CaptionsTextOutput()
            room_options.text_output = TextOutputOptions(next_in_chain=captions_tap)
            register_caption_tap(session, captions_tap)

        logger.info(
            "session built",
            mode=mode,
            text_only=text_only,
            has_stt=stt is not None,
            has_tts=tts is not None,
            has_vad=session_vad is not None,
            has_turn_detector=detector is not None,
            has_noise_cancellation=providers.noise_cancellation is not None and not text_only,
            video_input=room_options.video_input,
            max_tool_steps=config.tools.max_tool_steps,
            preemptive_generation=preemptive_enabled,
            conversation_preset=config.pipeline.conversation_preset,
            captions=captions_tap is not None,
        )
        return SessionPlan(
            session=session,
            room_options=room_options,
            avatar=None if text_only else providers.avatar,
            has_tts=tts is not None,
            is_realtime=mode == "realtime",
            mode=mode,
            text_only=text_only,
            llm_capabilities=llm_capabilities_of(resolved),
            thinking_sound="none" if text_only else config.voice.thinking_sound,
            ambient_sound="none" if text_only else config.voice.ambient_sound,
            conversation_preset=config.pipeline.conversation_preset,
            captions_tap=captions_tap,
        )

    @staticmethod
    def _keeps_server_side_turns(model: Any) -> bool:
        """Whether a realtime model detects turns itself and cannot hand them to the client."""
        caps = getattr(model, "capabilities", None)
        return bool(getattr(caps, "turn_detection", False)) and not bool(
            getattr(caps, "can_disable_turn_detection", False)
        )

    @staticmethod
    def _audio_input(providers: BuiltProviders, *, text_only: bool) -> Any:
        """`RoomOptions.audio_input`: off for text, a noise filter when configured."""
        if text_only:
            return False
        if providers.noise_cancellation is not None:
            return AudioInputOptions(noise_cancellation=providers.noise_cancellation)
        return NOT_GIVEN

    @staticmethod
    def _wants_video_input(resolved: ResolvedAgentConfig, *, realtime_model: bool) -> bool:
        """Only realtime models consume the room's video input.

        `AgentActivity.push_video` forwards frames to a realtime session only, so
        enabling `video_input` in cascaded mode would decode frames the model
        never sees. Cascaded vision goes through `FrameBuffer` +
        `PlatformAgent.on_user_turn_completed` instead. Half-cascade runs a
        realtime model, so it sees frames the same way realtime mode does.
        """
        caps = resolved.config.capabilities
        return realtime_model and (caps.camera or caps.screen_share)


#: `VoiceConfig.thinking_sound` → the `BuiltinAudioClip` member it plays (verified in 1.8.2,
#: `voice/background_audio.py`).
THINKING_SOUND_CLIPS: Final[dict[str, str]] = {
    "keyboard_typing": "KEYBOARD_TYPING",
    "keyboard_typing2": "KEYBOARD_TYPING2",
    "office_ambience": "OFFICE_AMBIENCE",
}

#: Playback volume of the thinking sound (BACKGROUND-TOOLS.md §4.2).
THINKING_SOUND_VOLUME: Final[float] = 0.6

#: Playback volume of the ambient clip: under the voice, never over it.
AMBIENT_SOUND_VOLUME: Final[float] = 0.4


async def start_background_audio(
    plan: SessionPlan, room: Any, *, player_factory: Callable[..., Any] | None = None
) -> Callable[[str], Awaitable[None]] | None:
    """Start one `BackgroundAudioPlayer` for the thinking sound and the ambient clip.

    `BackgroundAudioPlayer(ambient_sound=..., thinking_sound=...)` (livekit-agents 1.8.3
    `voice/background_audio.py:135-143`) publishes one track mixing both: the ambient clip
    loops for the whole call, the thinking sound plays only while `agent_state ==
    "thinking"` (the blocking wait and the inline part of an `auto` tool). It is heard on
    SIP legs too. Called after `session.start`: nothing starts on the text channel, with
    neither sound set, or when the session has no audio output. An `asset:<id>` ambient
    clip is not played yet (a later package serves uploaded clips). A failure to start is
    logged and the call goes on silently.

    Args:
        plan: The built session plan (`thinking_sound`, `ambient_sound`, `text_only`, `session`).
        room: The connected room the player publishes to.
        player_factory: Test seam; defaults to `livekit.agents.BackgroundAudioPlayer`.

    Returns:
        A shutdown callback (``async (reason) -> None``) that closes the player, or
        `None` when nothing was started.
    """
    if plan.text_only:
        return None
    thinking = plan.thinking_sound
    ambient = plan.ambient_sound
    if ambient.startswith("asset:"):
        logger.warning("uploaded ambient clips are not played yet; the call goes on without one")
        ambient = "none"
    if thinking == "none" and ambient == "none":
        return None
    output = getattr(plan.session, "output", None)
    if getattr(output, "audio", None) is None:
        logger.info(
            "background audio skipped: the session has no audio output",
            thinking_sound=thinking,
            ambient_sound=ambient,
        )
        return None
    from livekit.agents import AudioConfig, BackgroundAudioPlayer, BuiltinAudioClip  # noqa: PLC0415

    kwargs: dict[str, Any] = {}
    if thinking != "none":
        kwargs["thinking_sound"] = AudioConfig(
            BuiltinAudioClip[THINKING_SOUND_CLIPS[thinking]], volume=THINKING_SOUND_VOLUME
        )
    if ambient != "none":
        # `AMBIENT_SOUNDS` are the `BuiltinAudioClip` member names lower-cased (`background_audio.py:29-36`).
        kwargs["ambient_sound"] = AudioConfig(BuiltinAudioClip[ambient.upper()], volume=AMBIENT_SOUND_VOLUME)
    factory = player_factory or BackgroundAudioPlayer
    player = factory(**kwargs)
    try:
        await player.start(room=room, agent_session=plan.session)
    except Exception:
        logger.warning("background audio could not start; the call goes on without it", exc_info=True)
        with contextlib.suppress(Exception):
            await player.aclose()
        return None
    logger.info("background audio started", thinking_sound=thinking, ambient_sound=ambient)

    async def _stop_background_audio(reason: str) -> None:
        del reason
        with contextlib.suppress(Exception):
            await player.aclose()

    return _stop_background_audio


#: The V4-12 name, kept for callers and tests written before the ambient clip (V5-07).
start_thinking_sound = start_background_audio


# ------------------------------------------------------------ consent and disclosure (V5-15)

#: The greeting placeholder that marks where the AI disclosure is spoken.
DISCLOSURE_PLACEHOLDER: Final[str] = "{disclosure}"

#: Consent block kinds whose empty `text` is filled from the workspace's wording.
_PRESET_CONSENT_KINDS: Final[frozenset[str]] = frozenset({"recording", "ai_disclosure"})


def recording_needs_consent(config: AgentConfig) -> bool:
    """Whether the recording waits for the caller's agreement (`recording.enabled` and `require_consent`)."""
    return bool(config.recording.enabled and config.recording.require_consent)


def _speaks_disclosure(config: AgentConfig, channel: str) -> bool:
    """Whether the disclosure is spoken: `greeting`/`both`, or `banner` on a phone call (no screen)."""
    disclosure = config.disclosure
    if not disclosure.enabled:
        return False
    return disclosure.position in ("greeting", "both") or is_sip_channel(channel)


def _with_disclosure(greeting: str, disclosure: str) -> str:
    """`greeting` with `disclosure` at its `{disclosure}` placeholder, else in front (once)."""
    if DISCLOSURE_PLACEHOLDER in greeting:
        return " ".join(greeting.replace(DISCLOSURE_PLACEHOLDER, disclosure).split())
    if disclosure in greeting:
        return greeting
    return f"{disclosure} {greeting.strip()}".strip()


def _without_placeholder(greeting: str) -> str:
    return " ".join(greeting.replace(DISCLOSURE_PLACEHOLDER, "").split())


def apply_compliance(resolved: ResolvedAgentConfig) -> ResolvedAgentConfig:
    """Bake the AI disclosure and the consent wording into the session's config (V5-15, D-V5-22).

    Runs after the flow preparation (a flow's start node replaces the
    greeting), so the greeting and every tool read one config:

    * **Wording.** `disclosure.text` and `recording.consent_text` are filled
      from the workspace's effective wording (`resolved.compliance`; the
      default jurisdiction's preset from an api before V5-15) when the agent
      leaves them empty, and so is the empty `text` of every `recording` or
      `ai_disclosure` consent block (`terms`/`custom` blocks need their own).
      The worker hashes exactly these strings on the `consent` event.
    * **Greeting.** When the disclosure is spoken (`position` `greeting` or
      `both`; `banner` too on a phone call, which has no screen), it replaces
      a `{disclosure}` placeholder in the greeting or goes in front of it,
      once. Without a greeting to carry it (`first_speaker="user"` or an
      empty greeting) the line becomes an instruction for the agent's first
      reply instead. A placeholder is removed when nothing is spoken.
    * **Recording consent.** With `recording.require_consent` (and recording
      on), an instruction tells the agent to ask before the recording starts:
      with `request_consent` when a `recording` consent block is on screen,
      else out loud with the exact question, then `record_consent`.

    Args:
        resolved: The prepared config (after `prepare_flow_resolved`).

    Returns:
        A copy; `resolved` itself is not modified.
    """
    config = resolved.config
    compliance = resolved.compliance or ResolvedCompliance()
    disclosure_text = (config.disclosure.text or "").strip() or compliance.disclosure_text
    consent_text = (config.recording.consent_text or "").strip() or compliance.recording_text
    disclosure = config.disclosure.model_copy(update={"text": disclosure_text})
    recording = config.recording.model_copy(update={"consent_text": consent_text})

    blocks = []
    for spec in config.panel.blocks:
        if spec.type == "consent" and not str(spec.config.get("text") or "").strip():
            kind = spec.config.get("kind", "recording")
            if kind in _PRESET_CONSENT_KINDS:
                text = consent_text if kind == "recording" else disclosure_text
                spec = spec.model_copy(update={"config": {**spec.config, "text": text}})
        blocks.append(spec)
    panel = config.panel.model_copy(update={"blocks": blocks})

    voice = config.voice
    notes: list[str] = []
    speaks = _speaks_disclosure(config, resolved.channel)
    if speaks and voice.greeting.strip() and voice.first_speaker == "agent":
        voice = voice.model_copy(update={"greeting": _with_disclosure(voice.greeting, disclosure_text)})
    else:
        if DISCLOSURE_PLACEHOLDER in voice.greeting:
            voice = voice.model_copy(update={"greeting": _without_placeholder(voice.greeting)})
        if speaks:
            notes.append(f'AI disclosure: begin your first reply by saying exactly: "{disclosure_text}"')

    if recording_needs_consent(config) and resolved.channel != "text":
        on_screen = not is_sip_channel(resolved.channel) and any(
            spec.type == "consent" and spec.config.get("kind", "recording") == "recording"
            for spec in config.panel.blocks
        )
        if on_screen:
            notes.append(
                "Recording consent: this call is recorded only after the caller agrees. Early in the "
                "call, ask with request_consent (it shows the question on their screen); if they "
                "answer out loud, call record_consent with their answer."
            )
        else:
            notes.append(
                "Recording consent: this call is recorded only after the caller agrees. Early in the "
                f'call, ask exactly: "{consent_text}" Then call record_consent with accepted true or '
                "false. Never say the call is being recorded before they agree."
            )

    instructions = config.instructions
    if notes:
        parts = [instructions.rstrip(), *notes] if instructions.strip() else notes
        instructions = "\n\n".join(parts)
    updated = config.model_copy(
        update={
            "disclosure": disclosure,
            "recording": recording,
            "panel": panel,
            "voice": voice,
            "instructions": instructions,
        }
    )
    logger.debug(
        "compliance applied",
        jurisdiction=compliance.jurisdiction,
        disclosure_spoken=speaks,
        recording_consent=recording_needs_consent(config),
    )
    resolved_recording = resolved.recording.model_copy(update={"consent_text": consent_text})
    return resolved.model_copy(update={"config": updated, "recording": resolved_recording})


# ---------------------------------------------------------------------- caller memory (V5-40)

#: The heading in front of the recalled memories (outside the fence: it is the platform's words).
MEMORY_HEADING: Final[str] = (
    "What we know about this caller from earlier conversations (it may be out of date; confirm "
    "anything important with them before relying on it):"
)
#: The fence's ``source`` for recalled memories.
MEMORY_SOURCE: Final[str] = "memory"
#: ``memory.max_recall_tokens`` becomes a character budget at about four characters a token.
_CHARS_PER_TOKEN: Final[int] = 4


class MemoryRecallApi(Protocol):
    """The one api call the recall needs (``ConfigClient.memory_recall``)."""

    async def memory_recall(self, request: MemoryRecallIn) -> MemoryRecallOut:
        """``POST /internal/v1/memory/recall``; never raises."""
        ...


def memory_block(memories: Sequence[str], *, max_tokens: int) -> str:
    """The recalled memories as a fenced instruction block, or ``""`` when none fit.

    Each memory is one ``- `` line; lines are added newest first until the next
    one would pass the budget (``max_tokens`` x 4 characters, the fence included),
    so a memory is never cut in half.
    """
    budget = max_tokens * _CHARS_PER_TOKEN - fence_overhead(MEMORY_SOURCE)
    lines: list[str] = []
    used = 0
    for memory in memories:
        text = " ".join(str(memory).split())
        if not text:
            continue
        line = f"- {text}"
        cost = len(line) + (1 if lines else 0)
        if used + cost > budget:
            break
        lines.append(line)
        used += cost
    if not lines:
        return ""
    fenced = fence("\n".join(lines), source=MEMORY_SOURCE, max_chars=budget)
    return f"{MEMORY_HEADING}\n{fenced}"


def with_memory(config: AgentConfig, recall: MemoryRecallOut) -> AgentConfig:
    """``config`` with the recalled memories and the memory notice appended to its instructions."""
    notes: list[str] = []
    block = memory_block(recall.memories, max_tokens=config.memory.max_recall_tokens)
    if block:
        notes.append(block)
    consent_line = (config.memory.consent_line or "").strip()
    if recall.remember and consent_line:
        notes.append(f'Memory notice: early in the call, tell the caller: "{consent_line}"')
    if not notes:
        return config
    base = config.instructions.rstrip()
    instructions = "\n\n".join([base, *notes]) if base else "\n\n".join(notes)
    return config.model_copy(update={"instructions": instructions})


async def apply_memory_recall(
    resolved: ResolvedAgentConfig,
    api: object,
    *,
    caller_e164: str | None = None,
    timeout_s: float = 6.0,
) -> ResolvedAgentConfig:
    """Add what the memory knows about the caller to the session's instructions (V5-40).

    Called once, after `apply_compliance` and before the session is assembled. With
    ``memory.enabled`` off (every agent saved before V5-40) it returns ``resolved``
    unchanged without calling the api. Never raises: a failed recall starts the
    session without memories.

    Args:
        resolved: The prepared config.
        api: The worker's api client; anything without ``memory_recall`` (a test
            fake of the older client) is skipped.
        caller_e164: The caller's number when the worker already knows it and the
            api may not (an inbound call before its leg is reported).
        timeout_s: The most the recall may hold up the start.

    Returns:
        A copy with the memories appended, or ``resolved`` itself.
    """
    if not resolved.config.memory.enabled:
        return resolved
    if not callable(getattr(api, "memory_recall", None)):
        logger.debug("memory recall skipped: the api client has no memory_recall")
        return resolved
    request = MemoryRecallIn(session_id=resolved.session_id, caller_e164=caller_e164)
    try:
        result = await asyncio.wait_for(cast(MemoryRecallApi, api).memory_recall(request), timeout=timeout_s)
    except Exception as exc:  # noqa: BLE001 - memory is an enhancement, never a precondition
        logger.warning("memory recall failed", error_type=type(exc).__name__)
        return resolved
    logger.info("memory recalled", status=result.status, count=len(result.memories), remember=result.remember)
    config = with_memory(resolved.config, result)
    if config is resolved.config:
        return resolved
    return resolved.model_copy(update={"config": config})
