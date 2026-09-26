"""V5-16: MCP OAuth, part 2 — refresh under a lock, the worker's token route, revoke (T §4.3.6).

Every outbound call goes to :class:`fakes.oauth_as.OAuthWorld` (an in-process MCP server and
authorization server behind ``httpx.MockTransport``); nothing touches the network. The
sign-in itself is made through the V5-14 routes (start, the fake consent, the callback).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
from collections.abc import AsyncIterator, Iterator

import fakeredis
import httpx
import pytest
from auth_helpers import key_client, make_api_key
from conftest import captured_text, create_agent, inference_config
from fakes.oauth_as import MCP_URL, OAuthWorld
from fastapi import FastAPI
from lkap_contracts.agent_config import ResolvedAgentConfig
from lkap_contracts.tools import McpServerDefinition
from sqlalchemy import select
from test_mcp_oauth import _audits, _bag, _connect, _definition, _rows, _tool
from test_webhooks import _make_endpoint

from lkap_api.db.constants import DEFAULT_WORKSPACE_ID
from lkap_api.db.models import Credential, McpOauthClient, Tool, WebhookDelivery, utcnow
from lkap_api.db.session import Database
from lkap_api.mcp_oauth import tokens
from lkap_api.mcp_oauth.tokens import NeedsReauth, get_access_token, token_sha256
from lkap_api.settings import Settings
from lkap_api.vault import Vault

TOKEN_ROUTE = "/internal/v1/tools/{tool_id}/oauth/token"


@pytest.fixture
def world(app: FastAPI) -> Iterator[OAuthWorld]:
    """The fake MCP server + authorization server as the api's outbound client."""
    from lkap_api.deps import get_http_client

    fake = OAuthWorld()

    async def override() -> AsyncIterator[httpx.AsyncClient]:
        async with httpx.AsyncClient(transport=fake.transport()) as http:
            yield http

    app.dependency_overrides[get_http_client] = override
    yield fake
    app.dependency_overrides.pop(get_http_client, None)


# -------------------------------------------------------------------------- helpers
async def _signed_in(
    admin_client: httpx.AsyncClient, client: httpx.AsyncClient, world: OAuthWorld, *, name: str = "tracker"
) -> tuple[str, str]:
    """A tool connected through the V5-14 routes: ``(tool_id, credential_id)``."""
    tool_id = await _tool(admin_client) if name == "tracker" else await _named_tool(admin_client, name)
    _, _, response = await _connect(admin_client, client, world, tool_id)
    assert response.status_code == 302 and response.headers["location"].endswith("oauth=ok")
    return tool_id, await _credential_id(admin_client, tool_id)


