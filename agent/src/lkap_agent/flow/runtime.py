"""The flow runtime: one `FlowRuntime` per flow session (PLAN-V2 V2-15, ARCHITECTURE-V2 §2.5).

Mapping of a `FlowSpec` onto livekit-agents 1.8.3 (verified against the
installed SDK):

* **Nodes.** Every `agent` node — and the `start` node when it has to route
  (anything but exactly one edge to an agent node) — runs as one
  :class:`~lkap_agent.flow.node_agent.FlowNodeAgent` (a `PlatformAgent`), with
  `Agent.id = node.id` so `AgentHandoff` chat items and traces name the node.
* **Edges** are `go_to_<target>` tools (:mod:`lkap_agent.flow.edges`). Taking
  an edge to an agent node returns the next node's agent from the tool, which
  the SDK turns into `AgentSession.update_agent`. The conversation is carried
  with `chat_ctx=<current agent's chat_ctx>`; `Agent.__init__` copies it with
  `ChatContext.copy(tools=...)`, which also drops old `go_to_*` call pairs the
  new node has no tool for.
* **Sibling tools of an edge** (R-V4-64, V4-19): when a batch of parallel
  tool calls hands off, the draining node generates no tool reply (every
  output of the batch is marked `reply_required=False` in the synchronous
  `function_tools_executed` handler), and the batch's call/output pairs are
  carried into the next node, whose `on_enter` answers from them
  (:meth:`FlowRuntime.on_function_tools_executed`).
* **transition_speech** is queued with `session.say()` inside the tool:
  speech scheduling is not paused yet at tool time, and the old activity's
  `drain()` waits for every queued speech before the next node starts, so the
  caller hears it before the next node talks. Without a TTS (a realtime model
  that cannot `say()`), it goes through `generate_reply(instructions=...)`.
* **Variables.** Taking an edge starts the source node's `extract` in the
  background with the session's `workflow_llm`; the next node waits for it
  (bounded) in `on_enter`, then renders its `{{ name }}` placeholders and the
  "already collected" block before it speaks.
* **End nodes** end the call from the tool: the pending extraction settles,
  `FlowState.disposition` is set, the farewell is spoken and awaited, then the
  job shuts down (the same route as the `end_call` built-in).
* **Prompt order** (R-V2-13): `AgentConfig.instructions` (the agent's base
  prompt, in both modes) → the global node's instructions → the node's own.
  The global node's tools and knowledge bases are added to every node; a
  flow that scopes no knowledge base at all searches the agent's
  `knowledge.kb_ids` from every node (R-V4-29).
* **Transitions in the prompt** (R-V4-30): every node with outgoing edges
  lists them (`go_to_<target> — when <condition>`) and is told to call the
  tool instead of doing the next step's work; a routing start node's only job
  is to pick one.
* **max_turns**: after `max_turns` caller turns in one node without a
  transition, the node's fallback edge (highest `priority`, then declaration
  order) is taken as soon as the agent is listening again.
* **Events**: a `handoff` session event `{from, to, edge_id, reason}` on every
  node entry after the start, and one `flow_ended` event at teardown with the
  final `FlowState`. The disposition and variables reach the api in the
  session summary (R-V2-8): `main._shutdown_callback` hands the `FlowState` to
  `SessionObserver.shutdown(flow=...)` after this teardown settled it.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from livekit.agents import Agent, FunctionToolsExecutedEvent, RunContext, get_job_context
from livekit.agents import llm as lk_llm
from lkap_contracts.agent_config import ResolvedAgentConfig, effective_qa
from lkap_contracts.flow import (
    AgentNode,
    EndNode,
    FlowEdge,
    FlowNode,
    FlowSpec,
    FlowState,
    GlobalNode,
    StartNode,
    TransferNode,
    VariableSpec,
    edge_tool_name,
)
from lkap_contracts.tools import McpServerDefinition
from packs.base import Pack

from lkap_agent.flow.edges import build_edge_tools, condition_clause, group_edges_by_target
from lkap_agent.flow.providers import NodeProviders
from lkap_agent.flow.state import ScopedKbClient, attach_flow_state
from lkap_agent.flow.variables import (
    VariableValue,
    extract_variables,
    known_variables_block,
    referenced_variables,
    render_template,
    transcript_text,
)
from lkap_agent.logging import get_logger
from lkap_agent.platform_agent import SessionContext, compose_instructions
from lkap_agent.providers.factory import ProviderFactory
from lkap_agent.ui.blocks import resolve_block_specs

if TYPE_CHECKING:
    from lkap_agent.flow.node_agent import FlowNodeAgent

__all__ = [
    "EXTRACTION_TIMEOUT_S",
    "NO_CONDITION_CLAUSE",
    "ROUTER_INSTRUCTIONS",
    "SETTLE_TIMEOUT_S",
    "TEARDOWN_SETTLE_TIMEOUT_S",
    "TRANSITION_RULE",
    "ConversationNode",
    "FlowRuntime",
    "FlowServices",
    "TransferHandler",
    "flow_of",
    "is_flow",
    "prepare_flow_resolved",
]

logger = get_logger(__name__)

#: Budget for one extraction call chain (first try + one repair).
EXTRACTION_TIMEOUT_S: Final[float] = 10.0
#: How long the next node waits for the previous node's extraction before it speaks.
SETTLE_TIMEOUT_S: Final[float] = 6.0
#: How long teardown waits for extractions before `flow_ended` is recorded
#: (bounded: the summary target is ≤10 s after hang-up).
TEARDOWN_SETTLE_TIMEOUT_S: Final[float] = 4.0
#: Upper bound on awaiting the end node's farewell playout.
_FAREWELL_PLAYOUT_TIMEOUT_S: Final[float] = 30.0

#: `custom` blocks with this `config.kind` mirror the flow position (CONTRACTS-V2 §4.5).
FLOW_PROGRESS_KIND: Final[str] = "flow_progress"

#: A routing start node's own instruction (R-V4-30).
ROUTER_INSTRUCTIONS: Final[str] = (
    "You are at the start of the call. Your only job here is to find out which step applies and "
    "call its go_to_* tool; do not ask the next step's questions yourself."
)
#: Follows the transitions list of every node with outgoing edges (R-V4-30).
TRANSITION_RULE: Final[str] = (
    "When one of these conditions is met, call that tool right away instead of answering, and do "
    "not do the next step's work yourself. Never mention steps, tools or transitions to the caller."
)
#: The transitions list's clause for an edge without a condition.
NO_CONDITION_CLAUSE: Final[str] = "this step is done"

#: A node that talks: an agent node, or the start node acting as a router.
ConversationNode = AgentNode | StartNode

#: Performs a transfer node's handoff to a human/number (V2-17 plugs telephony in).
#: Returns True when the transfer was started and the flow should end.
TransferHandler = Callable[[TransferNode, FlowState], Awaitable[bool]]


def _default_shutdown(reason: str) -> None:
    get_job_context().shutdown(reason=reason)


def flow_of(resolved: ResolvedAgentConfig) -> FlowSpec | None:
    """The flow a session runs, or `None` for a prompt agent (no flow, or an empty one)."""
    flow = resolved.config.flow
    if flow is None or not flow.nodes:
        return None
    return flow


def is_flow(resolved: ResolvedAgentConfig) -> bool:
    """Whether `resolved` is a flow-mode agent (`AgentConfig.flow` with nodes)."""
    return flow_of(resolved) is not None


def prepare_flow_resolved(resolved: ResolvedAgentConfig) -> ResolvedAgentConfig:
    """Apply the flow's session-level settings to the resolved config.

    * The start node's `greeting`/`greeting_mode` (when set) replace
      `voice.greeting`/`voice.greeting_mode`, so `PlatformAgent` speaks it.
    * `qa` becomes `effective_qa(config)` (R-V2-11): a `qa` node turns QA on
      and its `rubric_prompt` wins. The api resolves `qa_llm` by the same
      rule; if it is missing anyway, the judge reports R-V2-6's
      `failed, "qa_llm not resolved"` rather than QA silently staying off.

    Prompt agents are returned unchanged.
    """
    flow = flow_of(resolved)
    if flow is None:
        return resolved
    config = resolved.config
    voice = config.voice
    start = next(n for n in flow.nodes if isinstance(n, StartNode))
    if start.greeting is not None:
        voice = voice.model_copy(update={"greeting": start.greeting, "greeting_mode": start.greeting_mode})
    return resolved.model_copy(
        update={"config": config.model_copy(update={"voice": voice, "qa": effective_qa(config)})}
    )


@dataclass(slots=True)
class FlowServices:
    """What the worker hands the flow runtime (everything `_assemble` already built)."""

    resolved: ResolvedAgentConfig
    ctx: SessionContext
    pack: Pack
    #: Built-in + declarative HTTP + pack tools; nodes pick theirs by name.
    tool_pool: list[lk_llm.Tool | lk_llm.Toolset]
    provider_factory: ProviderFactory
    has_tts: bool
    #: MCP server rows (`resolved.tools` of kind `mcp`); nodes pick theirs by name.
    mcp_definitions: list[McpServerDefinition] = field(default_factory=list)
    #: Builds `MCPToolset`s for a node's MCP rows (`declarative.build_mcp_toolsets`, or its
    #: `build_mcp_servers` alias); called with `flow_node=True` (the 1.8.3 gate, R-V4-39).
    mcp_servers_builder: Callable[..., list[Any]] = lambda _defs, **_kwargs: []
    vision_max_frame_age_s: float = 8.0
    record_event: Callable[[str, dict[str, Any]], None] | None = None
    #: Ends the job (end nodes); defaults to `get_job_context().shutdown`.
    shutdown: Callable[[str], None] | None = None
    #: Transfer nodes; `None` → transfers report "unavailable" to the model.
    transfer: TransferHandler | None = None
    #: Seed values (e.g. an outbound call's `variables`).
    initial_variables: Mapping[str, VariableValue] | None = None


class FlowRuntime:
    """Runs one `FlowSpec` for one session: node agents, transitions, variables, end."""

    def __init__(self, spec: FlowSpec, services: FlowServices) -> None:
        self.spec = spec
        self.services = services
        ctx = services.ctx
        self._nodes: dict[str, FlowNode] = {n.id: n for n in spec.nodes}
        self._start = next(n for n in spec.nodes if isinstance(n, StartNode))
        self._global = next((n for n in spec.nodes if isinstance(n, GlobalNode)), None)
        self._specs: dict[str, VariableSpec] = {v.name: v for v in spec.variables}
        order = {edge.id: index for index, edge in enumerate(spec.edges)}
        self._out: dict[str, list[FlowEdge]] = {}
        for edge in sorted(spec.edges, key=lambda e: (-e.priority, order[e.id])):
            self._out.setdefault(edge.source, []).append(edge)

        initial = {k: v for k, v in (services.initial_variables or {}).items() if v is not None}
        self.state = FlowState(current_node=self._start.id, path=[self._start.id], variables=initial)
        attach_flow_state(ctx.session, self.state)
        ctx.userdata["flow"] = self.state

        self._kb = ScopedKbClient(ctx.kb, [])
        ctx.kb = self._kb
        self._providers = NodeProviders(
            factory=services.provider_factory, resolved=services.resolved, mode=ctx.pipeline_mode
        )
        self._tools_by_name: dict[str, lk_llm.Tool | lk_llm.Toolset] = {}
        for tool in services.tool_pool:
            name = _tool_name(tool)
            if name:
                self._tools_by_name.setdefault(name, tool)
        self._mcp_by_name = {d.name: d for d in services.mcp_definitions}
        self._allowed_kb_ids = set(services.resolved.kb_ids)
        #: R-V4-29: whether any global/agent node narrows knowledge; if none does, every node
        #: searches all of the agent's resolved knowledge bases.
        self._flow_scopes_kbs = any(n.kb_ids for n in spec.nodes if isinstance(n, AgentNode | GlobalNode))
        self._progress_block_ids = [
            spec_.id
            for spec_ in resolve_block_specs(ctx.config.panel, services.pack.manifest)
            if spec_.type == "custom" and spec_.config.get("kind") == FLOW_PROGRESS_KIND
        ]

        self.current_agent: FlowNodeAgent | None = None
        #: Shared across nodes: D-W2-8 R5 disables per-turn vision for the whole session.
        self.vision_disabled = False
        self._pending: set[asyncio.Task[None]] = set()
        self._extracted: set[str] = set()
        self._background: set[asyncio.Task[Any]] = set()
        self._finished = False
        self._ended = False
        self._warned: set[str] = set()
        #: Edge call id → the node agent that call returned, until its batch is executed.
        self._handoff_targets: dict[str, Agent] = {}
        # R-V4-64: synchronous, so it runs before the SDK reads `has_tool_reply`.
        ctx.session.on("function_tools_executed", self.on_function_tools_executed)

    # ------------------------------------------------------------------ build

    def start_agent(self) -> FlowNodeAgent:
        """The agent `session.start` runs first.

        A start node with exactly one edge to an agent node begins directly at
        that node (the greeting is the start node's; the `handoff` from start
        is recorded on entry). Otherwise the start node itself routes.
        """
        edges = self._out.get(self._start.id, [])
        if len(edges) == 1:
            target = self._nodes[edges[0].target]
            if isinstance(target, AgentNode):
                return self.build_node_agent(
                    target, chat_ctx=None, entry=True, handoff=(self._start.id, edges[0].id, "start")
                )
        return self.build_node_agent(self._start, chat_ctx=None, entry=True, handoff=None)

    def build_node_agent(
        self,
        node: ConversationNode,
        *,
        chat_ctx: lk_llm.ChatContext | None,
        entry: bool,
        handoff: tuple[str, str, str] | None,
    ) -> FlowNodeAgent:
        """Construct the agent for `node` (not started; the SDK starts it on handoff)."""
        from lkap_agent.flow.node_agent import FlowNodeAgent  # noqa: PLC0415  (import cycle)

        return FlowNodeAgent(runtime=self, node=node, chat_ctx=chat_ctx, entry=entry, handoff=handoff)

    # -------------------------------------------------------------- node parts

    def node_label(self, node_id: str) -> str:
        """The node's label, or its id."""
        node = self._nodes.get(node_id)
        return (node.label or node.id) if node is not None else node_id

    def outgoing(self, node_id: str) -> list[FlowEdge]:
        """`node_id`'s edges, highest priority first, then declaration order."""
        return list(self._out.get(node_id, []))

    def fallback_edge(self, node_id: str) -> FlowEdge | None:
        """The edge `max_turns` forces: the first of :meth:`outgoing`."""
        edges = self._out.get(node_id, [])
        return edges[0] if edges else None

    def instructions_for(self, node: ConversationNode) -> str:
        """The composed system prompt for `node`, rendered with the current variables.

        Order (R-V2-13): `AgentConfig.instructions` (the base prompt) → the
        global node's instructions → the node's instructions → flow notes → the
        already-collected variables → the pack's mode addendum and the
        platform pipeline note (`compose_instructions`). Empty parts are
        skipped, so an author who wants the global node alone empties
        `instructions`.
        """
        variables = self.state.variables
        parts: list[str] = [
            render_template(self.services.ctx.config.instructions, variables, missing="(not yet known)")
        ]
        if self._global is not None:
            parts.append(render_template(self._global.instructions, variables, missing="(not yet known)"))
        if isinstance(node, AgentNode):
            parts.append(render_template(node.instructions, variables, missing="(not yet known)"))
        else:
            parts.append(ROUTER_INSTRUCTIONS)
        edges = self._out.get(node.id)
        if edges:
            parts.append(
                f"Conversation flow: you are in the step '{node.label or node.id}'. "
                f"Your next steps: {self._transitions_text(edges)}. {TRANSITION_RULE}"
            )
        known = known_variables_block(variables, self.spec.variables)
        if known:
            parts.append(known)
        base = "\n\n".join(p.strip() for p in parts if p and p.strip())
        return compose_instructions(
            base, mode=self.services.ctx.pipeline_mode, manifest=self.services.pack.manifest
        )

    def _transitions_text(self, edges: list[FlowEdge]) -> str:
        """`go_to_<target> — when <condition>` for each distinct target, `; `-joined (R-V4-30)."""
        items: list[str] = []
        for target_id, group in group_edges_by_target(edges).items():
            conditions = [c for c in (condition_clause(e.condition) for e in group) if c]
            when = " or ".join(conditions) if conditions else NO_CONDITION_CLAUSE
            items.append(f"{edge_tool_name(target_id)} — when {when}")
        return "; ".join(items)

    def _tool_names_for(self, node: ConversationNode) -> list[str]:
        names = list(self._global.tools) if self._global is not None else []
        if isinstance(node, AgentNode):
            names += node.tools
        return list(dict.fromkeys(names))

    def tools_for(self, node: ConversationNode) -> list[lk_llm.Tool | lk_llm.Toolset]:
        """The node's tools (global + node, by name) followed by its edge tools."""
        tools: list[lk_llm.Tool | lk_llm.Toolset] = []
        for name in self._tool_names_for(node):
            tool = self._tools_by_name.get(name)
            if tool is not None:
                tools.append(tool)
            elif name not in self._mcp_by_name:
                self._warn_once(
                    f"tool:{node.id}:{name}",
                    "flow node references a tool this session does not have",
                    node=node.id,
                    tool=name,
                )
        tools.extend(build_edge_tools(self, node.id, self._out.get(node.id, [])))
        return tools

    def mcp_toolsets_for(self, node: ConversationNode) -> list[Any]:
        """Fresh `MCPToolset`s for the node's MCP tool rows (by name); the node's activity closes them."""
        defs = [self._mcp_by_name[n] for n in self._tool_names_for(node) if n in self._mcp_by_name]
        if not defs:
            return []
        try:
            return list(self.services.mcp_servers_builder(defs, flow_node=True))
        except Exception:
            logger.warning("could not build the node's MCP toolsets", node=node.id, exc_info=True)
            return []

    def kb_ids_for(self, node: ConversationNode) -> list[str]:
        """The knowledge bases `node` searches (R-V4-29).

        When no `global` or `agent` node declares a `kb_ids`, every node (the
        routing start included) searches all of the agent's resolved knowledge
        bases, in list order. Otherwise: the global node's plus the node's own,
        limited to the ones the api resolved for the session.
        """
        if not self._flow_scopes_kbs:
            return list(self.services.resolved.kb_ids)
        ids = list(self._global.kb_ids) if self._global is not None else []
        if isinstance(node, AgentNode):
            ids += node.kb_ids
        kept = []
        for kb_id in dict.fromkeys(ids):
            if kb_id in self._allowed_kb_ids:
                kept.append(kb_id)
            else:
                self._warn_once(
                    f"kb:{node.id}:{kb_id}",
                    "flow node references a knowledge base not resolved for this session",
                    node=node.id,
                    kb_id=kb_id,
                )
        return kept

    def agent_options_for(self, node: ConversationNode) -> dict[str, Any]:
        """`Agent` kwargs for the node: `llm`/`tts` overrides and `allow_interruptions`."""
        options: dict[str, Any] = {}
        if isinstance(node, AgentNode):
            options.update(self._providers.agent_kwargs(node))
            if node.allow_interruptions is not None:
                options["turn_handling"] = {"interruption": {"enabled": node.allow_interruptions}}
        return options

    @property
    def provider_warnings(self) -> list[str]:
        """Every provider-override warning so far (for tests and diagnostics)."""
        return list(self._providers.warnings)

    # ----------------------------------------------------------------- entry

    def enter(self, agent: FlowNodeAgent, handoff: tuple[str, str, str] | None) -> None:
        """Book-keeping when `agent` becomes the active node (from its `on_enter`)."""
        self.current_agent = agent
        node = agent.node
        if node.id != self.state.current_node:
            self.state.current_node = node.id
            self.state.path.append(node.id)
        self._kb.set_scope(self.kb_ids_for(node))
        if handoff is not None:
            source, edge_id, reason = handoff
            self._record("handoff", {"from": source, "to": node.id, "edge_id": edge_id, "reason": reason})
        logger.info("flow node entered", node=node.id, path=list(self.state.path))
        self.publish_progress()

    async def settle(self, timeout_s: float = SETTLE_TIMEOUT_S) -> None:
        """Wait (bounded) for in-flight extractions (and a handoff's carried tool pairs) to land."""
        pending = [t for t in self._pending if not t.done()]
        if not pending:
            return
        _done, still = await asyncio.wait(pending, timeout=timeout_s)
        if still:
            logger.warning("flow variable extraction still running; continuing", pending=len(still))

    # ------------------------------------------------------------ transitions

    async def take_edge(self, source_id: str, edge: FlowEdge, context: RunContext[Any]) -> Any:
        """Tool body of `go_to_<target>` (see :mod:`lkap_agent.flow.edges`)."""
        agent = self.current_agent
        if agent is None or agent.node.id != source_id:
            current = getattr(context.session, "current_agent", None)
            agent = current if current is not None and hasattr(current, "node") else agent
        if agent is None:
            return "The conversation flow is not ready yet."
        result = await self.transition(agent, edge, reason="edge")
        if isinstance(result, Agent):
            # The batch this call belongs to hands off to `result` (R-V4-64).
            self._handoff_targets[context.function_call.call_id] = result
        return result

    async def transition(self, source: FlowNodeAgent, edge: FlowEdge, *, reason: str) -> Any:
        """Leave `source` along `edge`.

        Returns:
            The next `FlowNodeAgent` (agent/start target); `None` when the flow
            ended (end node) or a transfer took over; a string for the model
            when the transition cannot happen.
        """
        if self._finished:
            return "The call is already ending."
        target = self._nodes.get(edge.target)
        if target is None:
            return "That step does not exist."
        self._begin_extraction(source.node)
        logger.info(
            "flow transition", source=source.node.id, target=target.id, edge_id=edge.id, reason=reason
        )
        match target:
            case AgentNode() | StartNode():
                if edge.transition_speech:
                    if referenced_variables(edge.transition_speech) & _extract_names(source.node):
                        # The line names a value this very transition extracts: wait for it.
                        await self.settle()
                    self._speak(render_template(edge.transition_speech, self.state.variables))
                return self.build_node_agent(
                    target,
                    chat_ctx=source.chat_ctx,
                    entry=False,
                    handoff=(source.node.id, edge.id, reason),
                )
            case EndNode():
                await self._finish(source, target, edge, reason)
                return None
            case TransferNode():
                return await self._transfer(source, target, edge, reason)
            case _:
                return "That step cannot be entered."

    def on_function_tools_executed(self, ev: FunctionToolsExecutedEvent) -> None:
        """No reply from the draining node once a batch hands off (R-V4-64, V4-19).

        livekit-agents 1.8.3 gathers a whole batch of parallel tool calls, then
        emits this event, then — when a tool returned an `Agent` — calls
        `update_agent` and still generates the tool reply on the **old**
        activity, with the old node's instructions, whenever any output of the
        batch has `reply_required` (`AgentActivity._pipeline_reply_task_impl`;
        the realtime path is the same). An edge tool's own output never asks for
        a reply, but a sibling's does — a plain result, or a background tool's
        first `ctx.update()` announce — so the caller heard the router before
        the target node. Completion order inside the batch does not matter: the
        decision is made over all of its outputs at once.

        So when the batch hands off, every output is marked
        `reply_required=False` (`cancel_tool_reply`, the SDK's public knob; a
        `ToolResult` returned by the tool cannot reach a background tool's
        announce). A background sibling still running is cancelled by the old
        activity's drain (D-V4-37).

        The batch's call/output pairs are also carried into the target node:
        the target copied the old node's context when the edge tool ran, before
        the SDK committed the batch, and the SDK merges nothing on a handoff.
        They are added through `Agent.update_chat_ctx` (a plain assignment while
        the target has no activity yet, filtered to the target's tools like
        `Agent.__init__`'s copy), tracked with the extractions so the target's
        `on_enter` settles it before `generate_reply()` answers from them.

        Registered on the session as a **synchronous** handler: the SDK reads
        `has_tool_reply` right after emitting the event.
        """
        targets = [
            self._handoff_targets.pop(call.call_id)
            for call in ev.function_calls
            if call.call_id in self._handoff_targets
        ]
        if not ev.has_agent_handoff:
            return
        if ev.has_tool_reply:
            ev.cancel_tool_reply()
            logger.debug(
                "flow handoff: no reply from the draining node",
                node=self.state.current_node,
                tools=[call.name for call in ev.function_calls],
            )
        if len(targets) == 1:
            self._carry_batch(targets[0], ev)

    def _carry_batch(self, target: Agent, ev: FunctionToolsExecutedEvent) -> None:
        """Add the batch's call/output pairs to `target`'s context (see above)."""
        chat_ctx = target.chat_ctx.copy()
        known = {item.id for item in chat_ctx.items}
        items: list[lk_llm.ChatItem] = [
            *(call for call in ev.function_calls if call.id not in known),
            *(out for out in ev.function_call_outputs if out.id not in known),
        ]
        if not items:
            return
        chat_ctx.insert(items)
        task = asyncio.create_task(target.update_chat_ctx(chat_ctx), name="lkap_flow_carry_batch")
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def fallback(self, source: FlowNodeAgent) -> None:
        """Take `source`'s fallback edge outside a tool (the `max_turns` guard)."""
        edge = self.fallback_edge(source.node.id)
        if edge is None:
            self._warn_once(
                f"fallback:{source.node.id}",
                "max_turns reached but the node has no edge",
                node=source.node.id,
            )
            return
        result = await self.transition(source, edge, reason="max_turns")
        if result is not None and not isinstance(result, str):
            source.session.update_agent(result)

    async def _finish(self, source: FlowNodeAgent, end: EndNode, edge: FlowEdge, reason: str) -> None:
        self._finished = True
        await self.settle()
        self.state.current_node = end.id
        self.state.path.append(end.id)
        self.state.disposition = end.disposition
        self._record("handoff", {"from": source.node.id, "to": end.id, "edge_id": edge.id, "reason": reason})
        logger.info("flow reached an end node", node=end.id, disposition=end.disposition)
        self.publish_progress()
        texts = [edge.transition_speech, end.farewell]
        for text in texts:
            if not text:
                continue
            handle = self._speak(render_template(text, self.state.variables))
            if handle is not None and inspect.isawaitable(handle):
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(_await(handle), _FAREWELL_PLAYOUT_TIMEOUT_S)
        shutdown = self.services.shutdown or _default_shutdown
        try:
            shutdown(f"flow reached end node {end.id}")
        except Exception:
            logger.warning("could not end the job after the flow's end node", exc_info=True)

    async def _transfer(self, source: FlowNodeAgent, node: TransferNode, edge: FlowEdge, reason: str) -> Any:
        handler = self.services.transfer
        if handler is None:
            self._record(
                "transfer", {"node": node.id, "to": node.to, "mode": node.mode, "status": "unavailable"}
            )
            return (
                "Transferring the call is not available right now. Tell the caller and keep helping "
                "them in the current step."
            )
        if node.announce:
            self._speak(render_template(node.announce, self.state.variables))
        self.state.current_node = node.id
        self.state.path.append(node.id)
        self._record("handoff", {"from": source.node.id, "to": node.id, "edge_id": edge.id, "reason": reason})
        try:
            started = await handler(node, self.state)
        except Exception as exc:
            logger.warning("flow transfer failed", node=node.id, exc_info=True)
            started = False
            self._record("transfer", {"node": node.id, "to": node.to, "status": "failed", "error": str(exc)})
        if started:
            self._finished = True
            return None
        return "The transfer did not go through. Apologise to the caller and keep helping them."

    # -------------------------------------------------------------- variables

    def _begin_extraction(self, node: FlowNode) -> None:
        """Start `node`'s exit extraction once, in the background."""
        if not isinstance(node, AgentNode) or not node.extract or node.id in self._extracted:
            return
        self._extracted.add(node.id)
        specs = []
        for name in node.extract:
            spec = self._specs.get(name)
            if spec is None:
                self._warn_once(
                    f"var:{node.id}:{name}",
                    "extract names an undeclared variable",
                    node=node.id,
                    variable=name,
                )
                continue
            specs.append(spec)
        if not specs:
            return
        try:
            history = self.services.ctx.session.history
        except Exception:
            history = None
        transcript = transcript_text(history) if isinstance(history, lk_llm.ChatContext) else ""
        task = asyncio.create_task(self._extract(node.id, specs, transcript))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _extract(self, node_id: str, specs: list[VariableSpec], transcript: str) -> None:
        values = await extract_variables(
            self.services.ctx.workflow_llm, specs, transcript, timeout_s=EXTRACTION_TIMEOUT_S
        )
        if not values:
            return
        self.state.variables.update(values)
        logger.info("flow variables captured", node=node_id, names=sorted(values))
        self._record("info", {"message": "flow variables captured", "node": node_id, "names": sorted(values)})
        agent = self.current_agent
        if agent is not None and agent.entered:
            self.spawn(agent.refresh_instructions())
        self.publish_progress()

    # --------------------------------------------------------------- teardown

    async def on_session_end(self, reason: str) -> None:
        """Capture the current node's variables (bounded) and record `flow_ended` once."""
        if self._ended:
            return
        self._ended = True
        current = self._nodes.get(self.state.current_node)
        if current is not None and not self._finished:
            self._begin_extraction(current)
        await self.settle(TEARDOWN_SETTLE_TIMEOUT_S)
        end = self._nodes.get(self.state.current_node)
        payload: dict[str, Any] = {
            "completed": isinstance(end, EndNode),
            "current_node": self.state.current_node,
            "path": list(self.state.path),
            "variables": dict(self.state.variables),
            "disposition": self.state.disposition,
            "reason": reason,
        }
        if isinstance(end, EndNode):
            payload["webhook_event"] = end.webhook_event
        missing = [
            s.name for s in self.spec.variables if s.required and self.state.variables.get(s.name) is None
        ]
        if missing:
            payload["missing_required"] = missing
        self._record("flow_ended", payload)
        for task in list(self._background):
            task.cancel()

    # ---------------------------------------------------------------- helpers

    def publish_progress(self) -> None:
        """Mirror `FlowState` into every `flow_progress` custom block (best effort)."""
        if not self._progress_block_ids:
            return
        set_block = getattr(self.services.ctx.ui, "set_block", None)
        if not callable(set_block):
            return
        state = {
            "current_node": self.state.current_node,
            "label": self.node_label(self.state.current_node),
            "path": list(self.state.path),
            "variables": dict(self.state.variables),
            "disposition": self.state.disposition,
        }
        for block_id in self._progress_block_ids:
            self.spawn(set_block(block_id, dict(state)))

    def _speak(self, text: str) -> Any:
        """Queue `text` on the current activity.

        `AgentActivity.say()` needs a TTS only while audio output is on (a
        text-channel session has none), so it is used whenever it can work;
        otherwise (a realtime model without `say()` support) the line goes
        through `generate_reply(instructions=...)`.
        """
        text = text.strip()
        if not text:
            return None
        session = self.services.ctx.session
        try:
            if self.services.has_tts or not _audio_output_on(session):
                return session.say(text)
            return session.generate_reply(instructions=f"Say exactly this and nothing more: {text}")
        except Exception:
            logger.warning("could not speak a flow line", exc_info=True)
            return None

    def _record(self, event_type: str, payload: dict[str, Any]) -> None:
        record = self.services.record_event
        if record is None:
            return
        try:
            record(event_type, payload)
        except Exception:
            logger.debug("could not record a flow event", event_type=event_type, exc_info=True)

    def spawn(self, awaitable: Awaitable[Any]) -> None:
        async def _run() -> None:
            try:
                await awaitable
            except Exception:
                logger.debug("flow background step failed", exc_info=True)

        task = asyncio.create_task(_run())
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def _warn_once(self, key: str, message: str, **fields: Any) -> None:
        if key in self._warned:
            return
        self._warned.add(key)
        logger.warning(message, **fields)


def _audio_output_on(session: Any) -> bool:
    """Whether the session publishes audio (mirrors the check in `AgentActivity.say`)."""
    output = getattr(session, "output", None)
    return bool(getattr(output, "audio", None) is not None and getattr(output, "audio_enabled", False))


def _extract_names(node: FlowNode) -> set[str]:
    return set(node.extract) if isinstance(node, AgentNode) else set()


async def _await(awaitable: Any) -> None:
    await awaitable


def _tool_name(tool: lk_llm.Tool | lk_llm.Toolset) -> str | None:
    """A tool's model-facing name (`Toolset`s by id)."""
    if isinstance(tool, lk_llm.FunctionTool | lk_llm.RawFunctionTool):
        return tool.info.name
    if isinstance(tool, lk_llm.Toolset):
        return tool.id
    return None
