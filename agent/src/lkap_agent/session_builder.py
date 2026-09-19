"""Assemble an `AgentSession` and its `RoomOptions` from a resolved config.

This is the one place that knows how `AgentConfig` maps onto the LiveKit
runtime (docs/ARCHITECTURE.md §4/§6/§8). Verified against livekit-agents 1.8.2:

* `turn_handling` is a `TurnHandlingOptions` TypedDict; `allow_interruptions`
  lives at `turn_handling["interruption"]["enabled"]`.
* `vad=NOT_GIVEN` makes `AgentSession` construct `inference.VAD` itself, which
  needs network. The builder therefore always passes `vad` explicitly, using the
  prewarmed Silero instance from `proc.userdata` or `None`.
* `inference.TurnDetector()` is a cascaded-mode concern: realtime models do
  server-side turn detection and must not get a second detector.
* `RoomOptions.video_input` defaults to `False`; only realtime models receive
  frames through it (cascaded vision is injected per turn by `PlatformAgent`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from livekit.agents import NOT_GIVEN, AgentSession, TurnHandlingOptions
from livekit.agents.voice.room_io import RoomOptions
from lkap_contracts.agent_config import ResolvedAgentConfig

from lkap_agent.logging import get_logger
from lkap_agent.providers.factory import BuiltProviders

__all__ = ["SessionBuilder", "SessionPlan", "build_turn_handling"]

logger = get_logger(__name__)

#: `TurnHandlingOptions` keys a config may set; anything else is dropped so a
#: stale row cannot raise a TypeError inside `AgentSession.__init__`.
_TURN_HANDLING_KEYS: frozenset[str] = frozenset(
    {"turn_detection", "endpointing", "interruption", "preemptive_generation", "user_turn_limit"}
)


@dataclass(slots=True)
class SessionPlan:
    """A constructed session plus everything `run_session` needs to start it."""

    session: AgentSession[Any]
    room_options: RoomOptions
    avatar: Any | None
    has_tts: bool
    is_realtime: bool

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
        turn_detector: An `inference.TurnDetector()` for cascaded mode, else `None`.

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
            resolved: The config fetched from the api.
            providers: Already-constructed plugin objects, one per slot.
            vad: The prewarmed Silero VAD from `proc.userdata`, or `None` to run
                without one (realtime models and tests).
            turn_detector: A turn-detection mode for cascaded pipelines; ignored
                in realtime mode, where the model detects turns server-side.

        Returns:
            A :class:`SessionPlan`.

        Raises:
            ValueError: If the pipeline mode has no model in its required slot.
        """
        config = resolved.config
        is_realtime = config.pipeline.mode == "realtime"

        if is_realtime:
            model = providers.realtime
            if model is None:
                raise ValueError("pipeline.mode is 'realtime' but no realtime provider was resolved")
            stt, tts = None, providers.tts
            detector = None
        else:
            model = providers.llm
            if model is None:
                raise ValueError("pipeline.mode is 'cascaded' but no llm provider was resolved")
            stt, tts = providers.stt, providers.tts
            detector = turn_detector

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
            vad=cast(Any, vad),
            turn_handling=turn_handling,
            max_tool_steps=config.tools.max_tool_steps,
            user_away_timeout=config.voice.user_away_timeout_s,
        )

        room_options = RoomOptions(
            participant_identity=resolved.participant_identity,
            video_input=self._wants_video_input(resolved, is_realtime=is_realtime),
            # D-W2-9p: a voice-only agent refuses typed input at the worker, not just in the UI.
            text_input=NOT_GIVEN if config.capabilities.chat_input else False,
            close_on_disconnect=True,
        )

        logger.info(
            "session built",
            mode=config.pipeline.mode,
            has_stt=stt is not None,
            has_tts=tts is not None,
            has_vad=vad is not None,
            has_turn_detector=detector is not None,
            video_input=room_options.video_input,
            max_tool_steps=config.tools.max_tool_steps,
        )
        return SessionPlan(
            session=session,
            room_options=room_options,
            avatar=providers.avatar,
            has_tts=tts is not None,
            is_realtime=is_realtime,
        )

    @staticmethod
    def _wants_video_input(resolved: ResolvedAgentConfig, *, is_realtime: bool) -> bool:
        """Only realtime models consume the room's video input.

        `AgentActivity.push_video` forwards frames to a realtime session only, so
        enabling `video_input` in cascaded mode would decode frames the model
        never sees. Cascaded vision goes through `FrameBuffer` +
        `PlatformAgent.on_user_turn_completed` instead.
        """
        caps = resolved.config.capabilities
        return is_realtime and (caps.camera or caps.screen_share)
