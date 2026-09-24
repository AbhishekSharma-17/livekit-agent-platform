"""Two-workspace isolation (CONTRACTS-V2 §3.1: cross-workspace reads are 404, never 403).

Workspace A is the bootstrapped ``default``; workspace B (``beta``) is created
directly in the database because Phase 1 has no create-workspace route. Alice
administers A, Bob administers B.

Covered: every route V2-02 owns, the agents router and V2-03's connections
(asks #20/#21). Tests that go through scoped code run under ``tenant_guard``,
which fails any unscoped tenant query (requested after ``world`` so fixture
setup is not checked). Still unscoped, and therefore not asserted here:
sessions, credentials, tools and knowledge bases (``docs/v2/_asks.md`` #25).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import jwt
import pytest
from auth_helpers import (
    WEB_ORIGIN,
    add_membership,
    key_client,
    login,
    make_api_key,
    make_user,
    make_workspace,
    set_agent_columns,
)
from conftest import create_agent
from fastapi import FastAPI

from lkap_api.db.constants import DEFAULT_WORKSPACE_ID
from lkap_api.db.session import Database
from lkap_api.settings import Settings, get_settings


@dataclass
class World:
    """The two workspaces and their signed-in admins."""

    beta_id: str
    alice: httpx.AsyncClient
    bob: httpx.AsyncClient
    agent_a: dict[str, object]


@pytest.fixture
async def world(app: FastAPI, database: Database, admin_client: httpx.AsyncClient) -> AsyncIterator[World]:
    """Workspaces A (default) and B (beta), an agent in A, and a client per admin."""
    beta_id = await make_workspace(database, "beta")
    await make_user(database, "alice@a.example", role="admin", workspace_id=DEFAULT_WORKSPACE_ID)
    await make_user(database, "bob@b.example", role="admin", workspace_id=beta_id)
    agent_a = await create_agent(admin_client, name="Alpha agent", published=False)
    async with await login(app, "alice@a.example") as alice, await login(app, "bob@b.example") as bob:
        yield World(beta_id=beta_id, alice=alice, bob=bob, agent_a=agent_a)


# ------------------------------------------------------------ workspace routes
async def test_bob_sees_only_his_workspace(world: World) -> None:
    body = (await world.bob.get("/v1/workspaces")).json()

    assert [w["slug"] for w in body["items"]] == ["beta"]


@pytest.mark.parametrize(
    ("method", "path", "json"),
    [
        ("GET", "/v1/workspaces/default/members", None),
        ("PUT", "/v1/workspaces/default", {"name": "Pwned"}),
        ("POST", "/v1/workspaces/default/invites", {"email": "mole@b.example", "role": "admin"}),
        ("POST", "/v1/workspaces/default/members", {"email": "bob@b.example", "role": "owner"}),
        ("GET", f"/v1/workspaces/{DEFAULT_WORKSPACE_ID}/members", None),
    ],
)
async def test_other_workspace_routes_are_404_not_403(
    world: World, method: str, path: str, json: dict[str, str] | None
) -> None:
    response = await world.bob.request(method, path, json=json)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_selecting_another_workspace_by_header_is_404(world: World) -> None:
    for selector in ("default", DEFAULT_WORKSPACE_ID, "no-such-workspace"):
        response = await world.bob.get("/v1/api-keys", headers={"X-Workspace": selector})
        assert response.status_code == 404, selector


async def test_api_keys_are_listed_and_revoked_per_workspace(world: World, tenant_guard: None) -> None:
    key_a = (await world.alice.post("/v1/api-keys", json={"name": "A", "scopes": ["*"]})).json()
    key_b = (await world.bob.post("/v1/api-keys", json={"name": "B", "scopes": ["*"]})).json()

    bob_keys = (await world.bob.get("/v1/api-keys")).json()
    cross_revoke = await world.bob.delete(f"/v1/api-keys/{key_a['id']}")
    alice_keys = (await world.alice.get("/v1/api-keys")).json()

    assert [k["id"] for k in bob_keys["items"]] == [key_b["id"]]
    assert cross_revoke.status_code == 404
    assert [k["id"] for k in alice_keys["items"]] == [key_a["id"]]
    assert alice_keys["items"][0]["revoked_at"] is None


async def test_audit_log_is_per_workspace(world: World, tenant_guard: None) -> None:
    await world.alice.post("/v1/api-keys", json={"name": "A", "scopes": ["*"]})
    await world.bob.post("/v1/api-keys", json={"name": "B", "scopes": ["*"]})

    bob_rows = (await world.bob.get("/v1/audit")).json()["items"]

    assert bob_rows
    assert {row["workspace_id"] for row in bob_rows} == {world.beta_id}


async def test_api_key_is_bound_to_its_workspace(app: FastAPI, database: Database, world: World) -> None:
    _, raw = await make_api_key(database, ["*"], workspace_id=world.beta_id)

    async with key_client(app, raw) as own:
        listed = await own.get("/v1/api-keys")
    async with key_client(app, raw, **{"X-Workspace": "default"}) as other:
        crossed = await other.get("/v1/api-keys")

    assert listed.status_code == 200
    assert crossed.status_code == 404


async def test_agent_limits_of_another_workspace_are_404(world: World, tenant_guard: None) -> None:
    agent_id = world.agent_a["id"]

    read = await world.bob.get(f"/v1/agents/{agent_id}/limits")
    write = await world.bob.put(
        f"/v1/agents/{agent_id}/limits",
        json={
            "max_concurrent_sessions": 99,
            "max_session_duration_s": 99,
            "rate_per_ip_per_min": 99,
            "rate_per_agent_per_min": 99,
        },
    )
    own = await world.alice.get(f"/v1/agents/{agent_id}/limits")

    assert (read.status_code, write.status_code, own.status_code) == (404, 404, 200)
    assert own.json()["max_concurrent_sessions"] == 5


async def test_admin_of_another_workspace_is_not_privileged_on_connect(world: World) -> None:
    response = await world.bob.post(f"/v1/agents/{world.agent_a['id']}/connect", json={})

    assert response.status_code == 403  # unpublished: public rules apply to Bob


async def test_user_in_two_workspaces_must_pick_one(app: FastAPI, database: Database, world: World) -> None:
    carol = await make_user(database, "carol@example.com", role="viewer")
    await add_membership(database, carol, world.beta_id, "builder")

    async with await login(app, "carol@example.com") as client:
        ambiguous = await client.get("/v1/agents")
        picked = await client.get("/v1/agents", headers={"X-Workspace": "beta"})
        me = (await client.get("/v1/auth/me")).json()

    assert ambiguous.status_code == 400
    assert set(ambiguous.json()["error"]["details"]["workspaces"]) == {"default", "beta"}
    assert picked.status_code == 200
    assert {(w["slug"], w["role"]) for w in me["workspaces"]} == {("default", "viewer"), ("beta", "builder")}


async def test_role_is_per_workspace(app: FastAPI, database: Database, world: World) -> None:
    dave = await make_user(database, "dave@example.com", role="viewer")
    await add_membership(database, dave, world.beta_id, "admin")

    async with await login(app, "dave@example.com") as client:
        in_beta = await client.get("/v1/api-keys", headers={"X-Workspace": "beta"})
        in_default = await client.get("/v1/api-keys", headers={"X-Workspace": "default"})

    assert in_beta.status_code == 200
    assert in_default.status_code == 403


# --------------------------------------------------------------- agents router
async def test_agents_of_another_workspace_are_not_listed(world: World) -> None:
    body = (await world.bob.get("/v1/agents")).json()

    assert world.agent_a["id"] not in {agent["id"] for agent in body["items"]}


@pytest.mark.parametrize(
    ("method", "suffix", "json"),
    [
        ("GET", "", None),
        ("PUT", "", {"name": "Pwned"}),
        ("DELETE", "", None),
        ("POST", "/validate", None),
    ],
)
async def test_agent_of_another_workspace_is_404(
    world: World, method: str, suffix: str, json: dict[str, str] | None
) -> None:
    response = await world.bob.request(method, f"/v1/agents/{world.agent_a['id']}{suffix}", json=json)

    assert response.status_code == 404


async def test_agents_router_queries_are_scoped(world: World, tenant_guard: None) -> None:
    agent_id = world.agent_a["id"]

    listed = await world.alice.get("/v1/agents")
    fetched = await world.alice.get(f"/v1/agents/{agent_id}")
    renamed = await world.alice.put(f"/v1/agents/{agent_id}", json={"name": "Renamed"})
    validated = await world.alice.post(f"/v1/agents/{agent_id}/validate")

    assert [r.status_code for r in (listed, fetched, renamed, validated)] == [200, 200, 200, 200]
    assert fetched.json()["workspace_id"] == DEFAULT_WORKSPACE_ID


async def test_agent_created_in_a_workspace_belongs_to_it(world: World) -> None:
    from conftest import inference_config

    created = await world.bob.post(
        "/v1/agents", json={"name": "Beta agent", "config": inference_config().model_dump(mode="json")}
    )

    assert created.status_code == 201, created.text
    assert created.json()["workspace_id"] == world.beta_id
    assert created.json()["id"] not in {
        a["id"] for a in (await world.alice.get("/v1/agents")).json()["items"]
    }


# ---------------------------------------------------------- connections (V2-03)
async def _beta_connection(database: Database, beta_id: str) -> str:
    from lkap_api.db.models import LiveKitConnection, new_id
    from lkap_api.vault import Vault

    vault = Vault(get_settings().master_key)
    connection_id = new_id()
    async with database.session() as session:
        session.add(
            LiveKitConnection(
                id=connection_id,
                workspace_id=beta_id,
                slug="beta-cloud",
                name="Beta cloud",
                deployment_type="cloud",
                url="wss://beta-project.livekit.cloud",
                api_key_ct=vault.encrypt({"api_key": "APIbetaKEY1234"}),
                api_secret_ct=vault.encrypt({"api_secret": "beta-secret-" + "s" * 32}),
                deployment_mode="external",
                is_default=True,
            )
        )
    return connection_id


async def test_connections_of_another_workspace_are_invisible(database: Database, world: World) -> None:
    beta_connection = await _beta_connection(database, world.beta_id)

    alice_list = (await world.alice.get("/v1/connections")).json()
    bob_list = (await world.bob.get("/v1/connections")).json()
    read = await world.alice.get(f"/v1/connections/{beta_connection}")
    tested = await world.alice.post(f"/v1/connections/{beta_connection}/test")
    rotated = await world.alice.post(
        f"/v1/connections/{beta_connection}/rotate", json={"api_key": "k" * 12, "api_secret": "s" * 40}
    )

    assert beta_connection not in {c["id"] for c in alice_list["items"]}
    assert [c["id"] for c in bob_list["items"]] == [beta_connection]
    assert "beta-secret" not in str(bob_list)
    assert (read.status_code, tested.status_code, rotated.status_code) == (404, 404, 404)


async def test_agent_cannot_be_bound_to_another_workspaces_connection(
    database: Database, world: World
) -> None:
    beta_connection = await _beta_connection(database, world.beta_id)

    response = await world.alice.put(
        f"/v1/agents/{world.agent_a['id']}", json={"connection_id": beta_connection}
    )

    assert response.status_code == 422
    assert response.json()["error"]["details"]["connection_id"] == beta_connection


async def test_connect_never_mints_with_another_workspaces_connection(
    database: Database, world: World, settings: Settings
) -> None:
    beta_connection = await _beta_connection(database, world.beta_id)
    # Force a cross-workspace binding behind the api's back: minting must ignore it.
    await set_agent_columns(database, str(world.agent_a["id"]), connection_id=beta_connection, published=True)

    body = (
        await world.alice.post(
            f"/v1/agents/{world.agent_a['id']}/connect", json={}, headers={"Origin": WEB_ORIGIN}
        )
    ).json()

    assert body["serverUrl"] == settings.livekit_url
    claims = jwt.decode(
        body["participantToken"],
        settings.livekit_api_secret,
        algorithms=["HS256"],
        issuer=settings.livekit_api_key,
    )
    assert claims["iss"] == settings.livekit_api_key


# ------------------------------------- sessions, credentials, tools, KBs (asks #25)
_TOOL_DEFINITION = {
    "kind": "http",
    "name": "get_weather",
    "description": "Current weather for a city",
    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
    "method": "GET",
    "url": "https://api.example.com/weather/{{ city }}",
    "allowed_hosts": ["api.example.com"],
}


@dataclass
class AlphaRows:
    """One row of each resource, created by Alice in workspace A."""

    session_id: str
    credential_id: str
    tool_id: str
    kb_id: str


@pytest.fixture
async def alpha_rows(world: World) -> AlphaRows:
    """A session, a credential, a tool and a knowledge base in workspace A."""
    credential = await world.alice.post(
        "/v1/credentials",
        json={"provider_id": "openai-llm", "label": "A key", "secrets": {"api_key": "sk-alpha-123"}},
    )
    tool = await world.alice.post(
        "/v1/tools", json={"kind": "http", "name": "get_weather", "definition": _TOOL_DEFINITION}
    )
    kb = await world.alice.post("/v1/knowledge-bases", json={"name": "Alpha policies"})
    session = await world.alice.post(f"/v1/agents/{world.agent_a['id']}/connect", json={})
    for response in (credential, tool, kb):
        assert response.status_code == 201, response.text
    assert session.status_code == 200, session.text
    return AlphaRows(
        session_id=session.json()["sessionId"],
        credential_id=credential.json()["id"],
        tool_id=tool.json()["id"],
        kb_id=kb.json()["id"],
    )


@pytest.mark.parametrize(
    ("collection", "attr"),
    [
        ("/v1/sessions", "session_id"),
        ("/v1/credentials", "credential_id"),
        ("/v1/tools", "tool_id"),
        ("/v1/knowledge-bases", "kb_id"),
    ],
)
async def test_other_workspace_rows_are_not_listed(
    world: World, alpha_rows: AlphaRows, collection: str, attr: str
) -> None:
    row_id = getattr(alpha_rows, attr)

    bob_ids = {item["id"] for item in (await world.bob.get(collection)).json()["items"]}
    alice_ids = {item["id"] for item in (await world.alice.get(collection)).json()["items"]}

    assert row_id not in bob_ids
    assert row_id in alice_ids


@pytest.mark.parametrize(
    ("method", "path", "json"),
    [
        ("GET", "/v1/sessions/{session_id}", None),
        ("GET", "/v1/sessions/{session_id}/events", None),
        ("DELETE", "/v1/sessions/{session_id}", None),
        ("GET", "/v1/credentials/{credential_id}", None),
        ("PUT", "/v1/credentials/{credential_id}", {"label": "Pwned"}),
        ("POST", "/v1/credentials/{credential_id}/test", None),
        ("DELETE", "/v1/credentials/{credential_id}", None),
        ("GET", "/v1/tools/{tool_id}", None),
        ("PUT", "/v1/tools/{tool_id}", {"kind": "http", "name": "x", "definition": _TOOL_DEFINITION}),
        ("POST", "/v1/tools/{tool_id}/dry-run", {"arguments": {"city": "x"}}),
        ("DELETE", "/v1/tools/{tool_id}", None),
        ("GET", "/v1/knowledge-bases/{kb_id}", None),
        ("PUT", "/v1/knowledge-bases/{kb_id}", {"name": "Pwned"}),
        ("GET", "/v1/knowledge-bases/{kb_id}/documents", None),
        ("POST", "/v1/knowledge-bases/{kb_id}/search", {"query": "x", "k": 3}),
        ("DELETE", "/v1/knowledge-bases/{kb_id}", None),
    ],
)
async def test_other_workspace_rows_are_404(
    world: World, alpha_rows: AlphaRows, method: str, path: str, json: dict[str, object] | None
) -> None:
    url = path.format(**vars(alpha_rows))

    response = await world.bob.request(method, url, json=json)

    assert response.status_code == 404, response.text
    assert (await world.alice.get(url.split("/events")[0])).status_code == 200 if method == "GET" else True


async def test_new_rows_land_in_the_callers_workspace(world: World, alpha_rows: AlphaRows) -> None:
    created = await world.bob.post(
        "/v1/credentials",
        json={"provider_id": "openai-llm", "label": "B key", "secrets": {"api_key": "sk-beta-123"}},
    )
    tool = await world.bob.post(
        "/v1/tools",
        json={
            "kind": "http",
            "name": "t",
            "definition": {**_TOOL_DEFINITION, "credential_id": alpha_rows.credential_id},
        },
    )

    assert created.status_code == 201
    assert created.json()["id"] not in {
        c["id"] for c in (await world.alice.get("/v1/credentials")).json()["items"]
    }
    # A tool in B cannot borrow A's credential.
    assert tool.status_code == 422


async def test_scoped_routers_issue_only_scoped_queries(
    world: World, alpha_rows: AlphaRows, tenant_guard: None
) -> None:
    for path in (
        "/v1/sessions",
        f"/v1/sessions/{alpha_rows.session_id}",
        "/v1/credentials",
        f"/v1/credentials/{alpha_rows.credential_id}",
        "/v1/tools",
        f"/v1/tools/{alpha_rows.tool_id}",
        "/v1/knowledge-bases",
        f"/v1/knowledge-bases/{alpha_rows.kb_id}",
    ):
        assert (await world.alice.get(path)).status_code == 200, path
