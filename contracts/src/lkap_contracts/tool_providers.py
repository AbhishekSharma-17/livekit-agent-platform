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
  account id, subject, status). Composio holds every third-party token;
  LKAP never stores one.

The model names carry an ``App`` prefix where §3's short names would clash
with existing exports (``ConnectionOut`` is the LiveKit connection).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final, Literal

from pydantic import BaseModel, Field

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

    Until the ``provider`` tool kind exists (V5-47) the picks are stored on the
    connection and returned; V5-47 turns them into tools (attached to
    ``agent_id`` when given).
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
        default_factory=list, description="Tool ids created (empty until V5-47 lands)"
    )


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
    "AppReconnectIn": AppReconnectIn,
    "AppKeyTestIn": AppKeyTestIn,
    "AppKeyTestOut": AppKeyTestOut,
    "AppsStatusOut": AppsStatusOut,
    "AppActionsPickIn": AppActionsPickIn,
    "AppActionsPickOut": AppActionsPickOut,
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
    "READ_MARKERS",
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
    "AppsStatusOut",
    "AuthOption",
    "ConnectMethod",
    "ConnectionStatus",
    "SubjectKind",
    "ToolProviderId",
    "ToolkitOut",
    "ToolkitPage",
    "action_risk",
    "agent_subject",
    "workspace_subject",
]
