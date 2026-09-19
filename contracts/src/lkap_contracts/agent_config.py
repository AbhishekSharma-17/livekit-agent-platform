"""Agent configuration: what an admin saves, and what the worker receives resolved."""

from typing import Any, Literal

from pydantic import BaseModel

from lkap_contracts.tools import ToolDefinition

PipelineMode = Literal["realtime", "cascaded"]

#: Slots of :attr:`ResolvedAgentConfig.resolved`.
ProviderSlot = Literal["realtime", "stt", "llm", "tts", "avatar", "image_gen", "workflow_llm"]


class ProviderRef(BaseModel):
    """Points at a registry provider plus the credential and options to use."""

    provider_id: str
    credential_id: str | None = None
    model: str | None = None
    fields: dict[str, str | int | float | bool] = {}


class PipelineConfig(BaseModel):
    """Which providers fill which slot, and how turns are handled."""

    mode: PipelineMode = "cascaded"
    realtime: ProviderRef | None = None
    stt: ProviderRef | None = None
    llm: ProviderRef | None = None
    tts: ProviderRef | None = None
    avatar: ProviderRef | None = None
    image_gen: ProviderRef | None = None
    workflow_llm: ProviderRef | None = None
    turn_handling: dict[str, Any] = {}


class VoiceConfig(BaseModel):
    """Greeting and conversational behaviour."""

    greeting: str = "Hello! How can I help you today?"
    greeting_mode: Literal["say", "generate"] = "say"
    language: str = "en"
    allow_interruptions: bool = True
    user_away_timeout_s: float | None = 15.0


class CapabilitiesConfig(BaseModel):
    """Which input affordances the session page offers."""

    camera: bool = False
    screen_share: bool = False
    chat_input: bool = True
    vision_inject_per_turn: bool = True


class ToolsConfig(BaseModel):
    """Built-in tool gating plus references to admin-authored tool rows."""

    builtin_disabled: list[str] = []
    http_request_enabled: bool = False
    tool_ids: list[str] = []
    max_tool_steps: int = 3


class KnowledgeConfig(BaseModel):
    """Knowledge bases attached to the agent and how they are injected."""

    kb_ids: list[str] = []
    auto_inject: bool = True
    top_k: int = 4


class AgentConfig(BaseModel):
    """The full, admin-editable configuration of one agent (stored as JSON)."""

    v: Literal[1] = 1
    instructions: str
    pipeline: PipelineConfig
    voice: VoiceConfig = VoiceConfig()
    capabilities: CapabilitiesConfig = CapabilitiesConfig()
    tools: ToolsConfig = ToolsConfig()
    knowledge: KnowledgeConfig = KnowledgeConfig()
    pack_settings: dict[str, Any] = {}
    timezone: str = "UTC"


class ResolvedProvider(BaseModel):
    """A provider ready to construct: class path plus complete constructor kwargs."""

    provider_id: str
    python_class: str
    model: str | None
    kwargs: dict[str, object]


class ResolvedAgentConfig(BaseModel):
    """What the worker receives from ``/internal/v1/sessions/{id}/resolved``.

    Contains decrypted credentials in ``resolved[*].kwargs`` and substituted tool
    secrets in ``tools``. Never log this object.
    """

    v: Literal[1] = 1
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
