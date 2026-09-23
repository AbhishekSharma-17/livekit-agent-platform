"""Assemble an `AgentSession` and its `RoomOptions` from a resolved config.

This is the one place that knows how `AgentConfig` maps onto the LiveKit
runtime (docs/ARCHITECTURE.md §4/§6/§8, ARCHITECTURE-V2 D-V2-10). Verified
against livekit-agents 1.8.2:

* `turn_handling` is a `TurnHandlingOptions` TypedDict; `allow_interruptions`
  lives at `turn_handling["interruption"]["enabled"]`.
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
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, cast

from livekit.agents import NOT_GIVEN, AgentSession, TurnHandlingOptions
from livekit.agents.voice.room_io import AudioInputOptions, RoomOptions
from lkap_contracts.agent_config import AvatarOptions, PipelineMode, ResolvedAgentConfig, ResolvedProvider

from lkap_agent.logging import get_logger
from lkap_agent.providers.factory import BuiltProviders

__all__ = [
    "AVATAR_OPTION_KWARGS",
    "SessionBuilder",
    "SessionPlan",
    "build_turn_handling",
    "factory_view",
    "is_text_channel",
    "prepare_resolved",
]

logger = get_logger(__name__)

#: `TurnHandlingOptions` keys a config may set; anything else is dropped so a
#: stale row cannot raise a TypeError inside `AgentSession.__init__`.
_TURN_HANDLING_KEYS: frozenset[str] = frozenset(
    {"turn_detection", "endpointing", "interruption", "preemptive_generation", "user_turn_limit"}
)

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
    return resolved.model_copy(update={"resolved": slots})


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

    @property
    def needs_generate_reply_greeting(self) -> bool:
        """Whether `say()` is unavailable for the greeting (ARCHITECTURE §15.9).

        `AgentActivity.say` raises without a TTS unless the realtime model sets
        `capabilities.supports_say`. Neither `livekit-plugins-google` nor
        `livekit-plugins-openai` 1.8.2 does, so a realtime session with no TTS
        must greet via `generate_reply(instructions=...)`.
        """
        return not self.has_tts


def build_turn_handling(
    configured: dict[str, Any],
    *,
    allow_interruptions: bool,
    turn_detector: Any | None,
) -> TurnHandlingOptions:
    """Merge the agent's `pipeline.turn_handling` with the platform's own keys.

    Args:
        configured: `AgentConfig.pipeline.turn_handling` as stored by the console.
        allow_interruptions: `AgentConfig.voice.allow_interruptions`.
        turn_detector: The session's turn detector (cascaded / half-cascade), else `None`.

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
        else:
            if providers.vad is not None or providers.turn_detection is not None:
                logger.warning(
                    "ignoring the vad/turn_detection slots: this session has no client-side turn-taking",
                    mode=mode,
                    text_only=text_only,
                )
            session_vad, detector = None, None

        turn_handling = build_turn_handling(
            config.pipeline.turn_handling,
            allow_interruptions=config.voice.allow_interruptions,
            turn_detector=detector,
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
        )
        return SessionPlan(
            session=session,
            room_options=room_options,
            avatar=None if text_only else providers.avatar,
            has_tts=tts is not None,
            is_realtime=mode == "realtime",
            mode=mode,
            text_only=text_only,
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
