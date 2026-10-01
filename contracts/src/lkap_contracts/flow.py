"""Flow graphs: the node/edge spec a flow-mode agent runs (CONTRACTS-V2 §4.5).

A flow is stored inside ``AgentConfig.flow`` and executed by the worker
(V2-15): each agent node is one LiveKit ``Agent`` whose outgoing edges become
handoff tools named ``go_to_{target_id}``. Structural rules that can be checked
without the database are enforced here; reference resolution (tool ids, kb ids)
happens at api validation time (``lkap_api.flows.validation``).
"""

import re
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lkap_contracts.common import NODE_ID_PATTERN, VARIABLE_NAME_PATTERN, ProviderRef
from lkap_contracts.tool_context import (
    CTX_PLACEHOLDERS,
    MAX_PINNED_ARGUMENTS,
    PLACEHOLDER_PATTERN,
    Bindings,
    PinnedValue,
)

__all__ = [
    "MAX_TOOL_NODE_TIMEOUT_S",
    "NODE_ID_PATTERN",
    "TOOL_NODE_OUTCOMES",
    "VARIABLE_NAME_PATTERN",
    "AgentNode",
    "EndNode",
    "FlowEdge",
    "FlowNode",
    "FlowSpec",
    "FlowState",
    "GlobalNode",
    "NodeBase",
    "NodeKind",
    "QaNode",
    "StartNode",
    "ToolNode",
    "ToolNodeOutcome",
    "ToolNodeOutcomes",
    "TransferNode",
    "VariableSpec",
    "argument_template_issues",
    "edge_tool_name",
    "is_valid_node_id",
    "tool_only_cycles",
]

NodeKind = Literal["start", "agent", "end", "global", "transfer", "qa", "tool"]

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
    #: Knowledge bases added to every node's scope (R-V2-13). When no ``global``
    #: or ``agent`` node lists any, every node searches all of the agent's
    #: ``knowledge.kb_ids`` (R-V4-29).
    kb_ids: list[str] = []


class AgentNode(NodeBase):
    """One conversational step with its own instructions, tools and extractions."""

    kind: Literal["agent"] = "agent"
    instructions: str = ""
    #: Tool names (R-V2-10): builtin, block, pack, or the ``name`` of a tool in
    #: ``config.tools.tool_ids``.
    tools: list[str] = []
    #: This step's knowledge bases, searched with the global node's. When no
    #: ``global`` or ``agent`` node lists any, every node searches all of the
    #: agent's ``knowledge.kb_ids`` (R-V4-29).
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


#: How a ``tool`` node's call turned out (D-V6-28): ``ok`` — it succeeded; ``empty`` — it
#: succeeded with an empty result, or the node has bindings and none of them found a value;
#: ``error`` — it failed, timed out, refused (a missing value) or could not run.
ToolNodeOutcome = Literal["ok", "error", "empty"]
TOOL_NODE_OUTCOMES: Final[tuple[ToolNodeOutcome, ...]] = ("ok", "error", "empty")

#: Longest a ``tool`` node waits for its tool (D-V6-28).
MAX_TOOL_NODE_TIMEOUT_S: Final[float] = 30.0

#: Any ``{{ … }}`` in an argument template (each must be a ``ctx``/``var`` placeholder).
_ANY_PLACEHOLDER_RE: Final = re.compile(r"{{(.*?)}}")
_CONTEXT_PLACEHOLDER_RE: Final = re.compile(PLACEHOLDER_PATTERN)
_VARIABLE_NAME_RE: Final = re.compile(VARIABLE_NAME_PATTERN)


