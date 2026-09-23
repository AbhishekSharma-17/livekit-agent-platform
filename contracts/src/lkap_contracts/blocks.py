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
from lkap_contracts.ui_protocol import BlockSpec, BlockType

__all__ = [
    "BLOCK_CONFIG_MODELS",
    "CustomBlockConfig",
    "DocumentBlockConfig",
    "EmptyBlockConfig",
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
