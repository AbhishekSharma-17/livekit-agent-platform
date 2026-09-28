"""Declarative tool definitions (admin-authored HTTP tools and MCP servers) and the
platform's built-in tool names.

The built-in names are the single source the worker, the api and the web share
(asks V2-16-3): ``lkap_agent.tools.builtin`` registers them,
``lkap_api.flows.validation`` checks flow node tool references against them, and
``generated/builtin_tools.json`` carries them to the console.
"""

import json
import re
from datetime import datetime
from typing import Annotated, Any, Final, Literal, Self, get_args

from pydantic import BaseModel, Field, field_validator, model_validator

from lkap_contracts.datasets import (
    DATASET_COLUMN_NAME_PATTERN,
    MAX_DATASET_COLUMNS,
    MAX_DATASET_KEY_COLUMNS,
    MAX_DATASET_LOOKUP_ROWS,
    DatasetMatch,
)
from lkap_contracts.tool_context import (
    MAX_PINNED_ARGUMENTS,
    Bindings,
    ConfirmReadback,
    PinnedValue,
    RequiresVars,
    ToolContextSpec,
    placeholder_issues,
)
from lkap_contracts.tool_providers import ToolProviderId
from lkap_contracts.ui_protocol import BlockType

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
    "convert_time",
    "describe_asset",
    # V5-25 (curated P0): two local helpers, registered by default like the time tools,
    "calculate",
    "spell_back",
    # and four network tools, each registered only when the agent configures it
    # (:data:`CONFIGURED_BUILTINS`).
    "web_search",
    "fetch_url",
    "send_sms",
    "notify_team",
    # V5-31: registered only when the agent lists more than one language
    # (``AgentConfig.voice.languages``); see :data:`LANGUAGE_TOOL_NAMES`.
    "switch_language",
    # V6-06: a picture from the agent's ``pipeline.image_gen`` model into a gallery block;
    # registered only when that slot resolves and the panel has a gallery.
    "generate_image",
    # V6-13: runs the live extraction now; registered only when ``extraction`` is on with a
    # ``manual`` trigger (``AgentConfig.extraction.triggers``).
    "extract_now",
)

#: Built-ins that reach a vendor or the internet and are registered only when the agent
#: configures them (V5-25): ``web_search`` with ``tools.web_search``, ``fetch_url`` with a
#: non-empty ``tools.fetch_url_allowed_hosts``, ``send_sms`` with ``tools.sms``,
#: ``notify_team`` with ``tools.notify_team``. ``builtin_disabled`` still switches them off.
CONFIGURED_BUILTINS: Final[frozenset[str]] = frozenset({"web_search", "fetch_url", "send_sms", "notify_team"})

#: Built-ins that change the world (V5-25): run with ``is_read=False``, so a non-blocking run
#: asks before running twice (``on_duplicate="confirm"``) and is not cancellable by default.
WRITE_BUILTINS: Final[frozenset[str]] = frozenset(
    # V6-06: the checklist tools and a generated picture change what the caller sees.
    # V6-08: so does writing in the notebook.
    {
        "send_sms",
        "notify_team",
        "set_checklist",
        "check_item",
        "generate_image",
        "notebook_write",
        "notebook_check",
    }
)

#: Built-ins registered only when the agent speaks more than one language (V5-31):
#: ``switch_language`` changes the reply language, the voice and the transcriber mid-call.
LANGUAGE_TOOL_NAMES: Final[frozenset[str]] = frozenset({"switch_language"})

#: How ``escalate_to_human`` asks a person to get involved (V5-37): ``transfer`` (hand the caller
#: over; the default, and the tool's only behaviour before V5-37), ``takeover`` (a person joins and
#: takes over the call), ``listen_in`` (a supervisor listens and may guide the agent in writing),
#: ``callback`` (someone calls the caller back later). The tool flags the session, records the
#: ``escalation`` event with the mode and writes the ``handoff`` block; it does not dial anyone.
EscalationMode = Literal["transfer", "takeover", "listen_in", "callback"]
ESCALATION_MODES: Final[tuple[str, ...]] = ("transfer", "takeover", "listen_in", "callback")

