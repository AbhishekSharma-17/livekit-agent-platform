"""Declarative tool definitions (admin-authored HTTP tools and MCP servers) and the
platform's built-in tool names.

The built-in names are the single source the worker, the api and the web share
(asks V2-16-3): ``lkap_agent.tools.builtin`` registers them,
``lkap_api.flows.validation`` checks flow node tool references against them, and
``generated/builtin_tools.json`` carries them to the console.
"""

from datetime import datetime
from typing import Annotated, Any, Final, Literal, Self

from pydantic import BaseModel, Field, model_validator

from lkap_contracts.tool_providers import ToolProviderId

#: Model-facing tool names must match this pattern.
TOOL_NAME_PATTERN = r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$"

#: Every built-in tool name, in the order the worker's ``build_builtin_tools``
#: considers them; the names ``AgentConfig.tools.builtin_disabled`` refers to.
BUILTIN_TOOL_NAMES: Final[tuple[str, ...]] = (
    "end_call",
    "search_knowledge",
    "http_request",
    "describe_current_frame",
    "pin_frame",
    "push_note",
    "set_status",
    "escalate_to_human",
    "current_time",
)

#: Built-ins registered only when the agent has camera or screen share on.
VISION_TOOL_NAMES: Final[frozenset[str]] = frozenset({"describe_current_frame", "pin_frame"})

#: Panel-block tools (CONTRACTS-V2 §4.4). Not in :data:`BUILTIN_TOOL_NAMES`: each is
#: registered only when the panel has a block it can write (``builtin_disabled``
#: still switches it off).
BLOCK_TOOL_NAMES: Final[tuple[str, ...]] = (
    "update_block",
    "show_document",
    "table_append",
    "request_form",
    "request_choice",
    "resolve_choice",
    "set_details",
    "show_text",
    "set_steps",
)

#: Block types whose state ``update_block`` may write (envelope blocks, forms and
#: choices have their own tools: a request's status belongs to the request).
UPDATABLE_BLOCK_TYPES: Final[frozenset[str]] = frozenset(
    {
        "document",
        "gallery",
        "table",
        "transcript",
        "video",
        "kb_citations",
        "custom",
        "details",
        "markdown",
        "steps",
    }
)

#: Each block tool → the panel block types that make the worker register it.
#: ``set_steps`` is further limited to a ``steps`` block whose ``config.source`` is
#: not ``"flow"`` (the flow writes those itself, V5-08).
BLOCK_TOOL_TYPES: Final[dict[str, frozenset[str]]] = {
    "update_block": UPDATABLE_BLOCK_TYPES,
    "show_document": frozenset({"document"}),
    "table_append": frozenset({"table"}),
    "request_form": frozenset({"form"}),
    "request_choice": frozenset({"choices"}),
    "resolve_choice": frozenset({"choices"}),
    "set_details": frozenset({"details"}),
    "show_text": frozenset({"markdown"}),
    "set_steps": frozenset({"steps"}),
}


def builtin_tools_document() -> dict[str, Any]:
    """The built-in tool names as plain JSON (``generated/builtin_tools.json``).

    Returns:
        ``{builtin_tool_names, vision_tool_names, block_tool_names, block_tool_types}``;
        sets are sorted so the export is deterministic.
    """
    return {
        "builtin_tool_names": list(BUILTIN_TOOL_NAMES),
        "vision_tool_names": sorted(VISION_TOOL_NAMES),
        "block_tool_names": list(BLOCK_TOOL_NAMES),
        "block_tool_types": {name: sorted(types) for name, types in BLOCK_TOOL_TYPES.items()},
    }


#: How one tool runs relative to the conversation (docs/v4/BACKGROUND-TOOLS.md D-V4-31):
#: ``blocking`` waits for the result (the default), ``background`` announces at once and
#: reports the result when the agent is next idle, ``auto`` waits inline up to
#: ``auto_threshold_ms`` and then behaves like ``background``.
ToolExecutionMode = Literal["blocking", "background", "auto"]
#: What happens when the model calls a tool that is already running (D-V4-32).
DuplicatePolicy = Literal["allow", "reject", "replace", "confirm"]
#: What counts as "the same call" for :data:`DuplicatePolicy`.
DuplicateScope = Literal["name", "name_and_args"]

#: The modes that let the conversation continue while the tool runs.
NON_BLOCKING_MODES: Final[frozenset[str]] = frozenset({"background", "auto"})


