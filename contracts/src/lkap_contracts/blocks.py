"""Per-block-type ``BlockSpec.config`` schemas (ruling R-V2-17, CONTRACTS-V2 §4.4).

``BlockSpec.config`` is public: it travels to every browser on
``AgentPublicOut.panel`` (R-V2-7). So a block's config may only carry the keys
its type declares here, and every model but ``custom`` forbids anything else.

The keys are exactly the ones the worker honours (``lkap_agent.ui.blocks.
initial_block_state`` seeds a block's state from every config key that names a
state field) and the ones the console's panel composer edits
(``web/src/panels/blocks/catalog.ts::configFields``, kept equal to these
schemas by a vitest parity test):

* ``table`` → ``columns: [{key, label, type}]``;
* ``document`` → ``url`` (an ``https://`` link; empty means none) and ``page`` (≥ 1);
* ``transcript`` → ``show_tools``;
* ``video`` → ``source`` (``agent_avatar`` / ``user_camera`` / ``user_screen`` /
  ``track:<sid>``) and ``muted``;
* the envelope blocks (``status``, ``notes``, ``checklist``, ``activity``) and
  ``form``, ``gallery``, ``kb_citations`` → no keys;
* ``choices`` → ``multi``, ``layout`` (``buttons`` / ``list`` / ``chips``),
  ``max_options`` (V5-08);
* ``details`` → ``columns`` (1 or 2) and ``fields: [{key, label, type}]``, the
  starting rows (seeded as ``items`` with no value);
* ``markdown`` → ``max_chars`` and ``allow_links``;
* ``steps`` → ``steps: [{id, label}]`` (seeded as pending), ``source``
  (``flow`` / ``manual``) and ``show_notes``;
* ``consent`` → ``kind`` (``recording`` / ``ai_disclosure`` / ``terms`` /
  ``custom``), ``text`` (empty uses the workspace's preset for ``recording``
  and ``ai_disclosure``), ``required``, ``decline_action`` (``continue`` /
  ``end_call``) and ``show_banner`` (V5-15);
* ``custom`` → ``kind`` (e.g. ``"flow_progress"``, R-V2-14) plus any
  pack-declared JSON, which is public too.

:func:`validate_block_config` turns a violation into addressable
:class:`~lkap_contracts.common.Issue` rows (``panel.blocks[i].config.<key>``);
the api registers it into ``config_service.VALIDATORS`` from ``lkap_api.panels``.
The schemas are exported as ``generated/schemas/BlockConfig_<type>.schema.json``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from lkap_contracts.common import Issue
from lkap_contracts.compliance import MAX_CONSENT_TEXT_CHARS, ConsentDeclineAction, ConsentKind
from lkap_contracts.ui_protocol import BlockSpec, BlockType, DetailsValueType

__all__ = [
    "BLOCK_CONFIG_MODELS",
    "ChoicesBlockConfig",
    "ConsentBlockConfig",
    "CustomBlockConfig",
    "DetailsBlockConfig",
    "DetailsFieldConfig",
    "DocumentBlockConfig",
    "EmptyBlockConfig",
    "MarkdownBlockConfig",
    "StepConfig",
    "StepsBlockConfig",
    "TableBlockConfig",
    "TableColumnConfig",
    "TranscriptBlockConfig",
    "VideoBlockConfig",
    "block_config_schema_name",
    "validate_block_config",
    "validate_panel_block_configs",
]

#: ``VideoBlockState.source`` values a config may start a video block on.
VIDEO_SOURCE_PATTERN: Final[str] = r"^(agent_avatar|user_camera|user_screen|track:.+)$"


class _StrictConfig(BaseModel):
    """Base of every block config: unknown keys are errors (R-V2-17)."""

    model_config = ConfigDict(extra="forbid")


class EmptyBlockConfig(_StrictConfig):
    """A block type that takes no configuration at all."""


class TableColumnConfig(_StrictConfig):
    """One starting column of a table block (``TableColumn``, strictly)."""

    key: str = Field(min_length=1)
    label: str
    type: Literal["string", "number", "boolean", "date"] = "string"


class TableBlockConfig(_StrictConfig):
    """``table``: the columns the table starts with (the agent adds more)."""

    columns: list[TableColumnConfig] = []


class DocumentBlockConfig(_StrictConfig):
    """``document``: an optional starting document and page."""

    url: str | None = None
    page: int = Field(default=1, ge=1)

    @field_validator("url")
    @classmethod
    def _https_only(cls, value: str | None) -> str | None:
        """Empty means "no starting document"; anything else must be ``https://``."""
        if value and not value.startswith("https://"):
            raise ValueError("must be an https:// link")
        return value


class TranscriptBlockConfig(_StrictConfig):
    """``transcript``: whether tool calls are interleaved with the turns."""

    show_tools: bool = False


class VideoBlockConfig(_StrictConfig):
    """``video``: which track the block starts on, and whether it starts muted."""

    source: str = Field(default="agent_avatar", pattern=VIDEO_SOURCE_PATTERN)
    muted: bool = False


class ChoicesBlockConfig(_StrictConfig):
    """``choices``: single or multiple answers, how the options are laid out, how many at most."""

    multi: bool = False
    layout: Literal["buttons", "list", "chips"] = "buttons"
    max_options: int = Field(default=8, ge=2, le=20)


class DetailsFieldConfig(_StrictConfig):
    """One starting row of a ``details`` card (the agent fills the value and may add rows)."""

    key: str = Field(min_length=1)
    label: str
    type: DetailsValueType = "string"


