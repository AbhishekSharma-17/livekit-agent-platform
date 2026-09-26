"""Agent configuration: what an admin saves, and what the worker receives resolved."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator

from lkap_contracts.common import Issue, ProviderRef, SessionChannel
from lkap_contracts.connections import ConnectionInfo
from lkap_contracts.flow import FlowSpec, QaNode
from lkap_contracts.providers import ModelCapabilities
from lkap_contracts.telephony import TelephonyConfig
from lkap_contracts.tool_providers import AppsMode
from lkap_contracts.tools import ToolDefinition, ToolExecution, ToolExecutionMode
from lkap_contracts.turn_handling import (
    AMBIENT_SOUND_PATTERN,
    ConversationPreset,
    TurnDetectorSettings,
    TurnHandlingOptions,
)
from lkap_contracts.ui_protocol import BlockSpec

PipelineMode = Literal["realtime", "cascaded", "half_cascade"]

#: ``VoiceConfig.thinking_sound``: the three ``BuiltinAudioClip``s of livekit-agents 1.8.2 that
#: suit a wait (``KEYBOARD_TYPING``, ``KEYBOARD_TYPING2``, ``OFFICE_AMBIENCE``), or none.
ThinkingSound = Literal["none", "keyboard_typing", "keyboard_typing2", "office_ambience"]

#: Slots of :attr:`ResolvedAgentConfig.resolved`.
ProviderSlot = Literal[
    "realtime",
    "stt",
    "llm",
    "tts",
    "avatar",
    "image_gen",
    "workflow_llm",
    "qa_llm",
    "vad",
    "turn_detection",
    "noise_cancellation",
]
#: qa_llm (R-V2-6): resolved by the api from qa.model -> workflow_llm -> llm -> Inference LLM
#: default, present in ``resolved`` only when ``AgentConfig.qa.enabled``. The worker builds
#: its judge from this slot and never walks the chain itself.

__all__ = [
    "KNOWLEDGE_RERANK_VALUES",
    "AgentConfig",
    "AgentLimits",
    "AppsMode",
    "AvatarOptions",
    "CallerTimezoneMode",
    "CapabilitiesConfig",
    "ConnectionInfo",
    "ConversationPreset",
    "KnowledgeConfig",
    "KnowledgeQueryMode",
    "KnowledgeSearchMode",
    "LocaleConfig",
    "PanelLayout",
    "PipelineConfig",
    "PipelineMode",
    "ProviderRef",
    "ProviderSlot",
    "QaConfig",
    "RecordingConfig",
    "ResolvedAgentConfig",
    "ResolvedProvider",
    "TelephonyConfig",
    "ThinkingSound",
    "ToolsConfig",
    "TurnDetectorSettings",
    "TurnHandlingOptions",
    "VoiceConfig",
    "pipeline_issues",
]


class AvatarOptions(BaseModel):
    """Publisher-side options applied to whichever avatar plugin is selected."""

    participant_name: str = "Avatar"
    video_quality: Literal["low", "medium", "high", "very_high"] | None = None
    idle_timeout_s: int | None = None
    max_duration_s: int | None = None


class PipelineConfig(BaseModel):
    """Which providers fill which slot, and how turns are handled.

    Slot requirements per mode are reported by :func:`pipeline_issues` rather
    than raised here: the api returns them as a ``ValidationResult`` so a
    half-finished pipeline can still be saved as a draft.
    """

    mode: PipelineMode = "cascaded"
    realtime: ProviderRef | None = None
    stt: ProviderRef | None = None
    llm: ProviderRef | None = None
    tts: ProviderRef | None = None
    avatar: ProviderRef | None = None
    avatar_options: AvatarOptions = AvatarOptions()
    image_gen: ProviderRef | None = None
    workflow_llm: ProviderRef | None = None
    vad: ProviderRef | None = None
    turn_detection: ProviderRef | None = None
    noise_cancellation: ProviderRef | None = None
    turn_handling: Annotated[TurnHandlingOptions | dict[str, Any], Field(union_mode="left_to_right")] = {}
    """``AgentSession(turn_handling=...)`` (V5-07). Validated against the typed model and kept
    as a plain dict of the keys that were set; a dict whose typed keys do not validate is kept
    as it is (compatibility). Used as is only with ``conversation_preset == "custom"``."""
    conversation_preset: ConversationPreset = "custom"
    """A named preset replaces the turn-taking keys of ``turn_handling`` when a session starts
    (``turn_handling.resolve_turn_handling``); it is never stored expanded."""
    turn_detector: TurnDetectorSettings | None = None
    """Where the end-of-turn model runs and how sensitive it is; unset = as before V5-07."""

    @field_validator("turn_handling", mode="after")
    @classmethod
    def _turn_handling_as_dict(cls, value: TurnHandlingOptions | dict[str, Any]) -> dict[str, Any]:
        """Keep the runtime value a plain dict of the stored keys (one shape for every consumer)."""
        if isinstance(value, TurnHandlingOptions):
            dumped: dict[str, Any] = value.model_dump()
            return dumped
        return value


class VoiceConfig(BaseModel):
    """Greeting and conversational behaviour."""

    greeting: str = "Hello! How can I help you today?"
    greeting_mode: Literal["say", "generate"] = "say"
    language: str = "en"
    allow_interruptions: bool = True
    user_away_timeout_s: float | None = 15.0
    first_speaker: Literal["agent", "user"] = "agent"
    thinking_sound: ThinkingSound = "none"
    """A built-in clip played while the agent waits on a blocking tool (never on the text channel)."""
    ambient_sound: Annotated[str, Field(pattern=AMBIENT_SOUND_PATTERN)] = "none"
    """A background clip played for the whole call (V5-07): ``none``, a built-in name
    (``turn_handling.AMBIENT_SOUNDS``) or ``asset:<id>`` (an uploaded clip, played from a later
    package). Needs audio output; never on the text channel."""


class CapabilitiesConfig(BaseModel):
    """Which input affordances the session page offers."""

    camera: bool = False
    screen_share: bool = False
    chat_input: bool = True
    vision_inject_per_turn: bool = True
    dtmf: bool = False


class ToolsConfig(BaseModel):
    """Built-in tool gating plus references to admin-authored tool rows."""

    builtin_disabled: list[str] = []
    http_request_enabled: bool = False
    tool_ids: list[str] = []
    max_tool_steps: int = 3
    execution_default: ToolExecutionMode = "blocking"
    """How read tools run when they set no mode of their own: GET HTTP tools and the built-ins
    in ``BACKGROUNDABLE_BUILTINS``. Never reaches writes, MCP tools, telephony, forms or flow edges."""
    builtin_execution: dict[str, ToolExecution] = {}
    """Per built-in execution settings, keyed by a name in ``BACKGROUNDABLE_BUILTINS``."""
    apps: AppsMode = AppsMode()
    """Connected apps (docs/v5/COMPOSIO.md D-V5-C6): ``off`` by default. The api provisions the
    app server or tool finder on save and attaches it through ``tool_ids``."""


#: ``KnowledgeConfig.mode``: how a knowledge search ranks chunks (V5-04's ``KnowledgeService``).
KnowledgeSearchMode = Literal["vector", "hybrid"]

#: ``KnowledgeConfig.rerank`` values the platform implements today. The field itself is a string so
#: a ``connection:<id>`` reranker can be accepted later (V5-20) without a contracts change; the api
#: validator refuses anything outside this set until then.
KNOWLEDGE_RERANK_VALUES: tuple[str, ...] = ("none", "local")

#: ``KnowledgeConfig.query_mode``: what the auto-inject search query is built from.
KnowledgeQueryMode = Literal["last_turn", "conversation"]


class KnowledgeConfig(BaseModel):
    """Knowledge bases attached to the agent and how they are injected.

    The v2 fields (V5-06) default so an agent saved before them retrieves at
    least what it did: hybrid search on, no score floor, no rerank.
    """

    kb_ids: list[str] = []
    auto_inject: bool = True
    top_k: int = 4
    min_score: float | None = Field(
        default=None,
        description="Drop hits scoring below this (0-1); null keeps every hit. In hybrid mode without "
        "rerank the score is rank-derived, so a floor only trims the tail of the list.",
    )
    prefetch: bool = Field(
        default=True,
        description="Start the auto-inject search on the caller's interim transcript, so the result is "
        "usually ready when the turn ends.",
    )
    rerank: Literal["none", "local"] | str = Field(
        default="none",
        description="`local` rescores the candidates with the local cross-encoder (adds ~60-100 ms).",
    )
    mode: KnowledgeSearchMode = Field(
        default="hybrid", description="`hybrid` fuses keyword matches with embedding similarity."
    )
    max_inject_tokens: int = Field(
        default=1200,
        ge=1,
        description="Upper bound on the knowledge note added to a turn (approximate tokens).",
    )
    skip_short_turns: bool = Field(
        default=True,
        description="Skip the auto-inject search for backchannels and very short turns (yes, okay, digits).",
    )
    query_mode: KnowledgeQueryMode = Field(
        default="conversation",
        description="`conversation` searches with the last user turn plus the previous assistant sentence "
        "and flow variables; `last_turn` with the user's words only.",
    )


class RecordingConfig(BaseModel):
    """Whether and where the session is recorded through Egress."""

    enabled: bool = False
    audio_only: bool = True
    storage_config_id: str | None = None
    retention_days: int | None = None


class QaConfig(BaseModel):
    """Post-call scoring of the session by an LLM judge."""

    enabled: bool = False
    rubric_prompt: str | None = None
    model: ProviderRef | None = None


class PanelLayout(BaseModel):
    """Which panel renders the session, and (for ``composite``) its blocks."""

    panel_id: str = "composite"
    layout: Literal["side", "wide"] = "side"
    blocks: list[BlockSpec] = []


#: ``LocaleConfig.caller_timezone``: ``detect`` resolves the caller's own zone per session
#: (browser, then phone number, then the business zone); ``business`` always uses the
#: business timezone (R-V5-10).
CallerTimezoneMode = Literal["detect", "business"]


class LocaleConfig(BaseModel):
    """Whose clock the agent talks in (R-V5-10).

    ``AgentConfig.timezone`` stays the **business** timezone (opening hours, seeds,
    booking defaults); this block says how the **caller's** timezone is chosen at
    session start.
    """

    caller_timezone: CallerTimezoneMode = Field(
        default="detect",
        description="`detect` uses the caller's own timezone (from the browser, else the phone "
        "number, else the business timezone); `business` always uses the business timezone.",
    )


class AgentLimits(BaseModel):
    """Per-agent guard rails enforced by ``connect`` (CONTRACTS-V2 §3.3)."""

    max_concurrent_sessions: int = 5
    max_session_duration_s: int = 1800
    rate_per_ip_per_min: int = 6
    rate_per_agent_per_min: int = 60


class AgentConfig(BaseModel):
    """The full, admin-editable configuration of one agent (stored as JSON).

    ``v`` accepts ``1`` for one release: v1 rows are rewritten to v2 by the
    ``v2_008_agentconfig_v2`` migration, and the console still posts ``v: 1``
    until WP-3 lands.
    """

    v: Literal[1, 2] = 2
    instructions: str
    pipeline: PipelineConfig
    voice: VoiceConfig = VoiceConfig()
    capabilities: CapabilitiesConfig = CapabilitiesConfig()
    tools: ToolsConfig = ToolsConfig()
    knowledge: KnowledgeConfig = KnowledgeConfig()
    panel: PanelLayout = PanelLayout()
    recording: RecordingConfig = RecordingConfig()
    qa: QaConfig = QaConfig()
    flow: FlowSpec | None = None
    #: R-V2-21: the transfer destinations (``transfer_call`` registers only when non-empty).
    telephony: TelephonyConfig = TelephonyConfig()
    pack_settings: dict[str, Any] = {}
    timezone: str = Field(
        default="UTC",
        description="The business timezone (IANA name): opening hours and bookings are in this zone.",
    )
    locale: LocaleConfig = LocaleConfig()
    """How the caller's timezone is chosen (R-V5-10); agents saved before it behave as ``detect``."""


