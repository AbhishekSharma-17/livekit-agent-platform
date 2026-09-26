"""``oauth/start`` and ``oauth/status`` for one MCP tool (V5-14, research-v4 tools §4.3.3).

Starting a sign-in is credential management: it needs ``admin`` and, for API keys,
``providers:write`` (the ``/v1/credentials`` write rule), on top of the tools route
policy. Reading the status needs ``admin`` (``providers:read``). Neither returns a
token, a code, a ``state`` other than inside the authorization url, or a secret.
"""

from __future__ import annotations

import asyncio
from typing import Final

import httpx
from lkap_contracts.api_models import McpOauthStartIn, McpOauthStartOut, McpOauthStatusOut
from lkap_contracts.tools import McpOAuthAuth, McpServerDefinition, ToolDefinition
from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api import net_guard
from lkap_api.auth import audit
from lkap_api.auth.deps import WorkspaceContext
from lkap_api.auth.roles import Requirement
from lkap_api.db.models import Tool
from lkap_api.errors import NotFoundError, UnprocessableEntityError
from lkap_api.logging import get_logger
from lkap_api.mcp_oauth.credential import binds_tool, load_sign_in, parse_time
from lkap_api.mcp_oauth.discovery import discover
from lkap_api.mcp_oauth.flows import create_flow
from lkap_api.mcp_oauth.http import McpOauthError, UrlPolicy, host_of
from lkap_api.mcp_oauth.registration import NeedsClientRegistration, choose_client, redirect_uri
from lkap_api.settings import Settings
from lkap_api.vault import Vault

log = get_logger(__name__)

#: Starting a sign-in binds a credential to a tool: the ``/v1/credentials`` write rule.
START_REQUIREMENT: Final = Requirement("admin", "providers:write")
#: Reading a tool's sign-in status.
STATUS_REQUIREMENT: Final = Requirement("admin", "providers:read")
#: Discovery plus registration give up after this many seconds in total.
START_TIMEOUT_S: Final[float] = 30.0

_TOOL: TypeAdapter[ToolDefinition] = TypeAdapter(ToolDefinition)


def as_http_error(exc: McpOauthError) -> UnprocessableEntityError:
    """The 422 a refused sign-in step becomes (``details.reason``; never a url or a value)."""
    details: dict[str, str] = {"reason": exc.reason}
    if exc.field is not None:
        details["field"] = exc.field
    return UnprocessableEntityError(str(exc), details=details)


async def oauth_tool(
    db: AsyncSession, ctx: WorkspaceContext, tool_id: str
) -> tuple[Tool, McpServerDefinition, McpOAuthAuth]:
    row = await db.scalar(select(Tool).where(Tool.id == tool_id, Tool.workspace_id == ctx.workspace_id))
    if row is None:
        raise NotFoundError(f"unknown tool '{tool_id}'")
    definition = _TOOL.validate_python(row.definition)
    if not isinstance(definition, McpServerDefinition) or not isinstance(definition.auth, McpOAuthAuth):
        raise UnprocessableEntityError(
            "this tool is not an MCP server that signs in (set auth.kind to 'oauth')",
            details={"reason": "not_oauth", "field": "definition.auth.kind"},
        )
    return row, definition, definition.auth


def _audit(db: AsyncSession, ctx: WorkspaceContext, tool_id: str, action: str, **payload: object) -> None:
    audit.record(
        db,
        workspace_id=ctx.workspace_id,
        actor_type=ctx.actor.actor_type,
        actor_id=ctx.actor.id,
        action=action,
        target_type="tool",
        target_id=tool_id,
        payload=dict(payload),
    )


