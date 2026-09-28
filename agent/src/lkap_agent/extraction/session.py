"""One session's live structure: the extraction runner and the rules engine, wired to the session.

:func:`live_structure` builds a :class:`LiveStructure` once per session (``userdata``) when the
agent has extraction on or any rule, and returns ``None`` otherwise, so an agent saved before
V6-13 gets no listener, no task and no call. It is first built by the built-in tool builder
(every session passes through it at assembly, before any tool can run, realtime pipelines
included) and again, idempotently, by ``PlatformAgent.on_user_turn_completed``.

It listens to the ``AgentSession`` itself:

* ``conversation_item_added`` — a caller message counts towards ``every_n_turns`` (turns
  shorter than ``min_turn_chars`` do not); an ``AgentHandoff`` item whose ``old_agent_id`` is a
  flow step (a node agent's id is its node id) is a ``node_exit``;
* ``function_tools_executed`` — records each tool's outcome for ``tool.<name>.ok``, runs a
  ``tool`` trigger, and re-evaluates the rules (a tool's bindings are applied before it returns,
  so they are in place). Background tools report later through ``tool_execution_updated``;
  their outcome counts from the batch that carries their final result;
* ``close`` — cancels anything still running.

Every extraction runs as a task (a new trigger while one runs is coalesced into one more
run), so the caller's reply is never delayed. After each extraction the rules are evaluated.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any, Final

from livekit.agents import llm as lk_llm
from lkap_contracts.extraction import (
    EXTRACTION_BUDGET_S,
    EveryNTurnsTrigger,
    ManualTrigger,
    NodeExitTrigger,
    ToolTrigger,
)

from lkap_agent.extraction.runner import ExtractionRun, ExtractionRunner, Trigger
from lkap_agent.logging import get_logger
from lkap_agent.rules.engine import RulesEngine, RuleTrigger

__all__ = [
    "LIVE_STRUCTURE_USERDATA_KEY",
    "LiveStructure",
    "live_structure",
    "wants_live_structure",
    "wants_extract_now",
]

_log = get_logger(__name__)

#: ``SessionContext.userdata`` key of the session's :class:`LiveStructure`.
LIVE_STRUCTURE_USERDATA_KEY: Final[str] = "lkap.live_structure"


def wants_live_structure(config: Any) -> bool:
    """Whether an agent config has live extraction on (with fields) or any enabled rule."""
    extraction = getattr(config, "extraction", None)
    rules = getattr(config, "rules", None) or []
    on = bool(extraction is not None and extraction.enabled and extraction.fields)
    return on or any(getattr(rule, "enabled", True) for rule in rules)


def wants_extract_now(config: Any) -> bool:
    """Whether the agent gets the ``extract_now`` tool (extraction on with a ``manual`` trigger)."""
    extraction = getattr(config, "extraction", None)
    if extraction is None or not extraction.enabled or not extraction.fields:
        return False
    return any(isinstance(trigger, ManualTrigger) for trigger in extraction.triggers)


class LiveStructure:
    """The session's extraction runner and rules engine (see the module docstring)."""

    def __init__(self, ctx: Any) -> None:
        """Build the parts the agent config asks for (does not attach; see :meth:`attach`)."""
        self.ctx = ctx
        config = ctx.config
        extraction = config.extraction
        self.runner: ExtractionRunner | None = (
            ExtractionRunner(ctx, extraction) if extraction.enabled and extraction.fields else None
        )
        self.engine: RulesEngine | None = RulesEngine(ctx, list(config.rules)) if config.rules else None
        self._every: int | None = None
        self._tool_triggers: set[str] = set()
        self._node_exit: set[str] | None = None
        if self.runner is not None:
            for trigger in extraction.triggers:
                match trigger:
                    case EveryNTurnsTrigger(n=n):
                        self._every = n
                    case ToolTrigger(tools=tools):
                        self._tool_triggers = set(tools)
                    case NodeExitTrigger(nodes=nodes):
                        self._node_exit = set(nodes)
        self._min_chars = extraction.min_turn_chars
        self._turns = 0
        self._outcomes: dict[str, bool] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._running: asyncio.Task[ExtractionRun | None] | None = None
        self._again: Trigger | None = None
        self._started = False
        self._closed = False

    # ------------------------------------------------------------------ wiring

    def attach(self, session: Any) -> bool:
        """Listen to ``session``'s events; ``False`` when it cannot be listened to (a test double)."""
        on = getattr(session, "on", None)
        if not callable(on):
            return False
        try:
            on("conversation_item_added", self.on_conversation_item)
            on("function_tools_executed", self.on_function_tools_executed)
            on("close", self.on_close)
        except Exception:
            _log.debug("live_structure.attach_failed", exc_info=True)
            return False
        return True

    def _spawn(self, coro: Any) -> asyncio.Task[Any] | None:
        if self._closed:
            coro.close()
            return None
        try:
            task = asyncio.get_running_loop().create_task(coro)
        except RuntimeError:
            coro.close()
            return None
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # ------------------------------------------------------------------ events

    def on_conversation_item(self, ev: Any) -> None:
        """A caller message (``every_n_turns``) or a flow step change (``node_exit``)."""
        item = getattr(ev, "item", None)
        if isinstance(item, lk_llm.ChatMessage):
            if item.role == "user":
                self._on_caller_turn(item.text_content or "")
            return
        if isinstance(item, lk_llm.AgentHandoff):
            old = item.old_agent_id
            if old and self._node_exit is not None and (not self._node_exit or old in self._node_exit):
                self.request("node_exit")

    def _on_caller_turn(self, text: str) -> None:
        first = not self._started
        self._started = True
        if first and self.runner is not None:
            self._spawn(self.runner.refresh_panel())
        counted = len(text.strip()) >= self._min_chars
        if counted:
            self._turns += 1
        if self.runner is not None and self._every is not None and counted and self._turns % self._every == 0:
            self.request("turn")
        elif self.engine is not None:
            self._spawn(self._evaluate("turn"))

    def on_function_tools_executed(self, ev: Any) -> None:
        """Record the tools' outcomes, run a ``tool`` trigger, re-evaluate the rules."""
        names: set[str] = set()
        try:
            for call, output in ev.zipped():
                self._outcomes[call.name] = not bool(getattr(output, "is_error", False))
                names.add(call.name)
        except Exception:
            _log.debug("live_structure.tool_outcomes_failed", exc_info=True)
        if self.runner is not None and names & self._tool_triggers:
            self.request("tool")
        elif self.engine is not None and names:
            self._spawn(self._evaluate("tool"))

    def on_close(self, _ev: Any = None) -> None:
        """Cancel whatever is still running (the session ended)."""
        self._closed = True
        for task in list(self._tasks):
            task.cancel()

    # ------------------------------------------------------------------ running

    def request(self, trigger: Trigger) -> None:
        """Run an extraction in the background; a request while one runs becomes one more run."""
        if self.runner is None:
            return
        if self._running is not None and not self._running.done():
            self._again = trigger
            return
        self._running = self._spawn(self._loop(trigger))

    async def _loop(self, trigger: Trigger) -> ExtractionRun | None:
        result = await self._run_once(trigger)
        while self._again is not None and not self._closed:
            trigger, self._again = self._again, None
            result = await self._run_once(trigger)
        return result

    async def _run_once(self, trigger: Trigger) -> ExtractionRun | None:
        assert self.runner is not None
        result = await self.runner.run(trigger)
        await self._evaluate("extraction")
        return result

    async def run_now(self) -> ExtractionRun | None:
        """``extract_now``: wait for a run in progress (within the budget), then run once more."""
        if self.runner is None:
            return None
        running = self._running
        if running is not None and not running.done():
            with contextlib.suppress(Exception):
                await asyncio.wait_for(asyncio.shield(running), timeout=EXTRACTION_BUDGET_S)
        return await self._run_once("manual")

    async def _evaluate(self, trigger: RuleTrigger) -> None:
        if self.engine is not None and self.engine.active:
            await self.engine.evaluate(trigger, self._outcomes)

    async def wait_idle(self) -> None:
        """Wait for every task started so far (tests)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # ------------------------------------------------------------------ the next turn

    def add_instructions(self, turn_ctx: lk_llm.ChatContext) -> int:
        """Add the pending ``instruct`` notes to this turn's context only; how many were added."""
        if self.engine is None:
            return 0
        notes = self.engine.take_instructions()
        for text in notes:
            turn_ctx.add_message(role="system", content=text)
        return len(notes)


def live_structure(ctx: Any) -> LiveStructure | None:
    """The session's :class:`LiveStructure`, built and attached on first use; ``None`` when not wanted."""
    userdata = getattr(ctx, "userdata", None)
    if not isinstance(userdata, dict):
        return None
    existing = userdata.get(LIVE_STRUCTURE_USERDATA_KEY)
    if isinstance(existing, LiveStructure):
        return existing
    if not wants_live_structure(getattr(ctx, "config", None)):
        return None
    structure = LiveStructure(ctx)
    structure.attach(getattr(ctx, "session", None))
    userdata[LIVE_STRUCTURE_USERDATA_KEY] = structure
    return structure
