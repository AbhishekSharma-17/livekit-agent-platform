"""`FlowNodeAgent`: one flow node as a `PlatformAgent` (ARCHITECTURE-V2 D-V2-13).

Everything a prompt agent does per turn — knowledge auto-inject, per-turn
vision, pack hooks, silent-reply control, panel blocks — still comes from
`PlatformAgent`; a node only changes *what* it runs with:

* instructions: global prefix + node prompt + flow notes + collected
  variables, rendered by the runtime (`PlatformAgent(instructions=...)`);
* tools: the node's and the global node's, plus its `go_to_*` edge tools;
* knowledge: auto-inject searches the node's (and global) knowledge bases
  (`_auto_inject_kb_ids` hook + the runtime's scoped `SessionContext.kb`);
* `Agent(id=node.id, chat_ctx=<previous node's chat_ctx>, llm=, tts=,
  turn_handling=)` via `PlatformAgent(agent_options=...)`.

Session-level handlers (`function_tools_executed`, `conversation_item_added`,
`error`, the shutdown callback) are bound by the worker to the **first** node
agent only; they read shared state (the one `SessionContext`, the pack) so they
stay correct after handoffs, and the two that are per-agent — the vision
auto-degrade (`on_session_error`) and the teardown (`on_pack_session_end`) —
are routed through the runtime here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from livekit.agents import AgentStateChangedEvent, ConversationItemAddedEvent
from livekit.agents import llm as lk_llm
from lkap_contracts.flow import AgentNode

from lkap_agent.flow.variables import render_template
from lkap_agent.logging import get_logger
from lkap_agent.platform_agent import PlatformAgent

if TYPE_CHECKING:
    from lkap_agent.flow.runtime import ConversationNode, FlowRuntime

__all__ = ["FlowNodeAgent"]

logger = get_logger(__name__)


class FlowNodeAgent(PlatformAgent):
    """The running agent for one `agent` node (or the routing `start` node)."""

    def __init__(
        self,
        *,
        runtime: FlowRuntime,
        node: ConversationNode,
        chat_ctx: lk_llm.ChatContext | None,
        entry: bool,
        handoff: tuple[str, str, str] | None,
    ) -> None:
        """Build the agent for `node`.

        Args:
            runtime: The session's flow runtime.
            node: The node this agent runs.
            chat_ctx: The previous node's chat context (carried; `None` for the entry node).
            entry: Whether `session.start` runs this agent (it greets and publishes the UI).
            handoff: `(from_node, edge_id, reason)` recorded when this node is entered.
        """
        self._runtime = runtime
        self._node = node
        self._entry = entry
        self._handoff = handoff
        self._entered = False
        self._user_turns = 0
        self._fallback_fired = False
        services = runtime.services
        options: dict[str, Any] = {"id": node.id, **runtime.agent_options_for(node)}
        if chat_ctx is not None:
            options["chat_ctx"] = chat_ctx
        super().__init__(
            ctx=services.ctx,
            pack=services.pack,
            tools=runtime.tools_for(node),
            mcp_toolsets=runtime.mcp_toolsets_for(node),
            has_tts=services.has_tts,
            vision_max_frame_age_s=services.vision_max_frame_age_s,
            record_event=services.record_event,
            instructions=runtime.instructions_for(node),
            agent_options=options,
            # R-V4-71: the session's one `function_tools_executed` handler is the entry
            # node's, but every node carries the same set; the entry node alone logs.
            silent_reply_tools=services.silent_reply_tools,
            report_silent_reply_unhonoured=entry,
        )
        # D-W2-8 R5 is session-wide: a node entered after vision was disabled keeps it off.
        self._vision_disabled = runtime.vision_disabled

    # ------------------------------------------------------------ properties

    @property
    def node(self) -> ConversationNode:
        """The flow node this agent runs."""
        return self._node

    @property
    def runtime(self) -> FlowRuntime:
        """The session's flow runtime."""
        return self._runtime

    @property
    def entered(self) -> bool:
        """Whether this node's `on_enter` has run (it is or was the active node)."""
        return self._entered

    @property
    def max_turns(self) -> int | None:
        """The node's `max_turns` guard (`None`: unlimited)."""
        return self._node.max_turns if isinstance(self._node, AgentNode) else None

    # ----------------------------------------------------------------- hooks

    async def on_enter(self) -> None:
        """Enter the node: book-keeping, settle variables, then greet (entry) or continue."""
        self._runtime.enter(self, self._handoff)
        self._entered = True
        if self.max_turns is not None:
            self.session.on("conversation_item_added", self._on_conversation_item_for_turns)
            self.session.on("agent_state_changed", self._on_agent_state_for_turns)
        await self._runtime.settle()
        await self.refresh_instructions()
        if self._entry:
            await super().on_enter()
            return
        self.session.generate_reply()

    async def on_exit(self) -> None:
        """Stop counting turns for this node."""
        if self.max_turns is None:
            return
        try:
            self.session.off("conversation_item_added", self._on_conversation_item_for_turns)
            self.session.off("agent_state_changed", self._on_agent_state_for_turns)
        except Exception:
            logger.debug("could not detach the flow node's turn counters", node=self._node.id, exc_info=True)

    async def refresh_instructions(self) -> None:
        """Re-render the node prompt (variables may have changed) and update it if it differs."""
        fresh = self._runtime.instructions_for(self._node)
        if fresh == self.instructions:
            return
        try:
            await self.update_instructions(fresh)
        except Exception:
            logger.warning("could not refresh the flow node instructions", node=self._node.id, exc_info=True)

    def _greeting_text(self) -> str:
        """The start greeting with `{{ name }}` placeholders rendered (seeded variables only)."""
        return render_template(super()._greeting_text(), self._runtime.state.variables).strip()

    def _auto_inject_kb_ids(self) -> list[str]:
        """Auto-inject searches the node's and the global node's knowledge bases."""
        return self._runtime.kb_ids_for(self._node)

    def on_session_error(self, ev: Any) -> str | None:
        """Route the vision auto-degrade to the active node and share its outcome."""
        current = self._runtime.current_agent
        if current is not None and current is not self:
            return current.on_session_error(ev)
        message = super().on_session_error(ev)
        if self.vision_disabled:
            self._runtime.vision_disabled = True
        return message

    async def on_pack_session_end(self, reason: str) -> None:
        """Record the final flow state (`flow_ended`), then run the pack's teardown."""
        try:
            await self._runtime.on_session_end(reason)
        except Exception:
            logger.warning("flow teardown failed", exc_info=True)
        await super().on_pack_session_end(reason)

    # -------------------------------------------------------------- max_turns

    def _on_conversation_item_for_turns(self, ev: ConversationItemAddedEvent) -> None:
        """Count the caller's turns while this node is active."""
        item = ev.item
        if self._runtime.current_agent is not self or not isinstance(item, lk_llm.ChatMessage):
            return
        if item.role == "user" and item.text_content:
            self._user_turns += 1

    def _on_agent_state_for_turns(self, ev: AgentStateChangedEvent) -> None:
        """Once the node has answered `max_turns` caller turns, take the fallback edge."""
        limit = self.max_turns
        if limit is None or self._fallback_fired or ev.new_state != "listening":
            return
        if self._runtime.current_agent is not self or self.session.current_agent is not self:
            return
        if self._user_turns < limit:
            return
        self._fallback_fired = True
        logger.info("flow node reached max_turns", node=self._node.id, max_turns=limit)
        self._runtime.spawn(self._runtime.fallback(self))
