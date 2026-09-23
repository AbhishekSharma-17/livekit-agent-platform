"""Request and response models shared by the api, the worker and the web codegen.

Every model here is exported to JSON Schema and TypeScript; the console and the
session page use the generated types rather than hand-written interfaces.
"""

import re
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints, field_validator

from lkap_contracts.agent_config import (
    AgentConfig,
    AgentLimits,
    CapabilitiesConfig,
    PanelLayout,
    PipelineMode,
)
from lkap_contracts.common import Issue as Issue
from lkap_contracts.common import SessionChannel as SessionChannel
from lkap_contracts.common import Severity as Severity
from lkap_contracts.connections import (
    ConnectionCapabilities,
    DeploymentMode,
    DeploymentType,
)
from lkap_contracts.flow import FlowSpec
from lkap_contracts.packs import PackManifest
from lkap_contracts.pricing import Unit as Unit
from lkap_contracts.providers import CatalogKind, ProviderSpec
from lkap_contracts.telephony import DTMF_PATTERN, E164_PATTERN, TRANSFER_TARGET_PATTERN
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
class ProviderOut(ProviderSpec):
    """A registry entry plus this workspace's settings (``GET /v1/providers``)."""

    enabled: bool = True
    installed_on: list[str] = Field(default_factory=list)
    default_credential_id: str | None = None


class ProvidersResponse(BaseModel):
    """``GET /v1/providers``.

    ``v=2`` (V2-06): ``providers`` carries the enriched :class:`ProviderOut`
    (a superset of ``ProviderSpec`` with this workspace's ``enabled``,
    ``installed_on`` and ``default_credential_id``), not the bare registry
    entry.
    """

    v: Literal[1, 2] = 2
    providers: list[ProviderOut]


class ProviderSettingsIn(BaseModel):
    """``PUT /v1/providers/{id}/settings``."""

    enabled: bool = True
    default_credential_id: str | None = None


class CatalogItem(BaseModel):
    """One model, voice, avatar or persona listed by a vendor catalog adapter."""

    id: str
    label: str
    meta: dict[str, Any] = {}


class CatalogResponse(BaseModel):
    """``GET /v1/providers/{id}/catalog``.

    ``error`` (V2-06, additive) carries a short, secret-free note when the
    live vendor call failed and ``items``/``source`` fell back to a cached or
    static list instead — the endpoint always returns ``200`` (CONTRACTS-V2
    "a failed vendor call must never break the providers page").
    """

    kind: CatalogKind
    items: list[CatalogItem] = []
    fetched_at: datetime | None = None
    source: Literal["vendor", "static"] = "static"
    error: str | None = None


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
    checked_at: datetime | None = None
    catalog_preview: list[CatalogItem] | None = None


# --------------------------------------------------------------------------- agents
class AgentCreate(BaseModel):
    """``POST /v1/agents``. ``config=None`` seeds from the pack manifest."""

    name: str
    description: str = ""
    pack_id: str = "generic"
    ui_panel_id: str | None = None
    config: AgentConfig | None = None
    connection_id: str | None = None
    mode: Literal["prompt", "flow"] = "prompt"


class AgentUpdate(BaseModel):
    """``PUT /v1/agents/{id}`` — a partial update; omitted fields are unchanged."""

    name: str | None = None
    description: str | None = None
    ui_panel_id: str | None = None
    config: AgentConfig | None = None
    published: bool | None = None
    connection_id: str | None = None
    mode: Literal["prompt", "flow"] | None = None
    limits: AgentLimits | None = None
    allowed_origins: list[str] | None = None


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
    workspace_id: str = ""
    connection_id: str | None = None
    mode: Literal["prompt", "flow"] = "prompt"
    archived_at: datetime | None = None
    limits: AgentLimits = AgentLimits()
    allowed_origins: list[str] = []
    session_count: int | None = None
    last_session_at: datetime | None = None


