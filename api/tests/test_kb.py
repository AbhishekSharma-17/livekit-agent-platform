"""Tests for `lkap_api.routers.knowledge`: KB CRUD, document upload, search.

`get_embedder` is overridden with `FakeEmbedder` everywhere so the suite never
loads the fastembed ONNX model or touches the network; `-m "not live"` stays
network-free. LanceDB itself is real (a local file store, no server) so these
tests exercise the actual vector round trip, not a mock of it.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest
from fastapi import FastAPI

from lkap_api.kb.embed import FakeEmbedder
from lkap_api.routers.knowledge import get_embedder


@pytest.fixture(autouse=True)
def _fake_embedder(app: FastAPI) -> Iterator[None]:
    app.dependency_overrides[get_embedder] = lambda: FakeEmbedder()
    yield
    app.dependency_overrides.pop(get_embedder, None)


async def test_knowledge_router_is_mounted(admin_client: httpx.AsyncClient) -> None:
    response = await admin_client.get("/v1/knowledge-bases")
    assert response.status_code == 200


async def test_create_requires_admin(client: httpx.AsyncClient) -> None:
    response = await client.post("/v1/knowledge-bases", json={"name": "X"})
    assert response.status_code == 401


async def test_create_list_get_kb(admin_client: httpx.AsyncClient) -> None:
    created = await admin_client.post("/v1/knowledge-bases", json={"name": "Policies", "description": "d"})
    assert created.status_code == 201, created.text
    kb = created.json()
    assert kb["name"] == "Policies"
    assert kb["embedder_id"] == "fastembed-embedding"
    assert kb["chunk_count"] == 0
    assert kb["document_count"] == 0

    listed = await admin_client.get("/v1/knowledge-bases")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    fetched = await admin_client.get(f"/v1/knowledge-bases/{kb['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == kb["id"]


async def test_get_unknown_kb_is_404(admin_client: httpx.AsyncClient) -> None:
    response = await admin_client.get("/v1/knowledge-bases/does-not-exist")
    assert response.status_code == 404


async def test_create_kb_rejects_unknown_embedder_id(admin_client: httpx.AsyncClient) -> None:
    response = await admin_client.post("/v1/knowledge-bases", json={"name": "X", "embedder_id": "made-up"})
    assert response.status_code == 422


async def test_update_kb_renames_it(admin_client: httpx.AsyncClient) -> None:
    created = await admin_client.post("/v1/knowledge-bases", json={"name": "Old name"})
    kb_id = created.json()["id"]
    updated = await admin_client.put(f"/v1/knowledge-bases/{kb_id}", json={"name": "New name"})
    assert updated.status_code == 200
    assert updated.json()["name"] == "New name"


async def _upload(
    admin_client: httpx.AsyncClient, kb_id: str, filename: str, content: bytes, mime: str
) -> dict:
    response = await admin_client.post(
        f"/v1/knowledge-bases/{kb_id}/documents", files={"file": (filename, content, mime)}
    )
    assert response.status_code == 202, response.text
    return response.json()


async def test_upload_ingests_synchronously_under_the_asgi_transport(admin_client: httpx.AsyncClient) -> None:
    # `BackgroundTasks` run inside the same ASGI call under `httpx.ASGITransport`,
    # so by the time `post()` returns the document is already `ready`.
    created = await admin_client.post("/v1/knowledge-bases", json={"name": "Claims KB"})
    kb_id = created.json()["id"]

    document = await _upload(
        admin_client,
        kb_id,
        "policy.md",
        b"# Flood coverage\n\nFlood damage is covered under the HO-4 policy line.",
        "text/markdown",
    )
    assert document["status"] == "pending"

    listed = await admin_client.get(f"/v1/knowledge-bases/{kb_id}/documents")
    assert listed.status_code == 200
    [stored] = listed.json()["items"]
    assert stored["status"] == "ready"
    assert stored["chunk_count"] >= 1

    kb_after = await admin_client.get(f"/v1/knowledge-bases/{kb_id}")
    assert kb_after.json()["chunk_count"] == stored["chunk_count"]
    assert kb_after.json()["document_count"] == 1


async def test_search_returns_the_matching_chunk(admin_client: httpx.AsyncClient) -> None:
    created = await admin_client.post("/v1/knowledge-bases", json={"name": "Claims KB"})
    kb_id = created.json()["id"]
    await _upload(
        admin_client,
        kb_id,
        "policy.md",
        b"# Flood coverage\n\nFlood damage is covered under the HO-4 policy line.",
        "text/markdown",
    )
    await _upload(
        admin_client,
        kb_id,
        "unrelated.md",
        b"# Parking\n\nThe office garage closes at 9pm on weekdays.",
        "text/markdown",
    )

    response = await admin_client.post(
        f"/v1/knowledge-bases/{kb_id}/search", json={"query": "flood damage", "k": 2}
    )
    assert response.status_code == 200
    hits = response.json()["hits"]
    assert hits
    assert "flood" in hits[0]["text"].lower()
    assert hits[0]["filename"] == "policy.md"


async def test_delete_document_recomputes_chunk_count(admin_client: httpx.AsyncClient) -> None:
    created = await admin_client.post("/v1/knowledge-bases", json={"name": "Temp KB"})
    kb_id = created.json()["id"]
    document = await _upload(admin_client, kb_id, "a.md", b"hello world " * 200, "text/markdown")

    deleted = await admin_client.delete(f"/v1/knowledge-bases/{kb_id}/documents/{document['id']}")
    assert deleted.status_code == 204

    kb_after = await admin_client.get(f"/v1/knowledge-bases/{kb_id}")
    assert kb_after.json()["chunk_count"] == 0
    assert kb_after.json()["document_count"] == 0


async def test_delete_unknown_document_is_404(admin_client: httpx.AsyncClient) -> None:
    created = await admin_client.post("/v1/knowledge-bases", json={"name": "Temp KB"})
    kb_id = created.json()["id"]
    response = await admin_client.delete(f"/v1/knowledge-bases/{kb_id}/documents/does-not-exist")
    assert response.status_code == 404


async def test_delete_kb_removes_it_and_its_vector_table(admin_client: httpx.AsyncClient) -> None:
    created = await admin_client.post("/v1/knowledge-bases", json={"name": "Temp KB"})
    kb_id = created.json()["id"]
    await _upload(admin_client, kb_id, "a.md", b"some searchable content about deductibles", "text/markdown")

    deleted = await admin_client.delete(f"/v1/knowledge-bases/{kb_id}")
    assert deleted.status_code == 204

    missing = await admin_client.get(f"/v1/knowledge-bases/{kb_id}")
    assert missing.status_code == 404


async def test_internal_search_requires_service_token(client: httpx.AsyncClient) -> None:
    response = await client.post("/internal/v1/kb/search", json={"kb_ids": ["x"], "query": "q"})
    assert response.status_code == 401


async def test_internal_search_returns_hits_across_kbs(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient
) -> None:
    created = await admin_client.post("/v1/knowledge-bases", json={"name": "Internal KB"})
    kb_id = created.json()["id"]
    await _upload(
        admin_client, kb_id, "a.txt", b"The deductible for auto claims is five hundred dollars.", "text/plain"
    )

    response = await service_client.post(
        "/internal/v1/kb/search", json={"kb_ids": [kb_id], "query": "deductible", "k": 2}
    )
    assert response.status_code == 200
    hits = response.json()["hits"]
    assert hits
    assert "deductible" in hits[0]["text"].lower()


async def test_internal_search_with_no_kb_ids_returns_no_hits(service_client: httpx.AsyncClient) -> None:
    response = await service_client.post("/internal/v1/kb/search", json={"kb_ids": [], "query": "anything"})
    assert response.status_code == 200
    assert response.json()["hits"] == []


@pytest.mark.parametrize("k", [0, 50])
async def test_kb_search_rejects_k_outside_1_to_20(admin_client: httpx.AsyncClient, k: int) -> None:
    """DECISIONS-W2.md D-W2-5: the api enforces `1 <= k <= 20`."""
    created = await admin_client.post("/v1/knowledge-bases", json={"name": "K KB"})
    kb_id = created.json()["id"]
    response = await admin_client.post(f"/v1/knowledge-bases/{kb_id}/search", json={"query": "q", "k": k})
    assert response.status_code == 422


@pytest.mark.parametrize("k", [0, 50])
async def test_internal_search_rejects_k_outside_1_to_20(service_client: httpx.AsyncClient, k: int) -> None:
    payload = {"kb_ids": ["x"], "query": "q", "k": k}
    response = await service_client.post("/internal/v1/kb/search", json=payload)
    assert response.status_code == 422
