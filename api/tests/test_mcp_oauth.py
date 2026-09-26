"""V5-14: MCP OAuth, part 1 — discovery, registration, start, the callback, status (T §4.3.11).

Every outbound call goes to :class:`fakes.oauth_as.OAuthWorld` (an in-process MCP server
and authorization server behind ``httpx.MockTransport``, installed as the api's outbound
client); nothing touches the network.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import os
import shutil
import sqlite3
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from auth_helpers import key_client, make_api_key
from conftest import captured_text
from fakes.oauth_as import AS_ROOT, MCP_URL, OAuthWorld
from fastapi import FastAPI
from lkap_contracts.tools import McpServerDefinition
from sqlalchemy import select

from lkap_api.db.models import AuditLog, Credential, McpOauthClient, McpOauthFlow, Tool, utcnow
from lkap_api.db.session import Database
from lkap_api.mcp_oauth.discovery import resource_covers
from lkap_api.mcp_oauth.flows import authorization_url, hash_state
from lkap_api.mcp_oauth.http import UrlPolicy, url_problem
from lkap_api.mcp_oauth.logsafe import REDACTED, redact_oauth_fields
from lkap_api.mcp_oauth.router import CallbackQueryFilter
from lkap_api.sessions_sweep import sweep_mcp_oauth
from lkap_api.settings import Settings, get_settings
from lkap_api.vault import Vault

CALLBACK = "/v1/oauth/mcp/callback"
LOCAL_REDIRECT = "http://127.0.0.1:8080/v1/oauth/mcp/callback"
PUBLIC_BASE = "https://lkap.example.com"
CLIENT_SECRET = "pre-registered-secret-Wd4Rt8"


# ------------------------------------------------------------------------- fixtures
@pytest.fixture
def world(app: FastAPI) -> Iterator[OAuthWorld]:
    """The fake MCP server + authorization server as the api's outbound client."""
    from lkap_api.deps import get_http_client

    fake = OAuthWorld()

    async def override() -> AsyncIterator[httpx.AsyncClient]:
        async with httpx.AsyncClient(transport=fake.transport()) as client:
            yield client

    app.dependency_overrides[get_http_client] = override
    yield fake
    app.dependency_overrides.pop(get_http_client, None)


def _use_settings(app: FastAPI, settings: Settings, **update: Any) -> None:
    changed = settings.model_copy(update=update)
    app.dependency_overrides[get_settings] = lambda: changed


# -------------------------------------------------------------------------- helpers
async def _tool(client: httpx.AsyncClient, auth: dict[str, Any] | None = None, url: str = MCP_URL) -> str:
    definition = {"kind": "mcp", "name": "tracker", "url": url, "auth": {"kind": "oauth", **(auth or {})}}
    response = await client.post(
        "/v1/tools", json={"kind": "mcp", "name": "tracker", "definition": definition}
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def _start(
    client: httpx.AsyncClient, tool_id: str, body: dict[str, Any] | None = None
) -> httpx.Response:
    return await client.post(f"/v1/tools/{tool_id}/oauth/start", json=body or {})


async def _started(
    client: httpx.AsyncClient, tool_id: str, body: dict[str, Any] | None = None
) -> dict[str, Any]:
    response = await _start(client, tool_id, body)
    assert response.status_code == 200, response.text
    out: dict[str, Any] = response.json()
    assert out["status"] == "redirect"
    return out


def _query(url: str) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}


async def _callback(client: httpx.AsyncClient, params: dict[str, str]) -> httpx.Response:
    return await client.get(CALLBACK, params=params, follow_redirects=False)


async def _connect(
    admin: httpx.AsyncClient, client: httpx.AsyncClient, world: OAuthWorld, tool_id: str
) -> tuple[dict[str, Any], dict[str, str], httpx.Response]:
    started = await _started(admin, tool_id)
    params = world.authorize(started["authorization_url"])
    return started, params, await _callback(client, params)


async def _rows(database: Database, model: Any) -> list[Any]:
    async with database.session() as session:
        return list((await session.execute(select(model))).scalars().all())


async def _definition(database: Database, tool_id: str) -> McpServerDefinition:
    async with database.session() as session:
        row = (await session.execute(select(Tool).where(Tool.id == tool_id))).scalar_one()
        return McpServerDefinition.model_validate(row.definition)


async def _bag(database: Database, settings: Settings, credential_id: str) -> dict[str, str]:
    async with database.session() as session:
        row = (await session.execute(select(Credential).where(Credential.id == credential_id))).scalar_one()
        return Vault(settings.master_key).decrypt(row.ciphertext)


async def _audits(database: Database, prefix: str = "mcp_oauth.") -> list[AuditLog]:
    return [row for row in await _rows(database, AuditLog) if row.action.startswith(prefix)]


