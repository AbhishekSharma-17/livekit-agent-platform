"""The ``mcp_oauth_flows`` row: one pending sign-in, ten minutes, single use (V5-14).

``state`` is 32 random bytes (url-safe), handed to the browser once inside the
authorization url and stored only as its SHA-256 (:func:`hash_state`), so a database
read cannot replay a callback. The PKCE verifier (S256) is stored only as a vault
ciphertext. Starting a new sign-in for a tool deletes that tool's unfinished flows,
so at most one ``state`` per tool is live at a time.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import secrets
from dataclasses import dataclass
from typing import Final
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from mcp.client.auth.oauth2 import PKCEParameters
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.db.models import McpOauthFlow, utcnow
from lkap_api.mcp_oauth.discovery import PKCE_METHOD, Discovery
from lkap_api.mcp_oauth.registration import ClientChoice
from lkap_api.vault import Vault

#: How long a started sign-in may take (research-v4 tools §4.3.2).
FLOW_TTL: Final[dt.timedelta] = dt.timedelta(minutes=10)
#: The longest ``state`` the callback accepts (``token_urlsafe(32)`` is 43 characters).
MAX_STATE_CHARS: Final[int] = 256
#: The browser-binder cookie (R-V5-14): set by ``oauth/start``, required by the callback.
BINDER_COOKIE: Final[str] = "lkap_mcp_oauth"
#: The cookie's path: only the sign-in routes ever receive it.
BINDER_COOKIE_PATH: Final[str] = "/v1/oauth/mcp/"
#: The longest binder cookie value the callback reads.
MAX_BINDER_CHARS: Final[int] = 256


def hash_state(state: str) -> str:
    """The stored form of a ``state`` value (hex SHA-256)."""
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


def hash_binder(binder: str) -> str:
    """The stored form of a binder cookie value (hex SHA-256)."""
    return hashlib.sha256(binder.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class StartedFlow:
    """What ``oauth/start`` hands back (the ``state`` exists only inside the url)."""

    authorization_url: str
    expires_at: dt.datetime
    binder: str
    """The binder cookie's value (R-V5-14): the browser gets it, the flow keeps its hash."""


def authorization_url(
    endpoint: str,
    *,
    client_id: str,
    redirect: str,
    state: str,
    challenge: str,
    resource: str,
    scopes: tuple[str, ...] | None,
) -> str:
    """The provider's authorization url with the request's parameters (existing query kept)."""
    parts = urlsplit(endpoint)
    params = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key
        not in {
            "response_type",
            "client_id",
            "redirect_uri",
            "state",
            "code_challenge",
            "code_challenge_method",
            "resource",
            "scope",
        }
    ]
    params += [
        ("response_type", "code"),
        ("client_id", client_id),
        ("redirect_uri", redirect),
        ("state", state),
        ("code_challenge", challenge),
        ("code_challenge_method", PKCE_METHOD),
        ("resource", resource),
    ]
    if scopes:
        params.append(("scope", " ".join(scopes)))
    return urlunsplit(parts._replace(query=urlencode(params), fragment=""))


async def create_flow(
    db: AsyncSession,
    vault: Vault,
    *,
    workspace_id: str,
    tool_id: str,
    actor_type: str,
    actor_id: str | None,
    discovery: Discovery,
    client: ClientChoice,
    redirect: str,
    now: dt.datetime | None = None,
) -> StartedFlow:
    """Write the flow row (replacing the tool's unfinished ones) and build the authorization url."""
    ts = now or utcnow()
    await db.execute(
        delete(McpOauthFlow).where(
            McpOauthFlow.workspace_id == workspace_id,
            McpOauthFlow.tool_id == tool_id,
            McpOauthFlow.consumed_at.is_(None),
        )
    )
    state = secrets.token_urlsafe(32)
    binder = secrets.token_urlsafe(32)
    pkce = PKCEParameters.generate()
    server = discovery.auth_server
    expires_at = ts + FLOW_TTL
    db.add(
        McpOauthFlow(
            state_hash=hash_state(state),
            workspace_id=workspace_id,
            tool_id=tool_id,
            actor_type=actor_type,
            actor_id=actor_id,
            # The binder's hash sits beside the verifier in the encrypted bag (no column).
            verifier_ciphertext=vault.encrypt(
                {"code_verifier": pkce.code_verifier, "binder_sha256": hash_binder(binder)}
            ),
            issuer=server.issuer,
            iss_parameter_supported=server.iss_parameter_supported,
            resource=discovery.resource,
            token_endpoint=server.token_endpoint,
            revocation_endpoint=server.revocation_endpoint,
            registration=client.registration,
            client_id=client.client_id,
            client_row_id=client.client_row_id,
            token_endpoint_auth_method=client.token_endpoint_auth_method,
            scopes=" ".join(discovery.scopes) if discovery.scopes else None,
            redirect_uri=redirect,
            created_at=ts,
            expires_at=expires_at,
        )
    )
    await db.flush()
    url = authorization_url(
        server.authorization_endpoint,
        client_id=client.client_id,
        redirect=redirect,
        state=state,
        challenge=pkce.code_challenge,
        resource=discovery.resource,
        scopes=discovery.scopes,
    )
    return StartedFlow(authorization_url=url, expires_at=expires_at, binder=binder)


__all__ = [
    "BINDER_COOKIE",
    "BINDER_COOKIE_PATH",
    "FLOW_TTL",
    "MAX_BINDER_CHARS",
    "MAX_STATE_CHARS",
    "StartedFlow",
    "authorization_url",
    "create_flow",
    "hash_binder",
    "hash_state",
]
