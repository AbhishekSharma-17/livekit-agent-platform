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
from lkap_contracts.flow import AgentNode, FlowSpec, StartNode
from lkap_contracts.tools import ToolExecution
from test_main import FakeJobContext, RoomlessStarter, _deps, _metadata

from lkap_agent.flow import FlowNodeAgent, FlowUserdata
from lkap_agent.flow.runtime import CANCELLED_BY_STEP_CHANGE
from lkap_agent.main import NoopUiChannel, run_session
from lkap_agent.providers.factory import BuiltProviders, ProviderFactory
from lkap_agent.tools.execution import resolve_execution, wrap_tool

# ------------------------------------------------------------------ doubles


class ToolCall:
    """A scripted step that calls one tool (no arguments unless given)."""

    def __init__(self, name: str, arguments: dict[str, Any] | None = None) -> None:
        self.name = name
        self.arguments = arguments or {}


#: A text reply, one tool call, or a batch of parallel tool calls in one generation.
Step = str | ToolCall | list[ToolCall]


class _ScriptedStream(llm.LLMStream):
    def __init__(self, parent: ScriptedLLM, *, step: Step, **kwargs: Any) -> None:
        self._step = step
        super().__init__(parent, **kwargs)

    async def _run(self) -> None:
        step = self._step
        if isinstance(step, ToolCall | list):
            calls = [step] if isinstance(step, ToolCall) else step
            tool_calls = [
                llm.FunctionToolCall(name=c.name, arguments=json.dumps(c.arguments), call_id=f"call-{c.name}")
                for c in calls
            ]
            delta = llm.ChoiceDelta(role="assistant", tool_calls=tool_calls)
        else:
            delta = llm.ChoiceDelta(role="assistant", content=step)
        self._event_ch.send_nowait(llm.ChatChunk(id="scripted", delta=delta))


class ScriptedLLM(llm.LLM[Any]):
    """Plays `steps` in order (text replies or tool calls); records what each call saw."""

    def __init__(self, steps: Sequence[Step]) -> None:
        super().__init__()
        self.steps: list[Step] = list(steps)
        self.calls: list[tuple[str, list[str], dict[str, str]]] = []
        #: The full context of each call, in the same order as `calls`.
        self.contexts: list[llm.ChatContext] = []

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
        self.contexts.append(chat_ctx.copy())
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

    # The edge became a go_to tool whose description is an instruction naming the step (R-V4-30).
    prompt_a, names, descriptions = conversation.calls[1]
    assert "go_to_confirm" in names
    assert descriptions["go_to_confirm"] == (
        "Call this to move to the step 'Confirm' when: The caller has said their full name."
    )
    assert "Your next steps: go_to_confirm — when The caller has said their full name." in prompt_a
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


ROUTER_FLOW: dict[str, Any] = {
    "nodes": [
        {"id": "start", "kind": "start", "greeting": "Hi."},
        {"id": "claims", "kind": "agent", "label": "Claims", "instructions": "Claims."},
        {"id": "billing", "kind": "agent", "label": "Billing", "instructions": "Billing."},
        {"id": "g", "kind": "global", "instructions": "You work for Acme.", "tools": ["search_knowledge"]},
    ],
    "edges": [
        {"id": "a", "source": "start", "target": "claims", "condition": "The caller wants a claim."},
        {"id": "b", "source": "start", "target": "billing", "condition": "The caller asks about a bill."},
    ],
}


