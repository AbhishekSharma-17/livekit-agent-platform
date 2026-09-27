"""V5-20 knowledge-connection tools: list, create (+ test), update, test, and kb_create's connection_id.

The vendor side is an ``httpx.MockTransport`` installed with the api's own
``runtime.use_transport`` seam: no live call.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from conftest import BUILDER_SCOPES, OPERATOR_SCOPES, READ_ONLY_SCOPES
from lkap_api.knowledge_connections import runtime

TOOLS = {"kb_connection_list", "kb_connection_create", "kb_connection_update", "kb_connection_test"}
QDRANT_URL = "https://qdrant.example.com"


def _qdrant(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/":
        return httpx.Response(200, json={"title": "qdrant", "version": "1.15.0"})
    if request.url.path == "/collections":
        return httpx.Response(200, json={"result": {"collections": [{"name": "other"}]}})
    return httpx.Response(404, json={"status": {"error": "Collection doesn't exist!"}})


@pytest.fixture
def fake_qdrant() -> Iterator[list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _qdrant(request)

    with runtime.use_transport(httpx.MockTransport(handler)):
        yield seen


async def test_reads_for_readers_and_writes_for_operators(key: Any, mcp_session: Any) -> None:
    async with mcp_session(await key(READ_ONLY_SCOPES)) as mcp:
        reader = set(await mcp.tool_names()) & TOOLS
    async with mcp_session(await key(BUILDER_SCOPES)) as mcp:
        builder = set(await mcp.tool_names()) & TOOLS
    async with mcp_session(await key(OPERATOR_SCOPES)) as mcp:
        operator = set(await mcp.tool_names()) & TOOLS

    assert reader == builder == {"kb_connection_list"}
    assert operator == TOOLS


async def test_create_then_test_lists_the_collections(
    key: Any, mcp_session: Any, fake_qdrant: list[httpx.Request]
) -> None:
    async with mcp_session(await key(OPERATOR_SCOPES)) as mcp:
        created = await mcp.call(
            "kb_connection_create", name="Demo — Qdrant", kind="qdrant", settings={"url": QDRANT_URL}
        )
        listed = await mcp.call("kb_connection_list")

    assert created["ok"] is True, created
    connection, test = created["data"]["connection"], created["data"]["test"]
    assert connection["kind"] == "qdrant" and connection["settings"]["collection"] == "lkap_knowledge"
    assert test["ok"] is True and test["collections"] == ["other"] and test["target_exists"] is False
    assert "kb_create" in created["next_steps"][0]
    assert [item["id"] for item in listed["data"]] == [connection["id"]]
    assert all(request.url.host == "qdrant.example.com" for request in fake_qdrant)


async def test_a_failed_test_is_a_tool_failure_with_the_result(key: Any, mcp_session: Any) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"status": {"error": "Invalid API key"}})

    async with mcp_session(await key(OPERATOR_SCOPES)) as mcp:
        created = await mcp.call(
            "kb_connection_create", name="X", kind="qdrant", settings={"url": QDRANT_URL}, test=False
        )
        with runtime.use_transport(httpx.MockTransport(refuse)):
            tested = await mcp.call("kb_connection_test", connection_id=created["data"]["connection"]["id"])

    assert "test" not in created["data"]
    assert tested["ok"] is False and tested["error"]["code"] == "connection_test_failed"
    assert "HTTP 401" in tested["error"]["message"] and tested["data"]["status"] == "error"


async def test_plans_show_the_bodies(key: Any, mcp_session: Any) -> None:
    async with mcp_session(await key(OPERATOR_SCOPES)) as mcp:
        create = await mcp.call(
            "kb_connection_create",
            name="X",
            kind="pinecone",
            settings={"index": "kb"},
            credential_id="c1",
            plan=True,
        )
        update = await mcp.call("kb_connection_update", connection_id="k1", name="Renamed", plan=True)
        kb = await mcp.call("kb_create", name="Policies", connection_id="k1", plan=True)

    [create_request] = create["plan"]
    assert (create_request["method"], create_request["path"]) == ("POST", "/v1/knowledge-connections")
    assert create_request["body"]["credential_id"] == "c1"
    assert update["plan"][0]["body"] == {"name": "Renamed"}
    assert kb["plan"][0]["body"]["connection_id"] == "k1"
    assert json.dumps(create).count("api_key") == 0
