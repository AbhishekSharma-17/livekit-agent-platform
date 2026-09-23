"""Agent to UI protocol: the state envelope, patch messages, activity and RPC payloads.

The agent owns a :class:`UiState` envelope per session. It publishes a
:class:`UiSnapshot` on session start (``seq == 1``) and a :class:`UiPatch` for every
subsequent change. The browser applies patches strictly in ``seq`` order and asks
for a fresh snapshot over RPC when it sees a gap.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

TOPIC_UI_STATE = "lkap.ui.state"
TOPIC_UI_ACTIVITY = "lkap.ui.activity"
TOPIC_UI_ASSET = "lkap.ui.asset"
RPC_UI_REQUEST = "lkap.ui.request"
RPC_AGENT_ACTION = "lkap.agent.action"


#: Canonical mapping of topic constant name to wire value (exported to JSON/TS).
TOPICS: dict[str, str] = {
    "TOPIC_UI_STATE": TOPIC_UI_STATE,
    "TOPIC_UI_ACTIVITY": TOPIC_UI_ACTIVITY,
    "TOPIC_UI_ASSET": TOPIC_UI_ASSET,
    "RPC_UI_REQUEST": RPC_UI_REQUEST,
    "RPC_AGENT_ACTION": RPC_AGENT_ACTION,
}

#: Number of patches after which the agent re-sends a full snapshot.
SNAPSHOT_EVERY_N_PATCHES = 50
#: Maximum number of activity events retained in the state envelope.
ACTIVITY_RING_SIZE = 30

Tone = Literal["neutral", "info", "success", "warning", "danger"]


class StatusStamp(BaseModel):
    """The big "stamp" shown by a panel; ``key`` changes re-trigger the animation."""

    label: str
    tone: Tone = "neutral"
    key: str | None = None


class Note(BaseModel):
    """A line in the panel's notes list. A repeated ``key`` upserts in place."""

    id: str
    text: str
    kind: str = "note"
    tone: Tone = "neutral"
    ts: float
    key: str | None = None


class ChecklistItem(BaseModel):
    """One "still needed" item."""

    id: str
    label: str
    done: bool = False
    blocking: bool = False
    hint: str | None = None


class AssetRef(BaseModel):
    """Points at bytes already delivered on the ``lkap.ui.asset`` byte stream."""

    asset_id: str
    kind: str
    mime: str
    caption: str | None = None
    meta: dict[str, str] = {}
    ts: float


class ActivityEvent(BaseModel):
    """A tool call, workflow run or escalation rendered as a "team feed" entry."""

    v: Literal[1] = 1
    id: str
    ts: float
    source: str
    label: str
    phase: Literal["running", "done", "error", "cancelled"]
    headline: str
    urgent: bool = False
    duration_ms: int | None = None
    detail: dict[str, Any] | None = None


BlockType = Literal[
    "status",
    "notes",
    "checklist",
    "activity",
    "form",
    "document",
    "gallery",
    "table",
    "transcript",
    "video",
    "kb_citations",
    "custom",
]


class BlockSpec(BaseModel):
    """One block of a composite panel (CONTRACTS-V2 §4.4).

    ``id`` keys the block's state in :attr:`UiState.blocks`; ``config`` is
    block-type specific and rendered by the matching React component.
    """

    id: str
    type: BlockType
    title: str | None = None
    config: dict[str, Any] = {}
    order: int = 0


class FormBlockState(BaseModel):
    """A JSON-schema form the agent asked the user to fill in."""

    schema_: dict[str, Any] = Field(default_factory=dict, alias="schema")
    values: dict[str, Any] = {}
    status: Literal["idle", "requested", "submitted"] = "idle"
    submitted_at: float | None = None

    model_config = ConfigDict(populate_by_name=True)


class DocumentHighlight(BaseModel):
    """A rectangle called out on a document page; ``bbox`` is ``[x0, y0, x1, y1]``."""

    page: int
    bbox: tuple[float, float, float, float]
    note: str | None = None


class DocumentBlockState(BaseModel):
    """A document shown page by page, optionally with highlights."""

    asset_id: str | None = None
    url: str | None = None
    page: int = 1
    highlights: list[DocumentHighlight] = []


