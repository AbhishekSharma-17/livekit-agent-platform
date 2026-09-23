"""Shared pytest fixtures for `lkap_api` tests.

No `.env` file is read in tests: every required setting is supplied via
monkeypatched environment variables so the suite runs with no vendor keys,
no `.env`, and no network (`pytest -m "not live"`).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import types
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import greenlet
import httpx
import pytest
from fastapi import FastAPI
from lkap_contracts.agent_config import (
    AgentConfig,
    CapabilitiesConfig,
    PanelLayout,
    PipelineConfig,
    ProviderRef,
)
from lkap_contracts.migrate import default_panel_for
from lkap_contracts.packs import PackManifest

from lkap_api.bootstrap import bootstrap
from lkap_api.db.guard import tenant_scope_guard
from lkap_api.db.session import Database
from lkap_api.packs import clear_manifest_cache
from lkap_api.settings import Settings, get_settings

REQUIRED_ENV: dict[str, str] = {
    "LIVEKIT_URL": "wss://example.livekit.cloud",
    "LIVEKIT_API_KEY": "test-key",
    "LIVEKIT_API_SECRET": "test-secret-value-long-enough-for-hs256",
    "LKAP_MASTER_KEY": "TWk5rQ2mE4b3W6z8n1F0pQhV9xY7cJdKzL5aRtUvWo8=",
    "LKAP_ADMIN_TOKEN": "test-admin",
    "LKAP_SERVICE_TOKEN": "test-service",
}


def postgres_url() -> str | None:
    """Return `LKAP_TEST_DATABASE_URL` when the suite should run against Postgres.

    CI sets it to an `asyncpg` url (`.github/workflows/api-postgres.yml`); locally
    it is unset and every test runs on SQLite.
    """
    return os.environ.get("LKAP_TEST_DATABASE_URL") or None


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """An isolated `LKAP_DATA_DIR` for a single test."""
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch, data_dir: Path) -> Iterator[Settings]:
    """A `Settings` instance built entirely from env vars, no `.env` file."""
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("LKAP_DATA_DIR", str(data_dir))
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


# --------------------------------------------------------------------------- W1-API-CORE
# Fixtures below are additive: they build an isolated app, database and clients
# per test. The ASGI transport does not run the lifespan, so the database is
# created here and attached to `app.state` directly (bootstrap seeding is tested
# through `seed_bootstrap_credentials` instead).

GENERIC_MANIFEST = PackManifest(
    id="generic",
    version="0.1.0",
    name="Generic assistant",
    description="A pack with no code tools.",
    ui_panel_id="generic",
    # Matches `packs/src/packs/generic/manifest.py`'s real default_panel (R-V2-7):
    # the built-in composite panel with the four default blocks. Kept in sync by
    # hand since this fake exists precisely so tests don't import the real pack.
    default_panel=PanelLayout.model_validate(default_panel_for("generic")),
    default_instructions="You are a helpful assistant.",
    default_greeting="Hello! How can I help you today?",
    recommended_pipeline=PipelineConfig(
        mode="cascaded",
        stt=ProviderRef(provider_id="livekit-inference-stt"),
        llm=ProviderRef(provider_id="livekit-inference-llm"),
        tts=ProviderRef(provider_id="livekit-inference-tts"),
    ),
    capabilities=CapabilitiesConfig(),
    tool_names=[],
    state_schema={"type": "object"},
)

REALTIME_MANIFEST = PackManifest(
    id="insurance_claim",
    version="0.1.0",
    name="Insurance claim intake",
    description="A pack that would prefer Gemini Live.",
    ui_panel_id="insurance_notebook",
    default_instructions="You take first-notice-of-loss calls.",
    default_greeting="Hi, I can start your claim.",
    default_voice={"google-realtime": "Kore", "livekit-inference-tts": "Ashley"},
    recommended_pipeline=PipelineConfig(
        mode="realtime",
        realtime=ProviderRef(provider_id="google-realtime"),
        image_gen=ProviderRef(provider_id="google-image-gen"),
    ),
    capabilities=CapabilitiesConfig(camera=True),
    builtin_tools_disabled=["push_note", "set_status"],
    tool_names=["lookup_policy"],
    state_schema={"type": "object"},
)


@pytest.fixture
def fake_packs(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, PackManifest]]:
    """Install importable fake pack manifests under the default `LKAP_PACKS` paths."""
    manifests = {"packs.generic": GENERIC_MANIFEST, "packs.insurance_claim": REALTIME_MANIFEST}
    for path, manifest in manifests.items():
        module = types.ModuleType(f"{path}.manifest")
        module.MANIFEST = manifest  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, f"{path}.manifest", module)
        monkeypatch.setitem(sys.modules, path, types.ModuleType(path))
    clear_manifest_cache()
    yield {m.id: m for m in manifests.values()}
    clear_manifest_cache()


@pytest.fixture
async def database(settings: Settings) -> AsyncIterator[Database]:
    """A fresh database with the full schema and the bootstrapped default workspace.

    Every tenant table carries a `workspace_id` foreign key whose Python-side
    default is `DEFAULT_WORKSPACE_ID`, so the row it points at has to exist
    before any test inserts an agent. `bootstrap` also creates the default
    connection from the test `LIVEKIT_*` values, which is what the v1 routes
    keep resolving against.

    Set `LKAP_TEST_DATABASE_URL` to run the suite against Postgres instead; the
    schema is created and dropped per test, so point it at a scratch database.
    """
    db = Database(postgres_url() or settings.resolved_database_url)
    await db.create_all()
    await bootstrap(db, settings)
    try:
        yield db
    finally:
        if postgres_url():
            await db.drop_all()
        await db.dispose()


@pytest.fixture(autouse=True, scope="session")
def _fast_password_hashing() -> Iterator[None]:
    """Use minimal argon2id parameters in tests.

    Production hashing costs ~50-200 ms per call by design; bootstrap hashes the
    owner password for every test database, which alone added minutes to the
    suite. The hash format (``$argon2id$``) and verification path are unchanged.
    """
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


def _issued_by_test_code(_state: object) -> bool:
    """True when the nearest application-or-test frame issuing the query is a test module.

    Async sessions run the ORM inside a greenlet, so the caller's frames are
    reached through the greenlet's parent. Application code (anything under
    ``lkap_api/``) is always checked, however a test invokes it; a test's own
    assertion reads are not application queries and are skipped.
    """
    current = greenlet.getcurrent()
    frame = current.parent.gr_frame if current.parent is not None else sys._getframe(1)
    while frame is not None:
        filename = frame.f_code.co_filename
        if "/lkap_api/" in filename and not filename.endswith("/db/guard.py"):
            return False
        if "/tests/" in filename and not filename.endswith("/conftest.py"):
            return True
        frame = frame.f_back
    return False


@pytest.fixture(autouse=True)
def tenant_guard() -> Iterator[None]:
    """Fail any application query on a tenant table without a `workspace_id` predicate.

    Autouse since V2-02 scoped every admin router (asks #13/#25): the suite
    fails on an unscoped tenant query anywhere in `lkap_api`, whether reached
    through a route or called directly by a test. Reads issued by test code
    itself are exempt; deliberate cross-workspace application reads opt out
    with `.execution_options(lkap_cross_workspace=True)`.
    """
    with tenant_scope_guard(exempt=_issued_by_test_code):
        yield


@pytest.fixture
async def app(settings: Settings, database: Database, fake_packs: dict[str, PackManifest]) -> FastAPI:
    """An isolated FastAPI app wired to the per-test database."""
    from lkap_api.main import create_app

    application = create_app(settings)
    application.state.db = database
    return application


def _client(app: FastAPI, headers: dict[str, str] | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api.test", headers=headers
    )


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """An unauthenticated client (public routes and auth-failure cases)."""
    async with _client(app) as c:
        yield c


@pytest.fixture
async def admin_client(app: FastAPI, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    """A client presenting a valid `X-Admin-Token`."""
    async with _client(app, {"X-Admin-Token": settings.admin_token}) as c:
        yield c


@pytest.fixture
async def service_client(app: FastAPI, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    """A client presenting a valid `X-Service-Token` (the worker)."""
    async with _client(app, {"X-Service-Token": settings.service_token}) as c:
        yield c


@pytest.fixture
def mock_http(app: FastAPI) -> Iterator[list[httpx.Request]]:
    """Replace the outbound HTTP client with a recorded, offline mock transport."""
    from lkap_api.deps import get_http_client

    recorded: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json={"data": [{"id": "model-a"}], "summary": "all good"})

    async def override() -> AsyncIterator[httpx.AsyncClient]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as mocked:
            yield mocked

    app.dependency_overrides[get_http_client] = override
    yield recorded
    app.dependency_overrides.pop(get_http_client, None)


@pytest.fixture
def log_capture(app: FastAPI, caplog: pytest.LogCaptureFixture) -> Iterator[pytest.LogCaptureFixture]:
    """Re-attach pytest's log handler after `create_app` reconfigured logging."""
    root = logging.getLogger()
    root.addHandler(caplog.handler)
    caplog.set_level(logging.DEBUG)
    try:
        yield caplog
    finally:
        root.removeHandler(caplog.handler)


def captured_text(caplog: pytest.LogCaptureFixture, *, scope: str | None = "lkap_api") -> str:
    """Captured log output, by default only the lines this service emitted.

    `scope=None` includes third-party records too (SQLAlchemy/aiosqlite echo the
    statements they run, which is useful when asserting that a value never even
    reaches the database in plaintext).
    """
    records = [
        record
        for record in caplog.records
        if scope is None or record.name == scope or record.name.startswith(f"{scope}.")
    ]
    return "".join(
        f"{record.name} {record.getMessage()} {record.msg!r} {record.args!r}" for record in records
    )


def inference_config(**overrides: object) -> AgentConfig:
    """A valid, credential-free cascaded configuration for tests."""
    base = AgentConfig(
        instructions="You are a helpful assistant.",
        pipeline=PipelineConfig(
            mode="cascaded",
            stt=ProviderRef(provider_id="livekit-inference-stt"),
            llm=ProviderRef(provider_id="livekit-inference-llm"),
            tts=ProviderRef(provider_id="livekit-inference-tts"),
        ),
    )
    return base.model_copy(update=overrides) if overrides else base


async def create_agent(
    admin_client: httpx.AsyncClient, *, name: str = "Test agent", published: bool = True, **extra: object
) -> dict[str, object]:
    """Create an agent through the api and optionally publish it."""
    payload: dict[str, object] = {
        "name": name,
        "config": json.loads(inference_config().model_dump_json()),
    }
    payload.update(extra)
    response = await admin_client.post("/v1/agents", json=payload)
    assert response.status_code == 201, response.text
    agent: dict[str, object] = response.json()
    if published:
        response = await admin_client.put(f"/v1/agents/{agent['id']}", json={"published": True})
        assert response.status_code == 200, response.text
        agent = response.json()
    return agent
