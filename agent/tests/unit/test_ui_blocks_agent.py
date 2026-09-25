"""V2-10: `PlatformAgent` block init, block callbacks and the D-W2-9a snapshot order."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fakes.fake_api import resolved_config
from fakes.fake_ctx import (
    FakeBackgroundRunner,
    FakeFrameBuffer,
    FakeKbClient,
    FakeStructuredLLM,
    FakeUiChannel,
)
from fakes.fake_room import FakeRemoteParticipant, FakeRoom
from livekit import rtc
from livekit.agents import ChatContext, FunctionToolsExecutedEvent, llm
from lkap_contracts.agent_config import PanelLayout, ResolvedAgentConfig
from lkap_contracts.api_models import KbHit
from lkap_contracts.migrate import DEFAULT_COMPOSITE_BLOCKS
from lkap_contracts.ui_protocol import (
    RPC_AGENT_ACTION,
    RPC_UI_REQUEST,
    TOPIC_UI_STATE,
    AgentAction,
    AgentActionResult,
    BlockSpec,
    UiPatchOp,
    UiSnapshot,
)

from lkap_agent.packs.loader import NullPack, null_manifest
from lkap_agent.platform_agent import PlatformAgent, SessionContext
from lkap_agent.ui.channel import UiChannel

GENERIC_PANEL = PanelLayout.model_validate({"panel_id": "composite", "blocks": DEFAULT_COMPOSITE_BLOCKS})


class _BlockPack(NullPack):
    """A pack with a composite `default_panel` and an `on_block_action` hook."""

    def __init__(self, default_panel: PanelLayout | None = GENERIC_PANEL) -> None:
        super().__init__()
        self.manifest = null_manifest().model_copy(update={"default_panel": default_panel})
        self.block_actions: list[tuple[str, str, dict[str, Any]]] = []

    async def on_block_action(
        self, ctx: Any, block_id: str, name: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        self.block_actions.append((block_id, name, data))
        return {"handled": True}


def _config(panel: PanelLayout, **kwargs: Any) -> ResolvedAgentConfig:
    resolved = resolved_config(greeting="", **kwargs)
    resolved.config.panel = panel
    return resolved


def _agent(
    config: ResolvedAgentConfig,
    pack: Any = None,
    *,
    ui: Any = None,
    kb: FakeKbClient | None = None,
    session: Any = None,
) -> tuple[PlatformAgent, Any, FakeRoom, list[tuple[str, dict[str, Any]]]]:
    room = FakeRoom()
    room.add_remote_participant(FakeRemoteParticipant("user-guest"))
    events: list[tuple[str, dict[str, Any]]] = []
    channel = ui if ui is not None else UiChannel(room, config.session_id)  # type: ignore[arg-type]
    if isinstance(channel, UiChannel):
        channel.start()
    ctx = SessionContext(
        session_id=config.session_id,
        agent_id=config.agent_id,
        pipeline_mode=config.config.pipeline.mode,
        config=config.config,
        session=cast(Any, session if session is not None else object()),
        room=cast(rtc.Room, room),
        ui=channel,
        frames=FakeFrameBuffer(),
        kb=kb or FakeKbClient(),
        workflow_llm=FakeStructuredLLM(),
        background=FakeBackgroundRunner(),
        log=None,
        record_event=lambda t, p: events.append((t, p)),
    )
    agent = PlatformAgent(ctx=ctx, pack=pack or NullPack(), has_tts=True)
    return agent, channel, room, events


def _state_messages(room: FakeRoom) -> list[dict[str, Any]]:
    return [json.loads(m.text) for m in room.local_participant.sent_text if m.topic == TOPIC_UI_STATE]


async def test_on_enter_first_snapshot_is_seq_one_with_the_default_composite_blocks() -> None:
    agent, channel, room, _ = _agent(_config(GENERIC_PANEL))
    assert room.local_participant.sent_text == []  # nothing sent at construction
    await agent.on_enter()

    messages = _state_messages(room)
    assert [m["type"] for m in messages] == ["snapshot"]
    snapshot = UiSnapshot.model_validate(messages[0])
    assert snapshot.seq == 1
    assert snapshot.state.blocks == {"status": {}, "notes": {}, "checklist": {}, "activity": {}}
    assert list(channel.block_specs) == ["status", "notes", "checklist", "activity"]


async def test_empty_composite_config_falls_back_to_the_pack_default_panel() -> None:
    agent, channel, _room, _ = _agent(_config(PanelLayout()), _BlockPack())
    assert set(channel.state.blocks) == {"status", "notes", "checklist", "activity"}
    del agent


async def test_insurance_pack_keeps_blocks_empty_and_its_notebook_flow() -> None:
    from packs.insurance_claim.pack import PACK  # noqa: PLC0415

    panel = PanelLayout(panel_id="insurance_notebook", blocks=[])
    agent, channel, room, _ = _agent(_config(panel, pack_id="insurance_claim"), PACK)
    await agent.on_enter()

    assert channel.state.blocks == {}
    messages = _state_messages(room)
    assert messages[0]["seq"] == 1
    assert all(m["state"]["blocks"] == {} for m in messages if m["type"] == "snapshot")
    assert channel.state.custom  # the notebook's pack state is still there
    assert channel.block_specs == {}


async def test_block_init_on_a_v1_channel_just_assigns_state() -> None:
    fake = FakeUiChannel()
    panel = PanelLayout(blocks=[BlockSpec(id="t", type="table")])
    _agent(_config(panel), ui=fake)
    assert fake.state.blocks == {"t": {"columns": [], "rows": [], "selected_row": None}}


async def test_block_action_reaches_the_pack_hook() -> None:
    pack = _BlockPack()
    agent, _channel, room, _ = _agent(_config(GENERIC_PANEL), pack)
    raw = AgentAction(
        action="block_action", payload={"block_id": "notes", "name": "pin", "data": {"id": "n1"}}
    ).model_dump_json()
    result = AgentActionResult.model_validate_json(
        await room.local_participant.invoke_rpc(RPC_AGENT_ACTION, raw)
    )
    assert result.ok is True
    assert result.payload == {"handled": True}
    assert pack.block_actions == [("notes", "pin", {"id": "n1"})]
    del agent


async def test_block_action_without_a_pack_hook_is_a_no_op() -> None:
    agent, _channel, room, _ = _agent(_config(GENERIC_PANEL))
    raw = AgentAction(action="block_action", payload={"block_id": "notes", "name": "pin"}).model_dump_json()
    result = AgentActionResult.model_validate_json(
        await room.local_participant.invoke_rpc(RPC_AGENT_ACTION, raw)
    )
    assert (result.ok, result.payload) == (True, {})
    del agent


async def test_channel_events_go_to_the_session_record_event() -> None:
    panel = PanelLayout(blocks=[BlockSpec(id="g", type="gallery")])
    _agent_, channel, _room, events = _agent(_config(panel))
    await channel.set_block("g", {"asset_ids": ["a"]})
    assert events == [("block_update", {"block_id": "g", "block_type": "gallery", "op": "set"})]


async def test_late_form_submission_prompts_a_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    panel = PanelLayout(blocks=[BlockSpec(id="f", type="form")])
    agent, _channel, room, events = _agent(_config(panel))
    replies: list[str] = []
    fake_session = SimpleNamespace(generate_reply=lambda **kw: replies.append(kw["instructions"]))
    monkeypatch.setattr(PlatformAgent, "session", property(lambda self: fake_session))

    raw = AgentAction(action="form_submit", payload={"block_id": "f", "values": {"a": 1}}).model_dump_json()
    await room.local_participant.invoke_rpc(RPC_AGENT_ACTION, raw)

    assert len(replies) == 1 and '"a": 1' in replies[0]
    assert ("form_submitted", {"block_id": "f", "values": {"a": 1}}) in events


def _tools_executed(*names: str) -> FunctionToolsExecutedEvent:
    calls = [llm.FunctionCall(call_id=f"call-{n}", name=n, arguments="{}") for n in names]
    outputs = [
        llm.FunctionCallOutput(call_id=c.call_id, name=c.name, output="", is_error=False) for c in calls
    ]
    return FunctionToolsExecutedEvent(function_calls=calls, function_call_outputs=outputs)


@pytest.mark.parametrize(
    ("mode", "silenced"), [("realtime", True), ("half_cascade", True), ("cascaded", False)]
)
def test_request_form_reply_is_cancelled_on_realtime_models(mode: Any, silenced: bool) -> None:
    agent, *_ = _agent(_config(GENERIC_PANEL, mode=mode))
    event = _tools_executed("request_form")
    agent.on_function_tools_executed(event)
    assert event.has_tool_reply is not silenced


@pytest.mark.parametrize("mode", ["realtime", "half_cascade"])
def test_request_form_reply_is_kept_on_the_text_channel(mode: Any) -> None:
    """Asks #30: on `text` the tool answers with a line the model must act on; never silence it."""
    config = _config(GENERIC_PANEL, mode=mode)
    agent, *_ = _agent(config)
    agent.context.channel = "text"
    text_agent = PlatformAgent(ctx=agent.context, pack=NullPack(), has_tts=True)
    event = _tools_executed("request_form")
    text_agent.on_function_tools_executed(event)
    assert event.has_tool_reply is True


