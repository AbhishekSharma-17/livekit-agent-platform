"""The worker's bearer for MCP servers that sign in (V5-16, research-v4 tools §4.3.6).

The api is the OAuth client. The worker receives, per signed-in MCP server, a
short-lived access token in :attr:`ResolvedAgentConfig.mcp_oauth` and asks
``POST /internal/v1/tools/{tool_id}/oauth/token`` (service token, bound to this
session) for a new one — it never sees a refresh token, a client secret or a token
endpoint.

:class:`ApiIssuedBearer` wraps the MCP client's (already guarded) transport:

* every request carries ``Authorization: Bearer <access token>``; a token with less
  than :data:`EXPIRY_SKEW_S` left is replaced first;
* on ``401`` it asks the api once (naming the refused token by its SHA-256, so a token
  another request already renewed is not refreshed twice) and retries once; a second
  ``401`` is final;
* on ``403`` with ``error="insufficient_scope"`` the challenged scope names are logged
  (never the header) and nothing is fetched — the ``resource_metadata`` url is not read.

When no token can be had (``needs_reauth``, the second ``401``, ``insufficient_scope``)
the transport answers the MCP client itself, so the connection stays up and the
model hears one spoken-safe sentence (:data:`MCP_REAUTH_MESSAGE`): a ``tools/call`` gets
an ``isError`` result carrying it (livekit-agents turns that into
``ToolError(MCP_REAUTH_MESSAGE)``, which the session observer records as a
``tool_needs_reauth`` event), any other request a JSON-RPC error, a notification a
``202``. This is a transport rather than an ``httpx.Auth``: an auth flow cannot
replace the response, and the MCP client runs each request in a task group, so any
exception or error status there would take the whole server down (and the model would
hear a generic failure). After an admin signs in again, the next request simply gets a
new token.

No token value is ever logged or placed in an error message.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

import httpx
from lkap_contracts.agent_config import McpOAuthAccess, McpOAuthTokenIn, McpOAuthTokenOut
from lkap_contracts.tools import McpServerDefinition

from lkap_agent.config_client import McpOAuthTokenError
from lkap_agent.logging import get_logger

_log = get_logger(__name__)

#: What the model hears (and the console's "needs reconnect" match) when an MCP server
#: that signs in cannot be used until an admin signs in again. No url, no provider detail.
MCP_REAUTH_MESSAGE: Final = "This integration needs to be re-authorised by an admin"
#: A token with less than this left is replaced before use (the api refreshes below 60 s).
EXPIRY_SKEW_S: Final[float] = 30.0
#: After the api said ``needs_reauth``, requests wait this long before asking again.
NEEDS_REAUTH_BACKOFF_S: Final[float] = 30.0
#: JSON-RPC error code of the answer to a non-tool request that cannot be authorised.
REAUTH_RPC_ERROR: Final[int] = -32001
#: A scope name worth logging (letters, digits and the usual separators; anything else is dropped).
_SCOPE_NAME = re.compile(r"^[A-Za-z0-9_.:/\-]{1,120}$")
_CHALLENGE_PARAM = re.compile(r'([A-Za-z_]+)\s*=\s*(?:"([^"]*)"|([^\s,]+))')


class McpOAuthTokenSource(Protocol):
    """Where fresh tokens come from: :meth:`lkap_agent.config_client.ConfigClient.mcp_oauth_token`."""

    async def mcp_oauth_token(self, tool_id: str, request: McpOAuthTokenIn) -> McpOAuthTokenOut:
        """A fresh access token, or :class:`McpOAuthTokenError`."""
        ...


RecordEvent = Callable[[str, dict[str, Any]], None]


@dataclass
class McpOAuthBinding:
    """This session's access to its signed-in MCP servers (built once per session by ``main``)."""

    session_id: str
    source: McpOAuthTokenSource
    tokens: list[McpOAuthAccess] = field(default_factory=list)
    record_event: RecordEvent | None = None

    def access_for(self, definition: McpServerDefinition) -> McpOAuthAccess | None:
        """The access the api issued for ``definition`` (matched by name and url), if any."""
        for access in self.tokens:
            if access.name == definition.name and access.url == definition.url:
                return access
        return None


