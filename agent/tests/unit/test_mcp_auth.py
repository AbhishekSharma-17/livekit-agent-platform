"""V5-16: the worker's api-issued bearer for MCP servers that sign in.

The MCP server and the api's token route are fakes (``httpx.MockTransport`` and an
in-memory token source); nothing touches the network. The end-to-end tests drive a real
``GuardedMCPServerHTTP`` through ``MCPToolset.setup()`` and a tool call, because what the
model hears depends on how the MCP client reacts to the transport's answers.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
from typing import Any, cast

import httpx
import pytest
from fakes.fake_api import FakeApi
from livekit.agents import ToolError, llm
from livekit.agents.voice.events import ToolCallEnded, ToolCallStarted, ToolExecutionUpdatedEvent
from lkap_contracts.agent_config import McpOAuthAccess, McpOAuthTokenIn, McpOAuthTokenOut
from lkap_contracts.tools import McpOAuthAuth, McpServerDefinition

from lkap_agent.config_client import ConfigClient, McpOAuthTokenError
from lkap_agent.observability import SessionObserver
from lkap_agent.settings import Settings
from lkap_agent.tools.declarative import build_mcp_toolsets
from lkap_agent.tools.mcp_auth import (
    MCP_REAUTH_MESSAGE,
    ApiIssuedBearer,
    McpOAuthBinding,
    ServerToken,
    challenge_params,
    challenged_scopes,
    token_sha256,
)

MCP_URL = "https://mcp.example.com/mcp"
FIRST = "at-first-Hn4"
SECOND = "at-second-Wc7"


@pytest.fixture(autouse=True)
def _worker_settings(settings: Settings) -> Settings:
    """`build_mcp_toolsets` reads `LKAP_MCP_ALLOWED_HOSTS` from the worker's settings (V5-09)."""
    return settings


class FakeTokenRoute:
    """``POST /internal/v1/tools/{id}/oauth/token``: hands out ``tokens`` in order, or refuses."""

    def __init__(self, *tokens: str, refuse: str | None = None, expires_in_s: int = 600) -> None:
        self.tokens = list(tokens)
        self.refuse = refuse
        self.expires_in_s = expires_in_s
        self.calls: list[tuple[str, McpOAuthTokenIn]] = []

    async def mcp_oauth_token(self, tool_id: str, request: McpOAuthTokenIn) -> McpOAuthTokenOut:
        self.calls.append((tool_id, request))
        await asyncio.sleep(0)
        if self.refuse is not None or not self.tokens:
            raise McpOAuthTokenError(self.refuse or "unavailable", "refused")
        expires_at = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=self.expires_in_s)
        return McpOAuthTokenOut(access_token=self.tokens.pop(0), expires_at=expires_at)


class FakeMcpServer:
    """A streamable-HTTP MCP server that accepts one bearer (``valid``) and can refuse scopes."""

    def __init__(self, valid: str = FIRST) -> None:
        self.valid = valid
        self.insufficient_scope = False
        self.requests: list[httpx.Request] = []
        self.sent: list[str] = []
        """The ``Authorization`` header of each request as it arrived (a retry reuses the object)."""

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def bearers(self) -> list[str]:
        return list(self.sent)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.sent.append(request.headers.get("authorization", ""))
        if request.url.host != "mcp.example.com":
            return httpx.Response(200, json={"reached": request.url.host})
        if request.headers.get("authorization") != f"Bearer {self.valid}":
            return httpx.Response(
                401,
                headers={
                    "WWW-Authenticate": 'Bearer resource_metadata="https://mcp.example.com/.well-known/prm"'
                },
            )
        if request.method != "POST":
            return httpx.Response(405)
        message = json.loads(request.content)
        if "id" not in message:
            return httpx.Response(202)
        if self.insufficient_scope and message["method"] == "tools/call":
            return httpx.Response(
                403,
                headers={
                    "WWW-Authenticate": (
                        'Bearer error="insufficient_scope", scope="issues:write admin:all", '
                        'resource_metadata="https://evil.example.org/prm"'
                    )
                },
            )
        result: dict[str, Any]
        if message["method"] == "initialize":
            result = {
                "protocolVersion": message["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake-mcp", "version": "1.0"},
            }
        elif message["method"] == "tools/list":
            result = {
                "tools": [
                    {"name": "list_issues", "description": "List issues", "inputSchema": {"type": "object"}}
                ]
            }
        elif message["method"] == "tools/call":
            result = {"content": [{"type": "text", "text": "3 open issues"}], "isError": False}
        else:
            result = {}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": message["id"], "result": result})


def _access(token: str | None = FIRST, *, expires_in_s: int = 600) -> McpOAuthAccess:
    return McpOAuthAccess(
        tool_id="tool-1",
        name="tracker",
        url=MCP_URL,
        access_token=token,
        expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(seconds=expires_in_s),
    )


def _binding(
    route: FakeTokenRoute, *, access: McpOAuthAccess | None = None
) -> tuple[McpOAuthBinding, list[Any]]:
    events: list[Any] = []
    binding = McpOAuthBinding(
        session_id="sess-1",
        source=route,
        tokens=[access or _access()],
        record_event=lambda kind, payload: events.append((kind, payload)),
    )
    return binding, events


