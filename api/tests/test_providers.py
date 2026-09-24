"""`routers/providers.py`: workspace enablement, `installed_on` and vendor catalogs (V2-06)."""

from __future__ import annotations

import httpx
import respx
from connection_fakes import connection_row

from lkap_api.db.session import Database
from lkap_api.settings import Settings
from lkap_api.vault import Vault

BEY_URL = "https://api.bey.dev/v1/avatars"


async def _create_credential(
    admin_client: httpx.AsyncClient, *, provider_id: str = "bey-avatar", api_key: str = "bey-secret-1"
) -> str:
    response = await admin_client.post(
        "/v1/credentials",
        json={"provider_id": provider_id, "label": "Bey", "secrets": {"api_key": api_key}},
    )
    assert response.status_code == 201, response.text
    credential_id: str = response.json()["id"]
    return credential_id


# --------------------------------------------------------------------------------- list
async def test_list_filters_by_kind_availability_and_enabled(admin_client: httpx.AsyncClient) -> None:
    avatars = (await admin_client.get("/v1/providers", params={"kind": "avatar"})).json()
    deferred = (await admin_client.get("/v1/providers", params={"availability": "deferred"})).json()

    assert all(p["kind"] == "avatar" for p in avatars["providers"])
    assert {"bey-avatar", "simli-avatar"} <= {p["id"] for p in avatars["providers"]}
    assert all(p["availability"] == "deferred" for p in deferred["providers"])
    assert deferred["providers"], "PlayAI and friends should show up as deferred"


async def test_absence_of_a_workspace_providers_row_means_enabled(admin_client: httpx.AsyncClient) -> None:
    body = (await admin_client.get("/v1/providers", params={"enabled": "true"})).json()

    assert len(body["providers"]) > 100, "every entry is enabled until an admin disables one"


# --------------------------------------------------------------------------------- settings
async def test_settings_disable_then_reenable_a_provider(admin_client: httpx.AsyncClient) -> None:
    disabled = await admin_client.put("/v1/providers/bey-avatar/settings", json={"enabled": False})
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False

    listed = (await admin_client.get("/v1/providers", params={"enabled": "false"})).json()
    assert [p["id"] for p in listed["providers"]] == ["bey-avatar"]

    reenabled = await admin_client.put("/v1/providers/bey-avatar/settings", json={"enabled": True})
    assert reenabled.json()["enabled"] is True


async def test_settings_sets_a_default_credential(admin_client: httpx.AsyncClient) -> None:
    credential_id = await _create_credential(admin_client)

    response = await admin_client.put(
        "/v1/providers/bey-avatar/settings", json={"default_credential_id": credential_id}
    )

    assert response.status_code == 200
    assert response.json()["default_credential_id"] == credential_id


async def test_settings_rejects_a_credential_belonging_to_a_different_provider(
    admin_client: httpx.AsyncClient,
) -> None:
    credential_id = await _create_credential(admin_client, provider_id="openai-llm", api_key="sk-x")

    response = await admin_client.put(
        "/v1/providers/bey-avatar/settings", json={"default_credential_id": credential_id}
    )

    assert response.status_code == 422


async def test_settings_404s_for_an_unknown_provider(admin_client: httpx.AsyncClient) -> None:
    response = await admin_client.put("/v1/providers/not-a-provider/settings", json={"enabled": False})

    assert response.status_code == 404


async def test_settings_requires_an_admin_token(client: httpx.AsyncClient) -> None:
    response = await client.put("/v1/providers/bey-avatar/settings", json={"enabled": False})

    assert response.status_code == 401


