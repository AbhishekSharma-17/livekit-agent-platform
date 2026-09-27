"""Agent to UI protocol: the state envelope, patch messages, activity and RPC payloads.

The agent owns a :class:`UiState` envelope per session. It publishes a
:class:`UiSnapshot` on session start (``seq == 1``) and a :class:`UiPatch` for every
subsequent change. The browser applies patches strictly in ``seq`` order and asks
for a fresh snapshot over RPC when it sees a gap.
"""

from typing import Annotated, Any, Final, Literal, Self, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lkap_contracts.compliance import MAX_CONSENT_TEXT_CHARS, TEXT_HASH_PATTERN, ConsentKind, ConsentMethod

TOPIC_UI_STATE = "lkap.ui.state"
TOPIC_UI_ACTIVITY = "lkap.ui.activity"
TOPIC_UI_ASSET = "lkap.ui.asset"
#: Browser -> agent byte stream carrying a caller's file for an ``upload`` block or a
#: ``form`` file field (V5-19). Stream attributes: ``block_id`` (required), ``name`` (the
#: file's display name) and, for a form, ``field`` (the property name).
TOPIC_UI_UPLOAD = "lkap.ui.upload"
#: Agent -> browser text stream of live captions (V5-31): one :class:`CaptionSegment` JSON per
#: message, both sides of the conversation, published only while the panel has a ``captions``
#: block. The agent's words arrive in step with its audio (after the transcript synchroniser).
TOPIC_UI_CAPTIONS = "lkap.captions"
RPC_UI_REQUEST = "lkap.ui.request"
RPC_AGENT_ACTION = "lkap.agent.action"