async def test_a_routing_start_is_told_its_transitions_and_routes_on_the_tool() -> None:
    """R-V4-30: the router's prompt lists every go_to_* with its condition; descriptions are imperative."""
    conversation = ScriptedLLM(["Hi.", ToolCall("go_to_claims"), "Claims here."])
    _api, _ctx, starter = await _start(_flow_config(ROUTER_FLOW), conversation, FakeLLM(["{}"]))
    session = starter.session
    assert session is not None
    assert starter.agent.id == "start"

    await session.run(user_input="I'd like to file a claim.")
    await _wait_for(lambda: session.current_agent.id == "claims")

    prompt, names, descriptions = conversation.calls[1]
    assert names == ["go_to_billing", "go_to_claims"]
    assert (
        "You are at the start of the call. Your only job here is to find out which step applies and "
        "call its go_to_* tool; do not ask the next step's questions yourself."
    ) in prompt
    assert (
        "Conversation flow: you are in the step 'start'. Your next steps: "
        "go_to_claims — when The caller wants a claim; go_to_billing — when The caller asks about a bill. "
        "When one of these conditions is met, call that tool right away instead of answering, and do not "
        "do the next step's work yourself. Never mention steps, tools or transitions to the caller."
    ) in prompt
    assert all(d.startswith("Call this to move to the step") for d in descriptions.values())
    assert descriptions["go_to_claims"] == (
        "Call this to move to the step 'Claims' when: The caller wants a claim."
    )
    assert descriptions["go_to_billing"] == (
        "Call this to move to the step 'Billing' when: The caller asks about a bill."
    )
    # A node without outgoing edges gets no transitions note.
    assert "Your next steps" not in conversation.calls[2][0]


def _scopes(starter: RoomlessStarter) -> dict[str, list[str]]:
    """`kb_ids_for` of the start node and every agent node."""
    agent = starter.agent
    assert isinstance(agent, FlowNodeAgent)
    runtime = agent.runtime
    return {
        node.id: runtime.kb_ids_for(node)
        for node in runtime.spec.nodes
        if isinstance(node, AgentNode | StartNode)
    }


async def test_a_flow_that_scopes_no_knowledge_searches_all_of_the_agents() -> None:
    """R-V4-29: no `kb_ids` anywhere → every node, the router included, searches `[A, B]`."""
    from lkap_agent.tools.builtin import build_search_knowledge_tool  # noqa: PLC0415

    resolved = _flow_config(ROUTER_FLOW, kb_ids=["kb_a", "kb_b"], auto_inject=False)
    conversation = ScriptedLLM(
        ["Hi.", ToolCall("search_knowledge", {"query": "opening hours"}), "We open at nine."]
    )
    api = FakeApi(resolved)
    ctx = FakeJobContext(_metadata())
    starter = RoomlessStarter()
    deps = _deps(
        api,
        factory=_FlowFactory(conversation, FakeLLM(["{}"])),
        session_starter=starter,
        builtin_tools_builder=lambda session_ctx, *_a: [build_search_knowledge_tool(session_ctx)],
    )
    await run_session(ctx, deps)
    session = starter.session
    assert session is not None

    both = ["kb_a", "kb_b"]
    assert _scopes(starter) == {"start": both, "claims": both, "billing": both}
    await session.run(user_input="What are your opening hours?")
    await _wait_for(lambda: len(conversation.calls) >= 3)
    # The router's search_knowledge call reached the inner KbClient with the agent's knowledge bases.
    assert [(ids, query) for ids, query, _k in api.kb_queries] == [(["kb_a", "kb_b"], "opening hours")]


async def test_a_global_scope_applies_to_every_node_and_hides_the_rest() -> None:
    """R-V4-29: `global.kb_ids=[A]` only → `[A]` everywhere; B is unreachable."""
    flow = json.loads(json.dumps(ROUTER_FLOW))
    flow["nodes"][3]["kb_ids"] = ["kb_a"]
    _api, _ctx, starter = await _start(
        _flow_config(flow, kb_ids=["kb_a", "kb_b"]), ScriptedLLM(["Hi."]), FakeLLM(["{}"])
    )

    assert _scopes(starter) == {"start": ["kb_a"], "claims": ["kb_a"], "billing": ["kb_a"]}


async def test_one_node_scope_is_a_grant_to_that_node_only() -> None:
    """R-V4-29: only node X declares `[B]` → X sees `[B]`, every other node `[]` (today's semantics)."""
    flow = json.loads(json.dumps(ROUTER_FLOW))
    flow["nodes"][2]["kb_ids"] = ["kb_b"]
    _api, _ctx, starter = await _start(
        _flow_config(flow, kb_ids=["kb_a", "kb_b"]), ScriptedLLM(["Hi."]), FakeLLM(["{}"])
    )

    assert _scopes(starter) == {"start": [], "claims": [], "billing": ["kb_b"]}


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


# ------------------------------------------ V4-12: background tools wait for 1.8.3 on flows


