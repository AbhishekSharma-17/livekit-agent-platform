"""V2-10: `PlatformAgent` block init, block callbacks and the D-W2-9a snapshot order."""

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
    TOPIC_UI_STATE,
    AgentAction,
    AgentActionResult,
    BlockSpec,
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
    config: ResolvedAgentConfig, pack: Any = None, *, ui: Any = None, kb: FakeKbClient | None = None
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
        session=cast(Any, object()),
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


async def test_auto_injected_knowledge_is_cited_into_the_citations_block() -> None:
    hits = [KbHit(chunk_id="c1", document_id="d1", filename="policy.md", score=0.9, text="Flood is covered.")]
    panel = PanelLayout(blocks=[BlockSpec(id="sources", type="kb_citations")])
    agent, channel, _room, _ = _agent(_config(panel, kb_ids=["kb-1"]), kb=FakeKbClient(hits))
    await agent.on_user_turn_completed(ChatContext.empty(), llm.ChatMessage(role="user", content=["Flood?"]))
    assert channel.state.blocks["sources"]["items"][0]["text"] == "Flood is covered."