async def test_injected_knowledge_is_logged_at_info_with_counts_only() -> None:
    """Asks #36 / B-11: the KB signal must be visible on an INFO worker, without content."""
    from structlog.testing import capture_logs  # noqa: PLC0415

    hits = [KbHit(chunk_id="c1", document_id="d1", filename="policy.md", score=0.9, text="Flood is covered.")]
    agent, _channel, _room, _ = _agent(_config(GENERIC_PANEL, kb_ids=["kb-1"]), kb=FakeKbClient(hits))
    with capture_logs() as logs:
        await agent.on_user_turn_completed(
            ChatContext.empty(), llm.ChatMessage(role="user", content=["Flood?"])
        )

    [entry] = [e for e in logs if e["event"] == "injected knowledge"]
    assert entry["log_level"] == "info"
    assert entry["hits"] == 1
    assert "Flood is covered." not in repr(entry) and "policy.md" not in repr(entry)


async def test_auto_injected_knowledge_is_cited_into_the_citations_block() -> None:
    hits = [KbHit(chunk_id="c1", document_id="d1", filename="policy.md", score=0.9, text="Flood is covered.")]
    panel = PanelLayout(blocks=[BlockSpec(id="sources", type="kb_citations")])
    agent, channel, _room, _ = _agent(_config(panel, kb_ids=["kb-1"]), kb=FakeKbClient(hits))
    await agent.on_user_turn_completed(ChatContext.empty(), llm.ChatMessage(role="user", content=["Flood?"]))
    assert channel.state.blocks["sources"]["items"][0]["text"] == "Flood is covered."


