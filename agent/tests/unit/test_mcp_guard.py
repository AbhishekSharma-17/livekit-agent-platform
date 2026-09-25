"""Worker MCP servers: URL check at build time, guarded transport, no redirects.

research-v4 tools-and-integrations: MCP URLs used to bypass the SSRF check in
the worker, and livekit-agents 1.8.2's `MCPServerHTTP` builds its httpx client
with `follow_redirects=True`. The MCP endpoint is faked with
`httpx.MockTransport` (no network).
"""

from __future__ import annotations

import json
from typing import Any, cast

import httpx
import pytest
from fakes.fake_api import FakeApi, resolved_config
from fakes.fake_ctx import (
    FakeBackgroundRunner,
    FakeFrameBuffer,
    FakeKbClient,
    FakeStructuredLLM,
    FakeUiChannel,
)
from fakes.fake_llm import FakeLLM
from fakes.fake_room import FakeRoom
from livekit import rtc
from livekit.agents import AgentSession
from livekit.agents.llm.mcp import MCPServerHTTP, MCPToolset
from lkap_contracts.tools import McpServerDefinition, McpServerOrigin, ToolExecution
from test_main import FakeJobContext, _deps, _metadata

from lkap_agent.main import run_session
from lkap_agent.packs.loader import NullPack
from lkap_agent.platform_agent import PlatformAgent, SessionContext
from lkap_agent.tools._http_safety import GuardedTransport
from lkap_agent.tools.declarative import build_guarded_mcp_servers, build_mcp_toolsets
from lkap_agent.tools.mcp_client import GuardedMCPServerHTTP

PUBLIC_URL = "https://mcp.example.com/mcp"


class _FakeMcpEndpoint:
    """A minimal streamable-HTTP MCP server, or a redirect, behind `httpx.MockTransport`."""

    def __init__(self, *, redirect_to: str | None = None) -> None:
        self.redirect_to = redirect_to
        self.requests: list[httpx.Request] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def hosts(self) -> set[str]:
        return {request.url.host for request in self.requests}

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host != "mcp.example.com":
            return httpx.Response(200, json={"reached": request.url.host})
        if self.redirect_to is not None:
            return httpx.Response(302, headers={"Location": self.redirect_to})
        if request.method != "POST":
            return httpx.Response(405)
        message = json.loads(request.content)
        if "id" not in message:
            return httpx.Response(202)
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
                    {
                        "name": "lookup_policy",
                        "description": "Look up a policy",
                        "inputSchema": {"type": "object", "properties": {}},
                    }
                ]
            }
        else:
            result = {}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": message["id"], "result": result})


def _definition(url: str = PUBLIC_URL, name: str = "tools-server", **extra: Any) -> McpServerDefinition:
    return McpServerDefinition(name=name, url=url, **extra)


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/mcp",
        "http://metadata.google.internal/mcp",
        "http://localhost:8080/mcp",
        "http://127.0.0.1/mcp",
        "http://10.0.0.5/mcp",
        "http://[::1]/mcp",
        "http://2130706433/mcp",
        "file:///etc/passwd",
    ],
)
def test_build_mcp_servers_private_or_metadata_url_is_refused_and_skipped(url: str) -> None:
    skipped: list[tuple[str, str]] = []

    servers = build_guarded_mcp_servers(
        [_definition(url=url, name="internal")], on_skipped=lambda d, reason: skipped.append((d.name, reason))
    )

    assert servers == []
    assert len(skipped) == 1
    name, reason = skipped[0]
    assert name == "internal"
    assert "private or local" in reason or "unsupported URL scheme" in reason


def test_build_mcp_servers_skip_reason_never_echoes_the_url_path_or_query() -> None:
    skipped: list[str] = []

    build_guarded_mcp_servers(
        [_definition(url="http://169.254.169.254/mcp?token=resolved-secret")],
        on_skipped=lambda _d, reason: skipped.append(reason),
    )

    assert skipped and "resolved-secret" not in skipped[0]


def test_build_mcp_servers_mixed_list_keeps_the_public_server() -> None:
    skipped: list[str] = []

    servers = build_guarded_mcp_servers(
        [_definition(url="http://169.254.169.254/mcp", name="metadata"), _definition(name="public")],
        on_skipped=lambda d, _reason: skipped.append(d.name),
    )

    assert skipped == ["metadata"]
    assert len(servers) == 1
    assert isinstance(servers[0], GuardedMCPServerHTTP)
    assert servers[0].url == PUBLIC_URL