def token_sha256(token: str) -> str:
    """The hex SHA-256 the token route takes to name a refused token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def challenge_params(headers: httpx.Headers) -> dict[str, str]:
    """The parameters of a ``Bearer`` ``WWW-Authenticate`` challenge (lower-cased names)."""
    for value in headers.get_list("www-authenticate"):
        scheme, _, rest = value.strip().partition(" ")
        if scheme.lower() != "bearer":
            continue
        return {
            match.group(1).lower(): match.group(2) if match.group(2) is not None else match.group(3)
            for match in _CHALLENGE_PARAM.finditer(rest)
        }
    return {}


def challenged_scopes(params: dict[str, str]) -> list[str]:
    """The scope names a challenge asks for, safe to log (at most 20)."""
    return [scope for scope in params.get("scope", "").split() if _SCOPE_NAME.match(scope)][:20]


class ServerToken:
    """One MCP server's current access token, shared by every transport of that server."""

    def __init__(self, access: McpOAuthAccess, binding: McpOAuthBinding) -> None:
        """Start from the token the api put in the resolved config (maybe none)."""
        self.tool_id = access.tool_id
        self.server = access.name
        self._binding = binding
        self._token = access.access_token
        self._expires_at = access.expires_at.timestamp() if access.expires_at is not None else None
        self._lock = asyncio.Lock()
        self._blocked_until = 0.0
        self.reported: set[str] = set()
        """Reasons already recorded as a session event (one event per server and reason)."""

    def _usable(self) -> bool:
        if not self._token:
            return False
        return self._expires_at is None or self._expires_at - time.time() > EXPIRY_SKEW_S

    async def current(self) -> str:
        """A token to send now (fetched when missing or about to expire).

        Raises:
            McpOAuthTokenError: No token can be had.
        """
        if self._usable():
            assert self._token is not None  # noqa: S101 - _usable implies a token
            return self._token
        async with self._lock:
            if not self._usable():
                await self._fetch(rejected=None)
            assert self._token is not None  # noqa: S101 - _fetch sets it or raises
            return self._token

    async def renew(self, refused: str) -> str:
        """A token after the server refused ``refused`` (another request may have renewed it).

        Raises:
            McpOAuthTokenError: No token can be had.
        """
        async with self._lock:
            if self._token and self._token != refused:
                return self._token
            await self._fetch(rejected=refused)
            assert self._token is not None  # noqa: S101 - _fetch sets it or raises
            return self._token

    async def _fetch(self, *, rejected: str | None) -> None:
        if time.monotonic() < self._blocked_until:
            raise McpOAuthTokenError("needs_reauth", "the MCP server needs to be signed in again")
        request = McpOAuthTokenIn(
            session_id=self._binding.session_id,
            rejected_token_sha256=token_sha256(rejected) if rejected else None,
        )
        try:
            issued = await self._binding.source.mcp_oauth_token(self.tool_id, request)
        except McpOAuthTokenError as exc:
            if exc.reason == "needs_reauth":
                self._blocked_until = time.monotonic() + NEEDS_REAUTH_BACKOFF_S
            _log.warning("mcp_oauth.token_unavailable", mcp_server=self.server, reason=exc.reason)
            raise
        self._token = issued.access_token
        self._expires_at = issued.expires_at.timestamp() if issued.expires_at is not None else None
        _log.info("mcp_oauth.token_renewed", mcp_server=self.server, after_rejection=rejected is not None)


