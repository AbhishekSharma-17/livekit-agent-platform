"""``OAuthWorld``: an in-process MCP server plus its OAuth authorization server (V5-14 tests).

One ``httpx.MockTransport`` handler plays three parties, keyed by host:

* ``mcp.example.com`` — the MCP server: ``401`` with ``WWW-Authenticate`` without a
  valid bearer; with one, a tiny ``initialize`` / ``tools/list`` server. It also serves
  protected resource metadata at the well-known paths the test picks.
* ``auth.example.com`` — the authorization server: metadata (RFC 8414 or OpenID
  discovery, path-inserted or appended), dynamic registration, the token endpoint
  (checks the PKCE S256 verifier, ``redirect_uri``, ``resource``, the client and its
  secret; codes are single use).
* anything else — recorded and answered ``599`` (a test asserts it was never asked).

:meth:`OAuthWorld.authorize` stands in for the admin's browser at the consent screen:
it reads the authorization url LKAP built and returns the callback query.
No network, no real credentials.
"""

from __future__ import annotations

import base64
import hashlib
import itertools
import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

MCP_HOST = "mcp.example.com"
MCP_URL = f"https://{MCP_HOST}/mcp"
AS_HOST = "auth.example.com"
AS_ROOT = f"https://{AS_HOST}"


@dataclass
class _Grant:
    client_id: str
    redirect_uri: str
    challenge: str
    resource: str | None
    scope: str | None


