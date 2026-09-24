"""Remote streamable-HTTP mode (V3-06, ``AGENT-ACCESS.md`` §9, R-V3-24).

The service (:func:`lkap_mcp.http.build_app`) runs under a real uvicorn on a
random loopback port; its sessions talk to the real scratch api in-process
(``httpx.ASGITransport``, conftest's ``app``). Positive paths use the SDK's
streamable-HTTP client (``mcp.client.streamable_http`` + ``ClientSession``);
transport-level refusals (401/403/404/413/429) use raw ``httpx`` requests,
because the SDK client raises out of its task group on a non-2xx answer.

Only external boundaries are faked: LiveKit (``connection_fakes.fake_livekit``
for connection tests, ``FakeRoomTransport`` for the chat room).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
import uvicorn
from auth_helpers import make_api_key, make_workspace
from conftest import (
    API_BASE,
    BUILDER_SCOPES,
    OPERATOR_SCOPES,
    READ_ONLY_SCOPES,
    all_log_text,
    dumped,
)
from connection_fakes import KEY_B, SECRET_B, fake_livekit
from fastapi import FastAPI
from lkap_api.db.session import Database
from lkap_testing.fake_room import FakeRoomTransport
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import LATEST_PROTOCOL_VERSION, Implementation
from sse_starlette.sse import AppStatus
from starlette.applications import Starlette

from lkap_mcp.chat import tools as chat_tools
from lkap_mcp.client import LkapClient
from lkap_mcp.http import (
    CallLimits,
    HttpSessions,
    HttpSettings,
    NoSession,
    OriginPolicy,
    SessionClient,
    StartupRefused,
    _limited_call,
    build_app,
    build_session_server,
    check_startup,
    service_settings,
)
from lkap_mcp.registry import ServerContext, ToolSpec
from lkap_mcp.results import ToolResult
from lkap_mcp.settings import McpSettings

CLIENT_NAME = "http-test-client"
CHAT_TOOLS = {"chat_start", "chat_send", "chat_rewind", "chat_end"}
ACCEPT = "application/json, text/event-stream"


# ---------------------------------------------------------------------------- the running service
class Clock:
    """A controllable monotonic clock (idle sweeps)."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@dataclass
class Service:
    """A running HTTP service: its url, app and session registry."""

    base: str
    app: Starlette
    clock: Clock

    @property
    def url(self) -> str:
        return f"{self.base}/mcp"

    @property
    def sessions(self) -> HttpSessions:
        registry: HttpSessions = self.app.state.sessions
        return registry


@contextlib.asynccontextmanager
async def _running(app: Starlette) -> AsyncIterator[str]:
    # sse_starlette latches a process-global "server is exiting" flag when a uvicorn server
    # stops, after which every SSE response closes at once. One process runs one server in
    # production; tests start one per test, so the flag is cleared around each.
    AppStatus.should_exit = False
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=0,
        log_config=None,
        access_log=False,
        lifespan="on",
        timeout_graceful_shutdown=2,
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(500):
            if server.started or task.done():
                break
            await asyncio.sleep(0.01)
        if task.done():
            task.result()
        assert server.started, "uvicorn did not start"
        port = server.servers[0].sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 15)
        AppStatus.should_exit = False


ServiceFactory = Callable[..., Any]


@pytest.fixture
def service(app: FastAPI) -> ServiceFactory:
    """``async with service(**overrides) as svc`` → a running HTTP service on the scratch api.

    Overrides naming an :class:`HttpSettings` field go there; the rest to :class:`McpSettings`.
    """

    @contextlib.asynccontextmanager
    async def start(**overrides: Any) -> AsyncIterator[Service]:
        http_fields = {k: overrides.pop(k) for k in list(overrides) if k in HttpSettings.model_fields}
        settings = McpSettings(api_url=API_BASE, **overrides)
        clock = Clock()
        mcp_app = build_app(
            settings, HttpSettings(**http_fields), api_transport=httpx.ASGITransport(app=app), clock=clock
        )
        async with _running(mcp_app) as base:
            yield Service(base, mcp_app, clock)

    return start


@contextlib.asynccontextmanager
async def mcp_client(url: str, key: str, name: str = CLIENT_NAME) -> AsyncIterator[ClientSession]:
    """The SDK's streamable-HTTP client with the key as the bearer (the way Claude Code connects)."""
    http = httpx.AsyncClient(
        headers={"Authorization": f"Bearer {key}"}, timeout=httpx.Timeout(30.0, read=120.0)
    )
    async with (
        http,
        streamable_http_client(url, http_client=http) as (read, write, _),
        ClientSession(read, write, client_info=Implementation(name=name, version="0")) as session,
    ):
        await session.initialize()
        yield session


async def call(session: ClientSession, tool: str, /, **arguments: Any) -> dict[str, Any]:
    """Call a tool; return its structured ``ToolResult``."""
    result = await session.call_tool(tool, arguments)
    assert result.structuredContent is not None, result
    structured: dict[str, Any] = result.structuredContent
    return structured


async def tool_names(session: ClientSession) -> set[str]:
    return {tool.name for tool in (await session.list_tools()).tools}


