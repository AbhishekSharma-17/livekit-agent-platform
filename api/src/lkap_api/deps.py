"""FastAPI dependencies: settings, database sessions, vault and auth guards.

Routers depend on the ``*Dep`` aliases declared here rather than importing the
concrete objects, so tests can override any of them with
``app.dependency_overrides``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Annotated

import httpx
from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.auth import ADMIN_HEADER, SERVICE_HEADER, bearer_value, token_matches
from lkap_api.db.session import get_db
from lkap_api.errors import UnauthorizedError
from lkap_api.settings import Settings, get_settings
from lkap_api.vault import Vault

SettingsDep = Annotated[Settings, Depends(get_settings)]
DbDep = Annotated[AsyncSession, Depends(get_db)]

AdminTokenHeader = Annotated[str | None, Header(alias=ADMIN_HEADER, description="Static admin token")]
ServiceTokenHeader = Annotated[str | None, Header(alias=SERVICE_HEADER, description="Worker service token")]
AuthorizationHeader = Annotated[str | None, Header(alias="Authorization", include_in_schema=False)]


@lru_cache(maxsize=4)
def _vault_for(master_key: str) -> Vault:
    return Vault(master_key)


def get_vault(settings: SettingsDep) -> Vault:
    """Return the process-wide :class:`~lkap_api.vault.Vault`."""
    return _vault_for(settings.master_key)


VaultDep = Annotated[Vault, Depends(get_vault)]


async def get_http_client() -> AsyncIterator[httpx.AsyncClient]:
    """Yield an outbound HTTP client (credential tests, tool dry runs).

    Tests override this dependency with a client backed by
    ``httpx.MockTransport`` so the offline suite never touches the network.
    """
    async with httpx.AsyncClient(follow_redirects=False) as client:
        yield client


HttpClientDep = Annotated[httpx.AsyncClient, Depends(get_http_client)]


def is_admin(
    settings: SettingsDep,
    x_admin_token: AdminTokenHeader = None,
    authorization: AuthorizationHeader = None,
) -> bool:
    """Return whether the caller presented a valid admin token."""
    return token_matches(x_admin_token, settings.admin_token) or token_matches(
        bearer_value(authorization), settings.admin_token
    )


def optional_admin(admin: Annotated[bool, Depends(is_admin)]) -> bool:
    """Admin detection that never rejects — used by dual-audience routes."""
    return admin


def require_admin(admin: Annotated[bool, Depends(is_admin)]) -> bool:
    """Reject the request unless a valid admin token is present.

    Raises:
        UnauthorizedError: When the token is missing or wrong.
    """
    if not admin:
        raise UnauthorizedError(f"a valid {ADMIN_HEADER} header is required")
    return True


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
