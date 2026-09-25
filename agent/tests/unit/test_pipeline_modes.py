"""Pipeline modes, VAD / turn / noise slots, text channel and avatar options (PLAN-V2 V2-07).

`SessionBuilder` is exercised with sentinel objects; the factory and the
half-cascade path are exercised with the real installed plugins (constructed
offline, never connected).
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fakes.fake_api import FakeApi, resolved_config
from fakes.fake_llm import FakeLLM
from fakes.fake_tts import FakeTTS
from livekit.agents import NOT_GIVEN, inference
from livekit.agents.voice.room_io import AudioInputOptions
from lkap_contracts import providers as provider_registry
from lkap_contracts.agent_config import AvatarOptions, ResolvedAgentConfig, ResolvedProvider
from lkap_contracts.connections import ConnectionCapabilities, ConnectionInfo
from test_main import FakeJobContext, _deps, _metadata

from lkap_agent.main import default_turn_detector, run_session
from lkap_agent.providers import factory as factory_module
from lkap_agent.providers.factory import (
    CONSTRUCTIBLE_KINDS,
    DEFAULT_OPTIONAL_SLOTS,
    BuiltProviders,
    ProviderFactory,
)
from lkap_agent.session_builder import (
    ASYNC_TOOL_OPTIONS,
    AVATAR_OPTION_KWARGS,
    THINKING_SOUND_VOLUME,
    SessionBuilder,
    factory_view,
    prepare_resolved,
    start_thinking_sound,
)

SIGNATURES: dict[str, Any] = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "plugin_signatures.json").read_text()
)


@pytest.fixture(autouse=True)
def _inference_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIVEKIT_API_KEY", "test-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "test-secret")
    monkeypatch.setenv("LIVEKIT_URL", "wss://example.livekit.cloud")


def _with_slot(
    config: ResolvedAgentConfig, slot: str, provider_id: str, **kwargs: Any
) -> ResolvedAgentConfig:
    spec = provider_registry.get(provider_id)
    slots = dict(config.resolved)
    slots[slot] = ResolvedProvider(  # type: ignore[index]
        provider_id=provider_id, python_class=spec.python_class, model=None, kwargs=kwargs
    )
    return config.model_copy(update={"resolved": slots})


def _on_connection(config: ResolvedAgentConfig, turn_detector_mode: str) -> ResolvedAgentConfig:
    caps = ConnectionCapabilities(turn_detector_mode=turn_detector_mode)  # type: ignore[arg-type]
    return config.model_copy(update={"connection": ConnectionInfo(connection_id="c1", capabilities=caps)})


# ------------------------------------------------------------ half-cascade


async def test_half_cascade_runs_the_realtime_model_with_the_tts_and_client_side_turns() -> None:
    realtime, tts, vad, detector = object(), object(), object(), object()
    providers = BuiltProviders(realtime=realtime, tts=tts, stt=object())

    plan = SessionBuilder().build(
        resolved_config(mode="half_cascade", camera=True), providers, vad=vad, turn_detector=detector
    )

    assert plan.session.llm is realtime
    assert plan.session.stt is None, "the realtime model hears the audio itself"
    assert plan.session.tts is tts
    assert plan.session.vad is vad
    assert plan.session.turn_detection is detector
    assert plan.has_tts and not plan.is_realtime and plan.mode == "half_cascade"
    assert plan.room_options.video_input is True


async def test_half_cascade_with_a_model_that_keeps_server_side_turns_gets_no_detector() -> None:
    """Gemini Live cannot hand turns to the client (`can_disable_turn_detection=False`)."""
    from types import SimpleNamespace

    gemini_like = SimpleNamespace(
        capabilities=SimpleNamespace(turn_detection=True, can_disable_turn_detection=False)
    )
    vad, detector = object(), object()
    providers = BuiltProviders(realtime=gemini_like, tts=object())

    plan = SessionBuilder().build(
        resolved_config(mode="half_cascade"), providers, vad=vad, turn_detector=detector
    )

    assert plan.session.vad is vad
    assert plan.session.turn_detection is not detector
    assert plan.session._turn_detection_explicit is False


async def test_half_cascade_without_a_tts_is_a_build_failure() -> None:
    with pytest.raises(ValueError, match="no tts provider"):
        SessionBuilder().build(resolved_config(mode="half_cascade"), BuiltProviders(realtime=object()))


async def test_half_cascade_builds_the_realtime_model_with_its_text_modality_through_the_factory() -> None:
    """End to end through the real factory: Gemini Live in TEXT mode + Inference TTS."""
    from google.genai import types

    config = resolved_config(mode="half_cascade")
    built = ProviderFactory().build_all(config)
    plan = SessionBuilder().build(config, built)

    assert plan.session.llm is built.realtime
    assert built.realtime._opts.response_modalities == [types.Modality.TEXT]
    assert isinstance(plan.session.tts, inference.TTS)


async def test_half_cascade_refuses_a_realtime_provider_without_text_modality(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A realtime engine that cannot answer in text must not reach the session at all."""
    real_get = factory_module.get_spec

    def _no_text(provider_id: str) -> Any:
        spec = real_get(provider_id)
        if spec.kind != "realtime":
            return spec
        caps = spec.capabilities.model_copy(update={"text_modality": False})
        return spec.model_copy(update={"capabilities": caps})

    monkeypatch.setattr(factory_module, "get_spec", _no_text)
    api = FakeApi(resolved_config(mode="half_cascade"))
    ctx = FakeJobContext(_metadata())

    await run_session(ctx, _deps(api, factory=ProviderFactory()))

    assert ctx.shutdown_reasons == ["configuration invalid"]
    assert api.summaries[0].status == "failed"
    assert "text_modality" in (api.summaries[0].error or "")


