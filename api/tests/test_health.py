"""Application surface: health, OpenAPI, the provider/pack catalogues and the W2 seam."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from lkap_contracts.packs import PackManifest
from sqlalchemy import text, update

from lkap_api import __version__
from lkap_api.db.models import Agent
from lkap_api.db.session import Database
from lkap_api.routers.health import migration_head

API_ROOT = Path(__file__).resolve().parents[1]


async def _stamp(database: Database, revision: str | None) -> None:
    """Write `alembic_version` the way `alembic upgrade` would (tests build the schema with create_all)."""
    async with database.engine.begin() as conn:
        await conn.execute(
            text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
        )
        await conn.execute(text("DELETE FROM alembic_version"))
        if revision is not None:
            await conn.execute(text("INSERT INTO alembic_version (version_num) VALUES (:v)"), {"v": revision})


@pytest.fixture(autouse=True)
async def _at_head(database: Database) -> None:
    """Every health test starts from a schema stamped at the migration head."""
    migration_head.cache_clear()
    await _stamp(database, migration_head())


async def test_health_is_public_and_reports_db_ok(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "ok": True,
        "version": __version__,
        "livekit_url": "wss://example.livekit.cloud",
        "packs": ["insurance_claim", "generic"],
        "db": "ok",
        "agents_unbound": 0,
    }


async def test_migration_head_matches_the_script_directory() -> None:
    # Order-independent (asks #58): compare against a head computed afresh from
    # `api/alembic`, not a hard-coded revision or a value cached by an earlier test.
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    expected = ScriptDirectory.from_config(config).get_current_head()

    migration_head.cache_clear()

    assert expected is not None and expected.startswith(("v2_", "v3_"))
    assert migration_head() == expected


@pytest.mark.parametrize("revision", [None, "4135323c6ecc", "v2_003_scope_tables"])
async def test_health_reports_db_error_unless_the_schema_is_at_head(
    client: httpx.AsyncClient, database: Database, revision: str | None
) -> None:
    await _stamp(database, revision)

    body = (await client.get("/v1/health")).json()

    assert (body["ok"], body["db"]) == (False, "error")


async def test_health_reports_db_error_without_an_alembic_version_table(
    client: httpx.AsyncClient, database: Database
) -> None:
    async with database.engine.begin() as conn:
        await conn.execute(text("DROP TABLE alembic_version"))

    body = (await client.get("/v1/health")).json()

    assert (body["ok"], body["db"]) == (False, "error")


async def test_health_counts_agents_without_a_connection(
    client: httpx.AsyncClient, admin_client: httpx.AsyncClient, database: Database
) -> None:
    from conftest import create_agent

    agent = await create_agent(admin_client, published=False)
    bound = (await client.get("/v1/health")).json()["agents_unbound"]
    async with database.session() as session:
        await session.execute(update(Agent).where(Agent.id == agent["id"]).values(connection_id=None))

    body = (await client.get("/v1/health")).json()

    assert bound == 0
    assert body["agents_unbound"] == 1
    assert body["db"] == "ok"


async def test_health_lists_discovered_packs(
    client: httpx.AsyncClient, fake_packs: dict[str, PackManifest]
) -> None:
    response = await client.get("/v1/health")

    assert set(response.json()["packs"]) == set(fake_packs)


async def test_openapi_documents_every_route_with_a_summary(app: FastAPI) -> None:
    schema = app.openapi()

    missing = [
        f"{method.upper()} {path}"
        for path, operations in schema["paths"].items()
        for method, operation in operations.items()
        if not operation.get("summary")
    ]
    assert missing == []


async def test_openapi_exposes_the_expected_route_groups(app: FastAPI) -> None:
    paths = set(app.openapi()["paths"])

    assert {
        "/v1/providers",
        "/v1/packs",
        "/v1/credentials",
        "/v1/tools",
        "/v1/agents",
        "/v1/agents/{id_or_slug}/connect",
        "/v1/sessions",
        "/internal/v1/sessions/{session_id}/resolved",
        "/v1/health",
    } <= paths


async def test_unknown_route_uses_the_error_envelope(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/nope")

    assert response.status_code == 404
    assert set(response.json()["error"]) == {"code", "message", "details"}


async def test_providers_endpoint_lists_the_whole_registry(admin_client: httpx.AsyncClient) -> None:
    from lkap_contracts.providers import REGISTRY, mvp_providers

    body = (await admin_client.get("/v1/providers")).json()

    assert body["v"] == 2, "V2-06 bumped ProvidersResponse to v=2 (ProviderOut)"
    assert len(body["providers"]) == len(REGISTRY)
    assert len([p for p in body["providers"] if p["status"] == "mvp"]) == len(mvp_providers()) == 18
    assert {p["id"] for p in body["providers"]} >= {
        "livekit-inference-llm",
        "google-realtime",
        "http-tool-secret",
    }
    # Regression: `providers` must be genuine `ProviderOut` objects (this
    # broke the live endpoint with a 500 the moment `ProvidersResponse` was
    # retyped ahead of this router being updated to match).
    assert all(
        "enabled" in p and "installed_on" in p and "default_credential_id" in p for p in body["providers"]
    )


async def test_provider_detail_and_404(admin_client: httpx.AsyncClient) -> None:
    found = await admin_client.get("/v1/providers/google-realtime")
    missing = await admin_client.get("/v1/providers/nope")

    assert found.json()["python_class"] == "livekit.plugins.google.realtime.RealtimeModel"
    assert missing.status_code == 404


async def test_provider_specs_never_carry_secret_values(admin_client: httpx.AsyncClient) -> None:
    body = (await admin_client.get("/v1/providers")).json()

    for provider in body["providers"]:
        for field in provider["secret_fields"]:
            assert field["type"] == "secret"
            assert field.get("default") is None


async def test_catalogue_routes_require_an_admin_token(client: httpx.AsyncClient) -> None:
    assert (await client.get("/v1/providers")).status_code == 401
    assert (await client.get("/v1/packs")).status_code == 401


async def test_packs_endpoint_returns_manifests(
    admin_client: httpx.AsyncClient, fake_packs: dict[str, PackManifest]
) -> None:
    body = (await admin_client.get("/v1/packs")).json()

    assert {item["manifest"]["id"] for item in body["items"]} == set(fake_packs)
    assert body["items"][0]["manifest"]["v"] == 1


async def test_missing_packs_are_skipped_rather_than_fatal(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`LKAP_PACKS` may name a pack another work package has not landed yet."""
    from lkap_api.packs import clear_manifest_cache, discover_manifests

    clear_manifest_cache()
    try:
        assert discover_manifests(["packs.not_built_yet"]) == []
    finally:
        clear_manifest_cache()


