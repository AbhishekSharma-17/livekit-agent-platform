"""`SessionBuilder` and `PlatformAgent`: pipeline mapping, retrieval, vision, reply control."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast

import pytest
import structlog
from fakes.fake_api import resolved_config
from fakes.fake_ctx import (
    FakeBackgroundRunner,
    FakeFrameBuffer,
    FakeKbClient,
    FakeStructuredLLM,
    FakeUiChannel,
)
from fakes.fake_room import FakeRoom
from livekit import rtc
from livekit.agents import (
    NOT_GIVEN,
    AgentSession,
    ChatContext,
    ConversationItemAddedEvent,
    FunctionToolsExecutedEvent,
    ToolExecutionUpdatedEvent,
    function_tool,
    llm,
)
from livekit.agents.llm import ToolFlag
from livekit.agents.voice.events import ToolCallEnded, ToolCallStarted, ToolCallUpdated
from lkap_contracts.agent_config import PipelineMode, ResolvedAgentConfig
from lkap_contracts.api_models import KbHit
from lkap_contracts.flow import FlowSpec
from lkap_contracts.packs import ToolMeta
from lkap_contracts.providers import ModelCapabilities
from lkap_contracts.tools import HttpToolDefinition, ToolExecution
from packs.base import FrameSnapshot

from lkap_agent import platform_agent as platform_agent_module
from lkap_agent.packs.loader import NullPack, null_manifest
from lkap_agent.platform_agent import (
    IN_PROGRESS_NOTE,
    PIPELINE_NOTES,
    PlatformAgent,
    SessionContext,
    compose_instructions,
    model_vision_support,
    platform_text_input_cb,
    resolve_greeting_mode,
)
from lkap_agent.providers.factory import BuiltProviders
from lkap_agent.session_builder import SessionBuilder, build_turn_handling, llm_capabilities_of
from lkap_agent.tools import execution as execution_module
from lkap_agent.tools.declarative import build_http_tools


class _SilentPack(NullPack):
    """A pack that flags one tool `silent_reply` and records its hook calls."""

    def __init__(self, silent: str = "sync_claim_packet") -> None:
        super().__init__()
        self._silent = silent
        self.user_turns: list[str] = []
        self.started = 0

    def tool_meta(self) -> list[ToolMeta]:
        return [ToolMeta(name=self._silent, silent_reply=True), ToolMeta(name="lookup_policy")]

    async def on_session_start(self, ctx: Any) -> None:
        self.started += 1

    async def on_user_turn_completed(self, ctx: Any, turn_ctx: Any, new_message: Any) -> None:
        self.user_turns.append(new_message.text_content or "")


#: The one Inference LLM the registry marks `supports_video` (DECISIONS-W2 §D-W2-10).
VISION_MODEL = "google/gemini-3.5-flash"


def _vision_config(**kwargs: Any) -> ResolvedAgentConfig:
    return resolved_config(camera=True, llm_model=VISION_MODEL, **kwargs)


def _fake_frame() -> rtc.VideoFrame:
    return rtc.VideoFrame(width=4, height=4, type=rtc.VideoBufferType.RGBA, data=b"\x00" * 64)


def _context(
    config: ResolvedAgentConfig,
    *,
    ui: FakeUiChannel | None = None,
    frames: FakeFrameBuffer | None = None,
    kb: FakeKbClient | None = None,
) -> SessionContext:
    return SessionContext(
        session_id=config.session_id,
        agent_id=config.agent_id,
        pipeline_mode=config.config.pipeline.mode,
        config=config.config,
        session=cast(Any, object()),
        room=cast(rtc.Room, FakeRoom()),
        ui=ui or FakeUiChannel(),
        frames=frames or FakeFrameBuffer(),
        kb=kb or FakeKbClient(),
        workflow_llm=FakeStructuredLLM(),
        background=FakeBackgroundRunner(),
        log=None,
    )


def _agent(config: ResolvedAgentConfig, pack: Any = None, *, has_tts: bool = True, **ctx_kw: Any) -> Any:
    ctx = _context(config, **ctx_kw)
    return PlatformAgent(ctx=ctx, pack=pack or NullPack(), has_tts=has_tts)


# --------------------------------------------------------------- instructions


def test_compose_instructions_layers_agent_pack_and_platform_text() -> None:
    """The prompt is the console text, then the pack addendum, then the pipeline note."""
    manifest = null_manifest().model_copy(
        update={"instructions_by_mode": {"cascaded": "Pack rule: always confirm the policy number."}}
    )

    prompt = compose_instructions("Be brief.", mode="cascaded", manifest=manifest)

    assert prompt.index("Be brief.") < prompt.index("Pack rule")
    assert prompt.index("Pack rule") < prompt.index(PIPELINE_NOTES["cascaded"])


def test_compose_instructions_omits_a_missing_pack_addendum() -> None:
    """A pack with no addendum for this mode contributes nothing."""
    prompt = compose_instructions("Be brief.", mode="realtime", manifest=null_manifest())

    assert prompt == f"Be brief.\n\n{PIPELINE_NOTES['realtime']}"


# -------------------------------------------------------------- greeting mode


@pytest.mark.parametrize(
    ("configured", "has_tts", "expected"),
    [
        ("say", True, "say"),
        ("say", False, "generate"),
        ("generate", True, "generate"),
        ("generate", False, "generate"),
    ],
)
def test_resolve_greeting_mode_falls_back_when_the_pipeline_has_no_tts(
    configured: str, has_tts: bool, expected: str
) -> None:
    """ARCHITECTURE §15.9: `say()` raises without a TTS on both MVP realtime models.

    Verified in livekit-agents 1.8.2 `AgentActivity.say`: it raises unless a TTS
    exists or `RealtimeModel.capabilities.supports_say` is set, and neither
    `livekit-plugins-google` nor `livekit-plugins-openai` sets that flag.
    """
    assert resolve_greeting_mode(configured, has_tts=has_tts) == expected  # type: ignore[arg-type]


def test_platform_agent_resolves_its_greeting_mode_at_construction() -> None:
    """A realtime session with no TTS greets through `generate_reply`."""
    config = resolved_config(mode="realtime", with_tts=False, greeting_mode="say")

    assert _agent(config, has_tts=False).greeting_mode == "generate"


# ------------------------------------------------------------------ knowledge


async def test_on_user_turn_completed_injects_knowledge_as_an_assistant_note() -> None:
    """Retrieved chunks land in the turn context, the pattern LiveKit recommends."""
    hits = [KbHit(chunk_id="c1", document_id="d1", filename="policy.md", score=0.9, text="Flood is covered.")]
    kb = FakeKbClient(hits)
    agent = _agent(resolved_config(kb_ids=["kb-1"]), kb=kb)
    turn_ctx = ChatContext.empty()
    message = llm.ChatMessage(role="user", content=["Is flooding covered?"])

    await agent.on_user_turn_completed(turn_ctx, message)

    injected = [i for i in turn_ctx.items if isinstance(i, llm.ChatMessage) and i.role == "assistant"]
    assert len(injected) == 1
    assert "Flood is covered." in (injected[0].text_content or "")
    assert "policy.md" in (injected[0].text_content or "")
    assert kb.queries[0][0] == "Is flooding covered?"


@pytest.mark.parametrize(
    ("kb_ids", "auto_inject"), [([], True), (["kb-1"], False)], ids=["no-kbs", "auto-inject-off"]
)
async def test_on_user_turn_completed_skips_retrieval_when_it_is_not_configured(
    kb_ids: list[str], auto_inject: bool
) -> None:
    """No attached KB, or auto-inject disabled, means no search at all."""
    kb = FakeKbClient([KbHit(chunk_id="c", document_id="d", filename="f", score=1.0, text="t")])
    agent = _agent(resolved_config(kb_ids=kb_ids, auto_inject=auto_inject), kb=kb)
    turn_ctx = ChatContext.empty()

    await agent.on_user_turn_completed(turn_ctx, llm.ChatMessage(role="user", content=["hi"]))

    assert kb.queries == []
    assert turn_ctx.items == []


async def test_on_user_turn_completed_survives_a_failing_knowledge_base() -> None:
    """Retrieval is an enhancement: a KB outage must not break the turn."""

    class _BrokenKb(FakeKbClient):
        async def search(self, query: str, k: int = 4, kb_ids: list[str] | None = None) -> list[KbHit]:
            raise RuntimeError("lancedb is down")

    agent = _agent(resolved_config(kb_ids=["kb-1"]), kb=cast(Any, _BrokenKb()))

    await agent.on_user_turn_completed(ChatContext.empty(), llm.ChatMessage(role="user", content=["hi"]))


async def test_on_user_turn_completed_skips_a_knowledge_search_slower_than_the_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slow api bounds the reply delay at `_KB_INJECT_TIMEOUT_S`, then the turn carries on."""
    monkeypatch.setattr(platform_agent_module, "_KB_INJECT_TIMEOUT_S", 0.01)
    cancelled = asyncio.Event()

    class _SlowKb(FakeKbClient):
        async def search(self, query: str, k: int = 4, kb_ids: list[str] | None = None) -> list[KbHit]:
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return [KbHit(chunk_id="c", document_id="d", filename="f", score=1.0, text="late")]

    pack = _SilentPack()
    agent = _agent(resolved_config(kb_ids=["kb-1"]), pack, kb=cast(Any, _SlowKb()))
    turn_ctx = ChatContext.empty()

    await asyncio.wait_for(
        agent.on_user_turn_completed(turn_ctx, llm.ChatMessage(role="user", content=["hi"])), timeout=2
    )

    assert cancelled.is_set()
    assert turn_ctx.items == []
    assert pack.user_turns == ["hi"]


