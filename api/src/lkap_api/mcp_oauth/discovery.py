"""Discovery: MCP server → protected resource metadata → authorization server metadata (V5-14).

research-v4 tools §4.3.4 and the MCP authorization spec (draft of 2026-07-28):

1. ``POST {url}`` an ``initialize`` request without a token and read the
   ``401``'s ``WWW-Authenticate`` (``resource_metadata``, ``scope``). A server that
   answers 2xx needs no sign-in (``oauth_not_required``).
2. Protected resource metadata (RFC 9728): the header's url, then the path-based
   and root well-known urls (``build_protected_resource_metadata_discovery_urls``).
   No document → refused (the spec requires PRM; the legacy "AS on the MCP host"
   fallback is not used). The document's ``resource`` must contain the server's
   canonical url (``check_resource_allowed``), and that ``resource`` is what the
   authorization and token requests carry (RFC 8707).
3. Authorization server metadata (RFC 8414 and OpenID Connect Discovery, path
   inserted then appended: ``build_oauth_authorization_server_metadata_discovery_urls``).
   The document's ``issuer`` must name the server it was fetched for
   (``issuers_match``); ``code_challenge_methods_supported`` must list ``S256``
   (absent means no PKCE: refused). The ``issuer`` is kept exactly as the
   document spelled it, for the callback's RFC 9207 comparison.

Every server-supplied url passes :func:`lkap_api.mcp_oauth.http.require_url` before
it is fetched; redirects are never followed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

import httpx
from mcp.client.auth.utils import (
    build_oauth_authorization_server_metadata_discovery_urls,
    build_protected_resource_metadata_discovery_urls,
    extract_resource_metadata_from_www_auth,
    extract_scope_from_www_auth,
    issuers_match,
)
from mcp.shared.auth import OAuthMetadata, ProtectedResourceMetadata
from mcp.shared.auth_utils import check_resource_allowed, resource_url_from_server_url
from pydantic import ValidationError

from lkap_api.mcp_oauth.http import Fetched, McpOauthError, UrlPolicy, fetch, require_url
from lkap_api.mcp_test import PROTOCOL_VERSION

#: The PKCE method the spec requires; a provider that does not list it is refused.
PKCE_METHOD: Final[str] = "S256"
#: Scope added when the provider lists it, so a refresh token can be issued (spec: MAY).
OFFLINE_ACCESS: Final[str] = "offline_access"
#: At most this many authorization servers are considered from one metadata document.
MAX_AUTHORIZATION_SERVERS: Final[int] = 10


@dataclass(frozen=True)
class AuthServer:
    """What the sign-in needs from one authorization server's validated metadata."""

    issuer: str
    """Exactly as the metadata document spelled it (the RFC 9207 comparison is byte-for-byte)."""
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str | None
    revocation_endpoint: str | None
    cimd_supported: bool
    iss_parameter_supported: bool
    token_endpoint_auth_methods: tuple[str, ...] | None
    scopes_supported: tuple[str, ...] | None


@dataclass(frozen=True)
class Discovery:
    """The outcome of discovery for one MCP server."""

    resource: str
    """The RFC 8707 ``resource`` value (the metadata document's, which contains the server's url)."""
    auth_server: AuthServer
    authorization_servers: tuple[str, ...]
    scopes: tuple[str, ...] | None
    """What to ask for: the admin's override, else the 401's ``scope``, else PRM ``scopes_supported``."""


def canonical_resource(url: str) -> str:
    """The MCP server's canonical url (lowercase scheme and host, no fragment)."""
    return resource_url_from_server_url(url.strip())


def resource_covers(resource: str, server_url: str) -> bool:
    """Whether a token for ``resource`` is meant for the MCP server at ``server_url``."""
    return check_resource_allowed(
        requested_resource=canonical_resource(server_url), configured_resource=resource
    )


async def _probe(client: httpx.AsyncClient, server_url: str) -> Fetched:
    return await fetch(
        client,
        "POST",
        server_url,
        headers={
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        },
        json_body={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "lkap-api", "version": "0.1"},
            },
        },
    )


def _www_auth_values(probe: Fetched) -> tuple[str | None, str | None]:
    response = httpx.Response(probe.status, headers=probe.headers)
    return extract_resource_metadata_from_www_auth(response), extract_scope_from_www_auth(response)


async def _resource_metadata(
    client: httpx.AsyncClient, header_url: str | None, server_url: str, policy: UrlPolicy
) -> tuple[ProtectedResourceMetadata, dict[str, Any]]:
    for candidate in build_protected_resource_metadata_discovery_urls(header_url, server_url):
        url = require_url(candidate, policy, field="resource_metadata")
        fetched = await fetch(client, "GET", url, headers={"MCP-Protocol-Version": PROTOCOL_VERSION})
        if fetched.status != 200:
            continue  # not served here (a 3xx included: never followed)
        raw = fetched.json_object()
        if raw is None:
            continue
        try:
            return ProtectedResourceMetadata.model_validate(raw), raw
        except ValidationError:
            continue
    raise McpOauthError(
        "no_resource_metadata",
        "the MCP server does not say where to sign in (no protected resource metadata was found)",
    )


def _string_list(value: object) -> tuple[str, ...] | None:
    if not isinstance(value, list):
        return None
    return tuple(item for item in value if isinstance(item, str))