class AgentPublicOut(BaseModel):
    """What an unauthenticated browser may see about a published agent.

    ``panel`` (R-V2-7, CONTRACTS-V2 §4.4 "Layout delivery") is the
    *effective* layout — ``lkap_api.panels.effective_layout(agent, pack)`` —
    not necessarily ``AgentConfig.panel`` verbatim: it carries the block list
    the session actually renders, resolved the same way for ``connect`` and
    for the worker's ``/internal/v1/sessions/{id}/resolved``. ``ui_panel_id``
    stays as the mirror of ``panel.panel_id`` for one release.
    """

    id: str
    slug: str
    name: str
    description: str
    ui_panel_id: str
    panel: PanelLayout
    capabilities: CapabilitiesConfig
    pipeline_mode: PipelineMode


class ValidationResult(BaseModel):
    """``POST /v1/agents/{id}/validate``.

    ``errors``/``warnings`` are the flat v1 lists; ``issues`` carries the same
    findings with a ``path`` so the console can focus the offending field
    (UI_UX_SPEC §7.14).
    """

    ok: bool
    errors: list[str] = []
    warnings: list[str] = []
    issues: list[Issue] = []


class ConfigVersionOut(BaseModel):
    """One row of ``GET /v1/agents/{id}/versions``."""

    config_version: int
    created_at: datetime
    created_by: str | None = None
    note: str | None = None
    config: AgentConfig | None = None


class FlowValidateRequest(BaseModel):
    """``POST /v1/agents/{id}/flow/validate``."""

    flow: FlowSpec


class NodeSpecSchema(BaseModel):
    """The JSON schema of one flow node kind, for the flow builder's forms."""

    kind: str
    label: str
    json_schema: dict[str, Any] = {}


class NodeSpecsResponse(BaseModel):
    """``GET /v1/flows/node-specs``."""

    v: Literal[1] = 1
    nodes: list[NodeSpecSchema] = []


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


class SessionLatency(BaseModel):
    """Per-session latency percentiles collected from ``metrics_collected``."""

    eou_to_first_audio_ms_p50: float | None = None
    eou_to_first_audio_ms_p95: float | None = None
    llm_ttft_ms_p50: float | None = None
    llm_ttft_ms_p95: float | None = None
    tts_ttfb_ms_p50: float | None = None
    tts_ttfb_ms_p95: float | None = None
    turns: int = 0


class CostLine(BaseModel):
    """One priced usage line of a session.

    ``cost_usd`` is ``None`` with ``note="no price"`` when the price table has
    no entry — an unknown price is never reported as zero.
    """

    provider_id: str
    model: str | None = None
    unit: Unit
    quantity: Decimal
    unit_price_usd: Decimal | None = None
    cost_usd: Decimal | None = None
    note: str | None = None


class SessionCost(BaseModel):
    """The cost block of ``SessionDetailOut``."""

    total_usd: Decimal | None = None
    lines: list[CostLine] = []


class QaOut(BaseModel):
    """LLM-judge scoring of a finished session."""

    status: Literal["pending", "done", "failed", "skipped"] = "pending"
    score: int | None = None
    sentiment: Literal["positive", "neutral", "negative"] | None = None
    tags: list[str] = []
    summary: str | None = None
    scored_at: datetime | None = None
    model: str | None = None
    scored_by: Literal["worker", "api"] | None = None


class RecordingOut(BaseModel):
    """The recording block of ``SessionDetailOut`` (``url`` is a signed URL)."""

    status: Literal["none", "requested", "active", "ready", "failed"] = "none"
    url: str | None = None
    duration_s: float | None = None
    expires_at: datetime | None = None


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
    channel: SessionChannel = "web"
    connection_id: str | None = None
    cost_usd: Decimal | None = None
    disposition: str | None = None


class SessionDetailOut(SessionOut):
    """Session detail, including the transcript and the final UI state."""

    transcript: list[TranscriptTurn] | None = None
    final_ui_state: UiState | None = None
    caller: dict[str, Any] | None = None
    recording: RecordingOut = RecordingOut()
    cost: SessionCost = SessionCost()
    latency: SessionLatency = SessionLatency()
    qa: QaOut | None = None
    variables: dict[str, Any] = {}


class AnalyticsBucket(BaseModel):
    """One day or one agent in ``AnalyticsSummary``."""

    key: str
    sessions: int = 0
    minutes: float = 0.0
    cost_usd: Decimal | None = None
    failed: int = 0