# ================================================================ V5-08 (R-V5-1)

CHOICE_PANEL = PanelLayout(blocks=[BlockSpec(id="pick", type="choices"), BlockSpec(id="intake", type="form")])


class _RecordingSession:
    """The slice of `AgentSession` the barge-in wiring uses: `on(event, handler)`."""

    def __init__(self) -> None:
        self.handlers: list[tuple[str, Any]] = []

    def on(self, event: str, handler: Any) -> None:
        self.handlers.append((event, handler))


def _user_state(state: str = "speaking") -> Any:
    return SimpleNamespace(old_state="listening", new_state=state)


async def _until(predicate: Any) -> None:
    for _ in range(50):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition never became true")


@pytest.mark.parametrize(
    ("mode", "silenced"), [("realtime", True), ("half_cascade", True), ("cascaded", False)]
)
def test_request_choice_reply_is_cancelled_on_realtime_models(mode: Any, silenced: bool) -> None:
    agent, *_ = _agent(_config(CHOICE_PANEL, mode=mode))
    event = _tools_executed("request_choice")
    agent.on_function_tools_executed(event)
    assert event.has_tool_reply is not silenced


@pytest.mark.parametrize("mode", ["realtime", "half_cascade"])
@pytest.mark.parametrize("channel_name", ["sip_in", "sip_out"])
def test_request_choice_reply_is_kept_on_a_phone_call(mode: Any, channel_name: Any) -> None:
    """On a phone call the tool answers `voice_only` at once; the model must ask out loud."""
    agent, *_ = _agent(_config(CHOICE_PANEL, mode=mode))
    agent.context.channel = channel_name
    phone_agent = PlatformAgent(ctx=agent.context, pack=NullPack(), has_tts=True)
    choice = _tools_executed("request_choice")
    phone_agent.on_function_tools_executed(choice)
    assert choice.has_tool_reply is True
    form = _tools_executed("request_form")
    phone_agent.on_function_tools_executed(form)
    assert form.has_tool_reply is False


