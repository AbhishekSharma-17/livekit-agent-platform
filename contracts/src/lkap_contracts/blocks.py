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
* the envelope blocks (``status``, ``notes``, ``activity``) and
  ``form``, ``gallery``, ``kb_citations`` → no keys;
* ``checklist`` → ``caller_can_edit`` (V6-06: the caller may tick items);
* ``choices`` → ``multi``, ``layout`` (``buttons`` / ``list`` / ``chips``),
  ``max_options`` (V5-08);
* ``details`` → ``columns`` (1 or 2) and ``fields: [{key, label, type}]``, the
  starting rows (seeded as ``items`` with no value), and ``caller_can_edit`` (V6-06:
  the caller may change a row's value);
* ``markdown`` → ``max_chars`` and ``allow_links``;
* ``steps`` → ``steps: [{id, label}]`` (seeded as pending), ``source``
  (``flow`` / ``manual``) and ``show_notes``;
* ``consent`` → ``kind`` (``recording`` / ``ai_disclosure`` / ``terms`` /
  ``custom``), ``text`` (empty uses the workspace's preset for ``recording``
  and ``ai_disclosure``), ``required``, ``decline_action`` (``continue`` /
  ``end_call``) and ``show_banner`` (V5-15);
* ``upload`` → ``accept`` (media types or ``image/*`` from :data:`UPLOAD_MIME_TYPES`),
  ``max_files``, ``max_bytes`` and ``camera_capture`` (V5-19);
* ``captions`` → ``show_user``, ``show_agent``, ``target_language`` (reserved for
  translated captions) and ``position`` (``block`` / ``bottom``) (V5-31);
* ``handoff`` → ``show_queue`` and ``show_agent_name`` (V5-32);
* ``link`` → ``allowed_hosts`` (required: the sites links may go to, ``example.com`` or
  ``*.example.com``), ``open_in`` (``new_tab`` / ``dialog``) and ``show_qr`` (V5-43);
* ``slots`` → ``timezone_mode`` (``caller`` / ``agent``), ``days_visible`` and
  ``allow_custom`` (V5-43);
* ``cards`` → ``layout`` (``carousel`` / ``grid`` / ``list``), ``selectable``,
  ``max_cards`` and ``image_hosts`` (the sites card pictures may come from) (V5-43);
* ``notebook`` → ``paper`` (``plain`` / ``ruled`` / ``grid`` / ``legal``), ``font``
  (``print`` / ``handwritten``: typed notes in a handwriting font are a theme, not ink),
  ``sections: [{id, title, kind}]`` (``text`` / ``checklist`` / ``details`` / ``ink``),
  ``caller_can_write`` (the caller may add and change notes, tick items and change
  values) and ``caller_can_draw`` (reserved for the drawing board, V6-12) (V6-08);
* ``layout`` → ``kind`` (``tabs`` / ``columns``), ``children: [{block_id, label}]``
  (other top-level blocks of the panel, shown inside this one) and ``columns`` (2 or 3)
  (V6-08, D-V6-18); :func:`layout_issues` checks the children across the panel;
* ``custom`` → ``kind`` (e.g. ``"flow_progress"``, R-V2-14) plus any
  pack-declared JSON, which is public too.

:func:`validate_block_config` turns a violation into addressable
:class:`~lkap_contracts.common.Issue` rows (``panel.blocks[i].config.<key>``);
the api registers it into ``config_service.VALIDATORS`` from ``lkap_api.panels``.
The schemas are exported as ``generated/schemas/BlockConfig_<type>.schema.json``.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from lkap_contracts.common import Issue
from lkap_contracts.compliance import MAX_CONSENT_TEXT_CHARS, ConsentDeclineAction, ConsentKind
from lkap_contracts.providers import LANGUAGE_CODE_PATTERN
from lkap_contracts.ui_protocol import (
    DEFAULT_UPLOAD_MAX_BYTES,
    MAX_ALLOWED_HOSTS,
    MAX_CARDS,
    MAX_NOTEBOOK_SECTIONS,
    MAX_UPLOAD_BYTES,
    MAX_UPLOAD_FILES,
    NOTEBOOK_ID_PATTERN,
    BlockSpec,
    BlockType,
    CaptionsPosition,
    DetailsValueType,
    NotebookSectionKind,
    normalize_hosts,
)

__all__ = [
    "BLOCK_CONFIG_MODELS",
    "DEFAULT_UPLOAD_ACCEPT",
    "MAX_LAYOUT_CHILDREN",
    "UPLOAD_EXTENSIONS",
    "UPLOAD_MIME_TYPES",
    "CaptionsBlockConfig",
    "CardsBlockConfig",
    "ChecklistBlockConfig",
    "ChoicesBlockConfig",
    "ConsentBlockConfig",
    "CustomBlockConfig",
    "DetailsBlockConfig",
    "DetailsFieldConfig",
    "DocumentBlockConfig",
    "EmptyBlockConfig",
    "HandoffBlockConfig",
    "LayoutBlockConfig",
    "LayoutChildConfig",
    "LinkBlockConfig",
    "MarkdownBlockConfig",
    "NotebookBlockConfig",
    "NotebookFont",
    "NotebookPaper",
    "NotebookSectionConfig",
    "SlotsBlockConfig",
    "StepConfig",
    "StepsBlockConfig",
    "TableBlockConfig",
    "TableColumnConfig",
    "TranscriptBlockConfig",
    "UploadBlockConfig",
    "VideoBlockConfig",
    "accept_allows",
    "block_config_schema_name",
    "layout_issues",
    "safe_filename",
    "sniff_mime",
    "validate_accept",
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
    """``details``: one or two columns, the rows the card starts with, and whether the caller may edit.

    ``caller_can_edit`` (V6-06) lets the caller change the value of a row on screen; the
    agent is told about each change (as data, never as instructions).
    """

    columns: int = Field(default=1, ge=1, le=2)
    fields: list[DetailsFieldConfig] = []
    caller_can_edit: bool = False

    @field_validator("fields")
    @classmethod
    def _unique_keys(cls, value: list[DetailsFieldConfig]) -> list[DetailsFieldConfig]:
        keys = [f.key for f in value]
        if len(set(keys)) != len(keys):
            raise ValueError("field keys must be unique")
        return value


class ChecklistBlockConfig(_StrictConfig):
    """``checklist``: whether the caller may tick items on screen (V6-06).

    The items themselves live in the envelope (``UiState.checklist``), written by
    ``set_checklist`` / ``check_item`` or the pack; the agent is told about each tick.
    """

    caller_can_edit: bool = False


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


#: Every media type a caller may send (V5-19): photos and PDFs. Never HTML, SVG or
#: anything a browser would execute; the worker and the api both sniff the bytes
#: (:func:`sniff_mime`) and refuse a file whose content is not one of these.
UPLOAD_MIME_TYPES: Final[tuple[str, ...]] = (
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "image/heic",
    "image/heif",
    "application/pdf",
)
#: ``accept`` of an upload block (or a form file field) that sets none.
DEFAULT_UPLOAD_ACCEPT: Final[tuple[str, ...]] = ("image/*", "application/pdf")
#: The file extension a stored asset of each media type gets (never the caller's own).
UPLOAD_EXTENSIONS: Final[dict[str, str]] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/heic": ".heic",
    "image/heif": ".heif",
    "application/pdf": ".pdf",
    "text/markdown": ".md",
    "text/plain": ".txt",
}

_HEIC_BRANDS: Final[frozenset[bytes]] = frozenset({b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis"})
_HEIF_BRANDS: Final[frozenset[bytes]] = frozenset({b"mif1", b"msf1", b"heif"})
_FILENAME_MAX_CHARS: Final[int] = 120
_UNSAFE_FILENAME_CHARS: Final[re.Pattern[str]] = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]+')


def sniff_mime(data: bytes) -> str | None:
    """The media type ``data`` really is, from its magic bytes; ``None`` when it is not allowed.

    Only :data:`UPLOAD_MIME_TYPES` are recognised: JPEG, PNG, GIF, WebP,
    HEIC/HEIF (an ISO-BMFF ``ftyp`` box with an image brand) and PDF (``%PDF-``
    at offset 0). Everything else, including HTML, SVG, scripts and archives,
    is ``None``. The declared type of an upload is never trusted.
    """
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        brand = data[8:12]
        if brand in _HEIC_BRANDS:
            return "image/heic"
        if brand in _HEIF_BRANDS:
            return "image/heif"
        return None
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    return None


def validate_accept(values: Iterable[str]) -> list[str]:
    """Normalise an ``accept`` list, refusing any entry outside :data:`UPLOAD_MIME_TYPES`.

    Entries are lower-cased and de-duplicated; ``image/*`` stands for every image type.

    Raises:
        ValueError: For an entry that is not ``image/*`` or an allowed media type.
    """
    seen: list[str] = []
    for raw in values:
        value = raw.strip().lower()
        if value != "image/*" and value not in UPLOAD_MIME_TYPES:
            raise ValueError(
                f"{raw!r} is not a file type callers may send; use image/* or one of "
                + ", ".join(UPLOAD_MIME_TYPES)
            )
        if value not in seen:
            seen.append(value)
    return seen


def accept_allows(accept: Iterable[str], mime: str) -> bool:
    """Whether ``mime`` (a sniffed type) is allowed by ``accept`` (empty = :data:`DEFAULT_UPLOAD_ACCEPT`)."""
    if mime not in UPLOAD_MIME_TYPES:
        return False
    entries = [a.strip().lower() for a in accept] or list(DEFAULT_UPLOAD_ACCEPT)
    return any(entry == mime or (entry == "image/*" and mime.startswith("image/")) for entry in entries)


def safe_filename(name: str | None, *, fallback: str = "file") -> str:
    """A caller's filename reduced to a harmless display name.

    Directory parts are dropped (``../../x``, ``C:\\x``), control and
    path-reserved characters removed, Unicode normalised (NFKC) and the result
    capped at 120 characters keeping the extension. It is only ever shown and
    sent as a download name, never used in a storage key. A name that ends up
    empty or dots-only becomes ``fallback``.
    """
    text = unicodedata.normalize("NFKC", name or "").replace("\\", "/")
    text = text.rsplit("/", 1)[-1]
    text = _UNSAFE_FILENAME_CHARS.sub("", text).strip().strip(".").strip()
    if not text:
        return fallback
    if len(text) > _FILENAME_MAX_CHARS:
        stem, dot, ext = text.rpartition(".")
        if dot and 0 < len(ext) <= 10:
            text = stem[: _FILENAME_MAX_CHARS - len(ext) - 1] + "." + ext
        else:
            text = text[:_FILENAME_MAX_CHARS]
    return text


class UploadBlockConfig(_StrictConfig):
    """``upload``: what the caller may send, how many files and how large (V5-19).

    ``accept`` lists ``image/*`` or exact types from :data:`UPLOAD_MIME_TYPES`
    (photos and PDFs; never HTML or SVG). ``camera_capture`` asks a phone to
    open its camera. The worker enforces every limit before storing a file and
    the api checks them again.
    """

    accept: list[str] = Field(default=list(DEFAULT_UPLOAD_ACCEPT))
    max_files: int = Field(default=3, ge=1, le=MAX_UPLOAD_FILES)
    max_bytes: int = Field(default=DEFAULT_UPLOAD_MAX_BYTES, ge=1, le=MAX_UPLOAD_BYTES)
    camera_capture: bool = False

    @field_validator("accept")
    @classmethod
    def _allowed_types(cls, value: list[str]) -> list[str]:
        normalised = validate_accept(value)
        if not normalised:
            raise ValueError("accept at least one file type")
        return normalised


class CaptionsBlockConfig(_StrictConfig):
    """``captions``: live captions of both sides (V5-31).

    The worker streams each utterance on ``lkap.captions`` (``CaptionSegment``) while a
    ``captions`` block is on the panel: the caller's side when any such block has
    ``show_user``, the agent's when any has ``show_agent``. ``position="bottom"``
    overlays the captions on the video of avatar layouts. ``target_language`` is kept
    for translated captions, which a later package adds; the worker ignores it today.
    """

    show_user: bool = True
    show_agent: bool = True
    target_language: str | None = Field(default=None, pattern=LANGUAGE_CODE_PATTERN)
    position: CaptionsPosition = "block"


class HandoffBlockConfig(_StrictConfig):
    """``handoff``: where the hand-off of the caller to a person stands (V5-32).

    The worker writes ``HandoffBlockState`` as ``transfer_call`` runs
    (``requested → connecting → connected | timeout | ended``). ``show_queue`` shows the
    caller's place in a queue when one is known; ``show_agent_name`` shows the person's
    name once they are on the call.
    """

    show_queue: bool = True
    show_agent_name: bool = True


class LinkBlockConfig(_StrictConfig):
    """``link``: where links may go, how they open, and whether a QR code shows (V5-43).

    ``allowed_hosts`` is required: ``send_link`` refuses any link whose site is not
    listed (``example.com``, or ``*.example.com`` for its sub-domains), and only
    ``https://`` links are ever shown. ``open_in="dialog"`` opens the link in a
    dialog over the call instead of a new tab; ``show_qr`` shows a QR code so a
    caller on a computer can finish on their phone.
    """

    allowed_hosts: list[str] = Field(default=[], max_length=MAX_ALLOWED_HOSTS, validate_default=True)
    open_in: Literal["new_tab", "dialog"] = "new_tab"
    show_qr: bool = True

    @field_validator("allowed_hosts")
    @classmethod
    def _sites(cls, value: list[str]) -> list[str]:
        hosts = normalize_hosts(value)
        if not hosts:
            raise ValueError("add at least one site links may go to, e.g. example.com")
        return hosts


class SlotsBlockConfig(_StrictConfig):
    """``slots``: which timezone the slots show in, how many days, and free times (V5-43).

    ``timezone_mode="caller"`` shows the slots in the caller's own timezone,
    ``"agent"`` in the business timezone. ``allow_custom`` lets the caller ask
    for a time that is not offered: the agent confirms it out loud with
    ``resolve_slot``.
    """

    timezone_mode: Literal["caller", "agent"] = "caller"
    days_visible: int = Field(default=7, ge=1, le=31)
    allow_custom: bool = False


class CardsBlockConfig(_StrictConfig):
    """``cards``: how the cards are laid out, whether one can be picked, how many (V5-43).

    ``image_hosts`` lists the sites a card's ``image_url`` may come from (empty:
    only pictures already delivered in the session, by ``image_asset_id``).
    """

    layout: Literal["carousel", "grid", "list"] = "carousel"
    selectable: bool = True
    max_cards: int = Field(default=10, ge=1, le=MAX_CARDS)
    image_hosts: list[str] = Field(default=[], max_length=MAX_ALLOWED_HOSTS)

    @field_validator("image_hosts")
    @classmethod
    def _sites(cls, value: list[str]) -> list[str]:
        return normalize_hosts(value)


#: The paper a ``notebook`` is drawn on (V6-08).
NotebookPaper = Literal["plain", "ruled", "grid", "legal"]
#: How typed notes look: ``handwritten`` is a handwriting font on typed text (a theme); real
#: handwriting is ink (an ``ink`` section, V6-12).
NotebookFont = Literal["print", "handwritten"]


class NotebookSectionConfig(_StrictConfig):
    """One section of a ``notebook``: its id (what the tools name), heading and kind (V6-08)."""

    id: str = Field(pattern=NOTEBOOK_ID_PATTERN)
    title: str = Field(default="", max_length=80)
    kind: NotebookSectionKind = "text"


class NotebookBlockConfig(_StrictConfig):
    """``notebook``: a sectioned notebook the agent writes in, and the caller may too (V6-08, D-V6-15).

    ``sections`` lists what the notebook holds, in order: ``text`` notes
    (``notebook_write``), a ``checklist`` (``notebook_write`` then ``notebook_check``), a
    ``details`` card (``notebook_write`` with fields) or an ``ink`` drawing board (the
    ``canvas`` block of V6-12; until then it shows "Drawing board coming soon").
    ``caller_can_write`` lets the caller add and change notes, tick items and change
    values on screen; the agent is told about each change (as data, never as
    instructions). ``caller_can_draw`` is reserved for the drawing board (V6-12).
    """

    paper: NotebookPaper = "ruled"
    font: NotebookFont = "print"
    sections: list[NotebookSectionConfig] = Field(
        default=[NotebookSectionConfig(id="notes", title="Notes")],
        min_length=1,
        max_length=MAX_NOTEBOOK_SECTIONS,
    )
    caller_can_write: bool = False
    caller_can_draw: bool = False

    @field_validator("sections")
    @classmethod
    def _unique_ids(cls, value: list[NotebookSectionConfig]) -> list[NotebookSectionConfig]:
        ids = [section.id for section in value]
        if len(set(ids)) != len(ids):
            raise ValueError("section ids must be unique")
        return value


#: The most blocks one ``layout`` block holds.
MAX_LAYOUT_CHILDREN: Final[int] = 12


class LayoutChildConfig(_StrictConfig):
    """One block a ``layout`` shows inside it: the block's id and, for tabs, the tab's label."""

    block_id: str = Field(min_length=1, max_length=64)
    label: str | None = Field(default=None, max_length=40)


class LayoutBlockConfig(_StrictConfig):
    """``layout``: shows other blocks of the panel as tabs or side by side (V6-08, D-V6-18).

    A flat container: the children stay ordinary blocks of the panel (their state,
    tools and ``describe_panel`` entries are unchanged); the console renders a claimed
    child inside the layout instead of in the panel's flow. A child must exist, be
    claimed by one layout only, and never be a layout itself (:func:`layout_issues`).
    ``columns`` (2 or 3) is used with ``kind="columns"``.
    """

    kind: Literal["tabs", "columns"] = "tabs"
    children: list[LayoutChildConfig] = Field(default=[], max_length=MAX_LAYOUT_CHILDREN)
    columns: Literal[2, 3] = 2


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
    "checklist": ChecklistBlockConfig,
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
    "upload": UploadBlockConfig,
    "captions": CaptionsBlockConfig,
    "handoff": HandoffBlockConfig,
    "link": LinkBlockConfig,
    "slots": SlotsBlockConfig,
    "cards": CardsBlockConfig,
    "notebook": NotebookBlockConfig,
    "layout": LayoutBlockConfig,
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


def layout_issues(blocks: Sequence[BlockSpec], *, path: str = "panel.blocks") -> list[Issue]:
    """Check what every ``layout`` block of a panel claims (V6-08, D-V6-18).

    A layout's child must be another block of the same panel, never the layout itself
    and never another layout, and each block may be claimed by one layout only (so a
    child cannot be hidden in two places, or nowhere). A layout whose config does not
    validate is skipped here (:func:`validate_block_config` reports it). A layout that
    holds nothing gets a warning.

    Args:
        blocks: ``PanelLayout.blocks``.
        path: Where the list sits in the validated document.

    Returns:
        One ``error`` per bad child at ``<path>[i].config.children[j].block_id``, and a
        ``warning`` at ``<path>[i].config.children`` for an empty layout.
    """
    types = {spec.id: spec.type for spec in blocks}
    claimed: dict[str, str] = {}
    issues: list[Issue] = []
    for i, spec in enumerate(blocks):
        if spec.type != "layout":
            continue
        try:
            config = LayoutBlockConfig.model_validate(spec.config)
        except ValidationError:
            continue
        base = f"{path}[{i}].config.children"
        if not config.children:
            issues.append(
                Issue(
                    path=base,
                    message="this layout holds no blocks yet; add the blocks it shows",
                    severity="warning",
                )
            )
        for j, child in enumerate(config.children):
            where = f"{base}[{j}].block_id"
            target = child.block_id
            if target == spec.id:
                message = "a layout cannot hold itself"
            elif target not in types:
                message = f"there is no block {target!r} on this panel"
            elif types[target] == "layout":
                message = f"{target!r} is a layout; a layout cannot hold another layout"
            elif target in claimed:
                owner = claimed[target]
                message = (
                    f"{target!r} is already shown in {owner!r}"
                    if owner != spec.id
                    else f"{target!r} is listed twice in this layout"
                )
            else:
                claimed[target] = spec.id
                continue
            issues.append(Issue(path=where, message=message))
    return issues