def test_build_mcp_servers_without_a_callback_still_skips() -> None:
    assert build_guarded_mcp_servers([_definition(url="http://127.0.0.1/mcp")]) == []


def test_build_mcp_servers_passes_the_definition_through() -> None:
    (server,) = build_guarded_mcp_servers(
        [
            _definition(
                headers={"Authorization": "Bearer resolved-secret"},
                allowed_tools=["do_thing"],
                timeout_s=7,
                sse_read_timeout_s=120,
            )
        ]
    )

    assert isinstance(server, GuardedMCPServerHTTP)
    assert isinstance(server, MCPServerHTTP)
    assert server.url == PUBLIC_URL
    assert server.headers == {"Authorization": "Bearer resolved-secret"}
    assert server._allowed_tools == {"do_thing"}
    assert server._timeout == 7
    assert server._sse_read_timeout == 120
    assert server._use_streamable_http is True


async def test_guarded_client_uses_the_guarded_transport_and_no_redirects_by_default() -> None:
    (server,) = build_guarded_mcp_servers([_definition()])

    async with server._create_http_client() as client:
        assert client.follow_redirects is False
        assert isinstance(client._transport, GuardedTransport)


async def test_guarded_client_3xx_from_an_mcp_server_is_not_followed() -> None:
    endpoint = _FakeMcpEndpoint(redirect_to="http://169.254.169.254/latest/meta-data/")
    (server,) = build_guarded_mcp_servers([_definition()], transport_factory=endpoint.transport)

    async with server._create_http_client() as client:
        response = await client.post(PUBLIC_URL, json={"jsonrpc": "2.0", "id": 1, "method": "ping"})

    assert response.status_code == 302
    assert len(endpoint.requests) == 1
    assert endpoint.hosts() == {"mcp.example.com"}


async def test_guarded_server_initialize_does_not_follow_a_cross_origin_redirect() -> None:
    endpoint = _FakeMcpEndpoint(redirect_to="http://169.254.169.254/latest/meta-data/")
    (server,) = build_guarded_mcp_servers([_definition()], transport_factory=endpoint.transport)

    try:
        # The MCP SDK's task group wraps the `httpx.HTTPStatusError` for the refused redirect.
        with pytest.raises(ExceptionGroup) as excinfo:
            await server.initialize()
    finally:
        await server.aclose()

    assert excinfo.group_contains(httpx.HTTPStatusError, match="not followed")
    assert len(endpoint.requests) == 1
    assert endpoint.hosts() == {"mcp.example.com"}


async def test_guarded_server_public_url_connects_and_lists_tools() -> None:
    endpoint = _FakeMcpEndpoint()
    (server,) = build_guarded_mcp_servers(
        [_definition(headers={"Authorization": "Bearer resolved-secret"})],
        transport_factory=endpoint.transport,
    )

    try:
        await server.initialize()
        tools = await server.list_tools()
    finally:
        await server.aclose()

    assert [tool.info.name for tool in tools] == ["lookup_policy"]
    posts = [r for r in endpoint.requests if r.method == "POST"]
    assert posts and all(r.headers["Authorization"] == "Bearer resolved-secret" for r in posts)
    assert endpoint.hosts() == {"mcp.example.com"}


async def test_run_session_private_mcp_url_records_an_event_and_the_session_still_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LIVEKIT_API_KEY", "test-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "test-secret")
    monkeypatch.setenv("LIVEKIT_URL", "wss://example.livekit.cloud")
    built: list[list[Any]] = []

    def _builder(defs: list[McpServerDefinition], **kwargs: Any) -> list[Any]:
        servers = build_mcp_toolsets(defs, **kwargs)
        built.append(servers)
        return servers

    api = FakeApi(resolved_config(tools=[_definition(url="http://169.254.169.254/mcp?token=x", name="meta")]))
    ctx = FakeJobContext(_metadata())

    await run_session(ctx, _deps(api, mcp_servers_builder=_builder))
    await ctx.fire_shutdown("done")

    assert ctx.connected == 1
    assert "session_started" in api.event_types()
    assert built == [[]]
    (event,) = [e for e in api.events_of("error") if e.payload.get("mcp_server") == "meta"]
    assert "MCP server 'meta' skipped" in event.payload["message"]
    assert "private or local" in event.payload["message"]
    assert "token=x" not in event.payload["message"]


# ------------------------------------------------ V4-12: MCPToolset over the guarded server


