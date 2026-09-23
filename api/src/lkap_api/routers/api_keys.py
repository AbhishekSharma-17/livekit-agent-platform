"""Workspace API keys: create (shown once), list, revoke (CONTRACTS-V2 §3.1, §3.4).

Managing keys needs the ``admin`` role (§3.2). An API key can manage keys only
if it carries the ``*`` scope. Keys of another workspace are a 404.

``GET /v1/api-keys/self`` (v3, D-V3-6) is the exception: any API key may read
its own id, scopes and workspace, whatever its scopes, so an MCP server can
shape its tool list to what the key allows (R-V3-13).
"""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Depends, Query, Response, status
from lkap_contracts.api_models import Page
from sqlalchemy import func, select

from lkap_api.auth.api_keys import generate_api_key
from lkap_api.auth.audit import record
from lkap_api.auth.deps import PrincipalDep, WorkspaceContext, require
from lkap_api.auth.models import (
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyKind,
    ApiKeyOut,
    ApiKeySelfOut,
    ApiKeySelfWorkspace,
)
from lkap_api.db.models import ApiKey, Workspace, new_id, utcnow
from lkap_api.deps import DbDep
from lkap_api.errors import NotFoundError, UnauthorizedError, UnprocessableEntityError
from lkap_api.logging import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/v1/api-keys", tags=["api-keys"])

KeyAdminDep = Annotated[WorkspaceContext, Depends(require("admin", "*"))]


def _to_out(row: ApiKey) -> ApiKeyOut:
    return ApiKeyOut(
        id=row.id,
        workspace_id=row.workspace_id,
        name=row.name,
        prefix=row.prefix,
        scopes=[str(scope) for scope in row.scopes],
        created_by=row.created_by,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        revoked_at=row.revoked_at,
        expires_at=row.expires_at,
        kind=cast(ApiKeyKind, row.kind),  # CHECK constraint guarantees the value
        client=row.client,
        last_client=row.last_client,
    )


@router.get(
    "",
    response_model=Page[ApiKeyOut],
    summary="List API keys",
    description="Every key of the current workspace, newest first, including revoked ones. Needs `admin`.",
)
async def list_api_keys(
    ctx: KeyAdminDep,
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[ApiKeyOut]:
    """List the workspace's API keys (never their secrets)."""
    where = ApiKey.workspace_id == ctx.workspace_id
    rows = (
        await db.execute(
            select(ApiKey)
            .where(where)
            .order_by(ApiKey.created_at.desc(), ApiKey.id)
            .limit(limit)
            .offset(offset)
        )
    ).scalars()
    total = (await db.execute(select(func.count()).select_from(ApiKey).where(where))).scalar_one()
    return Page[ApiKeyOut](items=[_to_out(row) for row in rows], total=int(total))


@router.post(
    "",
    response_model=ApiKeyCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Create an API key",
    description=(
        "Returns the raw key (`lkap_…`) exactly once; only its sha256 is stored. Scopes: "
        "`agents:read|write`, `sessions:read|write`, `calls:write`, `connections:read|write`, "
        "`providers:read|write`, `webhooks:write`, `audit:read` or `*`. `kind=agent` with a "
        "`client` marks a key minted for an AI coding agent. Needs `admin`."
    ),
)
async def create_api_key(payload: ApiKeyCreate, ctx: KeyAdminDep, db: DbDep) -> ApiKeyCreated:
    """Create a key in the current workspace.

    Raises:
        UnprocessableEntityError: ``expires_at`` is in the past.
    """
    now = utcnow()
    if payload.expires_at is not None and payload.expires_at <= now:
        raise UnprocessableEntityError("expires_at must be in the future")
    raw, prefix, key_hash = generate_api_key()
    row = ApiKey(
        id=new_id(),
        workspace_id=ctx.workspace_id,
        name=payload.name,
        prefix=prefix,
        key_hash=key_hash,
        scopes=sorted(set(payload.scopes)),
        created_by=ctx.actor.id,
        created_at=now,
        expires_at=payload.expires_at,
        kind=payload.kind,
        client=payload.client,
    )
    db.add(row)
    await db.flush()
    record(
        db,
        workspace_id=ctx.workspace_id,
        actor_type=ctx.actor.actor_type,
        actor_id=ctx.actor.id,
        action="api_key.create",
        target_type="api_key",
        target_id=row.id,
        payload={
            "name": row.name,
            "scopes": row.scopes,
            "prefix": prefix,
            "kind": row.kind,
            "key_client": row.client,
        },
    )
    log.info("api_key_created", workspace_id=ctx.workspace_id, api_key_id=row.id, prefix=prefix)
    return ApiKeyCreated(**_to_out(row).model_dump(), key=raw)


@router.get(
    "/self",
    response_model=ApiKeySelfOut,
    summary="The calling API key",
    description=(
        "The key presented as `Authorization: Bearer lkap_…`: its id, name, prefix, kind, client, "
        "scopes, expiry and workspace. Any API key may call it, whatever its scopes; a signed-in "
        "user or the admin token gets 401 (they are not keys). Never returns secret material."
    ),
)
async def get_own_api_key(principal: PrincipalDep, db: DbDep) -> ApiKeySelfOut:
    """Describe the API key that authenticated this request.

    Raises:
        UnauthorizedError: The caller is not an API key, or its workspace is gone.
    """
    key = principal.api_key
    if principal.kind != "api_key" or key is None:
        raise UnauthorizedError("this route describes an API key: send 'Authorization: Bearer lkap_…'")
    workspace = await db.get(Workspace, key.workspace_id)
    if workspace is None:
        raise UnauthorizedError("the workspace of this API key no longer exists")
    return ApiKeySelfOut(
        id=key.id,
        name=key.name,
        prefix=key.prefix,
        kind=cast(ApiKeyKind, key.kind),
        client=key.client,
        scopes=[str(scope) for scope in key.scopes],
        expires_at=key.expires_at,
        workspace=ApiKeySelfWorkspace(id=workspace.id, slug=workspace.slug, name=workspace.name),
    )


@router.delete(
    "/{api_key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Revoke an API key",
    description="Revokes the key immediately (idempotent); the row is kept for the audit trail.",
)
async def revoke_api_key(api_key_id: str, ctx: KeyAdminDep, db: DbDep) -> None:
    """Revoke a key of the current workspace.

    Raises:
        NotFoundError: No such key in this workspace.
    """
    row = (
        await db.execute(
            select(ApiKey).where(ApiKey.id == api_key_id, ApiKey.workspace_id == ctx.workspace_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"api key '{api_key_id}' not found")
    if row.revoked_at is None:
        row.revoked_at = utcnow()
        record(
            db,
            workspace_id=ctx.workspace_id,
            actor_type=ctx.actor.actor_type,
            actor_id=ctx.actor.id,
            action="api_key.revoke",
            target_type="api_key",
            target_id=row.id,
            payload={"prefix": row.prefix},
        )
        log.info("api_key_revoked", workspace_id=ctx.workspace_id, api_key_id=row.id)
