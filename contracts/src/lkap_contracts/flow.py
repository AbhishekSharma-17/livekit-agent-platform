"""Flow graphs: the node/edge spec a flow-mode agent runs (CONTRACTS-V2 §4.5).

A flow is stored inside ``AgentConfig.flow`` and executed by the worker
(V2-15): each agent node is one LiveKit ``Agent`` whose outgoing edges become
handoff tools named ``go_to_{target_id}``. Structural rules that can be checked
without the database are enforced here; reference resolution (tool ids, kb ids)
happens at api validation time (``lkap_api.flows.validation``).
"""

import re
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

from lkap_contracts.common import ProviderRef

NodeKind = Literal["start", "agent", "end", "global", "transfer", "qa"]

#: Node ids become part of a tool name (``go_to_{id}``), so they are short and strict.
NODE_ID_PATTERN = r"^[a-z][a-z0-9_]{0,31}$"

#: Variable names are referenced from instructions as ``{{ name }}``.
VARIABLE_NAME_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"

_NODE_ID_RE = re.compile(NODE_ID_PATTERN)


def edge_tool_name(target_id: str) -> str:
    """Return the handoff tool name the worker exposes for an edge.

    Args:
        target_id: The edge's target node id.

    Returns:
        The tool name, for example ``"go_to_collect_details"``.
    """
    return f"go_to_{target_id}"


class VariableSpec(BaseModel):
    """One variable a flow extracts from the conversation."""

    name: str = Field(pattern=VARIABLE_NAME_PATTERN)
    type: Literal["string", "number", "boolean", "enum", "date", "phone", "email"] = "string"
    description: str = ""
    options: list[str] | None = None
    required: bool = False


class NodeBase(BaseModel):
    """Fields every node kind carries."""

    id: str = Field(pattern=NODE_ID_PATTERN)
    kind: NodeKind
    label: str = ""
    position: tuple[float, float] = (0.0, 0.0)


class StartNode(NodeBase):
    """Entry point: greets the caller and hands off along its first matching edge."""

    kind: Literal["start"] = "start"
    greeting: str | None = None
    greeting_mode: Literal["say", "generate"] = "say"


class GlobalNode(NodeBase):
    """Instructions, tools and knowledge merged into every agent node."""

    kind: Literal["global"] = "global"
    instructions: str = ""
    #: Tool names (R-V2-10): builtin, block, pack, or the ``name`` of a tool in
    #: ``config.tools.tool_ids``.
    tools: list[str] = []
    kb_ids: list[str] = []


class AgentNode(NodeBase):
    """One conversational step with its own instructions, tools and extractions."""

    kind: Literal["agent"] = "agent"
    instructions: str = ""
    #: Tool names (R-V2-10): builtin, block, pack, or the ``name`` of a tool in
    #: ``config.tools.tool_ids``.
    tools: list[str] = []
    kb_ids: list[str] = []
    extract: list[str] = []
    allow_interruptions: bool | None = None
    providers: dict[Literal["llm", "tts"], ProviderRef] = {}
    max_turns: int | None = None


class EndNode(NodeBase):
    """Terminal node: says farewell, records a disposition and ends the session."""

    kind: Literal["end"] = "end"
    farewell: str | None = None
    disposition: str | None = None
    webhook_event: bool = True


class TransferNode(NodeBase):
    """Hands the call to a human or another number."""

    kind: Literal["transfer"] = "transfer"
    to: str
    mode: Literal["cold", "warm"] = "cold"
    announce: str | None = None


class QaNode(NodeBase):
    """Post-call scoring step; never part of the conversational graph."""

    kind: Literal["qa"] = "qa"
    rubric_prompt: str | None = None


FlowNode = Annotated[
    StartNode | AgentNode | EndNode | GlobalNode | TransferNode | QaNode,
    Field(discriminator="kind"),
]


class FlowEdge(BaseModel):
    """A conditional transition; ``condition`` becomes the handoff tool description."""

    id: str
    source: str
    target: str
    condition: str = ""
    label: str | None = None
    transition_speech: str | None = None
    priority: int = 0


class FlowState(BaseModel):
    """Runtime position in a flow, stored on ``session.userdata.flow``."""

    current_node: str
    path: list[str] = []
    variables: dict[str, str | int | float | bool | None] = {}
    disposition: str | None = None


#: Node kinds that may never be an edge endpoint.
_UNCONNECTABLE_KINDS = ("global", "qa")


class FlowSpec(BaseModel):
    """The whole graph, validated structurally on construction."""

    v: Literal[1] = 1
    nodes: list[FlowNode] = []
    edges: list[FlowEdge] = []
    variables: list[VariableSpec] = []

    @model_validator(mode="after")
    def _check_structure(self) -> "FlowSpec":
        """Enforce the structural rules of CONTRACTS-V2 §4.5.

        Returns:
            The validated spec.

        Raises:
            ValueError: If the graph breaks any structural rule.
        """
        if not self.nodes and not self.edges:
            return self

        by_id: dict[str, FlowNode] = {}
        for node in self.nodes:
            if node.id in by_id:
                raise ValueError(f"duplicate node id: {node.id!r}")
            by_id[node.id] = node

        starts = [n for n in self.nodes if n.kind == "start"]
        if len(starts) != 1:
            raise ValueError(f"a flow needs exactly one start node, found {len(starts)}")
        if len([n for n in self.nodes if n.kind == "global"]) > 1:
            raise ValueError("a flow may declare at most one global node")

        for edge in self.edges:
            for role, node_id in (("source", edge.source), ("target", edge.target)):
                endpoint = by_id.get(node_id)
                if endpoint is None:
                    raise ValueError(f"edge {edge.id!r} has an unknown {role}: {node_id!r}")
                if endpoint.kind in _UNCONNECTABLE_KINDS:
                    raise ValueError(f"edge {edge.id!r} may not attach to a {endpoint.kind} node")
            if by_id[edge.source].kind == "end":
                raise ValueError(f"end node {edge.source!r} may not have outgoing edges")

        reachable = _reachable_from(starts[0].id, self.edges)
        unreachable = [
            n.id
            for n in self.nodes
            if n.kind not in (*_UNCONNECTABLE_KINDS, "start") and n.id not in reachable
        ]
        if unreachable:
            raise ValueError(f"nodes unreachable from start: {', '.join(sorted(unreachable))}")
        return self


def _reachable_from(start_id: str, edges: list[FlowEdge]) -> set[str]:
    """Return every node id reachable from ``start_id`` by following edges.

    Args:
        start_id: Id of the start node.
        edges: Every edge of the flow.

    Returns:
        The set of reachable node ids, including ``start_id``.
    """
    outgoing: dict[str, list[str]] = {}
    for edge in edges:
        outgoing.setdefault(edge.source, []).append(edge.target)
    seen = {start_id}
    queue = [start_id]
    while queue:
        for target in outgoing.get(queue.pop(), []):
            if target not in seen:
                seen.add(target)
                queue.append(target)
    return seen


def is_valid_node_id(node_id: str) -> bool:
    """Return True when ``node_id`` matches :data:`NODE_ID_PATTERN`."""
    return _NODE_ID_RE.match(node_id) is not None