def test_barge_in_handler_is_registered_once_per_session() -> None:
    session = _RecordingSession()
    agent, *_ = _agent(_config(CHOICE_PANEL), session=session)
    PlatformAgent(ctx=agent.context, pack=NullPack(), has_tts=True)  # e.g. a second flow node
    assert [name for name, _ in session.handlers] == ["user_state_changed"]


async def test_barge_in_cancels_a_pending_choice_but_not_a_pending_form() -> None:
    session = _RecordingSession()
    agent, channel, room, events = _agent(_config(CHOICE_PANEL), session=session)
    room.local_participant.rpc_call_responses[RPC_UI_REQUEST] = json.dumps({"ok": True, "payload": {}})
    [(_, handler)] = session.handlers
    choice = asyncio.create_task(channel.request_block("pick", timeout_s=30))
    form = asyncio.create_task(channel.request_form("intake", {"type": "object"}, timeout_s=30))
    await _until(lambda: channel.pending_requests == {"pick": "request", "intake": "form"})

    handler(_user_state("listening"))  # not speaking: nothing happens
    assert channel.pending_requests == {"pick": "request", "intake": "form"}

    handler(_user_state("speaking"))
    assert await choice is None
    await _until(lambda: channel.state.blocks["pick"]["status"] == "cancelled")
    assert channel.pending_requests == {"intake": "form"}
    assert channel.state.blocks["intake"]["status"] == "requested"
    assert (
        "block_update",
        {"block_id": "pick", "block_type": "choices", "op": "block_cancelled", "reason": "barge_in"},
    ) in events

    raw = AgentAction(action="form_submit", payload={"block_id": "intake", "values": {"a": 1}})
    await room.local_participant.invoke_rpc(RPC_AGENT_ACTION, raw.model_dump_json())
    assert await form == {"a": 1}
    del agent


async def test_late_choice_submission_prompts_a_block_neutral_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, channel, room, _events = _agent(_config(CHOICE_PANEL))
    replies: list[str] = []
    fake_session = SimpleNamespace(generate_reply=lambda **kw: replies.append(kw["instructions"]))
    monkeypatch.setattr(PlatformAgent, "session", property(lambda self: fake_session))
    await channel.patch_block(
        "pick", [UiPatchOp(op="set", path="/options", value=[{"id": "no", "label": "No"}])]
    )

    raw = AgentAction(action="block_submit", payload={"block_id": "pick", "values": {"selected": ["no"]}})
    await room.local_participant.invoke_rpc(RPC_AGENT_ACTION, raw.model_dump_json())

    assert len(replies) == 1
    assert "the pick block on screen" in replies[0] and '"selected": ["no"]' in replies[0]
    assert "form" not in replies[0]
    assert channel.state.blocks["pick"]["selected"] == ["no"]
    del agent
