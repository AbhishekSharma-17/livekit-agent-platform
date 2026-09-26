"""Disconnecting an MCP server's sign-in (V5-16, research-v4 tools §4.3.6).

``POST /v1/tools/{tool_id}/oauth/revoke``, and the same steps when the tool is deleted:

1. **RFC 7009** at the provider's ``revocation_endpoint`` (when it has one): the refresh
   token first (revoking it usually revokes its access tokens too), then the access
   token, each with the client's authentication.
2. **RFC 7592** client deletion, best effort, only for a dynamically registered client
   whose ``registration_client_uri`` passes the URL check, when LKAP holds its
   ``registration_access_token`` and **no other** sign-in or live sign-in flow of the
   workspace uses the same client (one registered client serves every tool at that
   issuer, asks #114 b). On success the ``mcp_oauth_clients`` row goes too.
3. The ``mcp-oauth`` credential is deleted and the tool's ``auth.credential_id`` cleared.

Every network step goes through the network guard with no redirects and a bound; a
failure is recorded in the audit row (``mcp_oauth.revoked``) and never stops step 3.
No token, secret or url is logged or audited — only the provider's host and outcomes.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Final, Literal

import httpx
from lkap_contracts.api_models import McpOauthStatusOut
from lkap_contracts.providers import MCP_OAUTH_PROVIDER_ID
from lkap_contracts.tools import McpOAuthAuth, McpServerDefinition, ToolDefinition
from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.auth import audit
from lkap_api.auth.deps import WorkspaceContext
from lkap_api.db.models import Credential, McpOauthClient, McpOauthFlow, Tool, utcnow
from lkap_api.logging import get_logger
from lkap_api.mcp_oauth.credential import binds_tool, load_sign_in
from lkap_api.mcp_oauth.http import McpOauthError, UrlPolicy, fetch, host_of, require_url
from lkap_api.mcp_oauth.registration import client_secrets
from lkap_api.mcp_oauth.service import START_REQUIREMENT, oauth_tool
from lkap_api.mcp_oauth.tokens import client_authentication, oauth_definition
from lkap_api.settings import Settings
from lkap_api.vault import Vault

log = get_logger(__name__)

#: All of a disconnect's provider calls give up after this many seconds in total.
REVOKE_TIMEOUT_S: Final[float] = 20.0

RevocationOutcome = Literal["ok", "failed", "unsupported", "no_token"]
Trigger = Literal["revoke", "tool_delete"]

_TOOL: TypeAdapter[ToolDefinition] = TypeAdapter(ToolDefinition)


@dataclass(frozen=True)
class RevokeOutcome:
    """What the provider calls achieved (the credential is deleted either way)."""

    revocation: RevocationOutcome
    client_deleted: bool | None
    """``None``: deletion was not attempted (not a dynamic client, shared, or no access token)."""


async def _revoke_tokens(
    client: httpx.AsyncClient, policy: UrlPolicy, bag: dict[str, str]
) -> RevocationOutcome:
    endpoint = bag.get("revocation_endpoint")
    if not endpoint:
        return "unsupported"
    tokens = [(hint, bag[hint]) for hint in ("refresh_token", "access_token") if bag.get(hint)]
    if not tokens:
        return "no_token"
    try:
        url = require_url(endpoint, policy, field="revocation_endpoint")
    except McpOauthError as exc:
        log.warning("mcp_oauth_revoke_refused", reason=exc.reason)
        return "failed"
    outcome: RevocationOutcome = "ok"
    for hint, token in tokens:
        data = {"token": token, "token_type_hint": hint}
        basic = client_authentication(
            data,
            client_id=bag.get("client_id", ""),
            client_secret=bag.get("client_secret"),
            method=bag.get("token_endpoint_auth_method"),
        )
        try:
            fetched = await fetch(
                client,
                "POST",
                url,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data=data,
                auth=basic,
            )
        except McpOauthError as exc:
            log.warning("mcp_oauth_revoke_failed", token_type=hint, reason=exc.reason)
            outcome = "failed"
            continue
        if fetched.status != 200:  # RFC 7009 §2.2: 200 even for an unknown token
            log.warning("mcp_oauth_revoke_failed", token_type=hint, status=fetched.status)
            outcome = "failed"
    return outcome


async def _client_shared(
    db: AsyncSession, vault: Vault, *, workspace_id: str, credential_id: str, issuer: str, client_id: str
) -> bool:
    """Whether another sign-in (or a live flow) of the workspace uses the same client."""
    others = (
        await db.execute(
            select(Credential).where(
                Credential.workspace_id == workspace_id,
                Credential.provider_id == MCP_OAUTH_PROVIDER_ID,
                Credential.id != credential_id,
            )
        )
    ).scalars()
    for other in others:
        bag = vault.decrypt(other.ciphertext)
        if bag.get("client_id") == client_id and bag.get("issuer") == issuer:
            return True
    live_flow = await db.scalar(
        select(McpOauthFlow.state_hash)
        .join(McpOauthClient, McpOauthClient.id == McpOauthFlow.client_row_id)
        .where(
            McpOauthFlow.workspace_id == workspace_id,
            McpOauthFlow.consumed_at.is_(None),
            McpOauthFlow.expires_at > utcnow(),
            McpOauthClient.workspace_id == workspace_id,
            McpOauthClient.issuer == issuer,
            McpOauthClient.client_id == client_id,
        )
        .limit(1)
    )
    return live_flow is not None


async def _delete_client(
    db: AsyncSession,
    vault: Vault,
    client: httpx.AsyncClient,
    policy: UrlPolicy,
    *,
    workspace_id: str,
    credential_id: str,
    bag: dict[str, str],
) -> bool | None:
    """RFC 7592 deletion of a dynamic client nobody else uses; ``None`` when not attempted."""
    issuer, client_id = bag.get("issuer", ""), bag.get("client_id", "")
    if bag.get("registration") != "dcr" or not issuer or not client_id:
        return None
    if await _client_shared(
        db, vault, workspace_id=workspace_id, credential_id=credential_id, issuer=issuer, client_id=client_id
    ):
        return None
    rows = list(
        (
            await db.execute(
                select(McpOauthClient).where(
                    McpOauthClient.workspace_id == workspace_id,
                    McpOauthClient.issuer == issuer,
                    McpOauthClient.client_id == client_id,
                    McpOauthClient.registration == "dcr",
                )
            )
        ).scalars()
    )
    # The clients row is the shared source of truth; the bag's copies are the fallback.
    stored_secrets = client_secrets(rows[0], vault) if rows else {}
    uri = (rows[0].registration_client_uri if rows else None) or bag.get("registration_client_uri")
    access = stored_secrets.get("registration_access_token") or bag.get("registration_access_token")
    if not uri or not access:
        return None
    try:
        # asks #114 a: the DCR answer's uri is server-supplied, so it is checked before any use.
        url = require_url(uri, policy, field="registration_client_uri")
        fetched = await fetch(client, "DELETE", url, headers={"Authorization": f"Bearer {access}"})
    except McpOauthError as exc:
        log.warning("mcp_oauth_client_delete_failed", reason=exc.reason)
        return False
    if fetched.status not in (200, 204):
        log.warning("mcp_oauth_client_delete_failed", status=fetched.status)
        return False
    for row in rows:
        await db.delete(row)
    return True


async def revoke_credential(
    db: AsyncSession,
    vault: Vault,
    client: httpx.AsyncClient,
    settings: Settings,
    *,
    workspace_id: str,
    credential: Credential,
    bag: dict[str, str],
) -> RevokeOutcome:
    """Revoke at the provider (best effort, bounded), then delete the credential row.

    The caller has checked that ``credential`` is an ``mcp-oauth`` credential of
    ``workspace_id``; it writes the audit row.
    """
    policy = UrlPolicy.from_settings(settings)
    revocation: RevocationOutcome = "failed"
    client_deleted: bool | None = None
    try:
        async with asyncio.timeout(REVOKE_TIMEOUT_S):
            revocation = await _revoke_tokens(client, policy, bag)
            client_deleted = await _delete_client(
                db, vault, client, policy, workspace_id=workspace_id, credential_id=credential.id, bag=bag
            )
    except TimeoutError:
        log.warning("mcp_oauth_revoke_timed_out", credential_id=credential.id)
    await db.delete(credential)
    await db.flush()
    return RevokeOutcome(revocation=revocation, client_deleted=client_deleted)


def _audit(
    db: AsyncSession,
    ctx: WorkspaceContext,
    tool_id: str,
    *,
    credential_id: str,
    bag: dict[str, str],
    outcome: RevokeOutcome,
    trigger: Trigger,
) -> None:
    audit.record(
        db,
        workspace_id=ctx.workspace_id,
        actor_type=ctx.actor.actor_type,
        actor_id=ctx.actor.id,
        action="mcp_oauth.revoked",
        target_type="tool",
        target_id=tool_id,
        payload={
            "trigger": trigger,
            "credential_id": credential_id,
            "issuer_host": host_of(bag.get("issuer", "")),
            "revocation": outcome.revocation,
            "client_deleted": outcome.client_deleted,
        },
    )


def _clear_reference(row: Tool, definition: McpServerDefinition) -> None:
    stored = definition.model_dump(mode="json")
    stored["auth"]["credential_id"] = None
    stored["credential_id"] = None
    row.definition = _TOOL.validate_python(stored).model_dump(mode="json")
    row.updated_at = utcnow()


async def disconnect_tool(
    db: AsyncSession,
    vault: Vault,
    client: httpx.AsyncClient,
    settings: Settings,
    ctx: WorkspaceContext,
    row: Tool,
    *,
    trigger: Trigger,
) -> RevokeOutcome | None:
    """Revoke and delete the sign-in of the oauth MCP tool ``row``; ``None`` when it has none.

    A credential the tool references but that belongs to another tool (``binds_tool``
    fails) is left alone; the reference is cleared.
    """
    definition = oauth_definition(row)
    if definition is None or not isinstance(definition.auth, McpOAuthAuth):
        return None
    credential_id = definition.auth.credential_id
    loaded = await load_sign_in(db, vault, workspace_id=row.workspace_id, credential_id=credential_id)
    outcome: RevokeOutcome | None = None
    if loaded is not None and binds_tool(loaded[1], tool_id=row.id, url=definition.url):
        credential, bag = loaded
        outcome = await revoke_credential(
            db, vault, client, settings, workspace_id=row.workspace_id, credential=credential, bag=bag
        )
        _audit(db, ctx, row.id, credential_id=credential.id, bag=bag, outcome=outcome, trigger=trigger)
        log.info(
            "mcp_oauth_revoked",
            tool_id=row.id,
            trigger=trigger,
            revocation=outcome.revocation,
            client_deleted=outcome.client_deleted,
        )
    if trigger == "revoke" and credential_id is not None:
        _clear_reference(row, definition)
    return outcome


async def revoke_tool_sign_in(
    db: AsyncSession,
    vault: Vault,
    client: httpx.AsyncClient,
    settings: Settings,
    ctx: WorkspaceContext,
    tool_id: str,
) -> McpOauthStatusOut:
    """``POST /v1/tools/{tool_id}/oauth/revoke``: disconnect, then report ``not_connected``."""
    ctx.check(START_REQUIREMENT)
    row, _definition, _auth = await oauth_tool(db, ctx, tool_id)
    await disconnect_tool(db, vault, client, settings, ctx, row, trigger="revoke")
    return McpOauthStatusOut(status="not_connected", worker_supported=True)


__all__ = [
    "REVOKE_TIMEOUT_S",
    "RevokeOutcome",
    "disconnect_tool",
    "revoke_credential",
    "revoke_tool_sign_in",
]