# ------------------------------------------------------------------------ discovery
@pytest.mark.parametrize("prm_via", ["header", "path", "root"])
async def test_start_finds_resource_metadata_by_header_and_both_well_known_fallbacks(
    admin_client: httpx.AsyncClient, world: OAuthWorld, prm_via: str
) -> None:
    world.prm_via = prm_via
    tool_id = await _tool(admin_client)

    started = await _started(admin_client, tool_id)

    asked = [r.url.path for r in world.requests if r.url.path.startswith("/.well-known/oauth-protected")]
    expected = {
        "header": ["/.well-known/oauth-protected-resource/by-header"],
        "path": ["/.well-known/oauth-protected-resource/mcp"],
        "root": ["/.well-known/oauth-protected-resource/mcp", "/.well-known/oauth-protected-resource"],
    }[prm_via]
    assert asked == expected
    assert started["issuer"] == AS_ROOT


@pytest.mark.parametrize(
    ("served", "expected_paths"),
    [
        ("oauth", ["/.well-known/oauth-authorization-server/tenant1"]),
        (
            "oidc",
            ["/.well-known/oauth-authorization-server/tenant1", "/.well-known/openid-configuration/tenant1"],
        ),
        (
            "oidc_append",
            [
                "/.well-known/oauth-authorization-server/tenant1",
                "/.well-known/openid-configuration/tenant1",
                "/tenant1/.well-known/openid-configuration",
            ],
        ),
    ],
)
async def test_authorization_server_metadata_is_found_path_inserted_then_appended(
    admin_client: httpx.AsyncClient, world: OAuthWorld, served: str, expected_paths: list[str]
) -> None:
    world.issuer = f"{AS_ROOT}/tenant1"
    world.as_metadata_at = served
    tool_id = await _tool(admin_client)

    started = await _started(admin_client, tool_id)

    asked = [r.url.path for r in world.requests if r.url.host == "auth.example.com" and r.method == "GET"]
    assert asked == expected_paths
    assert started["issuer"] == f"{AS_ROOT}/tenant1"


async def test_the_authorization_url_carries_pkce_s256_resource_state_and_the_challenged_scope(
    admin_client: httpx.AsyncClient, world: OAuthWorld, database: Database
) -> None:
    world.scopes_supported = ["issues:read", "offline_access"]
    tool_id = await _tool(admin_client)

    started = await _started(admin_client, tool_id)

    url = started["authorization_url"]
    assert url.startswith(f"{AS_ROOT}/authorize?")
    query = _query(url)
    assert query["tenant"] == "t1", "the endpoint's own query is kept"
    assert query["code_challenge_method"] == "S256" and len(query["code_challenge"]) == 43
    assert query["resource"] == MCP_URL
    assert query["redirect_uri"] == LOCAL_REDIRECT == started["redirect_uri"]
    assert query["scope"] == "issues:read offline_access"
    assert query["client_id"] == "dcr-client-1" and started["registration"] == "dcr"
    [flow] = await _rows(database, McpOauthFlow)
    assert flow.state_hash == hash_state(query["state"])
    assert query["state"].encode() not in flow.state_hash.encode()
    assert b"code_verifier" not in flow.verifier_ciphertext, "the verifier is a vault ciphertext"
    assert flow.expires_at - flow.created_at == dt.timedelta(minutes=10)
    assert flow.issuer == AS_ROOT and flow.resource == MCP_URL


async def test_an_admin_scope_override_wins(admin_client: httpx.AsyncClient, world: OAuthWorld) -> None:
    tool_id = await _tool(admin_client, {"scopes": ["issues:write"]})

    started = await _started(admin_client, tool_id)

    assert _query(started["authorization_url"])["scope"] == "issues:write"


@pytest.mark.parametrize("methods", [None, [], ["plain"]])
async def test_start_refuses_a_provider_without_pkce_s256(
    admin_client: httpx.AsyncClient, world: OAuthWorld, database: Database, methods: list[str] | None
) -> None:
    world.pkce_methods = methods
    tool_id = await _tool(admin_client)

    response = await _start(admin_client, tool_id)

    assert response.status_code == 422
    assert response.json()["error"]["details"]["reason"] == "pkce_unsupported"
    assert world.registrations == [] and await _rows(database, McpOauthFlow) == []


@pytest.mark.parametrize(
    "private",
    ["https://10.0.0.7/", "https://169.254.169.254/", "https://localhost.localhost/", "https://2130706433/"],
)
async def test_a_private_authorization_server_is_refused_before_any_fetch(
    admin_client: httpx.AsyncClient, world: OAuthWorld, private: str
) -> None:
    world.authorization_servers = [private]
    tool_id = await _tool(admin_client)

    response = await _start(admin_client, tool_id)

    assert response.status_code == 422
    assert response.json()["error"]["details"]["reason"] == "blocked_destination"
    host = urlsplit(private).hostname
    assert host not in world.hosts_asked()
    assert world.hosts_asked() == {"mcp.example.com"}


async def test_a_metadata_document_for_another_issuer_is_refused(
    admin_client: httpx.AsyncClient, world: OAuthWorld
) -> None:
    world.metadata_issuer = "https://honest.example.org"
    tool_id = await _tool(admin_client)

    response = await _start(admin_client, tool_id)

    assert response.json()["error"]["details"]["reason"] == "issuer_mismatch"