class ResolvedProvider(BaseModel):
    """A provider ready to construct: class path plus complete constructor kwargs."""

    provider_id: str
    python_class: str
    model: str | None
    kwargs: dict[str, object]
    capabilities: ModelCapabilities | None = None
    """What the model can do (V4-08, D-V4-24): filled for ``llm``, ``workflow_llm`` and
    ``realtime`` from declared → detected → live catalog → registry; ``None`` = not resolved."""


class ResolvedAgentConfig(BaseModel):
    """What the worker receives from ``/internal/v1/sessions/{id}/resolved``.

    Contains decrypted credentials in ``resolved[*].kwargs`` and substituted tool
    secrets in ``tools``. Never log this object.

    The v2 additions all carry defaults so a v1 api (before V2-01/V2-03 land)
    still produces a valid document.
    """

    v: Literal[2] = 2
    session_id: str
    agent_id: str
    agent_slug: str
    config_version: int
    pack_id: str
    ui_panel_id: str
    config: AgentConfig
    resolved: dict[ProviderSlot, ResolvedProvider]
    tools: list[ToolDefinition]
    kb_ids: list[str]
    participant_identity: str
    workspace_id: str = ""
    channel: SessionChannel = "web"
    connection: ConnectionInfo = ConnectionInfo()
    recording: RecordingConfig = RecordingConfig()
    panel: PanelLayout = PanelLayout()
    installed_provider_ids: list[str] | None = None
    #: R-V2-22: the session's seed variables (``sessions.variables``; an outbound call's
    #: ``CallCreate.variables``). Flow agents start their ``FlowState.variables`` with them;
    #: prompt agents get them appended to ``config.instructions`` by the worker.
    variables: dict[str, Any] = {}
    #: V4-17 (docs/v4/COSTS.md D-V4-45, R-V4-48): the vendors this workspace reconciles
    #: against their own per-request charge (``workspaces.settings["cost"]["reconcile"]``,
    #: e.g. ``["openrouter"]``). Non-empty makes the worker collect the per-request ids
    #: of its LLM/STT/TTS calls and post them as one ``metrics {kind: "provider_requests"}``
    #: event; empty (the default) collects nothing.
    cost_reconcile: list[str] = []
    #: R-V5-10: the effective business timezone: ``config.timezone`` when it is a valid IANA
    #: name, else the workspace default (``workspaces.settings.locale.timezone``), else
    #: ``"UTC"``. The api also writes it into this document's ``config.timezone``. ``None``
    #: (an api before V5-51) = the worker reads ``config.timezone`` itself.
    business_timezone: str | None = None
    #: R-V5-10: ``config.locale``, repeated here for the worker.
    locale: LocaleConfig = LocaleConfig()


