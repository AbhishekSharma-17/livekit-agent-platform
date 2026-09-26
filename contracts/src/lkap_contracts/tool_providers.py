"""Tool providers: connected apps through Composio (docs/v5/COMPOSIO.md §3, V5-18).

A *tool provider* gives agents third-party actions (a calendar, a CRM, a
ticketing system) through one vendor key. Composio is the only provider in
v5 (D-V5-1); the console calls the feature **Apps**.

Two kinds of ``credentials`` row back it, so no new table exists (D-V5-C3):

* the workspace's Composio API key, a normal provider-key row whose
  ``provider_id`` is ``composio`` (the registry entry of kind
  ``tool_provider``);
* one row per connected app, ``provider_id == TOOL_PROVIDER_ACCOUNT``, whose
  encrypted bag holds only references (toolkit, auth config id, connected
  account id, subject, status, and — R-V5-13 — the account's label and
  whether it is the app's default). Composio holds every third-party
  token; LKAP never stores one. One app may have several accounts.

The model names carry an ``App`` prefix where §3's short names would clash
with existing exports (``ConnectionOut`` is the LiveKit connection).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime
from typing import Any, Final, Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

#: Tool providers LKAP can talk to (``arcade`` joins by ruling, D-V5-1).
ToolProviderId = Literal["composio"]

#: The registry id of the Composio key (its credential home).
COMPOSIO_PROVIDER_ID: Final = "composio"

#: ``credentials.provider_id`` of a connected-app row. Deliberately *not* a
#: registry entry, so ``POST /v1/credentials`` can never forge one.
TOOL_PROVIDER_ACCOUNT: Final = "tool-provider-account"

#: The one host every Composio call and every Composio MCP url uses (D-V5-C10).
COMPOSIO_HOST: Final = "backend.composio.dev"

#: A connected app's state, lower-case mirror of Composio's five statuses plus ``unknown``.
ConnectionStatus = Literal["active", "initiated", "expired", "failed", "inactive", "unknown"]

#: How a connection authenticates (D-V5-C4). ``none`` is a keyless toolkit: a local
#: row only, so its actions can be picked like any other app's.
ConnectMethod = Literal["managed", "custom_oauth", "api_key", "none"]

#: What a toolkit accepts, in console words.
AuthOption = Literal["oauth_managed", "oauth_custom", "api_key", "bearer", "basic", "none"]

#: How risky an action is (D-V5-C7): reads run freely, writes and destructive actions block.
ActionRisk = Literal["read", "write", "destructive"]

#: Who a connection belongs to (D-V5-C2): the workspace, or one agent.
SubjectKind = Literal["workspace", "agent"]


#: The longest account label (R-V5-13): "Work", "Personal", an address.
MAX_ACCOUNT_LABEL: Final = 40

#: Most accounts of one app an agent's session may use (R-V5-13; Composio allows 2 to 10).
MAX_ACCOUNTS_PER_APP: Final = 5

#: Most apps ``AppsMode.accounts`` may name.
MAX_ACCOUNT_APPS: Final = 20

_LABEL_SLUG_RE = re.compile(r"[^a-z0-9]+")


def label_slug(label: str, *, fallback: str = "account") -> str:
    """The lower snake slug of an account label (R-V5-13): ``"Work Inbox"`` → ``work_inbox``.

    Used for the Composio ``alias`` of the account and for the ``__<slug>`` suffix of
    the tool names of an app's non-default accounts. Characters outside ``a-z0-9`` fold
    to ``_``; an empty result is ``fallback``.

    Args:
        label: The account's label.
        fallback: What an all-symbol label becomes.

    Returns:
        A slug of ``[a-z0-9_]``, at most :data:`MAX_ACCOUNT_LABEL` characters.
    """
    slug = _LABEL_SLUG_RE.sub("_", label.strip().lower()).strip("_")[:MAX_ACCOUNT_LABEL].strip("_")
    return slug or fallback


def workspace_subject(workspace_id: str) -> str:
    """The Composio ``user_id`` of a workspace-wide connection (D-V5-C2)."""
    return f"ws:{workspace_id}"


def agent_subject(agent_id: str) -> str:
    """The Composio ``user_id`` of a connection made for one agent (D-V5-C2)."""
    return f"agent:{agent_id}"


class AppAuthField(BaseModel):
    """One field the Connect dialog asks for (never a stored value)."""

    name: str
    label: str
    secret: bool = True
    required: bool = True


class ToolkitOut(BaseModel):
    """One Composio toolkit (an app), trimmed for the console gallery."""

    slug: str
    name: str
    logo: str | None = Field(None, description="The vendor's logo URL; shown by the console, never proxied")
    categories: list[str] = []
    description: str = ""
    auth: list[AuthOption] = Field(
        default_factory=list, description="The ways this app can be connected, best first"
    )
    tools_count: int = 0
    connected: bool = Field(False, description="Whether this workspace has an active connection to it")
    connection_id: str | None = Field(None, description="That connection's id, when connected")
    auth_fields: dict[str, list[AppAuthField]] = Field(
        default_factory=dict,
        description="Per auth option, the fields the Connect dialog asks for (one-app read only)",
    )
    auth_guide_url: str | None = Field(None, description="The vendor's setup guide for your own OAuth app")
    oauth_redirect_uri: str | None = Field(
        None, description="The redirect URI to register in your own OAuth app (custom OAuth only)"
    )


class ToolkitPage(BaseModel):
    """A page of toolkits; pass ``next_cursor`` back as ``cursor`` for the next one."""

    items: list[ToolkitOut]
    next_cursor: str | None = None
    total: int | None = Field(None, description="Composio's total for this query, when it reports one")


class AppActionOut(BaseModel):
    """One Composio tool (an app's action), trimmed."""

    slug: str
    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict, description="The action's JSON Schema input")
    important: bool = Field(False, description="Composio marks it as a featured action")
    tags: list[str] = []
    risk: ActionRisk = "write"
    version: str | None = None


