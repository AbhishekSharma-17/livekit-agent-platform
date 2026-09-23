"""V2-15 flow runtime, end to end through `run_session` with scripted LLMs (no network).

A real `AgentSession` runs in text mode (no room), so handoffs, `chat_ctx`
carry-over, `say()` ordering and tool execution are the SDK's own. A text
session has no TTS, so `PlatformAgent` greets through `generate_reply` (its
existing rule): every script starts with the greeting's reply.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Sequence
from typing import Any

import pytest
from fakes.fake_api import FakeApi, resolved_config
from fakes.fake_llm import FakeLLM
from fakes.fake_tts import FakeTTS
from livekit.agents import APIConnectOptions, llm
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN, NotGivenOr
from lkap_contracts.agent_config import ResolvedAgentConfig
from lkap_contracts.flow import FlowSpec
from test_main import FakeJobContext, RoomlessStarter, _deps, _metadata

from lkap_agent.flow import FlowNodeAgent, FlowUserdata
from lkap_agent.main import NoopUiChannel, run_session
from lkap_agent.providers.factory import BuiltProviders, ProviderFactory

# ------------------------------------------------------------------ doubles


class ToolCall:
    """A scripted step that calls one tool with no arguments."""

    def __init__(self, name: str) -> None:
        self.name = name


Step = str | ToolCall


class _ScriptedStream(llm.LLMStream):
    def __init__(self, parent: ScriptedLLM, *, step: Step, **kwargs: Any) -> None:
        self._step = step
        super().__init__(parent, **kwargs)

    async def _run(self) -> None:
        step = self._step
        if isinstance(step, ToolCall):
            call = llm.FunctionToolCall(name=step.name, arguments="{}", call_id=f"call-{step.name}")
            delta = llm.ChoiceDelta(role="assistant", tool_calls=[call])
        else:
            delta = llm.ChoiceDelta(role="assistant", content=step)
        self._event_ch.send_nowait(llm.ChatChunk(id="scripted", delta=delta))


class ScriptedLLM(llm.LLM[Any]):
    """Plays `steps` in order (text replies or tool calls); records what each call saw."""

    def __init__(self, steps: Sequence[Step]) -> None:
        super().__init__()
        self.steps: list[Step] = list(steps)
        self.calls: list[tuple[str, list[str], dict[str, str]]] = []

    @property
    def model(self) -> str:
        return "scripted-llm"

    def chat(
        self,
        *,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool] | None = None,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
        parallel_tool_calls: NotGivenOr[bool] = NOT_GIVEN,
        tool_choice: NotGivenOr[llm.ToolChoice] = NOT_GIVEN,
        extra_kwargs: NotGivenOr[dict[str, Any]] = NOT_GIVEN,
    ) -> _ScriptedStream:
        prompt = "\n".join(
            (item.text_content or "") for item in chat_ctx.items if isinstance(item, llm.ChatMessage)
        )
        descriptions = {
            t.info.name: (t.info.description or "") for t in tools or [] if isinstance(t, llm.FunctionTool)
        }
        self.calls.append((prompt, sorted(descriptions), descriptions))
        step = self.steps.pop(0) if self.steps else "Okay."
        return _ScriptedStream(
            self, step=step, chat_ctx=chat_ctx, tools=list(tools or []), conn_options=conn_options
        )


class _FlowFactory(ProviderFactory):
    def __init__(self, conversation: llm.LLM[Any], workflow: llm.LLM[Any]) -> None:
        self.conversation = conversation
        self.workflow = workflow

    def build_all(self, resolved: Any, *, optional: Any = None) -> BuiltProviders:
        return BuiltProviders(llm=self.conversation, tts=FakeTTS(), workflow_llm=self.workflow)


def _flow_config(flow: dict[str, Any], **kwargs: Any) -> ResolvedAgentConfig:
    base = resolved_config(channel="text", **kwargs)
    config = base.config.model_copy(update={"flow": FlowSpec.model_validate(flow)})
    return base.model_copy(update={"config": config})


INTAKE_FLOW: dict[str, Any] = {
    "nodes": [
        {"id": "start", "kind": "start", "greeting": "Hi, this is intake."},
        {
            "id": "collect",
            "kind": "agent",
            "label": "Collect name",
            "instructions": "Ask for the caller's full name.",
            "extract": ["name"],
        },
        {
            "id": "confirm",
            "kind": "agent",
            "label": "Confirm",
            "instructions": "Confirm the name {{ name }} with the caller.",
        },
        {"id": "done", "kind": "end", "farewell": "Goodbye {{ name }}.", "disposition": "completed"},
        {"id": "g", "kind": "global", "instructions": "You work for Acme Insurance."},
    ],
    "edges": [
        {"id": "e1", "source": "start", "target": "collect", "condition": "always"},
        {
            "id": "e2",
            "source": "collect",
            "target": "confirm",
            "condition": "The caller has said their full name.",
            "transition_speech": "Thanks {{ name }}, one moment.",
        },
        {"id": "e3", "source": "confirm", "target": "done", "condition": "The caller confirmed the name."},
    ],
    "variables": [{"name": "name", "type": "string", "description": "caller full name", "required": True}],
}


async def _wait_for(predicate: Callable[[], bool], timeout_s: float = 3.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.01)


async def _start(
    resolved: ResolvedAgentConfig, conversation: llm.LLM[Any], workflow: llm.LLM[Any]
) -> tuple[FakeApi, FakeJobContext, RoomlessStarter]:
    api = FakeApi(resolved)
    ctx = FakeJobContext(_metadata())
    starter = RoomlessStarter()
    await run_session(ctx, _deps(api, factory=_FlowFactory(conversation, workflow), session_starter=starter))
    assert starter.session is not None
    return api, ctx, starter


def _history_kinds(starter: RoomlessStarter) -> list[str]:
    assert starter.session is not None
    kinds: list[str] = []
    for item in starter.session.history.items:
        if isinstance(item, llm.ChatMessage) and item.role in ("user", "assistant") and item.text_content:
            kinds.append(f"{item.role}:{item.text_content}")
        elif item.type == "agent_handoff":
            kinds.append(f"handoff:{item.old_agent_id}->{item.new_agent_id}")
    return kinds


# -------------------------------------------------------------------- tests


async def test_three_node_flow_transitions_extracts_and_ends_with_a_disposition() -> None:
    conversation = ScriptedLLM(
        ["Hi, this is intake.", ToolCall("go_to_confirm"), "Is Ada Lovelace right?", ToolCall("go_to_done")]
    )
    workflow = FakeLLM([json.dumps({"name": "Ada Lovelace"})])
    api, ctx, starter = await _start(_flow_config(INTAKE_FLOW), conversation, workflow)
    session = starter.session
    assert session is not None

    # A single start edge begins directly at its target, greeting with the start node's line.
    assert isinstance(starter.agent, FlowNodeAgent)
    assert starter.agent.id == "collect"
    await session.run(user_input="My name is Ada Lovelace.")
    await _wait_for(lambda: session.current_agent.id == "confirm")
    await _wait_for(lambda: len(conversation.calls) >= 3)

    # The edge became a go_to tool whose description is the condition.
    _prompt, names, descriptions = conversation.calls[1]
    assert "go_to_confirm" in names
    assert descriptions["go_to_confirm"] == "The caller has said their full name."
    # The next node was rendered with the variable extracted on the transition, and it
    # carries the conversation (the caller never repeats themselves).
    prompt_b, names_b, _ = conversation.calls[2]
    assert "Confirm the name Ada Lovelace with the caller." in prompt_b
    assert "My name is Ada Lovelace." in prompt_b
    assert "You work for Acme Insurance." in prompt_b  # global node prefix
    assert names_b == ["go_to_done"]

    state = session.userdata
    assert isinstance(state, FlowUserdata)
    assert state.flow.variables == {"name": "Ada Lovelace"}

    await session.run(user_input="Yes, that's right.")
    await _wait_for(lambda: bool(ctx.shutdown_reasons))
    assert ctx.shutdown_reasons == ["flow reached end node done"]
    assert state.flow.disposition == "completed"
    assert state.flow.path == ["start", "collect", "confirm", "done"]

    kinds = _history_kinds(starter)
    # transition_speech (rendered after extraction) is spoken before the handoff to the next node.
    speech = kinds.index("assistant:Thanks Ada Lovelace, one moment.")
    assert speech < kinds.index("handoff:collect->confirm")
    assert kinds[-1] == "assistant:Goodbye Ada Lovelace."

    await ctx.fire_shutdown("done")
    handoffs = [e.payload for e in api.events_of("handoff")]
    assert handoffs == [
        {"from": "start", "to": "collect", "edge_id": "e1", "reason": "start"},
        {"from": "collect", "to": "confirm", "edge_id": "e2", "reason": "edge"},
        {"from": "confirm", "to": "done", "edge_id": "e3", "reason": "edge"},
    ]
    (ended,) = [e.payload for e in api.events_of("flow_ended")]
    assert ended["completed"] is True
    assert ended["disposition"] == "completed"
    assert ended["variables"] == {"name": "Ada Lovelace"}
    assert ended["webhook_event"] is True


async def test_max_turns_forces_the_fallback_edge() -> None:
    flow = {
        "nodes": [
            {"id": "start", "kind": "start", "greeting": "Hello."},
            {"id": "ask", "kind": "agent", "instructions": "Ask what they need.", "max_turns": 1},
            {"id": "human", "kind": "agent", "instructions": "Apologise and take a message."},
            {"id": "other", "kind": "agent", "instructions": "Other branch."},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "ask", "condition": "always"},
            {"id": "low", "source": "ask", "target": "other", "condition": "never", "priority": 0},
            {"id": "high", "source": "ask", "target": "human", "condition": "stuck", "priority": 5},
        ],
    }
    conversation = ScriptedLLM(["Hello.", "What do you need?", "Let me take a message."])
    api, ctx, starter = await _start(_flow_config(flow), conversation, FakeLLM(["{}"]))
    session = starter.session
    assert session is not None

    await session.run(user_input="Um, I am not sure.")
    await _wait_for(lambda: session.current_agent.id == "human")
    await _wait_for(lambda: len(conversation.calls) >= 3)
    assert "Apologise and take a message." in conversation.calls[2][0]

    await ctx.fire_shutdown("done")
    handoffs = [e.payload for e in api.events_of("handoff")]
    assert handoffs[-1] == {"from": "ask", "to": "human", "edge_id": "high", "reason": "max_turns"}
    (ended,) = [e.payload for e in api.events_of("flow_ended")]
    assert ended["completed"] is False
    assert ended["current_node"] == "human"


async def test_start_node_routes_when_it_has_several_edges() -> None:
    flow = {
        "nodes": [
            {"id": "start", "kind": "start", "greeting": "Hi."},
            {"id": "claims", "kind": "agent", "instructions": "Claims."},
            {"id": "billing", "kind": "agent", "instructions": "Billing."},
        ],
        "edges": [
            {"id": "a", "source": "start", "target": "claims", "condition": "The caller wants a claim."},
            {"id": "b", "source": "start", "target": "billing", "condition": "The caller asks about a bill."},
        ],
    }
    conversation = ScriptedLLM(["Hi.", ToolCall("go_to_billing"), "Sure, billing here."])
    _api, _ctx, starter = await _start(_flow_config(flow), conversation, FakeLLM(["{}"]))
    session = starter.session
    assert session is not None
    assert starter.agent.id == "start"
    await session.run(user_input="Question about my bill.")
    await _wait_for(lambda: session.current_agent.id == "billing")
    assert conversation.calls[1][1] == ["go_to_billing", "go_to_claims"]


async def test_transfer_without_a_handler_stays_in_the_node() -> None:
    flow = {
        "nodes": [
            {"id": "start", "kind": "start", "greeting": "Hi."},
            {"id": "help", "kind": "agent", "instructions": "Help."},
            {"id": "agent_desk", "kind": "transfer", "to": "+15550100"},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "help", "condition": "always"},
            {"id": "e2", "source": "help", "target": "agent_desk", "condition": "Caller wants a human."},
        ],
    }
    conversation = ScriptedLLM(["Hi.", ToolCall("go_to_agent_desk"), "Sorry, I can keep helping."])
    api, ctx, starter = await _start(_flow_config(flow), conversation, FakeLLM(["{}"]))
    session = starter.session
    assert session is not None
    await session.run(user_input="Get me a human.")
    await _wait_for(lambda: len(conversation.calls) >= 3)
    assert session.current_agent.id == "help"
    await ctx.fire_shutdown("done")
    (transfer,) = [e.payload for e in api.events_of("transfer")]
    assert transfer["status"] == "unavailable"


async def test_teardown_extracts_the_current_node_and_records_flow_ended() -> None:
    conversation = ScriptedLLM(["Hi.", "Thanks, noted."])
    workflow = FakeLLM([json.dumps({"name": "Grace Hopper"})])
    api, ctx, starter = await _start(_flow_config(INTAKE_FLOW), conversation, workflow)
    session = starter.session
    assert session is not None
    await session.run(user_input="I'm Grace Hopper.")
    await ctx.fire_shutdown("participant left")
    (ended,) = [e.payload for e in api.events_of("flow_ended")]
    assert ended["completed"] is False
    assert ended["current_node"] == "collect"
    assert ended["variables"] == {"name": "Grace Hopper"}
    assert ended["disposition"] is None


async def test_prompt_agents_are_untouched() -> None:
    api = FakeApi(resolved_config(channel="text"))
    ctx = FakeJobContext(_metadata())
    starter = RoomlessStarter()
    factory = _FlowFactory(FakeLLM(["Hi"]), FakeLLM(["{}"]))
    await run_session(ctx, _deps(api, factory=factory, session_starter=starter))
    assert not isinstance(starter.agent, FlowNodeAgent)
    await ctx.fire_shutdown("done")
    assert api.events_of("flow_ended") == []


@pytest.mark.parametrize(("greeting", "expected"), [("Hi {{ name }}!", "Hi !"), (None, "Voice greeting.")])
async def test_start_greeting_replaces_the_voice_greeting(greeting: str | None, expected: str) -> None:
    flow = json.loads(json.dumps(INTAKE_FLOW))
    flow["nodes"][0]["greeting"] = greeting
    conversation = ScriptedLLM([])
    await _start(_flow_config(flow, greeting="Voice greeting."), conversation, FakeLLM(["{}"]))
    await _wait_for(lambda: bool(conversation.calls))
    assert conversation.calls[0][0].endswith(f"Say exactly this and nothing more: {expected}")


class _RecordingUi(NoopUiChannel):
    """A no-op channel that records `set_block` calls."""

    def __init__(self) -> None:
        super().__init__("sess-1")
        self.blocks: list[tuple[str, dict[str, Any]]] = []

    async def set_block(self, block_id: str, state: dict[str, Any]) -> None:
        self.blocks.append((block_id, state))


async def test_nodes_get_their_own_tools_and_knowledge_and_publish_progress() -> None:
    from types import SimpleNamespace  # noqa: PLC0415

    from livekit.agents import function_tool  # noqa: PLC0415
    from lkap_contracts.ui_protocol import BlockSpec  # noqa: PLC0415

    from lkap_agent.platform_agent import platform_text_input_cb  # noqa: PLC0415

    def _tool(name: str) -> Any:
        async def _run() -> str:
            return name

        return function_tool(_run, name=name, description=f"The {name} tool.")

    flow = json.loads(json.dumps(INTAKE_FLOW))
    flow["nodes"][1]["tools"] = ["current_time", "not_a_tool"]
    flow["nodes"][1]["kb_ids"] = ["kb_claims", "kb_unknown"]
    flow["nodes"][4]["tools"] = ["push_note"]
    flow["nodes"][4]["kb_ids"] = ["kb_global"]
    resolved = _flow_config(flow, kb_ids=["kb_claims", "kb_global", "kb_other"])
    panel = resolved.config.panel.model_copy(
        update={"blocks": [BlockSpec(id="progress", type="custom", config={"kind": "flow_progress"})]}
    )
    resolved = resolved.model_copy(update={"config": resolved.config.model_copy(update={"panel": panel})})
    ui = _RecordingUi()
    conversation = ScriptedLLM(["Hi.", "Noted."])
    api = FakeApi(resolved)
    ctx = FakeJobContext(_metadata())
    starter = RoomlessStarter()
    deps = _deps(
        api,
        factory=_FlowFactory(conversation, FakeLLM(["{}"])),
        session_starter=starter,
        ui_channel_factory=lambda **_kw: ui,
        builtin_tools_builder=lambda *_a: [_tool("current_time"), _tool("push_note"), _tool("end_call")],
    )
    await run_session(ctx, deps)
    session = starter.session
    assert session is not None
    await _wait_for(lambda: bool(conversation.calls))

    await platform_text_input_cb(session, SimpleNamespace(text="What is covered?"))
    await _wait_for(lambda: len(conversation.calls) >= 2)
    # Global tools + node tools (unknown names dropped) + edge tools; `end_call` is not listed.
    assert conversation.calls[1][1] == ["current_time", "go_to_confirm", "push_note"]
    # Auto-inject searched only the node's and the global node's resolved knowledge bases.
    assert api.kb_queries and api.kb_queries[-1][0] == ["kb_global", "kb_claims"]
    # The flow position is mirrored into the flow_progress block.
    await _wait_for(lambda: bool(ui.blocks))
    block_id, state = ui.blocks[-1]
    assert block_id == "progress"
    assert state["current_node"] == "collect"
    assert state["path"] == ["start", "collect"]
