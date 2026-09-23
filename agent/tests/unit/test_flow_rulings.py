"""V2-16's worker-side rulings: R-V2-8 (summary), R-V2-11 (QA node), R-V2-13 (prompt order), R-V2-14."""

from __future__ import annotations

import json
from typing import Any

from fakes.fake_api import FakeApi, resolved_config
from fakes.fake_llm import FakeLLM
from test_flow_runtime import (
    INTAKE_FLOW,
    ScriptedLLM,
    ToolCall,
    _flow_config,
    _FlowFactory,
    _start,
    _wait_for,
)
from test_main import FakeJobContext, _deps, _metadata

from lkap_agent.flow import FlowNodeAgent, prepare_flow_resolved
from lkap_agent.main import _assemble
from lkap_agent.platform_agent import PlatformAgent
from lkap_agent.session_builder import prepare_resolved


async def test_a_flow_reaching_an_end_node_posts_its_disposition_and_variables_in_the_summary() -> None:
    conversation = ScriptedLLM(
        ["Hi, this is intake.", ToolCall("go_to_confirm"), "Is Ada Lovelace right?", ToolCall("go_to_done")]
    )
    workflow = FakeLLM([json.dumps({"name": "Ada Lovelace"})])
    api, ctx, starter = await _start(_flow_config(INTAKE_FLOW), conversation, workflow)
    session = starter.session
    assert session is not None
    await session.run(user_input="My name is Ada Lovelace.")
    await _wait_for(lambda: session.current_agent.id == "confirm")
    await session.run(user_input="Yes, that's right.")
    await _wait_for(lambda: bool(ctx.shutdown_reasons))

    await ctx.fire_shutdown("flow reached end node done")

    (summary,) = api.summaries
    assert summary.disposition == "completed"
    assert summary.variables == {"name": "Ada Lovelace"}


async def test_a_prompt_session_posts_the_summary_defaults() -> None:
    api = FakeApi(resolved_config(channel="text"))
    ctx = FakeJobContext(_metadata())
    await _start_prompt(api, ctx)

    await ctx.fire_shutdown("done")

    (summary,) = api.summaries
    assert (summary.disposition, summary.variables) == (None, {})


async def _start_prompt(api: FakeApi, ctx: FakeJobContext) -> None:
    from lkap_agent.main import run_session  # noqa: PLC0415

    await run_session(ctx, _deps(api, factory=_FlowFactory(FakeLLM(["Hi"]), FakeLLM(["{}"]))))


async def test_a_qa_node_scores_the_call_with_its_rubric_even_with_qa_disabled() -> None:
    """R-V2-11: QA runs; without an api-resolved judge the verdict is R-V2-6's failure."""
    flow = json.loads(json.dumps(INTAKE_FLOW))
    flow["nodes"].append({"id": "qa", "kind": "qa", "rubric_prompt": "Score empathy."})
    conversation = ScriptedLLM(["Hi, this is intake.", "Noted."])
    api, ctx, starter = await _start(_flow_config(flow), conversation, FakeLLM(["{}"]))
    assert starter.session is not None
    await starter.session.run(user_input="Hello.")

    await ctx.fire_shutdown("participant left")

    (verdict,) = api.qa
    assert verdict.status == "failed"
    assert verdict.error == "qa_llm not resolved"


async def test_the_prompt_is_base_instructions_then_global_then_node() -> None:
    conversation = ScriptedLLM(["Hi, this is intake."])
    resolved = _flow_config(INTAKE_FLOW, instructions="BASE: you are Ava from Acme.")
    await _start(resolved, conversation, FakeLLM(["{}"]))
    await _wait_for(lambda: bool(conversation.calls))

    prompt = conversation.calls[0][0]
    base = prompt.index("BASE: you are Ava from Acme.")
    global_ = prompt.index("You work for Acme Insurance.")
    node = prompt.index("Ask for the caller's full name.")
    flow_note = prompt.index("Conversation flow:")
    assert base < global_ < node < flow_note


def _assembled(resolved: Any) -> Any:
    api = FakeApi(resolved)
    ctx = FakeJobContext(_metadata())
    deps = _deps(api, factory=_FlowFactory(FakeLLM(["Hi"]), FakeLLM(["{}"])))
    _plan, agent = _assemble(ctx, deps, prepare_flow_resolved(prepare_resolved(resolved)))
    return agent


async def test_assemble_returns_a_flow_node_agent_for_a_flow_config() -> None:
    agent = _assembled(_flow_config(INTAKE_FLOW))
    assert isinstance(agent, FlowNodeAgent)
    assert agent.id == "collect"


async def test_assemble_returns_a_platform_agent_for_a_prompt_config() -> None:
    agent = _assembled(resolved_config(channel="text"))
    assert isinstance(agent, PlatformAgent)
    assert not isinstance(agent, FlowNodeAgent)
