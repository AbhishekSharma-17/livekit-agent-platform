"""V6-17: the flow `tool` node, end to end through `run_session` with scripted LLMs (no network).

A real `AgentSession` runs in text mode, so the tool step's call goes through livekit-agents'
own `execute_function_call` on the real session, and the handoffs are the SDK's own.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from fakes.fake_api import FakeApi, resolved_config
from fakes.fake_llm import FakeLLM
from livekit.agents import RunContext, ToolError, function_tool, llm
from lkap_contracts.agent_config import ResolvedAgentConfig
from lkap_contracts.flow import FlowSpec
from lkap_contracts.tools import HttpToolDefinition
from test_flow_runtime import ScriptedLLM, ToolCall, _FlowFactory, _history_kinds, _wait_for
from test_main import FakeJobContext, RoomlessStarter, _deps, _metadata

from lkap_agent.flow import FlowNodeAgent, FlowUserdata
from lkap_agent.flow.runtime import TOOL_STEP_FAILED_LINE
from lkap_agent.flow.tool_node import is_empty_result, render_arguments, unfence
from lkap_agent.main import run_session
from lkap_agent.tools.context import BOUND_VARIABLES_USERDATA_KEY, ToolCallContext
from lkap_agent.tools.declarative import build_http_tools
from lkap_agent.tools.untrusted import fence

# ------------------------------------------------------------------ pure helpers


@pytest.mark.parametrize(
    ("text", "content"),
    [
        (fence('{"a": 1}', source="http:x"), '{"a": 1}'),
        (fence("x" * 30, source="app:y", max_chars=10), "x" * 10),
        ("plain text", "plain text"),
        (fence("", source="mcp:z"), ""),
    ],
)
def test_unfence_returns_the_fenced_content(text: str, content: str) -> None:
    assert unfence(text) == content


@pytest.mark.parametrize(
    ("value", "empty"),
    [
        (None, True),
        ("", True),
        ("  ", True),
        ([], True),
        ({}, True),
        (0, False),
        (False, False),
        ("x", False),
    ],
)
def test_is_empty_result_counts_nothing_null_blank_and_empty_containers(value: Any, empty: bool) -> None:
    assert is_empty_result(value) is empty


def _context(variables: dict[str, Any], channel: str = "sip_in") -> ToolCallContext:
    session = SimpleNamespace(userdata={"lkap.variables": variables}, channel=channel, room=None)
    return ToolCallContext(session)


def test_render_arguments_fills_var_and_ctx_and_keeps_a_whole_variable_typed() -> None:
    variables = {"count": 3, "policy": "P-1"}
    rendered, missing = render_arguments(
        {"n": "{{ var.count }}", "text": "policy {{ var.policy }} on {{ ctx.channel }}", "flag": True},
        _context(variables),
        variables,
    )
    assert missing == []
    assert rendered == {"n": 3, "text": "policy P-1 on phone", "flag": True}


def test_render_arguments_reports_each_missing_value_once() -> None:
    rendered, missing = render_arguments(
        {"a": "{{ var.nope }}", "b": "{{ var.nope }} {{ ctx.caller_phone }}"}, _context({}, "web"), {}
    )
    assert [(ref.namespace, ref.name) for ref in missing] == [("var", "nope"), ("ctx", "caller_phone")]
    assert rendered["b"] == " "


# ------------------------------------------------------------------ end to end

LOOKUP_ROW = HttpToolDefinition(
    name="policy_lookup",
    description="Look up a policy.",
    parameters={
        "type": "object",
        "properties": {"policy": {"type": "string"}, "channel": {"type": "string"}},
    },
    method="GET",
    url="https://api.example.com/policies/{{ policy }}",
    allowed_hosts=["api.example.com"],
)


def _lookup_tool(calls: list[dict[str, Any]], result: str | Exception) -> Any:
    """Stands in for the declarative tool of the same name (a raw tool, as the builder makes)."""

    async def handler(raw_arguments: dict[str, object], context: RunContext[Any]) -> str:
        calls.append(dict(raw_arguments))
        if isinstance(result, Exception):
            raise result
        return fence(result, source="http:policy_lookup")

    return function_tool(
        handler,
        raw_schema={
            "name": "policy_lookup",
            "description": "Look up a policy.",
            "parameters": LOOKUP_ROW.parameters,
        },
    )


def _lookup_flow(*, error_edge: bool = True, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    on = {"ok": "found_it", "empty": "nothing"}
    if error_edge:
        on["error"] = "failed"
    nodes: list[dict[str, Any]] = [
        {"id": "start", "kind": "start", "greeting": "Hi."},
        {
            "id": "ask",
            "kind": "agent",
            "instructions": "Ask for the policy number.",
            "extract": ["policy_no"],
        },
        {
            "id": "lookup",
            "kind": "tool",
            "tool": "policy_lookup",
            "arguments": arguments
            if arguments is not None
            else {"policy": "{{ var.policy_no }}", "channel": "{{ ctx.channel }}"},
            "bindings": [{"path": "/holder", "to": "var:holder"}],
            "on": on,
        },
        {
            "id": "found",
            "kind": "agent",
            "label": "Found",
            "instructions": "Confirm the holder {{ holder }}.",
        },
        {"id": "sorry", "kind": "agent", "instructions": "Say no policy was found."},
        {"id": "done", "kind": "end", "disposition": "completed"},
    ]
    edges: list[dict[str, Any]] = [
        {"id": "e1", "source": "start", "target": "ask"},
        {
            "id": "e2",
            "source": "ask",
            "target": "lookup",
            "condition": "The caller gave the policy number.",
            "transition_speech": "One moment.",
        },
        {
            "id": "found_it",
            "source": "lookup",
            "target": "found",
            "transition_speech": "Found it, {{ holder }}.",
        },
        {"id": "nothing", "source": "lookup", "target": "sorry"},
        {"id": "e3", "source": "found", "target": "done", "condition": "confirmed"},
    ]
    if error_edge:
        nodes.append({"id": "oops", "kind": "agent", "instructions": "Apologise: the lookup failed."})
        edges.append({"id": "failed", "source": "lookup", "target": "oops"})
    return {
        "nodes": nodes,
        "edges": edges,
        "variables": [{"name": "policy_no"}, {"name": "holder"}],
    }


def _config(flow: dict[str, Any], **kwargs: Any) -> ResolvedAgentConfig:
    base = resolved_config(channel="text", tools=[LOOKUP_ROW])
    config = base.config.model_copy(update={"flow": FlowSpec.model_validate(flow)})
    return base.model_copy(update={"config": config, **kwargs})


async def _start(
    resolved: ResolvedAgentConfig, conversation: llm.LLM[Any], tools_builder: Any
) -> tuple[FakeApi, FakeJobContext, RoomlessStarter]:
    api = FakeApi(resolved)
    ctx = FakeJobContext(_metadata())
    starter = RoomlessStarter()
    workflow = FakeLLM([json.dumps({"policy_no": "P-1"})])
    deps = _deps(
        api,
        factory=_FlowFactory(conversation, workflow),
        session_starter=starter,
        declarative_tools_builder=tools_builder,
    )
    await run_session(ctx, deps)
    assert starter.session is not None
    return api, ctx, starter


async def _to_lookup(result: str | Exception, **flow: Any) -> tuple[Any, ...]:
    calls: list[dict[str, Any]] = []
    conversation = ScriptedLLM(["Hi.", ToolCall("go_to_lookup"), "Next step here."])
    api, ctx, starter = await _start(
        _config(_lookup_flow(**flow)), conversation, lambda _defs, **_: [_lookup_tool(calls, result)]
    )
    session = starter.session
    assert session is not None
    await session.run(user_input="My policy number is P-1.")
    return api, ctx, starter, session, conversation, calls


async def test_tool_step_calls_the_tool_binds_the_result_and_takes_the_ok_edge() -> None:
    api, ctx, starter, session, conversation, calls = await _to_lookup('{"holder": "Ada"}')
    await _wait_for(lambda: session.current_agent.id == "found")
    await _wait_for(lambda: len(conversation.calls) >= 3)

    # No model turn chose the tool: the arguments were filled from the variable and the session.
    assert calls == [{"policy": "P-1", "channel": "text"}]
    state = session.userdata
    assert isinstance(state, FlowUserdata)
    assert state.flow.variables["holder"] == "Ada"
    assert state.flow.path == ["start", "ask", "lookup", "found"]
    assert "holder" in starter.agent.runtime.services.ctx.userdata[BOUND_VARIABLES_USERDATA_KEY]

    # The next step reads the bound value fenced; the result itself never reaches the model.
    prompt, names, _ = conversation.calls[2]
    assert 'Confirm the holder <untrusted source="tool_binding">Ada</untrusted>.' in prompt
    assert "policy_lookup" not in names
    assert not any(
        isinstance(item, llm.FunctionCall) and item.name == "policy_lookup"
        for context in conversation.contexts
        for item in context.items
    )
    kinds = _history_kinds(starter)
    assert kinds.index("assistant:One moment.") < kinds.index("assistant:Found it, Ada.")
    assert kinds.index("assistant:Found it, Ada.") < kinds.index("handoff:ask->found")

    await ctx.fire_shutdown("done")
    handoffs = [e.payload for e in api.events_of("handoff")]
    assert handoffs == [
        {"from": "start", "to": "ask", "edge_id": "e1", "reason": "start"},
        {"from": "ask", "to": "lookup", "edge_id": "e2", "reason": "edge"},
        {"from": "lookup", "to": "found", "edge_id": "found_it", "reason": "ok"},
    ]
    (started,) = [e.payload for e in api.events_of("tool_call_started") if e.payload.get("flow_node")]
    assert started["tool"] == "policy_lookup"
    # The templates as written, never the rendered values.
    assert started["args_redacted"] == {"policy": "{{ var.policy_no }}", "channel": "{{ ctx.channel }}"}
    (ended,) = [e.payload for e in api.events_of("tool_call_ended") if e.payload.get("flow_node")]
    assert (ended["status"], ended["outcome"], ended["call_id"]) == ("done", "ok", started["call_id"])


@pytest.mark.parametrize(
    ("result", "target", "reason"),
    [
        ("[]", "sorry", "empty"),
        ('{"other": 1}', "sorry", "empty"),  # the binding found no value
        (ToolError("the service is down"), "oops", "error"),
    ],
    ids=["empty-result", "nothing-bound", "error"],
)
async def test_tool_step_branches_on_the_outcome(result: str | Exception, target: str, reason: str) -> None:
    api, ctx, _starter, session, _conversation, calls = await _to_lookup(result)
    await _wait_for(lambda: session.current_agent.id == target)
    assert len(calls) == 1
    await ctx.fire_shutdown("done")
    handoffs = [e.payload for e in api.events_of("handoff")]
    assert handoffs[-1]["reason"] == reason
    assert handoffs[-1]["to"] == target


async def test_tool_step_with_a_missing_value_calls_nothing_and_takes_the_error_edge() -> None:
    api, ctx, _starter, session, _conversation, calls = await _to_lookup(
        '{"holder": "Ada"}', arguments={"policy": "{{ var.never_set }}"}
    )
    await _wait_for(lambda: session.current_agent.id == "oops")
    assert calls == []
    await ctx.fire_shutdown("done")
    (ended,) = [e.payload for e in api.events_of("tool_call_ended") if e.payload.get("flow_node")]
    assert (ended["outcome"], ended["reason"]) == ("error", "missing_values")


async def test_tool_step_error_without_an_error_edge_ends_the_call_with_a_flow_error() -> None:
    api, ctx, starter, _session, _conversation, _calls = await _to_lookup(
        ToolError("the service is down"), error_edge=False
    )
    await _wait_for(lambda: bool(ctx.shutdown_reasons))
    assert ctx.shutdown_reasons == ["flow tool step lookup failed"]
    assert f"assistant:{TOOL_STEP_FAILED_LINE}" in _history_kinds(starter)
    await ctx.fire_shutdown("done")
    (error,) = [e.payload for e in api.events_of("flow_error")]
    assert (error["node"], error["tool"], error["outcome"]) == ("lookup", "policy_lookup", "error")
    (ended,) = [e.payload for e in api.events_of("flow_ended")]
    assert ended["completed"] is False
    assert ended["current_node"] == "lookup"


async def test_a_start_node_whose_one_edge_is_a_tool_step_runs_it_after_the_greeting() -> None:
    flow = _lookup_flow(arguments={"policy": "P-9"})
    flow["edges"] = [e for e in flow["edges"] if e["id"] not in ("e1", "e2")]
    flow["edges"].append({"id": "s", "source": "start", "target": "lookup"})
    flow["nodes"] = [n for n in flow["nodes"] if n["id"] != "ask"]
    flow["nodes"][0]["greeting"] = "Hi, one moment."
    calls: list[dict[str, Any]] = []
    conversation = ScriptedLLM(["Hi, one moment.", "Is Ada the holder?"])
    api, ctx, starter = await _start(
        _config(flow), conversation, lambda _defs, **_: [_lookup_tool(calls, '{"holder": "Ada"}')]
    )
    session = starter.session
    assert session is not None
    assert starter.agent.id == "start"
    await _wait_for(lambda: session.current_agent.id == "found")
    await _wait_for(lambda: len(conversation.calls) >= 2)
    assert calls == [{"policy": "P-9"}]
    # The start node never routed by itself: no edge tool, no router instruction.
    first_prompt, first_names, _ = conversation.calls[0]
    assert first_names == []
    assert "Your only job here" not in first_prompt
    await ctx.fire_shutdown("done")
    handoffs = [e.payload for e in api.events_of("handoff")]
    assert handoffs[:2] == [
        {"from": "start", "to": "lookup", "edge_id": "s", "reason": "start"},
        {"from": "lookup", "to": "found", "edge_id": "found_it", "reason": "ok"},
    ]


def _mocked_builder(defs: list[Any], **kwargs: Any) -> list[Any]:
    return build_http_tools(defs, platform_allowed_hosts=["api.example.com"], **kwargs)


async def test_tool_step_runs_the_real_declarative_tool_with_its_mock_fixture() -> None:
    conversation = ScriptedLLM(["Hi.", ToolCall("go_to_lookup"), "Is Ada right?"])
    resolved = _config(_lookup_flow(), tool_mocks={"policy_lookup": {"holder": "Ada"}})
    api, ctx, starter = await _start(resolved, conversation, _mocked_builder)
    session = starter.session
    assert session is not None
    await session.run(user_input="It is P-1.")
    await _wait_for(lambda: session.current_agent.id == "found")
    state = session.userdata
    assert isinstance(state, FlowUserdata)
    assert state.flow.variables["holder"] == "Ada"
    await ctx.fire_shutdown("done")


async def test_tool_step_never_confirms_a_read_back_so_such_a_tool_refuses() -> None:
    row = LOOKUP_ROW.model_copy(update={"confirm_readback": ["policy"]})
    conversation = ScriptedLLM(["Hi.", ToolCall("go_to_lookup"), "Sorry."])
    flow = _lookup_flow(arguments={"policy": "{{ var.policy_no }}", "confirmed": True})
    base = _config(flow, tool_mocks={"policy_lookup": {"holder": "Ada"}})
    resolved = base.model_copy(update={"tools": [row]})
    api, ctx, starter = await _start(resolved, conversation, _mocked_builder)
    session = starter.session
    assert session is not None
    await session.run(user_input="It is P-1.")
    await _wait_for(lambda: session.current_agent.id == "oops")
    await ctx.fire_shutdown("done")
    (ended,) = [e.payload for e in api.events_of("tool_call_ended") if e.payload.get("flow_node")]
    assert (ended["outcome"], ended["reason"]) == ("error", "tool_error")
    assert "read these back" in ended["result_preview"]


async def test_tool_step_hands_its_outcome_to_the_rules_engine() -> None:
    from lkap_agent.extraction.session import LIVE_STRUCTURE_USERDATA_KEY  # noqa: PLC0415

    seen: list[dict[str, bool]] = []
    calls: list[dict[str, Any]] = []
    conversation = ScriptedLLM(["Hi.", ToolCall("go_to_lookup"), "Next."])
    api, ctx, starter = await _start(
        _config(_lookup_flow()), conversation, lambda _defs, **_: [_lookup_tool(calls, '{"holder": "Ada"}')]
    )
    agent = starter.agent
    assert isinstance(agent, FlowNodeAgent)
    agent.runtime.services.ctx.userdata[LIVE_STRUCTURE_USERDATA_KEY] = SimpleNamespace(
        on_tool_outcomes=lambda outcomes: seen.append(dict(outcomes))
    )
    session = starter.session
    assert session is not None
    await session.run(user_input="It is P-1.")
    await _wait_for(lambda: session.current_agent.id == "found")
    assert {"policy_lookup": True} in seen
    await ctx.fire_shutdown("done")
