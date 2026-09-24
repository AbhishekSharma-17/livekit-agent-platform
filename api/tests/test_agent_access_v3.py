"""V3-00: the api side of the Agent Access Layer (docs/v3/AGENT-ACCESS.md §7).

* the ``audit:read`` scope on ``GET /v1/audit``;
* ``GET /v1/api-keys/self``;
* agent keys (``kind``, ``client``, ``last_client``) and migration ``v3_001_agent_keys``;
* ``X-LKAP-Client`` attribution in audit payloads;
* knowledge-base url import through the network guard.

Outbound fetches are mocked with ``respx`` (or a guarded client with a fake
resolver), so the suite never touches the network.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import os
import sqlite3
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from alembic.config import Config
from auth_helpers import client_for, login, make_api_key, make_user
from fastapi import FastAPI

from alembic import command
from lkap_api import net_guard
from lkap_api.auth.api_keys import resolve_api_key
from lkap_api.auth.audit import ClientInfo, parse_client_header
from lkap_api.db.constants import DEFAULT_WORKSPACE_ID
from lkap_api.db.models import ApiKey
from lkap_api.db.session import Database
from lkap_api.deps import get_http_client
from lkap_api.kb.embed import FakeEmbedder
from lkap_api.routers.knowledge import MAX_UPLOAD_BYTES, get_embedder, import_filename

API_ROOT = Path(__file__).resolve().parents[1]
MCP_HEADER = "lkap-mcp/0.1; client=claude-code; tool=agent_create; call=abc"
MCP_CLIENT = {"product": "lkap-mcp", "name": "claude-code", "tool": "agent_create", "call": "abc"}


# --------------------------------------------------------------------------- fixtures
async def _fake_resolve_embedder(*args: object, **kwargs: object) -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture(autouse=True)
def _fake_embedder(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    app.dependency_overrides[get_embedder] = lambda: FakeEmbedder()
    monkeypatch.setattr("lkap_api.kb.ingest.resolve_embedder", _fake_resolve_embedder)
    yield
    app.dependency_overrides.pop(get_embedder, None)


def _bearer(raw: str, **extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}", **extra}


async def _audit_rows(admin_client: httpx.AsyncClient) -> list[dict[str, Any]]:
    response = await admin_client.get("/v1/audit", params={"limit": 200})
    assert response.status_code == 200, response.text
    rows: list[dict[str, Any]] = response.json()["items"]
    return rows


async def _key_row(admin_client: httpx.AsyncClient, key_id: str) -> dict[str, Any]:
    listed = await admin_client.get("/v1/api-keys", params={"limit": 200})
    assert listed.status_code == 200, listed.text
    [row] = [item for item in listed.json()["items"] if item["id"] == key_id]
    return dict(row)


async def _new_kb(admin_client: httpx.AsyncClient, name: str = "Policies") -> str:
    created = await admin_client.post("/v1/knowledge-bases", json={"name": name})
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


# --------------------------------------------------------------------------- audit:read
async def test_list_audit_with_audit_read_key_returns_rows(app: FastAPI, database: Database) -> None:
    _, raw = await make_api_key(database, ["audit:read"])

    async with client_for(app, _bearer(raw)) as agent:
        response = await agent.get("/v1/audit")

    assert response.status_code == 200, response.text
    assert "items" in response.json()


async def test_list_audit_with_sessions_read_key_is_403_naming_the_scope(
    app: FastAPI, database: Database
) -> None:
    _, raw = await make_api_key(database, ["sessions:read"])

    async with client_for(app, _bearer(raw)) as agent:
        response = await agent.get("/v1/audit")

    assert response.status_code == 403, response.text
    error = response.json()["error"]
    assert error["details"]["required_scope"] == "audit:read"
    assert "audit:read" in error["message"]


async def test_create_api_key_with_audit_read_scope_is_accepted(admin_client: httpx.AsyncClient) -> None:
    response = await admin_client.post("/v1/api-keys", json={"name": "reader", "scopes": ["audit:read"]})

    assert response.status_code == 201, response.text
    assert response.json()["scopes"] == ["audit:read"]


# --------------------------------------------------------------------------- /v1/api-keys/self
async def test_get_own_api_key_read_only_key_returns_scopes_and_workspace(
    app: FastAPI, database: Database
) -> None:
    scopes = ["agents:read", "connections:read", "providers:read", "sessions:read"]
    key_id, raw = await make_api_key(database, scopes)

    async with client_for(app, _bearer(raw)) as agent:
        response = await agent.get("/v1/api-keys/self")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == key_id
    assert sorted(body["scopes"]) == scopes
    assert body["kind"] == "standard"
    assert body["client"] is None
    assert body["prefix"] == raw[:8]
    assert body["workspace"] == {"id": DEFAULT_WORKSPACE_ID, "slug": "default", "name": "Default"}
    assert not {"key", "key_hash"} & set(body)
    assert raw not in response.text


async def test_get_own_api_key_agent_key_reports_kind_and_client(app: FastAPI, database: Database) -> None:
    _, raw = await make_api_key(database, ["agents:read"], kind="agent", client="codex")

    async with client_for(app, _bearer(raw)) as agent:
        body = (await agent.get("/v1/api-keys/self")).json()

    assert (body["kind"], body["client"]) == ("agent", "codex")


async def test_get_own_api_key_with_cookie_session_is_401(app: FastAPI, database: Database) -> None:
    await make_user(database, "owner@example.com", role="owner")

    async with await login(app, "owner@example.com") as user:
        response = await user.get("/v1/api-keys/self")

    assert response.status_code == 401, response.text


async def test_get_own_api_key_with_admin_token_is_401(admin_client: httpx.AsyncClient) -> None:
    response = await admin_client.get("/v1/api-keys/self")

    assert response.status_code == 401, response.text


async def test_get_own_api_key_anonymous_is_401(client: httpx.AsyncClient) -> None:
    assert (await client.get("/v1/api-keys/self")).status_code == 401


async def test_get_own_api_key_revoked_key_is_401(
    app: FastAPI, database: Database, admin_client: httpx.AsyncClient
) -> None:
    key_id, raw = await make_api_key(database, ["agents:read"])
    assert (await admin_client.delete(f"/v1/api-keys/{key_id}")).status_code == 204

    async with client_for(app, _bearer(raw)) as agent:
        assert (await agent.get("/v1/api-keys/self")).status_code == 401


# --------------------------------------------------------------------------- agent keys + attribution
async def test_create_api_key_agent_kind_is_attributed_after_a_client_header_request(
    app: FastAPI, admin_client: httpx.AsyncClient
) -> None:
    created = await admin_client.post(
        "/v1/api-keys",
        json={
            "name": "Claude Code",
            "scopes": ["agents:write", "audit:read"],
            "kind": "agent",
            "client": "claude-code",
        },
    )
    assert created.status_code == 201, created.text
    key = created.json()
    assert (key["kind"], key["client"], key["last_client"]) == ("agent", "claude-code", None)

    listed = await _key_row(admin_client, key["id"])
    assert (listed["kind"], listed["client"], listed["last_client"]) == ("agent", "claude-code", None)

    async with client_for(app, _bearer(key["key"], **{"X-LKAP-Client": MCP_HEADER})) as agent:
        made = await agent.post("/v1/knowledge-bases", json={"name": "Agent KB"})
    assert made.status_code == 201, made.text

    assert (await _key_row(admin_client, key["id"]))["last_client"] == "lkap-mcp"
    [row] = [r for r in await _audit_rows(admin_client) if r["actor_id"] == key["id"]]
    assert row["actor_type"] == "api_key"
    assert row["action"] == "POST /v1/knowledge-bases"
    assert row["payload"]["client"] == MCP_CLIENT
    [minted] = [r for r in await _audit_rows(admin_client) if r["action"] == "api_key.create"]
    assert "client" not in minted["payload"]
    assert minted["payload"]["kind"] == "agent"


async def test_audit_record_with_client_header_tags_rows_written_by_routers(
    app: FastAPI, database: Database, admin_client: httpx.AsyncClient
) -> None:
    # `PUT /v1/workspaces/{id}` writes its own action-specific row via `record`.
    async with client_for(app, {"X-Admin-Token": "test-admin", "X-LKAP-Client": MCP_HEADER}) as agent:
        response = await agent.put(f"/v1/workspaces/{DEFAULT_WORKSPACE_ID}", json={"name": "Renamed"})
    assert response.status_code == 200, response.text

    [row] = [r for r in await _audit_rows(admin_client) if r["action"] == "workspace.update"]
    assert row["payload"]["client"] == MCP_CLIENT
    assert row["payload"]["fields"] == ["name"]


@pytest.mark.parametrize(
    "header",
    [
        "",
        "   ",
        "; client=claude-code",
        "lkap mcp/0.1; client=claude-code",
        "lkap-mcp/0.1; client",
        "lkap-mcp/0.1; client=" + "x" * 65,
        "lkap-mcp/0.1; client=a\x01b",
        "lkap-mcp/0.1; =claude-code",
        "x" * 65 + "/0.1",
    ],
)
async def test_resolve_principal_malformed_client_header_changes_nothing(
    app: FastAPI, database: Database, admin_client: httpx.AsyncClient, header: str
) -> None:
    key_id, raw = await make_api_key(database, ["agents:write"], kind="agent", client="claude-code")

    async with client_for(app, _bearer(raw, **{"X-LKAP-Client": header})) as agent:
        assert (await agent.post("/v1/knowledge-bases", json={"name": "KB"})).status_code == 201

    assert (await _key_row(admin_client, key_id))["last_client"] is None
    [row] = [r for r in await _audit_rows(admin_client) if r["actor_id"] == key_id]
    assert "client" not in row["payload"]


async def test_resolve_principal_without_header_after_one_with_it_records_no_client(
    app: FastAPI, database: Database, admin_client: httpx.AsyncClient
) -> None:
    # Under ASGITransport every request runs in the test's own task: the context
    # variable must be reset per request, not left over from the previous one.
    first_id, first_raw = await make_api_key(database, ["agents:write"])
    second_id, second_raw = await make_api_key(database, ["agents:write"])

    async with client_for(app, _bearer(first_raw, **{"X-LKAP-Client": MCP_HEADER})) as agent:
        assert (await agent.post("/v1/knowledge-bases", json={"name": "One"})).status_code == 201
    async with client_for(app, _bearer(second_raw)) as plain:
        assert (await plain.post("/v1/knowledge-bases", json={"name": "Two"})).status_code == 201

    rows = {r["actor_id"]: r for r in await _audit_rows(admin_client) if r["actor_type"] == "api_key"}
    assert rows[first_id]["payload"]["client"] == MCP_CLIENT
    assert "client" not in rows[second_id]["payload"]
    assert (await _key_row(admin_client, second_id))["last_client"] is None


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"kind": "robot"}, "kind"),
        ({"client": "c" * 65}, "client"),
        ({"client": ""}, "client"),
    ],
)
async def test_create_api_key_invalid_agent_fields_are_422(
    admin_client: httpx.AsyncClient, payload: dict[str, str], field: str
) -> None:
    response = await admin_client.post(
        "/v1/api-keys", json={"name": "bad", "scopes": ["agents:read"], **payload}
    )

    assert response.status_code == 422, response.text
    assert field in response.text


async def test_create_api_key_without_kind_defaults_to_standard(admin_client: httpx.AsyncClient) -> None:
    created = await admin_client.post("/v1/api-keys", json={"name": "script", "scopes": ["agents:read"]})

    assert created.status_code == 201, created.text
    assert (created.json()["kind"], created.json()["client"]) == ("standard", None)


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (MCP_HEADER, ClientInfo("lkap-mcp", "0.1", "claude-code", "agent_create", "abc")),
        ("lkap-mcp", ClientInfo("lkap-mcp")),
        ("lkap-mcp/0.1;client=Cursor IDE", ClientInfo("lkap-mcp", "0.1", "Cursor IDE")),
        (
            "lkap-mcp/0.1; CLIENT=codex; extra=ignored; tool=kb_create",
            ClientInfo("lkap-mcp", "0.1", "codex", "kb_create"),
        ),
        ("lkap-mcp/0.1; client=a; client=b", ClientInfo("lkap-mcp", "0.1", "a")),
        ("lkap-mcp/0.1; call=" + "c" * 64, ClientInfo("lkap-mcp", "0.1", call="c" * 64)),
        (None, None),
        ("lkap-mcp/0.1; client=" + "c" * 65, None),
        ("lkap-mcp/0.1; tool=a=b", None),
        ("lkap-mcp/0.1 extra", None),
        ("lkap-mcp/0.1; client=x" + ";" * 600, None),
    ],
)
def test_parse_client_header_grammar(header: str | None, expected: ClientInfo | None) -> None:
    assert parse_client_header(header) == expected


async def test_resolve_api_key_last_client_follows_the_last_used_resolution(database: Database) -> None:
    key_id, raw = await make_api_key(database, ["agents:read"])
    start = dt.datetime(2026, 9, 24, 12, 0, tzinfo=dt.UTC)

    async with database.session() as session:
        await resolve_api_key(session, raw, now=start, client_product="lkap-mcp")
    async with database.session() as session:  # within 60 s: nothing is written
        await resolve_api_key(session, raw, now=start + dt.timedelta(seconds=30), client_product="other")
    async with database.session() as session:  # due again, but no header: kept
        await resolve_api_key(session, raw, now=start + dt.timedelta(seconds=90))
    async with database.session() as session:
        row = await session.get(ApiKey, key_id)
        assert row is not None
        assert row.last_client == "lkap-mcp"
        assert row.last_used_at == start + dt.timedelta(seconds=90)


# --------------------------------------------------------------------------- knowledge-base url import
async def test_import_document_public_markdown_url_becomes_ready_with_chunks(
    admin_client: httpx.AsyncClient,
) -> None:
    kb_id = await _new_kb(admin_client)
    text = "# Flood coverage\n\nFlood damage to a basement is covered under the HO-4 policy line."

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://example.test/policy.md").respond(
            200, content=text.encode(), headers={"content-type": "text/markdown; charset=utf-8"}
        )
        response = await admin_client.post(
            f"/v1/knowledge-bases/{kb_id}/documents/import", json={"url": "https://example.test/policy.md"}
        )

    assert response.status_code == 201, response.text
    document = response.json()
    assert document["status"] == "pending"
    assert (document["filename"], document["mime"], document["bytes"]) == (
        "policy.md",
        "text/markdown",
        len(text.encode()),
    )
    [stored] = (await admin_client.get(f"/v1/knowledge-bases/{kb_id}/documents")).json()["items"]
    assert stored["status"] == "ready", stored
    assert stored["chunk_count"] >= 1
    hits = await admin_client.post(f"/v1/knowledge-bases/{kb_id}/search", json={"query": "basement flood"})
    assert "HO-4" in hits.json()["hits"][0]["text"]


async def test_import_document_filename_goes_through_the_basename_rule(
    admin_client: httpx.AsyncClient,
) -> None:
    kb_id = await _new_kb(admin_client)

    with respx.mock:
        respx.get("https://example.test/export").respond(
            200, content=b"plain notes", headers={"content-type": "text/plain"}
        )
        response = await admin_client.post(
            f"/v1/knowledge-bases/{kb_id}/documents/import",
            json={"url": "https://example.test/export", "filename": "../../etc/notes.txt"},
        )

    assert response.status_code == 201, response.text
    assert response.json()["filename"] == "notes.txt"


@pytest.mark.parametrize(
    ("url", "filename", "mime", "expected"),
    [
        ("https://example.test/a/policy.md", None, "text/markdown", "policy.md"),
        ("https://example.test/", None, "text/markdown", "document.md"),
        ("https://example.test/raw", None, "application/pdf", "raw.pdf"),
        ("https://example.test/My%20Doc.txt", None, "text/plain", "My Doc.txt"),
        ("https://example.test/x", "C:\\temp\\y.json", "application/json", "y.json"),
        ("https://example.test/x", None, "text/csv", "x"),
    ],
)
def test_import_filename_rules(url: str, filename: str | None, mime: str, expected: str) -> None:
    assert import_filename(url, filename, mime) == expected


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1/admin",
        "http://2130706433/",
        "http://10.0.0.7/doc.md",
        "http://localhost:8080/v1/health",
        "http://[::1]/doc.md",
    ],
)
async def test_import_document_private_destination_is_422_blocked_destination(
    admin_client: httpx.AsyncClient, url: str
) -> None:
    kb_id = await _new_kb(admin_client)

    with respx.mock(assert_all_called=False) as mock:
        route = mock.route().respond(200, content=b"secret", headers={"content-type": "text/plain"})
        response = await admin_client.post(f"/v1/knowledge-bases/{kb_id}/documents/import", json={"url": url})

    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["reason"] == "blocked_destination"
    assert not route.called
    assert (await admin_client.get(f"/v1/knowledge-bases/{kb_id}/documents")).json()["total"] == 0


async def test_import_document_name_resolving_to_loopback_is_blocked_at_connect_time(
    app: FastAPI, admin_client: httpx.AsyncClient
) -> None:
    # DNS rebinding: the name passes the offline check; the guarded transport
    # refuses the address it resolves to (prod policy: nothing exempt).
    async def resolve(host: str, port: int) -> list[ipaddress.IPv4Address]:
        return [ipaddress.IPv4Address("127.0.0.1")]

    async def guarded() -> AsyncIterator[httpx.AsyncClient]:
        transport = net_guard.GuardedTransport(net_guard.NetPolicy(), resolve=resolve)
        async with httpx.AsyncClient(transport=transport, follow_redirects=False) as client:
            yield client

    app.dependency_overrides[get_http_client] = guarded
    try:
        kb_id = await _new_kb(admin_client)
        response = await admin_client.post(
            f"/v1/knowledge-bases/{kb_id}/documents/import", json={"url": "http://rebind.example/doc.md"}
        )
    finally:
        app.dependency_overrides.pop(get_http_client, None)

    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["reason"] == "blocked_destination"


async def test_import_document_declared_30mb_body_is_413(admin_client: httpx.AsyncClient) -> None:
    kb_id = await _new_kb(admin_client)
    body = b"a" * (30 * 1024 * 1024)

    with respx.mock:
        respx.get("https://example.test/huge.txt").respond(
            200, content=body, headers={"content-type": "text/plain"}
        )
        response = await admin_client.post(
            f"/v1/knowledge-bases/{kb_id}/documents/import", json={"url": "https://example.test/huge.txt"}
        )

    assert response.status_code == 413, response.text
    assert response.json()["error"]["details"]["max_bytes"] == MAX_UPLOAD_BYTES
    assert (await admin_client.get(f"/v1/knowledge-bases/{kb_id}/documents")).json()["total"] == 0


async def test_import_document_streamed_body_over_the_cap_is_413(admin_client: httpx.AsyncClient) -> None:
    kb_id = await _new_kb(admin_client)
    chunk = b"b" * (1024 * 1024)

    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(30):
            yield chunk

    with respx.mock:
        respx.get("https://example.test/stream.txt").mock(
            return_value=httpx.Response(200, headers={"content-type": "text/plain"}, content=chunks())
        )
        response = await admin_client.post(
            f"/v1/knowledge-bases/{kb_id}/documents/import", json={"url": "https://example.test/stream.txt"}
        )

    assert response.status_code == 413, response.text


@pytest.mark.parametrize("content_type", ["image/png", "application/octet-stream", None])
async def test_import_document_unsupported_content_type_is_415(
    admin_client: httpx.AsyncClient, content_type: str | None
) -> None:
    kb_id = await _new_kb(admin_client)
    headers = {"content-type": content_type} if content_type else {}

    with respx.mock:
        respx.get("https://example.test/picture").mock(
            return_value=httpx.Response(200, headers=headers, stream=httpx.ByteStream(b"\x89PNG"))
        )
        response = await admin_client.post(
            f"/v1/knowledge-bases/{kb_id}/documents/import", json={"url": "https://example.test/picture"}
        )

    assert response.status_code == 415, response.text
    assert response.json()["error"]["code"] == "unsupported_media_type"


@pytest.mark.parametrize("content_type", ["application/pdf", "application/json", "text/csv"])
async def test_import_document_accepted_content_types_are_201(
    admin_client: httpx.AsyncClient, content_type: str
) -> None:
    kb_id = await _new_kb(admin_client)

    with respx.mock:
        respx.get("https://example.test/doc").respond(
            200, content=b'{"a": 1}', headers={"content-type": content_type}
        )
        response = await admin_client.post(
            f"/v1/knowledge-bases/{kb_id}/documents/import", json={"url": "https://example.test/doc"}
        )

    assert response.status_code == 201, response.text
    assert response.json()["mime"] == content_type


@pytest.mark.parametrize("status_code", [302, 404, 500])
async def test_import_document_non_2xx_answer_is_422_fetch_failed(
    admin_client: httpx.AsyncClient, status_code: int
) -> None:
    kb_id = await _new_kb(admin_client)

    with respx.mock:
        respx.get("https://example.test/moved.md").respond(
            status_code, headers={"location": "http://169.254.169.254/", "content-type": "text/plain"}
        )
        second = respx.get("http://169.254.169.254/")
        response = await admin_client.post(
            f"/v1/knowledge-bases/{kb_id}/documents/import", json={"url": "https://example.test/moved.md"}
        )

    assert response.status_code == 422, response.text
    details = response.json()["error"]["details"]
    assert (details["reason"], details["status_code"]) == ("fetch_failed", status_code)
    assert not second.called


async def test_import_document_connection_error_is_422_fetch_failed(admin_client: httpx.AsyncClient) -> None:
    kb_id = await _new_kb(admin_client)

    with respx.mock:
        respx.get("https://example.test/down.md").mock(side_effect=httpx.ConnectTimeout("timed out"))
        response = await admin_client.post(
            f"/v1/knowledge-bases/{kb_id}/documents/import", json={"url": "https://example.test/down.md"}
        )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["reason"] == "fetch_failed"


@pytest.mark.parametrize("url", ["ftp://example.test/doc.md", "file:///etc/passwd", "not a url"])
async def test_import_document_non_http_url_is_422(admin_client: httpx.AsyncClient, url: str) -> None:
    kb_id = await _new_kb(admin_client)

    response = await admin_client.post(f"/v1/knowledge-bases/{kb_id}/documents/import", json={"url": url})

    assert response.status_code == 422, response.text


async def test_import_document_unknown_kb_is_404(admin_client: httpx.AsyncClient) -> None:
    response = await admin_client.post(
        "/v1/knowledge-bases/nope/documents/import", json={"url": "https://example.test/policy.md"}
    )

    assert response.status_code == 404, response.text


async def test_import_document_read_only_key_is_403_and_agents_write_key_is_allowed(
    app: FastAPI, database: Database, admin_client: httpx.AsyncClient
) -> None:
    kb_id = await _new_kb(admin_client)
    _, reader = await make_api_key(database, ["agents:read"])
    _, writer = await make_api_key(database, ["agents:write"])
    url = f"/v1/knowledge-bases/{kb_id}/documents/import"

    with respx.mock:
        respx.get("https://example.test/policy.md").respond(
            200, content=b"covered", headers={"content-type": "text/markdown"}
        )
        async with client_for(app, _bearer(reader)) as read_only:
            denied = await read_only.post(url, json={"url": "https://example.test/policy.md"})
        async with client_for(app, _bearer(writer)) as builder:
            allowed = await builder.post(url, json={"url": "https://example.test/policy.md"})

    assert denied.status_code == 403, denied.text
    assert denied.json()["error"]["details"]["required_scope"] == "agents:write"
    assert allowed.status_code == 201, allowed.text


async def test_import_document_viewer_is_403(
    app: FastAPI, database: Database, admin_client: httpx.AsyncClient
) -> None:
    kb_id = await _new_kb(admin_client)
    await make_user(database, "viewer@example.com", role="viewer")

    async with await login(app, "viewer@example.com") as viewer:
        response = await viewer.post(
            f"/v1/knowledge-bases/{kb_id}/documents/import", json={"url": "https://example.test/policy.md"}
        )

    assert response.status_code == 403, response.text


async def test_import_document_is_audited_with_client_attribution(
    app: FastAPI, database: Database, admin_client: httpx.AsyncClient
) -> None:
    kb_id = await _new_kb(admin_client)
    key_id, raw = await make_api_key(database, ["agents:write"], kind="agent", client="claude-code")
    header = "lkap-mcp/0.1; client=claude-code; tool=kb_add_document; call=c1"

    with respx.mock:
        respx.get("https://example.test/policy.md").respond(
            200, content=b"covered", headers={"content-type": "text/markdown"}
        )
        async with client_for(app, _bearer(raw, **{"X-LKAP-Client": header})) as agent:
            response = await agent.post(
                f"/v1/knowledge-bases/{kb_id}/documents/import",
                json={"url": "https://example.test/policy.md"},
            )

    assert response.status_code == 201, response.text
    [row] = [r for r in await _audit_rows(admin_client) if r["actor_id"] == key_id]
    assert row["action"] == "POST /v1/knowledge-bases/{kb_id}/documents/import"
    assert row["target_id"] == kb_id
    assert row["payload"]["client"]["tool"] == "kb_add_document"


# --------------------------------------------------------------------------- migration v3_001
@pytest.fixture
def _no_livekit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "LKAP_MASTER_KEY"):
        monkeypatch.delenv(name, raising=False)


def _migrate(database: Path, target: str, *, downgrade: bool = False) -> None:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    config.attributes["configure_logger"] = False
    url = f"sqlite+aiosqlite:///{database}"
    config.set_main_option("sqlalchemy.url", url)
    os.environ["LKAP_DATABASE_URL"] = url
    try:
        (command.downgrade if downgrade else command.upgrade)(config, target)
    finally:
        os.environ.pop("LKAP_DATABASE_URL", None)


def _sql(database: Path, statement: str, *params: object) -> list[tuple[Any, ...]]:
    connection = sqlite3.connect(database)
    try:
        rows = connection.execute(statement, params).fetchall()
        connection.commit()
        return rows
    finally:
        connection.close()


def _api_key_columns(database: Path) -> set[str]:
    return {row[1] for row in _sql(database, "PRAGMA table_info(api_keys)")}


_INSERT_KEY = (
    "INSERT INTO api_keys (id, workspace_id, name, prefix, key_hash, scopes, created_at) "
    "VALUES (?, 'default', 'old key', 'lkap_abc', ?, '[\"agents:read\"]', '2026-09-01 00:00:00')"
)


@pytest.mark.usefixtures("_no_livekit_env")
def test_migration_v3_001_upgrade_downgrade_upgrade_keeps_keys(tmp_path: Path) -> None:
    database = tmp_path / "lkap.db"
    _migrate(database, "v2_011_recording_error")
    _sql(database, _INSERT_KEY, "k1", "h" * 64)
    assert not {"kind", "client", "last_client"} & _api_key_columns(database)

    _migrate(database, "v3_001_agent_keys")
    assert {"kind", "client", "last_client"} <= _api_key_columns(database)
    assert _sql(database, "SELECT id, kind, client, last_client FROM api_keys") == [
        ("k1", "standard", None, None)
    ]
    assert ("ix_api_keys_workspace",) in {(row[1],) for row in _sql(database, "PRAGMA index_list(api_keys)")}
    with pytest.raises(sqlite3.IntegrityError, match="kind_valid"):
        _sql(database, "UPDATE api_keys SET kind = 'robot' WHERE id = 'k1'")
    _sql(database, "UPDATE api_keys SET kind = 'agent', client = 'claude-code' WHERE id = 'k1'")

    _migrate(database, "v2_011_recording_error", downgrade=True)
    assert not {"kind", "client", "last_client"} & _api_key_columns(database)
    assert _sql(database, "SELECT id, name, key_hash FROM api_keys") == [("k1", "old key", "h" * 64)]
    assert ("ix_api_keys_workspace",) in {(row[1],) for row in _sql(database, "PRAGMA index_list(api_keys)")}

    _migrate(database, "v3_001_agent_keys")
    assert _sql(database, "SELECT kind FROM api_keys") == [("standard",)]
    assert _sql(database, "SELECT version_num FROM alembic_version") == [("v3_001_agent_keys",)]
