"""Tool kits: a use case in one step — tools, panel blocks, instructions, variables and rules (V6-18).

D-V6-26 of ``docs/v6/PLAN-V6.md``; ``docs/CONTRACTS.md`` §7. A :class:`ToolKit` bundles
everything one job needs ("look a record up", "open a case", "verify the caller"): the tool
rows to create, the panel blocks they fill, a short instruction snippet, the extraction
fields and rules that drive the panel, optional flow steps and a test case whose tools answer
from **fakes** (``KitTool.fake``, the ``AgentTest.mocks`` of that case, so the kit can be tried
offline). A kit has one or more **variants** — where its tools come from
(:data:`KitSource`): an HTTP API (``http``), a preset MCP server (``mcp_preset``), a connected
app's actions (``composio_action``), a workspace lookup table (``dataset``) or nothing but the
panel and the built-ins (``none``).

The catalogue ships with the api (``lkap_api/templates/kits/*.json``) and is served by
``GET /v1/tool-kits``; ``POST /v1/tool-kits/{id}/instantiate`` (:class:`ToolKitInstantiate`)
adds a kit to one agent in one new configuration version, or previews it with ``dry_run``
(:class:`ToolKitInstantiated`). Adding a kit twice with the same ``block_prefix`` adds nothing
the second time: every item is matched by its name or id (a tool attached to the agent by
name, a block, rule, extraction field or flow node by id, the snippet by its markers
:func:`kit_markers`).

**Names.** A kit names what it creates from the ``block_prefix`` (``record`` →
tool ``record_lookup``, block ``record_results``, rule ``record_found``), so the same kit can
be added twice under two prefixes. The catalogue files write ``{{ kit.prefix }}``, the
admin's ``{{ kit.<setting> }}`` values (:attr:`ToolKit.defaults`), ``{{ kit.tool.<key> }}``
(the name a kit tool ends up with) and, for lookup tables, ``{{ kit.dataset_id }}`` and
``{{ kit.key_column }}``; the api renders them before anything is validated or stored, and
``GET /v1/tool-kits`` shows each kit rendered with its ``default_prefix`` and the settings'
examples. ``{{ secret.NAME }}``, ``{{ ctx.* }}``, ``{{ var.* }}`` and ``{{ arg }}`` are left for
the tools as usual. A flow fragment names the agent step it hangs off as
:data:`KIT_FLOW_ANCHOR`.

**Keys.** A kit never needs a key to be added: without ``credential_id`` an HTTP tool is stored
without the headers that carry ``{{ secret.* }}`` (the response says which), and a connected
app's variant waits until the app is connected. Snippets are admin-authored (not fenced) and
hold no link and no key name (:func:`snippet_problems`).
"""

from __future__ import annotations

import re
from typing import Any, Final, Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from lkap_contracts.api_models import ValidationResult
from lkap_contracts.common import NODE_ID_PATTERN
from lkap_contracts.extraction import ExtractionField
from lkap_contracts.flow import FlowEdge, FlowNode, VariableSpec
from lkap_contracts.rules import Rule
from lkap_contracts.tools import TOOL_NAME_PATTERN, TOOL_TEMPLATE_ID_PATTERN, ToolDefinition
from lkap_contracts.ui_protocol import BlockSpec

__all__ = [
    "KIT_FLOW_ANCHOR",
    "KIT_ID_PATTERN",
    "KIT_MODELS",
    "KIT_PREFIX_PATTERN",
    "MAX_KIT_SNIPPET_CHARS",
    "KitApp",
    "KitAppAction",
    "KitBlock",
    "KitChange",
    "KitChangeKind",
    "KitChangeStatus",
    "KitExtraction",
    "KitFlowFragment",
    "KitRequires",
    "KitSetting",
    "KitSettingKind",
    "KitSource",
    "KitTestCase",
    "KitTool",
    "KitToolPlan",
    "KitVariant",
    "ToolKit",
    "ToolKitInstantiate",
    "ToolKitInstantiated",
    "ToolKitsResponse",
    "kit_markers",
    "snippet_problems",
]