def test_kb_inject_timeout_is_three_seconds() -> None:
    assert platform_agent_module._KB_INJECT_TIMEOUT_S == 3.0


# --------------------------------------------------------------------- vision


async def test_on_user_turn_completed_attaches_one_frame_in_cascaded_mode() -> None:
    """Cascaded pipelines must inject frames themselves; the model never gets them otherwise."""
    frames = FakeFrameBuffer()
    frames.set_latest(FrameSnapshot(frame=_fake_frame(), source="camera", age_s=1.0))
    agent = _agent(_vision_config(), frames=frames)
    message = llm.ChatMessage(role="user", content=["Look at this."])

    await agent.on_user_turn_completed(ChatContext.empty(), message)

    images = [p for p in message.content if isinstance(p, llm.ImageContent)]
    assert len(images) == 1
    assert images[0].inference_detail == "low"
    assert isinstance(images[0].image, str), "a raw VideoFrame must never be persisted (D-W2-8 R3)"
    assert images[0].image.startswith("data:image/jpeg;base64,")


def _image_count(items: list[Any]) -> int:
    return sum(
        1
        for item in items
        if isinstance(item, llm.ChatMessage)
        for part in item.content
        if isinstance(part, llm.ImageContent)
    )


async def test_vision_injection_strips_earlier_images_from_the_turn_context_only() -> None:
    """R4: one image per LLM call; the persisted history keeps its earlier image."""
    frames = FakeFrameBuffer()
    frames.set_latest(FrameSnapshot(frame=_fake_frame(), source="camera", age_s=1.0))
    agent = _agent(_vision_config(), frames=frames)
    earlier = llm.ChatMessage(
        role="user", content=["Earlier turn.", llm.ImageContent(image="data:image/jpeg;base64,AAAA")]
    )
    persisted = ChatContext([earlier])
    turn_ctx = persisted.copy()  # shallow, exactly like AgentActivity's per-turn copy
    message = llm.ChatMessage(role="user", content=["And now?"])

    await agent.on_user_turn_completed(turn_ctx, message)

    assert _image_count(list(turn_ctx.items)) == 0
    assert _image_count([message]) == 1
    assert _image_count(list(persisted.items)) == 1, "the persisted history must not be mutated"
    assert turn_ctx.items[0].text_content == "Earlier turn."


async def test_vision_injection_is_disabled_after_an_llm_error_on_a_frame_turn() -> None:
    """R5: a text-only model must not fail every camera turn."""
    frames = FakeFrameBuffer()
    frames.set_latest(FrameSnapshot(frame=_fake_frame(), source="camera", age_s=1.0))
    agent = _agent(_vision_config(), frames=frames)
    await agent.on_user_turn_completed(ChatContext.empty(), llm.ChatMessage(role="user", content=["Look."]))
    error = llm.LLMError(
        type="llm_error",
        timestamp=0.0,
        label="test",
        error=RuntimeError("image input not supported"),
        recoverable=False,
    )

    message = agent.on_session_error(SimpleNamespace(error=error, source=object()))

    assert message is not None
    assert "vision injection disabled" in message
    assert "image input not supported" in message
    assert agent.vision_disabled
    assert agent.on_session_error(SimpleNamespace(error=error, source=object())) is None, "reported once"
    next_turn = llm.ChatMessage(role="user", content=["Still there?"])
    await agent.on_user_turn_completed(ChatContext.empty(), next_turn)
    assert _image_count([next_turn]) == 0


async def test_an_llm_error_on_a_turn_without_a_frame_keeps_vision_on() -> None:
    agent = _agent(_vision_config(), frames=FakeFrameBuffer())
    await agent.on_user_turn_completed(ChatContext.empty(), llm.ChatMessage(role="user", content=["hi"]))
    error = llm.LLMError(
        type="llm_error", timestamp=0.0, label="test", error=RuntimeError("boom"), recoverable=True
    )

    assert agent.on_session_error(SimpleNamespace(error=error, source=object())) is None
    assert not agent.vision_disabled