# ---------------------------------------------------------------- realtime


async def test_realtime_mode_gets_no_vad_and_no_turn_detector_even_when_slots_are_set() -> None:
    """D-W2-9o: the worker hands a realtime session neither a VAD nor a detector.

    livekit-agents 1.8.2 `AgentSession.__init__` still constructs its own eager
    `inference.TurnDetector()` when `turn_detection` is absent; `AgentActivity`
    drops it for a server-side-turn realtime model (and without a VAD), which is
    why the assertion is on what the worker passed, not on `session.turn_detection`.
    """
    slot_vad, slot_detector, default_detector = object(), object(), object()
    providers = BuiltProviders(realtime=object(), vad=slot_vad, turn_detection=slot_detector)

    plan = SessionBuilder().build(
        resolved_config(mode="realtime", with_tts=False),
        providers,
        vad=object(),
        turn_detector=default_detector,
    )

    assert plan.session.vad is None
    assert plan.session.turn_detection not in (slot_detector, default_detector)
    assert plan.session._turn_detection_explicit is False
    assert plan.is_realtime


# ------------------------------------------------------------ vad / turn / nc slots


async def test_configured_vad_and_turn_detection_slots_replace_the_defaults() -> None:
    slot_vad, slot_detector = object(), object()
    providers = BuiltProviders(
        stt=object(), llm=object(), tts=object(), vad=slot_vad, turn_detection=slot_detector
    )

    plan = SessionBuilder().build(resolved_config(), providers, vad=object(), turn_detector=object())

    assert plan.session.vad is slot_vad
    assert plan.session.turn_detection is slot_detector


async def test_a_noise_cancellation_slot_becomes_the_audio_input_filter() -> None:
    nc = object()
    providers = BuiltProviders(stt=object(), llm=object(), tts=object(), noise_cancellation=nc)

    plan = SessionBuilder().build(resolved_config(), providers)

    audio_input = plan.room_options.audio_input
    assert isinstance(audio_input, AudioInputOptions)
    assert audio_input.noise_cancellation is nc


