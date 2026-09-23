"""FastAPI dependencies: settings, database sessions, vault and auth guards.

Routers depend on the ``*Dep`` aliases declared here rather than importing the
concrete objects, so tests can override any of them with
``app.dependency_overrides``.

``AdminDep``/``OptionalAdminDep`` keep their v1 names so the v1 routers need no
edit, but since V2-02 they authenticate through :mod:`lkap_api.auth.deps`
(cookie session, API key or break-glass admin token) and enforce the role
matrix. New routers depend on :data:`lkap_api.auth.deps.WorkspaceCtxDep` or
:func:`lkap_api.auth.deps.require` directly.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Annotated

import httpx
from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api import net_guard
from lkap_api.auth import SERVICE_HEADER, token_matches
from lkap_api.auth.deps import (
    OptionalWorkspaceCtxDep,
    WorkspaceContext,
    record_route_mutation,
    route_policy_context,
)
from lkap_api.auth.roles import policy_for
from lkap_api.db.session import get_db
from lkap_api.errors import UnauthorizedError
from lkap_api.settings import Settings, get_settings
from lkap_api.vault import Vault

SettingsDep = Annotated[Settings, Depends(get_settings)]
#: ``scope="function"``: `get_db` commits in its exit code, which must run before the
#: response is sent, or a client can act on a ``2xx`` before the write is visible
#: (V2-20: a revoked API key kept authenticating on the next request).
DbDep = Annotated[AsyncSession, Depends(get_db, scope="function")]

ServiceTokenHeader = Annotated[str | None, Header(alias=SERVICE_HEADER, description="Worker service token")]


@lru_cache(maxsize=4)
def _vault_for(master_key: str) -> Vault:
    return Vault(master_key)


def get_vault(settings: SettingsDep) -> Vault:
    """Return the process-wide :class:`~lkap_api.vault.Vault`."""
    return _vault_for(settings.master_key)


VaultDep = Annotated[Vault, Depends(get_vault)]


async def get_http_client(settings: SettingsDep) -> AsyncIterator[httpx.AsyncClient]:
    """Yield an outbound HTTP client (credential tests, tool dry runs, webhook tests, QA re-score).

    Every connection goes through :mod:`lkap_api.net_guard` (V2-21): private,
    loopback and metadata addresses are refused at connect time, after DNS.
    Tests override this dependency with a client backed by
    ``httpx.MockTransport`` so the offline suite never touches the network.
    """
    async with net_guard.guarded_http_client(net_guard.policy_from_settings(settings)) as client:
        yield client


HttpClientDep = Annotated[httpx.AsyncClient, Depends(get_http_client)]


async def admin_context(
    request: Request,
    db: DbDep,
    ctx: Annotated[WorkspaceContext, Depends(route_policy_context)],
) -> AsyncIterator[WorkspaceContext]:
    """The :class:`WorkspaceContext` of a v1 admin route, role-checked and audited.

    The caller must be authenticated — cookie session, API key or break-glass
    admin token (:mod:`lkap_api.auth.deps`) — and meet the route's entry in
    :data:`lkap_api.auth.roles.ROUTE_POLICY` (e.g. ``viewer`` may read agents,
    ``builder`` may edit them). A successful mutating call is audit-logged once
    per request, however many of the guards below a handler declares.

    Raises:
        UnauthorizedError: When no valid credential is present.
        ForbiddenError: When the role or API-key scope is insufficient.
    """
    yield ctx
    record_route_mutation(request, db, ctx)


#: ``scope="function"`` like `DbDep`: its exit code writes the audit row with the
#: request's session, before that session commits and before the response is sent.
AdminCtxDep = Annotated[WorkspaceContext, Depends(admin_context, scope="function")]


async def require_admin(_ctx: AdminCtxDep) -> bool:
    """Guard of every v1 admin route (V2-02: now role- and workspace-aware).

    Kept for the v1 ``_admin: AdminDep`` parameters; scoping a handler means
    swapping it for ``ctx: AdminCtxDep`` and filtering on ``ctx.workspace_id``.
    """
    return True


def optional_admin(request: Request, ctx: OptionalWorkspaceCtxDep) -> bool:
    """Privileged-caller detection that never rejects — used by dual-audience routes.

    True when the caller is authenticated and would pass this route's policy
    (for ``GET /v1/agents/{id}``: at least ``viewer``); anonymous callers get
    the public view.
    """
    if ctx is None:
        return False
    route = request.scope.get("route")
    route_path = getattr(route, "path", request.url.path)
    return ctx.allows(policy_for(request.method, str(route_path)))


def require_service(settings: SettingsDep, x_service_token: ServiceTokenHeader = None) -> bool:
    """Reject the request unless a valid worker service token is present.

    Raises:
        UnauthorizedError: When the token is missing or wrong.
    """
    if not token_matches(x_service_token, settings.service_token):
        raise UnauthorizedError(f"a valid {SERVICE_HEADER} header is required")
    return True


AdminDep = Annotated[bool, Depends(require_admin)]
OptionalAdminDep = Annotated[bool, Depends(optional_admin)]
ServiceDep = Annotated[bool, Depends(require_service)]