# ---------------------------------------------------------------------------- raw requests
def sse_messages(response: httpx.Response) -> list[dict[str, Any]]:
    """The JSON-RPC messages of an SSE (or JSON) response body."""
    if response.headers.get("content-type", "").startswith("application/json"):
        body = response.json()
        return body if isinstance(body, list) else [body]
    messages = []
    for line in response.text.splitlines():
        if line.startswith("data:") and line[5:].strip():
            messages.append(json.loads(line[5:].strip()))
    return messages


@dataclass
class Raw:
    """Hand-made streamable-HTTP requests (status codes a real client would get)."""

    url: str
    key: str | None
    next_id: int = field(default=1)

    def headers(self, session: str | None = None, **extra: str) -> dict[str, str]:
        headers = {"Accept": ACCEPT, "Content-Type": "application/json"}
        if self.key is not None:
            headers["Authorization"] = f"Bearer {self.key}"
        if session is not None:
            headers["Mcp-Session-Id"] = session
            headers["Mcp-Protocol-Version"] = LATEST_PROTOCOL_VERSION
        headers.update(extra)
        return headers

    async def post(
        self, payload: Any, *, session: str | None = None, headers: dict[str, str] | None = None
    ) -> httpx.Response:
        merged = self.headers(session)
        merged.update(headers or {})
        async with httpx.AsyncClient(timeout=30.0) as http:
            return await http.post(self.url, content=json.dumps(payload), headers=merged)

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.next_id += 1
        return {"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params or {}}

    def init_payload(self) -> dict[str, Any]:
        return self.request(
            "initialize",
            {
                "protocolVersion": LATEST_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "raw-client", "version": "0"},
            },
        )

    async def initialize(self) -> str:
        response = await self.post(self.init_payload())
        assert response.status_code == 200, response.text
        session = response.headers["mcp-session-id"]
        done = await self.post({"jsonrpc": "2.0", "method": "notifications/initialized"}, session=session)
        assert done.status_code == 202, done.text
        return session

    async def call(self, session: str, tool: str, **arguments: Any) -> httpx.Response:
        return await self.post(
            self.request("tools/call", {"name": tool, "arguments": arguments}), session=session
        )


def refusal(response: httpx.Response) -> dict[str, Any]:
    body: dict[str, Any] = response.json()
    data: dict[str, Any] = body["error"]["data"]
    return data


# ---------------------------------------------------------------------------- scratch data
async def builder_key(database: Database, scopes: list[str] | None = None, **kw: Any) -> tuple[str, str]:
    return await make_api_key(database, scopes or BUILDER_SCOPES, **kw)


@contextlib.asynccontextmanager
async def api_client(app: FastAPI, headers: dict[str, str]) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=API_BASE, headers=headers
    ) as client:
        yield client


async def register_ready_worker(app: FastAPI) -> None:
    async with api_client(app, {"X-Service-Token": "test-service"}) as service:
        response = await service.post(
            "/internal/v1/workers/register",
            json={
                "connection_id": None,
                "instance_key": "host:101",
                "image": "slim",
                "sdk_version": "1.8.2",
                "installed_provider_ids": [],
                "pack_ids": ["generic"],
                "managed_by": "external",
            },
        )
    assert response.status_code in (200, 201), response.text


async def draft_agent(
    admin: httpx.AsyncClient, name: str = "Claims intake", workspace: str | None = None
) -> str:
    from lkap_contracts.agent_config import AgentConfig, PipelineConfig, ProviderRef

    config = AgentConfig(
        instructions="You take first-notice-of-loss claims.",
        pipeline=PipelineConfig(
            mode="cascaded",
            stt=ProviderRef(provider_id="livekit-inference-stt"),
            llm=ProviderRef(provider_id="livekit-inference-llm"),
            tts=ProviderRef(provider_id="livekit-inference-tts"),
        ),
    )
    headers = {"X-Workspace": workspace} if workspace else None
    response = await admin.post(
        "/v1/agents", json={"name": name, "config": config.model_dump(mode="json")}, headers=headers
    )
    assert response.status_code == 201, response.text
    agent_id: str = response.json()["id"]
    return agent_id


@dataclass
class Rooms:
    """A room factory handing out `FakeRoomTransport`s and remembering them."""

    made: list[FakeRoomTransport] = field(default_factory=list)

    def __call__(self) -> FakeRoomTransport:
        room = FakeRoomTransport()
        self.made.append(room)
        return room


@pytest.fixture
def rooms(monkeypatch: pytest.MonkeyPatch) -> Rooms:
    factory = Rooms()
    monkeypatch.setattr(chat_tools, "TRANSPORT_FACTORY", factory)
    monkeypatch.setattr(chat_tools, "SETTLE_S", 0.05)
    return factory


async def open_chat(session: ClientSession, agent_id: str) -> dict[str, Any]:
    started = await call(session, "chat_start", agent_id_or_slug=agent_id, wait_for_greeting=False)
    assert started["ok"] is True, started
    data: dict[str, Any] = started["data"]
    return data


async def wait_until(predicate: Callable[[], bool], within_s: float = 5.0) -> None:
    for _ in range(int(within_s / 0.02)):
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not reached")


