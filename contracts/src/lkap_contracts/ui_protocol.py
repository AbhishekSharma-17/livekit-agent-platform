"""Agent to UI protocol: the state envelope, patch messages, activity and RPC payloads.

The agent owns a :class:`UiState` envelope per session. It publishes a
:class:`UiSnapshot` on session start (``seq == 1``) and a :class:`UiPatch` for every
subsequent change. The browser applies patches strictly in ``seq`` order and asks
for a fresh snapshot over RPC when it sees a gap.
"""

from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


#: Lifecycle of a requestable block (V5-02). ``requested`` means a request is
#: pending: a reconnecting browser renders it from the snapshot alone.
RequestStatus = Literal["idle", "requested", "submitted", "cancelled"]


class RequestableState(BaseModel):
    """Mixin for the state of every block the agent can ask the user to answer.

    The agent flips ``status`` to ``requested`` when it asks (``UiRequest.method
    == "request"``, or the legacy ``form``) and the browser answers with
    ``AgentAction.action == "block_submit"``. ``submitted`` stamps
    ``submitted_at``; ``cancelled`` covers a user cancel, a timeout and a
    barge-in on the generic ``request`` path. The legacy ``form`` path keeps
    its v2 statuses for one release: ``idle`` after a cancel, ``requested``
    after a timeout (so a late submission still lands).
    """

    status: RequestStatus = "idle"
    submitted_at: float | None = None


class FormBlockState(RequestableState):
    """A JSON-schema form the agent asked the user to fill in."""

    schema_: dict[str, Any] = Field(default_factory=dict, alias="schema")
    values: dict[str, Any] = {}

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
    * ``form`` — ``{block_id, schema, prefill}``; result ``{values}`` or ``{cancelled: true}``.
      Deprecated alias of ``request``, kept for one release (V5-02); its answer
      may come back as ``form_submit`` or ``block_submit``.
    * ``show_block`` — ``{block_id}``
    * ``navigate`` — ``{url}`` (new tab; the UI confirms first)
    * ``request`` — ``{block_id, timeout_s, schema?}`` (V5-02): the generic
      blocking request on any requestable block (:class:`RequestableState`).
      The block's state already shows ``status: "requested"``, so the browser
      acks at once (``{}``) and answers later with ``block_submit``; an inline
      ``{values}`` or ``{cancelled: true}`` result is accepted too.
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
        "request",
    ]
    payload: dict[str, Any] = {}


class UiRequestResult(BaseModel):
    """Result the browser returns for a :class:`UiRequest`."""

    ok: bool
    payload: dict[str, Any] = {}


class BlockRequestPayload(BaseModel):
    """``UiRequest.payload`` for ``method == "request"`` (V5-02).

    ``schema`` is optional block-specific input a renderer may need beyond the
    block state; extra keys a requestable block defines pass through.
    """

    block_id: str = Field(min_length=1)
    timeout_s: float = Field(gt=0)
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")

    model_config = ConfigDict(populate_by_name=True, extra="allow")


class BlockSubmitPayload(BaseModel):
    """``AgentAction.payload`` for ``action == "block_submit"`` (V5-02).

    Exactly one of ``values`` (the user's answer) or ``cancelled: true``.
    """

    block_id: str = Field(min_length=1)
    values: dict[str, Any] | None = None
    cancelled: bool = False

    @model_validator(mode="after")
    def _one_answer(self) -> Self:
        if self.cancelled == (self.values is not None):
            raise ValueError("block_submit needs either a values object or cancelled: true")
        return self


class AgentAction(BaseModel):
    """RPC payload for ``lkap.agent.action`` (browser asks the agent to do something).

    Payload keys per action:

    * ``get_snapshot`` — ``{}``
    * ``set_video_source`` — ``{source: "camera" | "screen" | "none"}``
    * ``ui_action`` — ``{name, data}`` → ``Pack.on_ui_action``
    * ``form_submit`` — ``{block_id, values}`` or ``{block_id, cancelled: true}``
      (``form`` blocks only; kept for one release beside ``block_submit``)
    * ``block_action`` — ``{block_id, name, data}`` → ``Pack.on_block_action``
    * ``rewind`` / ``inject_user_text`` — the text-session actions (V2-18)
    * ``block_submit`` — ``{block_id, values}`` or ``{block_id, cancelled: true}``
      (V5-02): the answer to a ``request`` (or a ``form``) on any requestable block
    """

    v: Literal[1] = 1
    action: Literal[
        "get_snapshot",
        "set_video_source",
        "ui_action",
        "form_submit",
        "block_action",
        "rewind",
        "inject_user_text",
        "block_submit",
    ]
    payload: dict[str, Any] = {}


class AgentActionResult(BaseModel):
    """Result the agent returns for an :class:`AgentAction`."""

    ok: bool
    payload: dict[str, Any] = {}
    error: str | None = None