class ToolExecution(BaseModel):
    """How one tool runs relative to the conversation (BACKGROUND-TOOLS.md §2).

    The agent-level default (``ToolsConfig.execution_default``) only ever reaches read
    tools: GET HTTP tools and the built-ins in :data:`BACKGROUNDABLE_BUILTINS`. The
    tools in :data:`NEVER_BACKGROUND_TOOLS` and the flow edge tools always block.
    """

    mode: ToolExecutionMode | None = None
    """None = the agent's `tools.execution_default` when the tool is a read tool, else `blocking`."""
    announce: str | None = None
    """What the model is told on the first update; None = "Working on <label>."
    Spoken in the model's words."""
    auto_threshold_ms: int = Field(default=700, ge=0, le=5000)
    """`auto` only: how long the tool runs inline before the agent announces it."""
    fillers: list[str] = Field(default=[], max_length=5)
    """Spoken verbatim after `filler_delay_s` of idle, one per interval; needs a TTS."""
    filler_delay_s: float = Field(default=4.0, ge=0.5, le=30)
    """Seconds of continuous idle before a filler is spoken."""
    filler_interval_s: float = Field(default=8.0, ge=1, le=60)
    """Seconds between two fillers."""
    cancellable: bool | None = None
    """None = true for read tools, false otherwise."""
    on_duplicate: DuplicatePolicy | None = None
    """None = "reject" for read tools, "confirm" otherwise."""
    duplicate_scope: DuplicateScope = "name_and_args"
    """What counts as the same call for `on_duplicate`."""
    max_duration_s: float = Field(default=60, gt=0, le=600)
    """Not applied to MCP tools: their bound is the server's `timeout_s`."""
    report_progress: bool = False
    """MCP tools only: forward the server's progress notifications as updates (ignored elsewhere)."""


#: Built-ins the agent-level default may background.
BACKGROUNDABLE_BUILTINS: Final[frozenset[str]] = frozenset(
    {"search_knowledge", "http_request", "describe_current_frame"}
)
#: Tools that are never non-blocking (validator error; the worker ignores it with a warning).
#: ``transfer_call`` and ``send_dtmf`` are the telephony tools (``lkap_agent.telephony``).
#: Every flow edge tool (``go_to_*``) is never non-blocking either: see :func:`never_background`.
NEVER_BACKGROUND_TOOLS: Final[frozenset[str]] = frozenset(
    {
        "end_call",
        "transfer_call",
        "send_dtmf",
        "request_form",
        # The V5-08 block tools: a request waits for the caller (D-V5-34); the
        # others write the panel, like update_block.
        "request_choice",
        "resolve_choice",
        "set_details",
        "show_text",
        "set_steps",
        "escalate_to_human",
        "update_block",
        "show_document",
        "table_append",
        "push_note",
        "set_status",
        "pin_frame",
        "current_time",
        # Composio Tool Router meta tools (docs/v5/COMPOSIO.md D-V5-C7): running an action,
        # opening a connection or waiting on one always waits for the result.
        "COMPOSIO_MULTI_EXECUTE_TOOL",
        "COMPOSIO_MANAGE_CONNECTIONS",
        "COMPOSIO_WAIT_FOR_CONNECTIONS",
    }
)
#: Flow edge tools are named ``go_to_<node>`` (:func:`lkap_contracts.flow.edge_tool_name`).
EDGE_TOOL_PREFIX: Final[str] = "go_to_"


def never_background(name: str) -> bool:
    """Whether the tool ``name`` must always block (the never-list or a flow edge tool)."""
    return name in NEVER_BACKGROUND_TOOLS or name.startswith(EDGE_TOOL_PREFIX)


class HttpToolDefinition(BaseModel):
    """An HTTP call exposed to the model as a raw-schema function tool."""

    kind: Literal["http"] = "http"
    name: str = Field(pattern=TOOL_NAME_PATTERN)
    description: str
    parameters: dict[str, Any]
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "POST"
    url: str
    headers: dict[str, str] = {}
    credential_id: str | None = None
    body_template: str | None = None
    allowed_hosts: list[str] = []
    timeout_s: float = 10
    max_result_chars: int = 4000
    result_path: str | None = None
    silent_reply: bool = False
    execution: ToolExecution = Field(default_factory=ToolExecution)
    """How the tool runs (docs/v4/BACKGROUND-TOOLS.md). A GET tool without a ``mode`` follows
    the agent's ``tools.execution_default``; any other method blocks unless it sets one."""

    @model_validator(mode="after")
    def _silent_reply_blocks(self) -> Self:
        if self.silent_reply and self.execution.mode in NON_BLOCKING_MODES:
            raise ValueError(
                "silent_reply cannot be combined with a background or automatic execution mode: "
                "the silenced reply would swallow the tool's announcement"
            )
        return self


#: Which provisioned server an origin-tagged MCP definition is (D-V5-C6): ``server`` exposes the
#: picked actions directly, ``router`` exposes the provider's search/execute meta tools.
McpOriginKind = Literal["server", "router"]


