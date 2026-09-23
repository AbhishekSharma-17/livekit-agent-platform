"""Principal resolution and the ``WorkspaceContext`` dependency (CONTRACTS-V2 §3.1).

A request is authenticated by the first of these that is present:

1. the ``lkap_session`` cookie (a user; refreshed and rotated when due);
2. ``Authorization: Bearer lkap_…`` (an API key, bound to one workspace);
3. the break-glass admin token — ``X-Admin-Token`` or a non-``lkap_`` bearer
   value — only when ``LKAP_ALLOW_ADMIN_TOKEN`` is on (default: ``dev`` only).
   It acts as ``owner`` of every workspace.

A stale or unknown cookie is ignored (the next credential is tried); an unknown,
revoked or expired API key is a 401, because a key is never sent by accident.

The workspace comes from ``X-Workspace: <slug|id>`` or ``?workspace=``. Without
one, a user gets their only workspace (400 if they have several); an API key
gets its own; the admin token gets the ``default`` workspace, which is what
keeps the v1 console working unchanged while it still proxies with the token.
A workspace the caller cannot see is a 404 — never a 403 — so ids do not leak.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Annotated, Literal

from fastapi import Depends, Header, Query, Request, Response
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.auth import (
    ADMIN_HEADER,
    SESSION_COOKIE,
    WORKSPACE_HEADER,
    WORKSPACE_QUERY,
    bearer_value,
    token_matches,
)
from lkap_api.auth.api_keys import looks_like_api_key, resolve_api_key
from lkap_api.auth.audit import MUTATING_METHODS, ActorType, record, route_target
from lkap_api.auth.ratelimit import RateLimiter, enforce, get_rate_limiter
from lkap_api.auth.roles import API_KEY_ROLE, Requirement, Role, policy_for, role_at_least, scope_allows
from lkap_api.auth.sessions import ResolvedSession, refresh_if_due, resolve_session, set_session_cookie
from lkap_api.db.constants import DEFAULT_WORKSPACE_SLUG
from lkap_api.db.models import ApiKey, User, UserSession, Workspace, WorkspaceMember
from lkap_api.db.session import get_db
from lkap_api.errors import BadRequestError, ForbiddenError, NotFoundError, UnauthorizedError
from lkap_api.logging import get_logger
from lkap_api.settings import Settings, get_settings

log = get_logger(__name__)

_Db = Annotated[AsyncSession, Depends(get_db, scope="function")]  # same scope as `deps.DbDep`
_Settings = Annotated[Settings, Depends(get_settings)]
_Limiter = Annotated[RateLimiter, Depends(get_rate_limiter)]

#: ``actor_id`` recorded for the break-glass admin token.
BREAK_GLASS_ACTOR = "break-glass"

PrincipalKind = Literal["user", "api_key", "admin"]


@dataclass(frozen=True)
class Principal:
    """Who is calling: a signed-in user, an API key or the break-glass token."""

    kind: PrincipalKind
    id: str
    user: User | None = None
    session: UserSession | None = None
    api_key: ApiKey | None = None
    scopes: tuple[str, ...] = field(default=())

    @property
    def actor_type(self) -> ActorType:
        """The ``audit_log.actor_type`` of this principal."""
        match self.kind:
            case "user":
                return "user"
            case "api_key":
                return "api_key"
            case _:
                return "system"


@dataclass(frozen=True)
class WorkspaceContext:
    """The workspace a request acts in, who acts, and with which role.

    Every admin route receives one; tenant queries filter on
    :attr:`workspace_id` and inserts set it.
    """

    workspace_id: str
    workspace_slug: str
    workspace_name: str
    actor: Principal
    role: Role

    def allows(self, requirement: Requirement) -> bool:
        """Return whether this caller meets ``requirement`` (role, and scope for keys)."""
        if self.actor.kind == "api_key" and not scope_allows(self.actor.scopes, requirement.scope):
            return False
        return role_at_least(self.role, requirement.role)

    def check(self, requirement: Requirement) -> None:
        """Raise :class:`ForbiddenError` unless :meth:`allows` holds."""
        if self.allows(requirement):
            return
        if self.actor.kind == "api_key" and not scope_allows(self.actor.scopes, requirement.scope):
            raise ForbiddenError(
                f"API key lacks the '{requirement.scope}' scope",
                details={"required_scope": requirement.scope},
            )
        raise ForbiddenError(
            f"requires the '{requirement.role}' role or higher in this workspace",
            details={"required_role": requirement.role, "role": self.role},
        )


def client_ip(request: Request) -> str:
    """The peer address of the request (proxy headers are not trusted)."""
    return request.client.host if request.client else ""


def cookie_allowed(request: Request, settings: Settings) -> bool:
    """Whether the ``lkap_session`` cookie may authenticate this request (CSRF, V2-21).

    ``SameSite=Lax`` keeps the cookie off cross-*site* POSTs, but a sibling
    subdomain or another port on the same host is the same *site*. So a
    state-changing request authenticates by cookie only when the browser does
    not call it cross-site (``Sec-Fetch-Site``) and its ``Origin``, when sent,
    is one of the platform's own web origins; a same-site sibling fails the
    Origin test. Requests without these headers (server-side fetches, curl)
    are unaffected; API keys and the service token never ride on a cookie.
    """
    if request.method.upper() not in MUTATING_METHODS:
        return True
    if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
        return False
    origin = request.headers.get("origin")
    if origin is None or origin == "null":
        return origin is None
    return origin.rstrip("/") in settings.web_origins


async def _principal_from_session(
    request: Request, response: Response, db: AsyncSession, settings: Settings
) -> Principal | None:
    raw_cookie = request.cookies.get(SESSION_COOKIE)
    if raw_cookie and not cookie_allowed(request, settings):
        log.info(
            "session_cookie_ignored_cross_origin",
            method=request.method,
            origin=request.headers.get("origin"),
            fetch_site=request.headers.get("sec-fetch-site"),
        )
        return None
    resolved: ResolvedSession | None = await resolve_session(db, raw_cookie)
    if resolved is None:
        return None
    rotated = await refresh_if_due(db, resolved, ttl=dt.timedelta(hours=settings.session_ttl_hours))
    if rotated is not None:
        set_session_cookie(response, rotated, settings)
    return Principal(kind="user", id=resolved.user.id, user=resolved.user, session=resolved.session)


async def resolve_principal(
    request: Request,
    response: Response,
    db: AsyncSession,
    settings: Settings,
    limiter: RateLimiter,
    *,
    x_admin_token: str | None,
    authorization: str | None,
) -> Principal | None:
    """Authenticate the request; ``None`` means anonymous.

    Raises:
        UnauthorizedError: For an unknown, revoked or expired API key.
        RateLimitedError: When an API key exceeds ``LKAP_API_KEY_RATE_PER_MIN``.
    """
    principal = await _principal_from_session(request, response, db, settings)
    if principal is not None:
        return principal

    bearer = bearer_value(authorization)
    if bearer is not None and looks_like_api_key(bearer):
        key = await resolve_api_key(db, bearer)
        if key is None:
            raise UnauthorizedError("invalid, revoked or expired API key")
        if settings.rate_limit_enabled:
            await enforce(
                limiter,
                f"api_key:{key.id}",
                capacity=settings.api_key_rate_per_min,
                what="api key requests per minute",
            )
        return Principal(kind="api_key", id=key.id, api_key=key, scopes=tuple(str(s) for s in key.scopes))

    presented = x_admin_token or bearer
    if presented and settings.admin_token_allowed and token_matches(presented, settings.admin_token):
        return Principal(kind="admin", id=BREAK_GLASS_ACTOR)
    return None


async def get_principal(
    request: Request,
    response: Response,
    db: _Db,
    settings: _Settings,
    limiter: _Limiter,
    x_admin_token: Annotated[
        str | None, Header(alias=ADMIN_HEADER, description="Break-glass admin token")
    ] = None,
    authorization: Annotated[str | None, Header(alias="Authorization", include_in_schema=False)] = None,
) -> Principal | None:
    """FastAPI dependency: the caller, or ``None`` when anonymous (never 401s on absence)."""
    return await resolve_principal(
        request,
        response,
        db,
        settings,
        limiter,
        x_admin_token=x_admin_token,
        authorization=authorization,
    )


OptionalPrincipalDep = Annotated[Principal | None, Depends(get_principal)]


def require_principal(principal: OptionalPrincipalDep) -> Principal:
    """FastAPI dependency: the caller, or 401.

    Raises:
        UnauthorizedError: When no credential was presented.
    """
    if principal is None:
        raise UnauthorizedError(
            "authentication required: sign in, or send an API key as 'Authorization: Bearer lkap_…'"
        )
    return principal


PrincipalDep = Annotated[Principal, Depends(require_principal)]


def _matches(workspace: Workspace, selector: str) -> bool:
    return selector in (workspace.id, workspace.slug)


async def user_memberships(db: AsyncSession, user_id: str) -> list[tuple[Workspace, str]]:
    """Return ``(workspace, role)`` for every workspace a user belongs to, by name."""
    rows = (
        await db.execute(
            select(Workspace, WorkspaceMember.role)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
            .where(WorkspaceMember.user_id == user_id)
            .order_by(Workspace.name, Workspace.id)
        )
    ).tuples()
    return [(workspace, role) for workspace, role in rows]


async def _workspace_by_selector(db: AsyncSession, selector: str) -> Workspace | None:
    return (
        await db.execute(select(Workspace).where(or_(Workspace.id == selector, Workspace.slug == selector)))
    ).scalar_one_or_none()


def _not_found(selector: str) -> NotFoundError:
    return NotFoundError(f"workspace '{selector}' not found")


def _context(workspace: Workspace, principal: Principal, role: str) -> WorkspaceContext:
    return WorkspaceContext(
        workspace_id=workspace.id,
        workspace_slug=workspace.slug,
        workspace_name=workspace.name,
        actor=principal,
        role=role,  # type: ignore[arg-type]  # CHECK constraint guarantees a Role
    )


async def resolve_workspace(db: AsyncSession, principal: Principal, selector: str | None) -> WorkspaceContext:
    """Resolve the workspace a principal acts in (see the module docstring for the rules).

    Args:
        db: The request session.
        principal: The authenticated caller.
        selector: A workspace slug or id, or ``None`` for the caller's default.

    Returns:
        The :class:`WorkspaceContext`.

    Raises:
        NotFoundError: The workspace does not exist or the caller is not in it.
        BadRequestError: A user in several workspaces sent no selector.
        ForbiddenError: A user with no workspace at all.
    """
    selector = selector.strip() if selector else None
    match principal.kind:
        case "user":
            assert principal.user is not None
            memberships = await user_memberships(db, principal.user.id)
            if selector:
                for workspace, role in memberships:
                    if _matches(workspace, selector):
                        return _context(workspace, principal, role)
                raise _not_found(selector)
            if len(memberships) == 1:
                workspace, role = memberships[0]
                return _context(workspace, principal, role)
            if not memberships:
                raise ForbiddenError("this user is not a member of any workspace")
            raise BadRequestError(
                f"you belong to several workspaces; send '{WORKSPACE_HEADER}: <slug>'",
                details={"workspaces": [w.slug for w, _ in memberships]},
            )
        case "api_key":
            assert principal.api_key is not None
            own = await db.get(Workspace, principal.api_key.workspace_id)
            if own is None:
                raise UnauthorizedError("the workspace of this API key no longer exists")
            if selector and not _matches(own, selector):
                raise _not_found(selector)
            return _context(own, principal, API_KEY_ROLE)
        case _:
            found = await _workspace_by_selector(db, selector or DEFAULT_WORKSPACE_SLUG)
            if found is None and not selector:
                only = (await db.execute(select(Workspace).limit(2))).scalars().all()
                found = only[0] if len(only) == 1 else None
                if found is None:
                    raise BadRequestError(f"send '{WORKSPACE_HEADER}: <slug>' to pick a workspace")
            if found is None:
                raise _not_found(selector or DEFAULT_WORKSPACE_SLUG)
            return _context(found, principal, "owner")


async def get_workspace_context(
    db: _Db,
    principal: PrincipalDep,
    x_workspace: Annotated[
        str | None, Header(alias=WORKSPACE_HEADER, description="Workspace slug or id")
    ] = None,
    workspace: Annotated[str | None, Query(alias=WORKSPACE_QUERY, include_in_schema=False)] = None,
) -> WorkspaceContext:
    """FastAPI dependency: the :class:`WorkspaceContext` of an authenticated request."""
    return await resolve_workspace(db, principal, x_workspace or workspace)


WorkspaceCtxDep = Annotated[WorkspaceContext, Depends(get_workspace_context)]


def require(role: Role, scope: str) -> Callable[..., Awaitable[WorkspaceContext]]:
    """Build a dependency that yields the context only if the caller meets ``role``/``scope``.

    Usage: ``ctx: Annotated[WorkspaceContext, Depends(require("admin", "connections:write"))]``.

    Args:
        role: Minimum member role (API keys act as ``admin``).
        scope: API-key scope the route needs.

    Returns:
        The dependency callable.
    """
    requirement = Requirement(role, scope)

    async def dependency(ctx: WorkspaceCtxDep) -> WorkspaceContext:
        ctx.check(requirement)
        return ctx

    dependency.__name__ = f"require_{role}_{scope.replace(':', '_').replace('*', 'all')}"
    return dependency


async def route_policy_context(request: Request, ctx: WorkspaceCtxDep) -> WorkspaceContext:
    """The context of a v1 admin route, checked against :data:`~lkap_api.auth.roles.ROUTE_POLICY`."""
    ctx.check(policy_for(request.method, _route_path(request)))
    return ctx


def _route_path(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else request.url.path


def record_route_mutation(request: Request, db: AsyncSession, ctx: WorkspaceContext) -> None:
    """Record a generic audit row for a successful mutating v1 admin request.

    Called by the v1 ``AdminDep`` guard after the handler returned; the v2
    routers of this package record richer, action-specific rows themselves.
    The row joins the request's transaction, so it commits exactly when the
    change does, and a failed request (whose exception unwinds the guard)
    records nothing.
    """
    if request.method.upper() not in MUTATING_METHODS:
        return
    route_path = _route_path(request)
    target_type, target_id = route_target(route_path, dict(request.path_params))
    record(
        db,
        workspace_id=ctx.workspace_id,
        actor_type=ctx.actor.actor_type,
        actor_id=ctx.actor.id,
        action=f"{request.method.upper()} {route_path}",
        target_type=target_type,
        target_id=target_id,
    )


async def optional_member_context(
    db: _Db,
    principal: OptionalPrincipalDep,
    x_workspace: Annotated[str | None, Header(alias=WORKSPACE_HEADER, include_in_schema=False)] = None,
) -> WorkspaceContext | None:
    """The caller's context if they are signed in and resolvable, else ``None`` (never raises)."""
    if principal is None:
        return None
    try:
        return await resolve_workspace(db, principal, x_workspace)
    except (NotFoundError, BadRequestError, ForbiddenError, UnauthorizedError):
        return None


OptionalWorkspaceCtxDep = Annotated[WorkspaceContext | None, Depends(optional_member_context)]


async def member_role_in(db: AsyncSession, principal: Principal, workspace_id: str) -> Role | None:
    """Return the principal's effective role in a specific workspace, or ``None``.

    Used where the workspace comes from the resource (``connect`` loads the agent
    first) rather than from ``X-Workspace``.
    """
    match principal.kind:
        case "admin":
            return "owner"
        case "api_key":
            assert principal.api_key is not None
            return API_KEY_ROLE if principal.api_key.workspace_id == workspace_id else None
        case _:
            assert principal.user is not None
            role = (
                await db.execute(
                    select(WorkspaceMember.role).where(
                        WorkspaceMember.workspace_id == workspace_id,
                        WorkspaceMember.user_id == principal.user.id,
                    )
                )
            ).scalar_one_or_none()
            return role  # type: ignore[return-value]  # CHECK constraint guarantees a Role