class AppActionPage(BaseModel):
    """A page of an app's actions."""

    items: list[AppActionOut]
    next_cursor: str | None = None
    total: int | None = None


class AppConnectIn(BaseModel):
    """``POST /v1/tool-providers/composio/connections``: connect an app.

    ``fields`` carries what the chosen method needs (the OAuth app's
    ``client_id``/``client_secret``, or the app's API key fields). It is
    forwarded to Composio once and never stored, logged or returned.
    """

    toolkit: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    subject: SubjectKind = "workspace"
    agent_id: str | None = Field(None, description="Required when subject is 'agent'")
    method: ConnectMethod = "managed"
    fields: dict[str, str] = Field(default_factory=dict, description="Write-only; never stored or returned")
    label: str | None = Field(
        None,
        min_length=1,
        max_length=MAX_ACCOUNT_LABEL,
        description="A name for this account (R-V5-13), e.g. 'Work'. Default: the name the app reports "
        "once signed in (an address or user name), else '<App> account <n>'",
    )


class AppConnectOut(BaseModel):
    """The started connection: open ``redirect_url`` in a browser tab (OAuth), or it is already active."""

    connection_id: str
    status: ConnectionStatus
    redirect_url: str | None = Field(None, description="The app's consent page; for a human to open")
    expires_at: datetime | None = Field(None, description="When an unfinished sign-in expires")


class AppConnectionOut(BaseModel):
    """A connected app of this workspace (no vendor tokens: Composio holds them)."""

    id: str
    provider: ToolProviderId = "composio"
    toolkit: str
    toolkit_name: str | None = None
    subject: str = Field(description="'ws:<workspace_id>' or 'agent:<agent_id>'")
    status: ConnectionStatus
    method: ConnectMethod
    connected_at: datetime | None = None
    last_checked_at: datetime | None = None
    needs_reconnect: bool = False
    picked_actions: list[str] = Field(default_factory=list, description="Actions chosen for agents")
    agents_using: int = 0
    label: str = Field(
        "",
        max_length=MAX_ACCOUNT_LABEL,
        description="The account's name (R-V5-13); the app's name for a connection made before labels",
    )
    is_default: bool = Field(
        True,
        description="The app's default account for this workspace (or agent): its tools keep the plain "
        "names and a session with no account choice uses it. Exactly one per app",
    )


