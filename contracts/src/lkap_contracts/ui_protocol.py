"""Agent to UI protocol: the state envelope, patch messages, activity and RPC payloads.

The agent owns a :class:`UiState` envelope per session. It publishes a
:class:`UiSnapshot` on session start (``seq == 1``) and a :class:`UiPatch` for every
subsequent change. The browser applies patches strictly in ``seq`` order and asks
for a fresh snapshot over RPC when it sees a gap.
"""

import re
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any, Final, Literal, Self, get_args
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)

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
#: Server -> agent reliable data packet (V5-43): a ``link`` block's outcome, sent by the api
#: when ``POST /v1/hooks/link/{session_id}`` verifies (:class:`LinkCompletedPacket`). The
#: worker honours only packets the server sent (no participant) for its own session.
TOPIC_UI_LINK = "lkap.ui.link"
#: Browser -> agent text stream of a caller's pen strokes on a ``canvas`` block (V6-12,
#: D-V6-16): one :class:`InkMessage` JSON per message, at most :data:`MAX_INK_MESSAGE_BYTES`,
#: at most :data:`MAX_INK_MESSAGES_PER_S` a second. The worker takes it from the session's
#: caller only, for a canvas the caller may draw on (:func:`lkap_contracts.blocks.
#: canvas_caller_can_draw`), and writes accepted strokes to the block's state.
TOPIC_UI_INK = "lkap.ui.ink"