#: Kit ids (``record_lookup``).
KIT_ID_PATTERN: Final[str] = r"^[a-z][a-z0-9_]{0,39}$"
#: A ``block_prefix``: short, so the names it builds stay within their limits.
KIT_PREFIX_PATTERN: Final[str] = r"^[a-z][a-z0-9_]{0,23}$"
#: Variant ids, tool keys and setting names.
_KEY_PATTERN: Final[str] = r"^[a-z][a-z0-9_]{0,31}$"
#: The longest instruction snippet.
MAX_KIT_SNIPPET_CHARS: Final[int] = 2000
#: What a flow fragment's edges call the agent step it is added after.
KIT_FLOW_ANCHOR: Final[str] = "@anchor"

#: Where a variant's tools come from; ``none``: only blocks, instructions, variables and rules.
KitSource = Literal["http", "mcp_preset", "composio_action", "dataset", "none"]
#: How a setting is checked: an ``https`` address, a site name, text or a whole number.
KitSettingKind = Literal["url", "host", "text", "integer"]

_URL_RE: Final = re.compile(r"://|\bwww\.|\b[a-z0-9-]+\.(?:com|net|org|io|app|dev|co)\b", re.IGNORECASE)
_KEY_NAME_RE: Final = re.compile(
    r"\bsecret\.|\b[A-Z][A-Z0-9]*_(?:KEY|TOKEN|SECRET|PASSWORD)\b|\b(?:api[_ -]?key|access[_ -]?token)\b",
    re.IGNORECASE,
)


def snippet_problems(text: str) -> list[str]:
    """Why an instruction snippet may not ship, in plain words (empty: it may).

    A snippet is admin-authored text appended to an agent's instructions: it may not hold a
    link or a site name, a key name, the kit markers, or a placeholder left unrendered.
    """
    problems: list[str] = []
    if not text.strip():
        problems.append("the snippet is empty")
    if len(text) > MAX_KIT_SNIPPET_CHARS:
        problems.append(f"the snippet is longer than {MAX_KIT_SNIPPET_CHARS} characters")
    if _URL_RE.search(text):
        problems.append("the snippet names a link or a site")
    if _KEY_NAME_RE.search(text):
        problems.append("the snippet names a key")
    if "<!--" in text or "-->" in text:
        problems.append("the snippet holds a comment marker")
    if "{{" in text or "}}" in text:
        problems.append("the snippet holds a placeholder")
    return problems


def kit_markers(kit_id: str, prefix: str) -> tuple[str, str]:
    """The comment lines around a kit's snippet in the agent's instructions (open, close)."""
    return f"<!-- kit:{kit_id}:{prefix} -->", f"<!-- /kit:{kit_id}:{prefix} -->"


def _unique(values: list[str], what: str) -> None:
    duplicates = sorted({value for value in values if values.count(value) > 1})
    if duplicates:
        raise ValueError(f"{what} must be unique: {', '.join(duplicates)}")


class KitSetting(BaseModel):
    """A value the admin gives when adding the kit (``ToolKit.defaults``): an address, a site, an id."""

    name: str = Field(pattern=_KEY_PATTERN, description="Written `{{ kit.<name> }}` in the catalogue")
    label: str = Field(min_length=1, max_length=80)
    help: str | None = Field(default=None, max_length=300)
    kind: KitSettingKind = "text"
    required: bool = False
    """The kit cannot be added without it (for the variants in :attr:`variants`)."""
    example: str | None = Field(default=None, max_length=200)
    """What the catalogue preview shows; never used as the stored value."""
    default: str | None = Field(default=None, max_length=200)
    """Used when the admin gives none (optional settings only)."""
    variants: list[str] = []
    """The variants that use it (empty: all of them)."""


