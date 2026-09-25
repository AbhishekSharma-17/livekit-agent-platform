"""Agent configuration: what an admin saves, and what the worker receives resolved."""

from typing import Any, Literal

from pydantic import BaseModel

from lkap_contracts.common import Issue, ProviderRef, SessionChannel
from lkap_contracts.connections import ConnectionInfo
from lkap_contracts.flow import FlowSpec, QaNode
from lkap_contracts.providers import ModelCapabilities
from lkap_contracts.telephony import TelephonyConfig
from lkap_contracts.tools import ToolDefinition, ToolExecution, ToolExecutionMode
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
    "AgentConfig",
    "AgentLimits",
    "AvatarOptions",
    "CapabilitiesConfig",
    "ConnectionInfo",
    "KnowledgeConfig",
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
    turn_handling: dict[str, Any] = {}


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


class KnowledgeConfig(BaseModel):
    """Knowledge bases attached to the agent and how they are injected."""

    kb_ids: list[str] = []
    auto_inject: bool = True
    top_k: int = 4


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
    timezone: str = "UTC"


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