class McpServerOrigin(BaseModel):
    """Where a provider-provisioned MCP server comes from (docs/v5/COMPOSIO.md §3)."""

    provider: ToolProviderId = "composio"
    kind: McpOriginKind
    remote_id: str = Field(min_length=1, max_length=200)
    """The provider's id for it (a Tool Router session id)."""
    config_hash: str | None = Field(default=None, max_length=64)
    """A fingerprint of what the api asked the provider for; a save that changes it re-provisions."""


class McpNoAuth(BaseModel):
    """The MCP server needs no credentials (a public server)."""

    kind: Literal["none"] = "none"


class McpHeaderAuth(BaseModel):
    """Static request headers, typically an API key (research-v4 tools §4.3.1).

    Header values may reference ``{{ secret.NAME }}``; the api substitutes them from
    ``credential_id`` (an ``http-tool-secret`` bag) when a session resolves.
    """

    kind: Literal["header"] = "header"
    headers: dict[str, str] = {}
    credential_id: str | None = None


class McpOAuthAuth(BaseModel):
    """Sign in through the server's OAuth provider (V5-14; saved only once V5-14 lands).

    The api is the OAuth client: it runs discovery, registration, consent and the token
    exchange, keeps the tokens in an ``mcp-oauth`` credential, and hands the worker a
    short-lived access token at session start (research-v4 tools §4.3).
    """

    kind: Literal["oauth"] = "oauth"
    credential_id: str | None = None
    """The ``mcp-oauth`` credential once connected."""
    registration: Literal["auto", "preregistered"] = "auto"
    client_id: str | None = None
    """Pre-registered clients only."""
    client_secret_ref: str | None = None
    """The name of a key in the credential bag holding the client secret (pre-registered only)."""
    scopes: list[str] | None = None
    """An admin override; ``None`` follows the spec's scope strategy."""
    subject: Literal["workspace", "agent"] = "workspace"


#: How the worker authenticates to an MCP server, discriminated on ``kind``.
McpAuth = Annotated[McpNoAuth | McpHeaderAuth | McpOAuthAuth, Field(discriminator="kind")]


class McpToolSnapshot(BaseModel):
    """One tool of a server's ``tools/list`` answer, as ``POST /v1/tools/{id}/test`` stored it."""

    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    input_schema: dict[str, Any] | None = None


class McpTestResult(BaseModel):
    """``POST /v1/tools/{id}/test``: connect, ``initialize``, ``tools/list``, store the snapshot."""

    ok: bool
    tool_names: list[str] = []
    tool_count: int = 0
    cached_at: datetime | None = None
    """When the snapshot was stored (only when ``ok``)."""
    duration_ms: int = 0
    reason: (
        Literal["blocked_destination", "needs_auth", "unreachable", "protocol_error", "http_error"] | None
    ) = None
    """Why the test failed, when it did."""
    error: str | None = None
    """A value-free sentence for the console (never a header, a token or the full url)."""


class McpServerDefinition(BaseModel):
    """A streamable-HTTP MCP server attached to the agent.

    ``auth`` says how the worker authenticates. ``headers`` and ``credential_id`` are the
    pre-V5-09 shape, kept as **deprecated mirrors** of header auth so readers that have not
    moved to ``auth`` yet keep working: a definition that sets them without ``auth`` (or
    with ``auth.kind == "none"``) folds them into :class:`McpHeaderAuth`; with ``auth`` set
    they are filled from it (``credential_id`` also mirrors an OAuth credential), and a
    value that disagrees with ``auth`` is an error. Stored rows need no data migration:
    they load as header auth and re-save with ``auth``.
    """

    kind: Literal["mcp"] = "mcp"
    name: str
    url: str
    auth: McpAuth = Field(default_factory=McpNoAuth)
    headers: dict[str, str] = {}
    """Deprecated: mirrors ``auth.headers`` for header auth (set ``auth`` instead)."""
    credential_id: str | None = None
    """Deprecated: mirrors ``auth.credential_id`` (set ``auth`` instead)."""
    allowed_tools: list[str] | None = None
    timeout_s: float = 5
    sse_read_timeout_s: float = 300
    tool_options: dict[str, ToolExecution] = {}
    """How each MCP tool runs, by tool name (a subset of ``allowed_tools`` when that is set).
    MCP tools never follow the agent default; their announcement comes from the server's
    progress notifications (``report_progress``)."""
    origin: McpServerOrigin | None = None
    """Set on servers LKAP provisions for a tool provider (Composio's app server or tool finder,
    docs/v5/COMPOSIO.md D-V5-C6). The api manages these rows; the worker connects to them only
    over ``https`` on the provider's host."""
    cached_tools: list[McpToolSnapshot] | None = None
    """The server's tools as ``POST /v1/tools/{id}/test`` last listed them, for the console.
    The worker still lists tools itself at session start (research-v4 tools §4.3.7)."""
    cached_at: datetime | None = None
    """When ``cached_tools`` was stored."""

    @model_validator(mode="after")
    def _fold_legacy_auth(self) -> Self:
        legacy_set = bool(self.headers) or self.credential_id is not None
        auth = self.auth
        if isinstance(auth, McpNoAuth):
            if legacy_set:
                self.auth = McpHeaderAuth(headers=dict(self.headers), credential_id=self.credential_id)
            return self
        if isinstance(auth, McpHeaderAuth):
            if legacy_set and (self.headers != auth.headers or self.credential_id != auth.credential_id):
                raise ValueError(
                    "headers/credential_id are deprecated mirrors of auth and disagree with "
                    "auth.headers/auth.credential_id: set auth only"
                )
            self.headers = dict(auth.headers)
            self.credential_id = auth.credential_id
            return self
        if self.headers:
            raise ValueError("an oauth MCP server takes no static headers")
        if self.credential_id is not None and self.credential_id != auth.credential_id:
            raise ValueError("credential_id disagrees with auth.credential_id: set auth only")
        self.credential_id = auth.credential_id
        return self

    @model_validator(mode="after")
    def _tool_options_are_allowed(self) -> Self:
        if self.allowed_tools is not None:
            unknown = sorted(set(self.tool_options) - set(self.allowed_tools))
            if unknown:
                raise ValueError(f"tool_options names tools outside allowed_tools: {', '.join(unknown)}")
        return self