class KitTool(BaseModel):
    """One tool a variant creates: a definition (or a tool template) and the fake a test uses."""

    key: str = Field(pattern=_KEY_PATTERN, description="Written `{{ kit.tool.<key> }}` in snippets and rules")
    label: str = Field(min_length=1, max_length=80)
    risk: Literal["read", "write"] = "read"
    definition: ToolDefinition | None = None
    """The tool as it is stored (rendered). HTTP tools get ``allowed_hosts`` from their url."""
    template: str | None = Field(default=None, pattern=TOOL_TEMPLATE_ID_PATTERN)
    """A tool template id (``cal_com.booking_create``) used instead of a definition."""
    only_with: Literal["sms"] | None = None
    """``sms``: added only when the agent can send text messages (``tools.sms``)."""
    fake: Any = None
    """What the tool answers in the kit's test case instead of calling out (``AgentTest.mocks``)."""

    @model_validator(mode="after")
    def _one_source(self) -> Self:
        if (self.definition is None) == (self.template is None):
            raise ValueError(f"kit tool '{self.key}' needs exactly one of definition or template")
        return self


class KitAppAction(BaseModel):
    """One action of a connected app a ``composio_action`` variant picks."""

    slug: str = Field(min_length=1, max_length=200, description="The provider's action slug")
    key: str = Field(pattern=_KEY_PATTERN)
    label: str = Field(min_length=1, max_length=80)
    risk: Literal["read", "write"] = "write"
    fake: Any = None


class KitApp(BaseModel):
    """The actions a ``composio_action`` variant picks when the connected app is this one."""

    toolkit: str = Field(pattern=r"^[a-z0-9_]{1,64}$", description="The app (lower case), e.g. `zendesk`")
    label: str = Field(min_length=1, max_length=80)
    actions: list[KitAppAction] = Field(min_length=1)

    @model_validator(mode="after")
    def _keys(self) -> Self:
        _unique([action.key for action in self.actions], f"action keys of '{self.toolkit}'")
        return self


class KitRequires(BaseModel):
    """What a variant needs, for the gallery (nothing here is needed to add it)."""

    secret_names: list[str] = []
    """The names a tool-secret key must hold for the tools to authenticate."""
    apps: list[str] = []
    """The connected apps (toolkits) it can use."""
    dataset: str | None = Field(default=None, max_length=300)
    """The lookup table it reads, in words."""
    min_key_columns: int = Field(default=1, ge=1, le=8)
    """A ``dataset`` variant: the fewest key columns its lookup must match on together."""
    sms: bool = False
    """Its optional one-time-code step needs text messages (``tools.sms``)."""


class KitBlock(BlockSpec):
    """A block the kit adds to the panel.

    ``shared`` blocks are the panel's one-of-a-kind blocks (the "still needed" checklist, the
    status stamp): a panel that already has a block of that type keeps it and gains nothing.
    """

    shared: bool = False


class KitExtraction(BaseModel):
    """How the kit changes ``AgentConfig.extraction`` besides adding its fields."""

    enabled: bool = True
    """Turn live extraction on."""
    still_needed: Literal["checklist"] | None = None
    """List the required fields not captured yet on the checklist (kept when already set)."""


class KitFlowFragment(BaseModel):
    """Steps added to an agent's existing flow after one of its steps (:data:`KIT_FLOW_ANCHOR`).

    Added only when the request names that step (``flow_anchor``) and the agent has a flow;
    an agent without a flow never becomes one.
    """

    nodes: list[FlowNode] = Field(min_length=1)
    edges: list[FlowEdge] = []
    """Edges among the nodes and to or from :data:`KIT_FLOW_ANCHOR`."""
    variables: list[VariableSpec] = []
    """Flow variables the steps read or bind (added when the flow lacks them)."""
    anchor_extract: list[str] = []
    """Variables the anchor step captures for the steps (added to its ``extract`` when it is
    an agent step)."""


class KitTestCase(BaseModel):
    """The simulated conversation the kit adds; its tools answer from their fakes."""

    name: str = Field(min_length=1, max_length=120)
    persona_instructions: str = Field(min_length=1, max_length=4000)
    scenario: str = Field(default="", max_length=4000)
    expectations: list[str] = []