def argument_template_issues(text: str) -> list[str]:
    """Why a ``tool`` node argument template is not valid (empty when it is).

    A template may name ``{{ ctx.<name> }}`` (a session value, :data:`CTX_PLACEHOLDERS`) and
    ``{{ var.<name> }}`` (a flow or captured variable), nothing else: a bare ``{{ name }}``
    reads as a tool argument, which a node has none of.

    Args:
        text: One string argument value.

    Returns:
        Plain sentences, one per problem.
    """
    issues: list[str] = []
    for match in _ANY_PLACEHOLDER_RE.finditer(text):
        whole = match.group(0)
        parsed = _CONTEXT_PLACEHOLDER_RE.fullmatch(whole)
        if parsed is None or parsed.group(1) is None:
            inner = match.group(1).strip()
            hint = f" (write {{{{ var.{inner} }}}})" if _VARIABLE_NAME_RE.match(inner) else ""
            issues.append(f"'{whole}' is not a value this step can fill: use {{{{ var.<name> }}}}{hint}")
            continue
        namespace, name = parsed.group(1), parsed.group(2)
        if namespace == "ctx" and name not in CTX_PLACEHOLDERS:
            issues.append(f"'{whole}' is not a session value. Use one of: {', '.join(CTX_PLACEHOLDERS)}")
        elif namespace == "var" and not _VARIABLE_NAME_RE.match(name):
            issues.append(f"'{whole}' is not a variable name (lowercase letters, digits and _)")
    return issues


class ToolNodeOutcomes(BaseModel):
    """Which outgoing edge a ``tool`` node takes for each outcome (edge ids)."""

    model_config = ConfigDict(extra="forbid")

    ok: str | None = None
    """The edge taken when the tool succeeded (required: a tool step always goes on)."""
    error: str | None = None
    """The edge taken when the tool failed. Without one, the call ends with a short apology."""
    empty: str | None = None
    """The edge taken when the tool found nothing. Without one, ``ok``'s edge is taken."""

    def edge_for(self, outcome: ToolNodeOutcome) -> str | None:
        """The edge id for ``outcome`` (``empty`` falls back to ``ok``; ``error`` has no fallback)."""
        match outcome:
            case "ok":
                return self.ok
            case "empty":
                return self.empty or self.ok
            case "error":
                return self.error

    def named(self) -> dict[ToolNodeOutcome, str]:
        """``{outcome: edge id}`` for every outcome that names an edge."""
        pairs: tuple[tuple[ToolNodeOutcome, str | None], ...] = (
            ("ok", self.ok),
            ("error", self.error),
            ("empty", self.empty),
        )
        return {outcome: edge_id for outcome, edge_id in pairs if edge_id}


class ToolNode(NodeBase):
    """Call one of the agent's attached tools with no model turn, then branch on the outcome (D-V6-28).

    The worker runs the tool the moment the step is entered (through the same execution path
    as a call the model makes: blocking, the same guardrails and fences), applies
    :attr:`bindings` to the result, and takes the edge :attr:`on` names for the outcome. The
    result text itself never reaches the model: what the next steps need goes into variables
    (``var:<name>`` bindings), which a later step's instructions show fenced, or onto the panel.
    """

    kind: Literal["tool"] = "tool"
    tool: str = Field(min_length=1, max_length=128)
    """The ``name`` of a tool attached to the agent (``config.tools.tool_ids``)."""
    mcp_tool: str | None = Field(default=None, min_length=1, max_length=128)
    """When :attr:`tool` names a server of tools: which of its tools to call."""
    arguments: dict[str, PinnedValue] = Field(default={}, max_length=MAX_PINNED_ARGUMENTS)
    """The call's arguments: literals, or text with ``{{ var.<name> }}`` / ``{{ ctx.<name> }}``
    filled in at call time. A text that is exactly one ``{{ var.<name> }}`` passes the
    variable's value as it is (a number stays a number)."""
    bindings: Bindings = []
    """Where parts of the result go: ``var:<name>`` (a flow variable), a ``details``/``table``
    block, the checklist, the status or a note — as a tool's own bindings."""
    timeout_s: float = Field(default=10.0, gt=0, le=MAX_TOOL_NODE_TIMEOUT_S)
    """How long the step waits for the tool before it counts as ``error``."""
    on: ToolNodeOutcomes = ToolNodeOutcomes()
    """The outgoing edge for each outcome."""

    @field_validator("arguments")
    @classmethod
    def _templates(cls, value: dict[str, PinnedValue]) -> dict[str, PinnedValue]:
        for name, argument in value.items():
            if not name or len(name) > 64:
                raise ValueError("an argument name is 1 to 64 characters")
            if isinstance(argument, str):
                problems = argument_template_issues(argument)
                if problems:
                    raise ValueError(f"argument '{name}': {problems[0]}")
        return value


