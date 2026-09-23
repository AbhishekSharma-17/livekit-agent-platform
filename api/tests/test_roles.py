"""The role matrix and API-key scopes on the admin surface (CONTRACTS-V2 §3.1–3.2)."""

from __future__ import annotations

import datetime as dt

import httpx
import pytest
from auth_helpers import key_client, login, make_api_key, make_user
from conftest import create_agent, inference_config
from fastapi import FastAPI
from sqlalchemy import select

from lkap_api.auth.roles import DEFAULT_REQUIREMENT, Requirement, policy_for, role_at_least, scope_allows
from lkap_api.db.models import AuditLog, utcnow
from lkap_api.db.session import Database


# ------------------------------------------------------------------ unit rules
@pytest.mark.parametrize(
    ("role", "minimum", "expected"),
    [
        ("viewer", "viewer", True),
        ("viewer", "builder", False),
        ("builder", "builder", True),
        ("admin", "builder", True),
        ("owner", "admin", True),
        ("admin", "owner", False),
        (None, "viewer", False),
        ("superuser", "viewer", False),
    ],
)
def test_role_at_least_orders_viewer_builder_admin_owner(
    role: str | None, minimum: str, expected: bool
) -> None:
    assert role_at_least(role, minimum) is expected  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("scopes", "needed", "expected"),
    [
        (["*"], "agents:write", True),
        (["agents:read"], "agents:read", True),
        (["agents:write"], "agents:read", True),
        (["agents:read"], "agents:write", False),
        (["sessions:read"], "agents:read", False),
        (["agents:write"], "*", False),
    ],
)
def test_scope_allows_star_and_write_implies_read(scopes: list[str], needed: str, expected: bool) -> None:
    assert scope_allows(scopes, needed) is expected


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("GET", "/v1/agents", Requirement("viewer", "agents:read")),
        ("PUT", "/v1/agents/{agent_id}", Requirement("builder", "agents:write")),
        ("GET", "/v1/sessions/{session_id}", Requirement("viewer", "sessions:read")),
        ("GET", "/v1/credentials", Requirement("builder", "providers:read")),
        ("POST", "/v1/credentials", Requirement("admin", "providers:write")),
        ("POST", "/v1/connections/{connection_id}/rotate", Requirement("admin", "connections:write")),
        ("GET", "/v1/agentsx", DEFAULT_REQUIREMENT),
        ("DELETE", "/v1/something-new", DEFAULT_REQUIREMENT),
    ],
)
def test_policy_for_matches_route_templates_and_fails_closed(
    method: str, path: str, expected: Requirement
) -> None:
    assert policy_for(method, path) == expected


# ------------------------------------------------------------------ over HTTP
async def test_unauthenticated_agents_list_is_401(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/agents")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_viewer_can_read_but_cannot_edit_an_agent(
    app: FastAPI, admin_client: httpx.AsyncClient, database: Database
) -> None:
    agent = await create_agent(admin_client, published=False)
    await make_user(database, "viewer@example.com", role="viewer")

    async with await login(app, "viewer@example.com") as viewer:
        listed = await viewer.get("/v1/agents")
        fetched = await viewer.get(f"/v1/agents/{agent['id']}")
        edited = await viewer.put(f"/v1/agents/{agent['id']}", json={"name": "Nope"})

    assert listed.status_code == 200
    assert fetched.status_code == 200
    assert "config" in fetched.json()  # members get the admin view, not the public one
    assert edited.status_code == 403
    assert edited.json()["error"]["details"]["required_role"] == "builder"


async def test_builder_can_edit_an_agent_and_the_change_is_audited(
    app: FastAPI, admin_client: httpx.AsyncClient, database: Database
) -> None:
    agent = await create_agent(admin_client, published=False)
    builder_id = await make_user(database, "builder@example.com", role="builder")

    async with await login(app, "builder@example.com") as builder:
        response = await builder.put(f"/v1/agents/{agent['id']}", json={"name": "Renamed"})

    assert response.status_code == 200
    assert response.json()["name"] == "Renamed"
    async with database.session() as session:
        rows = (
            (await session.execute(select(AuditLog).where(AuditLog.action == "PUT /v1/agents/{agent_id}")))
            .scalars()
            .all()
        )
    assert [(r.actor_type, r.actor_id, r.target_type, r.target_id) for r in rows] == [
        ("user", builder_id, "agents", agent["id"])
    ]


