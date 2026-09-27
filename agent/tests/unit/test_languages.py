"""V5-31: languages, the mid-call switch, detection, per-turn language and live captions."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fakes.fake_api import resolved_config
from fakes.fake_ctx import (
    FakeBackgroundRunner,
    FakeFrameBuffer,
    FakeKbClient,
    FakePackSessionContext,
    FakeStructuredLLM,
    FakeUiChannel,
)
from fakes.fake_room import FakeRoom
from fakes.fake_stt import FakeSTT
from fakes.fake_tts import FakeTTS
from livekit import rtc
from livekit.agents import ChatContext, ChatMessage, ToolError, UserInputTranscribedEvent
from livekit.agents.voice.room_io import TextOutputOptions
from lkap_contracts.agent_config import ProviderRef, ResolvedAgentConfig, ResolvedProvider
from lkap_contracts.ui_protocol import TOPIC_UI_CAPTIONS, BlockSpec, CaptionSegment

from lkap_agent.languages import (
    DETECTION_TURNS,
    LANGUAGE_EVENT,
    LANGUAGE_STATE_KEY,
    LANGUAGE_VOICES_USERDATA_KEY,
    SessionLanguages,
    apply_stt_detection,
    ensure_session_languages,
    language_rule,
    reply_note,
    session_languages,
)
from lkap_agent.observability import LANGUAGE_EXTRA_KEY, transcript_from_history
from lkap_agent.packs.loader import NullPack
from lkap_agent.platform_agent import CAPTIONS_USERDATA_KEY, PlatformAgent, SessionContext
from lkap_agent.providers.factory import BuiltProviders
from lkap_agent.session_builder import SessionBuilder, prepare_resolved, with_language_rule
from lkap_agent.tools.builtin import build_builtin_tools
from lkap_agent.tools.builtin.switch_language import build_switch_language_tool
from lkap_agent.ui.channel import (
    CAPTION_INTERIM_INTERVAL_S,
    CaptionsTextOutput,
    CaptionStream,
    UiChannel,
    caption_tap_for,
)

HINDI_VOICE = ResolvedProvider(
    provider_id="sarvam-tts",
    python_class="livekit.plugins.sarvam.TTS",
    model=None,
    kwargs={"speaker": "anushka"},
)


def _multilingual(
    languages: list[str] | None = None,
    *,
    auto_detect: bool = False,
    stt: str = "livekit-inference-stt",
    blocks: list[BlockSpec] | None = None,
    mode: Any = "cascaded",
    channel: str = "web",
) -> ResolvedAgentConfig:
    resolved = resolved_config(mode=mode, channel=channel)
    voice = resolved.config.voice.model_copy(
        update={
            "languages": ["en", "hi"] if languages is None else languages,
            "auto_detect": auto_detect,
            "voices_by_language": {"hi": ProviderRef(provider_id="sarvam-tts")},
        }
    )
    pipeline = resolved.config.pipeline
    if pipeline.stt is not None:
        pipeline = pipeline.model_copy(update={"stt": ProviderRef(provider_id=stt)})
        slots = dict(resolved.resolved)
        slots["stt"] = slots["stt"].model_copy(update={"provider_id": stt})
        resolved = resolved.model_copy(update={"resolved": slots})
    panel = resolved.config.panel.model_copy(update={"blocks": blocks or []})
    config = resolved.config.model_copy(update={"voice": voice, "pipeline": pipeline, "panel": panel})
    return resolved.model_copy(update={"config": config, "voices_by_language": {"hi": HINDI_VOICE}})


class _Factory:
    """Builds a `FakeTTS` per voice and records what it was asked for."""

    def __init__(self) -> None:
        self.built: list[ResolvedProvider] = []

    def build(self, slot: str, provider: ResolvedProvider) -> Any:
        assert slot == "tts"
        self.built.append(provider)
        return FakeTTS()


class _RecordingSTT(FakeSTT):
    """A fake transcriber that records `update_options` calls."""

    def __init__(self) -> None:
        super().__init__()
        self.updates: list[dict[str, Any]] = []

    def update_options(self, **kwargs: Any) -> None:
        self.updates.append(kwargs)


def _context(
    resolved: ResolvedAgentConfig,
    *,
    stt: Any = None,
    tts: Any = None,
    ui: Any = None,
    events: list[tuple[str, dict[str, Any]]] | None = None,
) -> SessionContext:
    session = SimpleNamespace(stt=stt, tts=tts, on=lambda *_a, **_k: None)
    ctx = SessionContext(
        session_id=resolved.session_id,
        agent_id=resolved.agent_id,
        pipeline_mode=resolved.config.pipeline.mode,
        config=resolved.config,
        session=cast(Any, session),
        room=cast(rtc.Room, FakeRoom()),
        ui=ui or FakeUiChannel(),
        frames=FakeFrameBuffer(),
        kb=FakeKbClient(),
        workflow_llm=FakeStructuredLLM(),
        background=FakeBackgroundRunner(),
        log=None,
        record_event=(lambda t, p: events.append((t, p))) if events is not None else (lambda t, p: None),
    )
    ctx.userdata[LANGUAGE_VOICES_USERDATA_KEY] = dict(resolved.voices_by_language)
    return ctx


def _agent(ctx: SessionContext, factory: _Factory | None = None) -> PlatformAgent:
    state = ensure_session_languages(ctx, factory=factory or _Factory())
    del state
    return PlatformAgent(ctx=ctx, pack=NullPack(), has_tts=True)


# ------------------------------------------------------------------ compatibility


def test_one_language_resolves_to_todays_behaviour() -> None:
    resolved = resolved_config()
    assert with_language_rule(resolved.config) == resolved.config
    prepared = prepare_resolved(resolved)
    assert prepared.config.instructions == resolved.config.instructions
    assert prepared.resolved["stt"].kwargs == resolved.resolved["stt"].kwargs
    ctx = _context(resolved)
    assert ensure_session_languages(ctx) is None
    tools = build_builtin_tools(
        FakePackSessionContext(config=resolved.config), disabled=[], http_enabled=False
    )
    assert "switch_language" not in {t.info.name for t in tools}


def test_two_languages_register_the_tool() -> None:
    resolved = _multilingual()
    tools = build_builtin_tools(
        FakePackSessionContext(config=resolved.config), disabled=[], http_enabled=False
    )
    assert "switch_language" in {t.info.name for t in tools}
    disabled = build_builtin_tools(
        FakePackSessionContext(config=resolved.config), disabled=["switch_language"], http_enabled=False
    )
    assert "switch_language" not in {t.info.name for t in disabled}


# ------------------------------------------------------------------------ prompt


def test_language_rule_is_fixed_and_names_the_script() -> None:
    rule = language_rule(["en", "hi"], auto_detect=False)
    assert rule == language_rule(["en", "hi"], auto_detect=False)
    assert "start in English (en)" in rule
    assert "Hindi (hi)" in rule
    assert "switch_language" in rule
    assert "Devanagari" in rule
    assert language_rule(["en"], auto_detect=False) == ""


def test_prepare_resolved_appends_the_rule_to_the_instructions() -> None:
    resolved = _multilingual()
    prepared = prepare_resolved(resolved)
    assert prepared.config.instructions.startswith(resolved.config.instructions)
    assert prepared.config.instructions.endswith(language_rule(["en", "hi"], auto_detect=False))


def test_reply_note() -> None:
    assert reply_note("hi") == (
        "Language: from now on reply in Hindi (hi), written in Devanagari script so the voice pronounces it."
    )
    assert reply_note("fr") == "Language: from now on reply in French (fr)."


# --------------------------------------------------------------------- detection


def test_auto_detect_builds_the_stt_with_multi() -> None:
    resolved = _multilingual(auto_detect=True)
    slots = dict(resolved.resolved)
    assert apply_stt_detection(resolved, slots) is True
    assert slots["stt"].kwargs["language"] == "multi"
    assert resolved.resolved["stt"].kwargs["language"] == "en"  # the input is not changed
    assert prepare_resolved(resolved).resolved["stt"].kwargs["language"] == "multi"


def test_auto_detect_uses_the_registry_value_per_provider() -> None:
    resolved = _multilingual(auto_detect=True, stt="sarvam-stt")
    slots = dict(resolved.resolved)
    apply_stt_detection(resolved, slots)
    assert slots["stt"].kwargs["language"] == "unknown"


def test_auto_detect_on_a_provider_without_detection_changes_nothing() -> None:
    resolved = _multilingual(auto_detect=True, stt="speechmatics-stt")
    slots = dict(resolved.resolved)
    assert apply_stt_detection(resolved, slots) is False
    assert slots["stt"].kwargs == resolved.resolved["stt"].kwargs


def test_detection_needs_consecutive_turns() -> None:
    state = SessionLanguages(
        allowed=["en", "hi"], current="en", auto_detect=True, switch_stt=False, stt_class=None
    )
    long_text = "mujhe apni policy ke baare mein jaanna hai"
    for turn in range(DETECTION_TURNS):
        state.vote("hi", long_text)
        language, due = state.close_turn(long_text)
        assert language == "hi"
        assert due == ("hi" if turn == DETECTION_TURNS - 1 else None)


def test_detection_resets_on_a_turn_in_the_current_language_and_ignores_short_or_unlisted() -> None:
    state = SessionLanguages(
        allowed=["en", "hi"], current="en", auto_detect=True, switch_stt=False, stt_class=None
    )
    text = "kya aap meri madad kar sakte hain"
    state.vote("hi", text)
    assert state.close_turn(text) == ("hi", None)
    state.vote("en", "yes that is right thank you")
    assert state.close_turn("yes that is right thank you") == ("en", None)
    state.vote("hi", text)
    assert state.close_turn(text) == ("hi", None)  # the streak restarted
    state.vote("hi", "haan")
    assert state.close_turn("haan") == ("hi", None)  # too short to count
    state.vote("fr", "je voudrais parler")
    assert state.close_turn("je voudrais parler") == ("fr", None)  # not an allowed language


def test_detection_picks_the_turn_language_by_characters() -> None:
    state = SessionLanguages(
        allowed=["en", "hi"], current="en", auto_detect=False, switch_stt=False, stt_class=None
    )
    state.vote("en", "ok")
    state.vote("hi-IN", "meri gaadi ka accident ho gaya hai")
    assert state.close_turn("ok meri gaadi ka accident ho gaya hai") == ("hi", None)


@pytest.mark.parametrize(
    ("requested", "code"), [("hi", "hi"), ("hi-IN", "hi"), ("Hindi", "hi"), ("EN", "en")]
)
def test_match(requested: str, code: str) -> None:
    state = SessionLanguages(
        allowed=["en", "hi"], current="en", auto_detect=False, switch_stt=True, stt_class=None
    )
    assert state.match(requested) == code
    assert state.match("fr") is None


# ------------------------------------------------------------------------ switch


async def test_switch_updates_the_stt_swaps_the_voice_and_keeps_the_chat_context() -> None:
    resolved = _multilingual()
    stt, pipeline_tts = _RecordingSTT(), FakeTTS()
    events: list[tuple[str, dict[str, Any]]] = []
    ctx = _context(resolved, stt=stt, tts=pipeline_tts, events=events)
    factory = _Factory()
    agent = _agent(ctx, factory)
    history = ChatContext.empty()
    history.add_message(role="user", content="Can we speak in Hindi?")
    await agent.update_chat_ctx(history)
    before = [item.id for item in agent.chat_ctx.items]

    result = await agent.switch_language("hi")

    assert result is not None and result.changed
    assert stt.updates == [{"language": "hi"}]
    assert factory.built == [HINDI_VOICE]
    assert isinstance(agent.tts, FakeTTS) and agent.tts is not pipeline_tts
    assert [item.id for item in agent.chat_ctx.items] == before
    assert session_languages(ctx) is not None and session_languages(ctx).current == "hi"  # type: ignore[union-attr]
    assert events == [
        (
            LANGUAGE_EVENT,
            {
                "from_language": "en",
                "to_language": "hi",
                "source": "tool",
                "stt_switched": True,
                "voice_switched": True,
            },
        )
    ]

    back = await agent.switch_language("en")
    assert back is not None and back.voice_switched
    assert agent.tts is pipeline_tts
    assert stt.updates[-1] == {"language": "en"}


async def test_switch_to_the_current_language_changes_nothing() -> None:
    resolved = _multilingual()
    stt = _RecordingSTT()
    ctx = _context(resolved, stt=stt, tts=FakeTTS())
    agent = _agent(ctx)
    result = await agent.switch_language("en")
    assert result is not None and not result.changed
    assert stt.updates == []


async def test_auto_detect_never_pins_the_transcriber() -> None:
    resolved = _multilingual(auto_detect=True)
    stt = _RecordingSTT()
    ctx = _context(resolved, stt=stt, tts=FakeTTS())
    agent = _agent(ctx)
    result = await agent.switch_language("hi")
    assert result is not None and not result.stt_switched and result.voice_switched
    assert stt.updates == []


async def test_a_provider_that_cannot_switch_keeps_its_language() -> None:
    resolved = _multilingual(stt="speechmatics-stt")
    stt = _RecordingSTT()
    ctx = _context(resolved, stt=stt, tts=FakeTTS())
    agent = _agent(ctx)
    result = await agent.switch_language("hi")
    assert result is not None and not result.stt_switched
    assert stt.updates == []


async def test_the_openai_transcriber_gets_a_two_letter_code() -> None:
    resolved = _multilingual(["en", "hi-IN"], stt="openrouter-stt")
    stt = _RecordingSTT()
    ctx = _context(resolved, stt=stt, tts=FakeTTS())
    agent = _agent(ctx)
    await agent.switch_language("hi-IN")
    assert stt.updates == [{"language": "hi"}]


async def test_realtime_has_nothing_to_swap() -> None:
    resolved = _multilingual(mode="realtime")
    ctx = _context(resolved)
    agent = _agent(ctx)
    result = await agent.switch_language("hi")
    assert result is not None and result.changed
    assert not result.stt_switched and not result.voice_switched


async def test_detected_switch_notes_the_reply_language_in_the_turn() -> None:
    resolved = _multilingual(auto_detect=True)
    ctx = _context(resolved, stt=_RecordingSTT(), tts=FakeTTS())
    agent = _agent(ctx)
    text = "mujhe claim ke baare mein poochna hai"
    for _ in range(DETECTION_TURNS):
        agent.on_user_input_transcribed(
            UserInputTranscribedEvent(transcript=text, is_final=True, language="hi")
        )
        turn_ctx = ChatContext.empty()
        message = ChatMessage(role="user", content=[text])
        await agent.on_user_turn_completed(turn_ctx, message)
        assert message.extra[LANGUAGE_EXTRA_KEY] == "hi"
    assert session_languages(ctx).current == "hi"  # type: ignore[union-attr]
    notes = [item for item in turn_ctx.items if isinstance(item, ChatMessage) and item.role == "system"]
    assert notes and notes[-1].text_content == reply_note("hi")
    assert agent.chat_ctx.items[-1].text_content == reply_note("hi")  # type: ignore[union-attr]


# -------------------------------------------------------------------------- tool


def _run_ctx(session: Any) -> Any:
    return SimpleNamespace(session=session)


async def test_tool_switches_through_the_current_agent() -> None:
    resolved = _multilingual()
    ctx = _context(resolved, stt=_RecordingSTT(), tts=FakeTTS())
    agent = _agent(ctx)
    tool = build_switch_language_tool(ctx)
    assert "Hindi (hi)" in (tool.info.description or "")
    answer = await tool(context=_run_ctx(SimpleNamespace(current_agent=agent)), language="hindi")
    assert "Switched to Hindi (hi)" in answer


async def test_tool_refuses_a_language_that_is_not_configured() -> None:
    resolved = _multilingual()
    ctx = _context(resolved, stt=_RecordingSTT(), tts=FakeTTS())
    agent = _agent(ctx)
    tool = build_switch_language_tool(ctx)
    with pytest.raises(ToolError, match="not one of your languages"):
        await tool(context=_run_ctx(SimpleNamespace(current_agent=agent)), language="fr")


# ------------------------------------------------------------------ transcript


def test_transcript_rows_carry_the_turn_language() -> None:
    history = ChatContext.empty()
    user = history.add_message(role="user", content="namaste")
    user.extra[LANGUAGE_EXTRA_KEY] = "hi"
    history.add_message(role="assistant", content="Namaste!")
    turns = transcript_from_history(history)
    assert [t.language for t in turns] == ["hi", None]


# --------------------------------------------------------------------- captions


def _captions_block(**config: Any) -> BlockSpec:
    return BlockSpec(id="cap", type="captions", config=config)


class _Sent:
    def __init__(self) -> None:
        self.segments: list[CaptionSegment] = []

    async def __call__(self, segment: CaptionSegment) -> None:
        self.segments.append(segment)


async def test_caption_stream_user_interim_then_final_share_an_id() -> None:
    sent = _Sent()
    stream = CaptionStream(sent)
    stream.user("mujhe", final=False, language="hi")
    stream.user("mujhe madad chahiye", final=True, language="hi")
    stream.user("next", final=False)
    await stream.drain()
    first, second, third = sent.segments
    assert (first.speaker, first.final, first.language) == ("user", False, "hi")
    assert second.id == first.id and second.final and second.text == "mujhe madad chahiye"
    assert third.id != first.id


async def test_caption_stream_agent_interims_are_throttled_and_the_final_always_goes() -> None:
    sent = _Sent()
    now = [100.0]
    stream = CaptionStream(sent, agent_language=lambda: "hi", clock=lambda: now[0])
    stream.agent_delta("Namaste, ")
    stream.agent_delta("main ")  # within the interval: not sent
    now[0] += CAPTION_INTERIM_INTERVAL_S
    stream.agent_delta("aapki madad karungi.")
    stream.agent_flush()
    await stream.drain()
    assert [(s.text, s.final) for s in sent.segments] == [
        ("Namaste,", False),
        ("Namaste, main aapki madad karungi.", False),
        ("Namaste, main aapki madad karungi.", True),
    ]
    assert {s.id for s in sent.segments} == {sent.segments[0].id}
    assert {s.language for s in sent.segments} == {"hi"}


async def test_caption_stream_honours_show_flags() -> None:
    sent = _Sent()
    stream = CaptionStream(sent, show_user=False, show_agent=False)
    stream.user("hello", final=True)
    stream.agent_delta("hi")
    stream.agent_flush()
    await stream.drain()
    assert sent.segments == []


async def test_captions_tap_feeds_the_bound_stream() -> None:
    sent = _Sent()
    tap = CaptionsTextOutput()
    await tap.capture_text("ignored")  # not bound yet
    tap.bind(CaptionStream(sent))
    await tap.capture_text("Hello")
    tap.flush()
    await tap._stream.drain()  # type: ignore[union-attr]
    assert [(s.text, s.final) for s in sent.segments] == [("Hello", False), ("Hello", True)]


async def test_ui_channel_sends_captions_on_their_topic() -> None:
    room = FakeRoom()
    channel = UiChannel(room, "sess-1")  # type: ignore[arg-type]
    await channel.caption(
        CaptionSegment(id="a-1", speaker="agent", text="Hi", final=True, language="en", ts=1.0)
    )
    (message,) = room.local_participant.sent_text
    assert message.topic == TOPIC_UI_CAPTIONS
    assert json.loads(message.text)["speaker"] == "agent"
    assert channel.state.blocks == {}  # captions are never stored


async def test_session_builder_adds_the_tap_only_with_a_captions_block() -> None:
    providers = BuiltProviders(stt=FakeSTT(), llm=object(), tts=FakeTTS())
    plain = SessionBuilder().build(resolved_config(), providers)
    assert plain.captions_tap is None
    with_block = _multilingual(blocks=[_captions_block()])
    plan = SessionBuilder().build(with_block, providers)
    assert plan.captions_tap is not None
    assert isinstance(plan.room_options.text_output, TextOutputOptions)
    assert plan.room_options.text_output.next_in_chain is plan.captions_tap
    assert caption_tap_for(plan.session) is plan.captions_tap
    text = SessionBuilder().build(
        _multilingual(blocks=[_captions_block()], channel="text"), BuiltProviders(llm=object())
    )
    assert text.captions_tap is None


async def test_agent_streams_captions_and_seeds_the_block_language() -> None:
    resolved = _multilingual(blocks=[_captions_block(show_agent=False)])
    room = FakeRoom()
    ui = UiChannel(room, resolved.session_id)  # type: ignore[arg-type]
    ctx = _context(resolved, stt=_RecordingSTT(), tts=FakeTTS(), ui=ui)
    agent = _agent(ctx)
    assert ui.state.blocks["cap"] == {"language": "en", "target_language": None}
    stream = agent.caption_stream()
    assert stream is not None and stream.show_user and not stream.show_agent
    assert ctx.userdata[CAPTIONS_USERDATA_KEY] is stream
    agent.on_user_input_transcribed(
        UserInputTranscribedEvent(transcript="namaste", is_final=True, language="hi")
    )
    await stream.drain()
    captions = [m for m in room.local_participant.sent_text if m.topic == TOPIC_UI_CAPTIONS]
    assert json.loads(captions[0].text)["language"] == "hi"
    await agent.switch_language("hi")
    assert ui.state.blocks["cap"]["language"] == "hi"


async def test_no_captions_without_a_block_or_on_the_text_channel() -> None:
    resolved = _multilingual(blocks=[])
    ctx = _context(resolved)
    agent = _agent(ctx)
    assert agent.caption_stream() is None
    text = _multilingual(blocks=[_captions_block()], channel="text")
    text_ctx = _context(text)
    text_ctx.channel = "text"
    assert _agent(text_ctx).caption_stream() is None


def test_state_lives_in_userdata_once() -> None:
    resolved = _multilingual()
    ctx = _context(resolved)
    first = ensure_session_languages(ctx)
    assert first is ctx.userdata[LANGUAGE_STATE_KEY]
    assert ensure_session_languages(ctx) is first


# ------------------------------------------------------------------ observer


async def test_observer_stamps_the_heard_language_on_the_user_turn() -> None:
    from fakes.fake_api import FakeApi
    from livekit.agents import ConversationItemAddedEvent

    from lkap_agent.observability import SessionObserver

    observer = SessionObserver(session_id="sess-1", client=cast(Any, FakeApi()))
    observer._on_transcribed(UserInputTranscribedEvent(transcript="namaste", is_final=True, language="hi"))
    user = ChatMessage(role="user", content=["namaste"])
    observer._on_conversation_item(ConversationItemAddedEvent(item=user))
    assistant = ChatMessage(role="assistant", content=["Namaste!"], extra={LANGUAGE_EXTRA_KEY: "hi"})
    observer._on_conversation_item(ConversationItemAddedEvent(item=assistant))
    observer._on_transcribed(UserInputTranscribedEvent(transcript="ok", is_final=True, language="multi"))
    plain = ChatMessage(role="user", content=["ok"])
    observer._on_conversation_item(ConversationItemAddedEvent(item=plain))
    payloads = [(e.type, e.payload.get("language")) for e in observer._buffer]
    assert payloads == [("user_turn", "hi"), ("agent_turn", "hi"), ("user_turn", None)]
    assert user.extra[LANGUAGE_EXTRA_KEY] == "hi"
    if observer._flush_task is not None:
        observer._flush_task.cancel()


# --------------------------------------------------------- SDK surface (tripwires)


def test_sdk_surfaces_this_package_relies_on() -> None:
    """livekit-agents 1.8.3: the facts V5-31 is built on. A failure means the SDK moved."""
    import inspect

    from livekit.agents import Agent

    parameters = inspect.signature(Agent.update_options).parameters
    assert {"stt", "tts"} <= set(parameters), "Agent.update_options lost stt/tts: the voice swap breaks"
    assert "language" in UserInputTranscribedEvent.model_fields, "no detected language on transcripts"
    assert "extra" in ChatMessage.model_fields, "ChatMessage.extra carries the turn language"
    assert "next_in_chain" in inspect.signature(TextOutputOptions).parameters, "captions tap has no seam"
    # `languages._running` falls back to the session's own components.
    from livekit.agents import AgentSession

    assert isinstance(AgentSession.__dict__.get("tts"), property), "AgentSession.tts is gone"
    assert isinstance(AgentSession.__dict__.get("stt"), property), "AgentSession.stt is gone"