class AnalyticsSummary(BaseModel):
    """``GET /v1/analytics/summary``."""

    sessions: int = 0
    minutes: float = 0.0
    cost_usd: Decimal | None = None
    failed: int = 0
    by_day: list[AnalyticsBucket] = []
    by_agent: list[AnalyticsBucket] = []


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
    #: R-V2-8: a flow session's end-node disposition and extracted variables
    #: (from ``FlowState``); prompt agents send the defaults.
    disposition: str | None = None
    variables: dict[str, Any] = {}


class InternalKbSearchRequest(BaseModel):
    """``POST /internal/v1/kb/search`` (service token)."""

    kb_ids: list[str]
    query: str
    k: int = Field(4, ge=1, le=20)


# --------------------------------------------------------------------------- health
class HealthResponse(BaseModel):
    """``GET /v1/health``.

    ``db`` is ``ok`` only when the database answers *and* its Alembic revision
    equals the migration head, so an un-migrated or half-migrated schema reports
    ``error``. ``agents_unbound`` counts agents with no LiveKit connection
    (CONTRACTS-V2 §1.3); it should be 0 once bootstrap has run.
    """

    ok: bool
    version: str
    livekit_url: str
    packs: list[str]
    db: Literal["ok", "error"]
    agents_unbound: int = 0


# ----------------------------------------------------------------------- auth/team
class UserOut(BaseModel):
    """A platform user, without any credential material."""

    id: str
    email: str
    name: str = ""
    is_platform_admin: bool = False


class WorkspaceMembership(BaseModel):
    """One workspace the signed-in user belongs to, and their role in it."""

    id: str
    slug: str
    name: str
    role: Literal["owner", "admin", "builder", "viewer"]


class Me(BaseModel):
    """``GET /v1/auth/me``."""

    user: UserOut
    workspaces: list[WorkspaceMembership] = []


# ---------------------------------------------------------------------- connections
class ConnectionTestResult(BaseModel):
    """``POST /v1/connections/{id}/test``."""

    ok: bool
    message: str
    capabilities: ConnectionCapabilities = ConnectionCapabilities()
    latency_ms: float | None = None


class ConnectionRotateIn(BaseModel):
    """``POST /v1/connections/{id}/rotate`` — bumps ``credentials_version``."""

    api_key: str
    api_secret: str


class WorkerInstanceOut(BaseModel):
    """One registered worker process of a connection's pool."""

    instance_key: str
    image: Literal["slim", "full"] = "slim"
    sdk_version: str = ""
    installed_provider_ids: list[str] = []
    pack_ids: list[str] = []
    status: Literal["starting", "ready", "draining", "gone"] = "starting"
    managed_by: Literal["external", "supervisor", "cloud"] = "external"
    registered_at: datetime | None = None
    last_heartbeat_at: datetime | None = None


class FleetStatus(BaseModel):
    """``GET /v1/connections/{id}/fleet``."""

    desired_replicas: int = 0
    #: R-V2-4: how many config-less restarts were requested, and when the last one was.
    restart_generation: int = 0
    restart_requested_at: datetime | None = None
    instances: list[WorkerInstanceOut] = []
    installed_provider_ids: list[str] = []
    image: Literal["slim", "full"] = "slim"


class FleetActionIn(BaseModel):
    """``POST /v1/connections/{id}/fleet`` (supervised connections only)."""

    action: Literal["start", "stop", "restart"]
    replicas: int | None = None