def test_build_mcp_toolsets_wraps_the_guarded_server_and_still_refuses_private_urls() -> None:
    skipped: list[str] = []

    toolsets = build_mcp_toolsets(
        [_definition(url="http://169.254.169.254/mcp", name="metadata"), _definition(name="public")],
        on_skipped=lambda d, _reason: skipped.append(d.name),
    )

    assert skipped == ["metadata"]
    (toolset,) = toolsets
    assert isinstance(toolset, MCPToolset)
    assert isinstance(toolset._mcp_server, GuardedMCPServerHTTP)
    assert toolset._mcp_server.url == PUBLIC_URL


async def test_the_toolset_s_client_is_still_guarded_and_never_follows_redirects() -> None:
    endpoint = _FakeMcpEndpoint(redirect_to="http://169.254.169.254/latest/meta-data/")
    (toolset,) = build_mcp_toolsets([_definition()], transport_factory=endpoint.transport)

    async with toolset._mcp_server._create_http_client() as client:
        assert client.follow_redirects is False
        response = await client.post(PUBLIC_URL, json={"jsonrpc": "2.0", "id": 1, "method": "ping"})

    assert response.status_code == 302
    assert endpoint.hosts() == {"mcp.example.com"}


class _Session:
    """Weak-referenceable session stand-in for the policy registry."""


async def test_a_platform_agent_lists_the_toolset_s_tools_to_the_llm_after_start() -> None:
    """The SDK sets the toolsets up itself (`AgentActivity._setup_toolsets` awaits `setup()`)."""
    endpoint = _FakeMcpEndpoint()
    definition = _definition(
        tool_options={"lookup_policy": ToolExecution(mode="background", report_progress=True)}
    )
    toolsets = build_mcp_toolsets([definition], transport_factory=endpoint.transport)
    config = resolved_config(channel="text")
    fake_llm = FakeLLM(["Hello."])
    session: AgentSession[Any] = AgentSession(llm=fake_llm)
    ctx = SessionContext(
        session_id=config.session_id,
        agent_id=config.agent_id,
        pipeline_mode=config.config.pipeline.mode,
        config=config.config,
        session=session,
        room=cast(rtc.Room, FakeRoom()),
        ui=FakeUiChannel(),
        frames=FakeFrameBuffer(),
        kb=FakeKbClient(),
        workflow_llm=FakeStructuredLLM(),
        background=FakeBackgroundRunner(),
        log=None,
    )
    agent = PlatformAgent(ctx=ctx, pack=NullPack(), mcp_toolsets=toolsets, has_tts=False)

    await session.start(agent)
    await session.run(user_input="Hi")

    listed = {tool.id: tool for tool in fake_llm.calls[-1].tools}
    assert "lookup_policy" in listed
    assert listed["lookup_policy"].info.on_duplicate == "confirm"
    assert endpoint.hosts() == {"mcp.example.com"}
    await session.aclose()


# --------------------------------------------------- V5-47: provider-provisioned servers
_ORIGIN = McpServerOrigin(kind="router", remote_id="trs_1")


@pytest.mark.parametrize(
    "url",
    [
        "https://mcp.example.com/tool_router/trs_1/mcp",
        "http://backend.composio.dev/tool_router/trs_1/mcp",
        "https://backend.composio.dev:8443/tool_router/trs_1/mcp",
        "https://user:pw@backend.composio.dev/tool_router/trs_1/mcp",
        "https://backend.composio.dev.example.com/mcp",
    ],
)
def test_an_origin_tagged_server_off_the_composio_host_is_refused(url: str) -> None:
    skipped: list[tuple[str, str]] = []

    servers = build_guarded_mcp_servers(
        [_definition(url=url, name="composio_tool_finder", origin=_ORIGIN)],
        on_skipped=lambda d, reason: skipped.append((d.name, reason)),
    )

    assert servers == []
    assert [name for name, _ in skipped] == ["composio_tool_finder"]
    assert "backend.composio.dev" in skipped[0][1]


def test_an_origin_tagged_server_on_the_composio_host_is_built() -> None:
    (server,) = build_guarded_mcp_servers(
        [
            _definition(
                url="https://backend.composio.dev/tool_router/trs_1/mcp",
                name="composio_tool_finder",
                headers={"x-api-key": "ak_placeholder_resolved"},
                origin=_ORIGIN,
            )
        ]
    )

    assert isinstance(server, GuardedMCPServerHTTP)
    assert server.url == "https://backend.composio.dev/tool_router/trs_1/mcp"


def test_a_plain_server_elsewhere_is_unaffected_by_the_pin() -> None:
    (server,) = build_guarded_mcp_servers([_definition()])

    assert server.url == PUBLIC_URL