async def start_sign_in(
    db: AsyncSession,
    vault: Vault,
    client: httpx.AsyncClient,
    settings: Settings,
    ctx: WorkspaceContext,
    tool_id: str,
    payload: McpOauthStartIn,
) -> McpOauthStartOut:
    """Discover, register, write the flow row and return where to send the browser."""
    ctx.check(START_REQUIREMENT)
    row, definition, auth = await oauth_tool(db, ctx, tool_id)
    if definition.origin is not None:
        raise UnprocessableEntityError(
            "an app server's access is managed by its provider, not by signing in here",
            details={"reason": "provider_managed"},
        )
    problem = net_guard.mcp_policy(settings).problem(definition.url)
    if problem is not None:
        raise UnprocessableEntityError(
            problem, details={"field": "definition.url", "reason": "blocked_destination"}
        )
    policy = UrlPolicy.from_settings(settings)
    try:
        redirect = redirect_uri(settings)
        async with asyncio.timeout(START_TIMEOUT_S):
            found = await discover(
                client,
                definition.url,
                policy,
                scopes_override=auth.scopes,
                authorization_server=payload.authorization_server,
            )
            try:
                choice = await choose_client(
                    db,
                    vault,
                    client,
                    settings,
                    policy,
                    workspace_id=ctx.workspace_id,
                    auth=auth,
                    server=found.auth_server,
                    redirect=redirect,
                    client_secret=payload.client_secret,
                )
            except NeedsClientRegistration:
                _audit(
                    db,
                    ctx,
                    row.id,
                    "mcp_oauth.start",
                    outcome="needs_client_registration",
                    issuer_host=host_of(found.auth_server.issuer),
                )
                return McpOauthStartOut(
                    status="needs_client_registration", redirect_uri=redirect, issuer=found.auth_server.issuer
                )
    except TimeoutError as exc:
        raise as_http_error(
            McpOauthError("unreachable", "the sign-in provider did not answer in time")
        ) from exc
    except McpOauthError as exc:
        log.info("mcp_oauth_start_refused", tool_id=row.id, reason=exc.reason)
        raise as_http_error(exc) from exc
    started = await create_flow(
        db,
        vault,
        workspace_id=ctx.workspace_id,
        tool_id=row.id,
        actor_type=ctx.actor.actor_type,
        actor_id=ctx.actor.id,
        discovery=found,
        client=choice,
        redirect=redirect,
    )
    _audit(
        db,
        ctx,
        row.id,
        "mcp_oauth.start",
        outcome="redirect",
        issuer_host=host_of(found.auth_server.issuer),
        registration=choice.registration,
    )
    log.info("mcp_oauth_started", tool_id=row.id, registration=choice.registration)
    return McpOauthStartOut(
        status="redirect",
        authorization_url=started.authorization_url,
        expires_at=started.expires_at,
        redirect_uri=redirect,
        issuer=found.auth_server.issuer,
        registration=choice.registration,
    )


async def sign_in_status(
    db: AsyncSession, vault: Vault, ctx: WorkspaceContext, tool_id: str
) -> McpOauthStatusOut:
    """The tool's sign-in, from its ``mcp-oauth`` credential (no token material)."""
    ctx.check(STATUS_REQUIREMENT)
    row, definition, auth = await oauth_tool(db, ctx, tool_id)
    loaded = await load_sign_in(db, vault, workspace_id=ctx.workspace_id, credential_id=auth.credential_id)
    if loaded is None or not binds_tool(loaded[1], tool_id=row.id, url=definition.url):
        return McpOauthStatusOut(status="not_connected")
    bag = loaded[1]
    match bag.get("status"):
        case "needs_reauth":
            status: str = "needs_reauth"
        case "revoked":
            status = "revoked"
        case _:
            status = "connected"
    registration = bag.get("registration")
    return McpOauthStatusOut.model_validate(
        {
            "status": status,
            "issuer": bag.get("issuer"),
            "scopes": (bag.get("scope") or "").split(),
            "expires_at": parse_time(bag.get("expires_at")),
            "connected_at": parse_time(bag.get("connected_at")),
            "last_refresh_at": parse_time(bag.get("last_refresh_at")),
            "registration": registration if registration in ("preregistered", "cimd", "dcr") else None,
            # V5-16: sessions use the sign-in (the worker's api-issued bearer).
            "worker_supported": True,
        }
    )


__all__ = [
    "START_REQUIREMENT",
    "STATUS_REQUIREMENT",
    "as_http_error",
    "oauth_tool",
    "sign_in_status",
    "start_sign_in",
]