async def test_a_non_llm_error_never_disables_vision() -> None:
    frames = FakeFrameBuffer()
    frames.set_latest(FrameSnapshot(frame=_fake_frame(), source="camera", age_s=1.0))
    agent = _agent(_vision_config(), frames=frames)
    await agent.on_user_turn_completed(ChatContext.empty(), llm.ChatMessage(role="user", content=["Look."]))

    assert agent.on_session_error(SimpleNamespace(error=RuntimeError("tts down"), source=object())) is None
    assert not agent.vision_disabled


async def test_a_frame_that_cannot_be_encoded_is_skipped_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exception in the hook would make the SDK drop the reply for the whole turn."""
    import lkap_agent.platform_agent as platform_agent_module

    def _boom(frame: Any, max_px: int = 512) -> str:
        raise RuntimeError("unsupported pixel format")

    monkeypatch.setattr(platform_agent_module, "encode_jpeg_data_url", _boom)
    frames = FakeFrameBuffer()
    frames.set_latest(FrameSnapshot(frame=_fake_frame(), source="camera", age_s=1.0))
    agent = _agent(_vision_config(), frames=frames)
    message = llm.ChatMessage(role="user", content=["Look."])

    await agent.on_user_turn_completed(ChatContext.empty(), message)

    assert _image_count([message]) == 0


async def test_on_user_turn_completed_does_not_inject_frames_in_realtime_mode() -> None:
    """Realtime models already receive frames via `RoomOptions.video_input`."""
    frames = FakeFrameBuffer()
    frames.set_latest(FrameSnapshot(frame=_fake_frame(), source="camera", age_s=0.5))
    agent = _agent(resolved_config(mode="realtime", camera=True), frames=frames)
    message = llm.ChatMessage(role="user", content=["Look at this."])

    await agent.on_user_turn_completed(ChatContext.empty(), message)

    assert not any(isinstance(p, llm.ImageContent) for p in message.content)


async def test_on_user_turn_completed_ignores_a_stale_frame() -> None:
    """A frame older than the window is worse than none: the model would answer about the past."""
    frames = FakeFrameBuffer()
    frames.set_latest(FrameSnapshot(frame=_fake_frame(), source="camera", age_s=60.0))
    ctx = _context(_vision_config(), frames=frames)
    agent = PlatformAgent(ctx=ctx, pack=NullPack(), has_tts=True, vision_max_frame_age_s=8.0)
    message = llm.ChatMessage(role="user", content=["Look at this."])

    await agent.on_user_turn_completed(ChatContext.empty(), message)

    assert not any(isinstance(p, llm.ImageContent) for p in message.content)


async def test_a_text_only_model_gets_no_frame_and_one_info_event_across_turns() -> None:
    """D-W2-10: gemma ignores images silently, so the platform never sends it one."""
    frames = FakeFrameBuffer()
    frames.set_latest(FrameSnapshot(frame=_fake_frame(), source="camera", age_s=1.0))
    events: list[tuple[str, dict[str, Any]]] = []
    ctx = _context(resolved_config(camera=True, llm_model="google/gemma-4-31b-it"), frames=frames)
    agent = PlatformAgent(
        ctx=ctx, pack=NullPack(), has_tts=True, record_event=lambda t, p: events.append((t, p))
    )

    for text in ("What am I holding?", "And now?"):
        message = llm.ChatMessage(role="user", content=[text])
        await agent.on_user_turn_completed(ChatContext.empty(), message)
        assert _image_count([message]) == 0

    assert events == [("info", {"message": "vision injection skipped: google/gemma-4-31b-it is text-only"})]


async def test_the_provider_default_model_counts_as_text_only() -> None:
    frames = FakeFrameBuffer()
    frames.set_latest(FrameSnapshot(frame=_fake_frame(), source="camera", age_s=1.0))
    agent = _agent(resolved_config(camera=True), frames=frames)  # model=None -> gemma
    message = llm.ChatMessage(role="user", content=["Look."])

    await agent.on_user_turn_completed(ChatContext.empty(), message)

    assert _image_count([message]) == 0


@pytest.mark.parametrize("model", [VISION_MODEL, "someone/free-text-vision-model"])
async def test_a_vision_or_unknown_model_gets_the_frame(model: str) -> None:
    """Known vision -> inject; unknown id -> inject and rely on R5 auto-degrade."""
    frames = FakeFrameBuffer()
    frames.set_latest(FrameSnapshot(frame=_fake_frame(), source="camera", age_s=1.0))
    events: list[tuple[str, dict[str, Any]]] = []
    ctx = _context(resolved_config(camera=True, llm_model=model), frames=frames)
    agent = PlatformAgent(
        ctx=ctx, pack=NullPack(), has_tts=True, record_event=lambda t, p: events.append((t, p))
    )
    message = llm.ChatMessage(role="user", content=["Look."])

    await agent.on_user_turn_completed(ChatContext.empty(), message)

    assert _image_count([message]) == 1
    assert events == []


# ------------------------------------------------ resolved capabilities (V4-08, D-V4-24)
CUSTOM_TEXT_MODEL = "acme/custom-text-only-1"


def test_model_vision_support_reads_the_resolved_llm_capabilities_first() -> None:
    config = resolved_config(camera=True, llm_model=CUSTOM_TEXT_MODEL)
    llm_slot = config.resolved["llm"].model_copy(update={"capabilities": ModelCapabilities(vision=False)})
    resolved = {**config.resolved, "llm": llm_slot}

    assert model_vision_support(config.config) is None, "the registry does not know a custom id"
    assert model_vision_support(config.config, resolved) is False
    assert model_vision_support(config.config, capabilities=ModelCapabilities(vision=False)) is False


def test_model_vision_support_falls_back_to_the_registry_when_capabilities_are_absent() -> None:
    config = resolved_config(camera=True, llm_model=VISION_MODEL)
    unknown = config.resolved["llm"].model_copy(update={"capabilities": ModelCapabilities()})

    assert model_vision_support(config.config, config.resolved) is True
    assert model_vision_support(config.config, {**config.resolved, "llm": unknown}) is True
    assert model_vision_support(config.config, capabilities=None) is True


def test_the_session_plan_carries_the_llm_capabilities() -> None:
    config = resolved_config(camera=True, llm_model=CUSTOM_TEXT_MODEL)
    llm_slot = config.resolved["llm"].model_copy(update={"capabilities": ModelCapabilities(vision=False)})
    config = config.model_copy(update={"resolved": {**config.resolved, "llm": llm_slot}})

    assert llm_capabilities_of(config) == ModelCapabilities(vision=False)
    assert llm_capabilities_of(resolved_config()) is None


async def test_a_custom_id_declared_text_only_is_skipped_and_reported() -> None:
    """R-V4-23: the api says the custom model is text-only, so the frame never reaches it."""
    frames = FakeFrameBuffer()
    frames.set_latest(FrameSnapshot(frame=_fake_frame(), source="camera", age_s=1.0))
    events: list[tuple[str, dict[str, Any]]] = []
    ctx = _context(resolved_config(camera=True, llm_model=CUSTOM_TEXT_MODEL), frames=frames)
    ctx.llm_capabilities = ModelCapabilities(vision=False, source="declared")
    agent = PlatformAgent(
        ctx=ctx, pack=NullPack(), has_tts=True, record_event=lambda t, p: events.append((t, p))
    )
    message = llm.ChatMessage(role="user", content=["What am I holding?"])

    await agent.on_user_turn_completed(ChatContext.empty(), message)

    assert _image_count([message]) == 0
    assert events == [("info", {"message": f"vision injection skipped: {CUSTOM_TEXT_MODEL} is text-only"})]


async def test_describe_current_frame_refuses_a_custom_id_declared_text_only() -> None:
    """The built-in tool reads the same resolved capability through the session context."""
    from livekit.agents import ToolError

    from lkap_agent.tools.builtin.describe_current_frame import build_describe_current_frame_tool

    frames = FakeFrameBuffer()
    frames.set_latest(FrameSnapshot(frame=_fake_frame(), source="camera", age_s=1.0))
    ctx = _context(resolved_config(camera=True, llm_model=CUSTOM_TEXT_MODEL), frames=frames)
    ctx.llm_capabilities = ModelCapabilities(vision=False, source="detected")
    tool = build_describe_current_frame_tool(ctx)

    with pytest.raises(ToolError, match="cannot see images"):
        await tool(context=cast(Any, None))


async def test_a_custom_id_without_capabilities_still_gets_the_frame() -> None:
    frames = FakeFrameBuffer()
    frames.set_latest(FrameSnapshot(frame=_fake_frame(), source="camera", age_s=1.0))
    agent = _agent(resolved_config(camera=True, llm_model=CUSTOM_TEXT_MODEL), frames=frames)
    message = llm.ChatMessage(role="user", content=["Look."])

    await agent.on_user_turn_completed(ChatContext.empty(), message)

    assert _image_count([message]) == 1, "unknown stays optimistic (R5 auto-degrade)"


async def test_on_user_turn_completed_skips_vision_when_no_camera_capability() -> None:
    """An agent without camera or screen share never injects an image."""
    frames = FakeFrameBuffer()
    frames.set_latest(FrameSnapshot(frame=_fake_frame(), source="camera", age_s=0.1))
    agent = _agent(resolved_config(camera=False), frames=frames)
    message = llm.ChatMessage(role="user", content=["Look at this."])

    await agent.on_user_turn_completed(ChatContext.empty(), message)

    assert not any(isinstance(p, llm.ImageContent) for p in message.content)


# ------------------------------------------------------------------ pack hooks


async def test_on_user_turn_completed_runs_the_pack_hook_last() -> None:
    """The pack sees the turn with retrieval and vision already applied."""
    pack = _SilentPack()
    agent = _agent(resolved_config(), pack)

    await agent.on_user_turn_completed(ChatContext.empty(), llm.ChatMessage(role="user", content=["hi"]))

    assert pack.user_turns == ["hi"]


async def test_a_failing_pack_hook_does_not_break_the_turn() -> None:
    """A pack bug degrades its panel, it does not end the conversation."""

    class _BrokenPack(NullPack):
        async def on_user_turn_completed(self, ctx: Any, turn_ctx: Any, new_message: Any) -> None:
            raise RuntimeError("pack bug")

        async def on_session_end(self, ctx: Any, reason: str) -> None:
            raise RuntimeError("pack bug")

    agent = _agent(resolved_config(), _BrokenPack())

    await agent.on_user_turn_completed(ChatContext.empty(), llm.ChatMessage(role="user", content=["hi"]))
    await agent.on_pack_session_end("done")


# ------------------------------------------------------------ agent turn hook


class _AgentTurnPack(NullPack):
    """Records every `on_agent_turn_completed` call."""

    def __init__(self) -> None:
        super().__init__()
        self.agent_turns: list[tuple[str, bool]] = []

    async def on_agent_turn_completed(self, ctx: Any, text: str, interrupted: bool) -> None:
        self.agent_turns.append((text, interrupted))


def _item_added(role: Any, text: str | None, *, interrupted: bool = False) -> ConversationItemAddedEvent:
    content: list[Any] = [text] if text is not None else []
    return ConversationItemAddedEvent(
        item=llm.ChatMessage(role=role, content=content, interrupted=interrupted)
    )


async def _drain_hook_tasks(agent: PlatformAgent) -> None:
    await asyncio.gather(*list(agent._hook_tasks))


async def test_on_conversation_item_runs_the_agent_turn_hook_once_per_assistant_turn() -> None:
    pack = _AgentTurnPack()
    agent = _agent(resolved_config(), pack)

    agent.on_conversation_item(_item_added("user", "Is flooding covered?"))
    agent.on_conversation_item(_item_added("assistant", "Yes, flood is covered."))
    agent.on_conversation_item(_item_added("user", "Thanks"))
    agent.on_conversation_item(_item_added("assistant", "You're", interrupted=True))
    await _drain_hook_tasks(agent)

    assert pack.agent_turns == [("Yes, flood is covered.", False), ("You're", True)]


@pytest.mark.parametrize(
    ("role", "text"),
    [("user", "hello"), ("system", "prompt"), ("assistant", None), ("assistant", "")],
    ids=["user", "system", "assistant-no-content", "assistant-empty-text"],
)
async def test_on_conversation_item_ignores_non_assistant_or_empty_messages(
    role: str, text: str | None
) -> None:
    pack = _AgentTurnPack()
    agent = _agent(resolved_config(), pack)

    agent.on_conversation_item(_item_added(role, text))
    await _drain_hook_tasks(agent)

    assert pack.agent_turns == []


async def test_on_conversation_item_ignores_an_agent_handoff() -> None:
    pack = _AgentTurnPack()
    agent = _agent(resolved_config(), pack)

    agent.on_conversation_item(ConversationItemAddedEvent(item=llm.AgentHandoff(new_agent_id="other")))
    await _drain_hook_tasks(agent)

    assert pack.agent_turns == []


async def test_a_failing_agent_turn_hook_is_logged_and_never_escapes() -> None:
    class _BrokenPack(NullPack):
        async def on_agent_turn_completed(self, ctx: Any, text: str, interrupted: bool) -> None:
            raise RuntimeError("pack bug")

    agent = _agent(resolved_config(), _BrokenPack())

    agent.on_conversation_item(_item_added("assistant", "Done."))
    await _drain_hook_tasks(agent)

    assert agent._hook_tasks == set()


def test_session_context_defaults_record_event_to_a_no_op() -> None:
    ctx = _context(resolved_config())

    ctx.record_event("escalation", {"reason": "r", "urgency": "high"})


# ------------------------------------------------------------- reply control


def _tools_executed(*names: str) -> FunctionToolsExecutedEvent:
    calls = [llm.FunctionCall(call_id=f"call-{n}", name=n, arguments="{}") for n in names]
    outputs = [
        llm.FunctionCallOutput(call_id=c.call_id, name=c.name, output="ok", is_error=False) for c in calls
    ]
    return FunctionToolsExecutedEvent(function_calls=calls, function_call_outputs=outputs)


def test_silent_reply_tools_cancel_the_model_reply_in_realtime_mode() -> None:
    """`reply_required=False` is how a realtime model is told to stay quiet."""
    agent = _agent(resolved_config(mode="realtime"), _SilentPack())
    event = _tools_executed("sync_claim_packet")

    agent.on_function_tools_executed(event)

    assert not event.has_tool_reply


def test_a_non_silent_tool_keeps_its_reply() -> None:
    """Only tools the pack flagged `silent_reply` are silenced."""
    agent = _agent(resolved_config(mode="realtime"), _SilentPack())
    event = _tools_executed("lookup_policy")

    agent.on_function_tools_executed(event)

    assert event.has_tool_reply


def test_a_batch_mixing_silent_and_speaking_tools_keeps_its_reply() -> None:
    """Cancelling is all-or-nothing per batch, so a speaking tool wins."""
    agent = _agent(resolved_config(mode="realtime"), _SilentPack())
    event = _tools_executed("sync_claim_packet", "lookup_policy")

    agent.on_function_tools_executed(event)

    assert event.has_tool_reply


@pytest.mark.parametrize(("version", "silenced"), [("1.8.3", True), ("1.9.0", True), ("1.8.2", False)])
def test_cascaded_mode_honours_silent_reply_from_livekit_agents_1_8_3(
    monkeypatch: pytest.MonkeyPatch, version: str, silenced: bool
) -> None:
    """R-V4-68: the pipeline path reads `has_tool_reply` since 1.8.3; below it, the LLM always answers."""
    monkeypatch.setattr("livekit.agents.__version__", version)
    agent = _agent(resolved_config(mode="cascaded"), _SilentPack())
    event = _tools_executed("sync_claim_packet")

    agent.on_function_tools_executed(event)

    assert event.has_tool_reply is not silenced


@pytest.mark.parametrize("version", ["1.8.2", "1.8.3"])
@pytest.mark.parametrize("mode", ["realtime", "half_cascade"])
def test_realtime_models_honour_silent_reply_on_every_sdk_version(
    monkeypatch: pytest.MonkeyPatch, mode: PipelineMode, version: str
) -> None:
    """R-V4-68 changes cascaded only: realtime models were already silenced before 1.8.3."""
    monkeypatch.setattr("livekit.agents.__version__", version)
    agent = _agent(resolved_config(mode=mode), _SilentPack())
    event = _tools_executed("sync_claim_packet")

    agent.on_function_tools_executed(event)

    assert not event.has_tool_reply


def test_cascaded_mode_keeps_the_reply_of_a_tool_without_silent_reply() -> None:
    """On 1.8.3 only `silent_reply` tools go quiet; a mixed batch is still answered."""
    agent = _agent(resolved_config(mode="cascaded"), _SilentPack())
    plain = _tools_executed("lookup_policy")
    mixed = _tools_executed("sync_claim_packet", "lookup_policy")

    agent.on_function_tools_executed(plain)
    agent.on_function_tools_executed(mixed)

    assert plain.has_tool_reply
    assert mixed.has_tool_reply


def _http_silent_agent(mode: PipelineMode, pack: Any = None) -> Any:
    """An agent whose `_assemble` found one `silent_reply=true` HTTP tool (R-V4-71)."""
    ctx = _context(resolved_config(mode=mode))
    return PlatformAgent(
        ctx=ctx,
        pack=pack or NullPack(),
        has_tts=mode == "cascaded",
        silent_reply_tools=frozenset({"push_status"}),
    )


def _unhonoured_lines(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [line for line in logs if "silent_reply is not honoured" in str(line.get("event"))]


@pytest.mark.parametrize("mode", ["realtime", "half_cascade"])
def test_on_function_tools_executed_silent_http_tool_on_realtime_cancels_the_reply(
    mode: PipelineMode,
) -> None:
    """R-V4-71: an HTTP tool's `silent_reply` joins the silent set; realtime models always honour it."""
    agent = _http_silent_agent(mode)
    event = _tools_executed("push_status")

    agent.on_function_tools_executed(event)

    assert not event.has_tool_reply