#: Canonical mapping of topic constant name to wire value (exported to JSON/TS).
TOPICS: dict[str, str] = {
    "TOPIC_UI_STATE": TOPIC_UI_STATE,
    "TOPIC_UI_ACTIVITY": TOPIC_UI_ACTIVITY,
    "TOPIC_UI_ASSET": TOPIC_UI_ASSET,
    "TOPIC_UI_UPLOAD": TOPIC_UI_UPLOAD,
    "TOPIC_UI_CAPTIONS": TOPIC_UI_CAPTIONS,
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


#: What a stored session asset is (``session_assets.kind``, V5-19): a caller's
#: ``upload``, a pinned camera/screen ``frame``, a drawn ``signature``, or a knowledge-base
#: ``document`` copied in so a citation can open it (R-V5-5).
SessionAssetKind = Literal["upload", "frame", "signature", "document"]


class AssetRef(BaseModel):
    """Points at bytes already delivered on the ``lkap.ui.asset`` byte stream.

    ``kind`` is the display kind (``photo`` for a pinned frame, a pack-defined
    kind, ``upload``). V5-19: ``stored`` says the bytes are also kept in the
    session's asset store (``session_assets``); ``asset_id`` is then the stored
    asset's id, which the console lists and downloads after the call. ``name``
    and ``size`` describe the file. The browser still renders from the bytes
    delivered on ``lkap.ui.asset`` (the display path).
    """

    asset_id: str
    kind: str
    mime: str
    caption: str | None = None
    meta: dict[str, str] = {}
    ts: float
    stored: bool = False
    name: str | None = None
    size: int | None = Field(default=None, ge=0)


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
    "choices",
    "details",
    "markdown",
    "steps",
    "consent",
    "upload",
    "captions",
    "handoff",
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
    """One retrieved chunk cited to the user.

    The locators (V5-08) come from the chunk's ingest metadata (V5-01) and are
    absent for chunks ingested before it. Tapping a citation sends
    ``block_action {name: "open_citation", data: {chunk_id}}``; the worker
    opens the source page in a ``document`` block when it has the source.
    """

    chunk_id: str
    filename: str
    score: float
    text: str
    document_id: str | None = None
    page: int | None = None
    heading_path: list[str] | None = None
    char_start: int | None = None
    char_end: int | None = None


class KbCitationsBlockState(BaseModel):
    """Knowledge-base hits backing the agent's last answer."""

    items: list[KbCitation] = []


class ChoiceOption(BaseModel):
    """One option of a ``choices`` block (V5-08)."""

    id: str = Field(min_length=1)
    label: str
    hint: str | None = None
    tone: Tone | None = None
    image_asset_id: str | None = None


class ChoiceReveal(BaseModel):
    """The right answer of a quiz-style ``choices`` block, shown once revealed."""

    correct: list[str] = []
    explanation: str | None = None


class ChoicesBlockState(RequestableState):
    """Quick replies the caller taps or answers by voice (``request_choice``, V5-08).

    ``selected`` holds the chosen option ids once ``status`` is ``submitted``
    (a single-choice block holds one). A generic ``block_submit`` answers with
    ``values: {selected: [...]}``.
    """

    prompt: str = ""
    options: list[ChoiceOption] = []
    multi: bool = False
    selected: list[str] = []
    reveal: ChoiceReveal | None = None


#: How a ``details`` value is formatted.
DetailsValueType = Literal["string", "number", "date", "money", "phone", "email", "badge"]


class DetailsItem(BaseModel):
    """One key-value row of a ``details`` card; ``set_details`` upserts by ``key``."""

    key: str = Field(min_length=1)
    label: str
    value: str | float | None = None
    type: DetailsValueType = "string"
    tone: Tone | None = None
    updated_at: float | None = None


class DetailsBlockState(BaseModel):
    """A key-value summary card, "what we have so far" (``set_details``, V5-08)."""

    items: list[DetailsItem] = []


class MarkdownBlockState(BaseModel):
    """Rich text in a strict Markdown subset, never raw HTML (``show_text``, V5-08)."""

    markdown: str = ""
    title: str | None = None
    updated_at: float | None = None


#: Where one step of a ``steps`` block stands.
StepStatus = Literal["pending", "active", "done", "skipped", "failed"]


class StepItem(BaseModel):
    """One step of a ``steps`` timeline."""

    id: str = Field(min_length=1)
    label: str
    status: StepStatus = "pending"
    note: str | None = None
    at: float | None = None


class StepsBlockState(BaseModel):
    """A progress timeline: ``set_steps``, or the flow position with ``source="flow"`` (V5-08).

    A ``custom`` block with ``kind == "flow_progress"`` keeps its raw flow
    mirror; the console renders it with the ``steps`` renderer (D-V5-33).
    """

    steps: list[StepItem] = []
    current: str | None = None


class ConsentBlockState(RequestableState):
    """A consent question or disclosure the caller accepts or declines (``request_consent``, V5-15).

    ``status`` is the request lifecycle every requestable block shares
    (:class:`RequestableState`); the caller's decision is ``accepted``
    (``None`` until they answer). A tap answers with ``block_submit {values:
    {accepted: true | false}}`` (only ``accepted`` is read from a browser,
    V5-27 S5-3); a spoken answer is recorded by ``record_consent`` (always
    ``method="voice"``, with the user turn it was heard in, S5-4). ``text`` is
    the exact wording shown (the block's ``config.text``, else the workspace's
    preset for its ``kind``) and ``text_hash`` its SHA-256 once answered; the
    worker hashes the wording it computed, never block state, so the answer is
    tied to the wording (``lkap_contracts.compliance.consent_text_hash``).
    """

    kind: ConsentKind = "recording"
    text: str = Field(default="", max_length=MAX_CONSENT_TEXT_CHARS)
    required: bool = True
    accepted: bool | None = None
    method: ConsentMethod | None = None
    at: float | None = None
    text_hash: str | None = Field(default=None, pattern=TEXT_HASH_PATTERN)


#: SHA-256 of a stored file, lowercase hex.
SHA256_PATTERN: Final[str] = r"^[0-9a-f]{64}$"
#: The largest file a caller may send, whatever a block says (25 MiB, like a KB upload).
MAX_UPLOAD_BYTES: Final[int] = 25 * 1024 * 1024
#: ``max_bytes`` of an upload block or form file field that does not set one (10 MiB).
DEFAULT_UPLOAD_MAX_BYTES: Final[int] = 10 * 1024 * 1024
#: The most files one upload request may take.
MAX_UPLOAD_FILES: Final[int] = 10


class UploadedFile(BaseModel):
    """One file the worker received, checked and stored for an ``upload`` block (V5-19).

    Written by the worker only: a ``block_submit`` never replaces the list
    (the browser's answer names asset ids; the worker keeps what it verified).
    """

    asset_id: str = Field(min_length=1)
    name: str
    mime: str
    size: int = Field(ge=0)
    sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)


#: Why the worker refused a file (the renderer shows the rejection's ``message``).
UploadRejectReason = Literal[
    "too_large", "type_not_allowed", "too_many_files", "empty", "not_requested", "failed"
]


class UploadRejection(BaseModel):
    """A file the worker refused, and why (V5-19)."""

    name: str
    reason: UploadRejectReason
    message: str


class UploadBlockState(RequestableState):
    """Files the caller sends from their device (``request_upload``, V5-19).

    While ``status`` is ``requested`` the browser streams each file on
    ``lkap.ui.upload`` (attributes ``block_id``, ``name``). The worker checks it
    against the block config's ``accept``, ``max_bytes`` and ``max_files`` (it
    sniffs the bytes and never trusts the declared type), stores it through the
    api and appends it to ``files``, or appends a ``rejected`` row. ``progress``
    (0-1) is how much of the file being received has arrived. The browser then
    answers ``block_submit {values: {files: [asset_id, ...]}}``.
    """

    prompt: str = ""
    files: list[UploadedFile] = []
    rejected: list[UploadRejection] = []
    progress: float | None = Field(default=None, ge=0, le=1)


