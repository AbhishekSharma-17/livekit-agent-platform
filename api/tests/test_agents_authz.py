"""V5-27: cross-workspace isolation of the V5 routes (docs/v5/SECURITY-REVIEW-V5.md §3).

The review's authorization sweep found no IDOR and named four tests worth adding; each
creates a resource in one workspace and reaches for it from another with a fully scoped
API key. The Builder side of the Apps admin gate (S5-40) is pinned in
``test_tool_providers.py`` next to the Composio fakes it needs.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from auth_helpers import key_client, make_api_key, make_workspace
from fastapi import FastAPI

from lkap_api.db.session import Database
from lkap_api.kb.embed import FakeEmbedder
from lkap_api.routers.knowledge import get_embedder

ALL_SCOPES = ["*"]


@pytest.fixture
async def clients(
    app: FastAPI, database: Database
) -> AsyncIterator[tuple[httpx.AsyncClient, httpx.AsyncClient]]:
    """``(home, stranger)``: fully scoped keys of the default workspace and of another one."""
    app.dependency_overrides[get_embedder] = lambda: FakeEmbedder()
    other = await make_workspace(database, "stranger-ws")
    _, home_raw = await make_api_key(database, ALL_SCOPES)
    _, stranger_raw = await make_api_key(database, ALL_SCOPES, workspace_id=other)
    async with key_client(app, home_raw) as home, key_client(app, stranger_raw) as stranger:
        yield home, stranger
    app.dependency_overrides.pop(get_embedder, None)


async def _kb_with_document(client: httpx.AsyncClient) -> tuple[str, str]:
    kb = await client.post("/v1/knowledge-bases", json={"name": "Home KB"})
    assert kb.status_code == 201, kb.text
    kb_id = kb.json()["id"]
    uploaded = await client.post(
        f"/v1/knowledge-bases/{kb_id}/documents", files={"file": ("a.md", b"# A\n\nText.", "text/markdown")}
    )
    assert uploaded.status_code == 202, uploaded.text
    return kb_id, uploaded.json()["id"]


async def test_reindex_other_workspace_kb_is_404(
    clients: tuple[httpx.AsyncClient, httpx.AsyncClient],
) -> None:
    home, stranger = clients
    kb_id, _ = await _kb_with_document(home)

    response = await stranger.post(f"/v1/knowledge-bases/{kb_id}/reindex", json={})

    assert response.status_code == 404


async def test_other_workspace_kb_upload_import_reindex_and_document_delete_are_404(
    clients: tuple[httpx.AsyncClient, httpx.AsyncClient],
) -> None:
    home, stranger = clients
    kb_id, document_id = await _kb_with_document(home)

    upload = await stranger.post(
        f"/v1/knowledge-bases/{kb_id}/documents", files={"file": ("b.md", b"# B", "text/markdown")}
    )
    imported = await stranger.post(
        f"/v1/knowledge-bases/{kb_id}/documents/import", json={"url": "https://example.com/a.md"}
    )
    reindex = await stranger.post(f"/v1/knowledge-bases/{kb_id}/reindex", json={})
    deleted = await stranger.delete(f"/v1/knowledge-bases/{kb_id}/documents/{document_id}")
    listed = await home.get(f"/v1/knowledge-bases/{kb_id}/documents")

    assert [r.status_code for r in (upload, imported, reindex, deleted)] == [404, 404, 404, 404]
    assert [item["id"] for item in listed.json()["items"]] == [document_id]


async def test_compliance_of_non_member_workspace_is_404(
    clients: tuple[httpx.AsyncClient, httpx.AsyncClient],
) -> None:
    home, stranger = clients

    own = await home.get("/v1/workspaces/default/compliance")
    foreign = await stranger.get("/v1/workspaces/default/compliance")

    assert own.status_code == 200
    assert foreign.status_code == 404, "a workspace the caller is not in is not found, not forbidden"


async def test_oauth_start_and_status_for_another_workspace_tool_is_404(
    clients: tuple[httpx.AsyncClient, httpx.AsyncClient],
) -> None:
    home, stranger = clients
    definition = {"kind": "mcp", "name": "t", "url": "https://mcp.example.com/mcp", "auth": {"kind": "oauth"}}
    tool = await home.post("/v1/tools", json={"kind": "mcp", "name": "t", "definition": definition})
    assert tool.status_code == 201, tool.text
    tool_id = tool.json()["id"]

    start = await stranger.post(f"/v1/tools/{tool_id}/oauth/start", json={})
    status = await stranger.get(f"/v1/tools/{tool_id}/oauth/status")
    revoke = await stranger.post(f"/v1/tools/{tool_id}/oauth/revoke")

    assert [r.status_code for r in (start, status, revoke)] == [404, 404, 404]
