"""Declarative rules: ``when`` a condition holds, ``then`` do a few fixed things (V6-13, D-V6-25).

``AgentConfig.rules`` (at most :data:`MAX_RULES`) is the generic form of the insurance
pack's ``rules.py``: required facts → checklist items, "if this, then that status /
escalation / note". A rule's ``when`` is the tiny grammar of :mod:`lkap_contracts.rules_expr`
(never ``eval``); its ``then`` is a list of :data:`RuleAction` s, each one of:

* ``checklist.set_item {id, label, done, blocking, hint}`` — add or replace one item of the
  panel's "still needed" checklist (other items are kept);
* ``checklist.check {id, done}`` — tick (or untick) an existing item;
* ``status.set {label, tone}`` — the status stamp;
* ``details.set {block_id, key, value, label}`` — one row of a ``details`` block;
* ``note.push {text, block_id}`` — a note (in the margin of ``block_id`` when given);
* ``var.set {name, value}`` — a session variable (the store tools and flows read);
* ``escalate {mode, reason, urgency}`` — what ``escalate_to_human`` does (status, the
  ``escalation`` event, the ``handoff`` block), with the rule's admin-written reason;
* ``instruct {text}`` — a one-turn system note for the model's next reply (the model still
  decides what to say);
* ``disposition.set {value}`` — the session's outcome label (a flow's ``disposition``).

``details.set`` values and ``note.push`` texts may name ``{{ var.<name> }}``; ``instruct``
text is sent as written.

When rules run (the worker's ``rules/engine.py``): after each extraction, after each batch
of tool results (bindings are already applied by then), and after a rule's ``var.set``
(at most three passes). ``once: true`` (the default) fires a rule at most once per session;
``once: false`` fires it each time its condition turns from false to true, never on every
pass while it stays true. Each firing records a ``rule_fired`` session event
(:class:`RuleFiredEvent`: the rule id and the kinds of its actions, never a value).

A ``matches`` pattern the regex-safety scanner refuses (S6-4) does not make a stored config
unloadable (V6-28, D-V6-31): the contract checks the grammar only, :func:`rule_issues` reports
the pattern as an error at ``rules[i].when`` (so a save is still refused), and the worker's
strict parse skips the rule, so it never fires.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Annotated, Any, Final, Literal

from pydantic import AfterValidator, BaseModel, Field, field_validator

from lkap_contracts.common import Issue
from lkap_contracts.flow import VARIABLE_NAME_PATTERN
from lkap_contracts.rules_expr import (
    MAX_CONDITION_CHARS,
    ConditionError,
    nested_repeat,
    parse_condition,
    referenced_patterns,
    referenced_tools,
    referenced_variables,
)
from lkap_contracts.tools import EscalationMode
from lkap_contracts.ui_protocol import BlockSpec, Tone

__all__ = [
    "MAX_RULES",
    "MAX_RULE_ACTIONS",
    "RULE_FIRED_EVENT",
    "RULE_ID_PATTERN",
    "RULE_MODELS",
    "ChecklistCheckAction",
    "ChecklistSetItemAction",
    "DetailsSetAction",
    "DispositionSetAction",
    "EscalateAction",
    "InstructAction",
    "NotePushAction",
    "Rule",
    "RuleAction",
    "RuleFiredEvent",
    "Rules",
    "StatusSetAction",
    "VarSetAction",
    "rule_issues",
    "slow_pattern_message",
]

#: Most rules on one agent.
MAX_RULES: Final[int] = 50
#: Most actions in one rule.
MAX_RULE_ACTIONS: Final[int] = 10
#: A rule id (and a ``checklist.*`` item id).
RULE_ID_PATTERN: Final[str] = r"^[A-Za-z0-9_-]{1,64}$"
_ITEM_ID_PATTERN: Final[str] = r"^[A-Za-z0-9_.:-]{1,64}$"
_BLOCK_ID_PATTERN: Final[str] = r"^[A-Za-z0-9_-]{1,64}$"
#: The session event each firing records.
RULE_FIRED_EVENT: Final[str] = "rule_fired"

_VAR_PLACEHOLDER_RE: Final = re.compile(r"{{\s*var\.([a-z][a-z0-9_]{0,63})\s*}}")


class ChecklistSetItemAction(BaseModel):
    """Add or replace one item of the "still needed" checklist (the others are kept)."""

    do: Literal["checklist.set_item"] = "checklist.set_item"
    id: str = Field(pattern=_ITEM_ID_PATTERN)
    label: str = Field(min_length=1, max_length=120)
    done: bool = False
    blocking: bool = False
    hint: str | None = Field(default=None, max_length=200)


class ChecklistCheckAction(BaseModel):
    """Tick (or, with ``done: false``, untick) an item that is already on the checklist."""

    do: Literal["checklist.check"] = "checklist.check"
    id: str = Field(pattern=_ITEM_ID_PATTERN)
    done: bool = True


class StatusSetAction(BaseModel):
    """Set the status stamp."""

    do: Literal["status.set"] = "status.set"
    label: str = Field(min_length=1, max_length=60)
    tone: Tone = "info"


class DetailsSetAction(BaseModel):
    """Write one row of a ``details`` block (``value`` may name ``{{ var.<name> }}``)."""

    do: Literal["details.set"] = "details.set"
    block_id: str = Field(pattern=_BLOCK_ID_PATTERN)
    key: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    value: str = Field(max_length=500)
    label: str | None = Field(default=None, max_length=80)


class NotePushAction(BaseModel):
    """Push a note (``text`` may name ``{{ var.<name> }}``); in ``block_id``'s margin when given."""

    do: Literal["note.push"] = "note.push"
    text: str = Field(min_length=1, max_length=500)
    block_id: str | None = Field(default=None, pattern=_BLOCK_ID_PATTERN)


