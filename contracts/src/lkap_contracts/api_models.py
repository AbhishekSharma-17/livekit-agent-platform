"""Request and response models shared by the api, the worker and the web codegen.

Every model here is exported to JSON Schema and TypeScript; the console and the
session page use the generated types rather than hand-written interfaces.
"""

import re
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, HttpUrl, StringConstraints, field_validator, model_validator

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
from lkap_contracts.pricing import PriceQuote as PriceQuote
from lkap_contracts.pricing import PriceSource as PriceSource
from lkap_contracts.pricing import Unit as Unit
from lkap_contracts.pricing import WorkspacePrice as WorkspacePrice
from lkap_contracts.providers import (
    BARE_TOKEN_MIN_LEN,
    MODEL_ID_MAX_LEN,
    MODEL_ID_PATTERN,
    SECRET_PREFIXES,
    CatalogKind,
    ModelCapabilities,
    ProviderKind,
    ProviderSpec,
)
from lkap_contracts.telephony import DTMF_PATTERN, E164_PATTERN, TRANSFER_TARGET_PATTERN
from lkap_contracts.templates import StarterTemplate
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


class ModelIdRules(BaseModel):
    """The model-id rule the console mirrors (docs/v4/CUSTOM-MODELS.md D-V4-23, R-V4-21).

    Defaults are the contracts' own constants, so ``ModelIdRules()`` is the
    live rule. ``pattern`` is the syntax rule as a JS-compatible regex; a value
    starting with one of ``secret_prefixes``, or a bare token of at least
    ``bare_token_min_len`` characters of one class with no ``/ . : -``, is
    refused as a secret first.
    """

    pattern: str = MODEL_ID_PATTERN
    secret_prefixes: list[str] = Field(default_factory=lambda: list(SECRET_PREFIXES))
    max_len: int = MODEL_ID_MAX_LEN
    bare_token_min_len: int = BARE_TOKEN_MIN_LEN


class ProvidersResponse(BaseModel):
    """``GET /v1/providers``.

    ``v=2`` (V2-06): ``providers`` carries the enriched :class:`ProviderOut`
    (a superset of ``ProviderSpec`` with this workspace's ``enabled``,
    ``installed_on`` and ``default_credential_id``), not the bare registry
    entry.

    ``model_id_rules`` (V4-07, additive): the model-id rule for the console;
    filled by ``GET /v1/providers`` and by the ``providers.json`` export.
    """

    v: Literal[1, 2] = 2
    providers: list[ProviderOut]
    model_id_rules: ModelIdRules | None = None


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
    total: int | None = None
    """Items matching ``q``/``model`` before ``limit``/``offset`` (V4-07); ``None`` from older apis."""


class ProviderModelOut(BaseModel):
    """One workspace's record of a model id (``provider_models``, D-V4-24, R-V4-24).

    Keyed by ``(provider_home, kind, model_id)``: ``provider_home`` is the
    entry's credential home, so an OpenRouter model tested from the STT entry
    and from the LLM entry is one row per kind. ``last_test_message`` is
    scrubbed of secrets and is vendor text: render it as data, never markup.
    ``last_test_fingerprint`` is the credential's display fingerprint at test
    time; a rotated key no longer matches it, which resets "Tested".
    """

    id: str
    provider_id: str
    provider_home: str
    kind: ProviderKind
    model_id: str
    declared: ModelCapabilities | None = None
    detected: ModelCapabilities | None = None
    last_test_at: datetime | None = None
    last_test_ok: bool | None = None
    last_test_message: str | None = None
    last_test_latency_ms: int | None = None
    last_test_cost_usd: Decimal | None = None
    last_test_credential_id: str | None = None
    last_test_fingerprint: str | None = None
    catalog_seen_at: datetime | None = None
    catalog_missing_since: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ProviderModelDeclare(BaseModel):
    """``PUT /v1/providers/{id}/models/{model_id}``: what an admin says the model can do."""

    declared: ModelCapabilities


#: The probes a "Test model" run may ask for (D-V4-26): ``basic`` always runs;
#: ``tools`` (llm, on by default) and ``vision`` (llm, opt-in) add one call each.
ProbeName = Literal["basic", "tools", "vision"]


