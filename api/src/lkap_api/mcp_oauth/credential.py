"""The ``mcp-oauth`` credential: one MCP server's sign-in, in the vault (V5-14, research-v4 tools §4.3.2).

A :class:`~lkap_api.db.models.Credential` row with ``provider_id="mcp-oauth"``. Its
encrypted bag (every value a string; absent keys are simply missing):

``access_token``, ``refresh_token``, ``expires_at`` (absolute, ISO-8601 UTC; the
provider only says ``expires_in``), ``scope``, ``issuer``, ``token_endpoint``,
``revocation_endpoint``, ``resource``, ``client_id``, ``client_secret``,
``token_endpoint_auth_method``, ``registration``, ``registration_client_uri``,
``registration_access_token``, ``client_metadata_url``, ``status``
(``active | needs_reauth | revoked``), ``tool_id``, ``connected_at``,
``last_refresh_at``.

``tool_id`` and ``resource`` bind the bag to the one tool whose sign-in wrote it: a
tool definition may reference an ``mcp-oauth`` credential only when both match
(:func:`binds_tool`). The fingerprint is ``issuer host · first scope`` (no secret).
No route ever returns a bag value.
"""

from __future__ import annotations

import datetime as dt
from typing import Final

from lkap_contracts.providers import MCP_OAUTH_PROVIDER_ID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.db.models import Credential
from lkap_api.mcp_oauth.discovery import resource_covers
from lkap_api.mcp_oauth.http import host_of
from lkap_api.vault import Vault

#: Bag ``status`` of a usable sign-in.
STATUS_ACTIVE: Final[str] = "active"
#: The bag keys whose values are secrets (never logged, never returned, never sent to the worker in V5-14).
SECRET_KEYS: Final[frozenset[str]] = frozenset(
    {"access_token", "refresh_token", "client_secret", "registration_access_token"}
)


def fingerprint_of(issuer: str, scope: str | None) -> str:
    """``issuer host · first scope``, cut to the column's 32 characters."""
    first = (scope or "").split()
    label = host_of(issuer) + (f" · {first[0]}" if first else "")
    return label[:32]


def parse_time(value: str | None) -> dt.datetime | None:
    """An ISO timestamp from the bag, as an aware UTC datetime (``None`` when absent or malformed)."""
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=dt.UTC)


def binds_tool(bag: dict[str, str], *, tool_id: str, url: str) -> bool:
    """Whether a sign-in bag belongs to the tool ``tool_id`` whose server lives at ``url``."""
    resource = bag.get("resource")
    return bag.get("tool_id") == tool_id and bool(resource) and resource_covers(str(resource), url)


async def load_sign_in(
    db: AsyncSession, vault: Vault, *, workspace_id: str, credential_id: str | None
) -> tuple[Credential, dict[str, str]] | None:
    """The ``mcp-oauth`` credential ``credential_id`` of the workspace and its bag, or ``None``."""
    if not credential_id:
        return None
    row = await db.scalar(
        select(Credential).where(
            Credential.id == credential_id,
            Credential.workspace_id == workspace_id,
            Credential.provider_id == MCP_OAUTH_PROVIDER_ID,
        )
    )
    if row is None:
        return None
    return row, vault.decrypt(row.ciphertext)


__all__ = [
    "SECRET_KEYS",
    "STATUS_ACTIVE",
    "binds_tool",
    "fingerprint_of",
    "load_sign_in",
    "parse_time",
]