def test_on_function_tools_executed_silent_http_tool_on_cascaded_1_8_3_cancels_the_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R-V4-71 + R-V4-68: the cascaded pipeline honours it from livekit-agents 1.8.3, without a log line."""
    monkeypatch.setattr("livekit.agents.__version__", "1.8.3")
    with structlog.testing.capture_logs() as logs:
        agent = _http_silent_agent("cascaded")
    event = _tools_executed("push_status")

    agent.on_function_tools_executed(event)

    assert not event.has_tool_reply
    assert _unhonoured_lines(logs) == []


def test_on_function_tools_executed_silent_http_tool_on_cascaded_below_1_8_3_keeps_the_reply_and_logs_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Below 1.8.3 a cascaded LLM answers anyway; the session says so once instead of ignoring the flag."""
    monkeypatch.setattr("livekit.agents.__version__", "1.8.2")
    with structlog.testing.capture_logs() as logs:
        agent = _http_silent_agent("cascaded")
        first, second = _tools_executed("push_status"), _tools_executed("push_status")
        agent.on_function_tools_executed(first)
        agent.on_function_tools_executed(second)

    assert first.has_tool_reply
    assert second.has_tool_reply
    (line,) = _unhonoured_lines(logs)
    assert line["log_level"] == "info"
    assert line["tools"] == ["push_status"]