async def test_resource_metadata_for_another_server_is_refused(
    admin_client: httpx.AsyncClient, world: OAuthWorld
) -> None:
    world.prm_resource = "https://mcp.example.com/other"
    tool_id = await _tool(admin_client)

    response = await _start(admin_client, tool_id)

    assert response.json()["error"]["details"]["reason"] == "resource_mismatch"


async def test_redirects_are_never_followed(admin_client: httpx.AsyncClient, world: OAuthWorld) -> None:
    world.prm_via = "path"
    world.prm_redirect = True
    tool_id = await _tool(admin_client)

    response = await _start(admin_client, tool_id)

    assert response.json()["error"]["details"]["reason"] == "no_resource_metadata"
    assert "elsewhere.example.org" not in world.hosts_asked()


async def test_a_redirecting_mcp_server_is_refused(
    admin_client: httpx.AsyncClient, world: OAuthWorld
) -> None:
    world.mcp_status_without_token = 302
    tool_id = await _tool(admin_client)

    response = await _start(admin_client, tool_id)

    assert response.json()["error"]["details"]["reason"] == "redirect"


async def test_a_server_that_needs_no_sign_in_says_so(
    admin_client: httpx.AsyncClient, world: OAuthWorld
) -> None:
    world.mcp_status_without_token = 200
    tool_id = await _tool(admin_client)

    response = await _start(admin_client, tool_id)

    assert response.json()["error"]["details"]["reason"] == "oauth_not_required"


# --------------------------------------------------------------------- registration
async def test_cimd_is_chosen_only_with_a_public_https_base_url(
    app: FastAPI, admin_client: httpx.AsyncClient, world: OAuthWorld, settings: Settings
) -> None:
    world.cimd_supported = True
    tool_id = await _tool(admin_client)

    local = await _started(admin_client, tool_id)
    assert local["registration"] == "dcr", "no public origin: the provider could not fetch the document"

    _use_settings(app, settings, public_base_url=PUBLIC_BASE)
    public = await _started(admin_client, tool_id)
    assert public["registration"] == "cimd"
    assert (
        _query(public["authorization_url"])["client_id"] == f"{PUBLIC_BASE}/v1/oauth/mcp/client-metadata.json"
    )
    assert public["redirect_uri"] == f"{PUBLIC_BASE}/v1/oauth/mcp/callback"
    assert len(world.registrations) == 1


async def test_dcr_is_used_when_the_provider_has_no_metadata_document_support(
    app: FastAPI, admin_client: httpx.AsyncClient, world: OAuthWorld, settings: Settings, database: Database
) -> None:
    _use_settings(app, settings, public_base_url=PUBLIC_BASE)
    tool_id = await _tool(admin_client)

    started = await _started(admin_client, tool_id)

    assert started["registration"] == "dcr"
    [registration] = world.registrations
    assert registration["redirect_uris"] == [f"{PUBLIC_BASE}/v1/oauth/mcp/callback"]
    assert registration["application_type"] == "web"
    assert registration["token_endpoint_auth_method"] == "none"
    assert registration["grant_types"] == ["authorization_code", "refresh_token"]
    [client_row] = await _rows(database, McpOauthClient)
    assert client_row.registration == "dcr" and client_row.client_id == "dcr-client-1"
    assert b"rat-fake" not in (client_row.ciphertext or b""), "the registration token is encrypted"


async def test_a_loopback_redirect_registers_as_a_native_client_and_is_reused(
    admin_client: httpx.AsyncClient, world: OAuthWorld
) -> None:
    first = await _tool(admin_client)
    second = await _tool(admin_client)

    await _started(admin_client, first)
    again = await _started(admin_client, second)

    [registration] = world.registrations
    assert registration["application_type"] == "native"
    assert _query(again["authorization_url"])["client_id"] == "dcr-client-1"


async def test_a_preregistered_client_is_used_without_registration(
    admin_client: httpx.AsyncClient, client: httpx.AsyncClient, world: OAuthWorld, database: Database
) -> None:
    world.registration = False
    world.preregistered = {"vendor-app-42": CLIENT_SECRET}
    tool_id = await _tool(admin_client, {"registration": "preregistered", "client_id": "vendor-app-42"})

    started = await _started(admin_client, tool_id, {"client_secret": CLIENT_SECRET})
    params = world.authorize(started["authorization_url"])
    response = await _callback(client, params)

    assert started["registration"] == "preregistered"
    assert world.registrations == []
    assert CLIENT_SECRET not in started.values()
    assert response.status_code == 302 and response.headers["location"].endswith("oauth=ok")
    [token_call] = world.token_calls
    assert "client_secret" not in token_call, "client_secret_basic: the secret rides the Authorization header"
    for row in await _audits(database):
        assert CLIENT_SECRET not in str(row.payload)


async def test_needs_client_registration_when_nothing_automatic_exists(
    admin_client: httpx.AsyncClient, world: OAuthWorld, database: Database
) -> None:
    world.registration = False
    tool_id = await _tool(admin_client)

    response = await _start(admin_client, tool_id)

    assert response.status_code == 200
    assert response.json() == {
        "status": "needs_client_registration",
        "authorization_url": None,
        "expires_at": None,
        "redirect_uri": LOCAL_REDIRECT,
        "issuer": AS_ROOT,
        "registration": None,
    }
    assert await _rows(database, McpOauthFlow) == []