async def test_failed_mutation_is_not_audited(app: FastAPI, database: Database) -> None:
    await make_user(database, "viewer2@example.com", role="viewer")
    async with await login(app, "viewer2@example.com") as viewer:
        await viewer.post("/v1/agents", json={"name": "x", "config": None})

    async with database.session() as session:
        assert (await session.execute(select(AuditLog).where(AuditLog.action.like("POST %")))).first() is None


async def test_builder_cannot_create_credentials_but_admin_can(app: FastAPI, database: Database) -> None:
    await make_user(database, "b@example.com", role="builder")
    await make_user(database, "a@example.com", role="admin")
    body = {"provider_id": "openai-llm", "label": "k", "secrets": {"api_key": "sk-test-123"}}

    async with await login(app, "b@example.com") as builder:
        refused = await builder.post("/v1/credentials", json=body)
        listed = await builder.get("/v1/credentials")
    async with await login(app, "a@example.com") as admin:
        created = await admin.post("/v1/credentials", json=body)

    assert refused.status_code == 403
    assert listed.status_code == 200
    assert created.status_code == 201, created.text


async def test_api_key_with_sessions_read_cannot_create_agents(app: FastAPI, database: Database) -> None:
    _, raw = await make_api_key(database, ["sessions:read"])
    payload = {"name": "From a key", "config": inference_config().model_dump(mode="json")}

    async with key_client(app, raw) as client:
        sessions = await client.get("/v1/sessions")
        created = await client.post("/v1/agents", json=payload)

    assert sessions.status_code == 200
    assert created.status_code == 403
    assert created.json()["error"]["details"]["required_scope"] == "agents:write"


async def test_api_key_with_agents_write_can_create_agents_and_is_audited(
    app: FastAPI, database: Database
) -> None:
    key_id, raw = await make_api_key(database, ["agents:write"])
    payload = {"name": "From a key", "config": inference_config().model_dump(mode="json")}

    async with key_client(app, raw) as client:
        created = await client.post("/v1/agents", json=payload)
        listed = await client.get("/v1/agents")  # write implies read

    assert created.status_code == 201, created.text
    assert listed.status_code == 200
    async with database.session() as session:
        row = (
            await session.execute(select(AuditLog).where(AuditLog.action == "POST /v1/agents"))
        ).scalar_one()
    assert (row.actor_type, row.actor_id) == ("api_key", key_id)


@pytest.mark.parametrize(
    "columns",
    [
        {"revoked_at": utcnow()},
        {"expires_at": utcnow() - dt.timedelta(minutes=1)},
    ],
)
async def test_revoked_or_expired_api_key_is_401(
    app: FastAPI, database: Database, columns: dict[str, object]
) -> None:
    _, raw = await make_api_key(database, ["*"], **columns)

    async with key_client(app, raw) as client:
        response = await client.get("/v1/agents")

    assert response.status_code == 401


async def test_unknown_api_key_is_401(app: FastAPI) -> None:
    async with key_client(app, "lkap_this-key-does-not-exist") as client:
        response = await client.get("/v1/agents")

    assert response.status_code == 401


async def test_api_key_requests_are_rate_limited(
    app: FastAPI, database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lkap_api.settings import get_settings

    monkeypatch.setenv("LKAP_API_KEY_RATE_PER_MIN", "3")
    get_settings.cache_clear()
    _, raw = await make_api_key(database, ["*"])

    async with key_client(app, raw) as client:
        statuses = [(await client.get("/v1/agents")).status_code for _ in range(4)]

    assert statuses == [200, 200, 200, 429]


async def test_user_without_any_workspace_is_403(app: FastAPI, database: Database) -> None:
    await make_user(database, "loner@example.com", role=None)

    async with await login(app, "loner@example.com") as client:
        response = await client.get("/v1/agents")

    assert response.status_code == 403