class GalleryBlockState(BaseModel):
    """Images delivered on the asset stream, with one optionally selected."""

    asset_ids: list[str] = []
    selected: str | None = None


class TableColumn(BaseModel):
    """One column of a table block."""

    key: str
    label: str
    type: Literal["string", "number", "boolean", "date"] = "string"


class TableBlockState(BaseModel):
    """Tabular data the agent appends rows to."""

    columns: list[TableColumn] = []
    rows: list[dict[str, Any]] = []
    selected_row: str | None = None


class TranscriptBlockState(BaseModel):
    """Transcript rendering options (the turns come from the LiveKit room)."""

    show_tools: bool = False


class VideoBlockState(BaseModel):
    """Which video track the block renders."""

    source: str = "agent_avatar"
    muted: bool = False


class KbCitation(BaseModel):
    """One retrieved chunk cited to the user."""

    chunk_id: str
    filename: str
    score: float
    text: str


class KbCitationsBlockState(BaseModel):
    """Knowledge-base hits backing the agent's last answer."""

    items: list[KbCitation] = []


class UiState(BaseModel):
    """The platform envelope every panel renders; ``custom`` is pack-defined.

    ``v`` accepts ``1`` for one release so v1 producers (and the web reducer's
    own literals) keep validating while panels migrate to blocks.
    """

    v: Literal[1, 2] = 2
    status: StatusStamp | None = None
    progress: int | None = None
    notes: list[Note] = []
    checklist: list[ChecklistItem] = []
    assets: list[AssetRef] = []
    activity: list[ActivityEvent] = []
    blocks: dict[str, Any] = {}
    custom: dict[str, Any] = {}


class UiSnapshot(BaseModel):
    """Full state replacement sent on session start, every 50 patches and on request."""

    v: Literal[1] = 1
    type: Literal["snapshot"] = "snapshot"
    seq: int
    session_id: str
    state: UiState


class UiPatchOp(BaseModel):
    """A single JSON-pointer-style mutation of :class:`UiState`."""

    op: Literal["set", "append", "remove", "upsert"]
    path: str
    value: Any = None
    key: str | None = None


class UiPatch(BaseModel):
    """An ordered batch of ops applied to the UI's copy of :class:`UiState`."""

    v: Literal[1] = 1
    type: Literal["patch"] = "patch"
    seq: int
    session_id: str
    ops: list[UiPatchOp]


UiStateMessage = Annotated[UiSnapshot | UiPatch, Field(discriminator="type")]


class UiRequest(BaseModel):
    """RPC payload for ``lkap.ui.request`` (agent asks the browser to do something).

    Payload keys per method are fixed (ruling R-V2-3b, PLAN-V2 §8):

    * ``open_dialog`` — ``{dialog: str, params?: dict}`` (never ``{id: ...}``)
    * ``focus`` — ``{target: str}``
    * ``request_video_source`` — ``{source: "camera" | "screen"}``
    * ``toast`` — ``{message: str, tone?: Tone}``
    * ``form`` — ``{block_id, schema, prefill}``; result ``{values}`` or ``{cancelled: true}``
    * ``show_block`` — ``{block_id}``
    * ``navigate`` — ``{url}`` (new tab; the UI confirms first)
    """

    v: Literal[1] = 1
    method: Literal[
        "open_dialog",
        "focus",
        "request_video_source",
        "toast",
        "form",
        "show_block",
        "navigate",
    ]
    payload: dict[str, Any] = {}


class UiRequestResult(BaseModel):
    """Result the browser returns for a :class:`UiRequest`."""

    ok: bool
    payload: dict[str, Any] = {}


class AgentAction(BaseModel):
    """RPC payload for ``lkap.agent.action`` (browser asks the agent to do something)."""

    v: Literal[1] = 1
    action: Literal[
        "get_snapshot",
        "set_video_source",
        "ui_action",
        "form_submit",
        "block_action",
        "rewind",
        "inject_user_text",
    ]
    payload: dict[str, Any] = {}


class AgentActionResult(BaseModel):
    """Result the agent returns for an :class:`AgentAction`."""

    ok: bool
    payload: dict[str, Any] = {}
    error: str | None = None