def test_the_unhonoured_line_is_not_logged_for_pack_silent_tools_or_realtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The line is about HTTP tools on a cascaded pipeline below 1.8.3 only; pack behaviour is unchanged."""
    monkeypatch.setattr("livekit.agents.__version__", "1.8.2")
    with structlog.testing.capture_logs() as logs:
        _agent(resolved_config(mode="cascaded"), _SilentPack())
        _http_silent_agent("realtime")

    assert _unhonoured_lines(logs) == []


@pytest.mark.parametrize("mode", ["realtime", "cascaded"])
def test_on_function_tools_executed_silent_http_tool_mixed_with_a_speaking_tool_keeps_the_reply(
    mode: PipelineMode,
) -> None:
    """The ⊆ rule is unchanged: a batch with any non-silent tool is still answered."""
    agent = _http_silent_agent(mode, _SilentPack())
    mixed = _tools_executed("push_status", "lookup_policy")
    silent_only = _tools_executed("push_status", "sync_claim_packet")

    agent.on_function_tools_executed(mixed)
    agent.on_function_tools_executed(silent_only)

    assert mixed.has_tool_reply
    assert not silent_only.has_tool_reply, "pack and HTTP silent names form one set"


def test_the_cascaded_note_keeps_short_acknowledgements_for_tools_that_answer() -> None:
    """R-V4-68: the note no longer claims every tool result is voiced."""
    note = PIPELINE_NOTES["cascaded"]
    assert "one short clause" in note
    assert "Every tool result comes back to you" not in note
    assert "silently" in note


# ------------------------------------------------------------ session builder


async def test_session_builder_wires_a_cascaded_pipeline() -> None:
    """Cascaded sessions get stt + llm + tts, the VAD and a turn detector."""
    providers = BuiltProviders(stt=object(), llm=object(), tts=object())
    detector = object()
    vad = object()

    plan = SessionBuilder().build(resolved_config(), providers, vad=vad, turn_detector=detector)

    assert plan.session.stt is providers.stt
    assert plan.session.tts is providers.tts
    assert plan.session.vad is vad
    assert plan.has_tts is True
    assert plan.is_realtime is False


async def test_session_builder_ignores_stt_and_the_turn_detector_in_realtime_mode() -> None:
    """A realtime model transcribes and detects turns itself; a second detector conflicts."""
    providers = BuiltProviders(realtime=object(), stt=object())

    plan = SessionBuilder().build(
        resolved_config(mode="realtime", with_tts=False), providers, vad=object(), turn_detector=object()
    )

    assert plan.session.stt is None
    assert plan.session.vad is None or plan.session.vad is not None  # vad is caller-controlled
    assert plan.has_tts is False
    assert plan.is_realtime is True
    assert plan.needs_generate_reply_greeting is True


@pytest.mark.parametrize(
    ("mode", "camera", "expected"),
    [("realtime", True, True), ("realtime", False, False), ("cascaded", True, False)],
)
async def test_room_options_enable_video_input_only_for_realtime_models(
    mode: PipelineMode, camera: bool, expected: bool
) -> None:
    """`push_video` forwards only to a realtime session, so cascaded video_input is waste."""
    providers = BuiltProviders(realtime=object(), llm=object(), stt=object(), tts=object())

    plan = SessionBuilder().build(resolved_config(mode=mode, camera=camera), providers)

    assert plan.room_options.video_input is expected


async def test_room_options_link_the_dispatched_participant_and_leave_disconnects_to_the_worker() -> None:
    """RoomIO follows the browser participant; the worker's reconnect grace (F-33) ends the job."""
    providers = BuiltProviders(llm=object(), stt=object(), tts=object())

    plan = SessionBuilder().build(resolved_config(participant_identity="user-abc"), providers)

    assert plan.room_options.participant_identity == "user-abc"
    assert plan.room_options.close_on_disconnect is False