class VarSetAction(BaseModel):
    """Set a session variable."""

    do: Literal["var.set"] = "var.set"
    name: str = Field(pattern=VARIABLE_NAME_PATTERN)
    value: str | int | float | bool | None = None

    @field_validator("value")
    @classmethod
    def _short(cls, value: Any) -> Any:
        if isinstance(value, str) and len(value) > 500:
            raise ValueError("a variable value is at most 500 characters")
        return value


class EscalateAction(BaseModel):
    """Flag the session for a person, as ``escalate_to_human`` does, with the rule's reason."""

    do: Literal["escalate"] = "escalate"
    mode: EscalationMode = "transfer"
    reason: str = Field(min_length=1, max_length=300)
    urgency: Literal["low", "normal", "high"] = "normal"


class InstructAction(BaseModel):
    """A system note for the model's next reply only (sent as written; no placeholders)."""

    do: Literal["instruct"] = "instruct"
    text: str = Field(min_length=1, max_length=500)


class DispositionSetAction(BaseModel):
    """Set the session's outcome label (a flow's ``disposition``)."""

    do: Literal["disposition.set"] = "disposition.set"
    value: str = Field(min_length=1, max_length=64)


RuleAction = Annotated[
    ChecklistSetItemAction
    | ChecklistCheckAction
    | StatusSetAction
    | DetailsSetAction
    | NotePushAction
    | VarSetAction
    | EscalateAction
    | InstructAction
    | DispositionSetAction,
    Field(discriminator="do"),
]


def _actions_bounded(value: list[Any]) -> list[Any]:
    # A validator rather than `max_length`: the TS generator turns a small `maxItems` into a
    # union of tuples no editor can assign an array to.
    if not value:
        raise ValueError("a rule needs at least one action")
    if len(value) > MAX_RULE_ACTIONS:
        raise ValueError(f"a rule has at most {MAX_RULE_ACTIONS} actions")
    return value