#: Canonical mapping of topic constant name to wire value (exported to JSON/TS).
TOPICS: dict[str, str] = {
    "TOPIC_UI_STATE": TOPIC_UI_STATE,
    "TOPIC_UI_ACTIVITY": TOPIC_UI_ACTIVITY,
    "TOPIC_UI_ASSET": TOPIC_UI_ASSET,
    "TOPIC_UI_UPLOAD": TOPIC_UI_UPLOAD,
    "TOPIC_UI_CAPTIONS": TOPIC_UI_CAPTIONS,
    "RPC_UI_REQUEST": RPC_UI_REQUEST,
    "RPC_AGENT_ACTION": RPC_AGENT_ACTION,
    "TOPIC_UI_LINK": TOPIC_UI_LINK,
    "TOPIC_UI_INK": TOPIC_UI_INK,
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


class _OmitsUnsetV6Keys(BaseModel):
    """Leaves a V6-06 key off the wire while it is ``None`` (``Note.block_id``, ``edited_by``).

    So a panel that never uses them sends exactly what it sent before V6-06 (the
    insurance pack's committed ``UiState`` goldens, every stored snapshot).
    """

    @model_serializer(mode="wrap")
    def _omit_unset_v6_keys(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        for key in ("block_id", "edited_by"):
            if key in data and data[key] is None:
                del data[key]
        return data


class Note(_OmitsUnsetV6Keys):
    """A line in the panel's notes list. A repeated ``key`` upserts in place.

    ``block_id`` (V6-06, D-V6-19) pins the note to one block of the panel: the
    console shows it in that block's margin instead of (only) the notes list.
    ``None`` is a panel-wide note, as before.
    """

    id: str
    text: str
    kind: str = "note"
    tone: Tone = "neutral"
    ts: float
    key: str | None = None
    block_id: str | None = None


#: Who last changed a value the agent also writes (V6-06): ``caller`` for an edit the caller
#: made on screen (a ``block_action {name: "edit"}``). ``None`` is the agent's own value; the
#: agent writing the value again clears the marker.
EditAuthor = Literal["caller"]


class ChecklistItem(_OmitsUnsetV6Keys):
    """One "still needed" item. ``edited_by`` is ``"caller"`` after the caller ticked it on screen (V6-06)."""

    id: str
    label: str
    done: bool = False
    blocking: bool = False
    hint: str | None = None
    edited_by: EditAuthor | None = None


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
    kind: Literal["tool", "guardrail"] | None = None
    """What the row is (V5-39): ``guardrail`` for a guardrail trip (``detail`` then carries
    ``stage``, ``rule`` and ``action``); ``None`` for every other row, as before."""


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
    "link",
    "slots",
    "cards",
    # V6-08 (D-V6-15, D-V6-18): a sectioned notebook, and a container that groups other blocks.
    "notebook",
    "layout",
    # V6-12 (D-V6-16): a drawing board the caller writes on and the agent marks up.
    "canvas",
    # V6-23 (D-V6-20): a signature, a chart, a timer, read-only code and a cart.
    "signature",
    "chart",
    "timer",
    "code",
    "cart",
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


class DetailsItem(_OmitsUnsetV6Keys):
    """One key-value row of a ``details`` card; ``set_details`` upserts by ``key``.

    ``edited_by`` is ``"caller"`` after the caller changed the value on screen (V6-06);
    ``set_details`` writing the row again clears it.
    """

    key: str = Field(min_length=1)
    label: str
    value: str | float | None = None
    type: DetailsValueType = "string"
    tone: Tone | None = None
    updated_at: float | None = None
    edited_by: EditAuthor | None = None


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


# --------------------------------------------------------------------- links (V5-43)

#: The longest link a ``link`` block or a card image may carry.
MAX_URL_CHARS: Final[int] = 2048
#: How many sites a ``link`` block's ``allowed_hosts`` (or a ``cards`` block's
#: ``image_hosts``) may list.
MAX_ALLOWED_HOSTS: Final[int] = 20
#: A site name in an allowlist: a host name with at least one dot, lower case, optionally
#: ``*.`` in front for "any sub-domain of" (the bare domain itself is not included then).
HOST_PATTERN: Final[str] = (
    r"^(\*\.)?([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$"
)
_HOST_RE: Final[re.Pattern[str]] = re.compile(HOST_PATTERN)
_URL_FORBIDDEN_RE: Final[re.Pattern[str]] = re.compile("[\\x00-\\x20\\x7f-\\x9f\\\\<>\"'`{}|^]")


def _is_numeric_host(host: str) -> bool:
    """An IPv4 literal (digits and dots only): links name sites, never addresses."""
    return host.replace(".", "").replace("*", "").isdigit()


def normalize_host(value: str) -> str:
    """One allowlist entry as a lower-case site name (``example.com``, ``*.example.com``).

    Raises:
        ValueError: For anything that is not a site name: a scheme, a path, a port,
            an IP address, spaces or a name without a dot.
    """
    host = value.strip().lower().rstrip(".")
    if not _HOST_RE.match(host) or _is_numeric_host(host):
        raise ValueError(f"{value!r} is not a site name. Write it like example.com or *.example.com")
    return host


def normalize_hosts(values: list[str]) -> list[str]:
    """Normalise an allowlist (:func:`normalize_host` each, duplicates dropped, order kept)."""
    out: list[str] = []
    for value in values:
        host = normalize_host(value)
        if host not in out:
            out.append(host)
    return out


def host_allowed(host: str, allowed_hosts: list[str]) -> bool:
    """Whether ``host`` is one of ``allowed_hosts`` (exact, or a sub-domain of a ``*.`` entry)."""
    host = host.strip().lower().rstrip(".")
    for entry in allowed_hosts:
        entry = entry.strip().lower().rstrip(".")
        if entry.startswith("*."):
            if host.endswith(entry[1:]) and len(host) > len(entry) - 1:
                return True
        elif host == entry:
            return True
    return False


def https_url_problem(url: str, *, allowed_hosts: list[str] | None = None) -> str | None:
    """Why ``url`` may not be shown to a caller, or ``None`` when it may (V5-43).

    Only ``https://`` links with a host name are accepted: never ``javascript:``,
    ``data:``, ``http:`` or any other scheme, never user names or passwords in the
    link, never spaces, quotes, angle brackets, backslashes or control characters,
    never more than :data:`MAX_URL_CHARS` characters. With ``allowed_hosts`` the
    host must also be one of them (:func:`host_allowed`).
    """
    if not url:
        return "the link is empty"
    if len(url) > MAX_URL_CHARS:
        return f"the link is longer than {MAX_URL_CHARS} characters"
    if _URL_FORBIDDEN_RE.search(url):
        return "the link contains spaces or characters a link cannot have"
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        _ = parts.port
    except ValueError:
        return "the link is not a valid web address"
    if parts.scheme.lower() != "https":
        return "only https:// links can be shown"
    if "@" in parts.netloc:
        return "a link cannot carry a user name or password"
    if not host or not _HOST_RE.match(host.lower()) or _is_numeric_host(host):
        return "the link has no valid site name"
    if allowed_hosts is not None and not host_allowed(host, allowed_hosts):
        return f"{host} is not one of the sites this panel may link to ({', '.join(allowed_hosts) or 'none'})"
    return None


def _checked_https_url(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    problem = https_url_problem(value)
    if problem is not None:
        raise ValueError(problem)
    return value


#: What a ``link`` block hands the caller to.
LinkKind = Literal["checkout", "esign", "portal", "other"]
#: Where a ``link`` block stands: ``idle`` (nothing sent yet), ``pending`` (shown or texted),
#: ``opened`` (the caller opened it), then ``completed`` / ``failed`` / ``expired`` (the
#: outcome the business's system reported through the link hook, or the expiry passing).
LinkStatus = Literal["idle", "pending", "opened", "completed", "failed", "expired"]
#: The outcomes the link hook may report.
LinkOutcome = Literal["completed", "failed", "expired"]
LINK_OUTCOMES: Final[tuple[str, ...]] = get_args(LinkOutcome)
#: How a link reached the caller: on the panel, or by text message on a voice-only call.
LinkChannel = Literal["panel", "sms"]
#: Longest button label and reference of a link.
MAX_LINK_LABEL_CHARS: Final[int] = 80
MAX_LINK_REFERENCE_CHARS: Final[int] = 128
#: A link reference: letters, digits and ``_.:-`` (an order id, an envelope id, a session id).
LINK_REFERENCE_PATTERN: Final[str] = r"^[A-Za-z0-9_.:-]{1,128}$"


class LinkBlockState(BaseModel):
    """A checkout, e-sign or portal link and where it stands (``send_link``, V5-43).

    Payments never happen in LKAP (D-V5-6): the block carries the link and the
    outcome the business's own system reports through ``POST /v1/hooks/link/
    {session_id}`` (signed), which the worker receives as a ``link_completed``
    packet on :data:`TOPIC_UI_LINK`. ``url`` is always an ``https://`` link on
    one of the block config's ``allowed_hosts`` (checked by the tool and here).
    ``reference`` is the business's id for the flow (an order or envelope id)
    that the hook may name instead of the block.
    """

    url: str | None = Field(default=None, max_length=MAX_URL_CHARS)
    label: str = Field(default="", max_length=MAX_LINK_LABEL_CHARS)
    kind: LinkKind = "other"
    status: LinkStatus = "idle"
    reference: str | None = Field(default=None, pattern=LINK_REFERENCE_PATTERN)
    channel: LinkChannel | None = None
    sent_at: float | None = None
    opened_at: float | None = None
    expires_at: float | None = None
    completed_at: float | None = None

    @field_validator("url")
    @classmethod
    def _https(cls, value: str | None) -> str | None:
        return _checked_https_url(value)


# --------------------------------------------------------------------- slots (V5-43)

#: The most slots one ``slots`` request may offer.
MAX_SLOTS: Final[int] = 50


def _aware_datetime(value: str) -> datetime:
    try:
        moment = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{value!r} is not an ISO 8601 date and time") from exc
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"{value!r} needs a UTC offset, e.g. 2026-10-02T09:30:00+01:00")
    return moment


class TimeSlot(BaseModel):
    """One bookable slot: ISO 8601 start and end, each with its UTC offset (V5-43)."""

    id: str = Field(min_length=1, max_length=64)
    start: str = Field(max_length=40)
    end: str = Field(max_length=40)
    label: str | None = Field(default=None, max_length=80)
    capacity: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if _aware_datetime(self.end) <= _aware_datetime(self.start):
            raise ValueError("a slot must end after it starts")
        return self


class SlotsBlockState(RequestableState):
    """A calendar slot picker the caller taps or answers by voice (``request_slot``, V5-43).

    The agent fetches availability with its own tools and offers ``slots``;
    ``selected`` is the chosen slot's id once ``status`` is ``submitted``. A tap
    answers ``block_submit {values: {selected: <slot id>}}``: the worker reads
    only ``selected`` and takes the start and end from its own ``slots``, never
    from the browser. ``timezone`` is the zone the slots are shown in (the
    caller's, or the business's with ``timezone_mode="agent"``).
    """

    prompt: str = Field(default="", max_length=300)
    timezone: str | None = Field(default=None, max_length=64)
    slots: list[TimeSlot] = Field(default=[], max_length=MAX_SLOTS)
    selected: str | None = None
    grouped_by_day: bool = True

    @field_validator("slots")
    @classmethod
    def _unique_ids(cls, value: list[TimeSlot]) -> list[TimeSlot]:
        ids = [slot.id for slot in value]
        if len(set(ids)) != len(ids):
            raise ValueError("slot ids must be unique")
        return value


# --------------------------------------------------------------------- cards (V5-43)

#: The most cards a ``cards`` block holds, whatever its config says.
MAX_CARDS: Final[int] = 20
#: A card action's name: what the worker and the pack see in ``block_action``.
CARD_ACTION_NAME_PATTERN: Final[str] = r"^[a-z][a-z0-9_]{0,31}$"


class CardFact(BaseModel):
    """One label and value line on a card."""

    label: str = Field(max_length=40)
    value: str = Field(max_length=120)


class CardAction(BaseModel):
    """A button on a card; a tap sends ``block_action {name, data: {card_id}}``."""

    name: str = Field(pattern=CARD_ACTION_NAME_PATTERN)
    label: str = Field(min_length=1, max_length=40)
    tone: Tone | None = None


class Card(BaseModel):
    """One card: a plan, a repair shop, an offer (V5-43).

    ``image_asset_id`` names a picture already delivered on ``lkap.ui.asset``;
    ``image_url`` is an ``https://`` picture on one of the block config's
    ``image_hosts`` (the tool checks the host; this model checks the link).
    """

    id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=120)
    subtitle: str | None = Field(default=None, max_length=200)
    image_asset_id: str | None = Field(default=None, max_length=128)
    image_url: str | None = Field(default=None, max_length=MAX_URL_CHARS)
    facts: list[CardFact] = Field(default=[], max_length=8)
    badges: list[Annotated[str, Field(max_length=30)]] = Field(default=[], max_length=5)
    actions: list[CardAction] = Field(default=[], max_length=3)

    @field_validator("image_url")
    @classmethod
    def _https(cls, value: str | None) -> str | None:
        return _checked_https_url(value)

    @field_validator("actions")
    @classmethod
    def _unique_actions(cls, value: list[CardAction]) -> list[CardAction]:
        names = [action.name for action in value]
        if len(set(names)) != len(names):
            raise ValueError("action names must be unique on a card")
        return value


class CardsBlockState(BaseModel):
    """Rich cards, a carousel or a comparison (``show_cards``, V5-43).

    Tapping a card (with ``selectable``) sends ``block_action {name: "select",
    data: {card_id}}``; an action button sends ``block_action {name: <action>,
    data: {card_id}}``. The worker records ``selected`` and tells the model in a
    background message; a pack's ``on_block_action`` sees it too.
    """

    cards: list[Card] = Field(default=[], max_length=MAX_CARDS)
    selected: str | None = None

    @field_validator("cards")
    @classmethod
    def _unique_ids(cls, value: list[Card]) -> list[Card]:
        ids = [card.id for card in value]
        if len(set(ids)) != len(ids):
            raise ValueError("card ids must be unique")
        return value


# --------------------------------------------------------------------- notebook (V6-08)

#: What one section of a ``notebook`` block holds (D-V6-15): running ``text`` notes, a
#: ``checklist``, a ``details`` card, or an ``ink`` drawing board (a ``canvas`` block, V6-12).
NotebookSectionKind = Literal["text", "checklist", "details", "ink"]
NOTEBOOK_SECTION_KINDS: Final[tuple[str, ...]] = get_args(NotebookSectionKind)
#: Who wrote a notebook entry: the agent (``notebook_write``) or the caller (an edit on screen).
NotebookAuthor = Literal["agent", "caller"]
#: A section id, an entry id or key, a checklist item id: letters, digits and ``_.:-``.
NOTEBOOK_ID_PATTERN: Final[str] = r"^[A-Za-z0-9_.:-]{1,64}$"
#: The most sections one notebook has.
MAX_NOTEBOOK_SECTIONS: Final[int] = 12
#: The most entries one text section holds (the agent replaces the section to start over).
MAX_NOTEBOOK_ENTRIES: Final[int] = 100
#: The longest entry of a text section.
MAX_NOTEBOOK_ENTRY_CHARS: Final[int] = 2000
#: The most items of a checklist section, and rows of a details section.
MAX_NOTEBOOK_ITEMS: Final[int] = 30

NotebookId = Annotated[str, Field(pattern=NOTEBOOK_ID_PATTERN)]


def _unique(ids: list[str], what: str) -> None:
    if len(set(ids)) != len(ids):
        raise ValueError(f"{what} must be unique")


class NotebookEntry(BaseModel):
    """One note in a ``text`` section of a notebook (V6-08).

    ``author`` is who wrote it; ``key`` makes a note replaceable in place (the agent
    writing the same key again updates it, like ``Note.key``); ``edited_by`` is
    ``"caller"`` after the caller changed an entry the agent wrote.
    """

    id: NotebookId
    text: str = Field(max_length=MAX_NOTEBOOK_ENTRY_CHARS)
    author: NotebookAuthor = "agent"
    key: NotebookId | None = None
    tone: Tone | None = None
    ts: float
    edited_by: EditAuthor | None = None


class NotebookTextSection(BaseModel):
    """A ``text`` section: running notes, newest last."""

    kind: Literal["text"] = "text"
    entries: list[NotebookEntry] = Field(default=[], max_length=MAX_NOTEBOOK_ENTRIES)

    @field_validator("entries")
    @classmethod
    def _unique_ids(cls, value: list[NotebookEntry]) -> list[NotebookEntry]:
        _unique([entry.id for entry in value], "entry ids")
        _unique([entry.key for entry in value if entry.key is not None], "entry keys")
        return value


class NotebookChecklistSection(BaseModel):
    """A ``checklist`` section: items the agent ticks (``notebook_check``) and, when allowed, the caller."""

    kind: Literal["checklist"] = "checklist"
    items: list[ChecklistItem] = Field(default=[], max_length=MAX_NOTEBOOK_ITEMS)

    @field_validator("items")
    @classmethod
    def _unique_ids(cls, value: list[ChecklistItem]) -> list[ChecklistItem]:
        _unique([item.id for item in value], "item ids")
        return value


class NotebookDetailsSection(BaseModel):
    """A ``details`` section: key-value rows, like a ``details`` card inside the notebook."""

    kind: Literal["details"] = "details"
    items: list[DetailsItem] = Field(default=[], max_length=MAX_NOTEBOOK_ITEMS)

    @field_validator("items")
    @classmethod
    def _unique_keys(cls, value: list[DetailsItem]) -> list[DetailsItem]:
        _unique([item.key for item in value], "row keys")
        return value


class NotebookInkSection(BaseModel):
    """An ``ink`` section: a drawing board (D-V6-16).

    V6-12: ``canvas_block_id`` names the panel's ``canvas`` block the section shows; the worker
    seeds it from the section's config (``NotebookSectionConfig.canvas_block_id``) and nothing
    else writes it. The strokes live in that canvas block's own state (``CanvasBlockState``);
    the console renders the canvas inside the notebook and leaves it out of the panel's flow.
    ``None`` is a section with no board yet ("Drawing board coming soon").
    """

    kind: Literal["ink"] = "ink"
    canvas_block_id: str | None = Field(default=None, max_length=64)


NotebookSection = Annotated[
    NotebookTextSection | NotebookChecklistSection | NotebookDetailsSection | NotebookInkSection,
    Field(discriminator="kind"),
]


class NotebookBlockState(BaseModel):
    """A ``notebook`` block's state (V6-08, D-V6-15): one entry per section, keyed by section id.

    The sections render in the order the block's config lists them (``NotebookBlockConfig.
    sections``); this map carries only their content, so a patch can address one section
    (``/sections/<id>/entries``). Each section's ``kind`` matches its config.
    """

    sections: dict[NotebookId, NotebookSection] = Field(default={}, max_length=MAX_NOTEBOOK_SECTIONS)
    updated_at: float | None = None


# --------------------------------------------------------------------- canvas (V6-12)

#: Who put a mark on a canvas: the caller (a pen stroke sent on :data:`TOPIC_UI_INK`) or the
#: agent (``draw_on_canvas``).
CanvasAuthor = Literal["agent", "caller"]
#: What a caller's stroke was drawn with: ``pen`` and ``highlighter`` are freehand, ``box`` and
#: ``arrow`` are shapes drawn by hand (their first and last points). Erasing is an ``erase``
#: message, never a stroke. A stroke never carries text (D-V6-16, security row 7).
InkTool = Literal["pen", "highlighter", "box", "arrow"]
INK_TOOLS: Final[tuple[str, ...]] = get_args(InkTool)
#: What one :class:`InkMessage` does: ``add`` starts a stroke (or, with the id of one of the
#: caller's strokes, continues it: a long stroke arrives in several messages), ``erase``
#: removes one of the caller's strokes (the eraser, or undo), ``clear`` removes all of them.
InkOp = Literal["add", "erase", "clear"]
#: What the agent draws (``draw_on_canvas``): a ``box``, a ``circle`` (the ellipse inside a box:
#: "circle the dent"), an ``arrow`` (two points), a ``text`` label, or a ``path`` of points.
CanvasShapeKind = Literal["box", "circle", "arrow", "text", "path"]
CANVAS_SHAPE_KINDS: Final[tuple[str, ...]] = get_args(CanvasShapeKind)
#: A stroke or shape id: letters, digits and ``_.:-``.
CANVAS_MARK_ID_PATTERN: Final[str] = r"^[A-Za-z0-9_.:-]{1,64}$"
#: A mark's colour: ``#rrggbb``.
CANVAS_COLOR_PATTERN: Final[str] = r"^#[0-9a-fA-F]{6}$"
#: The caller's default ink, and the agent's default mark-up colour.
DEFAULT_INK_COLOR: Final[str] = "#1f2937"
DEFAULT_SHAPE_COLOR: Final[str] = "#dc2626"
#: What is behind the drawing: nothing, the caller's live camera, or a picture of this session
#: (``asset:<asset id>``: a pinned frame, a gallery picture, an upload).
CANVAS_BACKGROUND_PATTERN: Final[str] = r"^(none|live_camera|asset:[A-Za-z0-9_.:-]{1,128})$"
#: Prefix of an asset background.
CANVAS_ASSET_BACKGROUND_PREFIX: Final[str] = "asset:"
#: The largest :class:`InkMessage` the worker reads (UTF-8 bytes); a longer one is dropped.
MAX_INK_MESSAGE_BYTES: Final[int] = 2048
#: How many ink messages a second the worker takes from the caller (the rest are dropped).
MAX_INK_MESSAGES_PER_S: Final[int] = 20
#: The most points one :class:`InkMessage` carries.
MAX_INK_POINTS_PER_MESSAGE: Final[int] = 128
#: The most points one stroke holds, all its messages together.
MAX_STROKE_POINTS: Final[int] = 1000
#: The most strokes one canvas holds, whatever its config says (D-V6-16).
MAX_CANVAS_STROKES: Final[int] = 2000
#: ``max_strokes`` of a canvas whose config sets none.
DEFAULT_CANVAS_STROKES: Final[int] = 500
#: The most stroke points one canvas holds, and one session takes from the caller in all.
MAX_CANVAS_POINTS: Final[int] = 20_000
MAX_SESSION_INK_POINTS: Final[int] = 50_000
#: The most shapes the agent keeps on one canvas, and the most points of one ``path``.
MAX_CANVAS_SHAPES: Final[int] = 200
MAX_SHAPE_POINTS: Final[int] = 200
#: The longest ``text`` shape, and the longest label on any shape (agent-authored).
MAX_SHAPE_TEXT_CHARS: Final[int] = 200
MAX_SHAPE_LABEL_CHARS: Final[int] = 80
#: The largest drawing snapshot (a PNG the page renders for ``read_canvas``).
MAX_CANVAS_SNAPSHOT_BYTES: Final[int] = 5 * 1024 * 1024
#: ``session_assets.meta.source`` of a stored drawing snapshot (kind ``frame``; set by the
#: worker, never by the page; no new asset kind, so no migration).
CANVAS_SNAPSHOT_SOURCE: Final[str] = "ink"

#: A normalised coordinate or pressure: 0 is the board's left (top) edge, 1 its right (bottom).
Unit = Annotated[float, Field(ge=0, le=1)]
#: One point of a caller's stroke: ``[x, y]`` or ``[x, y, pressure]``, each 0..1.
InkPoint = Annotated[list[Unit], Field(min_length=2, max_length=3)]
#: One point of an agent's shape: ``[x, y]``, 0..1.
ShapePoint = Annotated[list[Unit], Field(min_length=2, max_length=2)]
CanvasMarkId = Annotated[str, Field(pattern=CANVAS_MARK_ID_PATTERN)]
#: How far past the board's edge a box may reach (rounding in the browser).
_EDGE_SLACK: Final[float] = 1e-6


class InkStroke(BaseModel):
    """One of the caller's strokes on a canvas (V6-12), as the worker stored it.

    ``points`` are normalised to the board (``[x, y]`` or ``[x, y, pressure]``); ``width`` is
    in board pixels (``CanvasBlockState.width``). Written by the worker only, from
    :class:`InkMessage` s it accepted; a stroke carries no text.
    """

    id: CanvasMarkId
    author: CanvasAuthor = "caller"
    tool: InkTool = "pen"
    points: list[InkPoint] = Field(min_length=1, max_length=MAX_STROKE_POINTS)
    color: str = Field(default=DEFAULT_INK_COLOR, pattern=CANVAS_COLOR_PATTERN)
    width: float = Field(default=3, ge=0.5, le=40)
    ts: float


class CanvasShape(BaseModel):
    """One mark the agent drew on a canvas (``draw_on_canvas``, V6-12). ``draw_on_canvas``
    upserts shapes by ``id``.

    * ``box`` and ``circle``: ``x``, ``y`` (the top-left corner), ``w`` and ``h``, all 0..1 and
      inside the board (a circle is the ellipse inside that box);
    * ``arrow``: ``points`` — from and to;
    * ``path``: ``points`` — two or more;
    * ``text``: ``x``, ``y`` and ``text``.

    ``label`` is a short caption next to any shape. Both texts are the agent's own words.
    """

    id: CanvasMarkId
    author: CanvasAuthor = "agent"
    kind: CanvasShapeKind
    x: Unit | None = None
    y: Unit | None = None
    w: Unit | None = None
    h: Unit | None = None
    points: list[ShapePoint] = Field(default=[], max_length=MAX_SHAPE_POINTS)
    text: str | None = Field(default=None, max_length=MAX_SHAPE_TEXT_CHARS)
    label: str | None = Field(default=None, max_length=MAX_SHAPE_LABEL_CHARS)
    color: str = Field(default=DEFAULT_SHAPE_COLOR, pattern=CANVAS_COLOR_PATTERN)
    width: float = Field(default=4, ge=0.5, le=40)
    ts: float | None = None

    @model_validator(mode="after")
    def _fits_its_kind(self) -> Self:
        match self.kind:
            case "box" | "circle":
                if self.x is None or self.y is None or self.w is None or self.h is None:
                    raise ValueError(f"a {self.kind} needs x, y, w and h")
                if self.w <= 0 or self.h <= 0:
                    raise ValueError(f"a {self.kind} needs a width and a height above 0")
                if self.x + self.w > 1 + _EDGE_SLACK or self.y + self.h > 1 + _EDGE_SLACK:
                    raise ValueError(f"a {self.kind} must stay inside the board (x + w and y + h at most 1)")
            case "arrow":
                if len(self.points) != 2:
                    raise ValueError("an arrow needs exactly two points: from and to")
            case "path":
                if len(self.points) < 2:
                    raise ValueError("a path needs at least two points")
            case "text":
                if self.x is None or self.y is None or not (self.text or "").strip():
                    raise ValueError("a text mark needs x, y and some text")
        return self


class CanvasBlockState(BaseModel):
    """A ``canvas`` block's state (V6-12, D-V6-16): the caller's strokes and the agent's shapes.

    Every coordinate is normalised to the board (0..1), so ``width`` and ``height`` only fix
    its shape (and the pixel unit of a mark's ``width``). ``background`` is ``none``,
    ``live_camera`` (the marks overlay the caller's camera) or ``asset:<id>`` (a picture of
    this session, e.g. a pinned frame: "circle the dent"). ``strokes`` are written by the
    worker from the caller's :class:`InkMessage` s; ``shapes`` by ``draw_on_canvas``;
    ``snapshot_asset_id`` is the stored PNG ``read_canvas`` last read; ``limit_reached`` says
    the board is full (the caller's next strokes are dropped until it is cleared).
    """

    width: int = Field(default=1600, ge=1, le=10000)
    height: int = Field(default=1200, ge=1, le=10000)
    background: str = Field(default="none", pattern=CANVAS_BACKGROUND_PATTERN)
    strokes: list[InkStroke] = Field(default=[], max_length=MAX_CANVAS_STROKES)
    shapes: list[CanvasShape] = Field(default=[], max_length=MAX_CANVAS_SHAPES)
    snapshot_asset_id: str | None = Field(default=None, max_length=128)
    limit_reached: bool = False
    updated_at: float | None = None

    @field_validator("strokes")
    @classmethod
    def _unique_strokes(cls, value: list[InkStroke]) -> list[InkStroke]:
        _unique([stroke.id for stroke in value], "stroke ids")
        return value

    @field_validator("shapes")
    @classmethod
    def _unique_shapes(cls, value: list[CanvasShape]) -> list[CanvasShape]:
        _unique([shape.id for shape in value], "shape ids")
        return value


class InkMessage(BaseModel):
    """One message on :data:`TOPIC_UI_INK` (V6-12, D-V6-16): a piece of the caller's drawing.

    * ``add`` — ``stroke_id`` and at least one point. A new id starts a stroke (``tool``,
      ``color`` and ``width`` are taken from this first message); the id of a stroke the
      caller already drew continues it, its points appended (a long stroke is sent in pieces).
    * ``erase`` — ``stroke_id`` of one of the caller's strokes, no points.
    * ``clear`` — no points: every stroke of the caller on the block goes.

    At most :data:`MAX_INK_MESSAGE_BYTES` of JSON and :data:`MAX_INK_POINTS_PER_MESSAGE`
    points; the page batches a stroke's points (about every 50 ms) rather than sending each
    one. Strict: an unknown key, text, or a point outside 0..1 makes the whole message dropped.
    """

    v: Literal[1] = 1
    block_id: str = Field(min_length=1, max_length=64)
    stroke_id: str | None = Field(default=None, pattern=CANVAS_MARK_ID_PATTERN)
    op: InkOp = "add"
    tool: InkTool = "pen"
    points: list[InkPoint] = Field(default=[], max_length=MAX_INK_POINTS_PER_MESSAGE)
    color: str = Field(default=DEFAULT_INK_COLOR, pattern=CANVAS_COLOR_PATTERN)
    width: float = Field(default=3, ge=0.5, le=40)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _fits_its_op(self) -> Self:
        match self.op:
            case "add":
                if self.stroke_id is None or not self.points:
                    raise ValueError("an add names the stroke and carries at least one point")
            case "erase":
                if self.stroke_id is None or self.points:
                    raise ValueError("an erase names the stroke and carries no points")
            case "clear":
                if self.points:
                    raise ValueError("a clear carries no points")
        return self


# --------------------------------------------------------------------- next blocks (V6-23)

#: The session event a settled signature records (D-V6-20), as ``consent`` does for consent.
SIGNATURE_EVENT: Final[str] = "signature"
#: The session event the worker records when a timer runs out (``start_timer``).
TIMER_ENDED_EVENT: Final[str] = "timer_ended"
#: ``session_assets.meta.source`` of a stored signature picture (kind ``signature``; set by the
#: worker, never by the page).
SIGNATURE_SOURCE: Final[str] = "signature"
#: The largest signature picture the worker takes (a PNG the page renders when the caller signs).
MAX_SIGNATURE_BYTES: Final[int] = 1024 * 1024


class SignatureBlockState(RequestableState):
    """A ``signature`` block's state (V6-23, D-V6-20): wording the caller signs by hand.

    ``request_signature`` shows ``disclosure_text`` (the block config's wording when it sets
    one, else the agent's) over a small drawing board and waits (``status`` is the request
    lifecycle every requestable block shares). The caller's strokes stay on the page; when the
    caller taps Sign the page answers ``block_submit {values: {signed: true}}`` (``false`` for
    "Not now"), the worker asks for the picture (``UiRequest.method="snapshot"``) and stores the
    PNG as a session asset of kind ``signature``. Only then are ``signed``, ``asset_id``, ``at``
    and ``text_hash`` (the SHA-256 of the wording shown, ``compliance.consent_text_hash``)
    written, by the worker: a browser answer never writes them, and an answer with no request
    waiting is dropped.
    """

    disclosure_text: str = Field(default="", max_length=MAX_CONSENT_TEXT_CHARS)
    signed: bool | None = None
    asset_id: str | None = Field(default=None, max_length=128)
    text_hash: str | None = Field(default=None, pattern=TEXT_HASH_PATTERN)
    at: float | None = None


class SignatureEvent(BaseModel):
    """The ``signature`` session event (V6-23): what was signed, never the picture's bytes.

    ``text_hash`` ties the answer to the exact wording shown; ``asset_id`` names the stored
    picture (kind ``signature``) of a signed request. A declined request is recorded too
    (``signed: false``, no picture).
    """

    block_id: str = Field(min_length=1, max_length=64)
    signed: bool
    text_hash: str = Field(pattern=TEXT_HASH_PATTERN)
    asset_id: str | None = Field(default=None, max_length=128)
    method: Literal["drawn"] = "drawn"


#: What a ``chart`` block draws (D-V6-20): one big ``number``, ``bar`` s, a ``line``, a ``pie``
#: or a ``gauge`` (one value on a scale). The console draws it as inline SVG, no chart library.
ChartKind = Literal["number", "bar", "line", "pie", "gauge"]
CHART_KINDS: Final[tuple[str, ...]] = get_args(ChartKind)
#: The most points one chart holds (a 201st is refused).
MAX_CHART_POINTS: Final[int] = 200
#: The most series (lines, or bar groups) one chart holds.
MAX_CHART_SERIES: Final[int] = 8
#: The longest point label or series name, title, unit and caption.
MAX_CHART_LABEL_CHARS: Final[int] = 40
MAX_CHART_TITLE_CHARS: Final[int] = 120
MAX_CHART_UNIT_CHARS: Final[int] = 16
MAX_CHART_CAPTION_CHARS: Final[int] = 200
#: The largest magnitude of a chart value.
MAX_CHART_VALUE: Final[float] = 1e12

#: A finite chart value within :data:`MAX_CHART_VALUE`.
ChartValue = Annotated[float, Field(ge=-MAX_CHART_VALUE, le=MAX_CHART_VALUE, allow_inf_nan=False)]


class ChartPoint(BaseModel):
    """One value of a chart: its ``label`` (an x value or a slice name) and, for several lines or
    bar groups, its ``series``."""

    label: str = Field(min_length=1, max_length=MAX_CHART_LABEL_CHARS)
    value: ChartValue
    series: str | None = Field(default=None, min_length=1, max_length=MAX_CHART_LABEL_CHARS)


class ChartBlockState(BaseModel):
    """A ``chart`` block's state (``show_chart``, V6-23): display data the agent shows.

    * ``number`` and ``gauge`` show one value (at most one point); a gauge draws it on the scale
      ``gauge_min`` .. ``gauge_max``;
    * ``bar`` and ``line`` draw the points in order, grouped by ``series`` (at most 8);
    * ``pie`` draws one slice per point; its values are 0 or more.

    At most :data:`MAX_CHART_POINTS` points (a 201st is refused), every text capped. The
    agent's own words: ``describe_panel`` reads them back fenced like every block state.
    """

    kind: ChartKind = "bar"
    title: str | None = Field(default=None, max_length=MAX_CHART_TITLE_CHARS)
    unit: str | None = Field(default=None, max_length=MAX_CHART_UNIT_CHARS)
    points: list[ChartPoint] = Field(default=[], max_length=MAX_CHART_POINTS)
    gauge_min: ChartValue = 0
    gauge_max: ChartValue = 100
    caption: str | None = Field(default=None, max_length=MAX_CHART_CAPTION_CHARS)
    updated_at: float | None = None

    @model_validator(mode="after")
    def _fits_its_kind(self) -> Self:
        series = {point.series for point in self.points if point.series is not None}
        if len(series) > MAX_CHART_SERIES:
            raise ValueError(f"a chart shows at most {MAX_CHART_SERIES} series")
        if self.gauge_min >= self.gauge_max:
            raise ValueError("gauge_min must be below gauge_max")
        match self.kind:
            case "number" | "gauge":
                if len(self.points) > 1:
                    raise ValueError(f"a {self.kind} chart shows one value")
                if series:
                    raise ValueError(f"a {self.kind} chart has no series")
            case "pie":
                if series:
                    raise ValueError("a pie chart has no series")
                if any(point.value < 0 for point in self.points):
                    raise ValueError("a pie chart's values are 0 or more")
            case _:
                pass
        return self


#: How a ``timer`` block counts: down to zero, or up from zero.
TimerMode = Literal["countdown", "elapsed"]
TIMER_MODES: Final[tuple[str, ...]] = get_args(TimerMode)
#: Where a timer stands: not started, running, run out (``ended``, the worker ended it) or
#: ``stopped`` by the agent before its end.
TimerStatus = Literal["idle", "running", "ended", "stopped"]
#: The longest timer (4 hours).
MAX_TIMER_SECONDS: Final[int] = 4 * 3600
#: The longest timer label.
MAX_TIMER_LABEL_CHARS: Final[int] = 80


class TimerBlockState(BaseModel):
    """A ``timer`` block's state (``start_timer``, V6-23).

    ``countdown`` shows the time left until ``duration_s`` has passed since ``started_at``;
    ``elapsed`` shows the time since ``started_at`` and ends after ``duration_s`` too (its cap).
    The page counts on its own clock from the moment it sees ``running`` (``duration_s`` minus
    what has passed), so a clock difference between the worker and the browser does not matter;
    ``ends_at`` is the worker's own end time. When the time is up the worker writes ``ended``
    (and ``ended_at``), records a ``timer_ended`` session event and tells the model in one line.
    """

    mode: TimerMode = "countdown"
    label: str | None = Field(default=None, max_length=MAX_TIMER_LABEL_CHARS)
    status: TimerStatus = "idle"
    duration_s: int | None = Field(default=None, ge=1, le=MAX_TIMER_SECONDS)
    started_at: float | None = None
    ends_at: float | None = None
    ended_at: float | None = None

    @model_validator(mode="after")
    def _running_has_times(self) -> Self:
        if self.status == "running" and (
            self.duration_s is None or self.started_at is None or self.ends_at is None
        ):
            raise ValueError("a running timer has duration_s, started_at and ends_at")
        return self


#: The longest code a ``code`` block shows, whatever its config says.
MAX_CODE_CHARS: Final[int] = 20_000
#: A code block's language label (display only: nothing is ever run): ``python``, ``c++``, ``c#``,
#: ``json``, ``shell``.
CODE_LANGUAGE_PATTERN: Final[str] = r"^[a-z0-9][a-z0-9+#._-]{0,23}$"
#: The longest code block title.
MAX_CODE_TITLE_CHARS: Final[int] = 120


class CodeBlockState(BaseModel):
    """A ``code`` block's state (``show_code``, V6-23): read-only code or text in a fixed-width font.

    Display only: never run, never a link. ``language`` is a label (the console may colour
    by it); the console renders ``code`` as the text of a code block, never as Markdown or HTML.
    """

    code: str = Field(default="", max_length=MAX_CODE_CHARS)
    language: str | None = Field(default=None, pattern=CODE_LANGUAGE_PATTERN)
    title: str | None = Field(default=None, max_length=MAX_CODE_TITLE_CHARS)
    updated_at: float | None = None


#: An ISO 4217 currency code (``USD``, ``EUR``, ``INR``).
CURRENCY_PATTERN: Final[str] = r"^[A-Z]{3}$"
#: The most lines and adjustments (discount, tax, delivery) one cart holds.
MAX_CART_LINES: Final[int] = 50
MAX_CART_ADJUSTMENTS: Final[int] = 5
#: The most of one line, and the largest price or adjustment.
MAX_CART_QUANTITY: Final[int] = 9999
MAX_CART_AMOUNT: Final[float] = 1e9
#: The longest line name or note, and adjustment label.
MAX_CART_NAME_CHARS: Final[int] = 120
MAX_CART_LABEL_CHARS: Final[int] = 40
#: How far a stored total may be from the sum the platform computes (float rounding).
_CART_TOLERANCE: Final[float] = 0.005

#: A finite money amount within :data:`MAX_CART_AMOUNT` either way.
CartAmount = Annotated[float, Field(ge=-MAX_CART_AMOUNT, le=MAX_CART_AMOUNT, allow_inf_nan=False)]


class CartLine(BaseModel):
    """One line of a cart: what, how many, the price of one, and the line's total (computed)."""

    id: str = Field(pattern=NOTEBOOK_ID_PATTERN)
    name: str = Field(min_length=1, max_length=MAX_CART_NAME_CHARS)
    quantity: int = Field(default=1, ge=1, le=MAX_CART_QUANTITY)
    unit_price: float = Field(ge=0, le=MAX_CART_AMOUNT, allow_inf_nan=False)
    line_total: float = Field(default=0, ge=0, allow_inf_nan=False)
    note: str | None = Field(default=None, max_length=MAX_CART_NAME_CHARS)


class CartAdjustment(BaseModel):
    """A line under the subtotal: a discount (negative), a tax, a delivery fee."""

    label: str = Field(min_length=1, max_length=MAX_CART_LABEL_CHARS)
    amount: CartAmount


def _money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def cart_totals(lines: list[CartLine], adjustments: list[CartAdjustment]) -> tuple[list[float], float, float]:
    """The line totals, subtotal and total of a cart, to the cent (V6-23).

    The one computation every writer uses (``cart_set``, ``update_block``), so a cart's totals
    always add up: ``line_total = quantity times unit_price``, ``subtotal`` their sum, ``total`` the
    subtotal plus every adjustment.
    """
    line_totals = [_money(Decimal(line.quantity) * Decimal(str(line.unit_price))) for line in lines]
    subtotal = sum((Decimal(str(value)) for value in line_totals), Decimal(0))
    total = subtotal + sum((Decimal(str(a.amount)) for a in adjustments), Decimal(0))
    return line_totals, _money(subtotal), _money(total)


class CartBlockState(BaseModel):
    """A ``cart`` block's state (``cart_set``, V6-23): lines and totals the agent shows.

    Display data: an order is placed by the agent's own tools, never by this block. The totals
    must add up (:func:`cart_totals`), whoever writes the state, so the caller never sees a
    total that is not the sum of the lines; the writers compute them.
    """

    currency: str = Field(default="USD", pattern=CURRENCY_PATTERN)
    lines: list[CartLine] = Field(default=[], max_length=MAX_CART_LINES)
    adjustments: list[CartAdjustment] = Field(default=[], max_length=MAX_CART_ADJUSTMENTS)
    subtotal: float = Field(default=0, allow_inf_nan=False)
    total: float = Field(default=0, allow_inf_nan=False)
    updated_at: float | None = None

    @field_validator("lines")
    @classmethod
    def _unique_lines(cls, value: list[CartLine]) -> list[CartLine]:
        _unique([line.id for line in value], "line ids")
        return value

    @model_validator(mode="after")
    def _totals_add_up(self) -> Self:
        line_totals, subtotal, total = cart_totals(self.lines, self.adjustments)
        for line, expected in zip(self.lines, line_totals, strict=True):
            if abs(line.line_total - expected) > _CART_TOLERANCE:
                raise ValueError(f"line {line.id!r}: line_total must be quantity times price ({expected})")
        if abs(self.subtotal - subtotal) > _CART_TOLERANCE:
            raise ValueError(f"subtotal must be the sum of the line totals ({subtotal})")
        if abs(self.total - total) > _CART_TOLERANCE:
            raise ValueError(f"total must be the subtotal plus the adjustments ({total})")
        return self


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
    * ``snapshot`` — ``{block_id}`` (V6-12, D-V6-16): draw the ``canvas`` block as it shows
      (background, strokes and shapes) into a PNG of at most :data:`MAX_CANVAS_SNAPSHOT_BYTES`
      and send it on ``lkap.ui.upload`` with the attributes ``block_id`` (the canvas) and
      ``name``; ack at once with ``{}``. The worker takes the file only while it is waiting for
      that snapshot and only for a canvas the caller may draw on. V6-23: also ``{block_id}`` of a
      ``signature`` block right after the caller tapped Sign: the page renders the signature it
      holds (the strokes never leave the page before that) as a PNG of at most
      :data:`MAX_SIGNATURE_BYTES`, stored as a session asset of kind ``signature``.
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
        "snapshot",
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


# --------------------------------------------------------------------- caller edits (V6-06)

#: The ``block_action`` name a caller's edit of a block is sent with (D-V6-19).
BLOCK_EDIT_ACTION: Final[str] = "edit"
#: The block types a caller may edit when the block's config allows it: a ``details`` value
#: (:class:`DetailsEdit`) and a ``checklist`` tick (:class:`ChecklistEdit`) with
#: ``caller_can_edit``; a ``notebook`` note, tick or value (:class:`NotebookEdit`) with
#: ``caller_can_write`` (V6-08). An ``edit`` on any other built-in block (requestable, link,
#: consent, upload, captions, handoff and layout blocks included) is refused; a ``custom``
#: block's actions stay the pack's.
EDITABLE_BLOCK_TYPES: Final[frozenset[str]] = frozenset({"details", "checklist", "notebook"})
#: The config key that lets the caller edit a block of each editable type.
CALLER_EDIT_FLAGS: Final[dict[str, str]] = {
    "details": "caller_can_edit",
    "checklist": "caller_can_edit",
    "notebook": "caller_can_write",
}
#: The longest value a caller may type into a block.
MAX_CALLER_EDIT_CHARS: Final[int] = 500


class DetailsEdit(BaseModel):
    """``block_action {name: "edit", data}`` on a ``details`` block (V6-06).

    Changes the value of a row already on the card (``key``); the caller cannot add
    rows or change labels. An empty ``value`` clears it. ``key`` has the id shape (V6-21,
    S6-7): an odd value is refused by the contract, never echoed into a log line or an event.
    """

    key: str = Field(pattern=NOTEBOOK_ID_PATTERN)
    value: str = Field(max_length=MAX_CALLER_EDIT_CHARS)

    model_config = ConfigDict(extra="forbid")


class ChecklistEdit(BaseModel):
    """``block_action {name: "edit", data}`` on a ``checklist`` block (V6-06): tick or untick an item."""

    item_id: str = Field(pattern=NOTEBOOK_ID_PATTERN)
    done: bool

    model_config = ConfigDict(extra="forbid")


class NotebookEdit(BaseModel):
    """``block_action {name: "edit", data}`` on a ``notebook`` block (V6-08).

    Always names the section (``section_id``); then exactly one change, matching the
    section's kind:

    * a ``text`` section: ``{text}`` adds the caller's own note; ``{entry_id, text}``
      changes a note (an empty ``text`` removes it);
    * a ``checklist`` section: ``{item_id, done}`` ticks or unticks an item;
    * a ``details`` section: ``{key, value}`` changes the value of a row already there
      (an empty ``value`` clears it).

    An ``ink`` section is never edited this way (strokes travel on their own stream, V6-12).
    """

    section_id: str = Field(pattern=NOTEBOOK_ID_PATTERN)
    entry_id: str | None = Field(default=None, pattern=NOTEBOOK_ID_PATTERN)
    text: str | None = Field(default=None, max_length=MAX_CALLER_EDIT_CHARS)
    item_id: str | None = Field(default=None, pattern=NOTEBOOK_ID_PATTERN)
    done: bool | None = None
    key: str | None = Field(default=None, pattern=NOTEBOOK_ID_PATTERN)
    value: str | None = Field(default=None, max_length=MAX_CALLER_EDIT_CHARS)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _one_change(self) -> Self:
        note = self.text is not None and self.item_id is None and self.done is None and self.key is None
        tick = self.item_id is not None and self.done is not None
        row = self.key is not None and self.value is not None
        others_empty = {
            "note": self.value is None,
            "tick": self.text is None and self.entry_id is None and self.key is None and self.value is None,
            "row": self.text is None and self.entry_id is None and self.item_id is None and self.done is None,
        }
        shapes = [
            name for name, ok in (("note", note), ("tick", tick), ("row", row)) if ok and others_empty[name]
        ]
        if len(shapes) != 1:
            raise ValueError(
                "an edit names the section and one change: {text} or {entry_id, text} for notes, "
                "{item_id, done} for a checklist, {key, value} for a details row"
            )
        return self

    @property
    def change(self) -> Literal["note", "tick", "row"]:
        """Which of the three changes this edit is."""
        if self.item_id is not None:
            return "tick"
        if self.key is not None:
            return "row"
        return "note"


class AgentAction(BaseModel):
    """RPC payload for ``lkap.agent.action`` (browser asks the agent to do something).

    Payload keys per action:

    * ``get_snapshot`` — ``{}``
    * ``set_video_source`` — ``{source: "camera" | "screen" | "none"}``
    * ``ui_action`` — ``{name, data}`` → ``Pack.on_ui_action``
    * ``form_submit`` — ``{block_id, values}`` or ``{block_id, cancelled: true}``
      (``form`` blocks only; kept for one release beside ``block_submit``)
    * ``block_action`` — ``{block_id, name, data}`` → ``Pack.on_block_action``. V6-06:
      ``name == "edit"`` on a block of :data:`EDITABLE_BLOCK_TYPES` whose config sets
      ``caller_can_edit`` is checked (:class:`DetailsEdit`, :class:`ChecklistEdit`), applied
      and told to the model before the pack sees it; any other built-in block refuses it.
      V6-08: a ``notebook`` block with ``caller_can_write`` takes :class:`NotebookEdit`
    * ``rewind`` / ``inject_user_text`` — the text-session actions (V2-18)
    * ``block_submit`` — ``{block_id, values}`` or ``{block_id, cancelled: true}``
      (V5-02): the answer to a ``request`` (or a ``form``) on any requestable block
    * ``state_delta`` — ``{delta: [...]}`` (V5-43): an AG-UI ``STATE_DELTA`` (RFC 6902
      operations, :mod:`lkap_contracts.ui_agui`) on ``/blocks/<id>/...`` paths of blocks
      ``update_block`` may write (:data:`lkap_contracts.tools.UPDATABLE_BLOCK_TYPES`) less
      ``kb_citations`` and ``custom``, applied all or nothing through the block validators
      (:class:`StateDeltaPayload`); refused unless the agent's ``PanelLayout.accept_state_delta``
      is on (default off)
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
        "state_delta",
    ]
    payload: dict[str, Any] = {}


class AgentActionResult(BaseModel):
    """Result the agent returns for an :class:`AgentAction`."""

    ok: bool
    payload: dict[str, Any] = {}
    error: str | None = None


#: The most operations one ``state_delta`` action may carry.
MAX_STATE_DELTA_OPS: Final[int] = 100


class StateDeltaPayload(BaseModel):
    """``AgentAction.payload`` for ``action == "state_delta"`` (V5-43).

    ``delta`` is an AG-UI ``STATE_DELTA`` event's ``delta``: RFC 6902 operations
    (``add``, ``remove``, ``replace``, ``move``, ``copy``, ``test``) whose paths
    are JSON Pointers into :class:`UiState`. The worker refuses it unless the agent's
    ``PanelLayout.accept_state_delta`` is on (default off). Only ``/blocks/<id>/...``
    paths of blocks whose type ``update_block`` may write (less ``kb_citations`` and
    ``custom``) are accepted; the whole delta is
    refused when one operation is (a failed ``test`` included). ``type`` may be
    sent as ``"STATE_DELTA"`` so a whole AG-UI event can be forwarded as is.
    """

    type: Literal["STATE_DELTA"] | None = None
    delta: list[dict[str, Any]] = Field(min_length=1, max_length=MAX_STATE_DELTA_OPS)

    model_config = ConfigDict(extra="ignore")


class LinkHookIn(BaseModel):
    """Body of ``POST /v1/hooks/link/{session_id}`` (V5-43): a link's outcome.

    The business's own system (the one that received the payment provider's or
    the e-sign vendor's webhook) sends it, signed with ``X-LKAP-Signature`` like
    LKAP's outbound webhooks (``t=<unix>,v1=<hex HMAC-SHA256 of "<t>.<body>">``)
    using the signing secret of one of the workspace's webhook endpoints. It
    names the block, or the ``reference`` the link was sent with.
    """

    block_id: str | None = Field(default=None, min_length=1, max_length=64)
    reference: str | None = Field(default=None, pattern=LINK_REFERENCE_PATTERN)
    status: LinkOutcome = "completed"

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _names_the_link(self) -> Self:
        if self.block_id is None and self.reference is None:
            raise ValueError("name the link with block_id or reference")
        return self


class LinkHookOut(BaseModel):
    """What the link hook answers: the packet id and how many agents received it."""

    id: str
    session_id: str
    status: LinkOutcome
    delivered_to: int = Field(ge=0)


class LinkCompletedPacket(BaseModel):
    """The data packet the api sends the worker on :data:`TOPIC_UI_LINK` (V5-43).

    The worker applies it to the ``link`` block it names (by ``block_id`` or
    ``reference``) only while that block is ``pending`` or ``opened``, so a
    repeated delivery changes nothing; it then tells the model in a background
    message.
    """

    v: Literal[1] = 1
    op: Literal["link_completed"] = "link_completed"
    id: str = Field(min_length=1, max_length=64)
    session_id: str = Field(min_length=1, max_length=64)
    block_id: str | None = Field(default=None, max_length=64)
    reference: str | None = Field(default=None, pattern=LINK_REFERENCE_PATTERN)
    status: LinkOutcome = "completed"


class UiSnapshotRequestPacket(BaseModel):
    """A server-sent request for a fresh ``lkap.ui.state`` snapshot (asks #252, V5-43).

    Sent by the api with the server API on the supervisor topic
    (``lkap_contracts.api_models.SUPERVISOR_TOPIC``) when a hidden listener joins
    mid-call: a listener cannot call ``get_snapshot`` over RPC. The worker honours
    it only when the server sent it and it names the worker's session.
    """

    v: Literal[1] = 1
    op: Literal["snapshot"] = "snapshot"
    session_id: str = Field(min_length=1, max_length=64)
