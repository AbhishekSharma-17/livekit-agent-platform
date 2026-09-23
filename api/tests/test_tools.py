"""Declarative tool CRUD, templating and the HTTP dry run."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from conftest import captured_text, create_agent
from fastapi import FastAPI

from lkap_api.config_service import host_allowed, render_arguments, substitute_secrets
from lkap_api.routers.tools import extract_pointer

TOOL_SECRET = "tool-bearer-token-55aa"

HTTP_DEFINITION: dict[str, Any] = {
    "kind": "http",
    "name": "get_weather",
    "description": "Current weather for a city",
    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
    "method": "GET",
    "url": "https://api.example.com/weather/{{ city }}",
    "headers": {"Authorization": "Bearer {{ secret.WEATHER_KEY }}"},
    "allowed_hosts": ["api.example.com"],
    "result_path": "/summary",
}


async def _default_credential(admin_client: httpx.AsyncClient) -> str:
    """A `http-tool-secret` credential whose bag covers `HTTP_DEFINITION`'s `WEATHER_KEY`."""
    response = await admin_client.post(
        "/v1/credentials",
        json={"provider_id": "http-tool-secret", "label": "Weather", "secrets": {"WEATHER_KEY": TOOL_SECRET}},
    )
    assert response.status_code == 201, response.text
    credential_id: str = response.json()["id"]
    return credential_id


async def _create_tool(admin_client: httpx.AsyncClient, **overrides: Any) -> dict[str, Any]:
    definition = {**HTTP_DEFINITION, **overrides.pop("definition", {})}
    # F-15: a `{{ secret.NAME }}` placeholder needs a credential to resolve against;
    # `HTTP_DEFINITION`'s default header references one, so attach a matching
    # credential unless the caller already set (or deliberately omitted/broke) one.
    if definition.get("credential_id") is None and "{{ secret." in json.dumps(definition):
        definition["credential_id"] = await _default_credential(admin_client)
    payload: dict[str, Any] = {
        "kind": definition["kind"],
        "name": definition["name"],
        "definition": definition,
    }
    payload.update(overrides)
    response = await admin_client.post("/v1/tools", json=payload)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


# ------------------------------------------------------------------------- templating
def test_secret_substitution_replaces_only_secret_placeholders() -> None:
    rendered = substitute_secrets("Bearer {{ secret.KEY }} for {{ city }}", {"KEY": "abc", "city": "nope"})

    assert rendered == "Bearer abc for {{ city }}"


def test_unknown_secrets_render_empty() -> None:
    assert substitute_secrets("{{ secret.MISSING }}", {}) == ""


@pytest.mark.parametrize(
    ("template", "arguments", "url_encode", "expected"),
    [
        ("/x/{{ city }}", {"city": "San Francisco"}, True, "/x/San%20Francisco"),
        ("/x/{{ city }}", {"city": "a/b"}, True, "/x/a%2Fb"),
        ('{"q": "{{ city }}"}', {"city": "a b"}, False, '{"q": "a b"}'),
        ("/x/{{ missing }}", {}, True, "/x/"),
        ("/x/{{ n }}", {"n": 7}, True, "/x/7"),
    ],
)
def test_argument_rendering(
    template: str, arguments: dict[str, Any], url_encode: bool, expected: str
) -> None:
    assert render_arguments(template, arguments, url_encode=url_encode) == expected


@pytest.mark.parametrize(
    ("url", "allowed", "platform", "expected"),
    [
        ("https://api.example.com/x", ["api.example.com"], [], True),
        ("https://evil.example.com/x", ["api.example.com"], [], False),
        # Both allowlists empty: fail closed (F-05) — matches the worker's
        # _http_safety.check_url_allowed, which also refuses an empty union.
        ("https://api.example.com/x", [], [], False),
        ("https://api.example.com/x", [], ["other.com"], False),
        ("https://api.example.com/x", [], ["api.example.com"], True),
        ("not-a-url", ["api.example.com"], [], False),
    ],
)
def test_host_allowlist(url: str, allowed: list[str], platform: list[str], expected: bool) -> None:
    assert host_allowed(url, allowed_hosts=allowed, platform_hosts=platform) is expected


@pytest.mark.parametrize(
    ("pointer", "expected"),
    [("/summary", "all good"), ("/data/0/id", "model-a"), ("/missing", None), ("", {"a": 1})],
)
def test_json_pointer_extraction(pointer: str, expected: Any) -> None:
    payload = {"summary": "all good", "data": [{"id": "model-a"}]}
    assert extract_pointer(payload if pointer else {"a": 1}, pointer) == expected


# ------------------------------------------------------------------------------- CRUD
async def test_create_and_read_an_http_tool(admin_client: httpx.AsyncClient) -> None:
    tool = await _create_tool(admin_client)

    fetched = (await admin_client.get(f"/v1/tools/{tool['id']}")).json()
    assert fetched["kind"] == "http"
    assert fetched["definition"]["url"] == HTTP_DEFINITION["url"]
    assert fetched["definition"]["headers"]["Authorization"] == "Bearer {{ secret.WEATHER_KEY }}"


