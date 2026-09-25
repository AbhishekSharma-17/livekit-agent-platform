"""Workspaces, members, invites and the audit log (CONTRACTS-V2 §3.2, §3.4).

The router's prefix is ``/v1`` (not ``/v1/workspaces``) because it also serves
``GET /v1/audit``; ``main.py`` only includes :data:`router`.

Routes under ``/v1/workspaces/{workspace_id}`` take the workspace from the path
(slug or id) instead of ``X-Workspace``; a workspace the caller does not belong
to is a 404. Role rules (§3.2): members are listed to everyone in the
workspace; adding, re-roling, removing and inviting need ``admin``; anything
that grants or removes ``owner`` needs ``owner``; the last owner cannot be
demoted or removed. API keys can reach these routes only with the ``*`` scope.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Response, status
from lkap_contracts.api_models import Page
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.auth import invites
from lkap_api.auth.audit import record
from lkap_api.auth.deps import (
    PrincipalDep,
    WorkspaceContext,
    require,
    resolve_workspace,
    user_memberships,
)
from lkap_api.auth.models import (
    AuditOut,
    InviteCreate,
    InviteOut,
    MemberCreate,
    MemberOut,
    MemberUpdate,
    WorkspaceOut,
    WorkspaceUpdate,
)
from lkap_api.auth.roles import Requirement
from lkap_api.costs.vendors import RECONCILE_KEY, ReconcileSettingError, validate_reconcile
from lkap_api.db.models import AuditLog, User, Workspace, WorkspaceMember, new_id
from lkap_api.deps import DbDep, SettingsDep
from lkap_api.errors import ConflictError, ForbiddenError, NotFoundError, UnprocessableEntityError
from lkap_api.logging import get_logger
from lkap_api.settings import Settings

log = get_logger(__name__)

router = APIRouter(prefix="/v1", tags=["workspaces"])

_READ = Requirement("viewer", "*")
_MANAGE = Requirement("admin", "*")
_OWNER = Requirement("owner", "*")

LimitQuery = Annotated[int, Query(ge=1, le=200)]
OffsetQuery = Annotated[int, Query(ge=0)]


async def path_workspace(
    db: DbDep,
    principal: PrincipalDep,
    workspace_id: Annotated[str, Path(description="Workspace id or slug")],
) -> WorkspaceContext:
    """The caller's context in the workspace named by the path (404 when not a member)."""
    return await resolve_workspace(db, principal, workspace_id)


PathWorkspaceDep = Annotated[WorkspaceContext, Depends(path_workspace)]
#: v3 (D-V3-9): ``audit:read`` lets an agent key read its own activity without ``*``.
AuditReaderDep = Annotated[WorkspaceContext, Depends(require("admin", "audit:read"))]


def _workspace_out(workspace: Workspace, role: str) -> WorkspaceOut:
    return WorkspaceOut(
        id=workspace.id,
        slug=workspace.slug,
        name=workspace.name,
        settings=dict(workspace.settings or {}),
        role=role,
        created_at=workspace.created_at,
        updated_at=workspace.updated_at,
    )


def _member_out(member: WorkspaceMember, user: User) -> MemberOut:
    return MemberOut(
        user_id=user.id,
        email=user.email,
        name=user.name,
        role=member.role,
        created_at=member.created_at,
        disabled=user.disabled_at is not None,
        pending=user.password_hash is None,
    )


def _audit(db: AsyncSession, ctx: WorkspaceContext, action: str, target_id: str, **payload: Any) -> None:
    record(
        db,
        workspace_id=ctx.workspace_id,
        actor_type=ctx.actor.actor_type,
        actor_id=ctx.actor.id,
        action=action,
        target_type="member" if action.startswith("member.") else "workspace",
        target_id=target_id,
        payload=payload,
    )


#: Audit actions that prove an account was a member of the workspace (R-V2-30).
_MEMBERSHIP_EVIDENCE = ("member.join", "member.add", "member.remove")


async def _was_member(db: AsyncSession, workspace_id: str, user_id: str) -> bool:
    """Whether the account ever belonged to the workspace (its membership audit rows)."""
    found = await db.scalar(
        select(AuditLog.id)
        .where(
            AuditLog.workspace_id == workspace_id,
            AuditLog.target_id == user_id,
            AuditLog.action.in_(_MEMBERSHIP_EVIDENCE),
        )
        .limit(1)
    )
    return found is not None