class ConnectionRenameIn(BaseModel):
    """``PATCH /v1/tool-providers/composio/connections/{id}``: rename an account or make it the default.

    ``is_default=true`` moves the app's Default to this account (the previous default loses
    it in the same save); ``false`` is refused — make another account the default instead.
    Renaming renames the account at Composio too (its ``alias``); tool names already made
    keep theirs.
    """

    label: str | None = Field(None, min_length=1, max_length=MAX_ACCOUNT_LABEL)
    is_default: bool | None = None

    @model_validator(mode="after")
    def _one_change(self) -> Self:
        if self.label is None and self.is_default is None:
            raise ValueError("give a new label, is_default=true, or both")
        if self.is_default is False:
            raise ValueError("is_default=false is not a change: make another account the default instead")
        if self.label is not None and not self.label.strip():
            raise ValueError("label must not be blank")
        return self


class AppConnectionPage(BaseModel):
    """Every connected app of the workspace."""

    items: list[AppConnectionOut]
    total: int


class AppReconnectIn(BaseModel):
    """``POST …/connections/{id}/reconnect``: ``fields`` only for an API-key app (a new key)."""

    fields: dict[str, str] = Field(default_factory=dict, description="Write-only; never stored or returned")


class AppKeyTestIn(BaseModel):
    """``POST /v1/tool-providers/composio/key/test``: a pasted key, used once and never stored."""

    api_key: str = Field(min_length=1, max_length=512)


class AppKeyTestOut(BaseModel):
    """What a key test found; ``message`` is safe to show as is."""

    ok: bool
    account_name: str | None = None
    project_name: str | None = None
    toolkits_count: int | None = None
    message: str


class AppsStatusOut(BaseModel):
    """``GET /v1/tool-providers/composio/status``: the Apps section header."""

    enabled: bool = Field(description="A key exists and the provider is on for this workspace")
    credential_id: str | None = None
    fingerprint: str | None = None
    last_test_ok: bool | None = None
    last_test_at: datetime | None = None
    last_test_message: str | None = None
    connections: int = 0
    paused_tools: int = Field(0, description="Tools switched off while the provider is disabled")


class AppActionsPickIn(BaseModel):
    """``POST /v1/tool-providers/composio/materialise``: pick actions of a connected app.

    The picks are stored on the connection and each becomes a ``provider`` tool
    (one per action, reused when it exists), attached to ``agent_id`` when given.
    """

    connection_id: str
    actions: list[str] = Field(min_length=1, max_length=100)
    agent_id: str | None = None
    allow_destructive: bool = Field(
        False, description="Must be true to pick an action that deletes, removes or moves money"
    )


class AppActionsPickOut(BaseModel):
    """The connection's picked actions after the change."""

    connection_id: str
    picked_actions: list[str]
    agent_id: str | None = None
    tools_created: list[str] = Field(
        default_factory=list, description="Ids of the agent tools created for the picked actions"
    )
    tools_existing: list[str] = Field(
        default_factory=list, description="Ids of tools that already existed for picked actions"
    )


# ------------------------------------------------------------- agents (V5-47)
#: How an agent uses connected apps (D-V5-C6). ``actions``: the picked actions attached as
#: ``provider`` tools (recommended); ``server``: one app server exposing the allowed apps'
#: picked actions; ``router``: a tool finder whose search/execute tools find actions at run
#: time; ``off``: none of the provisioned servers (attached ``provider`` tools still run).
AppsModeName = Literal["actions", "server", "router", "off"]

#: The Tool Router meta tools (D-V5-C7; the live list is recorded in ``docs/v5/_briefs/v5-47-live.md``).
ROUTER_SEARCH_TOOLS: Final[tuple[str, ...]] = ("COMPOSIO_SEARCH_TOOLS", "COMPOSIO_GET_TOOL_SCHEMAS")
ROUTER_EXECUTE_TOOLS: Final[tuple[str, ...]] = ("COMPOSIO_MULTI_EXECUTE_TOOL",)
ROUTER_CONNECTION_TOOLS: Final[tuple[str, ...]] = (
    "COMPOSIO_MANAGE_CONNECTIONS",
    "COMPOSIO_WAIT_FOR_CONNECTIONS",
)
#: Meta tools never attached, whatever the flags: remote code, skills and feedback.
ROUTER_EXCLUDED_TOOLS: Final[tuple[str, ...]] = (
    "COMPOSIO_REMOTE_BASH_TOOL",
    "COMPOSIO_REMOTE_WORKBENCH",
    "COMPOSIO_SEARCH_SKILLS",
    "COMPOSIO_USE_SKILL",
    "COMPOSIO_MANAGE_SKILL",
    "COMPOSIO_SUBMIT_FEEDBACK",
)


