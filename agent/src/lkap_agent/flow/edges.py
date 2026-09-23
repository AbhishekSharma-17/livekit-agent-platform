"""Edges as handoff tools (CONTRACTS-V2 §4.5, ARCHITECTURE-V2 D-V2-13).

Every outgoing edge of a node becomes a zero-argument function tool named
`go_to_{target_id}` (`lkap_contracts.flow.edge_tool_name`) whose description
is the edge's natural-language condition — the LLM decides when the condition
holds, as in Dograh. Two edges from one node to the same target would collide
on the tool name, so they become one tool whose description joins the
conditions; the highest-priority edge is the one recorded in the `handoff`
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

__all__ = ["build_edge_tools", "edge_description", "group_edges_by_target"]


def group_edges_by_target(edges: list[FlowEdge]) -> dict[str, list[FlowEdge]]:
    """Group already priority-sorted edges by target, keeping first-seen order."""
    groups: dict[str, list[FlowEdge]] = {}
    for edge in edges:
        groups.setdefault(edge.target, []).append(edge)
    return groups


def edge_description(edges: list[FlowEdge], target_label: str) -> str:
    """The tool description: the condition verbatim, or the joined conditions of merged edges."""
    conditions = [e.condition.strip() for e in edges if e.condition.strip()]
    if not conditions:
        return f"Move the conversation to the next step: {target_label}."
    if len(conditions) == 1:
        return conditions[0]
    return "Call this when any of these is true: " + " OR ".join(f"({c})" for c in conditions)


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
