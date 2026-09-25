"""The MCP test connection behind ``POST /v1/tools/{id}/test`` (V5-09, research-v4 tools §4.3.7).

A minimal streamable-HTTP MCP client: ``initialize``, ``notifications/initialized``,
``tools/list`` (following ``nextCursor`` for a few pages), then a best-effort session
``DELETE``. The api carries no ``mcp`` SDK dependency (V5-14 adds its OAuth helpers), and
four JSON-RPC calls do not need one. The caller hands in the outbound client, which is
:func:`lkap_api.net_guard.guarded_http_client` in production: private addresses are refused
after DNS and redirects are never followed (a 3xx is reported, not followed).

A server may answer a request with a JSON body or with an event stream whose ``data:``
lines carry the JSON-RPC messages (MCP "Streamable HTTP" transport); both are read, and
an event stream is read only until the response with the request's id arrives.

The snapshot is capped (:data:`MAX_TOOLS`, :data:`MAX_DESCRIPTION_CHARS`,
:data:`MAX_SCHEMA_BYTES`): it is stored on the tool row and shown in the console, and a
hostile server must not be able to grow that row without bound.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Final, Literal

import httpx
from lkap_contracts.tools import McpToolSnapshot

from lkap_api.net_guard import blocked_cause

#: The protocol version LKAP offers in ``initialize`` (the server may answer an older one).
PROTOCOL_VERSION: Final[str] = "2025-06-18"
#: At most this many tools are kept from ``tools/list``.
MAX_TOOLS: Final[int] = 200
#: ``tools/list`` pages followed through ``nextCursor``.
MAX_PAGES: Final[int] = 5
#: A tool description is cut to this many characters.
MAX_DESCRIPTION_CHARS: Final[int] = 1000
#: A tool's ``inputSchema`` larger than this (serialised) is dropped from the snapshot.
MAX_SCHEMA_BYTES: Final[int] = 16_000
#: An event-stream answer longer than this is refused.
MAX_STREAM_BYTES: Final[int] = 2_000_000
#: The whole test (every request) gives up after this many per-request timeouts.
TOTAL_TIMEOUT_FACTOR: Final[int] = 3

McpTestReason = Literal["blocked_destination", "needs_auth", "unreachable", "protocol_error", "http_error"]

_ACCEPT: Final[str] = "application/json, text/event-stream"


class McpTestError(Exception):
    """The test connection failed; ``reason`` is machine-readable, the message value-free."""

    def __init__(self, reason: McpTestReason, message: str) -> None:
        """Keep the reason next to the message."""
        super().__init__(message)
        self.reason: McpTestReason = reason


@dataclass
class _Session:
    url: str
    headers: dict[str, str]
    client: httpx.AsyncClient
    timeout_s: float
    session_id: str | None = None
    protocol_version: str | None = None
    next_id: int = 1

    def request_headers(self) -> dict[str, str]:
        headers = {**self.headers, "Accept": _ACCEPT, "Content-Type": "application/json"}
        if self.session_id is not None:
            headers["Mcp-Session-Id"] = self.session_id
        if self.protocol_version is not None:
            headers["MCP-Protocol-Version"] = self.protocol_version
        return headers


def _check_status(response: httpx.Response) -> None:
    if response.status_code in (401, 403):
        raise McpTestError(
            "needs_auth", f"the server answered {response.status_code}: it needs sign-in or a key"
        )
    if 300 <= response.status_code < 400:
        raise McpTestError(
            "http_error",
            f"the server answered {response.status_code} with a redirect, which is never followed",
        )
    if response.status_code >= 400:
        raise McpTestError("http_error", f"the server answered {response.status_code}")


def _match(message: object, request_id: int) -> dict[str, Any] | None:
    if isinstance(message, list):
        for item in message:
            found = _match(item, request_id)
            if found is not None:
                return found
        return None
    if (
        isinstance(message, dict)
        and message.get("id") == request_id
        and ("result" in message or "error" in message)
    ):
        return message
    return None


async def _read_event_stream(response: httpx.Response, request_id: int) -> dict[str, Any] | None:
    data: list[str] = []
    size = 0
    async for line in response.aiter_lines():
        size += len(line) + 1
        if size > MAX_STREAM_BYTES:
            raise McpTestError("protocol_error", "the server's event stream is too large")
        if line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))
            continue
        if line == "" and data:
            try:
                found = _match(json.loads("\n".join(data)), request_id)
            except json.JSONDecodeError:
                found = None
            data = []
            if found is not None:
                return found
    if data:
        try:
            return _match(json.loads("\n".join(data)), request_id)
        except json.JSONDecodeError:
            return None
    return None


async def _call(session: _Session, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    request_id = session.next_id
    session.next_id += 1
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        body["params"] = params
    async with session.client.stream(
        "POST", session.url, headers=session.request_headers(), json=body, timeout=session.timeout_s
    ) as response:
        _check_status(response)
        if session.session_id is None and response.headers.get("mcp-session-id"):
            session.session_id = response.headers["mcp-session-id"]
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type == "text/event-stream":
            message = await _read_event_stream(response, request_id)
        else:
            raw = await response.aread()
            try:
                message = _match(json.loads(raw), request_id)
            except (json.JSONDecodeError, UnicodeDecodeError):
                message = None
    if message is None:
        raise McpTestError("protocol_error", f"the server sent no answer to {method}")
    if "error" in message:
        error = message["error"] if isinstance(message["error"], dict) else {}
        code = error.get("code")
        raise McpTestError("protocol_error", f"the server refused {method} (JSON-RPC error {code})")
    result = message.get("result")
    if not isinstance(result, dict):
        raise McpTestError("protocol_error", f"the server's answer to {method} is not an object")
    return result


async def _notify(session: _Session, method: str) -> None:
    response = await session.client.post(
        session.url,
        headers=session.request_headers(),
        json={"jsonrpc": "2.0", "method": method},
        timeout=session.timeout_s,
    )
    _check_status(response)


def _snapshot(tool: object) -> McpToolSnapshot | None:
    if not isinstance(tool, dict):
        return None
    name = tool.get("name")
    if not isinstance(name, str) or not 0 < len(name) <= 200:
        return None
    description = tool.get("description")
    schema = tool.get("inputSchema")
    if isinstance(schema, dict) and len(json.dumps(schema)) > MAX_SCHEMA_BYTES:
        schema = None
    return McpToolSnapshot(
        name=name,
        description=description[:MAX_DESCRIPTION_CHARS] if isinstance(description, str) else None,
        input_schema=schema if isinstance(schema, dict) else None,
    )


async def _list_tools(session: _Session) -> list[McpToolSnapshot]:
    initialized = await _call(
        session,
        "initialize",
        {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "lkap-api", "version": "0.1"},
        },
    )
    version = initialized.get("protocolVersion")
    session.protocol_version = version if isinstance(version, str) else PROTOCOL_VERSION
    await _notify(session, "notifications/initialized")
    tools: list[McpToolSnapshot] = []
    cursor: str | None = None
    for _page in range(MAX_PAGES):
        result = await _call(session, "tools/list", {"cursor": cursor} if cursor else None)
        listed = result.get("tools")
        if not isinstance(listed, list):
            raise McpTestError("protocol_error", "the server's tools/list answer has no tools")
        for item in listed:
            snapshot = _snapshot(item)
            if snapshot is not None and len(tools) < MAX_TOOLS:
                tools.append(snapshot)
        next_cursor = result.get("nextCursor")
        if not isinstance(next_cursor, str) or not next_cursor or len(tools) >= MAX_TOOLS:
            break
        cursor = next_cursor
    return tools


async def list_mcp_tools(
    url: str, headers: dict[str, str], *, client: httpx.AsyncClient, timeout_s: float
) -> list[McpToolSnapshot]:
    """Connect to an MCP server and return its tools (capped).

    Args:
        url: The server's streamable-HTTP endpoint (already checked against the MCP policy).
        headers: Request headers with secrets substituted (never logged or returned).
        client: The outbound client (guarded, no redirects).
        timeout_s: Per-request timeout in seconds.

    Returns:
        At most :data:`MAX_TOOLS` snapshots, in the server's order.

    Raises:
        McpTestError: Any failure, with a value-free message.
    """
    session = _Session(url=url, headers=headers, client=client, timeout_s=timeout_s)
    try:
        # Each request has its own timeout; this bounds the whole exchange, so a server
        # that drips an event stream cannot hold the admin request open.
        async with asyncio.timeout(timeout_s * TOTAL_TIMEOUT_FACTOR):
            return await _list_tools(session)
    except TimeoutError as exc:
        raise McpTestError("unreachable", "the server did not finish answering in time") from exc
    except httpx.TimeoutException as exc:
        raise McpTestError("unreachable", "the server did not answer in time") from exc
    except httpx.HTTPError as exc:
        blocked = blocked_cause(exc)
        if blocked is not None:
            raise McpTestError("blocked_destination", str(blocked)) from exc
        raise McpTestError("unreachable", f"could not reach the server ({type(exc).__name__})") from exc
    finally:
        if session.session_id is not None:
            try:
                await client.delete(url, headers=session.request_headers(), timeout=session.timeout_s)
            except httpx.HTTPError:
                pass


__all__ = [
    "MAX_TOOLS",
    "PROTOCOL_VERSION",
    "McpTestError",
    "McpTestReason",
    "list_mcp_tools",
]
