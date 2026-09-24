"""Fixtures: a scratch LKAP api in-process and a real MCP client session over memory streams.

* The api is ``lkap_api.main.create_app(settings)`` over ``httpx.ASGITransport``
  with a temp SQLite file, a fixed test master key, bootstrap seeding and the
  real packs; no network, no worker. External boundaries only are faked:
  the embedder (``FakeEmbedder``), vendor/tool hosts (``respx``) and LiveKit
  (the api tests' in-process Twirp server, ``connection_fakes.fake_livekit``).
* The MCP side is the real server (:func:`lkap_mcp.server.build_server`) and the
  real client (``mcp.ClientSession``) connected in memory, with
  ``clientInfo.name = "test-client"``, so schemas, annotations and results are
  exercised exactly as a coding agent sees them.
* :class:`RecordingTransport` records every request the MCP makes to the api
  (headers included) and can fabricate responses for chosen paths.

No ``.env`` file is read: every setting comes from monkeypatched env vars.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from mcp import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import Implementation

from lkap_mcp.client import LkapClient
from lkap_mcp.secrets import SEEN
from lkap_mcp.server import LkapServer, build_server
from lkap_mcp.settings import McpSettings

API_TESTS = Path(__file__).resolve().parents[2] / "api" / "tests"
if str(API_TESTS) not in sys.path:
    sys.path.append(str(API_TESTS))  # auth_helpers, connection_fakes (plain helper modules)

from auth_helpers import make_api_key  # noqa: E402
from lkap_api.bootstrap import bootstrap  # noqa: E402
from lkap_api.db.session import Database  # noqa: E402
from lkap_api.kb.embed import FakeEmbedder  # noqa: E402
from lkap_api.settings import Settings, get_settings  # noqa: E402

API_BASE = "http://api.test"
CLIENT_NAME = "test-client"

REQUIRED_ENV: dict[str, str] = {
    "LIVEKIT_URL": "wss://example.livekit.cloud",
    "LIVEKIT_API_KEY": "test-key",
    "LIVEKIT_API_SECRET": "test-secret-value-long-enough-for-hs256",
    "LKAP_MASTER_KEY": "TWk5rQ2mE4b3W6z8n1F0pQhV9xY7cJdKzL5aRtUvWo8=",
    "LKAP_ADMIN_TOKEN": "test-admin",
    "LKAP_SERVICE_TOKEN": "test-service",
}

#: The console presets (R-V3-9).
READ_ONLY_SCOPES = ["agents:read", "sessions:read", "connections:read", "providers:read", "audit:read"]
BUILDER_SCOPES = [*READ_ONLY_SCOPES, "agents:write", "sessions:write"]
OPERATOR_SCOPES = [*BUILDER_SCOPES, "connections:write", "providers:write", "webhooks:write"]


# --------------------------------------------------------------------------- api
@pytest.fixture
def api_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Settings]:
    """Api settings from env vars only, with an isolated data dir."""
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("LKAP_DATA_DIR", str(data))
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest.fixture
async def database(api_settings: Settings) -> AsyncIterator[Database]:
    """A fresh scratch SQLite database with the schema and the bootstrapped default workspace."""
    db = Database(api_settings.resolved_database_url)
    await db.create_all()
    await bootstrap(db, api_settings)
    try:
        yield db
    finally:
        await db.dispose()


async def _fake_resolve_embedder(*args: object, **kwargs: object) -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def app(
    api_settings: Settings,
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> Iterator[FastAPI]:
    """The scratch api app (ASGI transport skips lifespan, so the db is attached here)."""
    from lkap_api.main import create_app
    from lkap_api.routers.knowledge import get_embedder

    application = create_app(api_settings)
    application.state.db = database
    application.dependency_overrides[get_embedder] = lambda: FakeEmbedder()
    monkeypatch.setattr("lkap_api.kb.ingest.resolve_embedder", _fake_resolve_embedder)
    # create_app reconfigures root logging; put pytest's capture handler back.
    root = logging.getLogger()
    root.addHandler(caplog.handler)
    caplog.set_level(logging.DEBUG)
    try:
        yield application
    finally:
        root.removeHandler(caplog.handler)
        application.dependency_overrides.clear()


@pytest.fixture(autouse=True, scope="session")
def _fast_password_hashing() -> Iterator[None]:
    """Minimal argon2 parameters (bootstrap hashes a password per database)."""
    from argon2 import PasswordHasher
    from lkap_api.auth import passwords

    fast = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    patcher = pytest.MonkeyPatch()
    patcher.setattr(passwords, "_hasher", lambda: fast)
    passwords._dummy_hash.cache_clear()
    try:
        yield
    finally:
        patcher.undo()
        passwords._dummy_hash.cache_clear()


@pytest.fixture(autouse=True, scope="session")
def _no_fastembed_model_download() -> Iterator[None]:
    """Refuse to download the real embedding model."""
    from lkap_api.kb.embed import FastEmbedEmbedder

    async def _refuse(self: FastEmbedEmbedder) -> object:
        raise RuntimeError("tests must not load the fastembed model; use FakeEmbedder")

    patcher = pytest.MonkeyPatch()
    patcher.setattr(FastEmbedEmbedder, "_get_model", _refuse)
    try:
        yield
    finally:
        patcher.undo()


@pytest.fixture(autouse=True)
def _fresh_seen_values() -> Iterator[None]:
    """Each test starts with an empty seen-secret set."""
    SEEN.clear()
    yield
    SEEN.clear()


@pytest.fixture
async def admin(app: FastAPI, api_settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    """A direct admin client of the scratch api (for arranging and asserting)."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=API_BASE,
        headers={"X-Admin-Token": api_settings.admin_token},
    ) as client:
        yield client


