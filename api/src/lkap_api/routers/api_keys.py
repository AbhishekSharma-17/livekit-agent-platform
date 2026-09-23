"""Workspace API keys: create (shown once), list, revoke (CONTRACTS-V2 §3.1, §3.4).

Managing keys needs the ``admin`` role (§3.2). An API key can manage keys only
if it carries the ``*`` scope. Keys of another workspace are a 404.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from lkap_contracts.api_models import Page
from sqlalchemy import func, select

from lkap_api.auth.api_keys import generate_api_key
from lkap_api.auth.audit import record
from lkap_api.auth.deps import WorkspaceContext, require
from lkap_api.auth.models import ApiKeyCreate, ApiKeyCreated, ApiKeyOut
from lkap_api.db.models import ApiKey, new_id, utcnow
from lkap_api.deps import DbDep
from lkap_api.errors import NotFoundError, UnprocessableEntityError
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
        "`providers:read|write`, `webhooks:write` or `*`. Needs `admin`."
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
        payload={"name": row.name, "scopes": row.scopes, "prefix": prefix},
    )
    log.info("api_key_created", workspace_id=ctx.workspace_id, api_key_id=row.id, prefix=prefix)
    return ApiKeyCreated(**_to_out(row).model_dump(), key=raw)


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
