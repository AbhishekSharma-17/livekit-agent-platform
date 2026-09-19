"""Agent to UI protocol: the state envelope, patch messages, activity and RPC payloads.

The agent owns a :class:`UiState` envelope per session. It publishes a
:class:`UiSnapshot` on session start (``seq == 1``) and a :class:`UiPatch` for every
subsequent change. The browser applies patches strictly in ``seq`` order and asks
for a fresh snapshot over RPC when it sees a gap.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

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


class UiState(BaseModel):
    """The platform envelope every panel renders; ``custom`` is pack-defined."""

    v: Literal[1] = 1
    status: StatusStamp | None = None
    progress: int | None = None
    notes: list[Note] = []
    checklist: list[ChecklistItem] = []
    assets: list[AssetRef] = []
    activity: list[ActivityEvent] = []
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
    """RPC payload for ``lkap.ui.request`` (agent asks the browser to do something)."""

    v: Literal[1] = 1
    method: Literal["open_dialog", "focus", "request_video_source", "toast"]
    payload: dict[str, Any] = {}


class UiRequestResult(BaseModel):
    """Result the browser returns for a :class:`UiRequest`."""

    ok: bool
    payload: dict[str, Any] = {}


class AgentAction(BaseModel):
    """RPC payload for ``lkap.agent.action`` (browser asks the agent to do something)."""

    v: Literal[1] = 1
    action: Literal["get_snapshot", "set_video_source", "ui_action"]
    payload: dict[str, Any] = {}


class AgentActionResult(BaseModel):
    """Result the agent returns for an :class:`AgentAction`."""

    ok: bool
    payload: dict[str, Any] = {}
    error: str | None = None