#: Built-ins registered only when the agent has camera or screen share on.
VISION_TOOL_NAMES: Final[frozenset[str]] = frozenset({"describe_current_frame", "pin_frame"})

#: Built-ins that read the session's stored files (V5-19). ``describe_asset`` is registered
#: only on a cascaded pipeline whose LLM is not known to be text-only, and only when the
#: session can hold a picture: an ``upload`` or ``form`` block, or camera / screen share
#: (a pinned frame is stored).
ASSET_TOOL_NAMES: Final[frozenset[str]] = frozenset({"describe_asset"})

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
    "request_consent",
    "record_consent",
    "request_upload",
    # V5-43: reading the panel (any block), a link, a slot picker and cards.
    "describe_panel",
    "send_link",
    "request_slot",
    "resolve_slot",
    "show_cards",
    # V6-06: the envelope checklist, for any agent with a checklist block.
    "set_checklist",
    "check_item",
    # V6-08: writing in a notebook block (notes, its checklist and details sections).
    "notebook_write",
    "notebook_check",
)

#: Block types whose state ``update_block`` may write (envelope blocks, forms and
#: choices have their own tools: a request's status belongs to the request). V6-08: a
#: ``notebook`` is written by ``notebook_write`` / ``notebook_check`` only (so neither
#: ``update_block`` nor a page's ``state_delta`` reaches it) and a ``layout`` holds no state.
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
        # V5-43: cards are display data (a tap arrives as a block action, never as state).
        "cards",
    }
)

#: Every panel block type (V5-43): ``describe_panel`` is registered whenever the panel has
#: any block at all.
ALL_BLOCK_TYPES: Final[frozenset[str]] = frozenset(get_args(BlockType))

#: Each block tool → the panel block types that make the worker register it.
#: ``set_steps`` is further limited to a ``steps`` block whose ``config.source`` is
#: not ``"flow"`` (the flow writes those itself, V5-08). ``record_consent`` (V5-15) is the
#: one exception the other way: it is also registered without any ``consent`` block when
#: ``recording.require_consent`` is on, so a caller can agree out loud on any channel.
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
    "request_consent": frozenset({"consent"}),
    "record_consent": frozenset({"consent"}),
    "request_upload": frozenset({"upload"}),
    "describe_panel": ALL_BLOCK_TYPES,
    "send_link": frozenset({"link"}),
    "request_slot": frozenset({"slots"}),
    "resolve_slot": frozenset({"slots"}),
    "show_cards": frozenset({"cards"}),
    "set_checklist": frozenset({"checklist"}),
    "check_item": frozenset({"checklist"}),
    "notebook_write": frozenset({"notebook"}),
    "notebook_check": frozenset({"notebook"}),
}