# ------------------------------------------------------------------ workspaces
@router.get(
    "/workspaces",
    response_model=Page[WorkspaceOut],
    summary="List workspaces",
    description=(
        "The workspaces the caller can act in: a user's memberships, an API key's own workspace, "
        "or every workspace for the break-glass admin token."
    ),
)
async def list_workspaces(principal: PrincipalDep, db: DbDep) -> Page[WorkspaceOut]:
    """List the caller's workspaces with their role in each."""
    items: list[WorkspaceOut]
    match principal.kind:
        case "user":
            assert principal.user is not None
            items = [_workspace_out(w, role) for w, role in await user_memberships(db, principal.user.id)]
        case "api_key":
            ctx = await resolve_workspace(db, principal, None)
            workspace = await db.get(Workspace, ctx.workspace_id)
            items = [_workspace_out(workspace, ctx.role)] if workspace else []
        case _:
            rows = (await db.execute(select(Workspace).order_by(Workspace.name))).scalars().all()
            items = [_workspace_out(w, "owner") for w in rows]
    return Page[WorkspaceOut](items=items, total=len(items))


def _merge_settings(current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Merge top-level keys, except ``cost``, whose own keys are merged one level down.

    V4-17 (D-V4-45): the reconciliation opt-in (``cost.reconcile``) is written with this
    route, and a shallow merge would drop the workspace prices stored beside it
    (``cost.prices``, written by ``PUT /v1/workspace/prices``). ``cost.reconcile`` is
    validated here.

    Raises:
        UnprocessableEntityError: ``cost`` is not an object, or ``cost.reconcile`` names
            an unknown vendor or one whose client is not built yet.
    """
    merged = {**current, **incoming}
    if "cost" in incoming:
        cost_in = incoming["cost"]
        if not isinstance(cost_in, dict):
            raise UnprocessableEntityError("settings.cost must be an object")
        cost = dict(current.get("cost") or {}) if isinstance(current.get("cost"), dict) else {}
        cost.update(cost_in)
        if RECONCILE_KEY in cost_in:
            try:
                cost[RECONCILE_KEY] = validate_reconcile(cost_in[RECONCILE_KEY])
            except ReconcileSettingError as exc:
                raise UnprocessableEntityError(str(exc)) from exc
        merged["cost"] = cost
    return merged


@router.put(
    "/workspaces/{workspace_id}",
    response_model=WorkspaceOut,
    summary="Update a workspace",
    description="Rename a workspace or merge keys into its `settings`. Needs `admin`.",
)
async def update_workspace(payload: WorkspaceUpdate, ctx: PathWorkspaceDep, db: DbDep) -> WorkspaceOut:
    """Rename or reconfigure the workspace."""
    ctx.check(_MANAGE)
    workspace = await db.get(Workspace, ctx.workspace_id)
    if workspace is None:
        raise NotFoundError("workspace not found")
    changed: list[str] = []
    if payload.name is not None:
        workspace.name = payload.name
        changed.append("name")
    if payload.settings is not None:
        workspace.settings = _merge_settings(workspace.settings or {}, payload.settings)
        changed.append("settings")
    await db.flush()
    _audit(db, ctx, "workspace.update", workspace.id, fields=changed)
    return _workspace_out(workspace, ctx.role)


# --------------------------------------------------------------------- members
async def _load_member(db: AsyncSession, workspace_id: str, user_id: str) -> tuple[WorkspaceMember, User]:
    row = (
        await db.execute(
            select(WorkspaceMember, User)
            .join(User, User.id == WorkspaceMember.user_id)
            .where(WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == user_id)
        )
    ).one_or_none()
    if row is None:
        raise NotFoundError(f"member '{user_id}' not found")
    member, user = row._tuple()
    return member, user


async def _owner_count(db: AsyncSession, workspace_id: str) -> int:
    return int(
        (
            await db.execute(
                select(func.count())
                .select_from(WorkspaceMember)
                .where(WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.role == "owner")
            )
        ).scalar_one()
    )


def _check_owner_change(ctx: WorkspaceContext, *roles: str) -> None:
    """Granting or taking away ``owner`` needs ``owner``."""
    if "owner" in roles:
        ctx.check(_OWNER)


@router.get(
    "/workspaces/{workspace_id}/members",
    response_model=Page[MemberOut],
    summary="List members",
    description="Every member of the workspace with their role; `pending` marks invited users.",
)
async def list_members(
    ctx: PathWorkspaceDep, db: DbDep, limit: LimitQuery = 25, offset: OffsetQuery = 0
) -> Page[MemberOut]:
    """List the workspace's members."""
    ctx.check(_READ)
    base = select(WorkspaceMember, User).join(User, User.id == WorkspaceMember.user_id)
    base = base.where(WorkspaceMember.workspace_id == ctx.workspace_id)
    rows = (await db.execute(base.order_by(User.email).limit(limit).offset(offset))).tuples().all()
    total = (
        await db.execute(
            select(func.count())
            .select_from(WorkspaceMember)
            .where(WorkspaceMember.workspace_id == ctx.workspace_id)
        )
    ).scalar_one()
    return Page[MemberOut](items=[_member_out(m, u) for m, u in rows], total=int(total))


@router.post(
    "/workspaces/{workspace_id}/members",
    response_model=MemberOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a member",
    description=(
        "Re-adds a former member of this workspace by email. Any other account is invited "
        "instead: 409 `use_invite` (the invite is accepted with the account's own password), "
        "404 for an unknown email. Needs `admin`."
    ),
)
async def add_member(payload: MemberCreate, ctx: PathWorkspaceDep, db: DbDep) -> MemberOut:
    """Re-add a former member of the workspace (ruling R-V2-30).

    Accounts are global and nothing proves who owns an email, so adding an
    existing account in one step would let another workspace's admin who
    pre-registered the address hold a membership here (REVIEW-V2 R2-19). Only
    an account with a ``member.join`` / ``member.add`` / ``member.remove``
    audit row in **this** workspace is re-added; every other one needs an
    invite, which the user accepts with their own password.

    Raises:
        NotFoundError: No account has this email.
        ConflictError: Already a member, or ``use_invite`` for an account that
            was never a member here.
    """
    ctx.check(_MANAGE)
    _check_owner_change(ctx, payload.role)
    user = (await db.execute(select(User).where(User.email == payload.email))).scalar_one_or_none()
    if user is None:
        raise NotFoundError(f"no user with email '{payload.email}'; send an invite instead")
    if await db.get(WorkspaceMember, (ctx.workspace_id, user.id)) is not None:
        raise ConflictError(f"'{payload.email}' is already a member")
    if not await _was_member(db, ctx.workspace_id, user.id):
        raise ConflictError(
            f"'{payload.email}' has never been a member of this workspace; send an invite, which "
            "the user accepts with their own password",
            details={"reason": "use_invite"},
        )
    member = WorkspaceMember(workspace_id=ctx.workspace_id, user_id=user.id, role=payload.role)
    db.add(member)
    await db.flush()
    _audit(db, ctx, "member.add", user.id, role=payload.role)
    return _member_out(member, user)


@router.put(
    "/workspaces/{workspace_id}/members/{user_id}",
    response_model=MemberOut,
    summary="Change a member's role",
    description="Needs `admin`; granting or removing `owner` needs `owner`. The last owner stays.",
)
async def update_member(payload: MemberUpdate, user_id: str, ctx: PathWorkspaceDep, db: DbDep) -> MemberOut:
    """Change one member's role."""
    ctx.check(_MANAGE)
    member, user = await _load_member(db, ctx.workspace_id, user_id)
    _check_owner_change(ctx, member.role, payload.role)
    if member.role == "owner" and payload.role != "owner" and await _owner_count(db, ctx.workspace_id) <= 1:
        raise ConflictError("the workspace must keep at least one owner")
    previous = member.role
    member.role = payload.role
    await db.flush()
    _audit(db, ctx, "member.role_change", user.id, previous=previous, role=payload.role)
    return _member_out(member, user)


@router.delete(
    "/workspaces/{workspace_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Remove a member",
    description="Needs `admin` (`owner` to remove an owner). The last owner cannot be removed.",
)
async def remove_member(user_id: str, ctx: PathWorkspaceDep, db: DbDep) -> None:
    """Remove one member from the workspace."""
    ctx.check(_MANAGE)
    member, user = await _load_member(db, ctx.workspace_id, user_id)
    _check_owner_change(ctx, member.role)
    if member.role == "owner" and await _owner_count(db, ctx.workspace_id) <= 1:
        raise ConflictError("the workspace must keep at least one owner")
    await db.delete(member)
    await db.flush()
    _audit(db, ctx, "member.remove", user.id, role=member.role)


# --------------------------------------------------------------------- invites
def _invite_url(settings: Settings, token: str) -> str:
    base = (settings.web_base_url or (settings.web_origins[0] if settings.web_origins else "")).rstrip("/")
    return f"{base}/login?invite={token}"


@router.post(
    "/workspaces/{workspace_id}/invites",
    response_model=InviteOut,
    status_code=status.HTTP_201_CREATED,
    summary="Invite someone",
    description=(
        "Creates a one-time invite link valid for 7 days (`{LKAP_WEB_BASE_URL}/login?invite=<token>`). "
        "The invitee accepts it with `POST /v1/auth/accept-invite`. Needs `admin` (`owner` to "
        "invite an owner)."
    ),
)
async def create_invite(
    payload: InviteCreate, ctx: PathWorkspaceDep, db: DbDep, settings: SettingsDep
) -> InviteOut:
    """Issue an invite link, creating the (password-less) user if the email is new."""
    ctx.check(_MANAGE)
    _check_owner_change(ctx, payload.role)
    user = (await db.execute(select(User).where(User.email == payload.email))).scalar_one_or_none()
    if user is None:
        user = User(id=new_id(), email=payload.email, name=payload.name, password_hash=None)
        db.add(user)
        await db.flush()
    elif user.disabled_at is not None:
        raise ForbiddenError(f"'{payload.email}' is disabled")
    elif user.password_hash is None and await invites.invited_elsewhere(db, ctx.workspace_id, user.id):
        # V2-21: the token of a pending account sets its password; another
        # workspace's pending invitee must not be claimable from here.
        raise ConflictError(
            f"'{payload.email}' has a pending invite from another workspace; they must accept it "
            "(and sign in) before this workspace can invite them",
            details={"reason": "pending_elsewhere"},
        )
    if await db.get(WorkspaceMember, (ctx.workspace_id, user.id)) is not None:
        raise ConflictError(f"'{payload.email}' is already a member")
    token, expires_at = invites.issue(
        settings,
        workspace_id=ctx.workspace_id,
        user_id=user.id,
        role=payload.role,
        fingerprint=invites.state_fingerprint(
            user.password_hash, False, await invites.join_count(db, ctx.workspace_id, user.id)
        ),
    )
    _audit(db, ctx, "member.invite", user.id, role=payload.role)
    log.info("invite_created", workspace_id=ctx.workspace_id, user_id=user.id, role=payload.role)
    return InviteOut(
        email=user.email,
        role=payload.role,
        workspace_id=ctx.workspace_id,
        token=token,
        url=_invite_url(settings, token),
        expires_at=expires_at,
    )


# ----------------------------------------------------------------------- audit
@router.get(
    "/audit",
    response_model=Page[AuditOut],
    summary="Audit log",
    description=(
        "Mutating admin calls in the current workspace, newest first. Rows written while an "
        "`X-LKAP-Client` header was sent carry `payload.client`. Needs `admin`; an API key "
        "needs the `audit:read` scope."
    ),
)
async def list_audit(
    ctx: AuditReaderDep, db: DbDep, limit: LimitQuery = 25, offset: OffsetQuery = 0
) -> Page[AuditOut]:
    """Page through the workspace's audit log."""
    where = AuditLog.workspace_id == ctx.workspace_id
    rows = (
        await db.execute(
            select(AuditLog)
            .where(where)
            .order_by(AuditLog.ts.desc(), AuditLog.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars()
    total = (await db.execute(select(func.count()).select_from(AuditLog).where(where))).scalar_one()
    return Page[AuditOut](
        items=[AuditOut.model_validate(row, from_attributes=True) for row in rows], total=int(total)
    )
