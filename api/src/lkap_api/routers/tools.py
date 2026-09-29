"""Declarative tool CRUD, the HTTP dry run and the MCP test connection used by the console editor.

Workspace scoping (V2-02, asks #25): every admin handler takes ``ctx: AdminCtxDep``;
reads filter on ``ctx.workspace_id``, inserts set it, and a row of another
workspace is a 404."""

from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Query, Request, Response, status
from lkap_contracts.api_models import ToolCreate, ToolDryRunRequest, ToolDryRunResult, ToolOut, ToolPage
from lkap_contracts.providers import MCP_OAUTH_PROVIDER_ID
from lkap_contracts.tool_providers import COMPOSIO_PROVIDER_ID
from lkap_contracts.tools import (
    DatasetToolDefinition,
    HttpToolDefinition,
    McpOAuthAuth,
    McpServerDefinition,
    McpTestResult,
    ProviderToolDefinition,
    ToolDefinition,
)
from pydantic import TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api import net_guard
from lkap_api.auth.deps import WorkspaceContext
from lkap_api.auth.roles import Requirement
from lkap_api.config_service import host_allowed, render_arguments, resolve_tool_definition
from lkap_api.datasets.tools import check_dataset_tool
from lkap_api.db.models import Agent, Credential, Tool, utcnow
from lkap_api.db.session import Database
from lkap_api.deps import AdminCtxDep, DbDep, HttpClientDep, SettingsDep, VaultDep
from lkap_api.errors import BadRequestError, ForbiddenError, NotFoundError, UnprocessableEntityError
from lkap_api.key_usage import mark_used
from lkap_api.logging import get_logger
from lkap_api.mcp_oauth.credential import binds_tool, load_sign_in
from lkap_api.mcp_oauth.revoke import disconnect_tool
from lkap_api.mcp_oauth.tokens import NeedsReauth, TokenUnavailable, get_access_token
from lkap_api.mcp_test import McpTestError, list_mcp_tools
from lkap_api.settings import Settings
from lkap_api.tool_providers.bindings import (
    composio_binding_problem,
    is_composio_credential,
    is_composio_url,
    provider_connection_problem,
)
from lkap_api.tool_providers.service import AppConnection
from lkap_api.vault import Vault

log = get_logger(__name__)

router = APIRouter(prefix="/v1/tools", tags=["tools"])

_TOOL_ADAPTER: TypeAdapter[ToolDefinition] = TypeAdapter(ToolDefinition)

#: Dry-run responses are truncated to keep the console payload small.
DRY_RUN_MAX_CHARS = 8000
#: The dry run reads at most this many response bytes (S5-10); a larger answer is refused.
DRY_RUN_MAX_BYTES = 1_000_000