BACKGROUND_FLOW: dict[str, Any] = {
    "nodes": [
        {"id": "start", "kind": "start"},
        {
            "id": "lookup",
            "kind": "agent",
            "label": "Look it up",
            "instructions": "Look the item up.",
            "tools": ["lookup_item", "crm"],
        },
    ],
    "edges": [{"id": "e1", "source": "start", "target": "lookup"}],
}


async def test_flow_node_background_tools_run_blocking_until_livekit_agents_1_8_3(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R-V4-39: #7321 can drop a handoff in 1.8.2, so a flow node never backgrounds a tool."""
    monkeypatch.setattr("livekit.agents.__version__", "1.8.2")  # the downgrade path (R-V4-54)
    from lkap_contracts.tools import HttpToolDefinition, McpServerDefinition, ToolExecution  # noqa: PLC0415

    from lkap_agent.tools.declarative import build_http_tools  # noqa: PLC0415

    http = HttpToolDefinition(
        name="lookup_item",
        description="Look up an item.",
        parameters={"type": "object", "properties": {}},
        method="GET",
        url="https://api.example.com/items",
        allowed_hosts=["api.example.com"],
        execution=ToolExecution(mode="background"),
    )
    mcp = McpServerDefinition(
        name="crm",
        url="https://mcp.example.com/mcp",
        tool_options={"search": ToolExecution(mode="background", report_progress=True)},
    )
    mcp_calls: list[dict[str, Any]] = []

    def _mcp_builder(defs: list[Any], **kwargs: Any) -> list[Any]:
        mcp_calls.append({"names": [d.name for d in defs], **kwargs})
        return []

    resolved = _flow_config(BACKGROUND_FLOW, tools=[http, mcp])
    api = FakeApi(resolved)
    ctx = FakeJobContext(_metadata())
    starter = RoomlessStarter()
    deps = _deps(
        api,
        factory=_FlowFactory(ScriptedLLM(["Hi."]), FakeLLM(["{}"])),
        session_starter=starter,
        declarative_tools_builder=lambda defs: build_http_tools(
            defs, platform_allowed_hosts=["api.example.com"]
        ),
        mcp_servers_builder=_mcp_builder,
    )

    await run_session(ctx, deps)

    agent = starter.agent
    assert isinstance(agent, FlowNodeAgent) and agent.id == "lookup"
    (tool,) = [t for t in agent.tools if getattr(t, "id", None) == "lookup_item"]
    assert tool.info.flags == llm.ToolFlag.NONE
    assert tool.info.on_duplicate == "allow"
    assert {"names": ["crm"], "flow_node": True} in mcp_calls
    await ctx.fire_shutdown("done")
    infos = [e.payload["message"] for e in api.events_of("info")]
    assert any("1.8.3" in message and "lookup_item" in message for message in infos)


# ------------------------------- V4-19: no draining-node reply after a handoff (R-V4-64)


SIBLING_FLOW: dict[str, Any] = {
    "nodes": [
        {"id": "start", "kind": "start", "greeting": "Hi."},
        {"id": "claims", "kind": "agent", "label": "Claims", "instructions": "You are the claims desk."},
        {"id": "billing", "kind": "agent", "label": "Billing", "instructions": "You are the billing desk."},
        {"id": "g", "kind": "global", "instructions": "You work for Acme.", "tools": ["lookup_policy"]},
    ],
    "edges": [
        {"id": "a", "source": "start", "target": "claims", "condition": "The caller wants a claim."},
        {"id": "b", "source": "start", "target": "billing", "condition": "The caller asks about a bill."},
    ],
}

CLAIMS_LINE = "Claims desk: what is your policy number?"
POLICY_OUTPUT = "Policy P-1 is active."
USER_LINE = "I'd like to file a claim."


def _lookup_policy(delay_s: float) -> Any:
    """A plain (blocking) global tool that answers after `delay_s`."""
    from livekit.agents import function_tool  # noqa: PLC0415

    async def _run() -> str:
        await asyncio.sleep(delay_s)
        return POLICY_OUTPUT

    return function_tool(_run, name="lookup_policy", description="Look up the caller's policy.")


async def _start_sibling_flow(conversation: ScriptedLLM, delay_s: float) -> RoomlessStarter:
    api = FakeApi(_flow_config(SIBLING_FLOW))
    ctx = FakeJobContext(_metadata())
    starter = RoomlessStarter()
    deps = _deps(
        api,
        factory=_FlowFactory(conversation, FakeLLM(["{}"])),
        session_starter=starter,
        builtin_tools_builder=lambda *_a: [_lookup_policy(delay_s)],
    )
    await run_session(ctx, deps)
    assert starter.session is not None
    await _wait_for(lambda: "assistant:Hi." in _history_kinds(starter))
    return starter


def _after_user(starter: RoomlessStarter) -> list[str]:
    kinds = _history_kinds(starter)
    return kinds[kinds.index(f"user:{USER_LINE}") + 1 :]


def _pair_in(ctx: llm.ChatContext, name: str) -> tuple[bool, list[str]]:
    calls = [i for i in ctx.items if i.type == "function_call" and i.name == name]
    outputs = [i.output for i in ctx.items if i.type == "function_call_output" and i.name == name]
    return bool(calls), outputs


@pytest.mark.parametrize(
    ("batch", "delay_s"),
    [
        # The 2a shape: the sibling finishes 1.5 s after the edge tool.
        ([ToolCall("go_to_claims"), ToolCall("lookup_policy")], 1.5),
        # The sibling finishes first (listed first, no await).
        ([ToolCall("lookup_policy"), ToolCall("go_to_claims")], 0.0),
    ],
    ids=["sibling-after-edge", "sibling-before-edge"],
)
async def test_handoff_batch_the_router_never_replies_and_the_target_answers_first(
    batch: list[ToolCall], delay_s: float
) -> None:
    """R-V4-64: an edge + a sibling in one batch → no router message; the claims node speaks first."""
    conversation = ScriptedLLM(["Hi.", batch, CLAIMS_LINE])
    starter = await _start_sibling_flow(conversation, delay_s)
    session = starter.session
    assert session is not None
    assert starter.agent.id == "start"

    await session.run(user_input=USER_LINE)
    await _wait_for(lambda: session.current_agent.id == "claims", timeout_s=5.0)
    await _wait_for(lambda: f"assistant:{CLAIMS_LINE}" in _history_kinds(starter), timeout_s=5.0)
    await asyncio.sleep(0.2)  # room for a stray reply to land

    # Nothing between the caller's line and the handoff; the first message after it is the target's.
    assert _after_user(starter) == ["handoff:start->claims", f"assistant:{CLAIMS_LINE}"]
    # Only the greeting, the batch and the claims node's reply reached the LLM: no router tool reply.
    assert len(conversation.calls) == 3
    assert "You are the claims desk." in conversation.calls[2][0]
    state = session.userdata
    assert isinstance(state, FlowUserdata)
    assert state.flow.path == ["start", "claims"]
    # The sibling's call/output pair rode into the claims node's context, and its first
    # generation saw it; the edge's own pair is dropped (the claims node has no such tool).
    claims = session.current_agent
    assert _pair_in(claims.chat_ctx, "lookup_policy") == (True, [POLICY_OUTPUT])
    assert _pair_in(conversation.contexts[2], "lookup_policy") == (True, [POLICY_OUTPUT])
    assert _pair_in(conversation.contexts[2], "go_to_claims") == (False, [])


async def test_a_batch_without_an_edge_still_gets_its_tool_reply() -> None:
    """R-V4-64 is scoped to handoffs: a sibling alone is answered as before."""
    conversation = ScriptedLLM(["Hi.", [ToolCall("lookup_policy")], "Your policy is active."])
    starter = await _start_sibling_flow(conversation, 0.0)
    session = starter.session
    assert session is not None

    await session.run(user_input=USER_LINE)
    await _wait_for(lambda: len(conversation.calls) >= 3)
    await _wait_for(lambda: "assistant:Your policy is active." in _history_kinds(starter))

    assert session.current_agent.id == "start"
    assert _after_user(starter) == ["assistant:Your policy is active."]


# ------------- V4-20: cancelled and late sibling results reach the node the caller is on (R-V4-69)


def _background_lookup(delay_s: float, *, cancellable: bool) -> Any:
    """`lookup_policy` as a real background tool: announces at once, answers after `delay_s`."""
    resolved = resolve_execution(
        name="lookup_policy",
        kind="builtin",
        is_read=True,
        declared=ToolExecution(mode="background", cancellable=cancellable),
        agent_default="blocking",
        flow_node=True,
    )
    assert resolved.mode == "background" and resolved.cancellable is cancellable
    return wrap_tool(_lookup_policy(delay_s), resolved)


async def _start_with_tool(
    conversation: ScriptedLLM, tool: Any
) -> tuple[FakeApi, FakeJobContext, RoomlessStarter]:
    api = FakeApi(_flow_config(SIBLING_FLOW))
    ctx = FakeJobContext(_metadata())
    starter = RoomlessStarter()
    deps = _deps(
        api,
        factory=_FlowFactory(conversation, FakeLLM(["{}"])),
        session_starter=starter,
        builtin_tools_builder=lambda *_a: [tool],
    )
    await run_session(ctx, deps)
    assert starter.session is not None
    await _wait_for(lambda: "assistant:Hi." in _history_kinds(starter))
    return api, ctx, starter


async def _hand_off_with_sibling(conversation: ScriptedLLM, starter: RoomlessStarter) -> Any:
    session = starter.session
    assert session is not None
    await session.run(user_input=USER_LINE)
    await _wait_for(lambda: session.current_agent.id == "claims", timeout_s=5.0)
    await _wait_for(lambda: f"assistant:{CLAIMS_LINE}" in _history_kinds(starter), timeout_s=5.0)
    await asyncio.sleep(0.2)  # room for a stray reply to land
    # The R-V4-64 invariants hold: no router reply, the claims node speaks first.
    assert _after_user(starter) == ["handoff:start->claims", f"assistant:{CLAIMS_LINE}"]
    assert len(conversation.calls) == 3
    return session.current_agent


def _late_result_events(api: FakeApi) -> list[dict[str, Any]]:
    return [e.payload for e in api.events_of("info") if e.payload.get("message") == "flow_late_result"]


async def test_a_sibling_cancelled_by_the_step_change_leaves_a_cancelled_output_in_the_target() -> None:
    """R-V4-69 (a): the claims node sees the lookup was cancelled, not only its announce."""
    conversation = ScriptedLLM(["Hi.", [ToolCall("go_to_claims"), ToolCall("lookup_policy")], CLAIMS_LINE])
    api, ctx, starter = await _start_with_tool(conversation, _background_lookup(30.0, cancellable=True))

    claims = await _hand_off_with_sibling(conversation, starter)

    called, outputs = _pair_in(claims.chat_ctx, "lookup_policy")
    assert called
    assert len(outputs) == 2  # the announce, then the cancellation
    assert outputs[-1].startswith("Cancelled:")
    assert outputs[-1] == CANCELLED_BY_STEP_CHANGE
    assert POLICY_OUTPUT not in outputs
    # The cancellation is its own call/output pair (the SDK's `_final` id), never a
    # second output for the announce's call id.
    call_ids = [i.call_id for i in claims.chat_ctx.items if i.type == "function_call_output"]
    assert len(call_ids) == len(set(call_ids))
    # The claims node's first generation already saw it.
    assert _pair_in(conversation.contexts[2], "lookup_policy")[1][-1] == CANCELLED_BY_STEP_CHANGE
    await ctx.fire_shutdown("done")
    assert _late_result_events(api) == []


async def test_a_non_cancellable_sibling_s_late_result_is_carried_into_the_target() -> None:
    """R-V4-69 (b): the drain awaits it; its final pair lands in the claims node, once, no forced reply."""
    conversation = ScriptedLLM(["Hi.", [ToolCall("go_to_claims"), ToolCall("lookup_policy")], CLAIMS_LINE])
    api, ctx, starter = await _start_with_tool(conversation, _background_lookup(1.5, cancellable=False))

    claims = await _hand_off_with_sibling(conversation, starter)

    called, outputs = _pair_in(claims.chat_ctx, "lookup_policy")
    assert called
    assert outputs[-1] == POLICY_OUTPUT
    assert not any(o.startswith("Cancelled:") for o in outputs)
    # The drain waited for the result before the claims node started, so its first
    # generation answered with the result in context.
    assert _pair_in(conversation.contexts[2], "lookup_policy")[1][-1] == POLICY_OUTPUT
    await ctx.fire_shutdown("done")
    events = _late_result_events(api)
    assert len(events) == 1
    assert events[0]["tool"] == "lookup_policy"
    assert (events[0]["from"], events[0]["to"]) == ("start", "claims")


# ------------------------------------------ V5-08: a `steps` block follows the flow (D-V5-33)


async def test_a_flow_steps_block_and_a_flow_progress_block_both_follow_the_flow() -> None:
    from lkap_contracts.ui_protocol import BlockSpec  # noqa: PLC0415

    resolved = _flow_config(INTAKE_FLOW)
    panel = resolved.config.panel.model_copy(
        update={
            "blocks": [
                BlockSpec(id="progress", type="custom", config={"kind": "flow_progress"}),
                BlockSpec(id="steps", type="steps", config={"source": "flow"}),
                BlockSpec(id="mine", type="steps"),  # driven by set_steps: the flow leaves it alone
            ]
        }
    )
    resolved = resolved.model_copy(update={"config": resolved.config.model_copy(update={"panel": panel})})
    ui = _RecordingUi()
    conversation = ScriptedLLM(
        ["Hi, this is intake.", ToolCall("go_to_confirm"), "Is Ada Lovelace right?", ToolCall("go_to_done")]
    )
    api = FakeApi(resolved)
    ctx = FakeJobContext(_metadata())
    starter = RoomlessStarter()
    deps = _deps(
        api,
        factory=_FlowFactory(conversation, FakeLLM([json.dumps({"name": "Ada Lovelace"})])),
        session_starter=starter,
        ui_channel_factory=lambda **_kw: ui,
    )
    await run_session(ctx, deps)
    session = starter.session
    assert session is not None

    def latest(block_id: str) -> dict[str, Any]:
        return [state for bid, state in ui.blocks if bid == block_id][-1]

    await _wait_for(lambda: any(bid == "steps" for bid, _ in ui.blocks))
    # Compatibility: the custom flow_progress block keeps its raw FlowState mirror.
    assert latest("progress")["current_node"] == "collect"
    assert latest("steps") == {
        "steps": [
            {"id": "collect", "label": "Collect name", "status": "active", "note": None, "at": None},
            {"id": "confirm", "label": "Confirm", "status": "pending", "note": None, "at": None},
        ],
        "current": "collect",
    }

    await session.run(user_input="My name is Ada Lovelace.")
    await _wait_for(lambda: latest("steps")["current"] == "confirm")
    assert [s["status"] for s in latest("steps")["steps"]] == ["done", "active"]
    assert latest("progress")["path"] == ["start", "collect", "confirm"]

    await session.run(user_input="Yes, that's right.")
    await _wait_for(lambda: bool(ctx.shutdown_reasons))
    await _wait_for(lambda: latest("steps")["current"] is None)
    assert [s["status"] for s in latest("steps")["steps"]] == ["done", "done"]
    assert latest("progress")["disposition"] == "completed"
    assert not any(bid == "mine" for bid, _ in ui.blocks)


# ---------------------------- V4-21: an HTTP tool's silent_reply on flow agents (R-V4-71, ask #170)


NARRATION = "Your status came back as saved."


def _silent_http_row() -> Any:
    from lkap_contracts.tools import HttpToolDefinition  # noqa: PLC0415

    return HttpToolDefinition(
        name="push_status",
        description="Record the caller's status.",
        parameters={"type": "object", "properties": {}},
        method="GET",
        url="https://api.example.com/status",
        allowed_hosts=["api.example.com"],
        silent_reply=True,
    )


def _push_status_tool(calls: list[str]) -> Any:
    """Stands in for the declarative HTTP tool of the same name (the handler works on names)."""
    from livekit.agents import function_tool  # noqa: PLC0415

    async def _run() -> str:
        calls.append("push_status")
        return '{"status": "saved"}'

    return function_tool(_run, name="push_status", description="Record the caller's status.")


def _silent_flow_deps(
    resolved: ResolvedAgentConfig, conversation: llm.LLM[Any], workflow: llm.LLM[Any], calls: list[str]
) -> tuple[FakeJobContext, RoomlessStarter, Any]:
    ctx = FakeJobContext(_metadata())
    starter = RoomlessStarter()
    deps = _deps(
        FakeApi(resolved),
        factory=_FlowFactory(conversation, workflow),
        session_starter=starter,
        declarative_tools_builder=lambda _defs: [_push_status_tool(calls)],
    )
    return ctx, starter, deps


def _silent_flow(*, silent: bool) -> ResolvedAgentConfig:
    flow = json.loads(json.dumps(INTAKE_FLOW))
    flow["nodes"][1]["tools"] = ["push_status"]
    return _flow_config(flow, mode="cascaded", tools=[_silent_http_row()] if silent else [])


async def test_assemble_flow_branch_passes_the_silent_http_names_to_the_entry_node() -> None:
    """Ask #170: `_assemble`'s flow branch hands the names to `FlowServices`; the entry node carries them."""
    from lkap_contracts.tools import McpServerDefinition  # noqa: PLC0415

    from lkap_agent.flow import prepare_flow_resolved  # noqa: PLC0415
    from lkap_agent.main import _assemble  # noqa: PLC0415
    from lkap_agent.session_builder import prepare_resolved  # noqa: PLC0415

    speaking = _silent_http_row().model_copy(update={"name": "lookup_status", "silent_reply": False})
    mcp = McpServerDefinition(name="crm", url="https://mcp.example.com/mcp")
    resolved = _flow_config(INTAKE_FLOW, mode="cascaded", tools=[_silent_http_row(), speaking, mcp])
    ctx, _starter, deps = _silent_flow_deps(resolved, FakeLLM(["Hi"]), FakeLLM(["{}"]), [])

    _plan, agent = _assemble(ctx, deps, prepare_flow_resolved(prepare_resolved(resolved)))

    assert isinstance(agent, FlowNodeAgent)
    assert agent.runtime.services.silent_reply_tools == frozenset({"push_status"})
    assert agent._silent_reply_tools == frozenset({"push_status"})


@pytest.mark.parametrize(("silent", "narrated"), [(True, False), (False, True)], ids=["silent", "control"])
async def test_a_silent_http_tool_on_a_flow_node_gets_no_reply_on_1_8_3(
    monkeypatch: pytest.MonkeyPatch, silent: bool, narrated: bool
) -> None:
    """R-V4-71 on flows: a batch of only the silent HTTP tool ends the turn in cascaded mode (1.8.3)."""
    monkeypatch.setattr("livekit.agents.__version__", "1.8.3")
    conversation = ScriptedLLM(["Hi, this is intake.", ToolCall("push_status"), NARRATION])
    calls: list[str] = []
    ctx, starter, deps = _silent_flow_deps(_silent_flow(silent=silent), conversation, FakeLLM(["{}"]), calls)
    await run_session(ctx, deps)
    session = starter.session
    assert session is not None
    await _wait_for(lambda: bool(conversation.calls))

    await session.run(user_input="Please update my status.")
    await asyncio.sleep(0.2)

    assert calls == ["push_status"]
    assert session.current_agent.id == "collect"
    assert (f"assistant:{NARRATION}" in _history_kinds(starter)) is narrated
    assert len(conversation.calls) == (3 if narrated else 2)
    await ctx.fire_shutdown("done")


async def test_the_unhonoured_line_is_logged_once_per_flow_session_below_1_8_3(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Below 1.8.3 every node carries the names, but only the entry node logs; a transition adds none."""
    import structlog  # noqa: PLC0415

    monkeypatch.setattr("livekit.agents.__version__", "1.8.2")
    conversation = ScriptedLLM(["Hi, this is intake.", ToolCall("go_to_confirm"), "Is Ada Lovelace right?"])
    workflow = FakeLLM([json.dumps({"name": "Ada Lovelace"})])
    ctx, starter, deps = _silent_flow_deps(_silent_flow(silent=True), conversation, workflow, [])

    with structlog.testing.capture_logs() as logs:
        await run_session(ctx, deps)
        session = starter.session
        assert session is not None
        await session.run(user_input="My name is Ada Lovelace.")
        await _wait_for(lambda: session.current_agent.id == "confirm")

    confirm = session.current_agent
    assert isinstance(confirm, FlowNodeAgent)
    assert "push_status" in confirm._silent_reply_tools
    unhonoured = [line for line in logs if "silent_reply is not honoured" in str(line.get("event"))]
    assert len(unhonoured) == 1
    assert unhonoured[0]["tools"] == ["push_status"]
    await ctx.fire_shutdown("done")