def builtin_tools_document() -> dict[str, Any]:
    """The built-in tool names as plain JSON (``generated/builtin_tools.json``).

    Returns:
        ``{builtin_tool_names, vision_tool_names, block_tool_names, block_tool_types,
        configured_builtins, write_builtins, builtin_default_modes}``; sets are sorted so the
        export is deterministic.
    """
    return {
        "builtin_tool_names": list(BUILTIN_TOOL_NAMES),
        "vision_tool_names": sorted(VISION_TOOL_NAMES),
        "block_tool_names": list(BLOCK_TOOL_NAMES),
        "block_tool_types": {name: sorted(types) for name, types in BLOCK_TOOL_TYPES.items()},
        "configured_builtins": sorted(CONFIGURED_BUILTINS),
        "write_builtins": sorted(WRITE_BUILTINS),
        "builtin_default_modes": dict(sorted(BUILTIN_DEFAULT_MODES.items())),
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


#: Built-ins whose execution an admin may set (``ToolsConfig.builtin_execution``). The
#: agent-level default reaches only the read ones among them (not :data:`WRITE_BUILTINS`),
#: and only when the tool has no default of its own (:data:`BUILTIN_DEFAULT_MODES`).
BACKGROUNDABLE_BUILTINS: Final[frozenset[str]] = frozenset(
    {
        "search_knowledge",
        "http_request",
        "describe_current_frame",
        # V5-25: two reads and two fire-and-forget writes.
        "web_search",
        "fetch_url",
        "send_sms",
        "notify_team",
    }
)
#: A built-in's own mode when ``builtin_execution`` sets none (V5-25). It wins over the
#: agent's ``execution_default``: a search answers inline when it is quick (``auto``); a page
#: fetch, a text message and a team notification never hold the conversation up.
BUILTIN_DEFAULT_MODES: Final[dict[str, ToolExecutionMode]] = {
    "web_search": "auto",
    "fetch_url": "background",
    "send_sms": "background",
    "notify_team": "background",
}
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
        # V5-15: a consent request waits for the caller; recording an answer may start
        # the recording or end the call.
        "request_consent",
        "record_consent",
        # V5-19: an upload request waits for the caller's files; describing a file is
        # what the model needs for its next sentence (and reads a caller's document).
        "request_upload",
        "describe_asset",
        # V5-43: reading the panel is instant; a slot request waits for the caller; the
        # others write the panel (a link may also send a text, which the caller waits for).
        "describe_panel",
        "send_link",
        "request_slot",
        "resolve_slot",
        "show_cards",
        # V6-06: writing the checklist is instant; a picture runs as its own job (like the
        # insurance pack's sketch), so the call itself answers at once.
        "set_checklist",
        "check_item",
        "generate_image",
        # V6-08: writing in the notebook is instant.
        "notebook_write",
        "notebook_check",
        "escalate_to_human",
        "update_block",
        "show_document",
        "table_append",
        "push_note",
        "set_status",
        "pin_frame",
        "current_time",
        "convert_time",
        # V5-25: local and instant.
        "calculate",
        "spell_back",
        # V5-31: instant, and the model's next sentence is in the new language.
        "switch_language",
        # V6-13: bounded to two seconds, and the model reads the captured values next.
        "extract_now",
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


def _refuse_placeholder_issues(definition: BaseModel) -> None:
    """V6-07 (D-V6-22): raise on the first misplaced ``{{ ctx.* }}``/``{{ var.* }}`` (save-time refusal)."""
    issues = placeholder_issues(definition)
    if issues:
        first = issues[0]
        raise ValueError(f"{first.field}: {first.message}")


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
    requires_vars: RequiresVars = []
    """V6-07: variables that must be set before the tool calls out (the refusal names the
    missing ones so the model asks for them)."""
    confirm_readback: ConfirmReadback = []
    """V6-07: arguments the model reads back to the caller first; adds a ``confirmed``
    parameter and the tool refuses until it is true."""
    bindings: Bindings = []
    """V6-07: parts of a 2xx result copied onto the panel or into variables, no model turn."""

    @model_validator(mode="after")
    def _silent_reply_blocks(self) -> Self:
        if self.silent_reply and self.execution.mode in NON_BLOCKING_MODES:
            raise ValueError(
                "silent_reply cannot be combined with a background or automatic execution mode: "
                "the silenced reply would swallow the tool's announcement"
            )
        return self

    @model_validator(mode="after")
    def _context_placeholders(self) -> Self:
        _refuse_placeholder_issues(self)
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


#: Caps on a stored ``tools/list`` snapshot (the ones ``POST /v1/tools/{id}/test`` applies, S5-42).
MAX_CACHED_TOOLS: Final = 200
MAX_CACHED_TOOL_DESCRIPTION: Final = 1000
MAX_CACHED_TOOL_SCHEMA_BYTES: Final = 16_000


class McpToolSnapshot(BaseModel):
    """One tool of a server's ``tools/list`` answer, as ``POST /v1/tools/{id}/test`` stored it."""

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=MAX_CACHED_TOOL_DESCRIPTION)
    input_schema: dict[str, Any] | None = None

    @field_validator("input_schema")
    @classmethod
    def _schema_size(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None and len(json.dumps(value, default=str)) > MAX_CACHED_TOOL_SCHEMA_BYTES:
            raise ValueError(f"input_schema is larger than {MAX_CACHED_TOOL_SCHEMA_BYTES} bytes")
        return value


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
    cached_tools: list[McpToolSnapshot] | None = Field(default=None, max_length=MAX_CACHED_TOOLS)
    """The server's tools as ``POST /v1/tools/{id}/test`` last listed them, for the console.
    The worker still lists tools itself at session start (research-v4 tools §4.3.7)."""
    cached_at: datetime | None = None
    """When ``cached_tools`` was stored."""
    tool_context: dict[str, ToolContextSpec] = {}
    """V6-07: per MCP tool name, its required variables, read-back, bindings and pinned
    arguments (a subset of ``allowed_tools`` when that is set)."""

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

    @model_validator(mode="after")
    def _tool_context_is_allowed(self) -> Self:
        if self.allowed_tools is not None:
            unknown = sorted(set(self.tool_context) - set(self.allowed_tools))
            if unknown:
                raise ValueError(f"tool_context names tools outside allowed_tools: {', '.join(unknown)}")
        _refuse_placeholder_issues(self)
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
    requires_vars: RequiresVars = []
    """V6-07: variables that must be set before the action runs."""
    confirm_readback: ConfirmReadback = []
    """V6-07: arguments the model reads back to the caller first (adds ``confirmed``)."""
    bindings: Bindings = []
    """V6-07: parts of a successful result copied onto the panel or into variables."""
    pinned_arguments: dict[str, PinnedValue] = Field(default={}, max_length=MAX_PINNED_ARGUMENTS)
    """V6-07: arguments fixed by the admin, hidden from the model; string values may use
    ``{{ ctx.* }}`` and ``{{ var.* }}``."""

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

    @model_validator(mode="after")
    def _context_placeholders(self) -> Self:
        _refuse_placeholder_issues(self)
        return self


class DatasetToolDefinition(BaseModel):
    """A lookup in one of the workspace's datasets (V6-16, D-V6-27; ``lkap_contracts.datasets``).

    The model sees one string argument per key column (``key_columns``, minus the pinned
    ones) and gets back at most ``max_rows`` rows with ``return_columns`` (all columns when
    empty), matched ``exact`` or by ``prefix`` on the normalised value. The worker calls the
    api's internal lookup route with the session, so a lookup never reads another
    workspace's dataset, and fences the rows as ``dataset:<name>``. Read-only: nothing is
    ever written to a dataset from a call. The tool always runs blocking (a lookup is quick).

    V6-07: ``pinned_arguments`` fix a key column's value, hidden from the model (a string may
    be ``{{ ctx.caller_phone }}`` or ``{{ var.policy_number }}``); ``requires_vars`` refuse
    until variables are set; ``bindings`` copy a successful lookup onto the panel. The bound
    (and model-visible) result is the list of rows, so ``/0/holder_name`` is the first row's
    ``holder_name``.
    """

    kind: Literal["dataset"] = "dataset"
    name: str = Field(pattern=TOOL_NAME_PATTERN)
    description: str
    dataset_id: str = Field(min_length=1, max_length=64)
    key_columns: list[str] = Field(min_length=1, max_length=MAX_DATASET_KEY_COLUMNS)
    """The dataset's key columns this tool matches on (each one a declared key column)."""
    return_columns: list[str] = Field(default=[], max_length=MAX_DATASET_COLUMNS)
    """The columns a found row carries (empty: all of them)."""
    match: DatasetMatch = "exact"
    max_rows: int = Field(default=5, ge=1, le=MAX_DATASET_LOOKUP_ROWS)
    max_result_chars: int = Field(default=2000, ge=100, le=8000)
    requires_vars: RequiresVars = []
    """V6-07: variables that must be set before the lookup runs."""
    bindings: Bindings = []
    """V6-07: parts of the found rows copied onto the panel or into variables, no model turn."""
    pinned_arguments: dict[str, PinnedValue] = Field(default={}, max_length=MAX_PINNED_ARGUMENTS)
    """V6-07: key column → a fixed value the model never supplies (strings may use
    ``{{ ctx.* }}`` and ``{{ var.* }}``)."""

    @model_validator(mode="after")
    def _columns(self) -> Self:
        for column in (*self.key_columns, *self.return_columns):
            if re.match(DATASET_COLUMN_NAME_PATTERN, column) is None:
                raise ValueError(f"'{column}' is not a dataset column name (lower case letters, digits, _)")
        if len(set(self.key_columns)) != len(self.key_columns):
            raise ValueError("key_columns lists a column twice")
        outside = sorted(set(self.pinned_arguments) - set(self.key_columns))
        if outside:
            raise ValueError(f"pinned_arguments names columns outside key_columns: {', '.join(outside)}")
        return self

    @model_validator(mode="after")
    def _context_placeholders(self) -> Self:
        _refuse_placeholder_issues(self)
        return self


ToolDefinition = Annotated[
    HttpToolDefinition | McpServerDefinition | ProviderToolDefinition | DatasetToolDefinition,
    Field(discriminator="kind"),
]


# --------------------------------------------------------------- tool templates (V5-25)
#: Tool template ids: ``<group>.<name>`` (``cal_com.booking_create``).
TOOL_TEMPLATE_ID_PATTERN = r"^[a-z][a-z0-9_]{0,40}\.[a-z][a-z0-9_]{0,63}$"


class ToolTemplateDefault(BaseModel):
    """An argument of a template the admin may fix when adding the tool (``event_type_id``)."""

    name: str = Field(pattern=TOOL_NAME_PATTERN, description="The argument name in the tool's parameters")
    label: str
    help: str | None = None
    required: bool = False
    """The template cannot be added without a value for it."""


class ToolTemplate(BaseModel):
    """A ready-made HTTP tool (D-V5-36): the definition plus what an admin supplies to add it.

    ``definition`` references its key as ``{{ secret.NAME }}`` (``secret_names``) and is stored
    as a normal HTTP tool when instantiated; ``defaults`` become the JSON Schema ``default`` of
    those arguments (an argument the model leaves out takes it).
    """

    id: str = Field(pattern=TOOL_TEMPLATE_ID_PATTERN)
    group: str = Field(description="The set the template belongs to (`cal_com`)")
    group_label: str
    label: str
    summary: str = Field(description="One plain-language line for the console")
    secret_names: list[str] = Field(description="The `http-tool-secret` names the key must hold")
    defaults: list[ToolTemplateDefault] = []
    risk: Literal["read", "write"] = "read"
    docs_url: str | None = None
    definition: HttpToolDefinition


class ToolTemplatesResponse(BaseModel):
    """``GET /v1/tool-templates``: every template, grouped by ``group`` in catalogue order."""

    items: list[ToolTemplate]


class ToolTemplateInstantiate(BaseModel):
    """``POST /v1/tool-templates/{id}/instantiate``: add one template, or every template of a group.

    ``credential_id`` is an ``http-tool-secret`` key holding the template's ``secret_names``;
    ``defaults`` fixes template arguments (``{"event_type_id": 123456}``).
    """

    credential_id: str
    defaults: dict[str, str | int | float | bool] = {}
    agent_id: str | None = None
    enabled: bool = True
    names: list[str] | None = Field(
        default=None, description="Group instantiation only: the template names to add (default: all)"
    )


class ToolTemplateInstantiated(BaseModel):
    """What ``POST /v1/tool-templates/{id}/instantiate`` created (tool rows, in template order)."""

    tool_ids: list[str]
    names: list[str]
    template_ids: list[str]


#: The tool-template models, registered in ``export.py`` with one line (``**TOOL_TEMPLATE_MODELS``).
TOOL_TEMPLATE_MODELS: dict[str, type[BaseModel]] = {
    "ToolTemplateDefault": ToolTemplateDefault,
    "ToolTemplate": ToolTemplate,
    "ToolTemplatesResponse": ToolTemplatesResponse,
    "ToolTemplateInstantiate": ToolTemplateInstantiate,
    "ToolTemplateInstantiated": ToolTemplateInstantiated,
}