class Rule(BaseModel):
    """``when`` the condition holds, ``then`` run the actions (see the module docstring)."""

    id: str = Field(pattern=RULE_ID_PATTERN)
    label: str = Field(default="", max_length=120)
    """What the rule is for, in the admin's words (shown in the console and the timeline)."""
    when: str = Field(min_length=1, max_length=MAX_CONDITION_CHARS)
    """The condition (:mod:`lkap_contracts.rules_expr`), e.g. ``var.hazard matches /fire/i``."""
    then: Annotated[list[RuleAction], AfterValidator(_actions_bounded)]
    once: bool = True
    """``true``: at most once per session; ``false``: each time the condition turns true."""
    enabled: bool = True

    @field_validator("when")
    @classmethod
    def _parses(cls, value: str) -> str:
        # Grammar only (V6-28): a pattern the scanner refuses is an error of `rule_issues`,
        # so a stored config that has one still loads (the worker skips the rule).
        try:
            parse_condition(value, safe_patterns=False)
        except ConditionError as exc:
            raise ValueError(f"the condition does not read: {exc}") from exc
        return value


def _rules_bounded(value: list[Rule]) -> list[Rule]:
    if len(value) > MAX_RULES:
        raise ValueError(f"at most {MAX_RULES} rules")
    ids = [rule.id for rule in value]
    duplicates = sorted({rule_id for rule_id in ids if ids.count(rule_id) > 1})
    if duplicates:
        raise ValueError(f"rule ids must be unique: {', '.join(duplicates)}")
    return value


#: ``AgentConfig.rules``: at most :data:`MAX_RULES`, unique ids.
Rules = Annotated[list[Rule], AfterValidator(_rules_bounded)]


class RuleFiredEvent(BaseModel):
    """The ``rule_fired`` session event: which rule, and the kinds of what it did. Never a value."""

    rule_id: str
    label: str = ""
    actions: list[str] = []
    """The ``do`` of each action that ran, in order."""
    skipped: list[str] = []
    """The ``do`` of each action that could not run (a missing block, a closed panel)."""
    trigger: Literal["extraction", "tool", "variables", "turn"] = "extraction"


# --------------------------------------------------------------------------- validation


def _placeholder_names(text: str | None) -> set[str]:
    return set(_VAR_PLACEHOLDER_RE.findall(text or ""))


def slow_pattern_message(rule_id: str, pattern: str) -> str:
    """The error on a rule whose ``matches`` pattern the regex-safety scanner refuses (V6-28)."""
    return (
        f"rule '{rule_id}': the pattern /{pattern}/ can stall the call (it repeats a group that "
        "already repeats or holds alternatives, or two open-ended repeats can meet), so the rule "
        "never runs until it is fixed. Write it as a plain list of alternatives or with fixed "
        "counts, e.g. /fire|smoke/ instead of /(fire|smoke)+/"
    )