async def test_a_preregistered_server_needs_its_client_id_at_save(admin_client: httpx.AsyncClient) -> None:
    definition = {
        "kind": "mcp",
        "name": "t",
        "url": MCP_URL,
        "auth": {"kind": "oauth", "registration": "preregistered"},
    }
    response = await admin_client.post(
        "/v1/tools", json={"kind": "mcp", "name": "t", "definition": definition}
    )

    assert response.status_code == 422
    assert response.json()["error"]["details"]["reason"] == "client_id_required"


# ------------------------------------------------------------------------- callback
async def test_a_full_sign_in_stores_the_credential_and_redirects_with_nothing_else(
    admin_client: httpx.AsyncClient,
    client: httpx.AsyncClient,
    world: OAuthWorld,
    database: Database,
    settings: Settings,
) -> None:
    tool_id = await _tool(admin_client)
    before = utcnow()

    _, params, response = await _connect(admin_client, client, world, tool_id)

    assert response.status_code == 302
    assert response.headers["location"] == "http://localhost:3000/console/tools?oauth=ok"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    definition = await _definition(database, tool_id)
    assert definition.auth.kind == "oauth" and definition.credential_id == definition.auth.credential_id
    credential_id = definition.auth.credential_id
    assert credential_id is not None
    bag = await _bag(database, settings, credential_id)
    assert bag["access_token"] == world.access_token and bag["refresh_token"] == world.refresh_token
    expires_at = dt.datetime.fromisoformat(bag["expires_at"])
    assert before + dt.timedelta(seconds=3590) < expires_at < utcnow() + dt.timedelta(seconds=3610)
    assert bag["tool_id"] == tool_id and bag["resource"] == MCP_URL and bag["issuer"] == AS_ROOT
    assert bag["status"] == "active" and bag["registration"] == "dcr" and bag["client_id"] == "dcr-client-1"
    assert bag["registration_access_token"] == "rat-fake-Pq8Lm2"
    [token_call] = world.token_calls
    assert token_call["resource"] == [MCP_URL] and token_call["redirect_uri"] == [LOCAL_REDIRECT]
    assert token_call["grant_type"] == ["authorization_code"]
    for value in (world.access_token, world.refresh_token, params["code"], params["state"]):
        assert value not in response.headers["location"]
    [row] = [r for r in await _rows(database, Credential) if r.provider_id == "mcp-oauth"]
    assert row.fingerprint == "auth.example.com · issues:read"


async def test_status_reports_the_sign_in_without_token_material(
    admin_client: httpx.AsyncClient, client: httpx.AsyncClient, world: OAuthWorld
) -> None:
    tool_id = await _tool(admin_client)
    before = (await admin_client.get(f"/v1/tools/{tool_id}/oauth/status")).json()

    await _connect(admin_client, client, world, tool_id)
    after = await admin_client.get(f"/v1/tools/{tool_id}/oauth/status")

    assert before["status"] == "not_connected"
    body = after.json()
    assert body["status"] == "connected" and body["worker_supported"] is True, (
        "V5-16: sessions use the sign-in"
    )
    assert body["issuer"] == AS_ROOT and body["scopes"] == ["issues:read"] and body["registration"] == "dcr"
    assert body["expires_at"] is not None and body["connected_at"] is not None
    assert world.access_token not in after.text and world.refresh_token not in after.text
    tool = (await admin_client.get(f"/v1/tools/{tool_id}")).text
    assert world.access_token not in tool and world.refresh_token not in tool


@pytest.mark.parametrize(
    ("supported", "sent", "ok"),
    [
        (True, "match", True),
        (True, None, False),
        (False, "other", False),
        (False, None, True),
    ],
    ids=[
        "advertised-present-equal",
        "advertised-absent",
        "unadvertised-present-different",
        "unadvertised-absent",
    ],
)
async def test_the_four_rfc_9207_rows(
    admin_client: httpx.AsyncClient,
    client: httpx.AsyncClient,
    world: OAuthWorld,
    database: Database,
    supported: bool,
    sent: str | None,
    ok: bool,
) -> None:
    world.iss_supported = supported
    tool_id = await _tool(admin_client)
    started = await _started(admin_client, tool_id)
    iss: str | None = {"match": AS_ROOT, "other": "https://evil.example.org"}.get(sent or "")

    response = await _callback(client, world.authorize(started["authorization_url"], iss=iss))

    assert response.status_code == 302
    assert response.headers["location"].endswith("oauth=ok" if ok else "oauth=error")
    assert len(world.token_calls) == (1 if ok else 0)
    if not ok:
        [rejected] = await _audits(database, "mcp_oauth.callback_rejected")
        assert rejected.payload["reason"] == ("issuer_missing" if supported else "issuer_mismatch")


