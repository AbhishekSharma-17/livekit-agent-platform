"""The declarative rules engine (V6-13, D-V6-25).

One :class:`RulesEngine` per session evaluates ``AgentConfig.rules`` (their ``when`` parsed
once by :mod:`lkap_contracts.rules_expr`, never ``eval``) against the session's variables
and each tool's latest outcome, and runs the actions of every rule that fires:

* ``once: true`` fires at most once per session; ``once: false`` fires each time its
  condition turns from false to true (never on every pass while it stays true);
* a ``var.set`` that changed a variable triggers another pass (at most :data:`MAX_PASSES`);
* ``instruct`` texts are queued for the **next** caller turn, where
  ``PlatformAgent.on_user_turn_completed`` adds them to that turn's context only (a
  one-turn system note). A realtime model's server-side turns never pass through that
  hook, so there an ``instruct`` waits for the next turn that does;
* ``escalate`` does what ``escalate_to_human`` does (status, the ``escalation`` event in its
  shape, the ``handoff`` block, the watcher for a person joining) with the rule's reason;
  it does not post to the team's webhook;
* each firing records a ``rule_fired`` session event: the rule id, its label and the kinds
  of its actions, never a value.
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections.abc import Mapping
from typing import Any, Final, Literal

from lkap_contracts.api_models import EscalationEvent
from lkap_contracts.flow import FlowState
from lkap_contracts.rules import (
    RULE_FIRED_EVENT,
    ChecklistCheckAction,
    ChecklistSetItemAction,
    DetailsSetAction,
    DispositionSetAction,
    EscalateAction,
    InstructAction,
    NotePushAction,
    Rule,
    RuleAction,
    RuleFiredEvent,
    StatusSetAction,
    VarSetAction,
)
from lkap_contracts.rules_expr import ConditionError, Expr, evaluate, parse_condition
from lkap_contracts.ui_protocol import ActivityEvent, ChecklistItem, Note, UiPatchOp

from lkap_agent.flow.variables import EXTRACTED_VARIABLES_USERDATA_KEY
from lkap_agent.logging import get_logger
from lkap_agent.tools.context import BOUND_VARIABLES_USERDATA_KEY, session_variables

__all__ = ["DISPOSITION_USERDATA_KEY", "MAX_PASSES", "RulesEngine", "RuleTrigger"]

_log = get_logger(__name__)

#: Most evaluation passes per call (a ``var.set`` may make another rule true).
MAX_PASSES: Final[int] = 3
#: ``SessionContext.userdata`` key of a prompt agent's disposition (a flow's is ``FlowState.disposition``).
DISPOSITION_USERDATA_KEY: Final[str] = "lkap.disposition"
#: Most pending ``instruct`` notes kept for the next turn.
_MAX_PENDING_INSTRUCTIONS: Final[int] = 5

RuleTrigger = Literal["extraction", "tool", "variables", "turn"]

_VAR_RE: Final = re.compile(r"{{\s*var\.([a-z][a-z0-9_]{0,63})\s*}}")


class _Skipped(Exception):
    """An action that could not run (a missing block or item)."""


class RulesEngine:
    """Evaluates one session's rules (see the module docstring)."""

    def __init__(self, ctx: Any, rules: list[Rule]) -> None:
        """Parse the enabled rules once.

        Args:
            ctx: The session's ``SessionContext``.
            rules: ``AgentConfig.rules``.
        """
        self.ctx = ctx
        self._rules: list[tuple[Rule, Expr]] = []
        for rule in rules:
            if not rule.enabled:
                continue
            try:
                self._rules.append((rule, parse_condition(rule.when)))
            except ConditionError:
                _log.warning("rules.condition_unreadable", rule_id=rule.id)
        self._fired: set[str] = set()
        self._last: dict[str, bool] = {}
        self._instructions: list[str] = []
        self._lock = asyncio.Lock()

    @property
    def active(self) -> bool:
        """Whether any rule can still fire."""
        return any(not rule.once or rule.id not in self._fired for rule, _ in self._rules)

    def take_instructions(self) -> list[str]:
        """The pending ``instruct`` notes (cleared)."""
        pending, self._instructions = self._instructions, []
        return pending

    async def evaluate(self, trigger: RuleTrigger, tools: Mapping[str, bool] | None = None) -> list[str]:
        """Evaluate every rule and run the actions of those that fire.

        Args:
            trigger: What prompted the evaluation (for the event).
            tools: Each tool's latest outcome (``True`` = succeeded).

        Returns:
            The ids of the rules that fired, in order. Never raises.
        """
        fired: list[str] = []
        async with self._lock:
            for _ in range(MAX_PASSES):
                variables = dict(session_variables(self.ctx))
                changed = False
                for rule, expr in self._rules:
                    if rule.once and rule.id in self._fired:
                        continue
                    try:
                        truth = evaluate(expr, variables, tools or {})
                    except Exception:
                        _log.debug("rules.evaluate_failed", rule_id=rule.id, exc_info=True)
                        truth = False
                    previous = self._last.get(rule.id, False)
                    self._last[rule.id] = truth
                    if not truth or (not rule.once and previous):
                        continue
                    if rule.once:
                        self._fired.add(rule.id)
                    changed |= await self._fire(rule, trigger)
                    fired.append(rule.id)
                if not changed:
                    break
        return fired

    async def _fire(self, rule: Rule, trigger: RuleTrigger) -> bool:
        ran: list[str] = []
        skipped: list[str] = []
        changed = False
        for index, action in enumerate(rule.then):
            try:
                changed |= await self._run(rule, index, action)
                ran.append(action.do)
            except _Skipped:
                skipped.append(action.do)
            except Exception:
                _log.debug("rules.action_failed", rule_id=rule.id, action=action.do, exc_info=True)
                skipped.append(action.do)
        _log.info("rules.fired", rule_id=rule.id, actions=ran, skipped=skipped, trigger=trigger)
        event = RuleFiredEvent(
            rule_id=rule.id, label=rule.label, actions=ran, skipped=skipped, trigger=trigger
        )
        try:
            self.ctx.record_event(RULE_FIRED_EVENT, event.model_dump(mode="json"))
        except Exception:
            _log.debug("rules.event_failed", rule_id=rule.id, exc_info=True)
        return changed

    # ------------------------------------------------------------------ actions

    def _render(self, text: str) -> str:
        store = session_variables(self.ctx)

        def _sub(match: re.Match[str]) -> str:
            value = store.get(match.group(1))
            if value is None:
                return ""
            if isinstance(value, bool):
                return "yes" if value else "no"
            return str(value)

        return _VAR_RE.sub(_sub, text)[:500]

    def _block_types(self) -> dict[str, str]:
        from lkap_agent.ui.blocks import session_block_specs  # noqa: PLC0415 - avoids an import cycle

        panel = getattr(getattr(self.ctx, "config", None), "panel", None)
        specs = session_block_specs(self.ctx.ui, panel) if panel is not None else []
        return {spec.id: str(spec.type) for spec in specs}

    async def _run(self, rule: Rule, index: int, action: RuleAction) -> bool:
        """Run one action; ``True`` when it changed a variable."""
        ui = self.ctx.ui
        match action:
            case ChecklistSetItemAction():
                items: list[ChecklistItem] = list(ui.state.checklist)
                item = ChecklistItem(
                    id=action.id,
                    label=action.label,
                    done=action.done,
                    blocking=action.blocking,
                    hint=action.hint,
                )
                position = next((i for i, current in enumerate(items) if current.id == action.id), None)
                if position is None:
                    items.append(item)
                else:
                    items[position] = item
                await ui.set_checklist(items)
            case ChecklistCheckAction():
                items = list(ui.state.checklist)
                position = next((i for i, current in enumerate(items) if current.id == action.id), None)
                if position is None:
                    raise _Skipped
                items[position] = items[position].model_copy(update={"done": action.done})
                await ui.set_checklist(items)
            case StatusSetAction():
                await ui.set_status(action.label, action.tone)
            case DetailsSetAction():
                if self._block_types().get(action.block_id) != "details":
                    raise _Skipped
                from lkap_agent.tools.builtin.set_details import DetailIn, detail_item  # noqa: PLC0415

                state = ui.state.blocks.get(action.block_id) or {}
                rows = state.get("items") if isinstance(state, dict) else None
                existing = next(
                    (r for r in rows or [] if isinstance(r, dict) and r.get("key") == action.key), None
                )
                row = detail_item(
                    DetailIn(key=action.key, value=self._render(action.value), label=action.label or ""),
                    existing,
                    now=time.time(),
                )
                await ui.patch_block(
                    action.block_id, [UiPatchOp(op="upsert", path="/items", value=row, key=action.key)]
                )
            case NotePushAction():
                text = self._render(action.text)
                if not text.strip():
                    raise _Skipped
                if action.block_id is None:
                    await ui.add_note(text, "note", f"rule:{rule.id}:{index}")
                else:
                    if action.block_id not in self._block_types():
                        raise _Skipped
                    note = Note(
                        id=str(uuid.uuid4()), text=text, kind="note", ts=time.time(), block_id=action.block_id
                    )
                    await ui.patch([UiPatchOp(op="append", path="/notes", value=note)])
            case VarSetAction():
                store = session_variables(self.ctx)
                if store.get(action.name) == action.value:
                    return False
                store[action.name] = action.value
                userdata = getattr(self.ctx, "userdata", None)
                if isinstance(userdata, dict):
                    # The admin's own value now: no longer third-party or caller text.
                    for key in (BOUND_VARIABLES_USERDATA_KEY, EXTRACTED_VARIABLES_USERDATA_KEY):
                        names = userdata.get(key)
                        if isinstance(names, set):
                            names.discard(action.name)
                return True
            case EscalateAction():
                await self._escalate(rule, action)
            case InstructAction():
                self._instructions.append(action.text)
                del self._instructions[:-_MAX_PENDING_INSTRUCTIONS]
            case DispositionSetAction():
                userdata = getattr(self.ctx, "userdata", None)
                if not isinstance(userdata, dict):
                    raise _Skipped
                flow = userdata.get("flow")
                if isinstance(flow, FlowState):
                    flow.disposition = action.value
                else:
                    userdata[DISPOSITION_USERDATA_KEY] = action.value
        return False

    async def _escalate(self, rule: Rule, action: EscalateAction) -> None:
        from lkap_agent.tools.builtin.escalate_to_human import (  # noqa: PLC0415 - avoids an import cycle
            HANDOFF_REASONS,
            watch_for_human,
        )
        from lkap_agent.ui.blocks import set_handoff  # noqa: PLC0415

        ui = self.ctx.ui
        await ui.set_status("Escalated", "warning")
        payload = EscalationEvent(reason=action.reason, urgency=action.urgency, mode=action.mode).model_dump(
            mode="json"
        )
        if action.mode == "transfer":
            payload.pop("mode")  # the pre-V5-37 shape, as `escalate_to_human` records it
        try:
            self.ctx.record_event("escalation", payload)
        except Exception:
            _log.debug("rules.escalation_event_failed", rule_id=rule.id, exc_info=True)
        await ui.activity(
            ActivityEvent(
                id=f"rule-{rule.id}-{uuid.uuid4().hex[:8]}",
                ts=time.time(),
                source="escalate_to_human",
                label="Escalation",
                phase="done",
                headline=action.reason,
                urgent=action.urgency == "high",
                detail={"urgency": action.urgency, "mode": action.mode},
            )
        )
        try:
            await set_handoff(ui, self.ctx.config.panel, "requested", reason=HANDOFF_REASONS[action.mode])
        except Exception:
            _log.debug("rules.handoff_failed", rule_id=rule.id, exc_info=True)
        watch_for_human(self.ctx)
