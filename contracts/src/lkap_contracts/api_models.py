"""Request and response models shared by the api, the worker and the web codegen.

Every model here is exported to JSON Schema and TypeScript; the console and the
session page use the generated types rather than hand-written interfaces.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from lkap_contracts.agent_config import (
    AgentConfig,
    CapabilitiesConfig,
    PipelineMode,
)
from lkap_contracts.packs import PackManifest
from lkap_contracts.providers import ProviderSpec
from lkap_contracts.tools import ToolDefinition
from lkap_contracts.ui_protocol import UiState


# --------------------------------------------------------------------------- shared
class Page[T](BaseModel):
    """A non-cursor list envelope returned by every list endpoint."""

    items: list[T]
    total: int


class ErrorBody(BaseModel):
    """The body of an LKAP error response."""

    code: str
    message: str
    details: object | None = None


class ErrorResponse(BaseModel):
    """Envelope for all 4xx/5xx responses."""

    error: ErrorBody


# ------------------------------------------------------------------------ providers
class ProvidersResponse(BaseModel):
    """``GET /v1/providers``."""

    v: Literal[1] = 1
    providers: list[ProviderSpec]


# ---------------------------------------------------------------------- credentials
class CredentialCreate(BaseModel):
    """``POST /v1/credentials`` — secret values are write-only and never returned."""

    provider_id: str
    label: str
    secrets: dict[str, str]


class CredentialUpdate(BaseModel):
    """``PUT /v1/credentials/{id}`` — omitting ``secrets`` keeps the stored values."""

    provider_id: str | None = None
    label: str | None = None
    secrets: dict[str, str] | None = None


class CredentialOut(BaseModel):
    """A stored credential, without any secret material."""

    id: str
    provider_id: str
    label: str
    fingerprint: str
    created_at: datetime
    updated_at: datetime


class CredentialTestResult(BaseModel):
    """``POST /v1/credentials/{id}/test``."""

    ok: bool
    message: str


# --------------------------------------------------------------------------- agents
class AgentCreate(BaseModel):
    """``POST /v1/agents``. ``config=None`` seeds from the pack manifest."""

    name: str
    description: str = ""
    pack_id: str = "generic"
    ui_panel_id: str | None = None
    config: AgentConfig | None = None


class AgentUpdate(BaseModel):
    """``PUT /v1/agents/{id}`` — a partial update; omitted fields are unchanged."""

    name: str | None = None
    description: str | None = None
    ui_panel_id: str | None = None
    config: AgentConfig | None = None
    published: bool | None = None


class AgentOut(BaseModel):
    """Full agent record (admin only — ``config`` may reference credential ids)."""

    id: str
    slug: str
    name: str
    description: str
    pack_id: str
    ui_panel_id: str
    published: bool
    config: AgentConfig
    config_version: int
    created_at: datetime
    updated_at: datetime


class AgentPublicOut(BaseModel):
    """What an unauthenticated browser may see about a published agent."""

    id: str
    slug: str
    name: str
    description: str
    ui_panel_id: str
    capabilities: CapabilitiesConfig
    pipeline_mode: PipelineMode


class ValidationResult(BaseModel):
    """``POST /v1/agents/{id}/validate``."""

    ok: bool
    errors: list[str] = []
    warnings: list[str] = []


# -------------------------------------------------------------------------- connect
class ConnectRequest(BaseModel):
    """``POST /v1/agents/{id_or_slug}/connect`` — any ``roomConfig`` is ignored."""

    participant_name: str = "Guest"
    participant_identity: str | None = None
    participant_metadata: dict[str, str] = {}


class ConnectResponse(BaseModel):
    """LiveKit ``TokenSourceResponse``-compatible connection details (camelCase)."""

    serverUrl: str
    participantToken: str
    roomName: str
    participantName: str
    sessionId: str
    agent: AgentPublicOut
    uiPanelId: str
    protocolVersion: Literal[1] = 1


# ---------------------------------------------------------------------------- tools
class ToolCreate(BaseModel):
    """``POST /v1/tools``. ``agent_id=None`` makes the tool shared."""

    agent_id: str | None = None
    kind: Literal["http", "mcp"]
    name: str
    definition: ToolDefinition
    enabled: bool = True


class ToolOut(ToolCreate):
    """A stored tool row."""

    id: str
    created_at: datetime
    updated_at: datetime


class ToolDryRunRequest(BaseModel):
    """``POST /v1/tools/{id}/dry-run`` (http tools only)."""

    arguments: dict[str, Any] = {}


class ToolDryRunResult(BaseModel):
    """Outcome of a dry run, shown in the console tool editor."""

    ok: bool
    result: str
    status_code: int | None = None
    duration_ms: int


# ------------------------------------------------------------------ knowledge bases
class KbCreate(BaseModel):
    """``POST /v1/knowledge-bases``."""

    name: str
    description: str = ""
    embedder_id: str = "fastembed-embedding"


class KbOut(BaseModel):
    """A knowledge base with its current counts."""

    id: str
    name: str
    description: str
    embedder_id: str
    chunk_count: int
    document_count: int
    created_at: datetime
    updated_at: datetime


class KbDocumentOut(BaseModel):
    """An uploaded document and its ingestion status."""

    id: str
    kb_id: str
    filename: str
    mime: str
    bytes: int
    status: Literal["pending", "ready", "failed"]
    error: str | None = None
    chunk_count: int
    created_at: datetime


class KbSearchRequest(BaseModel):
    """``POST /v1/knowledge-bases/{id}/search``."""

    query: str
    k: int = Field(4, ge=1, le=20)


class KbHit(BaseModel):
    """One retrieved chunk."""

    chunk_id: str
    document_id: str
    filename: str
    score: float
    text: str


class KbSearchResponse(BaseModel):
    """Search results, best first."""

    hits: list[KbHit]


# ---------------------------------------------------------------------------- packs
class PackOut(BaseModel):
    """One discovered pack."""

    manifest: PackManifest


class PacksResponse(BaseModel):
    """``GET /v1/packs``."""

    items: list[PackOut]


# ------------------------------------------------------------------------- sessions
class TranscriptTurn(BaseModel):
    """One turn of the final transcript (images stripped)."""

    role: Literal["user", "assistant"]
    text: str
    ts: float
    interrupted: bool = False


class SessionOut(BaseModel):
    """Session list row."""

    id: str
    agent_id: str
    agent_name: str
    config_version: int
    room_name: str
    status: Literal["created", "active", "ended", "failed"]
    pipeline_mode: PipelineMode
    created_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None
    usage: dict[str, Any] | None = None
    error: str | None = None


class SessionDetailOut(SessionOut):
    """Session detail, including the transcript and the final UI state."""

    transcript: list[TranscriptTurn] | None = None
    final_ui_state: UiState | None = None


class SessionEventOut(BaseModel):
    """One row of the session event timeline."""

    id: int
    ts: datetime
    type: str
    payload: dict[str, Any]


# ------------------------------------------------------------------------- internal
class SessionEventIn(BaseModel):
    """An event posted by the worker."""

    ts: float
    type: str
    payload: dict[str, Any]


class SessionEventsIn(BaseModel):
    """``POST /internal/v1/sessions/{id}/events``."""

    events: list[SessionEventIn]


class SessionSummaryIn(BaseModel):
    """``PUT /internal/v1/sessions/{id}/summary`` — posted from a shutdown callback."""

    status: Literal["ended", "failed"]
    usage: dict[str, Any]
    transcript: list[TranscriptTurn]
    final_ui_state: UiState | None = None
    error: str | None = None


class InternalKbSearchRequest(BaseModel):
    """``POST /internal/v1/kb/search`` (service token)."""

    kb_ids: list[str]
    query: str
    k: int = Field(4, ge=1, le=20)


# --------------------------------------------------------------------------- health
class HealthResponse(BaseModel):
    """``GET /v1/health``."""

    ok: bool
    version: str
    livekit_url: str
    packs: list[str]
    db: Literal["ok", "error"]


# Concrete page parametrisations exported to JSON Schema / TypeScript.
CredentialPage = Page[CredentialOut]
AgentPage = Page[AgentOut]
ToolPage = Page[ToolOut]
KbPage = Page[KbOut]
KbDocumentPage = Page[KbDocumentOut]
SessionPage = Page[SessionOut]
SessionEventPage = Page[SessionEventOut]