async def test_an_issuer_differing_only_by_a_trailing_slash_is_a_mismatch(
    admin_client: httpx.AsyncClient, client: httpx.AsyncClient, world: OAuthWorld
) -> None:
    world.iss_supported = True
    tool_id = await _tool(admin_client)
    started = await _started(admin_client, tool_id)

    response = await _callback(client, world.authorize(started["authorization_url"], iss=f"{AS_ROOT}/"))

    assert response.headers["location"].endswith("oauth=error") and world.token_calls == []


async def test_on_an_issuer_mismatch_the_error_parameters_are_not_acted_on(
    admin_client: httpx.AsyncClient, client: httpx.AsyncClient, world: OAuthWorld, database: Database
) -> None:
    tool_id = await _tool(admin_client)
    started = await _started(admin_client, tool_id)
    state = _query(started["authorization_url"])["state"]

    await _callback(client, {"state": state, "iss": "https://evil.example.org", "error": "access_denied"})

    [rejected] = await _audits(database, "mcp_oauth.callback_rejected")
    assert rejected.payload["reason"] == "issuer_mismatch" and "provider_error" not in rejected.payload


async def test_a_declined_consent_is_an_error_redirect(
    admin_client: httpx.AsyncClient, client: httpx.AsyncClient, world: OAuthWorld, database: Database
) -> None:
    tool_id = await _tool(admin_client)
    started = await _started(admin_client, tool_id)
    state = _query(started["authorization_url"])["state"]

    response = await _callback(client, {"state": state, "error": "access_denied"})

    assert response.headers["location"].endswith("oauth=error") and world.token_calls == []
    [rejected] = await _audits(database, "mcp_oauth.callback_rejected")
    assert rejected.payload["provider_error"] == "access_denied"


@pytest.mark.parametrize("case", ["unknown", "consumed", "expired", "missing", "oversized"])
async def test_an_unknown_consumed_or_expired_state_is_400_with_no_token_exchange(
    admin_client: httpx.AsyncClient,
    client: httpx.AsyncClient,
    world: OAuthWorld,
    database: Database,
    case: str,
) -> None:
    tool_id = await _tool(admin_client)
    started = await _started(admin_client, tool_id)
    params = world.authorize(started["authorization_url"])
    if case == "unknown":
        params["state"] = "not-a-state-we-issued"
    elif case == "consumed":
        assert (await _callback(client, params)).status_code == 302
        world.token_calls.clear()
    elif case == "expired":
        async with database.session() as session:
            flow = (await session.execute(select(McpOauthFlow))).scalar_one()
            flow.expires_at = utcnow() - dt.timedelta(seconds=1)
    elif case == "missing":
        del params["state"]
    else:
        params["state"] = "s" * 300

    response = await _callback(client, params)

    assert response.status_code == 400
    assert "location" not in response.headers
    assert world.token_calls == []


async def test_a_failed_callback_still_uses_up_the_state(
    admin_client: httpx.AsyncClient, client: httpx.AsyncClient, world: OAuthWorld
) -> None:
    tool_id = await _tool(admin_client)
    started = await _started(admin_client, tool_id)
    params = world.authorize(started["authorization_url"])
    good_code = params["code"]
    params["code"] = "forged-code"

    first = await _callback(client, params)
    second = await _callback(client, {**params, "code": good_code})

    assert first.headers["location"].endswith("oauth=error")
    assert second.status_code == 400
    assert len(world.token_calls) == 1


async def test_a_tool_whose_url_changed_after_start_is_not_signed_in(
    admin_client: httpx.AsyncClient, client: httpx.AsyncClient, world: OAuthWorld, database: Database
) -> None:
    tool_id = await _tool(admin_client)
    started = await _started(admin_client, tool_id)
    async with database.session() as session:
        row = (await session.execute(select(Tool).where(Tool.id == tool_id))).scalar_one()
        row.definition = {**row.definition, "url": "https://mcp.example.com/elsewhere"}

    response = await _callback(client, world.authorize(started["authorization_url"]))

    assert response.headers["location"].endswith("oauth=error") and world.token_calls == []


async def test_starting_again_replaces_the_pending_sign_in(
    admin_client: httpx.AsyncClient, client: httpx.AsyncClient, world: OAuthWorld
) -> None:
    tool_id = await _tool(admin_client)
    first = await _started(admin_client, tool_id)
    await _started(admin_client, tool_id)

    response = await _callback(client, world.authorize(first["authorization_url"]))

    assert response.status_code == 400


async def test_signing_in_again_updates_the_same_credential(
    admin_client: httpx.AsyncClient, client: httpx.AsyncClient, world: OAuthWorld, database: Database
) -> None:
    tool_id = await _tool(admin_client)
    await _connect(admin_client, client, world, tool_id)
    first = (await _definition(database, tool_id)).auth

    world.access_token = "at-fake-second-9Kq"
    await _connect(admin_client, client, world, tool_id)

    assert (await _definition(database, tool_id)).auth == first
    assert len([r for r in await _rows(database, Credential) if r.provider_id == "mcp-oauth"]) == 1


