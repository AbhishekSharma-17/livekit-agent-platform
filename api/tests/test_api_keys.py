"""`/v1/api-keys`: create (shown once), list, revoke, scopes (CONTRACTS-V2 §3.1, §3.4)."""

from __future__ import annotations

import datetime as dt

import httpx
from auth_helpers import key_client, login, make_user
from fastapi import FastAPI
from sqlalchemy import select

from lkap_api.auth.sessions import hash_token
from lkap_api.db.models import ApiKey, AuditLog, utcnow
from lkap_api.db.session import Database


async def test_create_api_key_returns_the_raw_key_once_and_stores_only_its_hash(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    response = await admin_client.post("/v1/api-keys", json={"name": "CI", "scopes": ["agents:read"]})

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["key"].startswith("lkap_")
    assert len(body["key"]) > 40
    assert body["prefix"] == body["key"][:8]
    assert body["scopes"] == ["agents:read"]
    assert body["created_by"] == "break-glass"
    async with database.session() as session:
        row = await session.get(ApiKey, body["id"])
    assert row is not None
    assert row.key_hash == hash_token(body["key"])

    listed = (await admin_client.get("/v1/api-keys")).json()
    assert listed["total"] == 1
    assert "key" not in listed["items"][0]


async def test_created_key_authenticates_and_revoke_stops_it(
    app: FastAPI, admin_client: httpx.AsyncClient, database: Database
) -> None:
    created = (await admin_client.post("/v1/api-keys", json={"name": "k", "scopes": ["agents:read"]})).json()

    async with key_client(app, created["key"]) as client:
        before = await client.get("/v1/agents")
        revoked = await admin_client.delete(f"/v1/api-keys/{created['id']}")
        again = await admin_client.delete(f"/v1/api-keys/{created['id']}")
        after = await client.get("/v1/agents")

    assert before.status_code == 200
    assert revoked.status_code == 204
    assert again.status_code == 204
    assert after.status_code == 401
    async with database.session() as session:
        actions = (await session.execute(select(AuditLog.action).order_by(AuditLog.id))).scalars().all()
    assert actions.count("api_key.create") == 1
    assert actions.count("api_key.revoke") == 1


async def test_create_api_key_rejects_unknown_scopes_and_past_expiry(admin_client: httpx.AsyncClient) -> None:
    unknown = await admin_client.post("/v1/api-keys", json={"name": "k", "scopes": ["root"]})
    empty = await admin_client.post("/v1/api-keys", json={"name": "k", "scopes": []})
    past = await admin_client.post(
        "/v1/api-keys",
        json={"name": "k", "scopes": ["*"], "expires_at": (utcnow() - dt.timedelta(days=1)).isoformat()},
    )

    assert unknown.status_code == 422
    assert empty.status_code == 422
    assert past.status_code == 422


async def test_builder_cannot_manage_api_keys(app: FastAPI, database: Database) -> None:
    await make_user(database, "builder@example.com", role="builder")

    async with await login(app, "builder@example.com") as builder:
        listed = await builder.get("/v1/api-keys")
        created = await builder.post("/v1/api-keys", json={"name": "k", "scopes": ["*"]})

    assert listed.status_code == 403
    assert created.status_code == 403


async def test_admin_member_can_manage_api_keys_and_is_recorded_as_creator(
    app: FastAPI, database: Database
) -> None:
    admin_id = await make_user(database, "admin@example.com", role="admin")

    async with await login(app, "admin@example.com") as admin:
        created = await admin.post("/v1/api-keys", json={"name": "k", "scopes": ["sessions:read"]})

    assert created.status_code == 201
    assert created.json()["created_by"] == admin_id


async def test_scoped_api_key_cannot_mint_more_keys(app: FastAPI, admin_client: httpx.AsyncClient) -> None:
    created = (await admin_client.post("/v1/api-keys", json={"name": "k", "scopes": ["agents:write"]})).json()

    async with key_client(app, created["key"]) as client:
        response = await client.post("/v1/api-keys", json={"name": "k2", "scopes": ["*"]})

    assert response.status_code == 403