# ============================================================================ startup and config
@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    for name in (
        "LKAP_API_KEY",
        "LKAP_SERVICE_TOKEN",
        "LKAP_ADMIN_TOKEN",
        "LKAP_MASTER_KEY",
        "LKAP_MCP_PUBLIC_URL",
        "LKAP_ENV",
        "LKAP_MCP_HTTP",
        "LKAP_MCP_TRANSPORT",
    ):
        monkeypatch.delenv(name, raising=False)
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    try:
        yield monkeypatch
    finally:
        root.handlers[:] = handlers
        root.setLevel(level)


def test_main_http_in_prod_with_a_plain_http_public_url_refuses_to_start(
    clean_env: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from lkap_mcp.__main__ import main

    clean_env.setenv("LKAP_ENV", "prod")
    clean_env.setenv("LKAP_MCP_PUBLIC_URL", "http://api.example.com/mcp")

    assert main(["--http"]) == 2
    assert "https://" in capsys.readouterr().err


def test_main_http_env_flag_runs_the_same_checks(
    clean_env: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from lkap_mcp.__main__ import main

    clean_env.setenv("LKAP_ENV", "prod")
    clean_env.setenv("LKAP_MCP_HTTP", "1")

    assert main([]) == 2  # no LKAP_API_KEY needed in HTTP mode; refused for the missing https url
    assert "LKAP_MCP_PUBLIC_URL" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("env", "public", "refused"),
    [
        ("prod", None, True),
        ("prod", "http://api.example.com/mcp", True),
        ("prod", "https://api.example.com/mcp", False),
        ("dev", None, False),
        ("dev", "http://127.0.0.1:8090/mcp", False),
        ("dev", "api.example.com/mcp", True),  # not an absolute url
    ],
)
def test_check_startup_public_url_rules(env: str, public: str | None, refused: bool) -> None:
    settings = McpSettings(api_url=API_BASE, public_url=public)
    http = HttpSettings(env=env)

    if refused:
        with pytest.raises(StartupRefused):
            check_startup(settings, http, {})
    else:
        check_startup(settings, http, {})


@pytest.mark.parametrize(
    "name", ["LKAP_API_KEY", "LKAP_SERVICE_TOKEN", "LKAP_ADMIN_TOKEN", "LKAP_MASTER_KEY"]
)
def test_check_startup_refuses_a_key_or_platform_token_in_the_service_env(name: str) -> None:
    settings = McpSettings(api_url=API_BASE)

    with pytest.raises(StartupRefused, match=name):
        check_startup(settings, HttpSettings(env="dev"), {name: "x" * 20})


def test_service_settings_force_http_transport_and_drop_any_process_key_or_workspace() -> None:
    settings = service_settings(
        McpSettings(api_url=API_BASE, api_key="lkap_process_key_must_not_be_used", workspace="acme")
    )

    assert settings.http_mode is True and settings.api_key is None and settings.workspace is None


@pytest.mark.parametrize(
    ("host", "origin", "env", "ok"),
    [
        ("api.example.com", None, "prod", True),
        ("api.example.com:443", "https://api.example.com", "prod", True),
        ("API.EXAMPLE.COM", "https://api.example.com/", "prod", True),
        ("evil.example", None, "prod", False),  # DNS rebinding: wrong Host
        ("api.example.com", "https://evil.example", "prod", False),  # foreign Origin
        ("api.example.com", "http://api.example.com", "prod", False),  # scheme differs
        ("127.0.0.1:8090", None, "prod", False),  # no loopback allowance in prod
        ("127.0.0.1:8090", None, "dev", True),
        ("localhost:5555", "http://localhost:5555", "dev", True),
        ("[::1]:8090", None, "dev", True),
        ("evil.example", None, "dev", False),
        ("localhost", "null", "dev", False),
    ],
)
def test_origin_policy_host_and_origin(host: str, origin: str | None, env: str, ok: bool) -> None:
    policy = OriginPolicy.build(
        McpSettings(api_url=API_BASE, public_url="https://api.example.com/mcp"), HttpSettings(env=env)
    )

    assert (policy.host_ok(host) and policy.origin_ok(origin)) is ok


# ============================================================================ auth at the transport
async def test_healthz_is_unauthenticated_and_ignores_the_host(service: ServiceFactory) -> None:
    async with service() as svc, httpx.AsyncClient() as http:
        response = await http.get(f"{svc.base}/healthz", headers={"Host": "anything.example"})

    assert response.status_code == 200 and response.json() == {"status": "ok"}


@pytest.mark.parametrize(
    "authorization",
    [None, "Bearer not-an-lkap-key", "Basic lkap_abc", "Bearer", "lkap_without_scheme"],
)
async def test_missing_or_non_lkap_bearer_is_401_before_any_mcp_message(
    service: ServiceFactory, authorization: str | None
) -> None:
    async with service() as svc:
        raw = Raw(svc.url, key=None)
        headers = {"Authorization": authorization} if authorization else {}
        response = await raw.post(raw.init_payload(), headers=headers)

        assert response.status_code == 401
        assert response.headers["www-authenticate"].startswith("Bearer")
        assert refusal(response)["code"] == "unauthorized"
        assert svc.sessions.sessions == []


async def test_unknown_lkap_key_is_401_at_initialize_and_opens_no_session(service: ServiceFactory) -> None:
    async with service() as svc:
        response = await Raw(svc.url, key="lkap_" + "0" * 40).post(Raw(svc.url, None).init_payload())

        assert response.status_code == 401 and refusal(response)["code"] == "unauthorized"
        assert svc.sessions.sessions == []


@pytest.mark.parametrize(
    ("headers", "code"),
    [
        ({"Origin": "https://evil.example"}, "forbidden_origin"),
        ({"Host": "evil.example"}, "forbidden_host"),
        ({"Host": "evil.example:8090"}, "forbidden_host"),
    ],
)
async def test_foreign_origin_or_wrong_host_is_403(
    service: ServiceFactory, database: Database, headers: dict[str, str], code: str
) -> None:
    _, raw_key = await builder_key(database)
    async with service() as svc:
        raw = Raw(svc.url, raw_key)
        opening = await raw.post(raw.init_payload(), headers=headers)
        session = await raw.initialize()
        on_session = await raw.post(raw.request("tools/list"), session=session, headers=headers)

    assert opening.status_code == 403 and refusal(opening)["code"] == code
    assert on_session.status_code == 403 and refusal(on_session)["code"] == code


async def test_public_url_origin_is_accepted(service: ServiceFactory, database: Database) -> None:
    _, raw_key = await builder_key(database)
    async with service() as svc:
        # The service's own public origin (dev: the loopback url it is reached on).
        raw = Raw(svc.url, raw_key)
        response = await raw.post(raw.init_payload(), headers={"Origin": svc.base})

    assert response.status_code == 200


async def test_a_request_on_a_session_with_a_different_key_is_403(
    service: ServiceFactory, database: Database
) -> None:
    _, key_a = await builder_key(database)
    _, key_b = await builder_key(database)
    async with service() as svc:
        session = await Raw(svc.url, key_a).initialize()
        other = Raw(svc.url, key_b)
        response = await other.post(other.request("tools/list"), session=session)
        still_mine = await Raw(svc.url, key_a).post(
            Raw(svc.url, key_a).request("tools/list"), session=session
        )

    assert response.status_code == 403 and refusal(response)["code"] == "session_key_mismatch"
    assert still_mine.status_code == 200


async def test_unknown_session_id_is_404_and_get_without_a_session_is_400(
    service: ServiceFactory, database: Database
) -> None:
    _, raw_key = await builder_key(database)
    async with service() as svc:
        raw = Raw(svc.url, raw_key)
        unknown = await raw.post(raw.request("tools/list"), session="f" * 64)
        async with httpx.AsyncClient() as http:
            stream = await http.get(svc.url, headers=raw.headers())
        not_initialize = await raw.post(raw.request("tools/list"))

    assert unknown.status_code == 404
    assert stream.status_code == 400 and not_initialize.status_code == 400


async def test_session_ids_are_32_random_bytes_bound_to_the_key_hash(
    service: ServiceFactory, database: Database
) -> None:
    import hashlib

    _, raw_key = await builder_key(database)
    async with service() as svc:
        first = await Raw(svc.url, raw_key).initialize()
        second = await Raw(svc.url, raw_key).initialize()
        record = svc.sessions.get(first)

    assert len(first) == 64 and int(first, 16) >= 0 and first != second
    assert record is not None
    assert record.key_hash == hashlib.sha256(raw_key.encode()).hexdigest()
    assert raw_key not in repr(record.__dict__)


# ============================================================================ scope-shaped tool lists
async def test_read_only_bearer_sees_read_tools_only_and_builder_sees_writes(
    service: ServiceFactory, database: Database
) -> None:
    _, read_only = await builder_key(database, READ_ONLY_SCOPES)
    _, builder = await builder_key(database, BUILDER_SCOPES)

    async with service() as svc:
        async with mcp_client(svc.url, read_only) as ro, mcp_client(svc.url, builder) as rw:
            ro_tools = {tool.name: tool for tool in (await ro.list_tools()).tools}
            rw_tools = await tool_names(rw)
            me = await call(ro, "me")

    assert {"lkap_guide", "me", "agent_list", "agent_get"} <= set(ro_tools)
    assert not {"agent_create", "agent_update", "agent_publish"} & set(ro_tools)
    assert not CHAT_TOOLS & set(ro_tools)
    assert "webhook_create" not in ro_tools
    for tool in ro_tools.values():
        assert tool.annotations is not None and tool.annotations.readOnlyHint is True, tool.name
    assert {"agent_create", "agent_update"} | CHAT_TOOLS <= rw_tools
    assert me["ok"] is True and me["data"]["key"]["scopes"] == sorted(READ_ONLY_SCOPES)
    assert me["data"]["transport"] == "http"


async def test_two_workspaces_in_parallel_sessions_never_see_each_others_agents(
    service: ServiceFactory, database: Database
) -> None:
    other_ws = await make_workspace(database, "other-team")
    _, key_a = await builder_key(database)
    _, key_b = await builder_key(database, workspace_id=other_ws)

    async with service() as svc:
        async with mcp_client(svc.url, key_a) as a, mcp_client(svc.url, key_b) as b:
            made_a, made_b = await asyncio.gather(
                call(a, "agent_create", name="Alpha intake"), call(b, "agent_create", name="Beta intake")
            )
            list_a, list_b = await asyncio.gather(call(a, "agent_list"), call(b, "agent_list"))
            cross = await call(b, "agent_get", id_or_slug=made_a["data"]["agent"]["id"])
            plan_b = await call(b, "agent_create", name="Gamma", plan=True)
            me_a, me_b = await call(a, "me"), await call(b, "me")

    assert made_a["ok"] is True and made_b["ok"] is True, (made_a, made_b)
    names_a = {row["name"] for row in list_a["data"]}
    names_b = {row["name"] for row in list_b["data"]}
    assert "Alpha intake" in names_a and "Beta intake" not in names_a
    assert "Beta intake" in names_b and "Alpha intake" not in names_b
    assert cross["ok"] is False and cross["error"]["status"] == 404
    assert "Alpha" not in dumped(plan_b)
    assert me_a["data"]["workspace"]["id"] != me_b["data"]["workspace"]["id"]


# ============================================================================ secrets in HTTP mode
async def test_file_ref_is_ref_unavailable_in_http_mode(
    service: ServiceFactory, database: Database, tmp_path: Any
) -> None:
    secret_file = tmp_path / "livekit.env"
    secret_file.write_text("LIVEKIT_API_SECRET=never-read-from-disk-on-the-service\n")
    _, operator = await builder_key(database, OPERATOR_SCOPES)

    async with service() as svc, mcp_client(svc.url, operator) as session:
        result = await call(
            session,
            "connection_create",
            name="From a file",
            url="wss://project.livekit.cloud",
            api_key=KEY_B,
            api_secret=f"file:{secret_file}#LIVEKIT_API_SECRET",
        )

    assert result["ok"] is False and result["error"]["code"] == "ref_unavailable_in_http_mode"
    assert "never-read-from-disk" not in dumped(result)


async def test_inline_secret_creates_the_row_and_appears_in_no_log_or_result(
    service: ServiceFactory, database: Database, caplog: pytest.LogCaptureFixture
) -> None:
    _, operator = await builder_key(database, OPERATOR_SCOPES)

    async with fake_livekit() as (fake, url), service() as svc, mcp_client(svc.url, operator) as session:
        created = await call(
            session, "connection_create", name="Pasted", url=url, api_key=KEY_B, api_secret=SECRET_B
        )
        listed = await call(session, "connection_list")

    assert created["ok"] is True, created
    assert fake.calls, "the api tested the pasted key against the fake LiveKit"
    assert any(row["id"] == created["data"]["connection"]["id"] for row in listed["data"])
    service_logs = [r for r in caplog.records if not r.name.startswith(("mcp.client", "httpx"))]
    assert any(r.name.startswith("lkap_mcp") for r in service_logs)
    text = "\n".join(f"{r.getMessage()} {r.__dict__!r}" for r in service_logs)
    for value in (KEY_B, SECRET_B):
        assert value not in dumped(created) and value not in dumped(listed)
        assert value not in text


async def test_inline_secrets_off_refuses_inline_values_on_the_service(
    service: ServiceFactory, database: Database
) -> None:
    _, operator = await builder_key(database, OPERATOR_SCOPES)

    async with service(inline_secrets="off") as svc, mcp_client(svc.url, operator) as session:
        result = await call(
            session,
            "connection_create",
            name="Pasted",
            url="wss://p.livekit.cloud",
            api_key=KEY_B,
            api_secret=SECRET_B,
        )

    assert result["ok"] is False and result["error"]["code"] == "inline_secret_refused"
    assert SECRET_B not in dumped(result)


async def test_webhook_create_is_unavailable_in_http_mode(
    service: ServiceFactory, database: Database
) -> None:
    _, operator = await builder_key(database, OPERATOR_SCOPES)

    async with service() as svc, mcp_client(svc.url, operator) as session:
        result = await call(session, "webhook_create", url="https://hooks.example.com/lkap")

    assert result["ok"] is False and result["error"]["code"] == "unavailable_in_http_mode"


async def test_authorization_header_and_key_are_in_no_log_record(
    service: ServiceFactory, database: Database, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    _, raw_key = await builder_key(database)

    async with service() as svc:
        async with mcp_client(svc.url, raw_key) as session:
            await call(session, "me")
            await call(session, "agent_list")
        raw = Raw(svc.url, raw_key)
        await raw.post(raw.init_payload(), headers={"Origin": "https://evil.example"})  # a refusal
        await Raw(svc.url, "lkap_" + "9" * 40).post(raw.init_payload())  # an unknown key

    assert any(r.name == "lkap_mcp.http" for r in caplog.records)
    text = all_log_text(caplog)
    assert raw_key not in text
    assert "lkap_" + "9" * 40 not in text
    assert "Bearer " not in text
    assert "authorization" not in text.lower()


# ============================================================================ limits
async def test_sixth_session_for_one_key_is_429_and_other_keys_are_unaffected(
    service: ServiceFactory, database: Database
) -> None:
    _, raw_key = await builder_key(database)
    _, other = await builder_key(database)

    async with service() as svc:
        raw = Raw(svc.url, raw_key)
        opened = [await raw.initialize() for _ in range(5)]
        sixth = await raw.post(raw.init_payload())
        other_ok = await Raw(svc.url, other).post(raw.init_payload())
        # Closing one frees a slot.
        async with httpx.AsyncClient() as http:
            deleted = await http.delete(svc.url, headers=raw.headers(opened[0]))
        seventh = await raw.post(raw.init_payload())

    assert sixth.status_code == 429
    assert refusal(sixth)["code"] == "rate_limited" and refusal(sixth)["retry_after_s"] > 0
    assert "retry-after" in sixth.headers
    assert other_ok.status_code == 200
    assert deleted.status_code in (200, 204)
    assert seventh.status_code == 200


async def test_the_121st_tool_call_in_a_minute_is_429_with_retry_after_s(
    service: ServiceFactory, database: Database
) -> None:
    _, raw_key = await builder_key(database)

    async with service() as svc:
        raw = Raw(svc.url, raw_key)
        session = await raw.initialize()
        statuses = []
        for _ in range(120):
            statuses.append((await raw.call(session, "lkap_guide")).status_code)
        over = await raw.call(session, "lkap_guide")
        listing = await raw.post(raw.request("tools/list"), session=session)  # not a tool call
        svc.clock.now += 61  # the window slides
        after = await raw.call(session, "lkap_guide")

    assert statuses == [200] * 120
    assert over.status_code == 429
    body = over.json()
    assert body["error"]["data"]["code"] == "rate_limited"
    assert 0 < body["error"]["data"]["retry_after_s"] <= 60
    assert int(over.headers["retry-after"]) >= 1
    assert listing.status_code == 200
    assert after.status_code == 200


async def test_an_eleventh_request_in_flight_on_a_session_is_429(
    service: ServiceFactory, database: Database
) -> None:
    _, raw_key = await builder_key(database)

    async with service() as svc:
        raw = Raw(svc.url, raw_key)
        session = await raw.initialize()
        record = svc.sessions.get(session)
        assert record is not None
        record.in_flight = 10  # ten requests being served
        over = await raw.call(session, "lkap_guide")
        record.in_flight = 9
        under = await raw.call(session, "lkap_guide")

    assert over.status_code == 429 and refusal(over)["code"] == "rate_limited"
    assert under.status_code == 200


@pytest.mark.parametrize("with_session", [False, True])
async def test_a_2_mb_body_is_413(service: ServiceFactory, database: Database, with_session: bool) -> None:
    _, raw_key = await builder_key(database)

    async with service() as svc:
        raw = Raw(svc.url, raw_key)
        session = await raw.initialize() if with_session else None
        big = raw.request("tools/call", {"name": "lkap_search_docs", "arguments": {"query": "x" * 2_000_000}})
        response = await raw.post(big, session=session)
        sessions = len(svc.sessions.sessions)

    assert response.status_code == 413 and refusal(response)["code"] == "payload_too_large"
    assert sessions == (1 if with_session else 0)


async def test_a_body_streamed_without_content_length_is_still_capped(
    service: ServiceFactory, database: Database
) -> None:
    _, raw_key = await builder_key(database)

    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(40):
            yield b" " * 65536

    async with service() as svc:
        raw = Raw(svc.url, raw_key)
        async with httpx.AsyncClient() as http:
            response = await http.post(svc.url, content=chunks(), headers=raw.headers())

    assert response.status_code == 413


# ============================================================================ revocation
async def test_key_revoked_mid_session_relays_unauthorized_then_the_session_is_dropped(
    service: ServiceFactory, database: Database, admin: httpx.AsyncClient
) -> None:
    key_id, raw_key = await builder_key(database)

    async with service() as svc:
        async with mcp_client(svc.url, raw_key) as session:
            before = await call(session, "agent_list")
            (record,) = svc.sessions.sessions
            revoked = await admin.delete(f"/v1/api-keys/{key_id}")
            after = await call(session, "agent_list")
            await wait_until(lambda: svc.sessions.get(record.id) is None)
            raw = Raw(svc.url, raw_key)
            next_request = await raw.post(raw.request("tools/list"), session=record.id)

    assert before["ok"] is True
    assert revoked.status_code == 204
    assert after["ok"] is False and after["error"]["code"] == "unauthorized"
    assert next_request.status_code == 404
    assert svc.sessions.sessions == []


# ============================================================================ per-call limits (unit)
def _spec(name: str) -> ToolSpec:
    async def fn() -> ToolResult:
        return ToolResult.success({})

    from lkap_mcp.registry import READ

    return ToolSpec(name=name, fn=fn, scopes=frozenset(), annotations=READ, description="")


async def test_a_call_over_the_cap_is_call_timeout() -> None:
    async def slow(**kwargs: Any) -> ToolResult:
        await asyncio.sleep(5)
        return ToolResult.success({})

    limits = CallLimits(call_timeout_s=0.05, max_chats_total=20, chats_open=lambda: 0)

    result = await _limited_call(_spec("agent_list"), {}, slow, limits)

    assert result.ok is False and result.error is not None and result.error.code == "call_timeout"


async def test_a_tool_with_its_own_timeout_s_gets_that_plus_grace() -> None:
    async def slowish(**kwargs: Any) -> ToolResult:
        await asyncio.sleep(0.2)
        return ToolResult.success({"done": True})

    limits = CallLimits(call_timeout_s=0.05, max_chats_total=20, chats_open=lambda: 0)

    result = await _limited_call(_spec("chat_send"), {"timeout_s": 1.0}, slowish, limits)

    assert result.ok is True


async def test_chat_start_beyond_the_process_cap_is_too_many_chats() -> None:
    calls: list[str] = []

    async def start(**kwargs: Any) -> ToolResult:
        calls.append("started")
        return ToolResult.success({})

    limits = CallLimits(call_timeout_s=60, max_chats_total=20, chats_open=lambda: 20)

    result = await _limited_call(_spec("chat_start"), {}, start, limits)

    assert result.ok is False and result.error is not None and result.error.code == "too_many_chats"
    assert calls == [] and limits.chat_starts_pending == 0


# ============================================================================ R-V3-24 chat ownership
async def test_chat_id_of_session_a_is_unknown_to_session_b_of_the_same_key_and_to_another_key(
    service: ServiceFactory, database: Database, admin: httpx.AsyncClient, app: FastAPI, rooms: Rooms
) -> None:
    await register_ready_worker(app)
    agent_id = await draft_agent(admin)
    _, key_1 = await builder_key(database)
    other_ws = await make_workspace(database, "other-team")
    _, key_2 = await builder_key(database, workspace_id=other_ws)  # another key, another workspace

    async with service() as svc:
        async with (
            mcp_client(svc.url, key_1) as a,
            mcp_client(svc.url, key_1) as b,
            mcp_client(svc.url, key_2) as c,
        ):
            chat = await open_chat(a, agent_id)
            from_b = await call(b, "chat_send", chat_id=chat["chat_id"], text="hello")
            from_c = await call(c, "chat_send", chat_id=chat["chat_id"], text="hello")
            end_b = await call(b, "chat_end", chat_id=chat["chat_id"])
            from_a = await call(a, "chat_send", chat_id=chat["chat_id"], text="hello", timeout_s=0.2)
            owners = _owners(svc.sessions)
            ids = {s.id for s in svc.sessions.sessions}

    # R-V3-24 says "not_found"; the chat manager (V3-02) names that case `unknown_chat` and does
    # not reveal whether the id exists for another owner.
    for result in (from_b, from_c, end_b):
        assert result["ok"] is False and result["error"]["code"] == "unknown_chat", result
    assert from_a["ok"] is True
    assert owners and all(owner.startswith("session:") and owner[8:] in ids for owner in owners)


def _owners(sessions: HttpSessions) -> set[str]:
    """Every chat owner across the per-session chat managers."""
    owners: set[str] = set()
    for record in sessions.sessions:
        manager = chat_tools.manager_for(record.server.registry)
        owners |= {chat.owner for chat in manager._chats.values()}
    return owners


async def test_no_chat_owner_is_ever_an_object_id(
    service: ServiceFactory, database: Database, admin: httpx.AsyncClient, app: FastAPI, rooms: Rooms
) -> None:
    await register_ready_worker(app)
    agent_id = await draft_agent(admin)
    _, raw_key = await builder_key(database)

    async with service() as svc, mcp_client(svc.url, raw_key) as session:
        await open_chat(session, agent_id)
        (record,) = svc.sessions.sessions
        owners = _owners(svc.sessions)
        object_ids = {
            str(id(record.server)),
            str(id(record.server.ctx)),
            str(id(record.transport)),
            str(id(record.server.registry)),
        }

    assert owners == {f"session:{record.id}"}
    assert not any(oid in owner for owner in owners for oid in object_ids)
    assert not any(owner.startswith("session-object:") for owner in owners)


async def test_fourth_chat_in_one_session_is_too_many_chats_while_a_second_session_can_open_one(
    service: ServiceFactory, database: Database, admin: httpx.AsyncClient, app: FastAPI, rooms: Rooms
) -> None:
    await register_ready_worker(app)
    agent_id = await draft_agent(admin)
    _, raw_key = await builder_key(database)

    async with service() as svc:
        async with mcp_client(svc.url, raw_key) as a, mcp_client(svc.url, raw_key) as b:
            for _ in range(3):
                await open_chat(a, agent_id)
            fourth = await call(a, "chat_start", agent_id_or_slug=agent_id, wait_for_greeting=False)
            other = await call(b, "chat_start", agent_id_or_slug=agent_id, wait_for_greeting=False)

    assert fourth["ok"] is False and fourth["error"]["code"] == "too_many_chats"
    assert other["ok"] is True, other


async def test_the_process_wide_chat_cap_spans_sessions(
    service: ServiceFactory, database: Database, admin: httpx.AsyncClient, app: FastAPI, rooms: Rooms
) -> None:
    await register_ready_worker(app)
    agent_id = await draft_agent(admin)
    _, raw_key = await builder_key(database)

    async with service(max_chats_total=2) as svc:
        async with mcp_client(svc.url, raw_key) as a, mcp_client(svc.url, raw_key) as b:
            await open_chat(a, agent_id)
            await open_chat(b, agent_id)
            third = await call(a, "chat_start", agent_id_or_slug=agent_id, wait_for_greeting=False)

    assert third["ok"] is False and third["error"]["code"] == "too_many_chats"
    assert third["error"]["details"] == {"limit": 2}


async def test_ending_a_session_explicitly_closes_its_chats(
    service: ServiceFactory, database: Database, admin: httpx.AsyncClient, app: FastAPI, rooms: Rooms
) -> None:
    await register_ready_worker(app)
    agent_id = await draft_agent(admin)
    _, raw_key = await builder_key(database)

    async with service() as svc:
        async with mcp_client(svc.url, raw_key) as a, mcp_client(svc.url, raw_key) as b:
            await open_chat(a, agent_id)
            await open_chat(b, agent_id)
            managers = [chat_tools.manager_for(s.server.registry) for s in svc.sessions.sessions]
            assert all(m._reaper is not None for m in managers)
        # Leaving `mcp_client` sends DELETE (explicit termination) for both sessions.
        await wait_until(lambda: svc.sessions.sessions == [])

    assert len(rooms.made) == 2 and all(room.closed for room in rooms.made)
    assert all(m._reaper is None and m.chat_ids() == [] for m in managers)  # no per-session task leaks


async def test_an_idle_session_is_closed_with_its_chats(
    service: ServiceFactory, database: Database, admin: httpx.AsyncClient, app: FastAPI, rooms: Rooms
) -> None:
    await register_ready_worker(app)
    agent_id = await draft_agent(admin)
    _, raw_key = await builder_key(database)

    async with service() as svc:
        async with mcp_client(svc.url, raw_key) as idle, mcp_client(svc.url, raw_key) as busy:
            await open_chat(idle, agent_id)
            (idle_record,) = [s for s in svc.sessions.sessions if _owners_of(s)]
            svc.clock.now += 1799
            await call(busy, "me")  # a request keeps `busy` fresh
            assert await svc.sessions.sweep_idle() == []
            svc.clock.now += 2
            swept = await svc.sessions.sweep_idle()
            survivors = [s.id for s in svc.sessions.sessions]

    assert swept == [idle_record.id]
    assert idle_record.id not in survivors and len(survivors) == 1
    assert rooms.made[0].closed


def _owners_of(record: Any) -> set[str]:
    manager = chat_tools.manager_for(record.server.registry)
    return {chat.owner for chat in manager._chats.values()}


async def test_revocation_closes_the_sessions_chats(
    service: ServiceFactory, database: Database, admin: httpx.AsyncClient, app: FastAPI, rooms: Rooms
) -> None:
    await register_ready_worker(app)
    agent_id = await draft_agent(admin)
    key_id, raw_key = await builder_key(database)

    async with service() as svc, mcp_client(svc.url, raw_key) as session:
        chat = await open_chat(session, agent_id)
        await admin.delete(f"/v1/api-keys/{key_id}")
        # chat_send's own api call (the session-events tail) meets the 401.
        result = await call(session, "chat_send", chat_id=chat["chat_id"], text="still there?", timeout_s=0.2)
        await wait_until(lambda: svc.sessions.sessions == [])

    assert result["ok"] is False and result["error"]["code"] == "unauthorized"
    assert rooms.made[0].closed


async def test_shutdown_closes_every_sessions_chats(
    service: ServiceFactory, database: Database, admin: httpx.AsyncClient, app: FastAPI, rooms: Rooms
) -> None:
    await register_ready_worker(app)
    agent_id = await draft_agent(admin)
    _, raw_key = await builder_key(database)
    previous = chat_tools.OWNER_KEY

    async with service() as svc:
        assert chat_tools.OWNER_KEY == svc.sessions.owner_key
        raw = Raw(svc.url, raw_key)
        session = await raw.initialize()
        opened = await raw.call(session, "chat_start", agent_id_or_slug=agent_id, wait_for_greeting=False)
        assert opened.status_code == 200 and sse_messages(opened)[0]["result"]["structuredContent"]["ok"]

    assert rooms.made and rooms.made[0].closed
    assert chat_tools.OWNER_KEY is previous  # the hook is restored when the service stops


async def test_owner_key_fails_closed_and_the_call_is_no_session(
    service: ServiceFactory, database: Database
) -> None:
    _, raw_key = await builder_key(database)

    async with service() as svc:
        settings = service_settings(McpSettings(api_url=API_BASE))
        stray_client = SessionClient(
            settings, api_key=raw_key, transport=httpx.MockTransport(lambda r: httpx.Response(500))
        )
        stray = build_session_server(settings, stray_client, svc.sessions.limits)
        try:
            with pytest.raises(NoSession):
                chat_tools.OWNER_KEY(stray.ctx)
            spec = stray.registry.specs["chat_send"]
            result = await stray.registry.wrapped(spec)(chat_id="chat_x", text="hi")
            other_ctx = ServerContext(settings=settings, client=LkapClient(settings, api_key=raw_key))
            with pytest.raises(NoSession):
                svc.sessions.owner_key(other_ctx)
        finally:
            await stray.aclose()
            await other_ctx.client.aclose()

    assert result.ok is False and result.error is not None and result.error.code == "no_session"