class ModelTestRequest(BaseModel):
    """``POST /v1/providers/{provider_id}/test-model`` (D-V4-26, R-V4-25).

    ``model`` is checked by the model-id rule before anything else (422 without
    echo). ``credential_id`` defaults like the catalog route (the workspace's
    default, else its only key for the credential home). ``connection_id``
    matters only for the LiveKit Inference entries, whose probe signs the
    gateway request with that connection's key and secret (default: the
    workspace's default connection). ``fields`` are the slot's non-secret
    fields (``voice``, ``voice_id``, ``base_url`` …). ``force`` skips the
    10-minute re-run suppression.
    """

    model: str = Field(min_length=1, max_length=200)
    credential_id: str | None = None
    connection_id: str | None = None
    fields: dict[str, str | int | float | bool] = {}
    probes: list[ProbeName] = ["basic", "tools"]
    force: bool = False


class ProbeResult(BaseModel):
    """One call of a "Test model" run. ``message`` is scrubbed vendor text: data, never markup."""

    name: str
    ok: bool | None = None
    latency_ms: int | None = None
    message: str | None = None


class ModelTestResult(BaseModel):
    """The answer of ``POST /v1/providers/{provider_id}/test-model`` (D-V4-26).

    ``ok`` is ``None`` when nothing was probed (no probe for the entry, or an
    image model: images cost money). ``message`` and ``sample`` (at most 200
    characters: the LLM's word, the STT transcript) are scrubbed vendor text
    and **untrusted**: render them as data. ``cached`` answers come from the
    workspace's record within 10 minutes of the last run with the same key
    (their ``probes`` list is empty). A passing probe proves the vendor accepts
    the id with this key, not that the LiveKit plugin constructs it.
    """

    ok: bool | None = None
    provider_id: str
    model: str
    kind: ProviderKind
    checked_at: datetime
    cached: bool = False
    latency_ms: int | None = None
    probes: list[ProbeResult] = []
    detected: ModelCapabilities = ModelCapabilities()
    cost_estimate_usd: Decimal | None = None
    cost_note: str = ""
    message: str | None = None
    sample: str | None = None
    record_id: str | None = None


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
    """``POST /v1/agents``. ``config=None`` seeds from ``template_id`` or the pack manifest.

    v4 (docs/v4/TEMPLATES.md D-V4-3): ``template_id`` wins over ``pack_id``
    (the pack is the template's); sending it together with ``config`` is a 422.
    """

    name: str
    description: str = ""
    pack_id: str = "generic"
    template_id: str | None = None
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


class KbImportIn(BaseModel):
    """``POST /v1/knowledge-bases/{id}/documents/import`` (v3, R-V3-14).

    The api fetches ``url`` itself, through its outbound network guard, and
    ingests the body like an upload (25 MB cap; text, markdown, JSON or PDF).
    """

    url: HttpUrl = Field(description="A public http(s) url of the source document")
    filename: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="The stored document name; defaults to the url's last path segment",
    )


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


class TemplateEstimate(BaseModel):
    """A starter's per-minute estimate at list prices and default assumptions (docs/v4/COSTS.md §3.1)."""

    per_minute_usd_mid: Decimal
    per_minute_usd_low: Decimal
    per_minute_usd_high: Decimal
    as_of: str = Field(description="The oldest `as_of` among the prices used.")
    unpriced: int = Field(default=0, description="How many estimate lines have no price.")


class TemplateOut(BaseModel):
    """One starter, merged (``instructions.md`` folded in) plus the pack it layers on."""

    template: StarterTemplate
    pack: PackManifest
    derived: bool = False
    estimate: TemplateEstimate | None = Field(
        default=None,
        description="An estimate at list prices before any usage; null when nothing is priced.",
    )


class TemplatesResponse(BaseModel):
    """``GET /v1/templates``: catalogue order, then derived pack entries."""

    items: list[TemplateOut]


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
    price_source: PriceSource | None = Field(
        default=None, description="Which price source priced the line (null when unpriced)."
    )
    vendor_usd: Decimal | None = Field(
        default=None, description="The vendor's own charge for this line, when reconciled."
    )
    vendor_ref: str | None = Field(default=None, description='What was reconciled, e.g. "12 generations".')