# --------------------------------------------------------------------- permissions
async def test_start_needs_an_admin_with_providers_write(
    app: FastAPI, admin_client: httpx.AsyncClient, world: OAuthWorld, database: Database
) -> None:
    tool_id = await _tool(admin_client)
    _, raw = await make_api_key(database, ["agents:read", "agents:write", "providers:read"])

    async with key_client(app, raw) as builder:
        response = await builder.post(f"/v1/tools/{tool_id}/oauth/start", json={})

    assert response.status_code == 403
    assert world.requests == []


async def test_start_refuses_a_tool_that_does_not_sign_in(
    admin_client: httpx.AsyncClient, world: OAuthWorld
) -> None:
    definition = {"kind": "mcp", "name": "t", "url": MCP_URL}
    created = await admin_client.post(
        "/v1/tools", json={"kind": "mcp", "name": "t", "definition": definition}
    )

    response = await _start(admin_client, created.json()["id"])

    assert response.json()["error"]["details"]["reason"] == "not_oauth"


# --------------------------------------------------------------- binding at save
async def test_an_oauth_url_may_not_carry_secret_placeholders(admin_client: httpx.AsyncClient) -> None:
    definition = {
        "kind": "mcp",
        "name": "t",
        "url": f"{MCP_URL}?k={{{{ secret.refresh_token }}}}",
        "auth": {"kind": "oauth"},
    }
    response = await admin_client.post(
        "/v1/tools", json={"kind": "mcp", "name": "t", "definition": definition}
    )

    assert response.status_code == 422
    assert response.json()["error"]["details"]["reason"] == "oauth_url_placeholder"


async def _forged_sign_in(database: Database, settings: Settings, bag: dict[str, str]) -> str:
    """An ``mcp-oauth`` row written straight to the database (the route refuses one, S5-15)."""
    async with database.session() as session:
        row = Credential(
            provider_id="mcp-oauth",
            label="forged",
            ciphertext=Vault(settings.master_key).encrypt(bag),
            fingerprint="forged",
        )
        session.add(row)
        await session.flush()
        return row.id


async def test_a_sign_in_credential_cannot_be_bound_by_hand(
    admin_client: httpx.AsyncClient,
    client: httpx.AsyncClient,
    world: OAuthWorld,
    database: Database,
    settings: Settings,
) -> None:
    tool_id = await _tool(admin_client)
    await _connect(admin_client, client, world, tool_id)
    credential_id = (await _definition(database, tool_id)).auth.credential_id
    forged_id = await _forged_sign_in(database, settings, {"tool_id": "x", "resource": MCP_URL})

    def payload(url: str, cid: str | None, auth_kind: str = "oauth") -> dict[str, Any]:
        auth: dict[str, Any] = {"kind": auth_kind, "credential_id": cid}
        return {
            "kind": "mcp",
            "name": "t",
            "definition": {"kind": "mcp", "name": "t", "url": url, "auth": auth},
        }

    on_create = await admin_client.post("/v1/tools", json=payload(MCP_URL, credential_id))
    other_tool = await _tool(admin_client)
    stolen = await admin_client.put(f"/v1/tools/{other_tool}", json=payload(MCP_URL, credential_id))
    hand_made = await admin_client.put(f"/v1/tools/{tool_id}", json=payload(MCP_URL, forged_id))
    moved = await admin_client.put(
        f"/v1/tools/{tool_id}", json=payload("https://mcp.example.com/v2", credential_id)
    )
    as_header = await admin_client.put(f"/v1/tools/{tool_id}", json=payload(MCP_URL, credential_id, "header"))
    kept = await admin_client.put(f"/v1/tools/{tool_id}", json=payload(MCP_URL, credential_id))

    for response in (on_create, stolen, hand_made, moved, as_header):
        assert response.status_code == 422, response.text
    assert moved.json()["error"]["details"]["reason"] == "oauth_credential_mismatch"
    assert as_header.json()["error"]["details"]["reason"] == "oauth_credential_misuse"
    assert kept.status_code == 200, kept.text


async def test_a_save_without_the_credential_keeps_the_sign_in(
    admin_client: httpx.AsyncClient, client: httpx.AsyncClient, world: OAuthWorld, database: Database
) -> None:
    tool_id = await _tool(admin_client)
    await _connect(admin_client, client, world, tool_id)
    credential_id = (await _definition(database, tool_id)).auth.credential_id
    definition = {"kind": "mcp", "name": "t", "url": MCP_URL, "auth": {"kind": "oauth"}}

    response = await admin_client.put(
        f"/v1/tools/{tool_id}", json={"kind": "mcp", "name": "t", "definition": definition}
    )

    assert response.status_code == 200, response.text
    assert (await _definition(database, tool_id)).auth.credential_id == credential_id


# ----------------------------------------------------------------- the test route
async def test_the_test_route_connects_with_the_stored_access_token(
    admin_client: httpx.AsyncClient, client: httpx.AsyncClient, world: OAuthWorld
) -> None:
    tool_id = await _tool(admin_client)
    before = await admin_client.post(f"/v1/tools/{tool_id}/test")
    await _connect(admin_client, client, world, tool_id)

    after = await admin_client.post(f"/v1/tools/{tool_id}/test")

    assert before.json()["reason"] == "needs_auth"
    assert after.json()["ok"] is True, after.text
    assert after.json()["tool_names"] == ["list_issues", "create_issue"]
    assert world.access_token not in after.text