class ConnectionOut(BaseModel):
    """A LiveKit deployment the workspace can run agents on (secrets redacted)."""

    id: str
    workspace_id: str = ""
    slug: str
    name: str
    deployment_type: DeploymentType = "cloud"
    url: str
    fingerprint: str = ""
    credentials_version: int = 1
    agent_name: str = "lkap-agent"
    deployment_mode: DeploymentMode = "external"
    replicas: int = 1
    worker_image: Literal["slim", "full"] = "slim"
    region: str | None = None
    use_inference: bool = True
    storage_config_id: str | None = None
    is_default: bool = False
    status: Literal["unverified", "ok", "error"] = "unverified"
    capabilities: ConnectionCapabilities = ConnectionCapabilities()
    last_checked_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ConnectionCreate(BaseModel):
    """``POST /v1/connections`` — secrets are write-only."""

    slug: str
    name: str
    deployment_type: DeploymentType = "cloud"
    url: str
    api_key: str
    api_secret: str
    agent_name: str = "lkap-agent"
    deployment_mode: DeploymentMode = "external"
    replicas: int = 1
    worker_image: Literal["slim", "full"] = "slim"
    region: str | None = None
    use_inference: bool = True
    storage_config_id: str | None = None
    is_default: bool = False


class ConnectionUpdate(BaseModel):
    """``PUT /v1/connections/{id}`` — a partial update; secrets go through rotate."""

    name: str | None = None
    url: str | None = None
    agent_name: str | None = None
    deployment_mode: DeploymentMode | None = None
    replicas: int | None = None
    worker_image: Literal["slim", "full"] | None = None
    region: str | None = None
    use_inference: bool | None = None
    storage_config_id: str | None = None


# ------------------------------------------------------------------------ webhooks
class WebhookEndpointCreate(BaseModel):
    """``POST /v1/webhooks`` (https only outside dev)."""

    url: str
    events: list[str] = []
    description: str = ""
    enabled: bool = True


class WebhookEndpointOut(BaseModel):
    """A webhook endpoint; the signing secret is shown only by its prefix."""

    id: str
    url: str
    events: list[str] = []
    description: str = ""
    enabled: bool = True
    secret_prefix: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None


class WebhookDeliveryOut(BaseModel):
    """One delivery attempt of one event to one endpoint."""

    id: str
    endpoint_id: str
    event_type: str
    event_id: str
    attempt: int = 0
    status: Literal["pending", "delivered", "failed", "dead"] = "pending"
    next_attempt_at: datetime | None = None
    last_status_code: int | None = None
    last_error: str | None = None
    created_at: datetime | None = None
    delivered_at: datetime | None = None


class WebhookEvent(BaseModel):
    """The signed envelope posted to a webhook endpoint.

    Signature header: ``X-LKAP-Signature: t=<unix>,v1=<hmac_sha256(secret, f"{t}.{body}")>``.
    """

    id: str
    type: str
    created_at: datetime
    workspace_id: str
    data: dict[str, Any] = {}


# ----------------------------------------------------------------------- telephony
class CallCreate(BaseModel):
    """``POST /v1/calls`` — place one outbound call."""

    agent_id: str
    to_e164: str
    trunk_id: str | None = None
    variables: dict[str, Any] = {}
    timeout_s: int = 30


class CallOut(BaseModel):
    """One inbound or outbound call."""

    id: str
    session_id: str | None = None
    workspace_id: str = ""
    connection_id: str = ""
    direction: Literal["inbound", "outbound"]
    from_e164: str = ""
    to_e164: str = ""
    status: Literal[
        "dialing",
        "ringing",
        "answered",
        "no_answer",
        "busy",
        "failed",
        "completed",
        "transferred",
    ] = "dialing"
    sip_call_id: str | None = None
    lk_participant_identity: str | None = None
    started_at: datetime | None = None
    answered_at: datetime | None = None
    ended_at: datetime | None = None
    hangup_reason: str | None = None
    transfer_to: str | None = None


# ------------------------------------------------ telephony (V2-17, promoted by R-V2-25)
#: A phone number in E.164 form (``lkap_contracts.telephony.E164_PATTERN``).
E164 = Annotated[str, StringConstraints(pattern=E164_PATTERN)]

TrunkDirection = Literal["inbound", "outbound"]
ProviderHint = Literal["twilio", "telnyx", "other"]

_TRANSFER_TARGET = re.compile(TRANSFER_TARGET_PATTERN)


def _transfer_target(value: str) -> str:
    """Strip and check a transfer destination (E.164 or a ``tel:``/``sip:`` URI)."""
    value = value.strip()
    if not _TRANSFER_TARGET.match(value):
        raise ValueError("must be an E.164 number (+15551234567) or a tel:/sip: URI")
    return value


