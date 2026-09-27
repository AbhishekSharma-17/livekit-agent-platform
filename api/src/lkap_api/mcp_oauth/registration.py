"""Registration: which OAuth client id the sign-in uses (V5-14, research-v4 tools §4.3.5).

The spec's priority order:

1. **Pre-registered**: ``auth.registration == "preregistered"`` with ``auth.client_id``
   (the admin registered an app with the vendor and pasted its client id; a client
   secret, when the vendor issued one, arrives write-only in the start request and is
   kept on a ``mcp_oauth_clients`` row), or a client this workspace already registered
   dynamically at the same issuer for the same redirect URI (reused, not re-registered).
2. **Client ID Metadata Document** when the provider advertises
   ``client_id_metadata_document_supported`` **and** ``LKAP_PUBLIC_BASE_URL`` is a public
   ``https`` origin (D-V5-3): the client id is the url of the one per-deployment
   document served at ``/v1/oauth/mcp/client-metadata.json``.
3. **Dynamic Client Registration** (RFC 7591, deprecated but common) when the provider
   has a ``registration_endpoint``: a public client (``token_endpoint_auth_method="none"``)
   with ``application_type`` ``native`` for a loopback redirect, ``web`` otherwise.
4. Otherwise :class:`NeedsClientRegistration`: the admin registers an app and switches
   the server to ``preregistered``.

Client secrets and registration access tokens live only in the vault
(``mcp_oauth_clients.ciphertext``).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Final, Literal
from urllib.parse import urlsplit

import httpx
from lkap_contracts.tools import McpOAuthAuth
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api import net_guard
from lkap_api.db.models import McpOauthClient, utcnow
from lkap_api.mcp_oauth.discovery import AuthServer
from lkap_api.mcp_oauth.http import McpOauthError, UrlPolicy, fetch, loopback_host, require_url, url_problem
from lkap_api.settings import Settings
from lkap_api.vault import Vault

#: The path of the per-deployment client metadata document (D-V5-3).
CLIENT_METADATA_PATH: Final[str] = "/v1/oauth/mcp/client-metadata.json"
#: The path the authorization server sends the browser back to.
CALLBACK_PATH: Final[str] = "/v1/oauth/mcp/callback"
#: The client name providers show on their consent screen.
CLIENT_NAME: Final[str] = "LKAP"
#: Grants LKAP asks for (refresh is used from V5-16).
GRANT_TYPES: Final[tuple[str, ...]] = ("authorization_code", "refresh_token")

Registration = Literal["preregistered", "cimd", "dcr"]


class NeedsClientRegistration(Exception):
    """The provider offers neither a metadata document nor dynamic registration."""


@dataclass(frozen=True)
class ClientChoice:
    """The client the sign-in uses (the secret, if any, stays in memory for this request)."""

    registration: Registration
    client_id: str
    token_endpoint_auth_method: str
    client_row_id: str | None = None
    client_metadata_url: str | None = None


def public_base(settings: Settings) -> str:
    """The api origin a browser (and a provider fetching the metadata document) reaches."""
    return (settings.public_base_url or settings.worker_callback_base_url).rstrip("/")


def redirect_uri(settings: Settings) -> str:
    """The callback url; ``https``, or ``http`` on a loopback host (the spec allows both).

    Raises:
        McpOauthError: ``redirect_uri_not_https`` when neither holds.
    """
    uri = f"{public_base(settings)}{CALLBACK_PATH}"
    parsed = urlsplit(uri)
    if parsed.scheme == "https" or (parsed.scheme == "http" and loopback_host(parsed.hostname or "")):
        return uri
    raise McpOauthError(
        "redirect_uri_not_https",
        "the sign-in return address must be https (set LKAP_PUBLIC_BASE_URL) or a localhost address",
    )


def client_metadata_url(settings: Settings) -> str | None:
    """The metadata document's url when ``LKAP_PUBLIC_BASE_URL`` is public ``https``, else ``None``."""
    base = (settings.public_base_url or "").rstrip("/")
    if not base:
        return None
    parsed = urlsplit(base)
    if parsed.scheme != "https" or not parsed.hostname or parsed.path not in ("", "/"):
        return None
    if net_guard.check_url(base, net_guard.NetPolicy()) is not None or loopback_host(parsed.hostname):
        return None  # a private or local origin: the provider could not fetch the document
    return f"{base}{CLIENT_METADATA_PATH}"