# --------------------------------------------------------------------------- MCP
class RecordingTransport(httpx.AsyncBaseTransport):
    """Records every request and can fabricate responses for chosen ``(method, path)``."""

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self.inner = inner
        self.requests: list[httpx.Request] = []
        self.bodies: list[Any] = []
        self.fabricated: dict[tuple[str, str], httpx.Response] = {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = await request.aread()  # multipart uploads are streams until read
        try:
            self.bodies.append(json.loads(body) if body else None)
        except ValueError:
            self.bodies.append(None)
        fake = self.fabricated.get((request.method, request.url.path))
        if fake is not None:
            return httpx.Response(
                fake.status_code, headers=fake.headers, content=fake.content, request=request
            )
        return await self.inner.handle_async_request(request)

    def calls(self, method: str, path: str) -> list[int]:
        """Indexes of recorded requests matching ``method`` and ``path``."""
        return [
            index
            for index, request in enumerate(self.requests)
            if request.method == method and request.url.path == path
        ]


class Mcp:
    """A connected MCP client session plus the server and the recorded api traffic."""

    def __init__(self, session: ClientSession, server: LkapServer, transport: RecordingTransport) -> None:
        self.session = session
        self.server = server
        self.transport = transport

    async def tool_names(self) -> list[str]:
        """The tool list as the client sees it."""
        return sorted(tool.name for tool in (await self.session.list_tools()).tools)

    async def call(self, tool: str, /, **arguments: Any) -> dict[str, Any]:
        """Call a tool; return its structured ``ToolResult`` (or ``{"is_error", "text"}``)."""
        result = await self.session.call_tool(tool, arguments)
        text = "".join(getattr(block, "text", "") for block in result.content)
        if result.isError:
            return {"is_error": True, "text": text}
        assert result.structuredContent is not None, text
        structured: dict[str, Any] = result.structuredContent
        structured["_text"] = text
        return structured


McpFactory = Callable[..., Any]


@pytest.fixture
def mcp_session(app: FastAPI) -> McpFactory:
    """``async with mcp_session(raw_key, **settings) as m:`` → a connected :class:`Mcp`."""

    @asynccontextmanager
    async def connect(raw_key: str, **overrides: Any) -> AsyncIterator[Mcp]:
        settings = McpSettings(api_url=API_BASE, **overrides)
        transport = RecordingTransport(httpx.ASGITransport(app=app))
        client = LkapClient(settings, api_key=raw_key, transport=transport)
        server = build_server(settings, client=client)
        try:
            async with create_connected_server_and_client_session(
                server, client_info=Implementation(name=CLIENT_NAME, version="0")
            ) as session:
                yield Mcp(session, server, transport)
        finally:
            await server.aclose()

    return connect


@pytest.fixture
def key(database: Database) -> Callable[..., Any]:
    """``await key(scopes)`` → a raw API key of the default workspace."""

    async def make(scopes: list[str], **columns: Any) -> str:
        _, raw = await make_api_key(database, scopes, **columns)
        return raw

    return make


def dumped(result: dict[str, Any]) -> str:
    """Everything a client could read from a result (structured and text)."""
    return json.dumps(result, default=str)


def all_log_text(caplog: pytest.LogCaptureFixture) -> str:
    """Every captured record, rendered every way a handler could render it."""
    return "\n".join(
        f"{record.name} {record.getMessage()} {record.msg!r} {record.args!r} {record.__dict__!r}"
        for record in caplog.records
    )