def _rpc(request: httpx.Request) -> tuple[str | None, Any, bool]:
    """``(method, id, has_id)`` of a JSON-RPC request body (``(None, None, False)`` otherwise)."""
    if request.method != "POST":
        return None, None, False
    try:
        message = json.loads(request.content)
    except (ValueError, UnicodeDecodeError):
        return None, None, False
    if not isinstance(message, dict):
        return None, None, False
    method = message.get("method")
    return (method if isinstance(method, str) else None), message.get("id"), "id" in message


class ApiIssuedBearer(httpx.AsyncBaseTransport):
    """Adds the api-issued bearer to every MCP request; answers for the server when it cannot."""

    def __init__(
        self, inner: httpx.AsyncBaseTransport, token: ServerToken, *, record_event: RecordEvent | None
    ) -> None:
        """Wrap ``inner`` (the guarded transport) for the server whose token is ``token``."""
        self._inner = inner
        self._token = token
        self._record_event = record_event

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """Send with the bearer; one renewal and one retry on 401; map the unrecoverable cases."""
        await request.aread()  # the body is sent again on the retry
        try:
            token = await self._token.current()
        except McpOAuthTokenError as exc:
            return self._reauth(request, exc.reason)
        response = await self._send(request, token)
        if response.status_code == httpx.codes.UNAUTHORIZED:
            await response.aclose()
            try:
                token = await self._token.renew(token)
            except McpOAuthTokenError as exc:
                return self._reauth(request, exc.reason)
            response = await self._send(request, token)
            if response.status_code == httpx.codes.UNAUTHORIZED:
                await response.aclose()
                return self._reauth(request, "unauthorized")
        if response.status_code == httpx.codes.FORBIDDEN:
            params = challenge_params(response.headers)
            if params.get("error") == "insufficient_scope":
                await response.aclose()
                _log.warning(
                    "mcp_oauth.insufficient_scope",
                    mcp_server=self._token.server,
                    scopes=challenged_scopes(params),
                )
                return self._reauth(request, "insufficient_scope")
        return response

    async def _send(self, request: httpx.Request, token: str) -> httpx.Response:
        request.headers["Authorization"] = f"Bearer {token}"
        return await self._inner.handle_async_request(request)

    def _reauth(self, request: httpx.Request, reason: str) -> httpx.Response:
        method, rpc_id, has_id = _rpc(request)
        if method != "tools/call" and reason not in self._token.reported and self._record_event is not None:
            # A tool call's failure is recorded by the session observer (with its call id).
            self._token.reported.add(reason)
            self._record_event("tool_needs_reauth", {"mcp_server": self._token.server, "reason": reason})
        if method is None:
            return httpx.Response(httpx.codes.UNAUTHORIZED, request=request)
        if not has_id:
            return httpx.Response(httpx.codes.ACCEPTED, request=request)
        body: dict[str, Any] = {"jsonrpc": "2.0", "id": rpc_id}
        if method == "tools/call":
            body["result"] = {"content": [{"type": "text", "text": MCP_REAUTH_MESSAGE}], "isError": True}
        else:
            body["error"] = {"code": REAUTH_RPC_ERROR, "message": MCP_REAUTH_MESSAGE}
        return httpx.Response(httpx.codes.OK, json=body, request=request)

    async def aclose(self) -> None:
        """Close the wrapped transport."""
        await self._inner.aclose()


def bearer_transport_factory(
    base: Callable[[], httpx.AsyncBaseTransport], token: ServerToken, *, record_event: RecordEvent | None
) -> Callable[[], httpx.AsyncBaseTransport]:
    """A ``transport_factory`` for :class:`~lkap_agent.tools.mcp_client.GuardedMCPServerHTTP`."""
    return lambda: ApiIssuedBearer(base(), token, record_event=record_event)


__all__ = [
    "EXPIRY_SKEW_S",
    "MCP_REAUTH_MESSAGE",
    "ApiIssuedBearer",
    "McpOAuthBinding",
    "McpOAuthTokenSource",
    "ServerToken",
    "bearer_transport_factory",
    "challenge_params",
    "challenged_scopes",
    "token_sha256",
]