async def test_the_test_route_refreshes_an_expired_sign_in_before_calling(
    admin_client: httpx.AsyncClient,
    client: httpx.AsyncClient,
    world: OAuthWorld,
    database: Database,
    settings: Settings,
) -> None:
    """V5-16 (was: reported, not refreshed): the stored refresh token renews it first."""
    tool_id = await _tool(admin_client)
    await _connect(admin_client, client, world, tool_id)
    credential_id = (await _definition(database, tool_id)).auth.credential_id
    vault = Vault(settings.master_key)
    async with database.session() as session:
        row = (await session.execute(select(Credential).where(Credential.id == credential_id))).scalar_one()
        bag = vault.decrypt(row.ciphertext)
        row.ciphertext = vault.encrypt(
            {**bag, "expires_at": (utcnow() - dt.timedelta(minutes=1)).isoformat()}
        )
    world.requests.clear()

    response = await admin_client.post(f"/v1/tools/{tool_id}/test")

    assert response.json()["ok"] is True, response.text
    assert len(world.refresh_calls) == 1
    assert [r.url.path for r in world.requests][0] == "/token", "refresh before the MCP call"


# ------------------------------------------------------------- the metadata document
async def test_the_client_metadata_document_is_served_only_on_a_public_https_origin(
    app: FastAPI, client: httpx.AsyncClient, settings: Settings
) -> None:
    local = await client.get("/v1/oauth/mcp/client-metadata.json")
    _use_settings(app, settings, public_base_url=PUBLIC_BASE)
    public = await client.get("/v1/oauth/mcp/client-metadata.json")

    assert local.status_code == 404
    assert public.status_code == 200
    document = public.json()
    assert document["client_id"] == f"{PUBLIC_BASE}/v1/oauth/mcp/client-metadata.json"
    assert document["redirect_uris"] == [f"{PUBLIC_BASE}/v1/oauth/mcp/callback"]
    assert document["client_name"] == "LKAP" and document["token_endpoint_auth_method"] == "none"


@pytest.mark.parametrize("base", ["https://10.1.2.3", "http://lkap.example.com", "https://localhost"])
async def test_no_metadata_document_on_a_private_or_plain_origin(
    app: FastAPI, client: httpx.AsyncClient, settings: Settings, base: str
) -> None:
    _use_settings(app, settings, public_base_url=base)

    assert (await client.get("/v1/oauth/mcp/client-metadata.json")).status_code == 404


# ---------------------------------------------------------------------- audit, logs
async def test_audit_rows_name_the_steps_and_carry_no_secret(
    admin_client: httpx.AsyncClient, client: httpx.AsyncClient, world: OAuthWorld, database: Database
) -> None:
    tool_id = await _tool(admin_client)
    _, params, _ = await _connect(admin_client, client, world, tool_id)
    await _callback(client, params)  # a replay: rejected

    rows = await _audits(database)
    actions = [row.action for row in rows]
    assert "mcp_oauth.start" in actions and "mcp_oauth.callback_ok" in actions
    assert "mcp_oauth.callback_rejected" in actions
    ok = next(row for row in rows if row.action == "mcp_oauth.callback_ok")
    assert ok.target_id == tool_id and ok.actor_type == "system" and ok.actor_id == "break-glass"
    text = " ".join(str(row.payload) for row in rows)
    for secret in (world.access_token, world.refresh_token, params["code"], params["state"], "rat-fake"):
        assert secret not in text


async def test_no_token_code_or_state_reaches_a_log_line(
    admin_client: httpx.AsyncClient,
    client: httpx.AsyncClient,
    world: OAuthWorld,
    database: Database,
    log_capture: pytest.LogCaptureFixture,
) -> None:
    tool_id = await _tool(admin_client)
    started, params, _ = await _connect(admin_client, client, world, tool_id)
    await admin_client.post(f"/v1/tools/{tool_id}/test")
    [token_call] = world.token_calls

    text = captured_text(log_capture, scope=None)
    secrets = [
        world.access_token,
        world.refresh_token,
        params["code"],
        params["state"],
        token_call["code_verifier"][0],
        _query(started["authorization_url"])["code_challenge"],
    ]
    for secret in secrets:
        assert secret not in text


def test_the_log_processor_masks_oauth_fields() -> None:
    event = {
        "event": "x",
        "access_token": "a",
        "refresh_token": "r",
        "code": "c",
        "state": "s",
        "code_verifier": "v",
        "client_secret": "cs",
        "registration_access_token": "rat",
        "tool_id": "t1",
    }

    redacted = redact_oauth_fields(None, "info", dict(event))

    assert redacted["tool_id"] == "t1" and redacted["event"] == "x"
    for key in event.keys() - {"tool_id", "event"}:
        assert redacted[key] == REDACTED