#: Matches `{{ secret.NAME }}` placeholders (F-15); mirrors
#: `lkap_api.config_service._SECRET_RE`, kept local so this file's one
#: F-15 change never has to touch `config_service.py` (owned by V2-03).
_SECRET_RE = re.compile(r"\{\{\s*secret\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def _referenced_secret_names(definition: ToolDefinition) -> set[str]:
    """Every `{{ secret.NAME }}` name the tool's url/headers/body reference."""
    if isinstance(definition, DatasetToolDefinition):  # V6-16: a lookup carries no secret
        return set()
    if isinstance(definition, ProviderToolDefinition):  # V5-47: no url; the key rides a header
        texts: list[str] = list(definition.headers.values())
    else:
        texts = [definition.url, *definition.headers.values()]
    if isinstance(definition, HttpToolDefinition) and definition.body_template:
        texts.append(definition.body_template)
    names: set[str] = set()
    for text in texts:
        names.update(_SECRET_RE.findall(text))
    return names


def _authority_secret_problem(url: str) -> str | None:
    """S5-17: a ``{{ secret.* }}`` in a url's scheme or authority, which could move the host.

    The ceilings (``LKAP_MCP_ALLOWED_HOSTS``, the tool's ``allowed_hosts``) are checked on the
    saved url; a secret substituted into the authority (``443@evil.example``) would change
    the host after that check. Secrets belong in the path, query, headers or body.
    """
    scheme, sep, rest = url.partition("://")
    authority = rest if sep else scheme
    for stop in "/?#":
        authority = authority.split(stop, 1)[0]
    if _SECRET_RE.search(scheme if sep else "") or _SECRET_RE.search(authority):
        return (
            "a secret may not be placed in the url's scheme, host or port; use the path, a header or the body"
        )
    return None


def _definition_of(row: Tool) -> ToolDefinition:
    return _TOOL_ADAPTER.validate_python(row.definition)


def _to_out(row: Tool) -> ToolOut:
    definition = _definition_of(row)
    return ToolOut(
        id=row.id,
        agent_id=row.agent_id,
        kind=definition.kind,
        name=row.name,
        definition=definition,
        enabled=bool(row.enabled),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _load(db: AsyncSession, ctx: WorkspaceContext, tool_id: str) -> Tool:
    """Load a tool of the caller's workspace (404 for any other workspace)."""
    row = await db.scalar(select(Tool).where(Tool.id == tool_id, Tool.workspace_id == ctx.workspace_id))
    if row is None:
        raise NotFoundError(f"unknown tool '{tool_id}'")
    return row


#: Binding a secret to a tool is credential management (V2-21).
_BIND_CREDENTIAL = Requirement("admin", "providers:write")  # the /v1/credentials write rule
#: The only credential kind a tool may reference (a secret bag made for tools).
TOOL_SECRET_PROVIDER = "http-tool-secret"


async def _workspace_credential(db: AsyncSession, workspace_id: str, credential_id: str) -> Credential | None:
    """A credential of the given workspace, or ``None``."""
    stmt = select(Credential).where(Credential.id == credential_id, Credential.workspace_id == workspace_id)
    return (await db.execute(stmt)).scalar_one_or_none()


def _check_mcp_definition(definition: McpServerDefinition, settings: Settings) -> None:
    """V5-09: an MCP server's auth kind and url, at save and before a test connection.

    The url must pass :meth:`net_guard.McpPolicy.problem`: the network guard, ``https``
    outside a dev loopback host, and ``LKAP_MCP_ALLOWED_HOSTS`` when set (D-V5-4).
    V5-14: an ``oauth`` server's url carries no ``{{ secret.* }}`` placeholder (its
    credential is the sign-in, whose tokens must never be substituted into a url). A
    pre-registered server may be saved before its client id exists (ask #166): its
    ``oauth/start`` then answers ``needs_client_registration`` with the return address.
    """
    if isinstance(definition.auth, McpOAuthAuth):
        if _SECRET_RE.search(definition.url):
            raise UnprocessableEntityError(
                "an MCP server that signs in may not put secrets in its url",
                details={"field": "definition.url", "reason": "oauth_url_placeholder"},
            )
    problem = net_guard.mcp_policy(settings).problem(definition.url)
    if problem is not None:
        raise UnprocessableEntityError(
            problem, details={"field": "definition.url", "reason": "blocked_destination"}
        )


async def _check_payload(
    db: AsyncSession,
    vault: Vault,
    ctx: WorkspaceContext,
    payload: ToolCreate,
    settings: Settings,
    *,
    tool_id: str | None = None,
) -> None:
    """Validate cross-references, kind/definition agreement and secret placeholders.

    F-15: every `{{ secret.NAME }}` the definition's url/headers/body
    reference must resolve — either the tool has no `credential_id` at all
    (error: nothing to substitute from) or `NAME` is a key of that
    credential's decrypted secret bag (error otherwise, naming only the
    unresolved names, never a value). An MCP server's header auth binds an
    ``http-tool-secret`` bag through ``auth.credential_id``, which the contract
    mirrors to the deprecated top-level ``credential_id`` checked here (V5-09).

    V5-14: an ``oauth`` MCP server binds only its own ``mcp-oauth`` sign-in: on update
    (``tool_id`` set), when the credential's bag names this tool and a resource covering
    the url. The sign-in callback writes that binding; nothing else may create it, and no
    other definition may reference an ``mcp-oauth`` credential.
    """
    if payload.definition.kind != payload.kind:
        raise UnprocessableEntityError(
            f"kind '{payload.kind}' does not match definition kind '{payload.definition.kind}'"
        )
    if isinstance(payload.definition, McpServerDefinition):
        _check_mcp_definition(payload.definition, settings)
    if isinstance(payload.definition, (McpServerDefinition, HttpToolDefinition)):
        authority_problem = _authority_secret_problem(payload.definition.url)
        if authority_problem is not None:
            raise UnprocessableEntityError(
                authority_problem, details={"field": "definition.url", "reason": "secret_in_authority"}
            )
    if (
        payload.agent_id is not None
        and await db.scalar(
            select(Agent.id).where(Agent.id == payload.agent_id, Agent.workspace_id == ctx.workspace_id)
        )
        is None
    ):
        raise UnprocessableEntityError(f"unknown agent '{payload.agent_id}'")
    if isinstance(payload.definition, DatasetToolDefinition):
        # V6-16: the dataset must be this workspace's and the columns its own.
        await check_dataset_tool(db, ctx.workspace_id, payload.definition)
    credential_id = getattr(payload.definition, "credential_id", None)
    credential: Credential | None = None
    if credential_id is not None:
        # V2-21: a tool decides where its secrets are sent (url, headers, body), so
        # binding one is credential management (the /v1/credentials write rule),
        # never a builder, who could otherwise send an admin-held key to a host they own.
        if not ctx.allows(_BIND_CREDENTIAL):
            raise ForbiddenError(
                "attaching a credential to a tool needs the 'admin' role (and the "
                "'providers:write' scope for API keys): the tool decides where the secret is sent",
                details={"required_role": _BIND_CREDENTIAL.role, "required_scope": _BIND_CREDENTIAL.scope},
            )
        credential = await _workspace_credential(db, ctx.workspace_id, credential_id)
        if credential is None:
            raise UnprocessableEntityError(f"unknown credential '{credential_id}'")
        if credential.provider_id == MCP_OAUTH_PROVIDER_ID or isinstance(
            getattr(payload.definition, "auth", None), McpOAuthAuth
        ):
            _check_sign_in_binding(payload, credential, vault, tool_id)
        elif is_composio_credential(credential.provider_id):
            # V5-18 (COMPOSIO.md §4, D-V5-C10): the Composio key binds only to Composio app
            # servers (and V5-47's action tools); an HTTP tool could send it anywhere.
            problem = composio_binding_problem(payload.definition, credential.provider_id)
            if problem is not None:
                raise UnprocessableEntityError(
                    problem, details={"credential_id": credential_id, "provider_id": credential.provider_id}
                )
        elif credential.provider_id != TOOL_SECRET_PROVIDER:
            raise UnprocessableEntityError(
                f"a tool may only use '{TOOL_SECRET_PROVIDER}' credentials, not a provider key "
                f"('{credential.provider_id}')",
                details={"credential_id": credential_id, "provider_id": credential.provider_id},
            )
    if isinstance(payload.definition, ProviderToolDefinition):
        await _check_provider_binding(db, vault, ctx, payload)
    referenced = _referenced_secret_names(payload.definition)
    if referenced:
        if credential is None:
            raise UnprocessableEntityError(
                f"references {{{{ secret.{sorted(referenced)[0]} }}}} but has no credential_id"
            )
        known = set(vault.decrypt(credential.ciphertext))
        unknown = sorted(referenced - known)
        if unknown:
            raise UnprocessableEntityError(
                f"unknown secret name(s) for credential '{credential_id}': {', '.join(unknown)}"
            )


def _check_sign_in_binding(
    payload: ToolCreate, credential: Credential, vault: Vault, tool_id: str | None
) -> None:
    """V5-14: an ``mcp-oauth`` credential binds only to the oauth MCP tool whose sign-in wrote it."""
    definition = payload.definition
    details = {"credential_id": credential.id, "provider_id": credential.provider_id}
    if not isinstance(definition, McpServerDefinition) or not isinstance(definition.auth, McpOAuthAuth):
        raise UnprocessableEntityError(
            "an MCP sign-in credential belongs to the MCP server that signed in, not to this tool",
            details={**details, "reason": "oauth_credential_misuse"},
        )
    if credential.provider_id != MCP_OAUTH_PROVIDER_ID:
        raise UnprocessableEntityError(
            "an MCP server that signs in takes no key: its credential is created by signing in",
            details={**details, "reason": "oauth_credential_misuse"},
        )
    if tool_id is None or not binds_tool(
        vault.decrypt(credential.ciphertext), tool_id=tool_id, url=definition.url
    ):
        raise UnprocessableEntityError(
            "this sign-in belongs to another server or address: clear auth.credential_id and sign in again",
            details={**details, "reason": "oauth_credential_mismatch"},
        )


async def _check_provider_binding(
    db: AsyncSession, vault: Vault, ctx: WorkspaceContext, payload: ToolCreate
) -> None:
    """V5-47 (COMPOSIO.md §4, D-V5-C10): a ``provider`` tool binds the Composio key and a connected app.

    The key (``credential_id``) is required; ``connection_id`` must be a connected-app row of
    this workspace whose subject is the tool's, and an app connected for one agent serves only
    that agent's tools.
    """
    definition = payload.definition
    assert isinstance(definition, ProviderToolDefinition)  # noqa: S101 - narrowed by the caller
    if definition.credential_id is None:
        raise UnprocessableEntityError("an app action tool needs the Composio key as credential_id")
    key = await _workspace_credential(db, ctx.workspace_id, definition.credential_id)
    if key is None or key.provider_id != COMPOSIO_PROVIDER_ID:
        # S5-34: the action's key rides to the provider's host; only the provider's own key.
        raise UnprocessableEntityError(
            "an app action tool takes the Composio key as its credential, not another key",
            details={"credential_id": definition.credential_id},
        )
    row = await _workspace_credential(db, ctx.workspace_id, definition.connection_id)
    if row is None:
        raise UnprocessableEntityError(f"unknown app connection '{definition.connection_id}'")
    problem = composio_binding_problem(definition, row.provider_id, field="connection_id")
    if problem is None:
        conn = AppConnection.from_row(row, vault)
        problem = provider_connection_problem(
            definition_subject=definition.subject,
            connection_subject=conn.subject,
            tool_agent_id=payload.agent_id,
        )
        if (
            problem is None
            and definition.connected_account_id is not None
            and conn.connected_account_id is not None
            and definition.connected_account_id != conn.connected_account_id
        ):
            # S5-34: the pinned account must be the connection's own account.
            problem = "the tool's pinned account is not this connection's account"
    if problem is not None:
        raise UnprocessableEntityError(problem, details={"connection_id": definition.connection_id})


@router.post(
    "",
    response_model=ToolOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a tool",
    description=(
        "Stores an HTTP tool, an MCP server, a connected app's action (`provider`) or a lookup in "
        "one of the workspace's datasets (`dataset`), shared or owned by one agent."
    ),
)
async def create_tool(
    payload: ToolCreate, db: DbDep, vault: VaultDep, settings: SettingsDep, ctx: AdminCtxDep
) -> ToolOut:
    """Create a declarative tool row in the caller's workspace."""
    await _check_payload(db, vault, ctx, payload, settings)
    row = Tool(
        workspace_id=ctx.workspace_id,
        agent_id=payload.agent_id,
        kind=payload.kind,
        name=payload.name,
        definition=payload.definition.model_dump(mode="json"),
        enabled=payload.enabled,
    )
    db.add(row)
    await db.flush()
    log.info("tool_created", tool_id=row.id, kind=row.kind, agent_id=row.agent_id)
    return _to_out(row)


@router.get(
    "",
    response_model=ToolPage,
    summary="List tools",
    description="All tool rows, optionally filtered by owning agent or kind.",
)
async def list_tools(
    db: DbDep,
    ctx: AdminCtxDep,
    agent_id: str | None = Query(default=None, description="Only tools owned by this agent"),
    kind: str | None = Query(default=None, description="http | mcp | provider | dataset"),
) -> ToolPage:
    """Return the workspace's tool rows, newest first."""
    stmt = select(Tool).where(Tool.workspace_id == ctx.workspace_id)
    count_stmt = select(func.count()).select_from(Tool).where(Tool.workspace_id == ctx.workspace_id)
    if agent_id:
        stmt = stmt.where(Tool.agent_id == agent_id)
        count_stmt = count_stmt.where(Tool.agent_id == agent_id)
    if kind:
        stmt = stmt.where(Tool.kind == kind)
        count_stmt = count_stmt.where(Tool.kind == kind)
    rows = (await db.execute(stmt.order_by(Tool.created_at.desc()))).scalars().all()
    total = (await db.execute(count_stmt)).scalar_one()
    return ToolPage(items=[_to_out(r) for r in rows], total=total)


@router.get(
    "/{tool_id}",
    response_model=ToolOut,
    summary="Get a tool",
    description="One tool definition. Secret placeholders are returned unsubstituted.",
)
async def get_tool(tool_id: str, db: DbDep, ctx: AdminCtxDep) -> ToolOut:
    """Return one tool row."""
    return _to_out(await _load(db, ctx, tool_id))


@router.put(
    "/{tool_id}",
    response_model=ToolOut,
    summary="Update a tool",
    description="Replaces the tool definition, its name, owner and enabled flag.",
)
async def update_tool(
    tool_id: str, payload: ToolCreate, db: DbDep, vault: VaultDep, settings: SettingsDep, ctx: AdminCtxDep
) -> ToolOut:
    """Replace a tool definition."""
    row = await _load(db, ctx, tool_id)
    await _check_payload(db, vault, ctx, payload, settings, tool_id=row.id)
    stored = _definition_of(row)
    definition = _keep_oauth_sign_in(stored, _keep_mcp_snapshot(stored, payload.definition))
    row.agent_id = payload.agent_id
    row.kind = payload.kind
    row.name = payload.name
    row.definition = definition.model_dump(mode="json")
    row.enabled = payload.enabled
    row.updated_at = utcnow()
    await db.flush()
    log.info("tool_updated", tool_id=row.id, kind=row.kind)
    return _to_out(row)


def _keep_mcp_snapshot(stored: ToolDefinition, incoming: ToolDefinition) -> ToolDefinition:
    """Carry the stored ``cached_tools`` over a save that does not send one (V5-09).

    Editors that predate the snapshot post the definition without it; the tools a
    server listed stay valid as long as the url is the same.
    """
    if (
        isinstance(stored, McpServerDefinition)
        and isinstance(incoming, McpServerDefinition)
        and incoming.cached_tools is None
        and stored.cached_tools is not None
        and stored.url == incoming.url
    ):
        return incoming.model_copy(
            update={"cached_tools": stored.cached_tools, "cached_at": stored.cached_at}
        )
    return incoming


def _keep_oauth_sign_in(stored: ToolDefinition, incoming: ToolDefinition) -> ToolDefinition:
    """Carry an oauth server's sign-in over a save that does not send it (V5-14).

    The callback writes ``auth.credential_id``; an editor that re-posts the definition it
    loaded before the sign-in finished would otherwise drop it. Kept only while the url
    and the auth kind are unchanged (a new address needs a new sign-in).
    """
    if (
        isinstance(stored, McpServerDefinition)
        and isinstance(incoming, McpServerDefinition)
        and isinstance(stored.auth, McpOAuthAuth)
        and isinstance(incoming.auth, McpOAuthAuth)
        and stored.auth.credential_id is not None
        and incoming.auth.credential_id is None
        and stored.url == incoming.url
    ):
        credential_id = stored.auth.credential_id
        return incoming.model_copy(
            update={
                "auth": incoming.auth.model_copy(update={"credential_id": credential_id}),
                "credential_id": credential_id,
            }
        )
    return incoming


@router.delete(
    "/{tool_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a tool",
    description=(
        "Removes the tool row; agents referencing it fail validation until updated. An MCP "
        "server that signs in is disconnected first: its sign-in is revoked at the provider "
        "(best effort) and deleted."
    ),
)
async def delete_tool(
    tool_id: str,
    db: DbDep,
    vault: VaultDep,
    client: HttpClientDep,
    settings: SettingsDep,
    ctx: AdminCtxDep,
) -> Response:
    """Delete a tool row (V5-16: an oauth MCP server's sign-in is revoked and deleted first)."""
    row = await _load(db, ctx, tool_id)
    await disconnect_tool(db, vault, client, settings, ctx, row, trigger="tool_delete")
    await db.delete(row)
    log.info("tool_deleted", tool_id=tool_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def extract_pointer(payload: object, pointer: str) -> object:
    """Resolve a JSON pointer such as ``/data/summary`` against a parsed body.

    Args:
        payload: The parsed JSON body.
        pointer: An RFC 6901 pointer; ``""`` returns the whole payload.

    Returns:
        The referenced value, or ``None`` when the path does not exist.
    """
    if not pointer:
        return payload
    cursor = payload
    for token in pointer.lstrip("/").split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(cursor, dict):
            cursor = cursor.get(token)
        elif isinstance(cursor, list) and token.isdigit() and int(token) < len(cursor):
            cursor = cursor[int(token)]
        else:
            return None
    return cursor


async def _resolved_http_definition(db: AsyncSession, vault: Vault, row: Tool) -> HttpToolDefinition:
    definition = _definition_of(row)
    if not isinstance(definition, HttpToolDefinition):
        raise BadRequestError("dry run is only available for http tools")
    secrets: dict[str, str] = {}
    if definition.credential_id:
        credential = await _workspace_credential(db, row.workspace_id, definition.credential_id)
        if credential is None:
            raise UnprocessableEntityError(f"unknown credential '{definition.credential_id}'")
        secrets = vault.decrypt(credential.ciphertext)
        await mark_used(db, [credential.id])
    resolved = resolve_tool_definition(definition, secrets)
    assert isinstance(resolved, HttpToolDefinition)  # noqa: S101 - narrowed by kind
    return resolved


@router.post(
    "/{tool_id}/dry-run",
    response_model=ToolDryRunResult,
    summary="Dry-run an HTTP tool",
    description=(
        "Executes the tool once with the supplied arguments, exactly as the worker would: "
        "template substitution, host allowlist, timeout, `result_path` and truncation. "
        "The rendered request is never echoed back, so credential headers cannot leak."
    ),
)
async def dry_run_tool(
    tool_id: str,
    payload: ToolDryRunRequest,
    db: DbDep,
    vault: VaultDep,
    client: HttpClientDep,
    settings: SettingsDep,
    ctx: AdminCtxDep,
) -> ToolDryRunResult:
    """Run an HTTP tool once and report what the model would have seen."""
    row = await _load(db, ctx, tool_id)
    definition = await _resolved_http_definition(db, vault, row)

    url = render_arguments(definition.url, payload.arguments, url_encode=True)
    # V2-21: the dry run reads the response back to the caller, so it must never
    # reach a private, loopback or metadata address, allowlisted or not.
    net_guard.validate_url(url, net_guard.policy_from_settings(settings), field_name="definition.url")
    if not host_allowed(url, allowed_hosts=definition.allowed_hosts):
        raise BadRequestError(
            "the request host is not in the tool's allowed_hosts — add it "
            "(the worker also fails closed at call time; LKAP_HTTP_TOOL_ALLOWED_HOSTS "
            "on the worker is a separate, optional list)",
            details={"allowed_hosts": definition.allowed_hosts},
        )

    body: Any = None
    if definition.method in {"POST", "PUT", "PATCH"}:
        if definition.body_template:
            rendered = render_arguments(definition.body_template, payload.arguments, url_encode=False)
            try:
                body = json.loads(rendered)
            except json.JSONDecodeError as exc:
                raise UnprocessableEntityError(f"body_template did not render valid JSON: {exc}") from exc
        else:
            body = payload.arguments

    headers = dict(definition.headers)
    if settings.http_tool_user_agent and not any(name.lower() == "user-agent" for name in headers):
        # asks #29: the same default User-Agent the worker sends; the tool's own wins.
        headers["User-Agent"] = settings.http_tool_user_agent

    started = time.perf_counter()
    too_large = False
    try:
        async with client.stream(
            definition.method,
            url,
            headers=headers or None,
            json=body,
            timeout=definition.timeout_s,
        ) as response:
            # S5-10: read at most DRY_RUN_MAX_BYTES; a larger answer is refused, not buffered.
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > DRY_RUN_MAX_BYTES:
                    too_large = True
                    break
                chunks.append(chunk)
            raw = b"".join(chunks)
    except httpx.HTTPError as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        log.warning("tool_dry_run_failed", tool_id=tool_id, error_type=type(exc).__name__)
        blocked = net_guard.blocked_cause(exc)
        return ToolDryRunResult(
            ok=False,
            result=f"request failed: {blocked if blocked is not None else type(exc).__name__}",
            status_code=None,
            duration_ms=duration_ms,
        )
    duration_ms = int((time.perf_counter() - started) * 1000)
    if too_large:
        log.info("tool_dry_run_too_large", tool_id=tool_id, status_code=response.status_code)
        return ToolDryRunResult(
            ok=False,
            result=f"the response is larger than {DRY_RUN_MAX_BYTES // 1_000_000} MB; "
            "the dry run stopped reading",
            status_code=response.status_code,
            duration_ms=duration_ms,
        )

    text = raw.decode(response.encoding or "utf-8", errors="replace")
    if definition.result_path:
        try:
            extracted = extract_pointer(json.loads(raw), definition.result_path)
        except (ValueError, RecursionError):
            extracted = None
        text = (
            ""
            if extracted is None
            else json.dumps(extracted)
            if not isinstance(extracted, str)
            else extracted
        )
    truncated = text[: min(definition.max_result_chars, DRY_RUN_MAX_CHARS)]
    log.info("tool_dry_run", tool_id=tool_id, status_code=response.status_code, duration_ms=duration_ms)
    return ToolDryRunResult(
        ok=response.status_code < 400,
        result=truncated,
        status_code=response.status_code,
        duration_ms=duration_ms,
    )


# ------------------------------------------------------------------------- MCP test
#: One request of the test connection gives up after this many seconds at most.
MCP_TEST_MAX_TIMEOUT_S = 15.0


async def _mcp_resolved(
    db: AsyncSession, vault: Vault, row: Tool, definition: McpServerDefinition
) -> McpServerDefinition:
    """The definition with secrets substituted (url and headers), as the worker would use it."""
    secrets: dict[str, str] = {}
    if definition.credential_id:
        credential = await _workspace_credential(db, row.workspace_id, definition.credential_id)
        if credential is None:
            raise UnprocessableEntityError(f"unknown credential '{definition.credential_id}'")
        secrets = vault.decrypt(credential.ciphertext)
        await mark_used(db, [credential.id])
    resolved = resolve_tool_definition(definition, secrets)
    assert isinstance(resolved, McpServerDefinition)  # noqa: S101 - narrowed by kind
    return resolved


async def _oauth_request_headers(
    database: Database,
    db: AsyncSession,
    vault: Vault,
    client: httpx.AsyncClient,
    settings: Settings,
    row: Tool,
    definition: McpServerDefinition,
) -> dict[str, str] | McpTestResult:
    """The sign-in's access token as a bearer header (refreshed when needed, V5-16), or why not.

    The token is never substituted into the definition (``resolve_tool_definition`` is not
    used for oauth servers here).
    """
    assert isinstance(definition.auth, McpOAuthAuth)  # noqa: S101 - narrowed by the caller
    loaded = await load_sign_in(
        db, vault, workspace_id=row.workspace_id, credential_id=definition.auth.credential_id
    )
    if loaded is None or not binds_tool(loaded[1], tool_id=row.id, url=definition.url):
        return McpTestResult(ok=False, reason="needs_auth", error="sign in to this server first")
    try:
        token = await get_access_token(
            database,
            vault,
            client,
            settings,
            workspace_id=row.workspace_id,
            credential_id=loaded[0].id,
            tool_id=row.id,
        )
    except NeedsReauth:
        return McpTestResult(
            ok=False, reason="needs_auth", error="the sign-in needs to be renewed: sign in again"
        )
    except TokenUnavailable:
        return McpTestResult(
            ok=False, reason="unreachable", error="the sign-in provider could not refresh the token just now"
        )
    return {"Authorization": f"Bearer {token.access_token}"}


@router.post(
    "/{tool_id}/test",
    response_model=McpTestResult,
    summary="Test an MCP server",
    description=(
        "Connects to the MCP server with its stored auth, runs `initialize` and `tools/list`, "
        "stores the tool list on the definition (`cached_tools`, `cached_at`) and returns the "
        "names and the count. The url must pass the MCP host policy (the network guard, "
        "`https`, `LKAP_MCP_ALLOWED_HOSTS`); redirects are never followed. Headers and "
        "secrets are never echoed back."
    ),
)
async def test_mcp_tool(
    tool_id: str,
    request: Request,
    db: DbDep,
    vault: VaultDep,
    client: HttpClientDep,
    settings: SettingsDep,
    ctx: AdminCtxDep,
) -> McpTestResult:
    """Connect to an MCP server once and store its tool list."""
    row = await _load(db, ctx, tool_id)
    definition = _definition_of(row)
    if not isinstance(definition, McpServerDefinition):
        raise BadRequestError("the connection test is only available for MCP servers")
    _check_mcp_definition(definition, settings)
    if definition.origin is not None and not is_composio_url(definition.url):
        # D-V5-C10: a provisioned app server carries the Composio key; it only ever talks to Composio.
        raise UnprocessableEntityError(
            "an app server may only connect to its provider's https host",
            details={"field": "definition.url", "reason": "blocked_destination"},
        )
    url = definition.url
    if isinstance(definition.auth, McpOAuthAuth):
        database: Database = request.app.state.db
        headers_or_result = await _oauth_request_headers(
            database, db, vault, client, settings, row, definition
        )
        if isinstance(headers_or_result, McpTestResult):
            return headers_or_result
        headers = headers_or_result
    else:
        resolved = await _mcp_resolved(db, vault, row, definition)
        headers, url = dict(resolved.headers), resolved.url
        # S5-17: the connection goes to the url the worker would use, so it is the one checked.
        problem = net_guard.mcp_policy(settings).problem(url)
        if problem is not None:
            return McpTestResult(ok=False, reason="blocked_destination", error=problem)

    started = time.perf_counter()
    try:
        tools = await list_mcp_tools(
            url,
            headers,
            client=client,
            timeout_s=min(max(definition.timeout_s, 1.0), MCP_TEST_MAX_TIMEOUT_S),
        )
    except McpTestError as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        log.warning("tool_mcp_test_failed", tool_id=tool_id, reason=exc.reason, duration_ms=duration_ms)
        return McpTestResult(ok=False, duration_ms=duration_ms, reason=exc.reason, error=str(exc))
    duration_ms = int((time.perf_counter() - started) * 1000)

    cached_at = datetime.now(UTC)
    row.definition = definition.model_copy(update={"cached_tools": tools, "cached_at": cached_at}).model_dump(
        mode="json"
    )
    await db.flush()
    names = [tool.name for tool in tools]
    log.info("tool_mcp_test", tool_id=tool_id, tool_count=len(names), duration_ms=duration_ms)
    return McpTestResult(
        ok=True, tool_names=names, tool_count=len(names), cached_at=cached_at, duration_ms=duration_ms
    )
