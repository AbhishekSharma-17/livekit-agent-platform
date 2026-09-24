"""Edges as handoff tools (CONTRACTS-V2 §4.5, ARCHITECTURE-V2 D-V2-13).

Every outgoing edge of a node becomes a zero-argument function tool named
`go_to_{target_id}` (`lkap_contracts.flow.edge_tool_name`) whose description
is an instruction built from the edge's natural-language condition (R-V4-30):
"Call this to move to the step '<label>' when: <condition>." — the LLM decides
when the condition holds, as in Dograh, and a small model reads an imperative
where it would skip a bare clause. Two edges from one node to the same target
would collide on the tool name, so they become one tool whose description joins
the conditions; the highest-priority edge is the one recorded in the `handoff`
event.

The tool body delegates to :meth:`FlowRuntime.take_edge`, which returns the
next node's `FlowNodeAgent` (livekit-agents 1.8.2 hands off when a tool
returns an `Agent`: `voice.generation.make_tool_output` sets `agent_task`, and
a bare `Agent` return sets `reply_required=False`, so the old node does not
speak again), `None` for a terminal node (the tool already spoke), or a short
string the model reads when the transition cannot happen.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from livekit.agents import FunctionTool, RunContext, function_tool
from lkap_contracts.flow import FlowEdge, edge_tool_name

if TYPE_CHECKING:
    from lkap_agent.flow.runtime import FlowRuntime

__all__ = ["build_edge_tools", "condition_clause", "edge_description", "group_edges_by_target"]


def group_edges_by_target(edges: list[FlowEdge]) -> dict[str, list[FlowEdge]]:
    """Group already priority-sorted edges by target, keeping first-seen order."""
    groups: dict[str, list[FlowEdge]] = {}
    for edge in edges:
        groups.setdefault(edge.target, []).append(edge)
    return groups


def edge_description(edges: list[FlowEdge], target_label: str) -> str:
    """The tool description (R-V4-30): an instruction naming the step and its condition(s).

    One condition: ``Call this to move to the step '<label>' when: <condition>.``;
    merged edges: ``… when any of these is true: (<a>) OR (<b>).``; no condition:
    ``Move the conversation to the next step: <label>.``
    """
    conditions = [c for c in (condition_clause(e.condition) for e in edges) if c]
    if not conditions:
        return f"Move the conversation to the next step: {target_label}."
    head = f"Call this to move to the step '{target_label}' when"
    if len(conditions) == 1:
        return f"{head}: {conditions[0]}."
    return f"{head} any of these is true: " + " OR ".join(f"({c})" for c in conditions) + "."


def condition_clause(condition: str) -> str:
    """A condition as a clause inside a sentence: trimmed, without a trailing full stop."""
    return condition.strip().rstrip(".").strip()


def build_edge_tools(
    runtime: FlowRuntime, source_id: str, edges: list[FlowEdge]
) -> list[FunctionTool[..., Any]]:
    """One `go_to_<target>` tool per distinct target of `source_id`'s outgoing `edges`."""
    tools: list[FunctionTool[..., Any]] = []
    for target_id, group in group_edges_by_target(edges).items():
        tools.append(_edge_tool(runtime, source_id, target_id, group))
    return tools


def _edge_tool(
    runtime: FlowRuntime, source_id: str, target_id: str, group: list[FlowEdge]
) -> FunctionTool[..., Any]:
    primary = group[0]

    async def _go(context: RunContext[Any]) -> Any:
        return await runtime.take_edge(source_id, primary, context)

    return function_tool(
        _go,
        name=edge_tool_name(target_id),
        description=edge_description(group, runtime.node_label(target_id)),
    )
