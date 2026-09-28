"""Live structured extraction (V6-13, D-V6-24): the generic form of the insurance ``ClaimWorkflow``.

``AgentConfig.extraction`` names the facts the agent should capture from the conversation
(:class:`ExtractionField`: a flow ``VariableSpec`` plus a label, a hint for the extraction
model, a ``sensitive`` flag and where the value is shown). The worker extracts them **in the
background** with the session's ``workflow_llm`` (one prompt-for-JSON call, at most
:data:`EXTRACTION_BUDGET_S` seconds, never delaying the reply), keyed by a hash of the
transcript so unchanged text costs nothing, and writes the values into the session's
variables: the one store ``{{ var.* }}`` placeholders, ``requires_vars``, bindings, flows and
rules share. A ``null`` never overwrites a value; a flow node's own ``extract`` wins for the
names it lists (the live extraction leaves those names to the node).

When it runs (:data:`ExtractionTrigger`, one entry per kind):

* ``every_n_turns {n}`` — after every ``n``-th caller turn of at least ``min_turn_chars``
  characters (``n = 1``: every turn);
* ``tool {tools}`` — after one of these tools returns;
* ``node_exit {nodes}`` — when a flow leaves one of these steps (empty: any step);
* ``manual`` — the agent gets the ``extract_now`` tool and runs it when it needs the values.

This is the union of the plan's ``cadence`` (every turn, every n turns, manual) and the V6-13
brief's triggers (every n turns, tool, step exit); the default is every caller turn.

Where values show (:attr:`ExtractionField.show_in`): ``details:<block_id>`` (a row keyed by
the field name) or ``details:<block_id>.<key>``, or ``notebook:<block_id>.<section_id>`` (a
``details`` section: a row keyed by the field name; a ``text`` section: a note keyed by the field
name, "Label: value"). The required fields
not yet captured are listed as a "still needed" checklist when :attr:`ExtractionConfig.still_needed`
is ``checklist`` (items ``need_<field>``, ticked as values arrive; other items are kept).

Privacy: each run records an ``extraction`` session event (:class:`ExtractionEvent`) with
the field names and whether each is set; values only when the agent keeps sessions in
full (``privacy.storage_tier == "full"``) and never for a ``sensitive`` field.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Final, Literal

from pydantic import AfterValidator, BaseModel, Field, field_validator

from lkap_contracts.blocks import NotebookBlockConfig
from lkap_contracts.common import Issue
from lkap_contracts.flow import VariableSpec
from lkap_contracts.ui_protocol import BlockSpec

__all__ = [
    "EXTRACTION_BUDGET_S",
    "EXTRACTION_EVENT",
    "EXTRACTION_MODELS",
    "MAX_EXTRACTION_FIELDS",
    "STILL_NEEDED_ITEM_PREFIX",
    "EveryNTurnsTrigger",
    "ExtractionConfig",
    "ExtractionEvent",
    "ExtractionField",
    "ExtractionTrigger",
    "ManualTrigger",
    "NodeExitTrigger",
    "ShowInTarget",
    "ToolTrigger",
    "extraction_issues",
    "parse_show_in",
    "still_needed_item_id",
]

#: The session event each extraction run records.
EXTRACTION_EVENT: Final[str] = "extraction"
#: Wall-clock budget of one extraction call (first try and repair together).
EXTRACTION_BUDGET_S: Final[float] = 2.0
#: Most fields on one agent.
MAX_EXTRACTION_FIELDS: Final[int] = 30
#: The id prefix of the "still needed" checklist items the extraction owns.
STILL_NEEDED_ITEM_PREFIX: Final[str] = "need_"

_BLOCK_ID = r"[A-Za-z0-9_-]{1,64}"
_SHOW_IN_RES: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("details", re.compile(rf"^details:({_BLOCK_ID})(?:\.([A-Za-z0-9_-]{{1,64}}))?$")),
    ("notebook", re.compile(rf"^notebook:({_BLOCK_ID})\.([A-Za-z0-9_-]{{1,64}})$")),
)
#: The spellings of ``ExtractionField.show_in``.
SHOW_IN_FORMS: Final[tuple[str, ...]] = (
    "details:<block_id>",
    "details:<block_id>.<key>",
    "notebook:<block_id>.<section_id>",
)


@dataclass(frozen=True, slots=True)
class ShowInTarget:
    """A parsed ``show_in``."""

    kind: Literal["details", "notebook"]
    block_id: str
    key: str | None = None
    """``details``: the row key (``None``: the field name); ``notebook``: the section id."""


def parse_show_in(text: str) -> ShowInTarget:
    """Parse one ``show_in``.

    Raises:
        ValueError: ``text`` is none of :data:`SHOW_IN_FORMS`.
    """
    value = text.strip()
    for kind, pattern in _SHOW_IN_RES:
        match = pattern.match(value)
        if match is not None:
            return ShowInTarget(kind, match.group(1), match.group(2))  # type: ignore[arg-type]
    raise ValueError(f"'{text}' must be one of: {', '.join(SHOW_IN_FORMS)}")


def still_needed_item_id(name: str) -> str:
    """The checklist item id of a required field's "still needed" entry."""
    return f"{STILL_NEEDED_ITEM_PREFIX}{name}"