#: Why an actual line differs from its estimate (docs/v4/COSTS.md §4.3, first matching rule).
DriverReason = Literal[
    "more minutes",
    "fewer minutes",
    "more talk",
    "less talk",
    "longer prompts",
    "more turns",
    "unpriced line",
    "price changed",
    "not estimated",
    "as estimated",
]

#: Which part of the agent an estimate line prices (docs/v4/COSTS.md §3.1).
EstimateSlot = Literal[
    "stt",
    "llm",
    "tts",
    "realtime",
    "avatar",
    "workflow_llm",
    "image_gen",
    "embedding",
    "turn_detection",
    "vad",
    "livekit_agent",
    "livekit_participant",
    "livekit_sip",
    "livekit_egress",
    "qa_judge",
]

#: Where an assumption's value came from.
AssumptionSource = Literal["default", "workspace", "request"]

#: What kind of session an estimate is for.
EstimateChannel = Literal["web", "phone", "text"]


class CostDriver(BaseModel):
    """One estimate-vs-actual comparison row; a list is sorted by ``|delta_usd|``, largest first."""

    slot: EstimateSlot
    provider_id: str
    model: str | None = None
    unit: Unit
    estimated_quantity: Decimal | None = None
    actual_quantity: Decimal | None = None
    estimated_usd: Decimal | None = None
    actual_usd: Decimal | None = None
    delta_usd: Decimal | None = Field(
        default=None, description="actual - estimated; null when either side is unpriced."
    )
    reason: DriverReason


class SessionCost(BaseModel):
    """The cost block of ``SessionDetailOut``: actual lines, and the estimate snapshotted at creation."""

    total_usd: Decimal | None = None
    lines: list[CostLine] = []
    estimated_usd: Decimal | None = Field(
        default=None,
        description="The creation-time estimate: per-minute mid x actual minutes + per-session lines. "
        "Null for a session without a snapshot (never back-filled).",
    )
    estimate_per_minute_usd: Decimal | None = None
    variance_usd: Decimal | None = Field(
        default=None, description="total_usd - estimated_usd, when both exist."
    )
    variance_pct: float | None = None
    reconciled_usd: Decimal | None = Field(
        default=None, description="The vendors' own charge, when reconciled."
    )
    price_version: str | None = None
    estimate_as_of: str | None = None
    drivers: list[CostDriver] = []


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


#: Mirrors the `recording_status_valid` CHECK on `sessions` (db/models.py).
RecordingStatus = Literal["none", "requested", "active", "ready", "failed"]


class RecordingOut(BaseModel):
    """The recording block of ``SessionDetailOut`` (``url`` is a signed URL)."""

    status: RecordingStatus = "none"
    url: str | None = None
    duration_s: float | None = None
    expires_at: datetime | None = None
    #: Why `status == "failed"`, e.g. "recording/start answered HTTP 422"
    #: (docs/v2/_asks.md V2-20-3). `None` for every other status.
    error: str | None = None


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
    estimated_usd: Decimal | None = Field(
        default=None, description="The creation-time estimate (docs/v4/COSTS.md D-V4-43); null without one."
    )
    reconciled_usd: Decimal | None = None
    disposition: str | None = None
    #: Surfaced on the list row too (docs/v2/_asks.md V2-20-3) so a `failed`
    #: recording is visible without opening the session; the reason itself
    #: is only on `SessionDetailOut.recording.error` (list rows stay light).
    recording_status: RecordingStatus = "none"


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
    estimated_usd: Decimal | None = None
    sessions_estimated: int = 0
    accuracy_pct: float | None = Field(
        default=None, description="actual / estimated x 100 over the sessions that have both figures."
    )


class AnalyticsDriver(BaseModel):
    """One of the range's top cost drivers (``session_costs`` lines grouped by provider, model and unit)."""

    provider_id: str
    model: str | None = None
    unit: Unit
    cost_usd: Decimal
    share_pct: float
    estimated_usd: Decimal | None = None


