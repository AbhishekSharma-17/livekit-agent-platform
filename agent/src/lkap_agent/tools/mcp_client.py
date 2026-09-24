"""`MCPServerHTTP` with the SSRF-guarded transport and no redirect following.

Verified against livekit-agents 1.8.2 (`livekit/agents/llm/mcp.py`) and
mcp 1.30.0:

* `MCPServerHTTP.__init__` has no `auth`/`transport` kwarg; the httpx client is
  built by `_create_http_client(headers=None, timeout=None, auth=None)`, which
  hard-codes `follow_redirects=True`. `client_streams()` calls it with no
  arguments on the streamable-HTTP path and passes it as
  `httpx_client_factory` on the SSE path, so overriding that one method
  covers both.
* The override builds the client with `follow_redirects=False` and
  :func:`~lkap_agent.tools._http_safety.guarded_transport`, whose network
  backend resolves the host, refuses any private answer and connects to the
  checked address (DNS rebinding included).
* mcp 1.30.0's streamable-HTTP transport sends every request with
  `follow_redirects=False` itself and follows only a redirect that stays on the
  endpoint's origin (`mcp.shared._httpx_utils.stream_within_origin`); every
  other 3xx surfaces as `httpx.HTTPStatusError`. Such a same-origin hop still
  connects through the guarded transport.

Importing this module imports `livekit.agents.llm.mcp`, which needs the
optional `mcp` package: :func:`lkap_agent.tools.declarative.build_mcp_servers`
imports it lazily, after probing for the extra.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

import httpx
from livekit.agents.llm.mcp import MCPServerHTTP

from lkap_agent.tools._http_safety import guarded_transport

TransportFactory = Callable[[], httpx.AsyncBaseTransport]


class GuardedMCPServerHTTP(MCPServerHTTP):
    """An MCP HTTP server whose client never follows redirects and never reaches a private address."""

    def __init__(
        self,
        url: str,
        transport_type: Literal["sse", "streamable_http"] | None = None,
        allowed_tools: list[str] | None = None,
        headers: dict[str, Any] | None = None,
        timeout: float = 5,
        sse_read_timeout: float = 60 * 5,
        client_session_timeout_seconds: float = 5,
        *,
        transport_factory: TransportFactory = guarded_transport,
    ) -> None:
        """Same arguments as `MCPServerHTTP`, plus the transport seam.

        Args:
            url: The MCP endpoint (already checked by `check_url_public`).
            transport_type: `"streamable_http"` or `"sse"`.
            allowed_tools: Tool names to expose; `None` exposes all.
            headers: Static request headers (secrets already substituted by the api).
            timeout: Connect timeout in seconds.
            sse_read_timeout: Read timeout for the event stream in seconds.
            client_session_timeout_seconds: MCP session request timeout.
            transport_factory: Builds each client's transport; tests pass a fake.
        """
        super().__init__(
            url=url,
            transport_type=transport_type,
            allowed_tools=allowed_tools,
            headers=headers,
            timeout=timeout,
            sse_read_timeout=sse_read_timeout,
            client_session_timeout_seconds=client_session_timeout_seconds,
        )
        self._transport_factory = transport_factory

    def _create_http_client(
        self,
        headers: dict[str, Any] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
    ) -> httpx.AsyncClient:
        """The base client, with `follow_redirects=False` and the guarded transport."""
        kwargs: dict[str, Any] = {
            "follow_redirects": False,
            "timeout": timeout
            if timeout is not None
            else httpx.Timeout(self._timeout, read=self._sse_read_timeout),
            "headers": headers if headers is not None else self._headers,
            "transport": self._transport_factory(),
        }
        if auth is not None:
            kwargs["auth"] = auth
        self._http_client = httpx.AsyncClient(**kwargs)
        return self._http_client