async def _named_tool(admin_client: httpx.AsyncClient, name: str) -> str:
    definition = {"kind": "mcp", "name": name, "url": MCP_URL, "auth": {"kind": "oauth"}}
    response = await admin_client.post(
        "/v1/tools", json={"kind": "mcp", "name": name, "definition": definition}
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def _credential_id(admin_client: httpx.AsyncClient, tool_id: str) -> str:
    response = await admin_client.get(f"/v1/tools/{tool_id}")
    credential_id = response.json()["definition"]["auth"]["credential_id"]
    assert isinstance(credential_id, str)
    return credential_id


async def _set_bag(database: Database, settings: Settings, credential_id: str, **values: str | None) -> None:
    vault = Vault(settings.master_key)
    async with database.session() as session:
        row = (await session.execute(select(Credential).where(Credential.id == credential_id))).scalar_one()
        bag = {**vault.decrypt(row.ciphertext), **values}
        row.ciphertext = vault.encrypt({k: v for k, v in bag.items() if v is not None})


async def _expire_soon(database: Database, settings: Settings, credential_id: str, seconds: int = 20) -> None:
    await _set_bag(
        database, settings, credential_id, expires_at=(utcnow() + dt.timedelta(seconds=seconds)).isoformat()
    )


def _outbound(world: OAuthWorld, *, token_delay_s: float = 0.0) -> httpx.AsyncClient:
    """The fake world as an outbound client; ``token_delay_s`` slows the token endpoint so
    concurrent callers are all past the lock-free fast path before the first refresh lands."""

    async def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token" and token_delay_s:
            await asyncio.sleep(token_delay_s)
        return world.handle(request)

    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


class _NoProcessLocks(dict[str, asyncio.Lock]):
    """Every caller gets its own process lock: two api replicas, as far as the refresh knows."""

    def setdefault(self, key: str, default: asyncio.Lock | None = None, /) -> asyncio.Lock:
        return asyncio.Lock()


async def _session_for(admin_client: httpx.AsyncClient, tool_id: str) -> str:
    config = json.loads(inference_config().model_dump_json())
    config["tools"]["tool_ids"] = [tool_id]
    agent = await create_agent(admin_client, name=f"Agent {tool_id[:6]}", config=config)
    response = await admin_client.post(f"/v1/agents/{agent['id']}/connect", json={})
    assert response.status_code in (200, 201), response.text
    return str(response.json()["sessionId"])


def _refreshes(world: OAuthWorld) -> int:
    return len(world.refresh_calls)


# -------------------------------------------------------------------------- refresh
async def test_a_fresh_token_is_handed_out_without_a_refresh(
    admin_client: httpx.AsyncClient,
    client: httpx.AsyncClient,
    world: OAuthWorld,
    database: Database,
    settings: Settings,
) -> None:
    _, credential_id = await _signed_in(admin_client, client, world)

    async with _outbound(world) as http:
        token = await get_access_token(
            database,
            Vault(settings.master_key),
            http,
            settings,
            workspace_id=DEFAULT_WORKSPACE_ID,
            credential_id=credential_id,
        )

    assert token.access_token == world.access_token and _refreshes(world) == 0


async def test_two_concurrent_refreshes_on_one_credential_make_one_token_call(
    admin_client: httpx.AsyncClient,
    client: httpx.AsyncClient,
    world: OAuthWorld,
    database: Database,
    settings: Settings,
) -> None:
    _, credential_id = await _signed_in(admin_client, client, world)
    await _expire_soon(database, settings, credential_id)
    vault = Vault(settings.master_key)

    async with _outbound(world, token_delay_s=0.2) as http:
        first, second = await asyncio.gather(
            *(
                get_access_token(
                    database,
                    vault,
                    http,
                    settings,
                    workspace_id=DEFAULT_WORKSPACE_ID,
                    credential_id=credential_id,
                )
                for _ in range(2)
            )
        )

    assert _refreshes(world) == 1
    assert first == second and first.access_token == world.access_token


async def test_a_rotated_refresh_token_is_stored_and_the_old_one_is_gone(
    admin_client: httpx.AsyncClient,
    client: httpx.AsyncClient,
    world: OAuthWorld,
    database: Database,
    settings: Settings,
) -> None:
    _, credential_id = await _signed_in(admin_client, client, world)
    old_refresh = world.refresh_token
    await _expire_soon(database, settings, credential_id)

    async with _outbound(world) as http:
        token = await get_access_token(
            database,
            Vault(settings.master_key),
            http,
            settings,
            workspace_id=DEFAULT_WORKSPACE_ID,
            credential_id=credential_id,
        )

    bag = await _bag(database, settings, credential_id)
    assert world.refresh_calls[0]["refresh_token"] == [old_refresh]
    assert world.refresh_calls[0]["resource"] == [MCP_URL]
    assert bag["refresh_token"] == world.refresh_token != old_refresh
    assert bag["access_token"] == token.access_token and bag["status"] == "active"
    assert token.expires_at is not None and token.expires_at - utcnow() > dt.timedelta(minutes=50)


async def test_a_refresh_under_a_redis_lock_releases_the_lock(
    admin_client: httpx.AsyncClient,
    client: httpx.AsyncClient,
    world: OAuthWorld,
    database: Database,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``LKAP_REDIS_URL`` set: ``SET NX PX`` serialises two replicas' refreshes, then is released."""
    server = fakeredis.FakeServer()
    seen: list[fakeredis.aioredis.FakeRedis] = []

    def factory(_url: str) -> fakeredis.aioredis.FakeRedis:
        redis = fakeredis.aioredis.FakeRedis(server=server)
        seen.append(redis)
        return redis

    monkeypatch.setattr(tokens, "redis_factory", factory)
    monkeypatch.setattr(tokens, "_PROCESS_LOCKS", _NoProcessLocks())
    _, credential_id = await _signed_in(admin_client, client, world)
    await _expire_soon(database, settings, credential_id)
    with_redis = settings.model_copy(update={"redis_url": "redis://lock.test:6379/0"})

    async with _outbound(world, token_delay_s=0.2) as http:
        await asyncio.gather(
            *(
                get_access_token(
                    database,
                    Vault(settings.master_key),
                    http,
                    with_redis,
                    workspace_id=DEFAULT_WORKSPACE_ID,
                    credential_id=credential_id,
                )
                for _ in range(2)
            )
        )

    assert _refreshes(world) == 1 and seen
    probe = fakeredis.aioredis.FakeRedis(server=server)
    assert await probe.get(f"lkap:mcp_oauth:refresh:{credential_id}") is None


async def test_invalid_grant_flips_the_status_audits_and_emits_the_event(
    admin_client: httpx.AsyncClient,
    client: httpx.AsyncClient,
    service_client: httpx.AsyncClient,
    world: OAuthWorld,
    database: Database,
    settings: Settings,
) -> None:
    tool_id, credential_id = await _signed_in(admin_client, client, world)
    session_id = await _session_for(admin_client, tool_id)
    # A private destination: the delivery attempt is refused by the guard, no network.
    await _make_endpoint(database, Vault(settings.master_key), "https://10.0.0.1/hook")
    await _expire_soon(database, settings, credential_id)
    world.refresh_error = "invalid_grant"

    response = await service_client.post(TOKEN_ROUTE.format(tool_id=tool_id), json={"session_id": session_id})

    assert response.status_code == 409 and response.json()["error"]["details"]["reason"] == "needs_reauth"
    assert (await _bag(database, settings, credential_id))["status"] == "needs_reauth"
    (row,) = [a for a in await _audits(database) if a.action == "mcp_oauth.needs_reauth"]
    assert row.payload["reason"] == "invalid_grant" and row.target_id == tool_id
    (delivery,) = [d for d in await _rows(database, WebhookDelivery) if d.event_type == "tool.needs_reauth"]
    assert delivery.payload["data"] == {"tool_id": tool_id, "reason": "invalid_grant"}
    status = (await admin_client.get(f"/v1/tools/{tool_id}/oauth/status")).json()
    assert status["status"] == "needs_reauth"
    again = await service_client.post(TOKEN_ROUTE.format(tool_id=tool_id), json={"session_id": session_id})
    assert again.status_code == 409 and _refreshes(world) == 1, "no second refresh once flipped"


async def test_a_transient_refresh_failure_keeps_a_token_that_still_works(
    admin_client: httpx.AsyncClient,
    client: httpx.AsyncClient,
    world: OAuthWorld,
    database: Database,
    settings: Settings,
) -> None:
    _, credential_id = await _signed_in(admin_client, client, world)
    await _expire_soon(database, settings, credential_id, seconds=30)
    world.refresh_error, world.refresh_status = "server_error", 503

    async with _outbound(world) as http:
        token = await get_access_token(
            database,
            Vault(settings.master_key),
            http,
            settings,
            workspace_id=DEFAULT_WORKSPACE_ID,
            credential_id=credential_id,
        )

    assert token.access_token == world.access_token
    assert (await _bag(database, settings, credential_id))["status"] == "active"


async def test_an_expired_token_without_a_refresh_token_needs_reauth(
    admin_client: httpx.AsyncClient,
    client: httpx.AsyncClient,
    world: OAuthWorld,
    database: Database,
    settings: Settings,
) -> None:
    world.issue_refresh_token = False
    _, credential_id = await _signed_in(admin_client, client, world)
    await _expire_soon(database, settings, credential_id, seconds=-5)

    async with _outbound(world) as http:
        with pytest.raises(NeedsReauth) as caught:
            await get_access_token(
                database,
                Vault(settings.master_key),
                http,
                settings,
                workspace_id=DEFAULT_WORKSPACE_ID,
                credential_id=credential_id,
            )

    assert caught.value.reason == "no_refresh_token" and world.refresh_calls == []


# ------------------------------------------------------------------ the token route
async def test_the_token_route_returns_a_fresh_token_and_never_the_refresh_token(
    app: FastAPI,
    admin_client: httpx.AsyncClient,
    client: httpx.AsyncClient,
    service_client: httpx.AsyncClient,
    world: OAuthWorld,
    database: Database,
    settings: Settings,
) -> None:
    tool_id, credential_id = await _signed_in(admin_client, client, world)
    session_id = await _session_for(admin_client, tool_id)
    await _expire_soon(database, settings, credential_id)

    response = await service_client.post(TOKEN_ROUTE.format(tool_id=tool_id), json={"session_id": session_id})

    assert response.status_code == 200, response.text
    assert set(response.json()) == {"access_token", "expires_at"}
    assert response.json()["access_token"] == world.access_token and _refreshes(world) == 1
    assert world.refresh_token not in response.text and "rt-fake" not in response.text
    unauthenticated = await client.post(TOKEN_ROUTE.format(tool_id=tool_id), json={"session_id": session_id})
    assert unauthenticated.status_code == 401


async def test_a_rejected_token_is_refreshed_once_and_a_stale_hint_gets_the_new_one(
    admin_client: httpx.AsyncClient,
    client: httpx.AsyncClient,
    service_client: httpx.AsyncClient,
    world: OAuthWorld,
) -> None:
    """Rotation-safe: two workers that both saw a 401 on the same token cause one refresh."""
    tool_id, _ = await _signed_in(admin_client, client, world)
    session_id = await _session_for(admin_client, tool_id)
    refused = token_sha256(world.access_token)
    body = {"session_id": session_id, "rejected_token_sha256": refused}

    first = await service_client.post(TOKEN_ROUTE.format(tool_id=tool_id), json=body)
    second = await service_client.post(TOKEN_ROUTE.format(tool_id=tool_id), json=body)

    assert first.status_code == second.status_code == 200
    assert first.json()["access_token"] == second.json()["access_token"] == world.access_token
    assert _refreshes(world) == 1


@pytest.mark.parametrize("case", ["unknown_session", "tool_not_on_agent", "ended_session"])
async def test_the_token_route_is_bound_to_a_live_session_whose_agent_uses_the_tool(
    admin_client: httpx.AsyncClient,
    client: httpx.AsyncClient,
    service_client: httpx.AsyncClient,
    world: OAuthWorld,
    database: Database,
    case: str,
) -> None:
    tool_id, _ = await _signed_in(admin_client, client, world)
    session_id = await _session_for(admin_client, tool_id)
    expected = 404
    if case == "unknown_session":
        session_id = "0" * 32
    elif case == "tool_not_on_agent":
        other, _ = await _signed_in(admin_client, client, world, name="other")
        tool_id = other
    else:
        from lkap_api.db.models import Session as SessionRow

        async with database.session() as session:
            row = (await session.execute(select(SessionRow).where(SessionRow.id == session_id))).scalar_one()
            row.status = "ended"
        expected = 409
    world.requests.clear()

    response = await service_client.post(TOKEN_ROUTE.format(tool_id=tool_id), json={"session_id": session_id})

    assert response.status_code == expected
    assert "access_token" not in response.text and world.requests == []


async def test_the_resolved_config_carries_minutes_lived_access_and_no_refresh_token(
    admin_client: httpx.AsyncClient,
    client: httpx.AsyncClient,
    service_client: httpx.AsyncClient,
    world: OAuthWorld,
    database: Database,
    settings: Settings,
) -> None:
    tool_id, credential_id = await _signed_in(admin_client, client, world)
    session_id = await _session_for(admin_client, tool_id)
    await _expire_soon(database, settings, credential_id)  # a pre-session refresh

    response = await service_client.get(f"/internal/v1/sessions/{session_id}/resolved")

    assert response.status_code == 200, response.text
    resolved = ResolvedAgentConfig.model_validate(response.json())
    (access,) = resolved.mcp_oauth
    (definition,) = resolved.tools
    assert isinstance(definition, McpServerDefinition)
    assert (access.tool_id, access.name, access.url) == (tool_id, definition.name, definition.url)
    assert access.access_token == world.access_token and _refreshes(world) == 1
    assert access.expires_at is not None
    assert dt.timedelta(minutes=5) < access.expires_at - utcnow() < dt.timedelta(hours=2)
    assert definition.credential_id is None and definition.auth.credential_id is None  # type: ignore[union-attr]
    bag = await _bag(database, settings, credential_id)
    for secret in (
        bag["refresh_token"],
        "rt-fake",
        "rat-fake",
        "registration_access_token",
        "token_endpoint",
    ):
        assert secret not in response.text


async def test_a_sign_in_that_needs_reauth_is_left_out_of_the_session(
    admin_client: httpx.AsyncClient,
    client: httpx.AsyncClient,
    service_client: httpx.AsyncClient,
    world: OAuthWorld,
    database: Database,
    settings: Settings,
) -> None:
    tool_id, credential_id = await _signed_in(admin_client, client, world)
    session_id = await _session_for(admin_client, tool_id)
    await _set_bag(database, settings, credential_id, status="needs_reauth")

    response = await service_client.get(f"/internal/v1/sessions/{session_id}/resolved")

    assert response.status_code == 200, response.text
    resolved = ResolvedAgentConfig.model_validate(response.json())
    assert resolved.mcp_oauth == [] and len(resolved.tools) == 1


# -------------------------------------------------------------------------- revoke
async def test_revoke_calls_the_endpoints_in_order_and_deletes_the_sign_in(
    admin_client: httpx.AsyncClient,
    client: httpx.AsyncClient,
    world: OAuthWorld,
    database: Database,
    settings: Settings,
) -> None:
    tool_id, credential_id = await _signed_in(admin_client, client, world)
    access, refresh = world.access_token, world.refresh_token

    response = await admin_client.post(f"/v1/tools/{tool_id}/oauth/revoke")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "not_connected"
    assert world.revocations == [("refresh_token", refresh), ("access_token", access)]
    assert world.client_deletions == ["dcr-client-1"]
    assert [r for r in await _rows(database, Credential) if r.id == credential_id] == []
    assert await _rows(database, McpOauthClient) == []
    assert (await _definition(database, tool_id)).auth.credential_id is None  # type: ignore[union-attr]
    (row,) = [a for a in await _audits(database) if a.action == "mcp_oauth.revoked"]
    assert row.payload["revocation"] == "ok" and row.payload["client_deleted"] is True
    assert access not in json.dumps(row.payload) and refresh not in json.dumps(row.payload)
    status = (await admin_client.get(f"/v1/tools/{tool_id}/oauth/status")).json()
    assert status["status"] == "not_connected"


async def test_revoke_deletes_the_sign_in_even_when_the_provider_is_down(
    app: FastAPI,
    admin_client: httpx.AsyncClient,
    client: httpx.AsyncClient,
    world: OAuthWorld,
    database: Database,
) -> None:
    from lkap_api.deps import get_http_client

    tool_id, credential_id = await _signed_in(admin_client, client, world)

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("provider unreachable", request=request)

    async def override() -> AsyncIterator[httpx.AsyncClient]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(down)) as http:
            yield http

    app.dependency_overrides[get_http_client] = override

    response = await admin_client.post(f"/v1/tools/{tool_id}/oauth/revoke")

    assert response.status_code == 200, response.text
    assert [r for r in await _rows(database, Credential) if r.id == credential_id] == []
    (row,) = [a for a in await _audits(database) if a.action == "mcp_oauth.revoked"]
    assert row.payload["revocation"] == "failed" and row.payload["client_deleted"] is False


async def test_a_dynamic_client_shared_by_another_tool_is_kept(
    admin_client: httpx.AsyncClient, client: httpx.AsyncClient, world: OAuthWorld, database: Database
) -> None:
    first, _ = await _signed_in(admin_client, client, world)
    second, _ = await _signed_in(admin_client, client, world, name="second")
    assert len(world.registrations) == 1, "the second tool reuses the registered client"

    await admin_client.post(f"/v1/tools/{first}/oauth/revoke")

    assert world.client_deletions == [] and len(await _rows(database, McpOauthClient)) == 1

    await admin_client.post(f"/v1/tools/{second}/oauth/revoke")

    assert world.client_deletions == ["dcr-client-1"] and await _rows(database, McpOauthClient) == []


async def test_a_registration_client_uri_on_a_private_host_is_never_called(
    admin_client: httpx.AsyncClient, client: httpx.AsyncClient, world: OAuthWorld, database: Database
) -> None:
    """Asks #114 a: the DCR answer's uri passes the URL check before any RFC 7592 call."""
    tool_id, _ = await _signed_in(admin_client, client, world)
    async with database.session() as session:
        row = (await session.execute(select(McpOauthClient))).scalar_one()
        row.registration_client_uri = "https://169.254.169.254/register/dcr-client-1"
    world.requests.clear()

    await admin_client.post(f"/v1/tools/{tool_id}/oauth/revoke")

    assert world.client_deletions == []
    assert world.hosts_asked() == {"auth.example.com"}
    (audit_row,) = [a for a in await _audits(database) if a.action == "mcp_oauth.revoked"]
    assert audit_row.payload["client_deleted"] is False


async def test_deleting_the_tool_revokes_and_deletes_its_sign_in(
    admin_client: httpx.AsyncClient, client: httpx.AsyncClient, world: OAuthWorld, database: Database
) -> None:
    tool_id, credential_id = await _signed_in(admin_client, client, world)

    response = await admin_client.delete(f"/v1/tools/{tool_id}")

    assert response.status_code == 204
    assert [kind for kind, _ in world.revocations] == ["refresh_token", "access_token"]
    assert [r for r in await _rows(database, Credential) if r.id == credential_id] == []
    assert [r for r in await _rows(database, Tool) if r.id == tool_id] == []
    (row,) = [a for a in await _audits(database) if a.action == "mcp_oauth.revoked"]
    assert row.payload["trigger"] == "tool_delete"


async def test_revoke_needs_an_admin_with_providers_write(
    app: FastAPI,
    admin_client: httpx.AsyncClient,
    client: httpx.AsyncClient,
    world: OAuthWorld,
    database: Database,
) -> None:
    tool_id, credential_id = await _signed_in(admin_client, client, world)
    _, raw = await make_api_key(database, ["agents:read", "agents:write", "providers:read"])

    async with key_client(app, raw) as builder:
        response = await builder.post(f"/v1/tools/{tool_id}/oauth/revoke")

    assert response.status_code == 403
    assert world.revocations == [] and [r for r in await _rows(database, Credential) if r.id == credential_id]


# --------------------------------------------------------------------- log hygiene
async def test_no_token_reaches_a_log_line_across_refresh_route_and_revoke(
    log_capture: pytest.LogCaptureFixture,
    admin_client: httpx.AsyncClient,
    client: httpx.AsyncClient,
    service_client: httpx.AsyncClient,
    world: OAuthWorld,
    database: Database,
    settings: Settings,
) -> None:
    tool_id, credential_id = await _signed_in(admin_client, client, world)
    session_id = await _session_for(admin_client, tool_id)
    seen: set[str] = {world.access_token, world.refresh_token}
    await _expire_soon(database, settings, credential_id)
    await service_client.get(f"/internal/v1/sessions/{session_id}/resolved")
    seen |= {world.access_token, world.refresh_token}
    await service_client.post(
        TOKEN_ROUTE.format(tool_id=tool_id),
        json={"session_id": session_id, "rejected_token_sha256": token_sha256(world.access_token)},
    )
    seen |= {world.access_token, world.refresh_token}
    await admin_client.post(f"/v1/tools/{tool_id}/oauth/revoke")

    text = captured_text(log_capture, scope=None)
    # The flows ran (the info lines themselves depend on structlog's per-logger cache and the
    # level an earlier test configured, so the provider's record is the evidence).
    assert len(world.refresh_calls) == 2 and len(world.revocations) == 2
    for value in seen | {"rat-fake-Pq8Lm2"}:
        assert value not in text