class AppsRouterOptions(BaseModel):
    """What the tool finder may do (D-V5-C7)."""

    search: bool = Field(True, description="Let the agent look actions up during the conversation")
    execute: bool = Field(True, description="Let the agent run the actions it found")
    manage_connections: bool = Field(
        False,
        description="Let the agent offer a sign-in link for an app that is not connected yet "
        "(text and web chats only: a phone caller cannot open a link)",
    )


class AppsMode(BaseModel):
    """``AgentConfig.tools.apps``: how the agent uses connected apps (docs/v5/COMPOSIO.md D-V5-C6).

    The default ``off`` changes nothing for an agent saved before this field existed.
    """

    mode: AppsModeName = "off"
    allowed_toolkits: list[str] = Field(
        default_factory=list,
        max_length=50,
        description="Apps the app server or tool finder may use (toolkit slugs); empty = every "
        "connected app of the agent",
    )
    denied_actions: list[str] = Field(
        default_factory=list,
        max_length=500,
        description="Actions the app server or tool finder must never run (action slugs)",
    )
    reviewed_actions: list[str] = Field(
        default_factory=list,
        max_length=500,
        description="Destructive actions the builder has decided about (action slugs, R-V5-9); "
        "denied_actions says which way. An unreviewed destructive action is blocked",
    )
    router: AppsRouterOptions = Field(default_factory=AppsRouterOptions)
    accounts: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Per app (toolkit slug), the accounts (connection ids) the app server or tool "
        "finder may use (R-V5-13); an app not named, or named with an empty list, uses its default "
        f"account. At most {MAX_ACCOUNT_APPS} apps and {MAX_ACCOUNTS_PER_APP} accounts per app",
    )

    @field_validator("accounts")
    @classmethod
    def _accounts_shape(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        if len(value) > MAX_ACCOUNT_APPS:
            raise ValueError(f"at most {MAX_ACCOUNT_APPS} apps may name accounts")
        out: dict[str, list[str]] = {}
        for raw_toolkit, ids in value.items():
            toolkit = raw_toolkit.strip().lower()
            if not toolkit:
                raise ValueError("an app slug in accounts is empty")
            merged = list(dict.fromkeys([*out.get(toolkit, []), *(i.strip() for i in ids if i.strip())]))
            if len(merged) > MAX_ACCOUNTS_PER_APP:
                raise ValueError(f"'{toolkit}': at most {MAX_ACCOUNTS_PER_APP} accounts per app")
            out[toolkit] = merged
        return out


def effective_denied_actions(apps: AppsMode, destructive_in_scope: Iterable[str]) -> list[str]:
    """The actions an app server or tool finder must not run (R-V5-9), sorted and upper-cased.

    ``denied_actions`` plus every destructive action in scope the builder has not reviewed:
    ``denied_actions | (destructive_in_scope - reviewed_actions)``. So an unreviewed
    destructive action is blocked whatever the console did, a reviewed one that is not
    denied stays allowed, and a reviewed one that is denied stays denied. With
    ``reviewed_actions=[]`` every destructive action in scope is denied, which is the deny
    list the V5-48 console seeded client-side.

    Args:
        apps: The agent's ``tools.apps``.
        destructive_in_scope: The destructive action slugs the agent's allowed apps expose
            (``action_risk(slug) == "destructive"``).

    Returns:
        The effective deny list.
    """
    denied = {slug.strip().upper() for slug in apps.denied_actions if slug.strip()}
    reviewed = {slug.strip().upper() for slug in apps.reviewed_actions if slug.strip()}
    scope = {slug.strip().upper() for slug in destructive_in_scope if slug.strip()}
    return sorted(denied | (scope - reviewed))


def router_allowed_tools(options: AppsRouterOptions) -> list[str]:
    """The meta tools a tool finder attaches for ``options`` (D-V5-C7), in a stable order."""
    names: list[str] = []
    if options.search:
        names.extend(ROUTER_SEARCH_TOOLS)
    if options.execute:
        names.extend(ROUTER_EXECUTE_TOOLS)
    if options.manage_connections:
        names.extend(ROUTER_CONNECTION_TOOLS)
    return names


#: Registered in ``export.py`` with one line (``**TOOL_PROVIDER_MODELS``).
TOOL_PROVIDER_MODELS: dict[str, type[BaseModel]] = {
    "AppAuthField": AppAuthField,
    "ToolkitOut": ToolkitOut,
    "ToolkitPage": ToolkitPage,
    "AppActionOut": AppActionOut,
    "AppActionPage": AppActionPage,
    "AppConnectIn": AppConnectIn,
    "AppConnectOut": AppConnectOut,
    "AppConnectionOut": AppConnectionOut,
    "AppConnectionPage": AppConnectionPage,
    "ConnectionRenameIn": ConnectionRenameIn,
    "AppReconnectIn": AppReconnectIn,
    "AppKeyTestIn": AppKeyTestIn,
    "AppKeyTestOut": AppKeyTestOut,
    "AppsStatusOut": AppsStatusOut,
    "AppActionsPickIn": AppActionsPickIn,
    "AppActionsPickOut": AppActionsPickOut,
    "AppsMode": AppsMode,
    "AppsRouterOptions": AppsRouterOptions,
}

#: Action slugs whose name marks them destructive (D-V5-7): never picked without
#: an explicit confirmation, always blocking.
DESTRUCTIVE_MARKERS: Final[tuple[str, ...]] = ("DELETE", "REMOVE", "DESTROY", "PURGE", "SEND_MONEY", "REFUND")

#: Slug words and tags that mark an action as a read (runs without blocking the call).
READ_MARKERS: Final[tuple[str, ...]] = ("GET", "LIST", "FIND", "SEARCH", "FETCH", "READ", "RETRIEVE", "QUERY")


def action_risk(slug: str, tags: list[str] | None = None) -> ActionRisk:
    """Classify an action by its slug and tags (D-V5-C7).

    Destructive markers win over read markers, so ``LIST_AND_DELETE_X`` is
    destructive. A tag of ``readOnlyHint`` (or ``read``) marks a read.

    Args:
        slug: The Composio tool slug, e.g. ``GOOGLECALENDAR_FIND_FREE_SLOTS``.
        tags: The tool's tags.

    Returns:
        ``destructive``, ``read`` or ``write``.
    """
    words = set(slug.upper().replace("-", "_").split("_"))
    upper = slug.upper()
    if any(marker in upper if "_" in marker else marker in words for marker in DESTRUCTIVE_MARKERS):
        return "destructive"
    lowered = {tag.lower() for tag in tags or []}
    if lowered & {"readonlyhint", "read", "readonly", "read-only"}:
        return "read"
    if words & set(READ_MARKERS):
        return "read"
    return "write"


__all__ = [
    "COMPOSIO_HOST",
    "COMPOSIO_PROVIDER_ID",
    "DESTRUCTIVE_MARKERS",
    "MAX_ACCOUNTS_PER_APP",
    "MAX_ACCOUNT_APPS",
    "MAX_ACCOUNT_LABEL",
    "READ_MARKERS",
    "ROUTER_CONNECTION_TOOLS",
    "ROUTER_EXCLUDED_TOOLS",
    "ROUTER_EXECUTE_TOOLS",
    "ROUTER_SEARCH_TOOLS",
    "TOOL_PROVIDER_ACCOUNT",
    "TOOL_PROVIDER_MODELS",
    "ActionRisk",
    "AppActionOut",
    "AppActionPage",
    "AppActionsPickIn",
    "AppActionsPickOut",
    "AppAuthField",
    "AppConnectIn",
    "AppConnectOut",
    "AppConnectionOut",
    "AppConnectionPage",
    "AppKeyTestIn",
    "AppKeyTestOut",
    "AppReconnectIn",
    "AppsMode",
    "AppsModeName",
    "AppsRouterOptions",
    "AppsStatusOut",
    "AuthOption",
    "ConnectMethod",
    "ConnectionRenameIn",
    "ConnectionStatus",
    "SubjectKind",
    "ToolProviderId",
    "ToolkitOut",
    "ToolkitPage",
    "action_risk",
    "agent_subject",
    "effective_denied_actions",
    "label_slug",
    "router_allowed_tools",
    "workspace_subject",
]