#: Who said a caption's words (V5-31).
CaptionSpeaker = Literal["user", "agent"]
#: Where a ``captions`` block renders: in its panel slot, or over the video on avatar layouts.
CaptionsPosition = Literal["block", "bottom"]


class CaptionSegment(BaseModel):
    """One live caption message on ``lkap.captions`` (V5-31).

    ``id`` names the utterance: interim messages (``final: false``) carry the text so
    far and are replaced by later ones with the same ``id``; the ``final`` one closes
    it. ``language`` is the transcriber's detected language for the caller, or the
    agent's current reply language; ``None`` when unknown.
    """

    v: Literal[1] = 1
    id: str = Field(min_length=1)
    speaker: CaptionSpeaker
    text: str
    final: bool
    language: str | None = None
    ts: float


class CaptionsBlockState(BaseModel):
    """The ``captions`` block's state (V5-31). The words themselves stream on ``lkap.captions``.

    ``language`` is the conversation's current language (the agent's reply language,
    updated on a switch); ``target_language`` is copied from the config and reserved
    for translated captions (a later package).
    """

    language: str | None = None
    target_language: str | None = None


#: Where a hand-off to a person stands (V5-32; V5-37 drives it from ``escalate_to_human`` too):
#: ``idle`` (nothing asked), ``requested`` (the agent decided to hand over), ``connecting``
#: (the person's phone is ringing, or the caller is being put through), ``connected`` (the
#: person is on the call), ``timeout`` (nobody answered, or they declined: the agent carries on),
#: ``ended`` (the call left the agent, e.g. after a standard transfer).
HandoffStatus = Literal["idle", "requested", "connecting", "connected", "timeout", "ended"]


class HandoffBlockState(BaseModel):
    """The ``handoff`` block's state (V5-32): the hand-off of the caller to a person.

    ``target`` is the destination's label (never its number); ``mode`` is the
    transfer that actually ran (a warm request on a connection that cannot do it
    runs ``cold``); ``queue_position`` and ``agent_name`` are shown when the block's
    config asks for them and something fills them (V5-37's queue, the person's
    name once connected); ``reason`` is a short plain-words line for ``timeout``.
    """

    status: HandoffStatus = "idle"
    mode: Literal["cold", "warm"] | None = None
    target: str | None = None
    queue_position: int | None = Field(default=None, ge=0)
    agent_name: str | None = None
    reason: str | None = None


#: The field types ``request_form`` offers (V5-19 adds ``phone``, ``textarea`` and ``file``).
#: How each lands in the form's JSON schema (``FormBlockState.schema``):
#:
#: * ``string`` / ``number`` / ``integer`` / ``boolean`` → the same JSON-schema ``type``;
#: * ``date`` / ``email`` / ``phone`` → ``{type: "string", format: <the type>}``;
#: * ``select`` → ``{type: "string", enum: [...options]}``;
#: * ``textarea`` → ``{type: "string", "x-lkap-widget": "textarea"}`` (several lines);
#: * ``file`` → ``{type: "array", items: {type: "string"}, maxItems: <max_files>,
#:   "x-lkap-widget": "file", "x-lkap-upload": {accept, max_files, max_bytes}}``. The
#:   browser streams each file on ``lkap.ui.upload`` with ``block_id`` = the form block and
#:   ``field`` = the property name, and submits the stored asset ids as the field's value.
FormFieldType = Literal[
    "string", "number", "integer", "boolean", "date", "phone", "email", "select", "textarea", "file"
]
FORM_FIELD_TYPES: Final[tuple[str, ...]] = get_args(FormFieldType)
#: JSON-schema extension key naming the widget a form property renders as.
FORM_WIDGET_KEY: Final[str] = "x-lkap-widget"
#: JSON-schema extension key carrying a ``file`` field's limits (:class:`FormUploadSpec`).
FORM_UPLOAD_KEY: Final[str] = "x-lkap-upload"


class FormUploadSpec(BaseModel):
    """The limits of a form's ``file`` field (``x-lkap-upload``); empty ``accept`` = images and PDFs."""

    accept: list[str] = []
    max_files: int = Field(default=1, ge=1, le=MAX_UPLOAD_FILES)
    max_bytes: int = Field(default=DEFAULT_UPLOAD_MAX_BYTES, ge=1, le=MAX_UPLOAD_BYTES)


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