class TrunkCreate(BaseModel):
    """``POST /v1/telephony/trunks``.

    ``inbound`` trunks accept calls to ``numbers`` (optionally only from
    ``address``, an IP/CIDR/host allow-list entry); ``outbound`` trunks dial
    through ``address`` (the carrier's SIP host, e.g. ``example.pstn.twilio.com``)
    and present ``numbers[0]`` as the caller id. Secrets are write-only.
    """

    connection_id: str | None = Field(
        default=None, description="LiveKit connection; defaults to the workspace's default connection"
    )
    direction: TrunkDirection
    name: str = Field(min_length=1, max_length=200)
    numbers: list[E164] = Field(default_factory=list, max_length=100)
    provider_hint: ProviderHint = "other"
    address: str | None = Field(default=None, max_length=512)
    auth_username: str | None = Field(default=None, max_length=200)
    auth_password: str | None = Field(default=None, max_length=500, description="Write-only")


class TrunkUpdate(BaseModel):
    """``PUT /v1/telephony/trunks/{id}``: omitted fields keep their value.

    ``auth_password`` omitted keeps the stored password, ``""`` clears it.
    """

    name: str | None = Field(default=None, min_length=1, max_length=200)
    numbers: list[E164] | None = Field(default=None, max_length=100)
    provider_hint: ProviderHint | None = None
    address: str | None = Field(default=None, max_length=512)
    auth_username: str | None = Field(default=None, max_length=200)
    auth_password: str | None = Field(default=None, max_length=500, description="Write-only")


class TrunkOut(BaseModel):
    """A SIP trunk as stored by the platform, with its LiveKit id (never its password)."""

    id: str
    connection_id: str
    direction: TrunkDirection
    name: str
    lk_trunk_id: str | None = None
    numbers: list[str] = []
    provider_hint: ProviderHint = "other"
    address: str | None = None
    auth_username: str | None = None
    has_password: bool = False
    created_at: datetime
    updated_at: datetime


class DispatchRuleCreate(BaseModel):
    """``POST /v1/telephony/dispatch-rules``: route calls on an inbound trunk to an agent.

    ``numbers`` empty means every number of the trunk. Each call gets its own
    room named ``<room_prefix><caller>_<random>`` (LiveKit's individual rule).
    """

    trunk_id: str
    agent_id: str
    numbers: list[E164] = Field(default_factory=list, max_length=100)
    room_prefix: str = Field(default="call-", max_length=64, pattern=r"^[A-Za-z0-9_-]*$")
    pin: str | None = Field(default=None, pattern=r"^[0-9]{4,12}$", description="Write-only")


class DispatchRuleOut(BaseModel):
    """A dispatch rule; ``managed_by_number`` marks the rule a number's inbound agent owns."""

    id: str
    connection_id: str
    lk_rule_id: str | None = None
    trunk_id: str
    agent_id: str
    numbers: list[str] = []
    room_prefix: str = ""
    has_pin: bool = False
    managed_by_number: str | None = None
    created_at: datetime


class PhoneNumberCreate(BaseModel):
    """``POST /v1/telephony/numbers``.

    Binding a number to a trunk adds it to the trunk's numbers. Setting
    ``inbound_agent_id`` (inbound trunks only) creates a dispatch rule that
    sends calls to this number to that agent.
    """

    e164: E164
    trunk_id: str | None = None
    inbound_agent_id: str | None = None
    label: str = Field(default="", max_length=200)


class PhoneNumberUpdate(BaseModel):
    """``PUT /v1/telephony/numbers/{id}``: only the fields present in the body change.

    ``inbound_agent_id: null`` stops inbound routing for the number.
    """

    trunk_id: str | None = None
    inbound_agent_id: str | None = None
    label: str | None = Field(default=None, max_length=200)


class PhoneNumberOut(BaseModel):
    """A number owned by the workspace and the agent its inbound calls reach."""

    id: str
    e164: str
    trunk_id: str | None = None
    inbound_agent_id: str | None = None
    label: str = ""
    dispatch_rule_id: str | None = None