async def test_create_an_mcp_server(admin_client: httpx.AsyncClient) -> None:
    tool = await _create_tool(
        admin_client,
        definition={"kind": "mcp", "name": "docs", "url": "https://mcp.example.com", "headers": {}},
    )

    assert tool["kind"] == "mcp"
    assert tool["definition"]["timeout_s"] == 5


async def test_kind_must_match_the_definition(admin_client: httpx.AsyncClient) -> None:
    response = await admin_client.post(
        "/v1/tools", json={"kind": "mcp", "name": "x", "definition": HTTP_DEFINITION}
    )

    assert response.status_code == 422


async def test_unknown_agent_or_credential_is_rejected(admin_client: httpx.AsyncClient) -> None:
    bad_agent = await admin_client.post(
        "/v1/tools",
        json={"kind": "http", "name": "x", "agent_id": "ghost", "definition": HTTP_DEFINITION},
    )
    bad_credential = await admin_client.post(
        "/v1/tools",
        json={
            "kind": "http",
            "name": "x",
            "definition": {**HTTP_DEFINITION, "credential_id": "ghost"},
        },
    )

    assert bad_agent.status_code == 422
    assert bad_credential.status_code == 422


# ------------------------------------------------------------------------------ F-15
async def test_a_secret_placeholder_with_no_credential_id_is_rejected(
    admin_client: httpx.AsyncClient,
) -> None:
    """F-15: `{{ secret.NAME }}` needs a `credential_id` to resolve against."""
    response = await admin_client.post(
        "/v1/tools",
        json={"kind": "http", "name": "x", "definition": {**HTTP_DEFINITION, "credential_id": None}},
    )

    assert response.status_code == 422
    assert "WEATHER_KEY" in response.json()["error"]["message"]


async def test_an_unknown_secret_name_is_rejected(admin_client: httpx.AsyncClient) -> None:
    """F-15: `{{ secret.MISSING }}` → 422 when the credential's bag has no such key."""
    credential = (
        await admin_client.post(
            "/v1/credentials",
            json={"provider_id": "http-tool-secret", "label": "Weather", "secrets": {"OTHER_KEY": "x"}},
        )
    ).json()

    response = await admin_client.post(
        "/v1/tools",
        json={
            "kind": "http",
            "name": "x",
            "definition": {**HTTP_DEFINITION, "credential_id": credential["id"]},
        },
    )

    assert response.status_code == 422
    body = response.json()
    assert "WEATHER_KEY" in body["error"]["message"]
    assert "x" not in body["error"]["message"], "the secret value itself must never be echoed"


async def test_a_resolvable_secret_placeholder_is_accepted(admin_client: httpx.AsyncClient) -> None:
    credential = (
        await admin_client.post(
            "/v1/credentials",
            json={"provider_id": "http-tool-secret", "label": "Weather", "secrets": {"WEATHER_KEY": "k"}},
        )
    ).json()

    response = await admin_client.post(
        "/v1/tools",
        json={
            "kind": "http",
            "name": "x",
            "definition": {**HTTP_DEFINITION, "credential_id": credential["id"]},
        },
    )

    assert response.status_code == 201, response.text


async def test_a_definition_with_no_secret_placeholder_needs_no_credential(
    admin_client: httpx.AsyncClient,
) -> None:
    response = await admin_client.post(
        "/v1/tools",
        json={"kind": "http", "name": "x", "definition": {**HTTP_DEFINITION, "headers": {}}},
    )

    assert response.status_code == 201, response.text


async def test_update_also_enforces_f15(admin_client: httpx.AsyncClient) -> None:
    tool = await _create_tool(admin_client, definition={"headers": {}})  # no placeholder, no credential

    response = await admin_client.put(
        f"/v1/tools/{tool['id']}",
        json={
            "kind": "http",
            "name": tool["name"],
            "definition": {**HTTP_DEFINITION, "credential_id": None},
        },
    )

    assert response.status_code == 422


async def test_list_filters_by_agent_and_kind(admin_client: httpx.AsyncClient) -> None:
    agent = await create_agent(admin_client, published=False)
    await _create_tool(admin_client)
    await _create_tool(admin_client, agent_id=agent["id"])

    by_agent = (await admin_client.get("/v1/tools", params={"agent_id": agent["id"]})).json()
    by_kind = (await admin_client.get("/v1/tools", params={"kind": "mcp"})).json()

    assert by_agent["total"] == 1
    assert by_kind["total"] == 0


async def test_update_replaces_the_definition(admin_client: httpx.AsyncClient) -> None:
    tool = await _create_tool(admin_client)

    response = await admin_client.put(
        f"/v1/tools/{tool['id']}",
        json={
            "kind": "http",
            "name": "get_weather",
            "enabled": False,
            # Keep the credential `_create_tool` attached (F-15: the header's
            # `{{ secret.WEATHER_KEY }}` placeholder needs one to resolve against).
            "definition": {**tool["definition"], "method": "POST"},
        },
    )

    assert response.json()["definition"]["method"] == "POST"
    assert response.json()["enabled"] is False