def client_metadata_document(settings: Settings) -> dict[str, object] | None:
    """The one Client ID Metadata Document of this deployment, or ``None`` when unavailable."""
    url = client_metadata_url(settings)
    if url is None:
        return None
    return {
        "client_id": url,
        "client_name": CLIENT_NAME,
        "client_uri": public_base(settings),
        "redirect_uris": [redirect_uri(settings)],
        "grant_types": list(GRANT_TYPES),
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }


def secret_auth_method(server: AuthServer) -> str:
    """How to present a client secret: ``client_secret_basic`` unless only ``_post`` is offered."""
    methods = server.token_endpoint_auth_methods
    if methods is None or "client_secret_basic" in methods:
        return "client_secret_basic"  # RFC 8414: the default when the list is absent
    if "client_secret_post" in methods:
        return "client_secret_post"
    raise McpOauthError(
        "unsupported_client_auth", "the sign-in provider accepts no client-secret method LKAP supports"
    )


def client_secrets(row: McpOauthClient, vault: Vault) -> dict[str, str]:
    """The decrypted secret bag of a client row (``{}`` for a public client)."""
    return vault.decrypt(row.ciphertext) if row.ciphertext else {}


async def _preregistered(
    db: AsyncSession,
    vault: Vault,
    *,
    workspace_id: str,
    auth: McpOAuthAuth,
    server: AuthServer,
    redirect: str,
    client_secret: str | None,
) -> ClientChoice:
    if not auth.client_id:
        # Ask #166: no client id yet is the "register an app" answer (with the return address
        # to register), not a refusal: it is the one place the admin learns that address.
        raise NeedsClientRegistration()
    row = await db.scalar(
        select(McpOauthClient).where(
            McpOauthClient.workspace_id == workspace_id,
            McpOauthClient.issuer == server.issuer,
            McpOauthClient.client_id == auth.client_id,
            McpOauthClient.registration == "preregistered",
        )
    )
    if row is None:
        row = McpOauthClient(
            workspace_id=workspace_id,
            issuer=server.issuer,
            client_id=auth.client_id,
            registration="preregistered",
            redirect_uri=redirect,
            token_endpoint_auth_method="none",
        )
        db.add(row)
    if client_secret is not None:
        row.ciphertext = vault.encrypt({"client_secret": client_secret})
    row.redirect_uri = redirect
    row.token_endpoint_auth_method = (
        secret_auth_method(server) if client_secrets(row, vault).get("client_secret") else "none"
    )
    row.updated_at = utcnow()
    await db.flush()
    return ClientChoice(
        registration="preregistered",
        client_id=auth.client_id,
        token_endpoint_auth_method=row.token_endpoint_auth_method,
        client_row_id=row.id,
    )


async def _stored_dcr(
    db: AsyncSession, *, workspace_id: str, server: AuthServer, redirect: str
) -> McpOauthClient | None:
    now = utcnow()
    rows = (
        await db.execute(
            select(McpOauthClient)
            .where(
                McpOauthClient.workspace_id == workspace_id,
                McpOauthClient.issuer == server.issuer,
                McpOauthClient.registration == "dcr",
                McpOauthClient.redirect_uri == redirect,
            )
            .order_by(McpOauthClient.created_at.desc())
        )
    ).scalars()
    for row in rows:
        if row.client_secret_expires_at is None or row.client_secret_expires_at > now:
            return row
    return None


def _management_uri(uri: object, endpoint: str, policy: UrlPolicy) -> str | None:
    """The DCR answer's ``registration_client_uri`` when it is safe to keep (S5-16).

    It must pass the sign-in URL check and share the registration endpoint's origin; any
    other value is dropped (the client then cannot be deleted by RFC 7592, only revoked).
    """
    if not isinstance(uri, str) or url_problem(uri, policy) is not None:
        return None
    given, expected = urlsplit(uri), urlsplit(endpoint)
    same_origin = (given.scheme.lower(), given.hostname, given.port) == (
        expected.scheme.lower(),
        expected.hostname,
        expected.port,
    )
    return uri if same_origin else None