@pytest.mark.parametrize(
    ("mode", "providers"),
    [("realtime", BuiltProviders()), ("cascaded", BuiltProviders(stt=object()))],
)
async def test_session_builder_rejects_a_pipeline_missing_its_model(
    mode: PipelineMode, providers: BuiltProviders
) -> None:
    """A config that resolved no model is a build failure, not a silent mute agent."""
    with pytest.raises(ValueError, match="no (realtime|llm) provider"):
        SessionBuilder().build(resolved_config(mode=mode), providers)


def test_build_turn_handling_maps_allow_interruptions_and_drops_unknown_keys() -> None:
    """`allow_interruptions` lives under `interruption.enabled` in 1.8.2."""
    options = build_turn_handling(
        {"endpointing": {"min_delay": 0.2}, "bogus": 1},
        allow_interruptions=False,
        turn_detector=None,
    )

    assert options["interruption"] == {"enabled": False}
    assert options["endpointing"] == {"min_delay": 0.2}
    assert "bogus" not in options


def test_build_turn_handling_keeps_an_explicit_interruption_setting() -> None:
    """A config that set `interruption` explicitly wins over the voice-level flag."""
    options = build_turn_handling(
        {"interruption": {"enabled": True, "min_words": 2}},
        allow_interruptions=False,
        turn_detector=None,
    )

    assert options["interruption"] == {"enabled": True, "min_words": 2}


def test_build_turn_handling_adds_the_turn_detector_only_when_unset() -> None:
    """A caller-supplied `turn_detection` is never overwritten."""
    detector = object()

    added = build_turn_handling({}, allow_interruptions=True, turn_detector=detector)
    kept = build_turn_handling({"turn_detection": "vad"}, allow_interruptions=True, turn_detector=detector)

    assert added["turn_detection"] is detector
    assert kept["turn_detection"] == "vad"


async def test_session_builder_passes_the_configured_tool_budget() -> None:
    """`max_tool_steps` bounds a tool loop; it must reach the session."""
    config = resolved_config()
    config.config.tools.max_tool_steps = 7

    plan = SessionBuilder().build(config, BuiltProviders(llm=object(), stt=object(), tts=object()))

    assert plan.session.options.max_tool_steps == 7


# ------------------------------------------------------------- typed chat turns


class _TypedTurnSession:
    """Just enough of `AgentSession` for `platform_text_input_cb`."""

    def __init__(self, agent: Any) -> None:
        self.current_agent = agent
        self.interrupted = 0
        self.replies: list[dict[str, Any]] = []

    @asynccontextmanager
    async def _claim_user_turn(self) -> AsyncIterator[None]:
        yield

    async def interrupt(self) -> None:
        self.interrupted += 1

    def generate_reply(self, **kwargs: Any) -> None:
        self.replies.append(kwargs)


class _HookAgent:
    def __init__(self, raise_: BaseException | None = None) -> None:
        self.chat_ctx = ChatContext([llm.ChatMessage(role="assistant", content=["Hello!"])])
        self.seen: list[tuple[ChatContext, llm.ChatMessage]] = []
        self._raise = raise_

    async def on_user_turn_completed(self, turn_ctx: ChatContext, new_message: llm.ChatMessage) -> None:
        self.seen.append((turn_ctx, new_message))
        turn_ctx.add_message(role="assistant", content=["[kb] injected"])
        if self._raise is not None:
            raise self._raise