async def test_no_noise_cancellation_leaves_the_sdk_audio_input_default() -> None:
    plan = SessionBuilder().build(resolved_config(), BuiltProviders(stt=object(), llm=object(), tts=object()))

    assert plan.room_options.audio_input is NOT_GIVEN


def test_vad_turn_and_noise_kinds_are_constructible_and_optional_by_default() -> None:
    assert {"vad", "turn_detection", "noise_cancellation"} <= CONSTRUCTIBLE_KINDS
    assert {"vad", "turn_detection", "noise_cancellation"} <= DEFAULT_OPTIONAL_SLOTS


def test_the_factory_builds_the_inference_turn_detector_and_silero_without_a_model_kwarg() -> None:
    """Silero's `VAD.load` has no `model` parameter although its registry entry names one."""
    factory = ProviderFactory()
    config = _with_slot(resolved_config(), "turn_detection", "inference-turn-detector", version="v1-mini")
    config = _with_slot(config, "vad", "silero-vad", min_speech_duration=0.1)

    built = factory.build_all(config)

    assert isinstance(built.turn_detection, inference.TurnDetector)
    assert built.turn_detection.model == "turn-detector-v1-mini"
    assert built.vad is not None and type(built.vad).__name__ == "VAD"


def test_a_failing_optional_voice_slot_falls_back_instead_of_failing_the_call() -> None:
    config = _with_slot(resolved_config(), "vad", "silero-vad", not_a_real_kwarg=1)

    built = ProviderFactory().build_all(config)

    assert built.vad is None and built.llm is not None


@pytest.mark.parametrize(("mode", "model"), [("local", "turn-detector-v1-mini")])
def test_default_turn_detector_forces_v1_mini_on_a_local_connection(mode: str, model: str) -> None:
    detector = default_turn_detector(cast(Any, mode))
    assert isinstance(detector, inference.TurnDetector) and detector.model == model


def test_default_turn_detector_leaves_the_version_to_the_sdk_on_a_hosted_connection() -> None:
    assert isinstance(default_turn_detector("hosted"), inference.TurnDetector)


def test_prepare_resolved_pins_a_configured_inference_turn_detector_to_v1_mini_on_local() -> None:
    config = _with_slot(resolved_config(), "turn_detection", "inference-turn-detector")

    local = prepare_resolved(_on_connection(config, "local"))
    hosted = prepare_resolved(_on_connection(config, "hosted"))

    assert local.resolved["turn_detection"].kwargs == {"version": "v1-mini"}
    assert hosted.resolved["turn_detection"].kwargs == {}


def test_prepare_resolved_keeps_an_explicit_turn_detector_version() -> None:
    config = _with_slot(resolved_config(), "turn_detection", "inference-turn-detector", version="v1")

    local = prepare_resolved(_on_connection(config, "local"))
    assert local.resolved["turn_detection"].kwargs == {"version": "v1"}


# ------------------------------------------------------------ text channel


async def test_text_channel_runs_without_audio_and_always_takes_typed_input() -> None:
    config = prepare_resolved(resolved_config(channel="text", with_avatar=True, chat_input=False))
    providers = BuiltProviders(llm=object(), stt=object(), tts=object(), vad=object())

    plan = SessionBuilder().build(config, providers, vad=object(), turn_detector=object())

    assert set(config.resolved) == {"llm"}, "audio slots are never built for a typed session"
    assert plan.text_only
    assert plan.room_options.audio_input is False
    assert plan.room_options.audio_output is False
    assert plan.room_options.text_input is not False
    assert plan.session.stt is None and plan.session.tts is None and plan.session.vad is None
    assert plan.avatar is None and plan.needs_generate_reply_greeting


def test_text_channel_builds_a_realtime_model_in_its_text_modality() -> None:
    config = resolved_config(mode="realtime", channel="text", with_tts=False)

    assert factory_view(config).config.pipeline.mode == "half_cascade"
    assert config.config.pipeline.mode == "realtime", "only the factory sees the text construction"
    assert factory_view(resolved_config()).config.pipeline.mode == "cascaded"