class AnalyticsSummary(BaseModel):
    """``GET /v1/analytics/summary``."""

    sessions: int = 0
    minutes: float = 0.0
    cost_usd: Decimal | None = None
    failed: int = 0
    by_day: list[AnalyticsBucket] = []
    by_agent: list[AnalyticsBucket] = []
    estimated_usd: Decimal | None = None
    sessions_estimated: int = 0
    accuracy_pct: float | None = Field(
        default=None, description="actual / estimated x 100 over the sessions that have both figures."
    )
    top_drivers: list[AnalyticsDriver] = Field(default=[], description="At most 8, largest first.")


# ------------------------------------------------------------ cost estimates (V4-15)
class Assumption(BaseModel):
    """One named input of the usage model (docs/v4/COSTS.md D-V4-41)."""

    key: str
    value: float | str
    low: float | None = None
    high: float | None = None
    unit: str | None = None
    source: AssumptionSource
    label: str
    source_url: str | None = None


class EstimateLine(BaseModel):
    """One line of a per-minute estimate."""

    slot: EstimateSlot
    label: str = Field(description='The plain-language label, e.g. "Agent\'s voice".')
    provider_id: str
    model: str | None = None
    unit: Unit
    quantity_per_min: Decimal | None = Field(default=None, description="Null for a per-session line.")
    quantity_per_session: Decimal | None = None
    quote: PriceQuote | None = None
    usd_per_min: Decimal | None = Field(default=None, description="Null when unpriced.")
    usd_per_session: Decimal | None = None
    note: str | None = None


class MoneyRange(BaseModel):
    """A low / mid / high band in USD (a band from stated assumptions, not a confidence interval)."""

    low: Decimal
    mid: Decimal
    high: Decimal


class CostEstimate(BaseModel):
    """What an agent configuration is estimated to cost per minute, at list prices."""

    per_minute_usd: MoneyRange | None = Field(default=None, description="Null when every line is unpriced.")
    per_session_usd: MoneyRange | None = None
    session_minutes: float
    channel: EstimateChannel
    lines: list[EstimateLine] = []
    assumptions: list[Assumption] = []
    unpriced: list[str] = Field(default=[], description="A plain description of every unpriced line.")
    priced_share: float = Field(default=0.0, description="Priced lines / all lines, 0-1.")
    price_version: str
    as_of: str = Field(description="The oldest `as_of` among the quotes used.")
    sources: list[PriceSource] = []
    caveats: list[str] = []


class CostEstimateRequest(BaseModel):
    """``POST /v1/cost-estimates``: exactly one of ``agent_id``, ``template_id``, ``config``.

    ``POST /v1/agents/{id}/cost-estimate`` takes the same body with none of the three.
    """

    agent_id: str | None = None
    template_id: str | None = None
    config: AgentConfig | None = Field(default=None, description="An unsaved draft; validated, never stored.")
    assumptions: dict[str, float | str] | None = Field(
        default=None, description="Overrides by assumption key (see `GET /v1/cost-estimates/assumptions`)."
    )
    channel: EstimateChannel = "web"

    @model_validator(mode="after")
    def _at_most_one_source(self) -> "CostEstimateRequest":
        given = [v for v in (self.agent_id, self.template_id, self.config) if v is not None]
        if len(given) > 1:
            raise ValueError("give exactly one of agent_id, template_id or config")
        return self


class CostAssumptionsOut(BaseModel):
    """``GET /v1/cost-estimates/assumptions``: the workspace's effective assumptions."""

    assumptions: list[Assumption]
    sessions_sampled: int = Field(default=0, description="Ended sessions the workspace averages came from.")


class PriceQuoteItemIn(BaseModel):
    """One provider/model to quote."""

    provider_id: str = Field(min_length=1, max_length=64)
    model: str | None = Field(default=None, max_length=256)


class PriceQuotesRequest(BaseModel):
    """``POST /v1/pricing/quotes``."""

    items: list[PriceQuoteItemIn] = Field(max_length=100)


class PriceQuoteItem(BaseModel):
    """The quotes for one provider/model, with that slot's own per-minute share."""

    provider_id: str
    model: str | None = None
    kind: ProviderKind | None = None
    quotes: list[PriceQuote] = []
    per_minute_usd: Decimal | None = Field(
        default=None, description="This slot's own share of a minute at default assumptions (an estimate)."
    )
    note: str | None = None