def rule_issues(
    rules: list[Rule],
    blocks: Iterable[BlockSpec],
    *,
    known_variables: Iterable[str] | None = None,
    known_tools: Iterable[str] | None = None,
) -> list[Issue]:
    """The semantic checks of an agent's rules (the contract already refused what cannot parse).

    * A ``matches`` pattern :func:`~lkap_contracts.rules_expr.nested_repeat` refuses → error
      at ``rules[i].when`` (V6-28: the contract reads the grammar only, so a stored config
      with one still loads; the worker skips the rule).
    * A ``details.set`` into a block that is not on the panel, or is not a ``details`` block;
      a ``note.push`` into a block that is not on the panel → error.
    * A ``checklist.*`` action on a panel without a ``checklist`` block → warning (nothing shows).
    * A ``var.<name>`` no extraction field, flow variable or ``var.set`` sets, when
      ``known_variables`` is given → warning (the condition can only be false).
    * A ``tool.<name>`` that is not one of ``known_tools``, when given → warning.
    * A ``checklist.check`` of an item no rule (or the "still needed" list) creates is fine:
      tools and bindings create items too.

    Args:
        rules: ``AgentConfig.rules``.
        blocks: ``AgentConfig.panel.blocks``.
        known_variables: Variable names something sets (extraction fields, flow variables);
            the ``var.set`` targets of these rules are added. ``None`` skips the check.
        known_tools: Tool names the agent can call. ``None`` skips the check.

    Returns:
        Issues at ``rules[i]…``.
    """
    block_types: Mapping[str, str] = {block.id: str(block.type) for block in blocks}
    variables: set[str] | None = None
    if known_variables is not None:
        variables = set(known_variables)
        for rule in rules:
            variables.update(action.name for action in rule.then if isinstance(action, VarSetAction))
    tools = set(known_tools) if known_tools is not None else None
    has_checklist = "checklist" in block_types.values()
    issues: list[Issue] = []
    for index, rule in enumerate(rules):
        base = f"rules[{index}]"
        try:
            expr = parse_condition(rule.when, safe_patterns=False)
        except ConditionError as exc:  # stored another way
            issues.append(Issue(path=f"{base}.when", message=f"the condition does not read: {exc}"))
            continue
        for pattern in referenced_patterns(expr):
            if nested_repeat(pattern):
                issues.append(Issue(path=f"{base}.when", message=slow_pattern_message(rule.id, pattern)))
        used = referenced_variables(expr)
        for action in rule.then:
            if isinstance(action, DetailsSetAction | NotePushAction):
                used |= _placeholder_names(
                    action.value if isinstance(action, DetailsSetAction) else action.text
                )
        if variables is not None:
            for name in sorted(used - variables):
                issues.append(
                    Issue(
                        path=f"{base}.when",
                        message=f"rule '{rule.id}' reads var.{name}, which no extraction field, "
                        "flow variable or rule sets",
                        severity="warning",
                    )
                )
        if tools is not None:
            for name in sorted(referenced_tools(expr) - tools):
                issues.append(
                    Issue(
                        path=f"{base}.when",
                        message=f"rule '{rule.id}' reads tool.{name}, but the agent has no tool "
                        f"named '{name}'",
                        severity="warning",
                    )
                )
        for position, action in enumerate(rule.then):
            path = f"{base}.then[{position}]"
            if isinstance(action, DetailsSetAction):
                kind = block_types.get(action.block_id)
                if kind is None:
                    issues.append(
                        Issue(
                            path=f"{path}.block_id",
                            message=f"rule '{rule.id}' writes block '{action.block_id}', which is not "
                            "on this agent's panel",
                        )
                    )
                elif kind != "details":
                    issues.append(
                        Issue(
                            path=f"{path}.block_id",
                            message=f"rule '{rule.id}' writes block '{action.block_id}' as a details "
                            f"block, but it is a {kind} block",
                        )
                    )
            elif isinstance(action, NotePushAction) and action.block_id is not None:
                if action.block_id not in block_types:
                    issues.append(
                        Issue(
                            path=f"{path}.block_id",
                            message=f"rule '{rule.id}' notes on block '{action.block_id}', which is not "
                            "on this agent's panel",
                        )
                    )
            elif isinstance(action, ChecklistSetItemAction | ChecklistCheckAction) and not has_checklist:
                issues.append(
                    Issue(
                        path=path,
                        message=f"rule '{rule.id}' changes the checklist, but the panel has no "
                        "checklist block to show it",
                        severity="warning",
                    )
                )
    return issues


#: The rule models, registered in ``export.py`` with one line (``**RULE_MODELS``).
RULE_MODELS: dict[str, type[BaseModel]] = {
    "Rule": Rule,
    "RuleFiredEvent": RuleFiredEvent,
    "ChecklistSetItemAction": ChecklistSetItemAction,
    "ChecklistCheckAction": ChecklistCheckAction,
    "StatusSetAction": StatusSetAction,
    "DetailsSetAction": DetailsSetAction,
    "NotePushAction": NotePushAction,
    "VarSetAction": VarSetAction,
    "EscalateAction": EscalateAction,
    "InstructAction": InstructAction,
    "DispositionSetAction": DispositionSetAction,
}
