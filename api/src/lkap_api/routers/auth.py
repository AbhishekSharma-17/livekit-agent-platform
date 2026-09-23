"""Login, logout, the signed-in user, password change and invite acceptance.

CONTRACTS-V2 §3.1. Every route answers with the CONTRACTS §7 error envelope;
failed logins get one generic message whether the email exists or not, and
cost the same argon2 work either way.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Request, Response, status
from lkap_contracts.api_models import Me, UserOut, WorkspaceMembership
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.auth import SESSION_COOKIE, invites
from lkap_api.auth.audit import record
from lkap_api.auth.deps import (
    BREAK_GLASS_ACTOR,
    Principal,
    PrincipalDep,
    client_ip,
    user_memberships,
)
from lkap_api.auth.models import AcceptInviteIn, LoginIn, PasswordChangeIn
from lkap_api.auth.passwords import (
    hash_password_async,
    needs_rehash,
    password_problem,
    verify_password_async,
)
from lkap_api.auth.ratelimit import RateLimiterDep, enforce
from lkap_api.auth.sessions import (
    clear_session_cookie,
    create_session,
    expire_session_now,
    revoke_other_sessions,
    revoke_session,
    set_session_cookie,
)
from lkap_api.db.models import User, Workspace, WorkspaceMember
from lkap_api.deps import DbDep, SettingsDep
from lkap_api.errors import BadRequestError, ForbiddenError, UnauthorizedError, UnprocessableEntityError
from lkap_api.logging import get_logger
from lkap_api.settings import Settings

log = get_logger(__name__)

router = APIRouter(prefix="/v1/auth", tags=["auth"])

_INVALID_LOGIN = "invalid email or password"


def _ttl(settings: Settings) -> dt.timedelta:
    return dt.timedelta(hours=settings.session_ttl_hours)


async def _start_session(
    db: AsyncSession, request: Request, response: Response, settings: Settings, user: User
) -> None:
    raw = await create_session(
        db,
        user.id,
        ttl=_ttl(settings),
        user_agent=request.headers.get("user-agent", ""),
        ip=client_ip(request),
    )
    set_session_cookie(response, raw, settings)


def _require_user(principal: Principal) -> User:
    if principal.kind != "user" or principal.user is None:
        raise ForbiddenError("this route needs a signed-in user, not an API key or the admin token")
    return principal.user


@router.post(
    "/login",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Sign in",
    description=(
        "Checks email + password and sets the `lkap_session` cookie (HttpOnly, SameSite=Lax, "
        "Secure outside dev). Rate limited per client address and per email."
    ),
)
async def login(
    payload: LoginIn,
    request: Request,
    response: Response,
    db: DbDep,
    settings: SettingsDep,
    limiter: RateLimiterDep,
) -> None:
    """Authenticate a user and issue a fresh session.

    Raises:
        UnauthorizedError: Wrong email or password, or a disabled account.
        RateLimitedError: Too many attempts from this address or for this email.
    """
    if settings.rate_limit_enabled:
        await enforce(
            limiter,
            f"login:ip:{client_ip(request)}",
            capacity=settings.login_rate_per_min,
            what="login attempts per minute",
        )
        await enforce(
            limiter,
            f"login:email:{payload.email}",
            capacity=settings.login_rate_per_min,
            what="login attempts per minute",
        )
    user = (await db.execute(select(User).where(User.email == payload.email))).scalar_one_or_none()
    ok = await verify_password_async(user.password_hash if user else None, payload.password)
    if user is None or not ok or user.disabled_at is not None:
        log.info("login_failed", reason="bad_credentials")
        raise UnauthorizedError(_INVALID_LOGIN)
    if user.password_hash and needs_rehash(user.password_hash):
        user.password_hash = await hash_password_async(payload.password)
    await _start_session(db, request, response, settings, user)
    record(
        db, workspace_id=None, actor_type="user", actor_id=user.id, action="auth.login", target_type="user"
    )
    log.info("login_succeeded", user_id=user.id)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Sign out",
    description="Deletes the current session and clears the cookie. Idempotent.",
)
async def logout(request: Request, response: Response, db: DbDep, settings: SettingsDep) -> None:
    """End the current cookie session, if any."""
    await revoke_session(db, request.cookies.get(SESSION_COOKIE))
    clear_session_cookie(response, settings)


def _break_glass_me(workspaces: list[Workspace]) -> Me:
    return Me(
        user=UserOut(
            id=BREAK_GLASS_ACTOR,
            email="break-glass@lkap.local",
            name="Break-glass admin",
            is_platform_admin=True,
        ),
        workspaces=[WorkspaceMembership(id=w.id, slug=w.slug, name=w.name, role="owner") for w in workspaces],
    )


@router.get(
    "/me",
    response_model=Me,
    summary="The signed-in user",
    description=(
        "The user and every workspace they belong to, with their role. With the break-glass "
        "admin token this returns a synthetic platform-admin user that owns every workspace, so "
        "the console keeps working while it still proxies with the token. API keys get 403."
    ),
)
async def me(principal: PrincipalDep, db: DbDep) -> Me:
    """Describe the caller.

    Raises:
        UnauthorizedError: Not signed in.
        ForbiddenError: Called with an API key.
    """
    if principal.kind == "admin":
        workspaces = (await db.execute(select(Workspace).order_by(Workspace.name))).scalars().all()
        return _break_glass_me(list(workspaces))
    user = _require_user(principal)
    memberships = await user_memberships(db, user.id)
    return Me(
        user=UserOut(
            id=user.id, email=user.email, name=user.name, is_platform_admin=bool(user.is_platform_admin)
        ),
        workspaces=[
            WorkspaceMembership(id=w.id, slug=w.slug, name=w.name, role=role) for w, role in memberships
        ],
    )


@router.post(
    "/password",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Change password",
    description=(
        "Verifies the current password, stores the new one, signs out every other session and "
        "rotates this one (a new cookie is set)."
    ),
)
async def change_password(
    payload: PasswordChangeIn,
    principal: PrincipalDep,
    request: Request,
    response: Response,
    db: DbDep,
    settings: SettingsDep,
    limiter: RateLimiterDep,
) -> None:
    """Change the signed-in user's password.

    Raises:
        ForbiddenError: Not a cookie session.
        BadRequestError: The current password is wrong.
        UnprocessableEntityError: The new password is too short or too long.
    """
    user = _require_user(principal)
    if settings.rate_limit_enabled:
        await enforce(
            limiter,
            f"password:user:{user.id}",
            capacity=settings.login_rate_per_min,
            what="password attempts per minute",
        )
    if not await verify_password_async(user.password_hash, payload.current):
        raise BadRequestError("current password is incorrect")
    if (problem := password_problem(payload.new)) is not None:
        raise UnprocessableEntityError(problem)
    user.password_hash = await hash_password_async(payload.new)
    current_session_id = principal.session.id if principal.session else None
    await revoke_other_sessions(db, user.id, keep_session_id=current_session_id)
    if current_session_id is not None:
        await expire_session_now(db, current_session_id)
    await _start_session(db, request, response, settings, user)
    record(
        db,
        workspace_id=None,
        actor_type="user",
        actor_id=user.id,
        action="auth.password_change",
        target_type="user",
        target_id=user.id,
    )


@router.post(
    "/accept-invite",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Accept a workspace invite",
    description=(
        "For a new account, sets its password (and optional name); for an existing account, "
        "`password` must be that account's password. Adds the membership and signs in. "
        "Each invite link works once."
    ),
)
async def accept_invite(
    payload: AcceptInviteIn,
    request: Request,
    response: Response,
    db: DbDep,
    settings: SettingsDep,
    limiter: RateLimiterDep,
) -> None:
    """Redeem an invite token.

    Raises:
        BadRequestError: The token is invalid, expired or already used.
        UnauthorizedError: The existing account's password is wrong.
        UnprocessableEntityError: The new password is too short or too long.
    """
    if settings.rate_limit_enabled:
        await enforce(
            limiter,
            f"invite:ip:{client_ip(request)}",
            capacity=settings.login_rate_per_min,
            what="invite attempts per minute",
        )
    try:
        claims = invites.verify(settings, payload.token)
    except invites.InvalidInvite as exc:
        raise BadRequestError(f"invalid invite: {exc}") from exc
    user = await db.get(User, claims.user_id)
    workspace = await db.get(Workspace, claims.workspace_id)
    if user is None or workspace is None or user.disabled_at is not None:
        raise BadRequestError("invalid invite: it no longer matches an account")
    member = await db.get(WorkspaceMember, (claims.workspace_id, claims.user_id))
    if invites.state_fingerprint(user.password_hash, member is not None) != claims.fingerprint:
        raise BadRequestError("invalid invite: it has already been used")

    if user.password_hash is None:
        if (problem := password_problem(payload.password)) is not None:
            raise UnprocessableEntityError(problem)
        user.password_hash = await hash_password_async(payload.password)
        if payload.name:
            user.name = payload.name
    elif not await verify_password_async(user.password_hash, payload.password):
        raise UnauthorizedError(_INVALID_LOGIN)

    db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role=claims.role))
    await db.flush()
    await _start_session(db, request, response, settings, user)
    record(
        db,
        workspace_id=workspace.id,
        actor_type="user",
        actor_id=user.id,
        action="member.join",
        target_type="member",
        target_id=user.id,
        payload={"role": claims.role},
    )
    log.info("invite_accepted", workspace_id=workspace.id, user_id=user.id, role=claims.role)