class PriceQuotesResponse(BaseModel):
    """``POST /v1/pricing/quotes``."""

    items: list[PriceQuoteItem]
    price_version: str
    as_of: str


class WorkspacePricesIn(BaseModel):
    """``PUT /v1/workspace/prices``: the full list (replaces the stored one)."""

    prices: list[WorkspacePrice] = Field(max_length=100)


class WorkspacePricesOut(BaseModel):
    """``GET``/``PUT /v1/workspace/prices``."""

    prices: list[WorkspacePrice] = []


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
    """A dispatch rule; ``managed_by_number`` marks the rule a number's inbound agent owns.

    ``trunk_id`` is ``None`` only for the trunk-less rule of a LiveKit-hosted
    number (``phone_number_id`` names it; PHONE-NUMBERS.md D-V4-17).
    """

    id: str
    connection_id: str
    lk_rule_id: str | None = None
    trunk_id: str | None = None
    phone_number_id: str | None = None
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


#: Where a number comes from (PHONE-NUMBERS.md D-V4-14): typed in and bound to a
#: SIP trunk, or a LiveKit-hosted number mirrored from the project.
NumberSource = Literal["trunk", "livekit"]

#: ``PhoneNumber.status`` of a LiveKit-hosted number, lowered from the proto enum.
LkNumberStatus = Literal["active", "pending", "released", "offline", "unknown"]

#: ``PhoneNumber.inbound_status`` of a LiveKit-hosted number, lowered from the proto enum.
LkInboundStatus = Literal["active", "unavailable", "detached", "unknown"]

#: Whether calls to the number reach its inbound agent (derived; PHONE-NUMBERS.md §4.4).
AttachState = Literal["routed", "detached", "not_routed", "pending", "offline", "released"]


class PhoneNumberOut(BaseModel):
    """A number owned by the workspace and the agent its inbound calls reach.

    ``source="livekit"`` rows are mirrored from the LiveKit project by
    ``POST /v1/telephony/numbers/refresh``: they have a ``connection_id`` and
    an ``lk_number_id`` and never a trunk. ``warnings`` is filled only on the
    response of an assignment (the dispatch-rule conflict pre-check).
    """

    id: str
    e164: str
    source: NumberSource = "trunk"
    trunk_id: str | None = None
    connection_id: str | None = None
    inbound_agent_id: str | None = None
    label: str = ""
    dispatch_rule_id: str | None = None
    lk_number_id: str | None = None
    lk_status: LkNumberStatus | None = None
    lk_inbound_status: LkInboundStatus | None = None
    lk_rule_ids: list[str] = []
    attach_state: AttachState = "not_routed"
    region: str = ""
    lk_synced_at: datetime | None = None
    warnings: list[str] = []


class NumbersRefreshIn(BaseModel):
    """``POST /v1/telephony/numbers/refresh``: mirror the project's LiveKit-hosted numbers.

    Without ``connection_id`` every SIP-capable connection of the workspace is read.
    """

    connection_id: str | None = None


class NumbersRefreshOut(BaseModel):
    """What one connection's refresh changed. Nothing is bought or given back."""

    connection_id: str
    seen: int = 0
    added: int = 0
    updated: int = 0
    released: int = 0
    conflicts: list[str] = Field(
        default_factory=list, description="E.164 numbers already registered as trunk numbers (skipped)"
    )
    warnings: list[str] = []


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
    """``POST /internal/v1/sessions/{id}/recording`` — worker-side finalisation.

    ``egress_id`` defaults to ``""`` (docs/v2/_asks.md V2-20-3): a recording
    that never started (``status="failed"`` from ``recording/start`` itself
    failing) has no egress id at all, and the route's mismatch guard only
    fires when the session already has a *different*, real one on file.
    """

    egress_id: str = ""
    status: RecordingStatus
    duration_s: float | None = None
    #: Why `status == "failed"`; `None` for every other status or when the
    #: worker could not determine a reason.
    error: str | None = None


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
ProviderModelPage = Page[ProviderModelOut]
