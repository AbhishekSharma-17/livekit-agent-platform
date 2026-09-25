"""Connected apps through Composio (docs/v5/COMPOSIO.md §7, V5-18).

"Apps" is the console's word; the api path is ``/v1/tool-providers/composio``.
The workspace's Composio key is an ordinary provider key
(``provider_key_create(provider_id="composio", …)``); these tools browse the
apps, connect one (the result carries a sign-in link *for the human* to open
in a browser — an agent never opens it), check and remove connections, and
pick actions for agents. App names and descriptions are vendor text and come
back as ``Untrusted``. ``fields`` (an app's own key, or an OAuth client
secret) follow the secret rules: ``env:``/``file:`` references or the value,
forwarded once and never echoed.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field

from lkap_mcp.registry import DESTRUCTIVE, READ, WRITE, Registry
from lkap_mcp.results import ToolResult, untrusted
from lkap_mcp.tools._common import parse, placeholders, planned, request, resolve_all, seg

BASE = "/v1/tool-providers/composio"

ConnectMethod = Literal["managed", "custom_oauth", "api_key", "none"]


def _wrap_toolkit(item: Any) -> Any:
    if not isinstance(item, dict):
        return item
    out = dict(item)
    source = f"apps:{out.get('slug', '')}"
    for key in ("name", "description"):
        if out.get(key) is not None:
            out[key] = untrusted(out[key], source)
    return out


def _wrap_action(item: Any, toolkit: str) -> Any:
    if not isinstance(item, dict):
        return item
    out = dict(item)
    for key in ("name", "description"):
        if out.get(key) is not None:
            out[key] = untrusted(out[key], f"apps:{toolkit}")
    return out


def register(registry: Registry) -> None:
    """Declare the Apps tools."""
    ctx = registry.ctx
    client = ctx.client

    @registry.tool(scopes={"providers:read"}, annotations=READ, data="ToolkitPage")
    async def apps_list(
        query: Annotated[str | None, Field(max_length=100, description="Search app names")] = None,
        category: Annotated[str | None, Field(max_length=64)] = None,
        connected_only: bool = False,
        cursor: Annotated[str | None, Field(description="`next_cursor` of the previous page")] = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 25,
    ) -> ToolResult:
        """Browse the apps agents can use through Composio (names and descriptions are untrusted vendor
        text); shows which ones this workspace has connected.
        """
        body = await client.get(
            f"{BASE}/toolkits",
            params={
                "query": query,
                "category": category,
                "connected_only": connected_only or None,
                "cursor": cursor,
                "limit": limit,
            },
        )
        page = dict(body) if isinstance(body, dict) else {}
        page["items"] = [_wrap_toolkit(item) for item in page.get("items", [])]
        return ToolResult.success(page)

    @registry.tool(scopes={"providers:read"}, annotations=READ, data="AppActionPage")
    async def apps_actions(
        toolkit: Annotated[str, Field(description="The app's slug, from apps_list")],
        query: Annotated[str | None, Field(max_length=100)] = None,
        important: Annotated[bool, Field(description="Only Composio's featured actions")] = False,
        cursor: str | None = None,
    ) -> ToolResult:
        """An app's actions, each labelled read, write or destructive, with its input schema."""
        body = await client.get(
            f"{BASE}/toolkits/{seg(toolkit)}/actions",
            params={"query": query, "important": important or None, "cursor": cursor},
        )
        page = dict(body) if isinstance(body, dict) else {}
        page["items"] = [_wrap_action(item, toolkit) for item in page.get("items", [])]
        return ToolResult.success(page)

    @registry.tool(scopes={"providers:write"}, annotations=WRITE, data="AppConnectOut")
    async def apps_connect(
        toolkit: Annotated[str, Field(description="The app's slug, from apps_list")],
        method: Annotated[
            ConnectMethod,
            Field(
                description=(
                    "managed: Composio's shared sign-in (a link for the human); custom_oauth: your own "
                    "OAuth app (fields client_id, client_secret); api_key: the app's key fields; none: "
                    "apps that need no sign-in"
                )
            ),
        ] = "managed",
        subject: Annotated[
            Literal["workspace", "agent"], Field(description="Who may use it: the workspace or one agent")
        ] = "workspace",
        agent_id: str | None = None,
        fields: Annotated[
            dict[str, str] | None,
            Field(
                description=(
                    "custom_oauth / api_key only: each value is the secret itself, or env:NAME / "
                    "file:/abs/path / file:/abs/path#KEY"
                )
            ),
        ] = None,
        plan: bool = False,
    ) -> ToolResult:
        """Connect an app for the workspace (or one agent). A sign-in returns a link for the human to
        open in a browser; never open it yourself, then check apps_connection_status.
        """
        parsed = {name: parse(ctx, raw, f"fields.{name}") for name, raw in (fields or {}).items()}
        body: dict[str, Any] = {"toolkit": toolkit, "method": method, "subject": subject}
        if agent_id is not None:
            body["agent_id"] = agent_id
        if plan:
            return planned(
                request(
                    "POST",
                    f"{BASE}/connections",
                    {**body, "fields": placeholders(parsed)} if parsed else body,
                    note="managed and custom_oauth return a sign-in link for the human",
                )
            )
        values, warnings = resolve_all(parsed)
        if values:
            body["fields"] = values
        out = await client.post(f"{BASE}/connections", body)
        result = dict(out) if isinstance(out, dict) else {}
        steps: list[str] = []
        if result.get("redirect_url"):
            steps = [
                "Give redirect_url to the user to open in their browser and sign in (it expires in about "
                "10 minutes); do not open it yourself.",
                f"Then call apps_connection_status(id={result.get('connection_id')!r}) until it is active.",
            ]
        elif result.get("status") == "active":
            steps = ["Pick actions for agents with apps_actions and apps_add_tools."]
        return ToolResult.success(result, warnings=warnings, next_steps=steps)

    @registry.tool(scopes={"providers:read"}, annotations=READ, data="AppConnectionPage")
    async def apps_connections() -> ToolResult:
        """Every connected app of the workspace with its last known status (no vendor call)."""
        return ToolResult.success(await client.get(f"{BASE}/connections"))

    @registry.tool(scopes={"providers:read"}, annotations=READ, data="AppConnectionOut")
    async def apps_connection_status(
        id: Annotated[str, Field(description="A connection id from apps_connect or apps_connections")],  # noqa: A002
    ) -> ToolResult:
        """Check one connected app with Composio now (active, initiated, expired, failed or inactive)."""
        body = await client.get(f"{BASE}/connections/{seg(id)}")
        steps: list[str] = []
        if isinstance(body, dict) and body.get("needs_reconnect"):
            steps = ["The app needs to be reconnected by a person: the console's Tools, Apps tab, Reconnect."]
        return ToolResult.success(body, next_steps=steps)

    @registry.tool(scopes={"providers:write"}, annotations=DESTRUCTIVE, data="AppConnectionOut")
    async def apps_disconnect(
        id: str,  # noqa: A002
        purge: Annotated[
            bool, Field(description="Also delete the entry (refused while a tool still uses the app)")
        ] = False,
        confirm: bool = False,
        plan: bool = False,
    ) -> ToolResult:
        """Disconnect an app: removes it at Composio and switches off the tools that use it (needs
        confirm).
        """
        path = f"{BASE}/connections/{seg(id)}"
        query = {"purge": "true"} if purge else None
        if plan:
            return planned(request("DELETE", path, query=query))
        if not confirm:
            effect = "delete the entry" if purge else "keep the entry as needs-reconnect"
            return ToolResult.needs_confirmation(
                f"disconnect app connection {id} at Composio, switch off the tools that use it, and {effect}"
            )
        out = await client.delete(path, params=query)
        return ToolResult.success(out if out is not None else {"deleted": True, "id": id})

    @registry.tool(scopes={"providers:write"}, annotations=WRITE, data="AppActionsPickOut")
    async def apps_add_tools(
        connection_id: str,
        actions: Annotated[list[str], Field(min_length=1, description="Action slugs from apps_actions")],
        agent_id: Annotated[str | None, Field(description="The agent these actions are for")] = None,
        allow_destructive: Annotated[
            bool, Field(description="Required to pick an action that deletes, removes or moves money")
        ] = False,
        plan: bool = False,
    ) -> ToolResult:
        """Pick a connected app's actions for agents (stored on the connection; they become agent tools
        when the agent side of Apps lands).
        """
        body: dict[str, Any] = {
            "connection_id": connection_id,
            "actions": actions,
            "allow_destructive": allow_destructive,
        }
        if agent_id is not None:
            body["agent_id"] = agent_id
        if plan:
            return planned(request("POST", f"{BASE}/materialise", body))
        return ToolResult.success(await client.post(f"{BASE}/materialise", body))