async def test_text_channel_session_runs_end_to_end() -> None:
    api = FakeApi(resolved_config(channel="text", greeting="Hi!"))
    ctx = FakeJobContext(_metadata())
    llm = FakeLLM(["Hi!"])

    class _TextFactory(ProviderFactory):
        def build_all(self, resolved: Any, *, optional: Any = None) -> BuiltProviders:
            assert set(resolved.resolved) == {"llm"}
            return BuiltProviders(llm=llm, tts=FakeTTS())

    await run_session(ctx, _deps(api, factory=_TextFactory()))
    await ctx.fire_shutdown("done")

    assert "session_started" in api.event_types()
    assert api.summaries[0].status == "ended"


# ----------------------------------------------------------- avatar options


def test_avatar_options_become_constructor_kwargs_and_explicit_fields_win() -> None:
    config = _with_slot(resolved_config(), "avatar", "liveavatar-avatar", avatar_id="a1")
    pipeline = config.config.pipeline.model_copy(
        update={"avatar_options": AvatarOptions(participant_name="Ava", video_quality="high")}
    )
    config = config.model_copy(update={"config": config.config.model_copy(update={"pipeline": pipeline})})

    kwargs = prepare_resolved(config).resolved["avatar"].kwargs

    assert kwargs == {"avatar_id": "a1", "avatar_participant_name": "Ava", "video_quality": "high"}

    explicit = _with_slot(config, "avatar", "liveavatar-avatar", avatar_participant_name="Mine")
    assert prepare_resolved(explicit).resolved["avatar"].kwargs["avatar_participant_name"] == "Mine"


def test_unset_avatar_options_are_not_sent() -> None:
    config = _with_slot(resolved_config(), "avatar", "runway-avatar")

    assert prepare_resolved(config).resolved["avatar"].kwargs == {"avatar_participant_name": "Avatar"}


def test_every_avatar_option_kwarg_exists_in_the_plugin_constructor() -> None:
    """AST-verified 1.8.2 signatures: a renamed kwarg would silently fail every avatar start."""
    for provider_id, mapping in AVATAR_OPTION_KWARGS.items():
        params = SIGNATURES[provider_registry.get(provider_id).python_class]["params"]
        for kwarg in mapping.values():
            assert kwarg in params, (provider_id, kwarg)
    for spec in provider_registry.REGISTRY:
        if spec.kind == "avatar" and spec.availability == "available":
            assert "avatar_participant_name" in SIGNATURES[spec.python_class]["params"], spec.id


# ------------------------------------------------ V4-12: async-tool templates, thinking sound


def _plan_for(mode: str, **voice: Any) -> Any:
    config = resolved_config(mode=mode)  # type: ignore[arg-type]
    if voice:
        cfg = config.config.model_copy(update={"voice": config.config.voice.model_copy(update=voice)})
        config = config.model_copy(update={"config": cfg})
    providers = BuiltProviders(llm=object(), realtime=object(), stt=object(), tts=object())
    return SessionBuilder().build(config, providers, vad=object(), turn_detector=object())


@pytest.mark.parametrize("mode", ["cascaded", "half_cascade", "realtime"])
async def test_every_mode_gets_the_voice_safe_async_tool_templates(mode: str) -> None:
    plan = _plan_for(mode)

    options = plan.session._async_tool_options
    assert options == {**options, **ASYNC_TOOL_OPTIONS[mode]}  # type: ignore[index]
    for template in options.values():
        assert "call_id" not in str(template), "no id is ever read aloud"


