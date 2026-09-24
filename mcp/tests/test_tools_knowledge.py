"""Knowledge-base tools (§4.5): multipart upload with wait, url import via the api, untrusted hits."""

from __future__ import annotations

from typing import Any

import respx
from conftest import BUILDER_SCOPES

POLICY = "# Flood coverage\n\nFlood damage to a basement is covered under the HO-4 policy line."


async def test_kb_add_document_text_uploads_markdown_and_wait_returns_ready(
    key: Any, mcp_session: Any
) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        kb = (await mcp.call("kb_create", name="Policies"))["data"]
        added = await mcp.call("kb_add_document", kb_id=kb["id"], text=POLICY, filename="policy.md")
        [upload] = mcp.transport.calls("POST", f"/v1/knowledge-bases/{kb['id']}/documents")
        hits = await mcp.call("kb_search", kb_id=kb["id"], query="basement flood")

    assert added["ok"] is True, added
    assert (added["data"]["status"], added["data"]["filename"], added["data"]["mime"]) == (
        "ready",
        "policy.md",
        "text/markdown",
    )
    assert added["data"]["chunk_count"] >= 1
    assert b"multipart/form-data" in mcp.transport.requests[upload].headers["content-type"].encode()
    top = hits["data"][0]["text"]
    assert top["untrusted"] is True and "HO-4" in top["content"] and top["source"] == f"kb:{kb['id']}"


async def test_kb_add_document_url_calls_the_import_route_and_never_fetches_itself(
    key: Any, mcp_session: Any
) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        kb = (await mcp.call("kb_create", name="Imported"))["data"]
        with respx.mock(assert_all_called=True) as mock:
            mock.get("https://example.test/policy.md").respond(
                200, content=POLICY.encode(), headers={"content-type": "text/markdown"}
            )
            added = await mcp.call("kb_add_document", kb_id=kb["id"], url="https://example.test/policy.md")
        imports = mcp.transport.calls("POST", f"/v1/knowledge-bases/{kb['id']}/documents/import")

    assert added["ok"] is True, added
    assert added["data"]["status"] == "ready" and added["data"]["filename"] == "policy.md"
    assert len(imports) == 1
    assert mcp.transport.bodies[imports[0]] == {"url": "https://example.test/policy.md"}


async def test_kb_add_document_blocked_url_relays_the_guard_with_a_hint(key: Any, mcp_session: Any) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        kb = (await mcp.call("kb_create", name="Guarded"))["data"]
        added = await mcp.call(
            "kb_add_document", kb_id=kb["id"], url="http://169.254.169.254/latest/meta-data"
        )

    assert added["ok"] is False and added["error"]["status"] == 422
    assert added["error"]["details"]["reason"] == "blocked_destination"
    assert "public hosts only" in added["error"]["hint"]


async def test_kb_add_document_needs_exactly_one_source(key: Any, mcp_session: Any) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        none = await mcp.call("kb_add_document", kb_id="k1")
        both = await mcp.call("kb_add_document", kb_id="k1", text="a", url="https://example.test/a")

    assert none["error"]["code"] == both["error"]["code"] == "invalid_input"