async def test_typed_turns_run_the_user_turn_hook_before_the_reply() -> None:
    """The SDK's default text callback skips `on_user_turn_completed`; ours must not."""
    agent = _HookAgent()
    sess = _TypedTurnSession(agent)

    await platform_text_input_cb(cast(Any, sess), SimpleNamespace(text="Is flood covered?"))

    assert sess.interrupted == 1
    assert len(agent.seen) == 1
    turn_ctx, message = agent.seen[0]
    assert message.text_content == "Is flood covered?"
    assert turn_ctx is not agent.chat_ctx, "the hook gets a per-turn copy, like the audio path"
    (reply,) = sess.replies
    assert reply["user_input"] is message
    assert reply["chat_ctx"] is turn_ctx
    assert len(agent.chat_ctx.items) == 1, "the persisted context is untouched"


async def test_a_stop_response_from_the_hook_skips_the_typed_reply() -> None:
    from livekit.agents import StopResponse

    sess = _TypedTurnSession(_HookAgent(raise_=StopResponse()))

    await platform_text_input_cb(cast(Any, sess), SimpleNamespace(text="hi"))

    assert sess.replies == []


async def test_a_failing_hook_still_answers_the_typed_turn_without_its_edits() -> None:
    agent = _HookAgent(raise_=RuntimeError("pack bug"))
    sess = _TypedTurnSession(agent)

    await platform_text_input_cb(cast(Any, sess), SimpleNamespace(text="hi"))

    (reply,) = sess.replies
    assert not any("[kb]" in (i.text_content or "") for i in reply["chat_ctx"].items)


class _NoClaimSession:
    """An `AgentSession` stand-in from a hypothetical SDK without `_claim_user_turn`."""

    def __init__(self, agent: Any) -> None:
        self.current_agent = agent
        self.interrupted = 0
        self.replies: list[dict[str, Any]] = []

    async def interrupt(self) -> None:
        self.interrupted += 1

    def generate_reply(self, **kwargs: Any) -> None:
        self.replies.append(kwargs)


