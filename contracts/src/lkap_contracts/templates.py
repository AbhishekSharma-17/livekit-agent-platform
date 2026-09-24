"""Starter templates: data-only presets layered on a pack (docs/v4/TEMPLATES.md).

A starter template names a pack (``pack_id``, default ``generic``) and overlays
configuration on its manifest: instructions, greeting, pipeline, capabilities,
panel blocks, voice settings, knowledge seeds, HTTP tool seeds, a flow, QA,
telephony and recording. Everything it sets is a field of ``AgentConfig`` or a
row the api already knows how to create. The catalogue ships with the api
(``lkap_api/templates/catalog``) and is served by ``GET /v1/templates``.
"""

from typing import Any, Literal, Self

from pydantic import BaseModel, Field, field_serializer, model_validator

from lkap_contracts.agent_config import (
    CapabilitiesConfig,
    KnowledgeConfig,
    PanelLayout,
    PipelineConfig,
    QaConfig,
    RecordingConfig,
    TelephonyConfig,
    VoiceConfig,
)
from lkap_contracts.flow import FlowSpec
from lkap_contracts.packs import KbSeed
from lkap_contracts.tools import HttpToolDefinition

__all__ = [
    "TEMPLATE_ID_PATTERN",
    "EditorSection",
    "NextStep",
    "RequiredKey",
    "StarterTemplate",
    "TemplateCategory",
    "TemplateChip",
    "TemplateRequirements",
    "ToolSeed",
]

#: Template ids are slugs; derived ones are ``pack:<pack_id>``.
TEMPLATE_ID_PATTERN = r"^(pack:)?[a-z][a-z0-9_]{0,31}$"

TemplateCategory = Literal["blank", "support", "scheduling", "vision", "phone", "sales", "forms", "example"]

#: The closed chip vocabulary the gallery renders (label + icon per chip in the console).
TemplateChip = Literal[
    "rag",
    "citations",
    "http_tools",
    "forms",
    "table",
    "flow",
    "variables",
    "camera",
    "screen_share",
    "gallery",
    "telephony",
    "dtmf",
    "transfer",
    "webhook",
    "qa",
    "code_tools",
    "knowledge_seeds",
    "image_gen",
]

#: Editor sections a next step can deep-link to (``builtin-sections.tsx`` ids).
EditorSection = Literal[
    "providers", "instructions", "flow", "panel", "tools", "knowledge", "recording", "limits"
]


class RequiredKey(BaseModel):
    """A vendor key the template uses; ``optional`` keys only unlock an extra."""

    provider_id: str
    optional: bool = False
    purpose: str = ""


class TemplateRequirements(BaseModel):
    """What the workspace needs before the starter does everything it promises."""

    provider_keys: list[RequiredKey] = []
    #: A trunk, a number and a dispatch rule.
    telephony: bool = False
    #: A webhook endpoint subscribed to the events the starter's next steps name.
    webhook_endpoint: bool = False
    #: A storage config, for recording.
    storage: bool = False


class ToolSeed(BaseModel):
    """An HTTP tool row created for the new agent (``Tool(agent_id=…)``)."""

    definition: HttpToolDefinition
    enabled: bool = True


class NextStep(BaseModel):
    """One line of the post-create checklist; ``section`` deep-links into the editor, ``href`` elsewhere."""

    label: str = Field(max_length=120)
    section: EditorSection | None = None
    href: str | None = None


class StarterTemplate(BaseModel):
    """One starter: gallery metadata, the overlay on the pack manifest, and extras on the seeded config.

    Overlay fields left ``None`` keep the pack manifest's value. ``voice`` and
    ``knowledge`` are merged over the seeded config with ``exclude_unset``, so
    they may not set ``greeting`` (use the ``greeting`` overlay) or ``kb_ids``
    (filled from ``kb_seeds`` at create time).
    """

    v: Literal[1] = 1
    id: str = Field(pattern=TEMPLATE_ID_PATTERN)
    name: str = Field(max_length=48)
    tagline: str = Field(max_length=90)
    description: str
    category: TemplateCategory
    chips: list[TemplateChip] = []
    requires: TemplateRequirements = TemplateRequirements()
    order: int = 100
    pack_id: str = "generic"

    # --- overlay on PackManifest (None = keep the pack's value) ---------------
    instructions: str | None = None
    greeting: str | None = None
    pipeline: PipelineConfig | None = None
    capabilities: CapabilitiesConfig | None = None
    builtin_tools_disabled: list[str] | None = None
    default_voice: dict[str, str] = {}
    panel: PanelLayout | None = None

    # --- extras applied to the seeded AgentConfig -----------------------------
    voice: VoiceConfig | None = None
    http_request_enabled: bool = False
    max_tool_steps: int | None = None
    tool_seeds: list[ToolSeed] = []
    kb_seeds: list[KbSeed] = []
    knowledge: KnowledgeConfig | None = None
    flow: FlowSpec | None = None
    qa: QaConfig | None = None
    telephony: TelephonyConfig | None = None
    recording: RecordingConfig | None = None
    pack_settings: dict[str, Any] = {}
    timezone: str | None = None

    # --- gallery ----------------------------------------------------------------
    sample_prompts: list[str] = []
    next_steps: list[NextStep] = []

    @field_serializer("voice", "knowledge")
    def _dump_set_fields(self, value: VoiceConfig | KnowledgeConfig | None) -> dict[str, Any] | None:
        """Dump only the fields the template sets, so a dump re-validates and still merges the same.

        Returns:
            The set fields, or ``None``.
        """
        return None if value is None else value.model_dump(mode="json", exclude_unset=True)

    @model_validator(mode="after")
    def _check_merged_extras(self) -> Self:
        """Reject the two merged fields a template may not set (TEMPLATES §2, §4 step 3).

        Returns:
            The validated template.

        Raises:
            ValueError: ``voice`` sets ``greeting`` or ``knowledge`` sets ``kb_ids``.
        """
        if self.voice is not None and "greeting" in self.voice.model_fields_set:
            raise ValueError("voice.greeting is not allowed on a template; set the greeting overlay instead")
        if self.knowledge is not None and "kb_ids" in self.knowledge.model_fields_set:
            raise ValueError("knowledge.kb_ids is not allowed on a template; use kb_seeds instead")
        return self
