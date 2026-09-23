"""Pack manifests: the pure-Pydantic description the api reads without livekit."""

from typing import Any, Literal

from pydantic import BaseModel

from lkap_contracts.agent_config import (
    CapabilitiesConfig,
    PanelLayout,
    PipelineConfig,
    PipelineMode,
)
from lkap_contracts.ui_protocol import BlockSpec


class KbSeed(BaseModel):
    """A knowledge base the api creates when seeding an agent from this pack."""

    kb_name: str
    files: list[str]


class ToolMeta(BaseModel):
    """Runtime metadata for a pack code tool, used for reply control and UI labels."""

    name: str
    silent_reply: bool = False
    activity_label: str | None = None


class PackManifest(BaseModel):
    """Everything the api and console need to know about a pack."""

    v: Literal[1] = 1
    id: str
    version: str
    name: str
    description: str
    ui_panel_id: str
    default_panel: PanelLayout | None = None
    blocks: list[BlockSpec] = []
    default_instructions: str
    default_greeting: str
    default_voice: dict[str, str] = {}
    recommended_pipeline: PipelineConfig
    capabilities: CapabilitiesConfig
    builtin_tools_disabled: list[str] = []
    tool_names: list[str]
    state_schema: dict[str, Any]
    settings_schema: dict[str, Any] = {"type": "object"}
    kb_seeds: list[KbSeed] = []
    instructions_by_mode: dict[PipelineMode, str] = {}
