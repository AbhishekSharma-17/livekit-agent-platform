"""Declarative tool CRUD plus the HTTP dry run used by the console editor.

Workspace scoping (V2-02, asks #25): every admin handler takes ``ctx: AdminCtxDep``;
reads filter on ``ctx.workspace_id``, inserts set it, and a row of another
workspace is a 404."""

from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx
from fastapi import APIRouter, Query, Response, status
from lkap_contracts.api_models import ToolCreate, ToolDryRunRequest, ToolDryRunResult, ToolOut, ToolPage
from lkap_contracts.tools import HttpToolDefinition, ToolDefinition
from pydantic import TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.auth.deps import WorkspaceContext
from lkap_api.config_service import host_allowed, render_arguments, resolve_tool_definition
from lkap_api.db.models import Agent, Credential, Tool, utcnow
from lkap_api.deps import AdminCtxDep, DbDep, HttpClientDep, VaultDep
from lkap_api.errors import BadRequestError, NotFoundError, UnprocessableEntityError
from lkap_api.logging import get_logger
from lkap_api.vault import Vault

log = get_logger(__name__)

router = APIRouter(prefix="/v1/tools", tags=["tools"])

_TOOL_ADAPTER: TypeAdapter[ToolDefinition] = TypeAdapter(ToolDefinition)

#: Dry-run responses are truncated to keep the console payload small.
DRY_RUN_MAX_CHARS = 8000

#: Matches `{{ secret.NAME }}` placeholders (F-15); mirrors
#: `lkap_api.config_service._SECRET_RE`, kept local so this file's one
#: F-15 change never has to touch `config_service.py` (owned by V2-03).
_SECRET_RE = re.compile(r"\{\{\s*secret\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def _referenced_secret_names(definition: ToolDefinition) -> set[str]:
    """Every `{{ secret.NAME }}` name the tool's url/headers/body reference."""
    texts: list[str] = [definition.url, *definition.headers.values()]
    if isinstance(definition, HttpToolDefinition) and definition.body_template:
        texts.append(definition.body_template)
    names: set[str] = set()
    for text in texts:
        names.update(_SECRET_RE.findall(text))
    return names


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


async def _workspace_credential(db: AsyncSession, workspace_id: str, credential_id: str) -> Credential | None:
    """A credential of the given workspace, or ``None``."""
    stmt = select(Credential).where(Credential.id == credential_id, Credential.workspace_id == workspace_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def _check_payload(db: AsyncSession, vault: Vault, ctx: WorkspaceContext, payload: ToolCreate) -> None:
    """Validate cross-references, kind/definition agreement and secret placeholders.

    F-15: every `{{ secret.NAME }}` the definition's url/headers/body
    reference must resolve — either the tool has no `credential_id` at all
    (error: nothing to substitute from) or `NAME` is a key of that
    credential's decrypted secret bag (error otherwise, naming only the
    unresolved names, never a value).
    """
    if payload.definition.kind != payload.kind:
        raise UnprocessableEntityError(
            f"kind '{payload.kind}' does not match definition kind '{payload.definition.kind}'"
        )
    if (
        payload.agent_id is not None
        and await db.scalar(
            select(Agent.id).where(Agent.id == payload.agent_id, Agent.workspace_id == ctx.workspace_id)
        )
        is None
    ):
        raise UnprocessableEntityError(f"unknown agent '{payload.agent_id}'")
    credential_id = payload.definition.credential_id
    credential: Credential | None = None
    if credential_id is not None:
        credential = await _workspace_credential(db, ctx.workspace_id, credential_id)
        if credential is None:
            raise UnprocessableEntityError(f"unknown credential '{credential_id}'")
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


@router.post(
    "",
    response_model=ToolOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a tool",
    description="Stores an HTTP tool or MCP server definition, shared or owned by one agent.",
)
async def create_tool(payload: ToolCreate, db: DbDep, vault: VaultDep, ctx: AdminCtxDep) -> ToolOut:
    """Create a declarative tool row in the caller's workspace."""
    await _check_payload(db, vault, ctx, payload)
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
    kind: str | None = Query(default=None, description="http | mcp"),
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
    tool_id: str, payload: ToolCreate, db: DbDep, vault: VaultDep, ctx: AdminCtxDep
) -> ToolOut:
    """Replace a tool definition."""
    row = await _load(db, ctx, tool_id)
    await _check_payload(db, vault, ctx, payload)
    row.agent_id = payload.agent_id
    row.kind = payload.kind
    row.name = payload.name
    row.definition = payload.definition.model_dump(mode="json")
    row.enabled = payload.enabled
    row.updated_at = utcnow()
    await db.flush()
    log.info("tool_updated", tool_id=row.id, kind=row.kind)
    return _to_out(row)


@router.delete(
    "/{tool_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a tool",
    description="Removes the tool row; agents referencing it fail validation until updated.",
)
async def delete_tool(tool_id: str, db: DbDep, ctx: AdminCtxDep) -> Response:
    """Delete a tool row."""
    row = await _load(db, ctx, tool_id)
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
    ctx: AdminCtxDep,
) -> ToolDryRunResult:
    """Run an HTTP tool once and report what the model would have seen."""
    row = await _load(db, ctx, tool_id)
    definition = await _resolved_http_definition(db, vault, row)

    url = render_arguments(definition.url, payload.arguments, url_encode=True)
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

    started = time.perf_counter()
    try:
        response = await client.request(
            definition.method,
            url,
            headers=definition.headers or None,
            json=body,
            timeout=definition.timeout_s,
        )
    except httpx.HTTPError as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        log.warning("tool_dry_run_failed", tool_id=tool_id, error_type=type(exc).__name__)
        return ToolDryRunResult(
            ok=False,
            result=f"request failed: {type(exc).__name__}",
            status_code=None,
            duration_ms=duration_ms,
        )
    duration_ms = int((time.perf_counter() - started) * 1000)

    text = response.text
    if definition.result_path:
        try:
            extracted = extract_pointer(response.json(), definition.result_path)
        except ValueError:
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
