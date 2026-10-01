"""Starter templates: data-only presets layered on a pack (docs/v4/TEMPLATES.md).

A starter template names a pack (``pack_id``, default ``generic``) and overlays
configuration on its manifest: instructions, greeting, pipeline, capabilities,
panel blocks, voice settings, knowledge seeds, HTTP tool seeds, a flow, QA,
telephony and recording. Everything it sets is a field of ``AgentConfig`` or a
row the api already knows how to create. The catalogue ships with the api
(``lkap_api/templates/catalog``) and is served by ``GET /v1/templates``.

V6-22 (D-V6-21, ask #105): a starter may also set live extraction, rules and test
cases (``extraction``, ``rules``, ``tests``), seed lookup tables from its ``seeds/``
directory (:class:`DatasetSeed`, created or reused by name in the new agent's
workspace, like the knowledge seeds) and add tool kits (:class:`TemplateKit`,
applied in order as ``POST /v1/tool-kits/{id}/instantiate`` would, after the
template's own extraction fields and rules, so a kit keeps a field or rule of the
same name the template already declares).
"""

from typing import Any, Final, Literal, Self

from pydantic import BaseModel, Field, field_serializer, field_validator, model_validator

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
from lkap_contracts.agent_tests import MAX_AGENT_TESTS, AgentTest
from lkap_contracts.datasets import MAX_DATASET_KEY_COLUMNS, DatasetKeyColumn
from lkap_contracts.extraction import ExtractionConfig
from lkap_contracts.flow import FlowSpec
from lkap_contracts.packs import KbSeed
from lkap_contracts.rules import Rules
from lkap_contracts.tools import HttpToolDefinition

__all__ = [
    "MAX_TEMPLATE_DATASET_SEEDS",
    "MAX_TEMPLATE_KITS",
    "TEMPLATE_ID_PATTERN",
    "DatasetSeed",
    "EditorSection",
    "NextStep",
    "RequiredKey",
    "StarterTemplate",
    "TemplateCategory",
    "TemplateChip",
    "TemplateKit",
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


#: Most lookup tables one starter seeds.
MAX_TEMPLATE_DATASET_SEEDS: Final[int] = 4
#: Most kits one starter adds.
MAX_TEMPLATE_KITS: Final[int] = 8

# The kit id and prefix shapes of ``lkap_contracts.kits`` (not imported: ``kits`` imports
# ``api_models``, which imports this module; a contracts test keeps the two equal).
_KIT_ID_PATTERN = r"^[a-z][a-z0-9_]{0,39}$"
_KIT_PREFIX_PATTERN = r"^[a-z][a-z0-9_]{0,23}$"
_SEED_FILE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\.(csv|json)$"


class DatasetSeed(BaseModel):
    """A lookup table the starter creates from ``seeds/<file>`` (V6-22, ask #105).

    At create time the api reuses a table of this ``name`` in the agent's workspace when one
    exists and did not fail to import; otherwise it reads the file like an upload (the same
    parser and key normalisation) and stores the rows in the create's own transaction, so the
    table is ready before the agent is validated. Rows are demo data: ``name`` starts with
    ``Demo · `` for the shipped starters.
    """

    name: str = Field(min_length=1, max_length=120)
    file: str = Field(pattern=_SEED_FILE_PATTERN, description="A `.csv` or `.json` file under `seeds/`")
    key_columns: list[DatasetKeyColumn]
    """The columns lookups match on, and how their cells are normalised."""

    @field_validator("key_columns")
    @classmethod
    def _keys_bounded(cls, value: list[DatasetKeyColumn]) -> list[DatasetKeyColumn]:
        # A validator rather than `max_length`: the TS generator turns a small `maxItems` into a
        # union of tuples no editor can assign an array to.
        if not 1 <= len(value) <= MAX_DATASET_KEY_COLUMNS:
            raise ValueError(f"name 1 to {MAX_DATASET_KEY_COLUMNS} key columns")
        names = [column.name for column in value]
        if len(set(names)) != len(names):
            raise ValueError("a key column is named twice")
        return value


class TemplateKit(BaseModel):
    """A tool kit the starter adds to the new agent (``ToolKitInstantiate``, minus the agent).

    ``dataset`` names one of the starter's :attr:`StarterTemplate.dataset_seeds` for a
    ``dataset`` variant; ``key_columns`` defaults to that table's key columns. A starter never
    binds a key (``credential_id``) or a connected app, so kits whose variant needs one are
    refused when the catalogue loads.
    """

    kit_id: str = Field(pattern=_KIT_ID_PATTERN)
    variant: str | None = None
    block_prefix: str | None = Field(default=None, pattern=_KIT_PREFIX_PATTERN)
    settings: dict[str, str | int | float | bool] = {}
    dataset: str | None = Field(default=None, max_length=120)
    key_columns: list[str] | None = None
    add_test_case: bool = True


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
    # V6-22 (ask #105): extraction, rules and tests set on the seeded config, then the
    # lookup tables and kits created and added once the agent row exists.
    extraction: ExtractionConfig | None = None
    rules: Rules = []
    tests: list[AgentTest] = Field(default=[], max_length=MAX_AGENT_TESTS)
    dataset_seeds: list[DatasetSeed] = []
    kits: list[TemplateKit] = []

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
            raise ValueError("voice.greeting is not allowed on a template. Set the greeting overlay instead")
        if self.knowledge is not None and "kb_ids" in self.knowledge.model_fields_set:
            raise ValueError("knowledge.kb_ids is not allowed on a template. Use kb_seeds instead")
        if len(self.dataset_seeds) > MAX_TEMPLATE_DATASET_SEEDS:
            raise ValueError(f"a template seeds at most {MAX_TEMPLATE_DATASET_SEEDS} lookup tables")
        if len(self.kits) > MAX_TEMPLATE_KITS:
            raise ValueError(f"a template adds at most {MAX_TEMPLATE_KITS} kits")
        names = [seed.name for seed in self.dataset_seeds]
        if len(set(names)) != len(names):
            raise ValueError("dataset_seeds names must be unique")
        for kit in self.kits:
            if kit.dataset is not None and kit.dataset not in names:
                raise ValueError(
                    f"kit '{kit.kit_id}' names dataset '{kit.dataset}', which no dataset_seeds entry is"
                )
        test_ids = [test.id for test in self.tests]
        if len(set(test_ids)) != len(test_ids):
            raise ValueError("test case ids must be unique")
        return self