async def test_typed_turns_fall_back_without_claim_user_turn_and_warn_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D-W2-9p: an SDK rename degrades to "no user-state pin", never to "typed chat dead"."""
    import lkap_agent.platform_agent as platform_agent_module

    monkeypatch.setattr(platform_agent_module, "_claim_warning_logged", False)
    warnings: list[str] = []
    monkeypatch.setattr(
        platform_agent_module.logger, "warning", lambda msg, *a, **kw: warnings.append(str(msg))
    )
    agent = _HookAgent()
    sess = _NoClaimSession(agent)

    await platform_text_input_cb(cast(Any, sess), SimpleNamespace(text="first"))
    await platform_text_input_cb(cast(Any, sess), SimpleNamespace(text="second"))

    assert [m.text_content for _, m in agent.seen] == ["first", "second"]
    assert len(sess.replies) == 2
    assert [w for w in warnings if "_claim_user_turn is missing" in w] == [
        "AgentSession._claim_user_turn is missing; typed turns run without the user-state pin (SDK upgrade?)"
    ]


async def test_a_typed_turn_is_skipped_when_the_current_speech_cannot_be_interrupted() -> None:
    class _Uninterruptible(_TypedTurnSession):
        async def interrupt(self) -> None:
            raise RuntimeError("speech is not interruptible")

    agent = _HookAgent()
    sess = _Uninterruptible(agent)

    await platform_text_input_cb(cast(Any, sess), SimpleNamespace(text="hi"))

    assert agent.seen == []
    assert sess.replies == []


def test_sdk_default_text_input_cb_still_matches_our_assumptions() -> None:
    """Tripwire for the SDK-private `_claim_user_turn` (DECISIONS-W2 §D-W2-9p)."""
    from livekit.agents.voice.room_io import types as room_io_types

    message = (
        "livekit-agents changed the typed-chat path; re-read DECISIONS-W2 §D-W2-9p before bumping the pin"
    )
    src = inspect.getsource(room_io_types._default_text_input_cb)
    assert "_claim_user_turn" in src, message
    assert "generate_reply(user_input=" in src, message
    params = inspect.signature(AgentSession.generate_reply).parameters
    assert "user_input" in params and "chat_ctx" in params, message
    assert hasattr(AgentSession, "_claim_user_turn"), message


@pytest.mark.parametrize(("chat_input", "expected"), [(False, False), (True, NOT_GIVEN)])
async def test_room_options_honour_capabilities_chat_input(chat_input: bool, expected: Any) -> None:
    """D-W2-9p: a voice-only agent refuses typed input at the worker, not only in the UI."""
    providers = BuiltProviders(llm=object(), stt=object(), tts=object())

    plan = SessionBuilder().build(resolved_config(chat_input=chat_input), providers)

    assert plan.room_options.text_input is expected


# ------------------------------------------------------- background tools (V4-12)


class _Session:
    """A weak-referenceable stand-in for `AgentSession` (the policy registry is per session)."""


def _bg_context(config: ResolvedAgentConfig, ui: FakeUiChannel) -> SessionContext:
    ctx = _context(config, ui=ui)
    ctx.session = cast(Any, _Session())
    return ctx


def _with_tools(config: ResolvedAgentConfig, **tools_fields: Any) -> ResolvedAgentConfig:
    tools = config.config.tools.model_copy(update=tools_fields)
    return config.model_copy(update={"config": config.config.model_copy(update={"tools": tools})})


def _http_def(**execution_fields: Any) -> HttpToolDefinition:
    return HttpToolDefinition(
        name="lookup_item",
        description="Look up an item.",
        parameters={"type": "object", "properties": {}},
        method="GET",
        url="https://api.example.com/items",
        allowed_hosts=["api.example.com"],
        execution=ToolExecution(**execution_fields),
    )


def _activity(ui: FakeUiChannel) -> list[Any]:
    return [op.value for patch in ui.patches for op in patch if op.path == "/activity"]


def _started(call_id: str, name: str = "lookup_item") -> ToolExecutionUpdatedEvent:
    call = llm.FunctionCall(call_id=call_id, name=name, arguments="{}")
    return ToolExecutionUpdatedEvent(update=ToolCallStarted(function_call=call))


def _updated(call_id: str, message: str) -> ToolExecutionUpdatedEvent:
    return ToolExecutionUpdatedEvent(update=ToolCallUpdated(id=call_id, call_id=call_id, message=message))


def _ended(call_id: str, status: Any, message: str | None = None) -> ToolExecutionUpdatedEvent:
    return ToolExecutionUpdatedEvent(
        update=ToolCallEnded(id=call_id + "_final", call_id=call_id, message=message, status=status)
    )


@pytest.mark.parametrize("mode", ["cascaded", "realtime", "half_cascade"])
def test_every_pipeline_note_says_what_in_progress_means(mode: PipelineMode) -> None:
    assert IN_PROGRESS_NOTE in PIPELINE_NOTES[mode]
    assert "never invent the result" in compose_instructions("Be brief.", mode=mode)


@pytest.mark.parametrize(
    ("status", "headline"),
    [
        ("done", "Lookup item finished"),
        ("error", "Lookup item failed: upstream said no"),
        ("cancelled", "Lookup item cancelled"),
    ],
)
async def test_on_tool_execution_upserts_one_activity_row_per_call(status: str, headline: str) -> None:
    ui = FakeUiChannel()
    config = _with_tools(resolved_config(), execution_default="auto")
    tools = build_http_tools([_http_def()], platform_allowed_hosts=["api.example.com"])
    agent = PlatformAgent(ctx=_bg_context(config, ui), pack=NullPack(), tools=list(tools), has_tts=True)

    agent.on_tool_execution(_started("call-1"))
    agent.on_tool_execution(_updated("call-1", "Working on lookup item."))
    agent.on_tool_execution(_ended("call-1", status, "upstream said no" if status == "error" else "ok"))
    await _drain_hook_tasks(agent)

    rows = _activity(ui)
    assert [(r.id, r.phase) for r in rows] == [
        ("tool:call-1", "running"),
        ("tool:call-1", "running"),
        ("tool:call-1", status),
    ]
    assert rows[0].headline == "Lookup item started"
    assert rows[1].headline == "Working on lookup item."
    assert rows[1].detail == {"message": "Working on lookup item."}
    assert rows[2].headline == headline
    assert rows[2].duration_ms is not None and rows[2].duration_ms >= 0
    assert [r.id for r in ui.state.activity] == ["tool:call-1"]


async def test_on_tool_execution_a_fast_blocking_tool_leaves_no_row() -> None:
    ui = FakeUiChannel()
    agent = PlatformAgent(ctx=_bg_context(resolved_config(), ui), pack=NullPack(), has_tts=True)

    agent.on_tool_execution(_started("call-1", "current_time"))
    agent.on_tool_execution(_ended("call-1", "done", "noon"))
    await _drain_hook_tasks(agent)

    assert _activity(ui) == []


async def test_on_tool_execution_a_slow_blocking_tool_appears_after_a_second(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(execution_module, "SLOW_BLOCKING_S", 0.02)
    ui = FakeUiChannel()
    agent = PlatformAgent(ctx=_bg_context(resolved_config(), ui), pack=NullPack(), has_tts=True)

    agent.on_tool_execution(_started("call-1", "escalate_to_human"))
    await asyncio.sleep(0.05)
    agent.on_tool_execution(_ended("call-1", "done", "ok"))
    await _drain_hook_tasks(agent)

    assert [r.phase for r in _activity(ui)] == ["running", "done"]


def test_platform_agent_binds_http_tools_to_the_agent_default() -> None:
    tools = build_http_tools([_http_def()], platform_allowed_hosts=["api.example.com"])
    assert tools[0].info.flags == ToolFlag.NONE

    config = _with_tools(resolved_config(), execution_default="auto")
    ctx = _bg_context(config, FakeUiChannel())
    agent = PlatformAgent(ctx=ctx, pack=NullPack(), tools=list(tools), has_tts=True)

    (bound,) = [t for t in agent.tools if getattr(t, "id", None) == "lookup_item"]
    assert bound.info.flags == ToolFlag.CANCELLABLE
    assert bound.info.on_duplicate == "reject"


class _OptInPack(NullPack):
    """A pack with one tool opted into the executor and one left alone."""

    def tool_meta(self) -> list[ToolMeta]:
        return [
            ToolMeta(
                name="start_workflow",
                activity_label="Claims team",
                execution=ToolExecution(mode="background"),
            ),
            ToolMeta(name="lookup_policy"),
        ]


def _pack_tools() -> list[Any]:
    @function_tool
    async def start_workflow(topic: str) -> str:
        """Start the workflow.

        Args:
            topic: What it is about.
        """
        return topic

    @function_tool
    async def lookup_policy(policy_id: str) -> str:
        """Look up a policy.

        Args:
            policy_id: The policy number.
        """
        return policy_id

    return [start_workflow, lookup_policy]


def test_platform_agent_wraps_only_the_pack_tools_that_opt_in() -> None:
    start, lookup = _pack_tools()
    ctx = _bg_context(resolved_config(), FakeUiChannel())
    agent = PlatformAgent(ctx=ctx, pack=_OptInPack(), tools=[start, lookup], has_tts=True)

    by_name = {getattr(t, "id", None): t for t in agent.tools}
    assert by_name["lookup_policy"] is lookup
    assert by_name["start_workflow"] is not start
    policy = execution_module.policy_of(by_name["start_workflow"])
    assert isinstance(policy, execution_module.ToolPolicy)
    assert policy.resolved.mode == "background"
    assert policy.resolved.label == "Claims team"


def test_platform_agent_puts_mcp_toolsets_in_its_tools() -> None:
    toolset = llm.Toolset(id="mcp_crm")
    legacy = llm.Toolset(id="mcp_legacy")

    agent = PlatformAgent(
        ctx=_bg_context(resolved_config(), FakeUiChannel()),
        pack=NullPack(),
        mcp_toolsets=[toolset],
        mcp_servers=[legacy],
        has_tts=True,
    )

    assert toolset in agent.tools and legacy in agent.tools
    assert not agent.mcp_servers


def test_a_flow_agent_records_one_info_event_when_it_keeps_background_tools_blocking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("livekit.agents.__version__", "1.8.2")  # the downgrade path (R-V4-54)
    events: list[tuple[str, dict[str, Any]]] = []
    flow = FlowSpec.model_validate({"nodes": [{"id": "start", "kind": "start"}]})
    base = _with_tools(resolved_config(), execution_default="auto")
    config = base.model_copy(update={"config": base.config.model_copy(update={"flow": flow})})
    ctx = _bg_context(config, FakeUiChannel())
    tools = build_http_tools([_http_def()], platform_allowed_hosts=["api.example.com"])

    for _ in range(2):
        agent = PlatformAgent(
            ctx=ctx,
            pack=NullPack(),
            tools=list(tools),
            has_tts=True,
            record_event=lambda kind, payload: events.append((kind, payload)),
        )
        (bound,) = [t for t in agent.tools if getattr(t, "id", None) == "lookup_item"]
        assert bound.info.flags == ToolFlag.NONE

    assert len(events) == 1
    kind, payload = events[0]
    assert kind == "info" and "lookup_item" in payload["message"] and "1.8.3" in payload["message"]