def _bearer(
    server: FakeMcpServer, route: FakeTokenRoute, **access: Any
) -> tuple[httpx.AsyncClient, list[Any]]:
    binding, events = _binding(route, access=_access(**access) if access else None)
    token = ServerToken(binding.tokens[0], binding)
    transport = ApiIssuedBearer(server.transport(), token, record_event=binding.record_event)
    return httpx.AsyncClient(transport=transport), events


def _call(method: str = "tools/call", rpc_id: int | None = 7) -> dict[str, Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": {}}
    if rpc_id is not None:
        body["id"] = rpc_id
    return body


# ------------------------------------------------------------------- the transport
async def test_every_request_carries_the_api_issued_bearer() -> None:
    server, route = FakeMcpServer(), FakeTokenRoute()
    client, _ = _bearer(server, route)

    async with client:
        response = await client.post(MCP_URL, json=_call("tools/list"))

    assert response.status_code == 200
    assert server.bearers() == [f"Bearer {FIRST}"] and route.calls == []


async def test_a_401_asks_the_api_once_and_retries_once_with_the_new_token() -> None:
    server, route = FakeMcpServer(valid=SECOND), FakeTokenRoute(SECOND)
    client, _ = _bearer(server, route)

    async with client:
        response = await client.post(MCP_URL, json=_call("tools/list"))

    assert response.status_code == 200
    assert server.bearers() == [f"Bearer {FIRST}", f"Bearer {SECOND}"]
    ((tool_id, request),) = route.calls
    assert tool_id == "tool-1" and request.session_id == "sess-1"
    assert request.rejected_token_sha256 == token_sha256(FIRST)


async def test_a_second_401_is_final_and_answers_the_tool_call_with_the_admin_message() -> None:
    server, route = FakeMcpServer(valid="at-nobody-has-it"), FakeTokenRoute(SECOND, "at-third")
    client, _ = _bearer(server, route)

    async with client:
        response = await client.post(MCP_URL, json=_call())

    assert len(server.requests) == 2 and len(route.calls) == 1, "one renewal, one retry, never a third"
    body = response.json()
    assert body["id"] == 7 and body["result"]["isError"] is True
    assert body["result"]["content"][0]["text"] == MCP_REAUTH_MESSAGE


async def test_concurrent_401s_on_the_same_token_renew_it_once() -> None:
    server, route = FakeMcpServer(valid=SECOND), FakeTokenRoute(SECOND, "at-third")
    client, _ = _bearer(server, route)

    async with client:
        first, second = await asyncio.gather(
            client.post(MCP_URL, json=_call("tools/list", 1)),
            client.post(MCP_URL, json=_call("tools/list", 2)),
        )

    assert first.status_code == second.status_code == 200
    assert len(route.calls) == 1


async def test_a_token_about_to_expire_is_replaced_before_it_is_sent() -> None:
    server, route = FakeMcpServer(valid=SECOND), FakeTokenRoute(SECOND)
    client, _ = _bearer(server, route, expires_in_s=10)

    async with client:
        response = await client.post(MCP_URL, json=_call("tools/list"))

    assert response.status_code == 200
    assert server.bearers() == [f"Bearer {SECOND}"]
    assert route.calls[0][1].rejected_token_sha256 is None


async def test_no_token_in_the_resolved_config_is_fetched_before_the_first_request() -> None:
    server, route = FakeMcpServer(valid=SECOND), FakeTokenRoute(SECOND)
    client, _ = _bearer(server, route, token=None)

    async with client:
        await client.post(MCP_URL, json=_call("tools/list"))

    assert server.bearers() == [f"Bearer {SECOND}"] and len(route.calls) == 1


async def test_insufficient_scope_answers_with_the_admin_message_and_reads_no_url() -> None:
    server, route = FakeMcpServer(), FakeTokenRoute(SECOND)
    server.insufficient_scope = True
    client, events = _bearer(server, route)

    async with client:
        response = await client.post(MCP_URL, json=_call())

    assert route.calls == [], "no renewal: a new token has the same scopes"
    assert {r.url.host for r in server.requests} == {"mcp.example.com"} and len(server.requests) == 1
    text = response.json()["result"]["content"][0]["text"]
    assert text == MCP_REAUTH_MESSAGE and "http" not in text
    assert events == [], "a tool call's event comes from the session observer"


async def test_needs_reauth_answers_other_requests_with_an_rpc_error_and_backs_off() -> None:
    server, route = FakeMcpServer(valid="at-nobody-has-it"), FakeTokenRoute(refuse="needs_reauth")
    client, events = _bearer(server, route)

    async with client:
        listed = await client.post(MCP_URL, json=_call("tools/list", 3))
        notified = await client.post(MCP_URL, json=_call("notifications/initialized", None))
        again = await client.post(MCP_URL, json=_call("tools/list", 4))

    assert listed.json()["error"]["message"] == MCP_REAUTH_MESSAGE
    assert notified.status_code == 202
    assert again.json()["error"]["message"] == MCP_REAUTH_MESSAGE
    assert len(route.calls) == 1, "backs off instead of asking the api on every request"
    assert events == [("tool_needs_reauth", {"mcp_server": "tracker", "reason": "needs_reauth"})]


def test_the_challenge_is_parsed_and_only_scope_names_are_kept() -> None:
    headers = httpx.Headers(
        {"WWW-Authenticate": 'Bearer error="insufficient_scope", scope="a:read b/c <script>", realm=x'}
    )

    params = challenge_params(headers)

    assert params["error"] == "insufficient_scope" and params["realm"] == "x"
    assert challenged_scopes(params) == ["a:read", "b/c"]


# ------------------------------------------------------------------ end to end (SDK)
async def _toolset(server: FakeMcpServer, route: FakeTokenRoute, access: McpOAuthAccess | None = None) -> Any:
    binding, _ = _binding(route, access=access)
    definition = McpServerDefinition(name="tracker", url=MCP_URL, auth=McpOAuthAuth(credential_id=None))
    (toolset,) = build_mcp_toolsets([definition], transport_factory=server.transport, oauth=binding)
    await toolset.setup()
    return toolset


def _tool(toolset: Any) -> Any:
    (tool,) = [t for t in toolset.tools if t.id == "list_issues"]
    return tool


async def test_a_session_lists_and_calls_a_signed_in_server_with_the_bearer() -> None:
    server, route = FakeMcpServer(), FakeTokenRoute()
    toolset = await _toolset(server, route)
    try:
        result = await _tool(toolset)(raw_arguments={})
    finally:
        await toolset.aclose()

    assert "3 open issues" in str(result)
    assert set(server.bearers()) == {f"Bearer {FIRST}"}


async def test_after_a_revoke_the_next_call_fails_with_the_admin_message_and_the_server_stays_up() -> None:
    """The live check's last line: revoke, then the next turn hears the admin message."""
    server, route = FakeMcpServer(), FakeTokenRoute(refuse="needs_reauth")
    toolset = await _toolset(server, route)
    server.valid = "revoked-at-the-provider"
    try:
        for _ in range(2):  # the connection survives: the second call gets the same sentence
            with pytest.raises(ToolError) as caught:
                await _tool(toolset)(raw_arguments={})
            assert str(caught.value) == MCP_REAUTH_MESSAGE
    finally:
        await toolset.aclose()

    assert len(route.calls) == 1


async def test_insufficient_scope_on_a_call_is_the_spoken_safe_error() -> None:
    server, route = FakeMcpServer(), FakeTokenRoute()
    toolset = await _toolset(server, route)
    server.insufficient_scope = True
    try:
        with pytest.raises(ToolError) as caught:
            await _tool(toolset)(raw_arguments={})
    finally:
        await toolset.aclose()

    assert str(caught.value) == MCP_REAUTH_MESSAGE
    assert {r.url.host for r in server.requests} == {"mcp.example.com"}


async def test_the_observer_records_tool_needs_reauth_for_the_mcp_admin_message() -> None:
    api = FakeApi()
    observer = SessionObserver(session_id="sess-1", client=cast(Any, api))
    call = llm.FunctionCall(call_id="c9", name="list_issues", arguments="{}")

    observer._on_tool_execution(ToolExecutionUpdatedEvent(update=ToolCallStarted(function_call=call)))
    observer._on_tool_execution(
        ToolExecutionUpdatedEvent(
            update=ToolCallEnded(id="c9", call_id="c9", message=MCP_REAUTH_MESSAGE, status="error")
        )
    )
    await observer.flush()

    (event,) = api.events_of("tool_needs_reauth")
    assert event.payload == {"call_id": "c9", "tool": "list_issues"}


# ----------------------------------------------------------------- the token route
async def test_the_config_client_posts_the_token_route_and_maps_409() -> None:
    seen: list[httpx.Request] = []
    answers = iter(
        [
            httpx.Response(200, json={"access_token": SECOND, "expires_at": None}),
            httpx.Response(409, json={"error": {"code": "conflict"}}),
            httpx.Response(503, json={"error": {"code": "token_unavailable"}}),
        ]
    )

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return next(answers)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url="http://api.test")
    client = ConfigClient("http://api.test", "svc-token", client=http)
    request = McpOAuthTokenIn(session_id="sess-1", rejected_token_sha256=token_sha256(FIRST))

    issued = await client.mcp_oauth_token("tool-1", request)
    with pytest.raises(McpOAuthTokenError) as needs:
        await client.mcp_oauth_token("tool-1", request)
    with pytest.raises(McpOAuthTokenError) as later:
        await client.mcp_oauth_token("tool-1", request)
    await http.aclose()

    assert issued.access_token == SECOND
    assert needs.value.reason == "needs_reauth" and later.value.reason == "unavailable"
    assert seen[0].url.path == "/internal/v1/tools/tool-1/oauth/token"
    assert seen[0].headers["X-Service-Token"] == "svc-token"
    assert json.loads(seen[0].content) == request.model_dump()
    assert SECOND not in str(needs.value) + str(later.value)