class DetailsBlockConfig(_StrictConfig):
    """``details``: one or two columns, and the rows the card starts with."""

    columns: int = Field(default=1, ge=1, le=2)
    fields: list[DetailsFieldConfig] = []

    @field_validator("fields")
    @classmethod
    def _unique_keys(cls, value: list[DetailsFieldConfig]) -> list[DetailsFieldConfig]:
        keys = [f.key for f in value]
        if len(set(keys)) != len(keys):
            raise ValueError("field keys must be unique")
        return value


class MarkdownBlockConfig(_StrictConfig):
    """``markdown``: the longest text the agent may show, and whether links are clickable."""

    max_chars: int = Field(default=8000, ge=200, le=50000)
    allow_links: bool = False


class StepConfig(_StrictConfig):
    """One starting step of a ``steps`` block (with ``source="flow"``, ``id`` names a flow node)."""

    id: str = Field(min_length=1)
    label: str


class StepsBlockConfig(_StrictConfig):
    """``steps``: the starting steps, who drives them, and whether notes show under a step.

    ``source="flow"`` follows the agent's flow (the worker writes it on every
    step change and registers no tool); ``"manual"`` is driven by ``set_steps``.
    """

    steps: list[StepConfig] = []
    source: Literal["flow", "manual"] = "manual"
    show_notes: bool = True

    @field_validator("steps")
    @classmethod
    def _unique_ids(cls, value: list[StepConfig]) -> list[StepConfig]:
        ids = [s.id for s in value]
        if len(set(ids)) != len(ids):
            raise ValueError("step ids must be unique")
        return value


class ConsentBlockConfig(_StrictConfig):
    """``consent``: what the caller is asked to accept, and what a decline does (V5-15).

    The text is public by design (it must be auditable). Left empty, a
    ``recording`` block asks the workspace's recording question and an
    ``ai_disclosure`` block shows its disclosure line (Settings → Compliance);
    a ``terms`` or ``custom`` block needs its own text (the api validator says
    so). ``decline_action="end_call"`` ends the call after a polite goodbye
    when a ``required`` consent is declined. ``show_banner`` keeps the "you're
    talking to an AI assistant" banner on screen for the whole session.
    """

    kind: ConsentKind = "recording"
    text: str = Field(default="", max_length=MAX_CONSENT_TEXT_CHARS)
    required: bool = True
    decline_action: ConsentDeclineAction = "continue"
    show_banner: bool = True


class CustomBlockConfig(BaseModel):
    """``custom``: a pack-rendered block — ``kind`` plus any pack-declared JSON (public).

    ``kind`` names what the block shows (``"flow_progress"`` mirrors the flow
    state, R-V2-14). It is optional so a block added from the composer, which
    has no field for it yet, still saves.
    """

    model_config = ConfigDict(extra="allow")

    kind: str | None = None


#: The config model of every block type (R-V2-17).
BLOCK_CONFIG_MODELS: Final[dict[BlockType, type[BaseModel]]] = {
    "status": EmptyBlockConfig,
    "notes": EmptyBlockConfig,
    "checklist": EmptyBlockConfig,
    "activity": EmptyBlockConfig,
    "form": EmptyBlockConfig,
    "gallery": EmptyBlockConfig,
    "kb_citations": EmptyBlockConfig,
    "table": TableBlockConfig,
    "document": DocumentBlockConfig,
    "transcript": TranscriptBlockConfig,
    "video": VideoBlockConfig,
    "custom": CustomBlockConfig,
    "choices": ChoicesBlockConfig,
    "details": DetailsBlockConfig,
    "markdown": MarkdownBlockConfig,
    "steps": StepsBlockConfig,
    "consent": ConsentBlockConfig,
}


def block_config_schema_name(block_type: str) -> str:
    """The exported schema name of a block type's config (``BlockConfig_<type>``)."""
    return f"BlockConfig_{block_type}"


def _loc_path(base: str, loc: Sequence[int | str]) -> str:
    path = base
    for part in loc:
        path += f"[{part}]" if isinstance(part, int) else f".{part}"
    return path


def validate_block_config(spec: BlockSpec, *, path: str = "config") -> list[Issue]:
    """Check one block's ``config`` against its type's schema.

    Args:
        spec: The block.
        path: Where ``spec.config`` sits in the validated document, e.g.
            ``"panel.blocks[0].config"``.

    Returns:
        One ``error`` per violation, addressed at ``<path>.<key>`` (or ``<path>``
        itself for a problem with the object as a whole). Empty when valid.
    """
    model = BLOCK_CONFIG_MODELS.get(spec.type)
    if model is None:  # pragma: no cover - BlockSpec.type is a closed Literal
        return []
    config: Any = spec.config
    try:
        model.model_validate(config)
    except ValidationError as exc:
        issues: list[Issue] = []
        for error in exc.errors():
            loc = [p for p in error["loc"] if isinstance(p, int | str)]
            if error["type"] == "extra_forbidden":
                message = f"unknown config key for a {spec.type} block"
            else:
                message = str(error["msg"])
            issues.append(Issue(path=_loc_path(path, loc), message=message))
        return issues
    return []


def validate_panel_block_configs(blocks: Sequence[BlockSpec], *, path: str = "panel.blocks") -> list[Issue]:
    """Check every block of a panel layout (see :func:`validate_block_config`).

    Args:
        blocks: ``PanelLayout.blocks``.
        path: Where the list sits in the validated document.

    Returns:
        Every block's issues, in block order.
    """
    issues: list[Issue] = []
    for i, spec in enumerate(blocks):
        issues.extend(validate_block_config(spec, path=f"{path}[{i}].config"))
    return issues