#: Slots each pipeline mode requires, in the order the console renders them.
REQUIRED_SLOTS: dict[PipelineMode, tuple[str, ...]] = {
    "cascaded": ("stt", "llm", "tts"),
    "realtime": ("realtime",),
    "half_cascade": ("realtime", "tts"),
}


def pipeline_issues(pipeline: PipelineConfig) -> list[Issue]:
    """Return the slot-completeness problems of a pipeline.

    Only structural rules that need no database or registry lookup are checked;
    the api adds provider-level findings (unknown id, missing credential,
    ``text_modality`` for half-cascade) on top.

    Args:
        pipeline: The pipeline to inspect.

    Returns:
        One :class:`~lkap_contracts.common.Issue` per missing required slot, in
        slot order.
    """
    issues: list[Issue] = []
    for slot in REQUIRED_SLOTS[pipeline.mode]:
        if getattr(pipeline, slot) is None:
            issues.append(
                Issue(
                    path=f"pipeline.{slot}",
                    message=f"{pipeline.mode} mode requires a {slot} provider",
                    severity="error",
                )
            )
    return issues


def effective_qa(config: AgentConfig) -> QaConfig:
    """The QA settings a session actually runs with (ruling R-V2-11).

    A flow ``qa`` node is the author's intent to score the call, so it turns QA
    on without anybody also flipping ``qa.enabled``. The node's
    ``rubric_prompt`` (when set) replaces ``qa.rubric_prompt``; ``qa.model`` is
    unchanged. Prompt agents (and flows without a ``qa`` node) get
    ``config.qa`` back as-is. The api (provider resolution, validation slots)
    and the worker (``prepare_flow_resolved``) both call this one definition.

    Args:
        config: The agent configuration.

    Returns:
        The effective :class:`QaConfig`; ``config.qa`` itself when nothing changes.
    """
    flow = config.flow
    qa_node = next((n for n in flow.nodes if isinstance(n, QaNode)), None) if flow is not None else None
    if qa_node is None:
        return config.qa
    update: dict[str, Any] = {"enabled": True}
    if qa_node.rubric_prompt:
        update["rubric_prompt"] = qa_node.rubric_prompt
    return config.qa.model_copy(update=update)