# ---------------------------------------------------------------------------- installed_on
async def test_installed_on_reflects_the_connections_worker_image(
    admin_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    vault = Vault(settings.master_key)
    async with database.session() as session:
        slim_conn = connection_row(vault, slug="slim-conn", worker_image="slim")
        full_conn = connection_row(vault, slug="full-conn", worker_image="full")
        session.add(slim_conn)
        session.add(full_conn)
        await session.flush()
        slim_id, full_id = slim_conn.id, full_conn.id

    body = (await admin_client.get("/v1/providers")).json()
    by_id = {p["id"]: p for p in body["providers"]}

    # bey-avatar and simli-avatar ship in the `slim` worker image, so every pool carries them.
    assert {slim_id, full_id} <= set(by_id["bey-avatar"]["installed_on"])
    assert {slim_id, full_id} <= set(by_id["simli-avatar"]["installed_on"])
    # anam-avatar is `worker_image="full"` (V2-05): only the full-image pool can build it.
    assert full_id in by_id["anam-avatar"]["installed_on"]
    assert slim_id not in by_id["anam-avatar"]["installed_on"]


# ------------------------------------------------------------------------------- catalog
async def test_catalog_endpoint_fetches_caches_and_a_second_call_hits_the_cache(
    admin_client: httpx.AsyncClient,
) -> None:
    credential_id = await _create_credential(admin_client)
    await admin_client.put("/v1/providers/bey-avatar/settings", json={"default_credential_id": credential_id})

    with respx.mock:
        route = respx.get(BEY_URL).mock(return_value=httpx.Response(200, json=[{"id": "a1", "name": "Ava"}]))
        first = await admin_client.get("/v1/providers/bey-avatar/catalog")
        second = await admin_client.get("/v1/providers/bey-avatar/catalog")

    assert first.status_code == second.status_code == 200
    assert route.call_count == 1, "the second call must be served from the cache, not the vendor"
    body = first.json()
    assert body["kind"] == "avatars"
    assert body["source"] == "vendor"
    assert body["error"] is None
    assert body["fetched_at"] is not None, "the live fetch must stamp fetched_at, not just the cache hit"
    assert [i["id"] for i in body["items"]] == ["a1"]
    assert second.json()["items"] == body["items"]
    assert second.json()["fetched_at"] == body["fetched_at"], "the cache hit replays the same timestamp"


async def test_catalog_endpoint_refresh_bypasses_a_fresh_cache_entry(admin_client: httpx.AsyncClient) -> None:
    credential_id = await _create_credential(admin_client)
    await admin_client.put("/v1/providers/bey-avatar/settings", json={"default_credential_id": credential_id})

    with respx.mock:
        route = respx.get(BEY_URL).mock(return_value=httpx.Response(200, json=[{"id": "a1"}]))
        await admin_client.get("/v1/providers/bey-avatar/catalog")
        await admin_client.get("/v1/providers/bey-avatar/catalog", params={"refresh": "true"})

    assert route.call_count == 2


async def test_catalog_endpoint_falls_back_to_a_stale_cache_on_vendor_failure(
    admin_client: httpx.AsyncClient,
) -> None:
    credential_id = await _create_credential(admin_client)
    await admin_client.put("/v1/providers/bey-avatar/settings", json={"default_credential_id": credential_id})

    with respx.mock:
        respx.get(BEY_URL).mock(return_value=httpx.Response(200, json=[{"id": "a1", "name": "Ava"}]))
        warm = await admin_client.get("/v1/providers/bey-avatar/catalog")
    assert warm.status_code == 200

    with respx.mock:
        respx.get(BEY_URL).mock(return_value=httpx.Response(500))
        stale = await admin_client.get("/v1/providers/bey-avatar/catalog", params={"refresh": "true"})

    assert stale.status_code == 200, "a vendor failure must never break the providers page"
    body = stale.json()
    assert [i["id"] for i in body["items"]] == ["a1"], "served the last-known-good cache"
    assert body["error"] is not None


async def test_catalog_endpoint_falls_back_to_the_static_model_list_with_no_cache(
    admin_client: httpx.AsyncClient,
) -> None:
    credential_id = await _create_credential(admin_client, provider_id="openai-llm", api_key="sk-bad")
    await admin_client.put("/v1/providers/openai-llm/settings", json={"default_credential_id": credential_id})

    with respx.mock:
        respx.get("https://api.openai.com/v1/models").mock(return_value=httpx.Response(500))
        response = await admin_client.get("/v1/providers/openai-llm/catalog")

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "static"
    assert body["error"] is not None
    assert body["items"], "the registry's own suggestion list (ProviderSpec.models) is the last resort"


async def test_catalog_endpoint_with_no_credential_serves_the_static_list_and_makes_no_call(
    admin_client: httpx.AsyncClient,
) -> None:
    with respx.mock:
        route = respx.get("https://api.openai.com/v1/models")
        response = await admin_client.get("/v1/providers/openai-llm/catalog")

    assert response.status_code == 200
    assert route.call_count == 0
    body = response.json()
    assert body["source"] == "static"
    assert body["error"] is None, "no credential yet is the expected steady state, not a failure"


async def test_catalog_endpoint_422_for_a_provider_with_no_catalog(admin_client: httpx.AsyncClient) -> None:
    response = await admin_client.get("/v1/providers/azure-tts/catalog")

    assert response.status_code == 422


async def test_catalog_endpoint_422_for_an_unsupported_kind(admin_client: httpx.AsyncClient) -> None:
    response = await admin_client.get("/v1/providers/bey-avatar/catalog", params={"kind": "personas"})

    assert response.status_code == 422


async def test_catalog_endpoint_rejects_a_credential_from_a_different_provider(
    admin_client: httpx.AsyncClient,
) -> None:
    credential_id = await _create_credential(admin_client, provider_id="openai-llm", api_key="sk-x")

    response = await admin_client.get(
        "/v1/providers/bey-avatar/catalog", params={"credential_id": credential_id}
    )

    assert response.status_code == 422


async def test_catalog_endpoint_requires_an_admin_token(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/providers/bey-avatar/catalog")

    assert response.status_code == 401


async def test_catalog_endpoint_never_leaks_the_credential_secret(admin_client: httpx.AsyncClient) -> None:
    credential_id = await _create_credential(admin_client, api_key="super-secret-bey-key")
    await admin_client.put("/v1/providers/bey-avatar/settings", json={"default_credential_id": credential_id})

    with respx.mock:
        respx.get(BEY_URL).mock(return_value=httpx.Response(200, json=[{"id": "a1"}]))
        response = await admin_client.get("/v1/providers/bey-avatar/catalog")

    assert "super-secret-bey-key" not in response.text
