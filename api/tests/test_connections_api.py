"""`/v1/connections` CRUD, default handling, rotation and the `fleet_desired` writer (V2-03)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from conftest import inference_config
from connection_fakes import KEY_B, SECRET_B, add_agent, connection_row
from lkap_contracts.api_models import ConnectionCreate, ConnectionUpdate
from sqlalchemy import select

from lkap_api.connections import service
from lkap_api.db.constants import DEFAULT_WORKSPACE_ID
from lkap_api.db.models import FleetDesiredState, LiveKitConnection, Workspace
from lkap_api.db.session import Database
from lkap_api.errors import ConflictError
from lkap_api.settings import Settings
from lkap_api.vault import Vault

PACKS = ["packs.generic", "packs.insurance_claim"]


def _payload(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "slug": "cloud-a",
        "name": "Cloud A",
        "deployment_type": "cloud",
        "url": "wss://project-a.livekit.cloud",
        "api_key": KEY_B,
        "api_secret": SECRET_B,
    }
    body.update(overrides)
    return body


async def _create(admin_client: httpx.AsyncClient, **overrides: Any) -> dict[str, Any]:
    response = await admin_client.post("/v1/connections", json=_payload(**overrides))
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


async def _state(database: Database, connection_id: str) -> FleetDesiredState:
    async with database.session() as session:
        state = await session.get(FleetDesiredState, connection_id)
    assert state is not None
    return state


async def test_create_connection_returns_fingerprint_and_never_the_secret(
    admin_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    response = await admin_client.post("/v1/connections", json=_payload())

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["fingerprint"] == "…" + KEY_B[-4:]
    assert body["status"] == "unverified"
    assert body["credentials_version"] == 1
    assert SECRET_B not in response.text and KEY_B not in response.text
    async with database.session() as session:
        row = await session.get(LiveKitConnection, body["id"])
    assert row is not None
    assert SECRET_B.encode() not in bytes(row.api_secret_ct)
    assert Vault(settings.master_key).decrypt(row.api_secret_ct) == {"api_secret": SECRET_B}


async def test_create_connection_second_is_not_default_and_list_puts_default_first(
    admin_client: httpx.AsyncClient,
) -> None:
    created = await _create(admin_client)

    listing = (await admin_client.get("/v1/connections")).json()

    assert created["is_default"] is False
    assert listing["total"] == 2
    assert listing["items"][0]["is_default"] is True
    assert listing["items"][0]["slug"] == "default"


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"url": "ftp://example.com"}, "url"),
        ({"url": "not a url"}, "url"),
        ({"slug": "Bad Slug"}, "slug"),
        ({"agent_name": "has space"}, "agent_name"),
        ({"deployment_type": "self_hosted", "deployment_mode": "cloud_hosted"}, "deployment_mode"),
        ({"replicas": 99}, "replicas"),
    ],
)
async def test_create_connection_invalid_values_are_422(
    admin_client: httpx.AsyncClient, overrides: dict[str, Any], field: str
) -> None:
    response = await admin_client.post("/v1/connections", json=_payload(**overrides))

    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["field"] == field


async def test_create_connection_duplicate_slug_is_409(admin_client: httpx.AsyncClient) -> None:
    await _create(admin_client)

    response = await admin_client.post("/v1/connections", json=_payload(name="Again"))

    assert response.status_code == 409


async def test_connection_routes_without_admin_token_are_401(client: httpx.AsyncClient) -> None:
    assert (await client.get("/v1/connections")).status_code == 401
    assert (await client.post("/v1/connections", json=_payload())).status_code == 401


async def test_get_connection_by_slug_and_other_workspace_is_404(
    admin_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    created = await _create(admin_client)
    async with database.session() as session:
        other = Workspace(id="f" * 32, slug="other", name="Other")
        session.add(other)
        await session.flush()
        foreign = connection_row(Vault(settings.master_key), slug="foreign", workspace_id=other.id)
        session.add(foreign)

    assert (await admin_client.get("/v1/connections/cloud-a")).json()["id"] == created["id"]
    assert (await admin_client.get(f"/v1/connections/{foreign.id}")).status_code == 404


async def test_set_default_moves_the_flag(admin_client: httpx.AsyncClient) -> None:
    created = await _create(admin_client)

    response = await admin_client.post(f"/v1/connections/{created['id']}/default")

    assert response.status_code == 200
    items = (await admin_client.get("/v1/connections")).json()["items"]
    assert [item["slug"] for item in items if item["is_default"]] == ["cloud-a"]


async def test_delete_connection_with_bound_agents_is_409_and_unbound_is_204(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    created = await _create(admin_client)
    async with database.session() as session:
        agent = await add_agent(session, inference_config(), connection_id=created["id"])

    blocked = await admin_client.delete(f"/v1/connections/{created['id']}")
    async with database.session() as session:
        bound = await session.get(type(agent), agent.id)
        assert bound is not None
        bound.connection_id = None
    deleted = await admin_client.delete(f"/v1/connections/{created['id']}")

    assert blocked.status_code == 409
    assert blocked.json()["error"]["details"]["agents_bound"] == 1
    assert deleted.status_code == 204
    assert (await admin_client.get(f"/v1/connections/{created['id']}")).status_code == 404


async def test_delete_default_connection_is_409(admin_client: httpx.AsyncClient) -> None:
    response = await admin_client.delete("/v1/connections/default")

    assert response.status_code == 409


async def test_rotate_bumps_credentials_version_and_desired_hash(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    created = await _create(admin_client, deployment_mode="supervised", replicas=2)
    before = await _state(database, created["id"])

    response = await admin_client.post(
        f"/v1/connections/{created['id']}/rotate",
        json={"api_key": "APInewkey9876", "api_secret": "a-brand-new-secret-value-long-enough"},
    )

    body = response.json()
    after = await _state(database, created["id"])
    assert response.status_code == 200, response.text
    assert body["credentials_version"] == 2
    assert body["fingerprint"] == "…9876"
    assert body["status"] == "unverified"
    assert after.desired_hash != before.desired_hash
    assert after.desired_replicas == before.desired_replicas == 2


async def test_update_url_resets_status_and_changes_desired_hash(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    created = await _create(admin_client)
    async with database.session() as session:
        row = await session.get(LiveKitConnection, created["id"])
        assert row is not None
        row.status = "ok"
        row.capabilities = {"sip_enabled": False}
    before = await _state(database, created["id"])

    response = await admin_client.put(
        f"/v1/connections/{created['id']}", json={"url": "wss://project-c.livekit.cloud/"}
    )

    body = response.json()
    assert response.status_code == 200, response.text
    assert body["url"] == "wss://project-c.livekit.cloud"
    assert body["status"] == "unverified"
    assert body["capabilities"]["sip_enabled"] is True
    assert (await _state(database, created["id"])).desired_hash != before.desired_hash


async def test_fleet_desired_replicas_follow_mode_and_survive_rotation(
    database: Database, settings: Settings
) -> None:
    vault = Vault(settings.master_key)
    async with database.session() as session:
        row = await service.create_connection(
            session,
            vault,
            DEFAULT_WORKSPACE_ID,
            ConnectionCreate(**_payload(deployment_mode="supervised", replicas=2)),
            PACKS,
        )
        on_create = (await service.write_fleet_desired(session, row, PACKS)).desired_replicas
        on_stop = (await service.set_desired_replicas(session, row, 0, PACKS)).desired_replicas
        await service.rotate_credentials(
            session, vault, DEFAULT_WORKSPACE_ID, row.id, api_key="k2-abcd", api_secret="s2", packs=PACKS
        )
        after_rotate = (await service.write_fleet_desired(session, row, PACKS)).desired_replicas
        await service.update_connection(
            session, DEFAULT_WORKSPACE_ID, row.id, ConnectionUpdate(replicas=3), PACKS
        )
        after_resize = (await service.write_fleet_desired(session, row, PACKS)).desired_replicas
        await service.update_connection(
            session, DEFAULT_WORKSPACE_ID, row.id, ConnectionUpdate(deployment_mode="external"), PACKS
        )
        after_external = (await service.write_fleet_desired(session, row, PACKS)).desired_replicas

    assert (on_create, on_stop, after_rotate, after_resize, after_external) == (2, 0, 0, 3, 0)


async def test_set_desired_replicas_on_external_connection_conflicts(
    database: Database, settings: Settings
) -> None:
    async with database.session() as session:
        default = await service.default_connection(session, DEFAULT_WORKSPACE_ID)
        assert default is not None
        with pytest.raises(ConflictError):
            await service.set_desired_replicas(session, default, 1, PACKS)


async def test_list_fleet_desired_returns_supervised_only_and_rehashes_on_pack_change(
    database: Database, settings: Settings
) -> None:
    vault = Vault(settings.master_key)
    async with database.session() as session:
        row = await service.create_connection(
            session,
            vault,
            DEFAULT_WORKSPACE_ID,
            ConnectionCreate(**_payload(deployment_mode="supervised", replicas=1, worker_image="full")),
            PACKS,
        )
        first = await service.list_fleet_desired(session, PACKS)
        reordered = await service.list_fleet_desired(session, list(reversed(PACKS)))
        changed = await service.list_fleet_desired(session, ["packs.generic"])

    assert [d.connection_id for d in first] == [row.id]
    assert first[0].image == "full"
    assert first[0].agent_name == "lkap-agent"
    assert first[0].desired_replicas == 1
    assert reordered[0].desired_hash == first[0].desired_hash
    assert changed[0].desired_hash != first[0].desired_hash
    assert first[0].desired_hash == service.compute_desired_hash(
        url=row.url, credentials_version=1, agent_name="lkap-agent", image="full", packs=PACKS
    )


async def test_connection_service_queries_are_workspace_scoped(
    database: Database, settings: Settings, tenant_guard: None
) -> None:
    vault = Vault(settings.master_key)
    async with database.session() as session:
        row = await service.create_connection(
            session, vault, DEFAULT_WORKSPACE_ID, ConnectionCreate(**_payload()), PACKS
        )
        await service.list_connections(session, DEFAULT_WORKSPACE_ID)
        await service.get_connection(session, DEFAULT_WORKSPACE_ID, row.slug)
        await service.set_default_connection(session, DEFAULT_WORKSPACE_ID, row.id, PACKS)
        await service.update_connection(
            session, DEFAULT_WORKSPACE_ID, row.id, ConnectionUpdate(name="Renamed"), PACKS
        )
        rows = (
            await session.execute(
                select(LiveKitConnection).where(LiveKitConnection.workspace_id == DEFAULT_WORKSPACE_ID)
            )
        ).scalars()
    assert {r.slug for r in rows} == {"default", "cloud-a"}