class KitVariant(BaseModel):
    """One way to run the kit: where its tools come from, and what only this way adds."""

    id: str = Field(pattern=_KEY_PATTERN)
    label: str = Field(min_length=1, max_length=80)
    summary: str = Field(min_length=1, max_length=300)
    source: KitSource
    tools: list[KitTool] = []
    apps: list[KitApp] = []
    """``composio_action``: the actions to pick, per app (the connected app decides which)."""
    requires: KitRequires = KitRequires()
    blocks: list[KitBlock] = []
    """Blocks only this variant adds (after the kit's own)."""
    variables: list[ExtractionField] = []
    """Extraction fields only this variant adds."""
    rules: list[Rule] = []
    """Rules only this variant adds."""
    instructions_snippet: str | None = Field(default=None, max_length=MAX_KIT_SNIPPET_CHARS)
    """Replaces the kit's snippet for this variant."""
    flow_nodes: KitFlowFragment | None = None
    """Replaces the kit's flow fragment for this variant."""
    configures: list[Literal["notify_team"]] = []
    """Built-in settings the request's ``credential_id`` fills: ``notify_team`` sets
    ``tools.notify_team`` with that key (the team's webhook address), when it is not set yet."""

    @model_validator(mode="after")
    def _source_fits(self) -> Self:
        _unique([tool.key for tool in self.tools], f"tool keys of variant '{self.id}'")
        kinds = {tool.definition.kind if tool.definition is not None else "http" for tool in self.tools}
        match self.source:
            case "http":
                ok = bool(self.tools) and kinds == {"http"} and not self.apps
            case "dataset":
                ok = "dataset" in kinds and kinds <= {"dataset", "http"} and not self.apps
            case "mcp_preset":
                ok = len(self.tools) == 1 and kinds == {"mcp"} and not self.apps
            case "composio_action":
                ok = bool(self.apps) and not self.tools
            case "none":
                ok = not self.tools and not self.apps
        if not ok:
            raise ValueError(f"variant '{self.id}': its tools do not fit source '{self.source}'")
        return self


class ToolKit(BaseModel):
    """A catalogue kit (``GET /v1/tool-kits``), shown rendered with its default prefix."""

    id: str = Field(pattern=KIT_ID_PATTERN)
    name: str = Field(min_length=1, max_length=60)
    summary: str = Field(min_length=1, max_length=300)
    default_prefix: str = Field(pattern=KIT_PREFIX_PATTERN)
    default_variant: str
    variants: list[KitVariant] = Field(min_length=1)
    defaults: list[KitSetting] = []
    """The settings the admin gives when adding the kit (addresses, sites, ids)."""
    blocks: list[KitBlock] = []
    instructions_snippet: str = Field(min_length=1, max_length=MAX_KIT_SNIPPET_CHARS)
    """Appended to the agent's instructions between :func:`kit_markers` (admin-authored)."""
    variables: list[ExtractionField] = []
    """Extraction fields every variant adds."""
    extraction: KitExtraction | None = None
    """How the kit changes live extraction (``None``: it adds fields only, if any)."""
    rules: list[Rule] = []
    flow_nodes: KitFlowFragment | None = None
    test_case: KitTestCase | None = None
    docs_url: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        ids = [variant.id for variant in self.variants]
        _unique(ids, "variant ids")
        if self.default_variant not in ids:
            raise ValueError(f"default_variant '{self.default_variant}' is not a variant")
        _unique([setting.name for setting in self.defaults], "setting names")
        for setting in self.defaults:
            unknown = sorted(set(setting.variants) - set(ids))
            if unknown:
                raise ValueError(f"setting '{setting.name}' names unknown variant(s): {', '.join(unknown)}")
        return self

    def variant(self, variant_id: str | None) -> KitVariant | None:
        """The variant ``variant_id`` names (``None``: the default one), or ``None``."""
        wanted = variant_id or self.default_variant
        return next((variant for variant in self.variants if variant.id == wanted), None)


class ToolKitsResponse(BaseModel):
    """``GET /v1/tool-kits``: every kit, in catalogue order."""

    items: list[ToolKit]