class ExtractionField(VariableSpec):
    """One fact to capture: a flow ``VariableSpec`` plus how to ask for it and where it shows."""

    label: str = Field(default="", max_length=80)
    """What the caller-facing panel and the checklist call it (empty: the name in words)."""
    hint: str = Field(default="", max_length=300)
    """Guidance for the extraction model (``"the 8-character policy number, like PX-12345"``)."""
    sensitive: bool = False
    """Never written into an event or a log line, whatever the storage tier."""
    show_in: str | None = Field(default=None, max_length=140)
    """Where the value shows: ``details:<block_id>[.<key>]`` or ``notebook:<block_id>.<section_id>``
    (a details or text section of a notebook block)."""

    @field_validator("show_in")
    @classmethod
    def _target(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        parse_show_in(value)
        return value.strip()


class EveryNTurnsTrigger(BaseModel):
    """After every ``n``-th caller turn (``n = 1``: every turn)."""

    kind: Literal["every_n_turns"] = "every_n_turns"
    n: int = Field(default=1, ge=1, le=20)


class ToolTrigger(BaseModel):
    """After one of these tools returns (built-in, attached or block tool names)."""

    kind: Literal["tool"] = "tool"
    tools: list[Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")]]

    @field_validator("tools")
    @classmethod
    def _bounded(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("name at least one tool")
        if len(value) > 20:
            raise ValueError("at most 20 tools")
        return list(dict.fromkeys(value))


class NodeExitTrigger(BaseModel):
    """When a flow leaves one of these steps (empty: any step)."""

    kind: Literal["node_exit"] = "node_exit"
    nodes: list[str] = []


class ManualTrigger(BaseModel):
    """The agent gets the ``extract_now`` tool and decides when to run it."""

    kind: Literal["manual"] = "manual"


ExtractionTrigger = Annotated[
    EveryNTurnsTrigger | ToolTrigger | NodeExitTrigger | ManualTrigger,
    Field(discriminator="kind"),
]


def _fields_bounded(value: list[ExtractionField]) -> list[ExtractionField]:
    # A validator rather than `max_length` (the TS generator's tuple-union issue).
    if len(value) > MAX_EXTRACTION_FIELDS:
        raise ValueError(f"at most {MAX_EXTRACTION_FIELDS} extraction fields")
    names = [field.name for field in value]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(f"extraction field names must be unique: {', '.join(duplicates)}")
    return value


def _triggers_bounded(value: list[Any]) -> list[Any]:
    kinds = [trigger.kind for trigger in value]
    duplicates = sorted({kind for kind in kinds if kinds.count(kind) > 1})
    if duplicates:
        raise ValueError(f"one trigger of each kind: {', '.join(duplicates)} is repeated")
    return value


def _default_triggers() -> list[ExtractionTrigger]:
    return [EveryNTurnsTrigger()]


class ExtractionConfig(BaseModel):
    """What the agent captures from the conversation while it talks (off by default)."""

    enabled: bool = False
    fields: Annotated[list[ExtractionField], AfterValidator(_fields_bounded)] = []
    triggers: Annotated[list[ExtractionTrigger], AfterValidator(_triggers_bounded)] = Field(
        default_factory=_default_triggers
    )
    """When extraction runs; the default is after every caller turn."""
    min_turn_chars: int = Field(default=12, ge=0, le=200)
    """A caller turn shorter than this ("yes", "okay") does not count towards ``every_n_turns``."""
    still_needed: Literal["checklist"] | None = None
    """``checklist``: list the required fields not captured yet on the panel's checklist."""


class ExtractionEvent(BaseModel):
    """The ``extraction`` session event: one run of the live extraction."""

    trigger: Literal["turn", "tool", "node_exit", "manual"]
    status: Literal["ok", "failed", "timeout"]
    duration_ms: int = 0
    fields: dict[str, bool] = {}
    """Each field's name and whether it is set after this run."""
    changed: list[str] = []
    """The fields this run set or changed."""
    still_needed: list[str] = []
    """The required fields still missing."""
    values: dict[str, Any] | None = None
    """The changed fields' values: only on the ``full`` storage tier, never a ``sensitive``
    field; dropped by the post-call scrub on the other tiers."""


# --------------------------------------------------------------------------- validation


def _notebook_section_problem(config: Any, section_id: str | None) -> str | None:
    """Why a notebook section cannot show an extracted value, or ``None``."""
    try:
        sections = NotebookBlockConfig.model_validate(config or {}).sections
    except ValueError:
        return None  # the panel validator reports the invalid config
    section = next((s for s in sections if s.id == section_id), None)
    if section is None:
        return f"it has no section '{section_id}'"
    if section.kind not in ("details", "text"):
        return f"section '{section_id}' is a {section.kind} section; use a details or text section"
    return None


def extraction_issues(
    config: ExtractionConfig,
    blocks: Iterable[BlockSpec],
    *,
    flow_node_ids: Iterable[str] | None = None,
    flow_extracted: Mapping[str, str] | None = None,
    known_tools: Iterable[str] | None = None,
    builtin_disabled: Iterable[str] = (),
) -> list[Issue]:
    """The semantic checks of ``AgentConfig.extraction`` (nothing when it is off and empty).

    * ``show_in`` into a block that is not on the panel or is not a ``details`` block → error;
      a ``notebook:`` target naming a section the notebook does not have, or a checklist or
      drawing section → error.
    * ``still_needed: checklist`` with no checklist block, or with no required field → warning.
    * On with no fields → warning.
    * ``node_exit`` on an agent without a flow, or naming a step that does not exist → warning.
    * A ``tool`` trigger naming a tool the agent does not have, when ``known_tools`` is given → warning.
    * ``manual`` while ``extract_now`` is switched off → warning.
    * A field a flow step also extracts → warning (the step's own extraction wins for it).

    Args:
        config: ``AgentConfig.extraction``.
        blocks: ``AgentConfig.panel.blocks``.
        flow_node_ids: The flow's node ids (``None``: not a flow agent).
        flow_extracted: Variable name → the id of a flow step whose ``extract`` lists it.
        known_tools: Tool names the agent can call (``None`` skips that check).
        builtin_disabled: ``AgentConfig.tools.builtin_disabled``.

    Returns:
        Issues at ``extraction…``.
    """
    if not config.enabled and not config.fields:
        return []
    blocks = list(blocks)
    block_types: Mapping[str, str] = {block.id: str(block.type) for block in blocks}
    block_configs = {block.id: block.config for block in blocks}
    issues: list[Issue] = []
    if config.enabled and not config.fields:
        issues.append(
            Issue(
                path="extraction.fields", message="extraction is on but names no fields", severity="warning"
            )
        )
    for index, field in enumerate(config.fields):
        path = f"extraction.fields[{index}]"
        if field.show_in:
            try:
                target = parse_show_in(field.show_in)
            except ValueError as exc:
                issues.append(Issue(path=f"{path}.show_in", message=str(exc)))
                target = None
            if target is not None:
                kind = block_types.get(target.block_id)
                if kind is None:
                    issues.append(
                        Issue(
                            path=f"{path}.show_in",
                            message=f"'{field.name}' shows in block '{target.block_id}', which is not "
                            "on this agent's panel",
                        )
                    )
                elif kind != target.kind:
                    issues.append(
                        Issue(
                            path=f"{path}.show_in",
                            message=f"'{field.name}' shows in block '{target.block_id}' as a {target.kind} "
                            f"block, but it is a {kind} block",
                        )
                    )
                elif target.kind == "notebook":
                    problem = _notebook_section_problem(block_configs.get(target.block_id), target.key)
                    if problem is not None:
                        issues.append(
                            Issue(
                                path=f"{path}.show_in",
                                message=f"'{field.name}' shows in notebook '{target.block_id}': {problem}",
                            )
                        )
        if flow_extracted and field.name in flow_extracted:
            issues.append(
                Issue(
                    path=f"{path}.name",
                    message=f"the flow step '{flow_extracted[field.name]}' also captures '{field.name}'; the "
                    "step's own capture wins and the live extraction leaves it alone",
                    severity="warning",
                )
            )
    if config.still_needed == "checklist":
        if "checklist" not in block_types.values():
            issues.append(
                Issue(
                    path="extraction.still_needed",
                    message="the still-needed list goes on the checklist, but the panel has no "
                    "checklist block",
                    severity="warning",
                )
            )
        if not any(field.required for field in config.fields):
            issues.append(
                Issue(
                    path="extraction.still_needed",
                    message="no field is required, so the still-needed list stays empty",
                    severity="warning",
                )
            )
    nodes = set(flow_node_ids) if flow_node_ids is not None else None
    tools = set(known_tools) if known_tools is not None else None
    for index, trigger in enumerate(config.triggers):
        path = f"extraction.triggers[{index}]"
        if isinstance(trigger, NodeExitTrigger):
            if nodes is None:
                issues.append(
                    Issue(
                        path=path,
                        message="'when a step ends' only applies to flow agents; this agent has no flow",
                        severity="warning",
                    )
                )
            else:
                for name in trigger.nodes:
                    if name not in nodes:
                        issues.append(
                            Issue(
                                path=f"{path}.nodes",
                                message=f"the flow has no step '{name}'",
                                severity="warning",
                            )
                        )
        elif isinstance(trigger, ToolTrigger) and tools is not None:
            for name in trigger.tools:
                if name not in tools:
                    issues.append(
                        Issue(
                            path=f"{path}.tools",
                            message=f"the agent has no tool named '{name}'",
                            severity="warning",
                        )
                    )
        elif isinstance(trigger, ManualTrigger) and "extract_now" in set(builtin_disabled):
            issues.append(
                Issue(
                    path=path,
                    message="extraction runs on request, but the extract_now tool is switched off",
                    severity="warning",
                )
            )
    return issues


#: The extraction models, registered in ``export.py`` with one line (``**EXTRACTION_MODELS``).
EXTRACTION_MODELS: dict[str, type[BaseModel]] = {
    "ExtractionConfig": ExtractionConfig,
    "ExtractionField": ExtractionField,
    "ExtractionEvent": ExtractionEvent,
    "EveryNTurnsTrigger": EveryNTurnsTrigger,
    "ToolTrigger": ToolTrigger,
    "NodeExitTrigger": NodeExitTrigger,
    "ManualTrigger": ManualTrigger,
}
