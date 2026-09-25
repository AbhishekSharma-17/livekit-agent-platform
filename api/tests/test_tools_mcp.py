"""V5-09: MCP definitions — the auth union, the host policy, the test connection, the snapshot.

The MCP server is faked with ``httpx.MockTransport`` behind the ``get_http_client``
override (the same shape as the worker's ``test_mcp_guard.py`` fake): no network.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

import httpx
import pytest
from conftest import create_agent, inference_config
from fastapi import FastAPI
from lkap_contracts.agent_config import ResolvedAgentConfig
from lkap_contracts.tools import McpHeaderAuth, McpServerDefinition
from sqlalchemy import select

from lkap_api import net_guard
from lkap_api.db.models import Tool
from lkap_api.db.session import Database
from lkap_api.mcp_test import MAX_TOOLS
from lkap_api.settings import Settings

MCP_URL = "https://mcp.example.com/mcp"
TOOL_SECRET = "mcp-api-key-7e11"

Handler = Callable[[httpx.Request], httpx.Response]


# ---------------------------------------------------------------------------- helpers
async def _credential(client: httpx.AsyncClient, secrets: dict[str, str]) -> str:
    response = await client.post(
        "/v1/credentials", json={"provider_id": "http-tool-secret", "label": "MCP key", "secrets": secrets}
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _payload(definition: dict[str, Any]) -> dict[str, Any]:
    body = {"kind": "mcp", "name": "crm", "url": MCP_URL, **definition}
    return {"kind": "mcp", "name": body["name"], "definition": body}


async def _create(client: httpx.AsyncClient, definition: dict[str, Any] | None = None) -> httpx.Response:
    return await client.post("/v1/tools", json=_payload(definition or {}))


async def _insert_row(database: Database, definition: dict[str, Any]) -> str:
    """A tool row written as an older release (or another writer) left it, bypassing the save checks."""
    async with database.session() as session:
        row = Tool(kind="mcp", name=definition["name"], definition=definition, enabled=True)
        session.add(row)
        await session.flush()
        return row.id


async def _stored(database: Database, tool_id: str) -> dict[str, Any]:
    async with database.session() as session:
        row = (await session.execute(select(Tool).where(Tool.id == tool_id))).scalar_one()
        stored: dict[str, Any] = dict(row.definition)
        return stored


class FakeMcpServer:
    """A streamable-HTTP MCP server: JSON or event-stream answers, sessions, a cursor."""

    def __init__(
        self,
        *,
        tools: list[dict[str, Any]] | None = None,
        sse: bool = False,
        status: int | None = None,
        redirect_to: str | None = None,
        pages: int = 1,
    ) -> None:
        self.tools = (
            tools
            if tools is not None
            else [
                {
                    "name": "lookup_policy",
                    "description": "Look up a policy",
                    "inputSchema": {"type": "object"},
                },
                {"name": "open_claim", "description": "Open a claim"},
                {"name": "claim_status"},
            ]
        )
        self.sse = sse
        self.status = status
        self.redirect_to = redirect_to
        self.pages = pages
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host != "mcp.example.com":
            return httpx.Response(200, json={"reached": request.url.host})
        if self.redirect_to is not None:
            return httpx.Response(302, headers={"Location": self.redirect_to})
        if self.status is not None:
            return httpx.Response(self.status)
        if request.method == "DELETE":
            return httpx.Response(200)
        message = json.loads(request.content)
        if "id" not in message:
            return httpx.Response(202)
        result: dict[str, Any]
        if message["method"] == "initialize":
            result = {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake", "version": "1"},
            }
        elif message["method"] == "tools/list":
            cursor = (message.get("params") or {}).get("cursor")
            page = int(cursor) if cursor else 0
            size = max(1, len(self.tools) // self.pages)
            chunk = (
                self.tools[page * size : (page + 1) * size]
                if page < self.pages - 1
                else self.tools[page * size :]
            )
            result = {"tools": chunk}
            if page < self.pages - 1:
                result["nextCursor"] = str(page + 1)
        else:
            result = {}
        body = {"jsonrpc": "2.0", "id": message["id"], "result": result}
        headers = {"Mcp-Session-Id": "session-1"}
        if self.sse:
            text = f"event: message\ndata: {json.dumps(body)}\n\n"
            return httpx.Response(200, text=text, headers={**headers, "Content-Type": "text/event-stream"})
        return httpx.Response(200, json=body, headers=headers)


@pytest.fixture
def mcp_server(app: FastAPI) -> Iterator[Callable[..., FakeMcpServer]]:
    """Install a fake MCP server as the outbound client; returns a factory."""
    from lkap_api.deps import get_http_client

    def install(**kwargs: Any) -> FakeMcpServer:
        server = FakeMcpServer(**kwargs)

        async def override() -> AsyncIterator[httpx.AsyncClient]:
            async with httpx.AsyncClient(transport=httpx.MockTransport(server.handle)) as mocked:
                yield mocked

        app.dependency_overrides[get_http_client] = override
        return server

    yield install
    app.dependency_overrides.pop(get_http_client, None)


# ------------------------------------------------------------------- the auth union
async def test_a_stored_headers_row_loads_as_header_auth_and_resaves_in_the_new_shape(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    credential_id = await _credential(admin_client, {"KEY": TOOL_SECRET})
    legacy = {
        "kind": "mcp",
        "name": "crm",
        "url": MCP_URL,
        "headers": {"x-api-key": "{{ secret.KEY }}"},
        "credential_id": credential_id,
        "allowed_tools": None,
        "timeout_s": 5,
        "sse_read_timeout_s": 300,
        "tool_options": {},
    }
    tool_id = await _insert_row(database, legacy)

    fetched = (await admin_client.get(f"/v1/tools/{tool_id}")).json()
    assert fetched["definition"]["auth"] == {
        "kind": "header",
        "headers": {"x-api-key": "{{ secret.KEY }}"},
        "credential_id": credential_id,
    }

    response = await admin_client.put(
        f"/v1/tools/{tool_id}", json={"kind": "mcp", "name": "crm", "definition": legacy}
    )
    assert response.status_code == 200, response.text
    stored = await _stored(database, tool_id)
    assert stored["auth"]["kind"] == "header"
    assert stored["auth"]["credential_id"] == credential_id
    assert McpServerDefinition.model_validate(stored).auth == McpHeaderAuth(
        headers={"x-api-key": "{{ secret.KEY }}"}, credential_id=credential_id
    )


async def test_header_auth_binds_an_http_tool_secret(admin_client: httpx.AsyncClient) -> None:
    credential_id = await _credential(admin_client, {"KEY": TOOL_SECRET})

    response = await _create(
        admin_client,
        {
            "auth": {
                "kind": "header",
                "headers": {"Authorization": "Bearer {{ secret.KEY }}"},
                "credential_id": credential_id,
            }
        },
    )

    assert response.status_code == 201, response.text
    definition = response.json()["definition"]
    assert definition["auth"]["credential_id"] == credential_id
    assert definition["credential_id"] == credential_id  # the deprecated mirror


async def test_header_auth_with_an_unknown_secret_name_is_refused(admin_client: httpx.AsyncClient) -> None:
    credential_id = await _credential(admin_client, {"KEY": TOOL_SECRET})

    response = await _create(
        admin_client,
        {
            "auth": {
                "kind": "header",
                "headers": {"x-api-key": "{{ secret.OTHER }}"},
                "credential_id": credential_id,
            }
        },
    )

    assert response.status_code == 422
    assert "OTHER" in response.json()["error"]["message"]


async def test_oauth_auth_is_refused_until_sign_in_ships(admin_client: httpx.AsyncClient) -> None:
    response = await _create(admin_client, {"auth": {"kind": "oauth"}})

    assert response.status_code == 422
    assert response.json()["error"]["details"]["reason"] == "oauth_not_available"


async def test_legacy_and_auth_fields_that_disagree_are_refused(admin_client: httpx.AsyncClient) -> None:
    response = await _create(
        admin_client,
        {"auth": {"kind": "header", "headers": {"x-api-key": "a"}}, "headers": {"x-api-key": "b"}},
    )

    assert response.status_code == 422


# ------------------------------------------------------------------- the host policy
@pytest.mark.parametrize(
    "url",
    [
        "https://10.0.0.5/mcp",
        "https://169.254.169.254/mcp",
        "https://metadata.google.internal/mcp",
        "https://2130706433/mcp",
        "ftp://mcp.example.com/mcp",
    ],
)
async def test_a_private_mcp_url_is_refused_at_save(admin_client: httpx.AsyncClient, url: str) -> None:
    response = await _create(admin_client, {"url": url})

    assert response.status_code == 422
    assert response.json()["error"]["details"]["reason"] == "blocked_destination"


async def test_plain_http_is_refused_for_a_public_host(admin_client: httpx.AsyncClient) -> None:
    response = await _create(admin_client, {"url": "http://mcp.example.com/mcp"})

    assert response.status_code == 422
    assert "https" in response.json()["error"]["message"]


async def test_plain_http_to_loopback_is_allowed_in_dev(admin_client: httpx.AsyncClient) -> None:
    assert (await _create(admin_client, {"url": "http://127.0.0.1:8931/mcp"})).status_code == 201


@pytest.mark.parametrize("allow_private", [None, "127.0.0.1"])
def test_plain_http_to_loopback_is_refused_outside_dev(settings: Settings, allow_private: str | None) -> None:
    """In prod even an operator-exempted loopback host needs https."""
    policy = net_guard.mcp_policy(
        settings.model_copy(update={"env": "prod", "net_allow_private_hosts": allow_private})
    )

    assert policy.problem("http://127.0.0.1:8931/mcp") is not None
    if allow_private:
        assert policy.problem("https://127.0.0.1:8931/mcp") is None


async def test_an_empty_mcp_allowlist_lets_any_public_https_host_through(
    admin_client: httpx.AsyncClient,
) -> None:
    assert (await _create(admin_client, {"url": "https://mcp.vendor.example/mcp"})).status_code == 201


async def test_a_set_mcp_allowlist_is_a_ceiling(
    admin_client: httpx.AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "mcp_allowed_hosts", "mcp.example.com, other.example.com")

    assert (await _create(admin_client)).status_code == 201
    refused = await _create(admin_client, {"url": "https://mcp.vendor.example/mcp"})
    assert refused.status_code == 422
    assert "LKAP_MCP_ALLOWED_HOSTS" in refused.json()["error"]["message"]


@pytest.mark.parametrize(
    ("raw", "http_list", "url", "allowed"),
    [
        ("", "", "https://mcp.example.com/mcp", True),
        ("mcp.example.com", "", "https://mcp.example.com/mcp", True),
        ("mcp.example.com", "", "https://evil.example.com/mcp", False),
        ("MCP.Example.com ,", "", "https://mcp.example.com/mcp", True),
        ("@http", "", "https://mcp.example.com/mcp", False),
        ("@http", "mcp.example.com", "https://mcp.example.com/mcp", True),
        ("@http", "mcp.example.com", "https://evil.example.com/mcp", False),
        ("", "", "http://mcp.example.com/mcp", False),
        ("", "", "https://10.1.2.3/mcp", False),
    ],
)
def test_mcp_policy(settings: Settings, raw: str, http_list: str, url: str, allowed: bool) -> None:
    policy = net_guard.mcp_policy(
        settings.model_copy(update={"mcp_allowed_hosts": raw, "http_tool_allowed_hosts": http_list})
    )

    assert (policy.problem(url) is None) is allowed


def test_mcp_policy_problem_never_echoes_the_path_or_query(settings: Settings) -> None:
    problem = net_guard.mcp_policy(settings).problem("http://mcp.example.com/mcp?key=secret-value")

    assert problem is not None and "secret-value" not in problem


# ------------------------------------------------------------------- the test route
async def test_the_test_route_lists_and_stores_the_tools(
    admin_client: httpx.AsyncClient, database: Database, mcp_server: Callable[..., FakeMcpServer]
) -> None:
    server = mcp_server()
    credential_id = await _credential(admin_client, {"KEY": TOOL_SECRET})
    tool = (
        await _create(
            admin_client,
            {
                "auth": {
                    "kind": "header",
                    "headers": {"x-api-key": "{{ secret.KEY }}"},
                    "credential_id": credential_id,
                }
            },
        )
    ).json()

    response = await admin_client.post(f"/v1/tools/{tool['id']}/test")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["tool_names"] == ["lookup_policy", "open_claim", "claim_status"]
    assert body["tool_count"] == 3
    assert body["cached_at"] is not None
    assert TOOL_SECRET not in response.text
    # The stored auth went out with the secret substituted, on every request.
    posts = [r for r in server.requests if r.method == "POST"]
    assert [json.loads(r.content)["method"] for r in posts] == [
        "initialize",
        "notifications/initialized",
        "tools/list",
    ]
    assert all(r.headers["x-api-key"] == TOOL_SECRET for r in server.requests)
    assert posts[-1].headers["mcp-session-id"] == "session-1"
    assert posts[-1].headers["mcp-protocol-version"] == "2025-03-26"
    assert server.requests[-1].method == "DELETE"

    stored = await _stored(database, tool["id"])
    assert [t["name"] for t in stored["cached_tools"]] == ["lookup_policy", "open_claim", "claim_status"]
    assert stored["cached_tools"][0]["input_schema"] == {"type": "object"}
    fetched = (await admin_client.get(f"/v1/tools/{tool['id']}")).json()
    assert len(fetched["definition"]["cached_tools"]) == 3


async def test_the_test_route_reads_event_stream_answers(
    admin_client: httpx.AsyncClient, mcp_server: Callable[..., FakeMcpServer]
) -> None:
    mcp_server(sse=True)
    tool = (await _create(admin_client)).json()

    body = (await admin_client.post(f"/v1/tools/{tool['id']}/test")).json()

    assert body["ok"] is True
    assert body["tool_count"] == 3


async def test_the_test_route_follows_the_cursor_and_caps_the_snapshot(
    admin_client: httpx.AsyncClient, mcp_server: Callable[..., FakeMcpServer]
) -> None:
    tools = [{"name": f"tool_{i}", "description": "x" * 5000} for i in range(MAX_TOOLS + 20)]
    mcp_server(tools=tools, pages=2)
    tool = (await _create(admin_client)).json()

    body = (await admin_client.post(f"/v1/tools/{tool['id']}/test")).json()

    assert body["tool_count"] == MAX_TOOLS
    fetched = (await admin_client.get(f"/v1/tools/{tool['id']}")).json()
    assert all(len(t["description"]) <= 1000 for t in fetched["definition"]["cached_tools"])


@pytest.mark.parametrize(
    ("status", "reason"), [(401, "needs_auth"), (403, "needs_auth"), (500, "http_error")]
)
async def test_the_test_route_reports_http_failures(
    admin_client: httpx.AsyncClient, mcp_server: Callable[..., FakeMcpServer], status: int, reason: str
) -> None:
    mcp_server(status=status)
    tool = (await _create(admin_client)).json()

    body = (await admin_client.post(f"/v1/tools/{tool['id']}/test")).json()

    assert body["ok"] is False
    assert body["reason"] == reason
    assert body["tool_names"] == []


async def test_the_test_route_never_follows_a_redirect(
    admin_client: httpx.AsyncClient, mcp_server: Callable[..., FakeMcpServer]
) -> None:
    server = mcp_server(redirect_to="http://169.254.169.254/latest/meta-data/")
    tool = (await _create(admin_client)).json()

    body = (await admin_client.post(f"/v1/tools/{tool['id']}/test")).json()

    assert body["ok"] is False
    assert body["reason"] == "http_error"
    assert {r.url.host for r in server.requests} == {"mcp.example.com"}


async def test_the_test_route_reports_a_connect_failure(
    admin_client: httpx.AsyncClient, app: FastAPI
) -> None:
    from lkap_api.deps import get_http_client

    async def override() -> AsyncIterator[httpx.AsyncClient]:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to host", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as mocked:
            yield mocked

    app.dependency_overrides[get_http_client] = override
    tool = (await _create(admin_client)).json()

    body = (await admin_client.post(f"/v1/tools/{tool['id']}/test")).json()

    assert body == {**body, "ok": False, "reason": "unreachable"}
    app.dependency_overrides.pop(get_http_client, None)


async def test_the_test_route_blocks_a_private_address_at_connect_time(
    admin_client: httpx.AsyncClient, app: FastAPI
) -> None:
    """The real guarded client: a public-looking name that resolves inward is refused after DNS."""
    from lkap_api.deps import get_http_client

    async def resolve(_host: str, _port: int) -> list[Any]:
        import ipaddress

        return [ipaddress.ip_address("10.0.0.7")]

    async def override() -> AsyncIterator[httpx.AsyncClient]:
        transport = net_guard.GuardedTransport(net_guard.NetPolicy(), resolve=resolve)
        async with httpx.AsyncClient(transport=transport, follow_redirects=False) as guarded:
            yield guarded

    app.dependency_overrides[get_http_client] = override
    tool = (await _create(admin_client)).json()

    body = (await admin_client.post(f"/v1/tools/{tool['id']}/test")).json()

    assert body["ok"] is False
    assert body["reason"] == "blocked_destination"
    app.dependency_overrides.pop(get_http_client, None)


async def test_the_test_route_refuses_a_stored_private_url_before_connecting(
    admin_client: httpx.AsyncClient, database: Database, mcp_server: Callable[..., FakeMcpServer]
) -> None:
    server = mcp_server()
    tool_id = await _insert_row(database, {"kind": "mcp", "name": "old", "url": "https://10.0.0.5/mcp"})

    response = await admin_client.post(f"/v1/tools/{tool_id}/test")

    assert response.status_code == 422
    assert server.requests == []


async def test_the_test_route_refuses_a_stored_oauth_row(
    admin_client: httpx.AsyncClient, database: Database, mcp_server: Callable[..., FakeMcpServer]
) -> None:
    server = mcp_server()
    tool_id = await _insert_row(
        database, {"kind": "mcp", "name": "old", "url": MCP_URL, "auth": {"kind": "oauth"}}
    )

    response = await admin_client.post(f"/v1/tools/{tool_id}/test")

    assert response.status_code == 422
    assert response.json()["error"]["details"]["reason"] == "oauth_not_available"
    assert server.requests == []


async def test_the_test_route_is_mcp_only(admin_client: httpx.AsyncClient) -> None:
    tool = (
        await admin_client.post(
            "/v1/tools",
            json={
                "kind": "http",
                "name": "get_weather",
                "definition": {
                    "kind": "http",
                    "name": "get_weather",
                    "description": "Weather",
                    "parameters": {"type": "object", "properties": {}},
                    "url": "https://api.example.com/weather",
                    "allowed_hosts": ["api.example.com"],
                },
            },
        )
    ).json()

    response = await admin_client.post(f"/v1/tools/{tool['id']}/test")

    assert response.status_code == 400


async def test_a_save_keeps_the_snapshot_unless_the_url_changes(
    admin_client: httpx.AsyncClient, mcp_server: Callable[..., FakeMcpServer]
) -> None:
    mcp_server()
    tool = (await _create(admin_client)).json()
    await admin_client.post(f"/v1/tools/{tool['id']}/test")

    same = await admin_client.put(f"/v1/tools/{tool['id']}", json=_payload({"timeout_s": 7}))
    assert len(same.json()["definition"]["cached_tools"]) == 3

    moved = await admin_client.put(
        f"/v1/tools/{tool['id']}", json=_payload({"url": "https://mcp2.example.com/mcp"})
    )
    assert moved.json()["definition"]["cached_tools"] is None


async def test_the_worker_gets_resolved_header_auth_without_ids_or_snapshot(
    admin_client: httpx.AsyncClient,
    service_client: httpx.AsyncClient,
    mcp_server: Callable[..., FakeMcpServer],
) -> None:
    mcp_server()
    credential_id = await _credential(admin_client, {"KEY": TOOL_SECRET})
    tool = (
        await _create(
            admin_client,
            {
                "auth": {
                    "kind": "header",
                    "headers": {"x-api-key": "{{ secret.KEY }}"},
                    "credential_id": credential_id,
                }
            },
        )
    ).json()
    await admin_client.post(f"/v1/tools/{tool['id']}/test")
    config = json.loads(inference_config().model_dump_json())
    config["tools"]["tool_ids"] = [tool["id"]]
    agent = await create_agent(admin_client, name="MCP agent", config=config)
    session_id = (await admin_client.post(f"/v1/agents/{agent['id']}/connect", json={})).json()["sessionId"]

    raw = (await service_client.get(f"/internal/v1/sessions/{session_id}/resolved")).json()
    resolved = ResolvedAgentConfig.model_validate(raw)

    (definition,) = resolved.tools
    assert isinstance(definition, McpServerDefinition)
    assert definition.auth == McpHeaderAuth(headers={"x-api-key": TOOL_SECRET}, credential_id=None)
    assert definition.headers == {"x-api-key": TOOL_SECRET}
    assert definition.credential_id is None
    assert definition.cached_tools is None
    assert credential_id not in json.dumps(raw)