class CallTransferIn(BaseModel):
    """``POST /v1/calls/{id}/transfer``: a cold (SIP REFER) transfer.

    ``to`` must also pass the workspace's dialing policy (R-V2-23).
    """

    to: str = Field(min_length=3, max_length=256, description="E.164 number or tel:/sip: URI")

    @field_validator("to")
    @classmethod
    def _valid_target(cls, value: str) -> str:
        return _transfer_target(value)


class CallDtmfIn(BaseModel):
    """``POST /v1/calls/{id}/dtmf``: digits the agent plays into the call."""

    digits: str = Field(pattern=DTMF_PATTERN, description="0-9, *, #, A-D; up to 32")


class CallDtmfOut(BaseModel):
    """What was queued to the worker (no delivery acknowledgement, R-V2-25)."""

    call_id: str
    digits: str
    queued: bool = True


class CallReportIn(BaseModel):
    """``POST /internal/v1/telephony/calls/report`` — the worker's view of a SIP leg.

    Webhooks are the primary source of call status, but a dev install often
    has no public webhook url; the worker reports what it saw so the calls log
    is right either way. Transitions only ever move forward.
    """

    session_id: str
    status: Literal["answered", "completed", "failed"]
    direction: Literal["inbound", "outbound"] | None = None
    from_e164: str | None = Field(default=None, max_length=32)
    to_e164: str | None = Field(default=None, max_length=32)
    sip_call_id: str | None = Field(default=None, max_length=128)
    participant_identity: str | None = Field(default=None, max_length=200)
    reason: str | None = Field(default=None, max_length=128)


class InternalTransferIn(BaseModel):
    """``POST /internal/v1/telephony/sessions/{id}/transfer`` (the ``transfer_call`` tool)."""

    to: str = Field(min_length=3, max_length=256)
    participant_identity: str | None = Field(default=None, max_length=200)

    @field_validator("to")
    @classmethod
    def _valid_target(cls, value: str) -> str:
        return _transfer_target(value)


class InternalTransferOut(BaseModel):
    """The transfer outcome as the tool reports it to the model (``refused`` / ``failed`` / a call status)."""

    ok: bool
    status: str
    call_id: str | None = None
    reason: str | None = None


# --------------------------------------------------------- internal (service token)
class SessionStartIn(BaseModel):
    """``POST /internal/v1/sessions/start`` — the worker creates the session row.

    Used for rooms the platform did not create (inbound SIP), where the
    dispatch metadata carries no ``session_id``.
    """

    agent_id: str
    room_name: str
    channel: SessionChannel = "web"
    participant_identity: str = ""
    caller: dict[str, Any] | None = None
    dispatch_metadata: dict[str, Any] = {}


class SessionMetricsIn(BaseModel):
    """``POST /internal/v1/sessions/{id}/metrics``."""

    latency: SessionLatency = SessionLatency()
    usage_lines: list[CostLine] = []


class RecordingStartOut(BaseModel):
    """``POST /internal/v1/sessions/{id}/recording/start``."""

    egress_id: str


class SessionRecordingIn(BaseModel):
    """``POST /internal/v1/sessions/{id}/recording`` — worker-side finalisation."""

    egress_id: str
    status: Literal["none", "requested", "active", "ready", "failed"]
    duration_s: float | None = None


# Concrete page parametrisations exported to JSON Schema / TypeScript.
CredentialPage = Page[CredentialOut]
AgentPage = Page[AgentOut]
ToolPage = Page[ToolOut]
KbPage = Page[KbOut]
KbDocumentPage = Page[KbDocumentOut]
SessionPage = Page[SessionOut]
SessionEventPage = Page[SessionEventOut]
ConnectionPage = Page[ConnectionOut]
ConfigVersionPage = Page[ConfigVersionOut]
CallPage = Page[CallOut]
TrunkPage = Page[TrunkOut]
DispatchRulePage = Page[DispatchRuleOut]
PhoneNumberPage = Page[PhoneNumberOut]
WebhookEndpointPage = Page[WebhookEndpointOut]
WebhookDeliveryPage = Page[WebhookDeliveryOut]