@pytest.mark.parametrize("mode", ["cascaded", "half_cascade", "realtime"])
def test_the_async_tool_templates_render_with_the_sdk_s_arguments(mode: str) -> None:
    """`voice/tool_executor.py` formats each with a fixed argument set; a stray brace would raise."""
    options = ASYNC_TOOL_OPTIONS[mode]  # type: ignore[index]
    update = options["update_template"].format(function_name="lookup", call_id="c1", message="Fetching.")
    assert "Fetching.\n" in update
    duplicate_args = {"function_name": "lookup", "fnc_calls_json": [], "fnc_calls_text": ""}
    assert "on its way" in options["duplicate_reject_template"].format(**duplicate_args)
    assert "lk_agents_confirm_duplicate" in options["duplicate_confirm_template"].format(**duplicate_args)
    assert options["reply_at_tail_template"].format(call_ids=["c1"])
    assert "nothing at all" in options["reply_maybe_covered_template"].format(call_ids=["c1"])


async def test_the_plan_carries_the_thinking_sound_except_on_the_text_channel() -> None:
    assert _plan_for("cascaded").thinking_sound == "none"
    assert _plan_for("cascaded", thinking_sound="keyboard_typing").thinking_sound == "keyboard_typing"
    text_config = prepare_resolved(resolved_config(channel="text"))
    cfg = text_config.config.model_copy(
        update={"voice": text_config.config.voice.model_copy(update={"thinking_sound": "office_ambience"})}
    )
    text_plan = SessionBuilder().build(
        text_config.model_copy(update={"config": cfg}), BuiltProviders(llm=object())
    )
    assert text_plan.thinking_sound == "none"


def _with_audio_out() -> Any:
    """A session stand-in whose `output.audio` is set (the room's audio output after start)."""
    return SimpleNamespace(output=SimpleNamespace(audio=object()))


class _FakePlayer:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.started: list[dict[str, Any]] = []
        self.closed = 0

    async def start(self, **kwargs: Any) -> None:
        self.started.append(kwargs)

    async def aclose(self) -> None:
        self.closed += 1


async def test_the_thinking_sound_starts_a_player_and_stops_it_at_shutdown() -> None:
    from livekit.agents import BuiltinAudioClip  # noqa: PLC0415

    plan = _plan_for("cascaded", thinking_sound="keyboard_typing2")
    plan = dataclasses.replace(plan, session=_with_audio_out())
    players: list[_FakePlayer] = []

    def _factory(**kwargs: Any) -> _FakePlayer:
        players.append(_FakePlayer(**kwargs))
        return players[-1]

    room = object()
    stop = await start_thinking_sound(plan, room, player_factory=_factory)

    assert stop is not None
    (player,) = players
    clip = player.kwargs["thinking_sound"]
    assert clip.source is BuiltinAudioClip.KEYBOARD_TYPING2
    assert clip.volume == THINKING_SOUND_VOLUME
    assert player.started == [{"room": room, "agent_session": plan.session}]
    assert stop.__code__.co_argcount == 1, "the SDK calls shutdown callbacks with the reason"
    await stop("done")
    assert player.closed == 1


async def test_no_thinking_sound_without_a_setting_or_an_audio_output() -> None:
    def _never(**_kwargs: Any) -> Any:
        raise AssertionError("no player expected")

    assert await start_thinking_sound(_plan_for("cascaded"), object(), player_factory=_never) is None
    silent = _plan_for("cascaded", thinking_sound="keyboard_typing")
    assert silent.session.output.audio is None
    assert await start_thinking_sound(silent, object(), player_factory=_never) is None


async def test_a_thinking_sound_that_fails_to_start_is_skipped() -> None:
    class _Broken(_FakePlayer):
        async def start(self, **kwargs: Any) -> None:
            raise RuntimeError("no track")

    plan = _plan_for("cascaded", thinking_sound="office_ambience")
    plan = dataclasses.replace(plan, session=_with_audio_out())
    broken: list[_Broken] = []

    def _factory(**kwargs: Any) -> _Broken:
        broken.append(_Broken(**kwargs))
        return broken[-1]

    assert await start_thinking_sound(plan, object(), player_factory=_factory) is None
    assert broken[0].closed == 1