async def test_delete_removes_the_tool(admin_client: httpx.AsyncClient) -> None:
    tool = await _create_tool(admin_client)

    assert (await admin_client.delete(f"/v1/tools/{tool['id']}")).status_code == 204
    assert (await admin_client.get(f"/v1/tools/{tool['id']}")).status_code == 404


async def test_tools_require_an_admin_token(client: httpx.AsyncClient) -> None:
    assert (await client.get("/v1/tools")).status_code == 401


# ---------------------------------------------------------------------------- dry run
async def test_dry_run_renders_the_request_and_extracts_the_pointer(
    admin_client: httpx.AsyncClient, mock_http: list[httpx.Request]
) -> None:
    credential = (
        await admin_client.post(
            "/v1/credentials",
            json={
                "provider_id": "http-tool-secret",
                "label": "Weather",
                "secrets": {"WEATHER_KEY": TOOL_SECRET},
            },
        )
    ).json()
    tool = await _create_tool(admin_client, definition={"credential_id": credential["id"]})

    response = await admin_client.post(
        f"/v1/tools/{tool['id']}/dry-run", json={"arguments": {"city": "San Francisco"}}
    )

    body = response.json()
    assert response.status_code == 200, response.text
    assert body["ok"] is True
    assert body["status_code"] == 200
    assert body["result"] == "all good"
    assert body["duration_ms"] >= 0
    assert str(mock_http[0].url) == "https://api.example.com/weather/San%20Francisco"
    assert mock_http[0].headers["Authorization"] == f"Bearer {TOOL_SECRET}"


async def test_dry_run_never_echoes_the_rendered_credential(
    admin_client: httpx.AsyncClient,
    mock_http: list[httpx.Request],
    log_capture: pytest.LogCaptureFixture,
) -> None:
    credential = (
        await admin_client.post(
            "/v1/credentials",
            json={
                "provider_id": "http-tool-secret",
                "label": "Weather",
                "secrets": {"WEATHER_KEY": TOOL_SECRET},
            },
        )
    ).json()
    tool = await _create_tool(admin_client, definition={"credential_id": credential["id"]})

    response = await admin_client.post(f"/v1/tools/{tool['id']}/dry-run", json={"arguments": {}})

    assert TOOL_SECRET not in response.text
    assert TOOL_SECRET not in captured_text(log_capture)


async def test_dry_run_rejects_a_host_outside_the_allowlist(
    admin_client: httpx.AsyncClient, mock_http: list[httpx.Request]
) -> None:
    tool = await _create_tool(admin_client, definition={"url": "https://evil.example.com/x", "headers": {}})

    response = await admin_client.post(f"/v1/tools/{tool['id']}/dry-run", json={"arguments": {}})

    assert response.status_code == 400
    assert mock_http == []


async def test_dry_run_refuses_an_empty_allowlist_with_a_clear_message(
    admin_client: httpx.AsyncClient, mock_http: list[httpx.Request]
) -> None:
    """F-05: an empty allowed_hosts must fail the dry run (fail closed) with a
    message telling the author to add the host, instead of silently passing
    and only failing later at live call time."""
    tool = await _create_tool(admin_client, definition={"allowed_hosts": [], "headers": {}})

    response = await admin_client.post(f"/v1/tools/{tool['id']}/dry-run", json={"arguments": {}})

    assert response.status_code == 400
    body = response.json()
    assert "allowed_hosts" in body["error"]["message"]
    assert "add it" in body["error"]["message"]
    assert mock_http == []


async def test_dry_run_is_http_only(admin_client: httpx.AsyncClient, mock_http: list[httpx.Request]) -> None:
    tool = await _create_tool(
        admin_client,
        definition={"kind": "mcp", "name": "docs", "url": "https://mcp.example.com", "headers": {}},
    )

    response = await admin_client.post(f"/v1/tools/{tool['id']}/dry-run", json={"arguments": {}})

    assert response.status_code == 400
    assert "http tools" in response.json()["error"]["message"]


async def test_dry_run_truncates_long_results(admin_client: httpx.AsyncClient, app: FastAPI) -> None:
    from lkap_api.deps import get_http_client

    async def override() -> Any:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="x" * 500)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as mocked:
            yield mocked

    app.dependency_overrides[get_http_client] = override
    tool = await _create_tool(
        admin_client, definition={"result_path": None, "max_result_chars": 20, "headers": {}}
    )

    response = await admin_client.post(f"/v1/tools/{tool['id']}/dry-run", json={"arguments": {}})

    assert response.json()["result"] == "x" * 20
    app.dependency_overrides.pop(get_http_client, None)


async def test_dry_run_reports_transport_failures(admin_client: httpx.AsyncClient, app: FastAPI) -> None:
    from lkap_api.deps import get_http_client

    async def override() -> Any:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to host", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as mocked:
            yield mocked

    app.dependency_overrides[get_http_client] = override
    tool = await _create_tool(admin_client, definition={"headers": {}})

    response = await admin_client.post(f"/v1/tools/{tool['id']}/dry-run", json={"arguments": {}})

    body = response.json()
    assert body["ok"] is False
    assert body["status_code"] is None
    assert "ConnectError" in body["result"]
    app.dependency_overrides.pop(get_http_client, None)