async def test_knowledge_router_is_mounted(app: FastAPI) -> None:
    """W2-API-KB's `lkap_api.routers.knowledge` is picked up via the W1 import guard.

    Superseded by `main` not needing an edit at all (the guard already worked
    before W2-API-KB landed; see `test_knowledge_router_is_included_when_it_exists`
    below for that mechanism in isolation).
    """
    paths = app.openapi()["paths"]
    assert "/v1/knowledge-bases" in paths
    assert "/internal/v1/kb/search" in paths


async def test_knowledge_router_is_included_when_it_exists(
    settings: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys
    import types

    from fastapi import APIRouter

    module = types.ModuleType("lkap_api.routers.knowledge")
    router = APIRouter(prefix="/v1/knowledge-bases", tags=["knowledge"])

    @router.get("", summary="List knowledge bases")
    async def _list() -> dict[str, str]:
        return {"ok": "yes"}

    module.router = router  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "lkap_api.routers.knowledge", module)

    from lkap_api.main import create_app

    application = create_app(settings)  # type: ignore[arg-type]

    assert "/v1/knowledge-bases" in application.openapi()["paths"]


async def test_health_reports_db_error_before_migrations_run(
    app: FastAPI, settings: object, tmp_path: Any
) -> None:
    """`db: "error"` must also catch the "migrations were never run" case."""
    from lkap_api.db.session import Database

    empty = Database(f"sqlite+aiosqlite:///{tmp_path}/unmigrated.db")
    app.state.db = empty
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://api.test"
        ) as unmigrated:
            body = (await unmigrated.get("/v1/health")).json()
    finally:
        await empty.dispose()

    assert body["db"] == "error"
    assert body["ok"] is False