class ToolKitInstantiate(BaseModel):
    """``POST /v1/tool-kits/{id}/instantiate``: add a kit to one agent (or preview it).

    ``credential_id`` is a tool-secret key holding the variant's ``requires.secret_names``
    (binding it needs ``admin`` and ``providers:write``); without it the HTTP tools are stored
    without their key headers. A ``composio_action`` variant needs ``connection_id`` (a
    connected app) and, like picking app actions, ``admin`` and ``providers:write``. A
    ``dataset`` variant needs ``dataset_id``; ``key_columns`` defaults to the table's key columns.
    """

    agent_id: str
    variant: str | None = Field(default=None, description="A variant id (default: the kit's default)")
    block_prefix: str | None = Field(
        default=None, pattern=KIT_PREFIX_PATTERN, description="Names what the kit adds (default: the kit's)"
    )
    settings: dict[str, str | int | float | bool] = Field(
        default={}, description="Values of the kit's `defaults`, e.g. {'base_url': 'https://…'}"
    )
    credential_id: str | None = None
    connection_id: str | None = None
    actions: list[str] | None = Field(
        default=None, description="composio_action: the action slugs to pick instead of the kit's"
    )
    dataset_id: str | None = None
    key_columns: list[str] | None = None
    flow_anchor: str | None = Field(
        default=None, pattern=NODE_ID_PATTERN, description="Add the kit's flow steps after this step"
    )
    add_test_case: bool = Field(
        default=True, description="Add the kit's test case (its tools answer from fakes)"
    )
    dry_run: bool = Field(default=False, description="Only say what would be added; change nothing")

    @field_validator("actions")
    @classmethod
    def _actions(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and not 1 <= len(value) <= 20:
            raise ValueError("pick 1 to 20 actions")
        return value


KitChangeKind = Literal[
    "tool",
    "block",
    "instructions",
    "variable",
    "extraction",
    "rule",
    "flow_node",
    "flow_edge",
    "flow_variable",
    "test_case",
    "notify_team",
]
#: ``added`` (or would be, on a dry run), ``exists`` (kept as it is), ``skipped`` (see the note).
KitChangeStatus = Literal["added", "exists", "skipped"]


class KitChange(BaseModel):
    """One thing an instantiation adds, keeps or leaves out."""

    kind: KitChangeKind
    id: str
    label: str = ""
    status: KitChangeStatus
    note: str | None = None


class KitToolPlan(BaseModel):
    """A tool of the kit as it is (or would be) stored."""

    key: str
    name: str = Field(pattern=TOOL_NAME_PATTERN)
    kind: Literal["http", "mcp", "provider", "dataset"]
    status: KitChangeStatus
    tool_id: str | None = None
    """The row (``None`` on a dry run for a tool not created yet)."""
    definition: ToolDefinition | None = None
    """The definition as stored (``None`` for app actions on a dry run: the app decides it)."""


class ToolKitInstantiated(BaseModel):
    """What ``POST /v1/tool-kits/{id}/instantiate`` added, kept and left out (or would, on a dry run)."""

    kit_id: str
    variant: str
    prefix: str
    dry_run: bool
    changes: list[KitChange]
    tools: list[KitToolPlan]
    tool_ids: list[str] = []
    """Every tool of the kit attached to the agent (created or kept)."""
    instructions_snippet: str
    """The snippet as it is (or would be) appended, markers included."""
    config_version: int | None = None
    """The agent's configuration version afterwards (unchanged when nothing was added)."""
    notes: list[str] = []
    """What is left to do, in plain words (add a key, sign in to a server, connect an app)."""
    validation: ValidationResult


#: The kit models, registered in ``export.py`` with one line (``**KIT_MODELS``).
KIT_MODELS: dict[str, type[BaseModel]] = {
    "ToolKit": ToolKit,
    "ToolKitsResponse": ToolKitsResponse,
    "ToolKitInstantiate": ToolKitInstantiate,
    "ToolKitInstantiated": ToolKitInstantiated,
    "KitVariant": KitVariant,
    "KitTool": KitTool,
    "KitApp": KitApp,
    "KitAppAction": KitAppAction,
    "KitRequires": KitRequires,
    "KitSetting": KitSetting,
    "KitBlock": KitBlock,
    "KitExtraction": KitExtraction,
    "KitFlowFragment": KitFlowFragment,
    "KitTestCase": KitTestCase,
    "KitChange": KitChange,
    "KitToolPlan": KitToolPlan,
}
