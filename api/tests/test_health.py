"""Application surface: health, OpenAPI, the provider/pack catalogues and the W2 seam."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from lkap_contracts.packs import PackManifest

from lkap_api import __version__


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
    }


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

    assert body["v"] == 1
    assert len(body["providers"]) == len(REGISTRY)
    assert len([p for p in body["providers"] if p["status"] == "mvp"]) == len(mvp_providers()) == 18
    assert {p["id"] for p in body["providers"]} >= {
        "livekit-inference-llm",
        "google-realtime",
        "http-tool-secret",
    }


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
