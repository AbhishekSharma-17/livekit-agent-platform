"""Workspaces, members, role changes and the audit log (CONTRACTS-V2 §3.2, §3.4)."""

from __future__ import annotations

import httpx
from auth_helpers import login, make_user
from conftest import create_agent
from fastapi import FastAPI

from lkap_api.db.session import Database


async def test_list_workspaces_for_a_member_shows_their_role(app: FastAPI, database: Database) -> None:
    await make_user(database, "v@example.com", role="viewer")

    async with await login(app, "v@example.com") as client:
        body = (await client.get("/v1/workspaces")).json()

    assert body["total"] == 1
    assert body["items"][0]["slug"] == "default"
    assert body["items"][0]["role"] == "viewer"


async def test_update_workspace_needs_admin_and_merges_settings(app: FastAPI, database: Database) -> None:
    await make_user(database, "b@example.com", role="builder")
    await make_user(database, "a@example.com", role="admin")

    async with await login(app, "b@example.com") as builder:
        refused = await builder.put("/v1/workspaces/default", json={"name": "Nope"})
    async with await login(app, "a@example.com") as admin:
        first = await admin.put("/v1/workspaces/default", json={"settings": {"timezone": "Asia/Kolkata"}})
        second = await admin.put("/v1/workspaces/default", json={"name": "Acme"})

    assert refused.status_code == 403
    assert first.status_code == 200
    assert second.json()["name"] == "Acme"
    assert second.json()["settings"] == {"timezone": "Asia/Kolkata"}


async def test_members_are_listed_to_viewers(app: FastAPI, database: Database) -> None:
    await make_user(database, "v@example.com", role="viewer")

    async with await login(app, "v@example.com") as client:
        body = (await client.get("/v1/workspaces/default/members")).json()

    emails = {m["email"] for m in body["items"]}
    assert "v@example.com" in emails
    assert "owner@local" in emails


async def test_admin_adds_and_reroles_a_member_but_cannot_grant_owner(
    app: FastAPI, database: Database
) -> None:
    await make_user(database, "a@example.com", role="admin")
    user_id = await make_user(database, "x@example.com", role=None)

    async with await login(app, "a@example.com") as admin:
        added = await admin.post(
            "/v1/workspaces/default/members", json={"email": "x@example.com", "role": "viewer"}
        )
        duplicate = await admin.post(
            "/v1/workspaces/default/members", json={"email": "x@example.com", "role": "viewer"}
        )
        promoted = await admin.put(f"/v1/workspaces/default/members/{user_id}", json={"role": "builder"})
        to_owner = await admin.put(f"/v1/workspaces/default/members/{user_id}", json={"role": "owner"})
        unknown = await admin.post(
            "/v1/workspaces/default/members", json={"email": "ghost@example.com", "role": "viewer"}
        )

    assert added.status_code == 201
    assert duplicate.status_code == 409
    assert promoted.json()["role"] == "builder"
    assert to_owner.status_code == 403
    assert unknown.status_code == 404


async def test_last_owner_cannot_be_demoted_or_removed(app: FastAPI, database: Database) -> None:
    owner_id = await make_user(database, "o@example.com", role="owner")

    async with await login(app, "o@example.com") as owner:
        members = (await owner.get("/v1/workspaces/default/members")).json()["items"]
        bootstrap_owner = next(m["user_id"] for m in members if m["email"] == "owner@local")
        # Two owners: removing one is fine.
        removed = await owner.delete(f"/v1/workspaces/default/members/{bootstrap_owner}")
        demote_self = await owner.put(f"/v1/workspaces/default/members/{owner_id}", json={"role": "admin"})
        remove_self = await owner.delete(f"/v1/workspaces/default/members/{owner_id}")

    assert removed.status_code == 204
    assert demote_self.status_code == 409
    assert remove_self.status_code == 409


async def test_audit_log_records_v1_mutations_and_is_admin_only(
    app: FastAPI, admin_client: httpx.AsyncClient, database: Database
) -> None:
    agent = await create_agent(admin_client, published=True)
    await make_user(database, "b@example.com", role="builder")

    async with await login(app, "b@example.com") as builder:
        refused = await builder.get("/v1/audit")
    body = (await admin_client.get("/v1/audit", params={"limit": 50})).json()

    assert refused.status_code == 403
    actions = [(row["action"], row["actor_type"], row["actor_id"], row["target_id"]) for row in body["items"]]
    assert ("POST /v1/agents", "system", "break-glass", None) in actions
    assert ("PUT /v1/agents/{agent_id}", "system", "break-glass", agent["id"]) in actions  # the publish
    assert all(row["workspace_id"] == "00000000000000000000000000000001" for row in body["items"])


async def test_viewer_cannot_invite(app: FastAPI, database: Database) -> None:
    await make_user(database, "v@example.com", role="viewer")

    async with await login(app, "v@example.com") as viewer:
        response = await viewer.post("/v1/workspaces/default/invites", json={"email": "n@example.com"})

    assert response.status_code == 403


async def test_admin_cannot_invite_an_owner_but_owner_can(app: FastAPI, database: Database) -> None:
    await make_user(database, "a@example.com", role="admin")
    await make_user(database, "o@example.com", role="owner")

    async with await login(app, "a@example.com") as admin:
        refused = await admin.post(
            "/v1/workspaces/default/invites", json={"email": "n@example.com", "role": "owner"}
        )
    async with await login(app, "o@example.com") as owner:
        allowed = await owner.post(
            "/v1/workspaces/default/invites", json={"email": "n@example.com", "role": "owner"}
        )

    assert refused.status_code == 403
    assert allowed.status_code == 201
