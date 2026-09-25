"""``api_request``: refusals, confirmation, redaction and seen-value scrubbing (§4.11, D-V3-5)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from conftest import OPERATOR_SCOPES, READ_ONLY_SCOPES, dumped

from lkap_mcp.tools.generic import match_route

SECRET = "sk-echo-0123456789abcdef"


@pytest.mark.parametrize(
    ("method", "path", "body", "code"),
    [
        ("GET", "/v1/api-keys", None, "route_refused"),
        ("GET", "/v1/api-keys/self", None, "route_refused"),
        (
            "POST",
            "/v1/credentials",
            {"provider_id": "openai-llm", "label": "x", "secrets": {"api_key": SECRET}},
            "route_refused",
        ),
        ("GET", "/internal/v1/agents/a/config", None, "path_refused"),
        ("GET", "/v1/../internal/v1/x", None, "path_refused"),
        ("GET", "/v1/agents?published=true", None, "path_refused"),
        ("PUT", "/v1/workspaces/00000000000000000000000000000001", {"settings": {}}, "route_refused"),
        ("POST", "/v1/auth/login", {"email": "a@b.c", "password": "p"}, "route_refused"),
        ("POST", "/v1/connections/default/rotate", None, "route_refused"),
        ("POST", "/v1/calls", {"agent_id": "a", "to_e164": "+15550100"}, "route_refused"),
        ("POST", "/v1/telephony/trunks", {}, "route_refused"),
        (
            "POST",
            "/v1/connections",
            {
                "slug": "x",
                "name": "x",
                "url": "wss://p.livekit.cloud",
                "api_key": SECRET,
                "api_secret": "env:X",
            },
            "inline_secret_refused",
        ),
    ],
)
async def test_api_request_refuses_before_sending(
    key: Any, mcp_session: Any, method: str, path: str, body: Any, code: str
) -> None:
    raw = await key(OPERATOR_SCOPES)

    async with mcp_session(raw) as mcp:
        await mcp.tool_names()  # registration (the identity lookup) happens first
        before = len(mcp.transport.requests)
        result = await mcp.call("api_request", method=method, path=path, body=body, confirm=True)
        sent = [r.url.path for r in mcp.transport.requests[before:]]

    assert result["ok"] is False and result["error"]["code"] == code, result
    assert sent == []
    assert SECRET not in dumped(result)


async def test_api_request_get_returns_the_matched_route_and_the_response(key: Any, mcp_session: Any) -> None:
    raw = await key(READ_ONLY_SCOPES)

    async with mcp_session(raw) as mcp:
        result = await mcp.call(
            "api_request", method="GET", path="/v1/analytics/summary", query={"range": "7d"}
        )

    assert result["ok"] is True, result
    assert result["data"]["route"]["path"] == "/v1/analytics/summary"
    assert "sessions" in result["data"]["response"]


async def test_api_request_write_needs_confirm_and_a_read_only_key_cannot_write(
    key: Any, mcp_session: Any
) -> None:
    writer = await key(OPERATOR_SCOPES)
    reader = await key(READ_ONLY_SCOPES)

    async with mcp_session(writer) as mcp:
        unconfirmed = await mcp.call(
            "api_request", method="POST", path="/v1/knowledge-bases", body={"name": "x"}
        )
        planned = await mcp.call(
            "api_request", method="POST", path="/v1/knowledge-bases", body={"name": "x"}, plan=True
        )
        done = await mcp.call(
            "api_request", method="POST", path="/v1/knowledge-bases", body={"name": "x"}, confirm=True
        )
    async with mcp_session(reader) as mcp:
        refused = await mcp.call(
            "api_request", method="POST", path="/v1/knowledge-bases", body={"name": "y"}, confirm=True
        )
        [tool] = [t for t in (await mcp.session.list_tools()).tools if t.name == "api_request"]

    assert unconfirmed["error"]["code"] == "needs_confirmation"
    assert planned["plan"][0]["path"] == "/v1/knowledge-bases"
    assert done["ok"] is True and done["data"]["route"]["operation_id"]
    assert refused["error"]["code"] == "forbidden"
    assert tool.annotations is not None and tool.annotations.readOnlyHint is True


async def test_api_request_redacts_secret_named_keys_in_a_fabricated_response(
    key: Any, mcp_session: Any
) -> None:
    raw = await key(OPERATOR_SCOPES)

    async with mcp_session(raw) as mcp:
        mcp.transport.fabricated[("GET", "/v1/agents/fabricated")] = httpx.Response(
            200,
            json={
                "id": "fabricated",
                "api_secret": "x",
                "nested": {"token": "abc", "secret_prefix": "whsec_1"},
            },
        )
        result = await mcp.call("api_request", method="GET", path="/v1/agents/fabricated")

    response = result["data"]["response"]
    assert response["api_secret"] == "<redacted>"
    assert response["nested"] == {"token": "<redacted>", "secret_prefix": "whsec_1"}


async def test_api_request_scrubs_a_seen_secret_echoed_back_by_the_api(
    key: Any, mcp_session: Any, admin: Any
) -> None:
    raw = await key(OPERATOR_SCOPES)
    created = await admin.post(
        "/v1/knowledge-bases", json={"name": "Echo", "description": f"leaked {SECRET} here"}
    )
    kb_id = created.json()["id"]

    async with mcp_session(raw) as mcp:
        # The value is seen once, as an inline secret the api refuses (unknown provider).
        seen = await mcp.call(
            "provider_key_create",
            provider_id="openai-llm",
            label="k",
            secrets={"api_key": SECRET},
            test=False,
            plan=True,
        )
        echoed = await mcp.call("api_request", method="GET", path=f"/v1/knowledge-bases/{kb_id}")

    assert seen["ok"] is True
    assert echoed["data"]["response"]["description"] == "leaked <redacted> here"
    assert SECRET not in dumped(echoed)


def test_match_route_prefers_literal_segments_over_parameters() -> None:
    document = {
        "paths": {
            "/v1/api-keys/{api_key_id}": {"get": {"operationId": "by_id"}},
            "/v1/api-keys/self": {"get": {"operationId": "self"}},
            "/v1/knowledge-bases/{kb_id}/documents": {"get": {"operationId": "docs"}},
        }
    }

    assert match_route(document, "GET", "/v1/api-keys/self")["operation_id"] == "self"  # type: ignore[index]
    assert match_route(document, "GET", "/v1/knowledge-bases/k1/documents")["operation_id"] == "docs"  # type: ignore[index]
    assert match_route(document, "POST", "/v1/knowledge-bases/k1/documents") is None


@pytest.mark.parametrize(
    ("kind", "id_", "parent_id", "field"),
    [
        ("tool", "", None, "id"),
        ("tool", "   ", None, "id"),
        ("tool", "abc/def", None, "id"),
        ("agent", "../agents", None, "id"),
        ("kb_document", "doc-1", "kb/1", "parent_id"),
        ("kb_document", "", "kb-1", "id"),
    ],
)
async def test_lkap_delete_refuses_an_empty_or_slashed_id_before_sending(
    key: Any, mcp_session: Any, kind: str, id_: str, parent_id: str | None, field: str
) -> None:
    """Ask #103: `lkap_delete(kind="tool", id="")` sent `DELETE /v1/tools/` and reported success."""
    raw = await key(OPERATOR_SCOPES)

    async with mcp_session(raw) as mcp:
        await mcp.tool_names()
        before = len(mcp.transport.requests)
        planned = await mcp.call("lkap_delete", kind=kind, id=id_, parent_id=parent_id, plan=True)
        result = await mcp.call("lkap_delete", kind=kind, id=id_, parent_id=parent_id, confirm=True)
        sent = mcp.transport.requests[before:]

    for answer in (planned, result):
        assert answer["ok"] is False, answer
        assert answer["error"]["code"] == "invalid_input"
        assert answer["error"]["message"].startswith(field)
    assert sent == []