@dataclass
class OAuthWorld:
    """Configurable fake MCP server + authorization server."""

    issuer: str = AS_ROOT
    """The authorization server's issuer (a path makes discovery path-aware)."""
    prm_via: str = "header"
    """Where the MCP server publishes its metadata: ``header`` | ``path`` | ``root``."""
    as_metadata_at: str = "oauth"
    """Which well-known document the AS serves: ``oauth`` | ``oidc`` | ``oidc_append``."""
    www_scope: str | None = "issues:read"
    prm_scopes: list[str] | None = field(default_factory=lambda: ["issues:read", "issues:write"])
    prm_resource: str | None = None
    """Overrides the metadata's ``resource`` (default: the MCP url)."""
    authorization_servers: list[str] | None = None
    """Overrides the metadata's list (default: ``[issuer]``)."""
    metadata_issuer: str | None = None
    """Overrides the ``issuer`` inside the AS metadata (a mismatch test)."""
    pkce_methods: list[str] | None = field(default_factory=lambda: ["S256"])
    cimd_supported: bool = False
    registration: bool = True
    iss_supported: bool = False
    token_auth_methods: list[str] | None = None
    scopes_supported: list[str] | None = None
    issue_refresh_token: bool = True
    expires_in: int | None = 3600
    mcp_status_without_token: int = 401
    prm_redirect: bool = False
    access_token: str = "at-fake-7Hs2Kd9Qw4Lp1Zx8"
    refresh_token: str = "rt-fake-3Mv6Nb0Tc5Ry2Gu7"
    registered_secret: str | None = None
    """A client secret DCR issues (a confidential registration)."""
    preregistered: dict[str, str | None] = field(default_factory=dict)
    """client_id → secret (``None``: a public pre-registered client)."""

    requests: list[httpx.Request] = field(default_factory=list)
    token_calls: list[dict[str, list[str]]] = field(default_factory=list)
    registrations: list[dict[str, Any]] = field(default_factory=list)
    _grants: dict[str, _Grant] = field(default_factory=dict)
    _clients: dict[str, str | None] = field(default_factory=dict)
    _codes: itertools.count[int] = field(default_factory=lambda: itertools.count(1))

    # ------------------------------------------------------------------ derived
    @property
    def as_path(self) -> str:
        return urlsplit(self.issuer).path.rstrip("/")

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def hosts_asked(self) -> set[str]:
        return {request.url.host for request in self.requests}

    # ------------------------------------------------------------------ browser
    def authorize(self, authorization_url: str, *, iss: str | None | bool = True) -> dict[str, str]:
        """Consent as the admin: returns the callback query (``code``, ``state``, maybe ``iss``).

        ``iss=True`` sends the real issuer only when the AS advertises support; a string
        sends that value; ``False``/``None`` sends none.
        """
        query = {key: values[0] for key, values in parse_qs(urlsplit(authorization_url).query).items()}
        assert query["response_type"] == "code"
        assert query["code_challenge_method"] == "S256"
        code = f"code-{next(self._codes)}-Yt5Wq"
        self._grants[code] = _Grant(
            client_id=query["client_id"],
            redirect_uri=query["redirect_uri"],
            challenge=query["code_challenge"],
            resource=query.get("resource"),
            scope=query.get("scope"),
        )
        answer = {"code": code, "state": query["state"]}
        if iss is True:
            if self.iss_supported:
                answer["iss"] = self.issuer
        elif isinstance(iss, str):
            answer["iss"] = iss
        return answer

    # ------------------------------------------------------------------ dispatch
    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == MCP_HOST:
            return self._mcp(request)
        if request.url.host == AS_HOST:
            return self._as(request)
        return httpx.Response(599)

    def _prm_document(self) -> dict[str, Any]:
        return {
            "resource": self.prm_resource or MCP_URL,
            "authorization_servers": self.authorization_servers or [self.issuer],
            **({"scopes_supported": self.prm_scopes} if self.prm_scopes is not None else {}),
        }

    def _mcp(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/.well-known/oauth-protected-resource"):
            if self.prm_redirect:
                return httpx.Response(302, headers={"Location": "https://elsewhere.example.org/prm"})
            wanted = {
                "header": "/.well-known/oauth-protected-resource/by-header",
                "path": "/.well-known/oauth-protected-resource/mcp",
                "root": "/.well-known/oauth-protected-resource",
            }[self.prm_via]
            if path == wanted:
                return httpx.Response(200, json=self._prm_document())
            return httpx.Response(404)
        if path != "/mcp":
            return httpx.Response(404)
        bearer = request.headers.get("authorization", "")
        if bearer != f"Bearer {self.access_token}":
            if self.mcp_status_without_token == 200:
                return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})
            if self.mcp_status_without_token != 401:
                return httpx.Response(
                    self.mcp_status_without_token, headers={"Location": "https://x.example.org"}
                )
            challenge = "Bearer"
            params = []
            if self.prm_via == "header":
                params.append(
                    f'resource_metadata="https://{MCP_HOST}/.well-known/oauth-protected-resource/by-header"'
                )
            if self.www_scope:
                params.append(f'scope="{self.www_scope}"')
            if params:
                challenge += " " + ", ".join(params)
            return httpx.Response(401, headers={"WWW-Authenticate": challenge})
        if request.method == "DELETE":
            return httpx.Response(200)
        message = json.loads(request.content)
        if "id" not in message:
            return httpx.Response(202)
        if message["method"] == "initialize":
            result: dict[str, Any] = {"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {}}
        else:
            result = {"tools": [{"name": "list_issues"}, {"name": "create_issue"}]}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": message["id"], "result": result})

    def _as_metadata(self) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "issuer": self.metadata_issuer or self.issuer,
            "authorization_endpoint": f"{AS_ROOT}/authorize?tenant=t1",
            "token_endpoint": f"{AS_ROOT}/token",
            "revocation_endpoint": f"{AS_ROOT}/revoke",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
        }
        if self.pkce_methods is not None:
            doc["code_challenge_methods_supported"] = self.pkce_methods
        if self.registration:
            doc["registration_endpoint"] = f"{AS_ROOT}/register"
        if self.cimd_supported:
            doc["client_id_metadata_document_supported"] = True
        if self.iss_supported:
            doc["authorization_response_iss_parameter_supported"] = True
        if self.token_auth_methods is not None:
            doc["token_endpoint_auth_methods_supported"] = self.token_auth_methods
        if self.scopes_supported is not None:
            doc["scopes_supported"] = self.scopes_supported
        return doc

    def _metadata_path(self) -> str:
        tenant = self.as_path
        return {
            "oauth": f"/.well-known/oauth-authorization-server{tenant}",
            "oidc": f"/.well-known/openid-configuration{tenant}",
            "oidc_append": f"{tenant}/.well-known/openid-configuration",
        }[self.as_metadata_at]

    def _as(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and ".well-known" in path:
            if path == self._metadata_path():
                return httpx.Response(200, json=self._as_metadata())
            return httpx.Response(404)
        if path == "/register" and request.method == "POST":
            body = json.loads(request.content)
            self.registrations.append(body)
            client_id = f"dcr-client-{len(self.registrations)}"
            self._clients[client_id] = self.registered_secret
            answer = {
                **body,
                "client_id": client_id,
                "registration_client_uri": f"{AS_ROOT}/register/{client_id}",
            }
            answer["registration_access_token"] = "rat-fake-Pq8Lm2"
            if self.registered_secret:
                answer["client_secret"] = self.registered_secret
                answer["token_endpoint_auth_method"] = "client_secret_basic"
            return httpx.Response(201, json=answer)
        if path == "/token" and request.method == "POST":
            return self._token(request)
        return httpx.Response(404)

    def _client_auth(
        self, request: httpx.Request, form: dict[str, list[str]]
    ) -> tuple[str | None, str | None]:
        header = request.headers.get("authorization")
        if header and header.startswith("Basic "):
            user, _, password = base64.b64decode(header[6:]).decode().partition(":")
            return user, password
        return (form.get("client_id") or [None])[0], (form.get("client_secret") or [None])[0]

    def _token(self, request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode())
        self.token_calls.append(form)
        client_id, secret = self._client_auth(request, form)
        code = (form.get("code") or [""])[0]
        grant = self._grants.pop(code, None)
        if grant is None or form.get("grant_type") != ["authorization_code"]:
            return httpx.Response(400, json={"error": "invalid_grant"})
        verifier = (form.get("code_verifier") or [""])[0]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        known = {**self._clients, **self.preregistered}
        is_cimd = client_id is not None and client_id.startswith("https://")
        if (
            challenge != grant.challenge
            or client_id != grant.client_id
            or (form.get("redirect_uri") or [""])[0] != grant.redirect_uri
            or (form.get("resource") or [None])[0] != grant.resource
            or (not is_cimd and client_id not in known)
            or (not is_cimd and known.get(client_id) != secret)
        ):
            return httpx.Response(400, json={"error": "invalid_grant"})
        body: dict[str, Any] = {"access_token": self.access_token, "token_type": "bearer"}
        if self.expires_in is not None:
            body["expires_in"] = self.expires_in
        if self.issue_refresh_token:
            body["refresh_token"] = self.refresh_token
        if grant.scope:
            body["scope"] = grant.scope
        return httpx.Response(200, json=body)


__all__ = ["AS_HOST", "AS_ROOT", "MCP_HOST", "MCP_URL", "OAuthWorld"]
