"""The flow ``tool`` node contract (V6-17, D-V6-28)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from lkap_contracts.flow import (
    TOOL_NODE_OUTCOMES,
    FlowSpec,
    ToolNode,
    ToolNodeOutcomes,
    argument_template_issues,
    tool_only_cycles,
)


def _lookup_flow(**tool: Any) -> dict[str, Any]:
    node = {
        "id": "lookup",
        "kind": "tool",
        "tool": "policy_lookup",
        "arguments": {"policy": "{{ var.policy_no }}"},
        "bindings": [{"path": "/holder", "to": "var:holder"}],
        "on": {"ok": "found_it", "empty": "not_found", "error": "failed"},
        **tool,
    }
    return {
        "nodes": [
            {"id": "start", "kind": "start"},
            {"id": "ask", "kind": "agent", "instructions": "Ask for the policy number."},
            node,
            {"id": "found", "kind": "agent", "instructions": "Confirm the holder."},
            {"id": "done", "kind": "end"},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "ask"},
            {"id": "e2", "source": "ask", "target": "lookup", "condition": "the caller gave the number"},
            {"id": "found_it", "source": "lookup", "target": "found"},
            {"id": "not_found", "source": "lookup", "target": "ask"},
            {"id": "failed", "source": "lookup", "target": "done"},
            {"id": "e3", "source": "found", "target": "done"},
        ],
        "variables": [{"name": "policy_no"}, {"name": "holder"}],
    }


def test_flowspec_with_a_tool_node_parses_into_a_tool_node() -> None:
    spec = FlowSpec.model_validate(_lookup_flow())
    node = next(n for n in spec.nodes if isinstance(n, ToolNode))
    assert node.tool == "policy_lookup"
    assert node.timeout_s == 10.0
    assert node.on.named() == {"ok": "found_it", "error": "failed", "empty": "not_found"}
    assert node.bindings[0].target().key == "holder"


@pytest.mark.parametrize(
    ("outcomes", "outcome", "edge"),
    [
        (ToolNodeOutcomes(ok="a"), "ok", "a"),
        (ToolNodeOutcomes(ok="a"), "empty", "a"),
        (ToolNodeOutcomes(ok="a", empty="b"), "empty", "b"),
        (ToolNodeOutcomes(ok="a"), "error", None),
        (ToolNodeOutcomes(ok="a", error="c"), "error", "c"),
    ],
)
def test_tool_node_outcomes_edge_for_empty_falls_back_to_ok_but_error_does_not(
    outcomes: ToolNodeOutcomes, outcome: str, edge: str | None
) -> None:
    assert outcome in TOOL_NODE_OUTCOMES
    assert outcomes.edge_for(outcome) == edge  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("template", "fragment"),
    [
        ("{{ policy_no }}", "write {{ var.policy_no }}"),
        ("{{ ctx.home_address }}", "not a session value"),
        ("{{ secret.KEY }}", "not a value this step can fill"),
        ("{{ var.Policy }}", "not a variable name"),
    ],
)
def test_argument_template_issues_refuses_what_a_step_cannot_fill(template: str, fragment: str) -> None:
    issues = argument_template_issues(f"number {template}")
    assert issues and fragment in issues[0]


@pytest.mark.parametrize("template", ["{{ var.policy_no }}", "{{ctx.timezone}}", "plain text", ""])
def test_argument_template_issues_accepts_ctx_var_and_plain_text(template: str) -> None:
    assert argument_template_issues(template) == []


def test_tool_node_refuses_a_bare_placeholder_argument_at_parse() -> None:
    with pytest.raises(ValidationError, match="write"):
        FlowSpec.model_validate(_lookup_flow(arguments={"policy": "{{ policy_no }}"}))


@pytest.mark.parametrize(("timeout", "ok"), [(30, True), (0.5, True), (31, False), (0, False)])
def test_tool_node_timeout_is_bounded_by_thirty_seconds(timeout: float, ok: bool) -> None:
    if ok:
        assert ToolNode(id="t", tool="x", timeout_s=timeout).timeout_s == timeout
    else:
        with pytest.raises(ValidationError):
            ToolNode(id="t", tool="x", timeout_s=timeout)


def test_tool_node_refuses_more_than_twenty_bindings() -> None:
    bindings = [{"path": "", "to": f"var:v{i}"} for i in range(21)]
    with pytest.raises(ValidationError, match="at most 20"):
        ToolNode.model_validate({"id": "t", "tool": "x", "bindings": bindings})


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"on": {"empty": "not_found", "error": "failed"}}, "needs an 'ok' edge"),
        ({"on": {"ok": "nowhere"}}, "unknown edge"),
        ({"on": {"ok": "e3"}}, "does not leave it"),
        ({"on": {"ok": "found_it", "error": "failed"}}, "no outcome"),
    ],
)
def test_flowspec_refuses_broken_tool_node_outcomes(change: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        FlowSpec.model_validate(_lookup_flow(**change))


def _chain(edges: list[tuple[str, str, str]]) -> dict[str, Any]:
    return {
        "nodes": [
            {"id": "start", "kind": "start"},
            {"id": "a", "kind": "tool", "tool": "t", "on": {"ok": "ab"}},
            {"id": "b", "kind": "tool", "tool": "t", "on": {"ok": "bx"}},
            {"id": "talk", "kind": "agent"},
        ],
        "edges": [{"id": "s", "source": "start", "target": "a"}]
        + [{"id": i, "source": s, "target": t} for i, s, t in edges],
    }


def test_flowspec_refuses_a_loop_of_tool_nodes_only() -> None:
    with pytest.raises(ValidationError, match="form a loop with no agent step"):
        FlowSpec.model_validate(_chain([("ab", "a", "b"), ("bx", "b", "a")]))


def test_flowspec_accepts_a_loop_through_an_agent_node() -> None:
    raw = _chain([("ab", "a", "b"), ("bx", "b", "talk"), ("back", "talk", "a")])
    spec = FlowSpec.model_validate(raw)
    assert tool_only_cycles(spec.nodes, spec.edges) == []


def test_tool_only_cycles_finds_a_self_loop() -> None:
    raw = {
        "nodes": [
            {"id": "start", "kind": "start"},
            {"id": "a", "kind": "tool", "tool": "t", "on": {"ok": "aa"}},
        ],
        "edges": [{"id": "s", "source": "start", "target": "a"}, {"id": "aa", "source": "a", "target": "a"}],
    }
    with pytest.raises(ValidationError, match="form a loop"):
        FlowSpec.model_validate(raw)


def test_flowspec_without_tool_nodes_validates_as_before() -> None:
    raw = {
        "nodes": [
            {"id": "start", "kind": "start"},
            {"id": "ask", "kind": "agent"},
            {"id": "done", "kind": "end"},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "ask"},
            {"id": "e2", "source": "ask", "target": "done"},
        ],
    }
    spec = FlowSpec.model_validate(raw)
    assert [node.kind for node in spec.nodes] == ["start", "agent", "end"]
    assert FlowSpec.model_validate(spec.model_dump(mode="json")) == spec
    assert tool_only_cycles(spec.nodes, spec.edges) == []


def test_flow_and_tool_context_import_in_either_order() -> None:
    src = Path(__file__).resolve().parents[1] / "src"
    env = {**os.environ, "PYTHONPATH": str(src)}
    for first, second in (
        ("lkap_contracts.flow", "lkap_contracts.tool_context"),
        ("lkap_contracts.tool_context", "lkap_contracts.flow"),
    ):
        code = f"import {first}, {second}; print({second}.__name__)"
        done = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=False, env=env
        )
        assert done.returncode == 0, done.stderr