async def _register(
    db: AsyncSession,
    vault: Vault,
    client: httpx.AsyncClient,
    policy: UrlPolicy,
    *,
    workspace_id: str,
    server: AuthServer,
    redirect: str,
) -> McpOauthClient:
    assert server.registration_endpoint is not None  # noqa: S101 - checked by the caller
    endpoint = require_url(server.registration_endpoint, policy, field="registration_endpoint")
    native = loopback_host(urlsplit(redirect).hostname or "")
    fetched = await fetch(
        client,
        "POST",
        endpoint,
        headers={"Content-Type": "application/json"},
        json_body={
            "redirect_uris": [redirect],
            "grant_types": list(GRANT_TYPES),
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "client_name": CLIENT_NAME,
            "application_type": "native" if native else "web",
        },
    )
    if fetched.status not in (200, 201):
        raise McpOauthError(
            "registration_failed", f"the sign-in provider refused to register LKAP ({fetched.status})"
        )
    body = fetched.json_object()
    client_id = body.get("client_id") if body else None
    if body is None or not isinstance(client_id, str) or not client_id or len(client_id) > 2000:
        raise McpOauthError(
            "registration_failed", "the sign-in provider's registration answer has no client id"
        )
    secrets: dict[str, str] = {}
    for key in ("client_secret", "registration_access_token"):
        value = body.get(key)
        if isinstance(value, str) and value:
            secrets[key] = value
    method = body.get("token_endpoint_auth_method")
    if not isinstance(method, str) or method not in ("none", "client_secret_basic", "client_secret_post"):
        method = "none" if "client_secret" not in secrets else "client_secret_basic"
    uri = _management_uri(body.get("registration_client_uri"), endpoint, policy)
    expires = body.get("client_secret_expires_at")
    row = McpOauthClient(
        workspace_id=workspace_id,
        issuer=server.issuer,
        client_id=client_id,
        registration="dcr",
        redirect_uri=redirect,
        token_endpoint_auth_method=method,
        ciphertext=vault.encrypt(secrets) if secrets else None,
        # Kept for V5-16's client deletion; checked against the guard again before any use.
        registration_client_uri=uri,
        client_secret_expires_at=(
            dt.datetime.fromtimestamp(expires, dt.UTC) if isinstance(expires, int) and expires > 0 else None
        ),
    )
    db.add(row)
    await db.flush()
    return row


async def choose_client(
    db: AsyncSession,
    vault: Vault,
    client: httpx.AsyncClient,
    settings: Settings,
    policy: UrlPolicy,
    *,
    workspace_id: str,
    auth: McpOAuthAuth,
    server: AuthServer,
    redirect: str,
    client_secret: str | None,
) -> ClientChoice:
    """Pick (or obtain) the client id, in the spec's priority order.

    Raises:
        NeedsClientRegistration: When no automatic option exists.
        McpOauthError: A refused or failed registration.
    """
    if auth.registration == "preregistered":
        return await _preregistered(
            db,
            vault,
            workspace_id=workspace_id,
            auth=auth,
            server=server,
            redirect=redirect,
            client_secret=client_secret,
        )
    if client_secret is not None:
        raise McpOauthError(
            "client_secret_not_expected",
            "a client secret is only used with a pre-registered client",
            field="client_secret",
        )
    stored = await _stored_dcr(db, workspace_id=workspace_id, server=server, redirect=redirect)
    if stored is not None:
        return ClientChoice(
            registration="dcr",
            client_id=stored.client_id,
            token_endpoint_auth_method=stored.token_endpoint_auth_method,
            client_row_id=stored.id,
        )
    metadata_url = client_metadata_url(settings)
    if server.cimd_supported and metadata_url is not None:
        return ClientChoice(
            registration="cimd",
            client_id=metadata_url,
            token_endpoint_auth_method="none",
            client_metadata_url=metadata_url,
        )
    if server.registration_endpoint:
        row = await _register(
            db, vault, client, policy, workspace_id=workspace_id, server=server, redirect=redirect
        )
        return ClientChoice(
            registration="dcr",
            client_id=row.client_id,
            token_endpoint_auth_method=row.token_endpoint_auth_method,
            client_row_id=row.id,
        )
    raise NeedsClientRegistration()


__all__ = [
    "CALLBACK_PATH",
    "CLIENT_METADATA_PATH",
    "ClientChoice",
    "NeedsClientRegistration",
    "Registration",
    "choose_client",
    "client_metadata_document",
    "client_metadata_url",
    "client_secrets",
    "public_base",
    "redirect_uri",
    "secret_auth_method",
]