FlowNode = Annotated[
    StartNode | AgentNode | EndNode | GlobalNode | TransferNode | QaNode | ToolNode,
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

        _check_tool_nodes(self.nodes, self.edges)

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


def _check_tool_nodes(nodes: list[FlowNode], edges: list[FlowEdge]) -> None:
    """The ``tool`` node rules (D-V6-28), raising the first one broken.

    * the node names an ``ok`` edge (a tool step always goes on);
    * every edge an outcome names exists and leaves this node;
    * every edge leaving the node is named by an outcome (nothing else can take it);
    * no cycle runs through tool nodes only (it would loop with no one speaking).

    Raises:
        ValueError: A broken rule, in plain words.
    """
    edges_by_id = {edge.id: edge for edge in edges}
    for node in nodes:
        if not isinstance(node, ToolNode):
            continue
        named = node.on.named()
        if "ok" not in named:
            raise ValueError(f"tool node {node.id!r} needs an 'ok' edge (on.ok)")
        for outcome, edge_id in named.items():
            edge = edges_by_id.get(edge_id)
            if edge is None:
                raise ValueError(f"tool node {node.id!r} names an unknown edge for {outcome!r}: {edge_id!r}")
            if edge.source != node.id:
                raise ValueError(
                    f"tool node {node.id!r} names edge {edge_id!r} for {outcome!r}, which does not leave it"
                )
        used = set(named.values())
        for edge in edges:
            if edge.source == node.id and edge.id not in used:
                raise ValueError(
                    f"edge {edge.id!r} leaves tool node {node.id!r} but no outcome (on.ok, on.error, "
                    "on.empty) names it"
                )
    cycles = tool_only_cycles(nodes, edges)
    if cycles:
        raise ValueError(
            f"tool nodes {', '.join(repr(n) for n in cycles[0])} form a loop with no agent step. "
            "Route one of them through an agent node"
        )


def tool_only_cycles(nodes: list[FlowNode], edges: list[FlowEdge]) -> list[list[str]]:
    """Every loop made of ``tool`` nodes only (each as its node ids, sorted), for validators.

    Args:
        nodes: The flow's nodes.
        edges: The flow's edges.

    Returns:
        One sorted id list per strongly connected group of tool nodes that loops (a group of
        two or more, or one node with an edge to itself); empty when there is none.
    """
    tool_ids = {node.id for node in nodes if isinstance(node, ToolNode)}
    graph: dict[str, list[str]] = {node_id: [] for node_id in tool_ids}
    for edge in edges:
        if edge.source in tool_ids and edge.target in tool_ids:
            graph[edge.source].append(edge.target)
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    found: list[list[str]] = []
    counter = 0

    def _visit(root: str) -> None:
        # Tarjan's algorithm, iterative (a flow is small, but recursion limits are not ours to spend).
        nonlocal counter
        work: list[tuple[str, int]] = [(root, 0)]
        while work:
            node_id, child = work.pop()
            if child == 0:
                index[node_id] = low[node_id] = counter
                counter += 1
                stack.append(node_id)
                on_stack.add(node_id)
            targets = graph[node_id]
            if child < len(targets):
                work.append((node_id, child + 1))
                target = targets[child]
                if target not in index:
                    work.append((target, 0))
                elif target in on_stack:
                    low[node_id] = min(low[node_id], index[target])
                continue
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node_id])
            if low[node_id] == index[node_id]:
                group: list[str] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    group.append(member)
                    if member == node_id:
                        break
                if len(group) > 1 or node_id in graph[node_id]:
                    found.append(sorted(group))

    for node_id in sorted(tool_ids):
        if node_id not in index:
            _visit(node_id)
    return sorted(found)


def is_valid_node_id(node_id: str) -> bool:
    """Return True when ``node_id`` matches :data:`NODE_ID_PATTERN`."""
    return _NODE_ID_RE.match(node_id) is not None