def test_the_access_log_filter_drops_the_callback_query() -> None:
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", f"{CALLBACK}?code=abc&state=def", "1.1", 302),
        None,
    )
    other = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", "/v1/tools?kind=mcp", "1.1", 200),
        None,
    )

    assert CallbackQueryFilter().filter(record) and CallbackQueryFilter().filter(other)

    assert "abc" not in record.getMessage() and "def" not in record.getMessage()
    assert "/v1/tools?kind=mcp" in other.getMessage()


# ------------------------------------------------------------------------- the sweep
async def test_the_sweep_deletes_consumed_and_expired_flows(
    admin_client: httpx.AsyncClient, client: httpx.AsyncClient, world: OAuthWorld, database: Database
) -> None:
    done = await _tool(admin_client)
    await _connect(admin_client, client, world, done)
    stale = await _tool(admin_client)
    await _started(admin_client, stale)
    live = await _tool(admin_client)
    await _started(admin_client, live)
    async with database.session() as session:
        flow = (await session.execute(select(McpOauthFlow).where(McpOauthFlow.tool_id == stale))).scalar_one()
        flow.expires_at = utcnow() - dt.timedelta(seconds=1)
        session.add(
            McpOauthClient(
                workspace_id=flow.workspace_id,
                issuer=AS_ROOT,
                client_id="lapsed",
                registration="dcr",
                redirect_uri=LOCAL_REDIRECT,
                token_endpoint_auth_method="none",
                client_secret_expires_at=utcnow() - dt.timedelta(days=1),
            )
        )

    flows, clients = await sweep_mcp_oauth(database)

    assert (flows, clients) == (2, 1)
    assert [row.tool_id for row in await _rows(database, McpOauthFlow)] == [live]
    assert [row.client_id for row in await _rows(database, McpOauthClient)] == ["dcr-client-1"]


# --------------------------------------------------------------------------- units
@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://auth.example.com/token", True),
        ("http://auth.example.com/token", False),
        ("http://127.0.0.1:9000/token", True),
        ("https://user:pw@auth.example.com/token", False),
        ("https://auth.example.com/token#frag", False),
        ("https://192.168.1.5/token", False),
        ("https://metadata.google.internal/token", False),
    ],
)
def test_url_problem(url: str, ok: bool) -> None:
    from lkap_api.net_guard import NetPolicy

    policy = UrlPolicy(net=NetPolicy.from_entries(["127.0.0.1"]), allow_http_loopback=True)

    assert (url_problem(url, policy) is None) is ok


def test_resource_covers_is_hierarchical_and_case_insensitive_on_the_host() -> None:
    assert resource_covers("https://mcp.example.com", "https://MCP.example.com/mcp")
    assert resource_covers("https://mcp.example.com/mcp", "https://mcp.example.com/mcp")
    assert not resource_covers("https://mcp.example.com/mcp", "https://mcp.example.com/mcp2")
    assert not resource_covers("https://mcp.example.com", "https://other.example.com/mcp")


def test_the_state_is_stored_as_a_hash() -> None:
    assert hash_state("abc") == hashlib.sha256(b"abc").hexdigest()


def test_the_authorization_url_replaces_reserved_parameters_of_the_endpoint() -> None:
    url = authorization_url(
        "https://auth.example.com/authorize?client_id=evil&tenant=1",
        client_id="c",
        redirect="https://lkap.example.com/cb",
        state="s",
        challenge="x" * 43,
        resource=MCP_URL,
        scopes=None,
    )

    query = parse_qs(urlsplit(url).query)
    assert query["client_id"] == ["c"] and query["tenant"] == ["1"] and "scope" not in query


# ------------------------------------------------------------------ the migration
def _migrate_copy(database: Path, revision: str, *, downgrade: bool = False) -> None:
    from alembic.config import Config

    from alembic import command

    api_root = Path(__file__).resolve().parents[1]
    config = Config(str(api_root / "alembic.ini"))
    config.set_main_option("script_location", str(api_root / "alembic"))
    config.cmd_opts = None  # type: ignore[assignment]
    url = f"sqlite+aiosqlite:///{database}"
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["configure_logger"] = False
    os.environ["LKAP_DATABASE_URL"] = url
    try:
        (command.downgrade if downgrade else command.upgrade)(config, revision)
    finally:
        os.environ.pop("LKAP_DATABASE_URL", None)


def _tables(database: Path) -> set[str]:
    connection = sqlite3.connect(database)
    try:
        return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        connection.close()


def test_the_migration_goes_up_down_and_up_on_a_scratch_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``v5_004_mcp_oauth`` on a copy of the v1 seed (never the live database)."""
    for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "LKAP_MASTER_KEY"):
        monkeypatch.delenv(name, raising=False)
    database = tmp_path / "lkap.db"
    shutil.copy(Path(__file__).resolve().parent / "fixtures" / "v1_seed.sqlite", database)
    added = {"mcp_oauth_flows", "mcp_oauth_clients"}

    _migrate_copy(database, "v5_004_mcp_oauth")
    assert added <= _tables(database)
    _migrate_copy(database, "v5_009_consent", downgrade=True)
    assert not added & _tables(database)
    _migrate_copy(database, "v5_004_mcp_oauth")
    assert added <= _tables(database)