async def _auth_server_metadata(
    client: httpx.AsyncClient, as_url: str, server_url: str, policy: UrlPolicy
) -> AuthServer:
    for candidate in build_oauth_authorization_server_metadata_discovery_urls(as_url, server_url):
        url = require_url(candidate, policy, field="authorization_server_metadata")
        fetched = await fetch(client, "GET", url, headers={"MCP-Protocol-Version": PROTOCOL_VERSION})
        if fetched.status >= 500:
            raise McpOauthError(
                "authorization_server_error", f"the sign-in provider answered {fetched.status}"
            )
        if fetched.status != 200:
            continue
        raw = fetched.json_object()
        if raw is None:
            continue
        try:
            metadata = OAuthMetadata.model_validate(raw)
        except ValidationError:
            continue
        del metadata  # validated for shape; every value below comes from the raw document
        return _auth_server_from(raw, as_url, policy)
    raise McpOauthError(
        "no_authorization_server_metadata", "the sign-in provider publishes no authorization server metadata"
    )


def _auth_server_from(raw: dict[str, Any], as_url: str, policy: UrlPolicy) -> AuthServer:
    issuer = raw.get("issuer")
    if not isinstance(issuer, str) or not issuers_match(issuer, as_url):
        raise McpOauthError(
            "issuer_mismatch", "the sign-in provider's metadata names a different issuer than its address"
        )
    require_url(issuer, policy, field="issuer")
    methods = _string_list(raw.get("code_challenge_methods_supported"))
    if not methods or PKCE_METHOD not in methods:
        raise McpOauthError(
            "pkce_unsupported",
            "the sign-in provider does not support PKCE with S256, which MCP requires; LKAP will not sign in",
        )
    response_types = _string_list(raw.get("response_types_supported"))
    if response_types is not None and "code" not in response_types:
        raise McpOauthError("unsupported_response_type", "the sign-in provider does not offer the code flow")
    registration = raw.get("registration_endpoint")
    revocation = raw.get("revocation_endpoint")
    return AuthServer(
        issuer=issuer,
        authorization_endpoint=require_url(
            raw.get("authorization_endpoint"), policy, field="authorization_endpoint"
        ),
        token_endpoint=require_url(raw.get("token_endpoint"), policy, field="token_endpoint"),
        registration_endpoint=(
            require_url(registration, policy, field="registration_endpoint") if registration else None
        ),
        revocation_endpoint=(
            require_url(revocation, policy, field="revocation_endpoint") if revocation else None
        ),
        cimd_supported=raw.get("client_id_metadata_document_supported") is True,
        iss_parameter_supported=raw.get("authorization_response_iss_parameter_supported") is True,
        token_endpoint_auth_methods=_string_list(raw.get("token_endpoint_auth_methods_supported")),
        scopes_supported=_string_list(raw.get("scopes_supported")),
    )


def _scopes(
    override: list[str] | None,
    challenged: str | None,
    prm: ProtectedResourceMetadata,
    server: AuthServer,
) -> tuple[str, ...] | None:
    if override:
        chosen: list[str] = list(override)
    elif challenged:
        chosen = challenged.split()
    elif prm.scopes_supported:
        chosen = list(prm.scopes_supported)
    else:
        return None
    if server.scopes_supported and OFFLINE_ACCESS in server.scopes_supported and OFFLINE_ACCESS not in chosen:
        chosen.append(OFFLINE_ACCESS)
    return tuple(dict.fromkeys(chosen))


async def discover(
    client: httpx.AsyncClient,
    server_url: str,
    policy: UrlPolicy,
    *,
    scopes_override: list[str] | None = None,
    authorization_server: str | None = None,
) -> Discovery:
    """Run discovery for the MCP server at ``server_url`` (already checked against the MCP policy).

    Args:
        client: The guarded outbound client (no redirects).
        server_url: The MCP server's endpoint.
        policy: Where sign-in requests may go.
        scopes_override: The admin's scopes (``McpOAuthAuth.scopes``), if any.
        authorization_server: Which of several advertised servers to use (default: the first).

    Raises:
        McpOauthError: Every refusal, with a value-free message.
    """
    probe = await _probe(client, server_url)
    if 200 <= probe.status < 300:
        raise McpOauthError("oauth_not_required", "the MCP server answered without asking for a sign-in")
    if 300 <= probe.status < 400:
        raise McpOauthError(
            "redirect", f"the MCP server answered {probe.status} with a redirect, which is never followed"
        )
    header_url, challenged = _www_auth_values(probe) if probe.status in (401, 403) else (None, None)
    prm, raw_prm = await _resource_metadata(client, header_url, server_url, policy)

    resource = raw_prm.get("resource")
    if not isinstance(resource, str) or not resource_covers(resource, server_url):
        raise McpOauthError(
            "resource_mismatch", "the MCP server's metadata describes a different server than its address"
        )
    listed = _string_list(raw_prm.get("authorization_servers")) or ()
    listed = listed[:MAX_AUTHORIZATION_SERVERS]
    if not listed:
        raise McpOauthError("no_authorization_server", "the MCP server names no sign-in provider")
    if authorization_server is not None and authorization_server not in listed:
        raise McpOauthError(
            "unknown_authorization_server",
            "the chosen sign-in provider is not one the MCP server names",
            field="authorization_server",
        )
    as_url = require_url(authorization_server or listed[0], policy, field="authorization_servers")
    server = await _auth_server_metadata(client, as_url, server_url, policy)
    return Discovery(
        resource=resource,
        auth_server=server,
        authorization_servers=listed,
        scopes=_scopes(scopes_override, challenged, prm, server),
    )


__all__ = [
    "OFFLINE_ACCESS",
    "PKCE_METHOD",
    "AuthServer",
    "Discovery",
    "canonical_resource",
    "discover",
    "resource_covers",
]