class ProviderToolDefinition(BaseModel):
    """One action of a connected app run through a tool provider (docs/v5/COMPOSIO.md §3, D-V5-C8).

    Created by materialisation (``POST /v1/tool-providers/composio/materialise``): the
    parameters are pinned from the provider's schema at import, the description is its first
    sentence (editable). The worker runs it with ``POST /api/v3.1/tools/execute/{tool_slug}``
    on the provider's host. ``headers`` carry the provider key as a ``{{ secret.NAME }}``
    placeholder that the api substitutes from ``credential_id`` when a session resolves, as
    for HTTP tools; ``connection_id`` is the connected-app row, ``subject`` the provider's
    ``user_id`` copied from it.
    """

    kind: Literal["provider"] = "provider"
    provider: ToolProviderId = "composio"
    name: str = Field(pattern=TOOL_NAME_PATTERN)
    description: str
    parameters: dict[str, Any]
    """The action's JSON Schema input, pinned at import ("Refresh schema" diffs it)."""
    tool_slug: str = Field(min_length=1, max_length=200)
    """The provider's action slug, e.g. ``GOOGLECALENDAR_FIND_FREE_SLOTS``."""
    toolkit: str = ""
    """The app the action belongs to (lower case), e.g. ``googlecalendar``."""
    connection_id: str
    """The connected-app row (``credentials`` of provider ``tool-provider-account``)."""
    credential_id: str | None = None
    """The provider key row (``credentials`` of provider ``composio``); cleared once resolved."""
    subject: str
    """The provider's ``user_id``: ``ws:<workspace_id>`` or ``agent:<agent_id>``."""
    connected_account_id: str | None = None
    """The provider's connected account to run as; ``None`` lets the provider pick the
    subject's account for the app (the api does not store the vendor id on the tool)."""
    headers: dict[str, str] = Field(default_factory=lambda: {"x-api-key": "{{ secret.api_key }}"})
    """Sent with every execute call; secrets are substituted by the api, never stored resolved."""
    timeout_s: float = Field(default=10, gt=0, le=60)
    max_result_chars: int = Field(default=1500, ge=100, le=20000)
    result_path: str | None = "data"
    """A top-level key of the execute response (``data``) or a JSON pointer (``/data/items``)."""
    silent_reply: bool = False
    execution: ToolExecution = Field(default_factory=ToolExecution)
    """How the action runs (docs/v4/BACKGROUND-TOOLS.md): reads are ``auto``, writes block."""
    schema_version: str | None = None
    """The provider's tool version at import; ``None`` runs the latest."""
    risk: Literal["read", "write", "destructive"] = "write"
    """How risky the action is (D-V5-C7); destructive actions always block."""

    @model_validator(mode="after")
    def _silent_reply_blocks(self) -> Self:
        if self.silent_reply and self.execution.mode in NON_BLOCKING_MODES:
            raise ValueError(
                "silent_reply cannot be combined with a background or automatic execution mode: "
                "the silenced reply would swallow the tool's announcement"
            )
        if self.risk == "destructive" and self.execution.mode in NON_BLOCKING_MODES:
            raise ValueError("a destructive action always runs blocking")
        return self


ToolDefinition = Annotated[
    HttpToolDefinition | McpServerDefinition | ProviderToolDefinition, Field(discriminator="kind")
]
