"""V5-20 knowledge connections: routes, Test connection, stores per knowledge base, hosted rerank.

Every vendor is the in-memory fake of ``tests/fakes/vector_stores.py`` behind
an ``httpx.MockTransport`` (``runtime.use_transport``): no live call. The
embedder is :class:`~lkap_api.kb.embed.FakeEmbedder` (the ``test_kb.py``
seams), and Qdrant's BM25 model is a hashing stand-in.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from auth_helpers import key_client, make_api_key, make_workspace
from fakes.vector_stores import (
    QDRANT_HOST,
    WEAVIATE_HOST,
    FakeVendors,
    HashSparse,
)
from fastapi import FastAPI
from sqlalchemy import select

from lkap_api.db.models import AuditLog, KnowledgeBase, KnowledgeConnection
from lkap_api.db.session import Database
from lkap_api.kb.embed import FAKE_EMBEDDER_DIMENSION, FakeEmbedder
from lkap_api.kb.search import QUERY_CACHE
from lkap_api.kb.stores.pinecone import check_host
from lkap_api.kb.stores.qdrant import use_sparse_encoder
from lkap_api.knowledge_connections import runtime
from lkap_api.knowledge_connections.http import ConnectorError, scrub
from lkap_api.net_guard import GuardedTransport, NetPolicy
from lkap_api.routers.knowledge import get_embedder
from lkap_api.settings import Settings

API_ROOT = Path(__file__).resolve().parents[1]
QDRANT_URL = f"https://{QDRANT_HOST}"
WEAVIATE_URL = f"https://{WEAVIATE_HOST}"
DOC = (
    b"# Flood cover\n\nFlood damage to the ground floor is covered up to the policy limit.\n\n"
    b"# Theft\n\nStolen bicycles are covered when locked to a fixed frame.\n"
)


async def _fake_resolve_embedder(*args: object, **kwargs: object) -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture(autouse=True)
def _offline(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeVendors]:
    app.dependency_overrides[get_embedder] = lambda: FakeEmbedder()
    for target in (
        "lkap_api.kb.ingest.resolve_embedder",
        "lkap_api.kb.jobs.resolve_embedder",
        "lkap_api.knowledge_connections.router.resolve_embedder",
    ):
        monkeypatch.setattr(target, _fake_resolve_embedder)
    QUERY_CACHE.clear()
    vendors = FakeVendors()
    with runtime.use_transport(vendors.transport()), use_sparse_encoder(HashSparse()):
        yield vendors
    app.dependency_overrides.pop(get_embedder, None)
    QUERY_CACHE.clear()


@pytest.fixture
def vendors(_offline: FakeVendors) -> FakeVendors:
    return _offline


async def _key(client: httpx.AsyncClient, provider_id: str, value: str) -> str:
    response = await client.post(
        "/v1/credentials",
        json={"provider_id": provider_id, "label": f"{provider_id} key", "secrets": {"api_key": value}},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def _connection(
    client: httpx.AsyncClient, kind: str, settings: dict[str, Any], credential_id: str | None
) -> dict[str, Any]:
    response = await client.post(
        "/v1/knowledge-connections",
        json={"name": f"Demo — {kind}", "kind": kind, "settings": settings, "credential_id": credential_id},
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def _qdrant(client: httpx.AsyncClient, **settings: Any) -> dict[str, Any]:
    key = await _key(client, "qdrant", "qdrant-test-key")
    return await _connection(client, "qdrant", {"url": QDRANT_URL, **settings}, key)


async def _kb(client: httpx.AsyncClient, connection_id: str | None) -> dict[str, Any]:
    body: dict[str, Any] = {"name": "Harbor Lane policies"}
    if connection_id is not None:
        body["connection_id"] = connection_id
    response = await client.post("/v1/knowledge-bases", json=body)
    assert response.status_code == 201, response.text
    return dict(response.json())


async def _upload(client: httpx.AsyncClient, kb_id: str) -> dict[str, Any]:
    response = await client.post(
        f"/v1/knowledge-bases/{kb_id}/documents", files={"file": ("policy.md", DOC, "text/markdown")}
    )
    assert response.status_code == 202, response.text
    listed = (await client.get(f"/v1/knowledge-bases/{kb_id}/documents")).json()["items"]
    return dict(listed[0])


# ------------------------------------------------------------------------------ CRUD
async def test_create_list_get_returns_a_fingerprint_never_the_key(admin_client: httpx.AsyncClient) -> None:
    created = await _qdrant(admin_client)

    assert created["kind"] == "qdrant" and created["provider_id"] == "qdrant"
    assert created["status"] == "unverified"
    assert created["settings"] == {"url": QDRANT_URL, "collection": "lkap_knowledge", "native_hybrid": False}
    assert created["credential_fingerprint"] and created["credential_id"]
    listed = (await admin_client.get("/v1/knowledge-connections")).json()
    assert listed["total"] == 1
    fetched = (await admin_client.get(f"/v1/knowledge-connections/{created['id']}")).json()
    for body in (created, listed["items"][0], fetched):
        assert "qdrant-test-key" not in str(body)


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://qdrant.example.com", "https"),
        ("https://10.0.0.5:6333", "private"),
        ("https://169.254.169.254", "metadata"),
        ("https://user:pw@qdrant.example.com", "user name"),
        ("https://qdrant.example.com/?x=1", "query"),
    ],
)
async def test_a_url_the_guard_refuses_is_a_422(
    admin_client: httpx.AsyncClient, url: str, reason: str
) -> None:
    response = await admin_client.post(
        "/v1/knowledge-connections", json={"name": "Bad", "kind": "qdrant", "settings": {"url": url}}
    )
    assert response.status_code == 422, response.text
    assert reason in response.text


async def test_a_local_qdrant_is_allowed_in_dev_over_http(admin_client: httpx.AsyncClient) -> None:
    created = await _connection(admin_client, "qdrant", {"url": "http://localhost:6333"}, None)
    assert created["settings"]["url"] == "http://localhost:6333"


async def test_unknown_settings_and_wrong_or_missing_keys_are_refused(
    admin_client: httpx.AsyncClient,
) -> None:
    cohere_key = await _key(admin_client, "cohere-rerank", "cohere-test-key")
    extra = await admin_client.post(
        "/v1/knowledge-connections",
        json={"name": "X", "kind": "qdrant", "settings": {"url": QDRANT_URL, "vectors": 3}},
    )
    wrong_key = await admin_client.post(
        "/v1/knowledge-connections",
        json={"name": "X", "kind": "qdrant", "settings": {"url": QDRANT_URL}, "credential_id": cohere_key},
    )
    no_key = await admin_client.post(
        "/v1/knowledge-connections", json={"name": "X", "kind": "pinecone", "settings": {"index": "kb"}}
    )
    assert extra.status_code == wrong_key.status_code == no_key.status_code == 422
    assert "needs a 'qdrant' key" in wrong_key.text


async def test_writes_need_admin_and_providers_write(app: FastAPI, database: Database) -> None:
    _, raw = await make_api_key(database, ["providers:read"])
    async with key_client(app, raw) as client:
        assert (await client.get("/v1/knowledge-connections")).status_code == 200
        response = await client.post(
            "/v1/knowledge-connections", json={"name": "X", "kind": "qdrant", "settings": {"url": QDRANT_URL}}
        )
    assert response.status_code == 403


async def test_another_workspace_sees_nothing(
    app: FastAPI, database: Database, admin_client: httpx.AsyncClient
) -> None:
    created = await _qdrant(admin_client)
    other = await make_workspace(database, "harbor-lane")
    _, raw = await make_api_key(database, ["*"], workspace_id=other)
    async with key_client(app, raw) as client:
        assert (await client.get("/v1/knowledge-connections")).json()["total"] == 0
        assert (await client.get(f"/v1/knowledge-connections/{created['id']}")).status_code == 404
        kb = await client.post("/v1/knowledge-bases", json={"name": "X", "connection_id": created["id"]})
    assert kb.status_code == 422


async def test_update_resets_status_and_location_is_frozen_while_in_use(
    admin_client: httpx.AsyncClient, vendors: FakeVendors
) -> None:
    connection = await _qdrant(admin_client)
    await admin_client.post(f"/v1/knowledge-connections/{connection['id']}/test")
    renamed = await admin_client.put(
        f"/v1/knowledge-connections/{connection['id']}", json={"name": "Renamed"}
    )
    assert renamed.json()["status"] == "ok"  # a rename keeps the verdict
    await _kb(admin_client, connection["id"])

    moved = await admin_client.put(
        f"/v1/knowledge-connections/{connection['id']}",
        json={"settings": {"url": QDRANT_URL, "collection": "elsewhere"}},
    )
    hybrid = await admin_client.put(
        f"/v1/knowledge-connections/{connection['id']}",
        json={"settings": {"url": QDRANT_URL, "native_hybrid": True}},
    )

    assert moved.status_code == 409 and "Harbor Lane policies" in moved.text
    assert hybrid.status_code == 200 and hybrid.json()["status"] == "unverified"
    assert hybrid.json()["capabilities"]["hybrid"] is True


async def test_deleting_a_connection_with_knowledge_bases_is_a_409_naming_them(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    connection = await _qdrant(admin_client)
    kb = await _kb(admin_client, connection["id"])

    refused = await admin_client.delete(f"/v1/knowledge-connections/{connection['id']}")
    assert refused.status_code == 409
    assert "Harbor Lane policies" in refused.text

    assert (await admin_client.delete(f"/v1/knowledge-bases/{kb['id']}")).status_code == 204
    assert (await admin_client.delete(f"/v1/knowledge-connections/{connection['id']}")).status_code == 204
    async with database.session() as session:
        actions = [row.action for row in (await session.execute(select(AuditLog))).scalars()]
        payloads = [row.payload for row in (await session.execute(select(AuditLog))).scalars()]
    assert {"knowledge_connection.create", "knowledge_connection.delete"} <= set(actions)
    assert all("qdrant-test-key" not in str(payload) for payload in payloads)
    assert any((payload or {}).get("host") == QDRANT_HOST for payload in payloads)


# ------------------------------------------------------------------------------ Test connection
async def test_test_lists_collections_and_reports_a_dimension_mismatch_naming_it(
    admin_client: httpx.AsyncClient, vendors: FakeVendors
) -> None:
    from fakes.vector_stores import QdrantCollection

    vendors.qdrant.collections["lkap_knowledge"] = QdrantCollection(size=1536)
    vendors.qdrant.collections["other"] = QdrantCollection(size=384)
    connection = await _qdrant(admin_client)

    result = (await admin_client.post(f"/v1/knowledge-connections/{connection['id']}/test")).json()

    assert result["ok"] is False and result["status"] == "error"
    assert result["collections"] == ["lkap_knowledge", "other"]
    assert result["dimension_found"] == 1536 and result["dimension_expected"] == FAKE_EMBEDDER_DIMENSION
    assert "Qdrant collection 'lkap_knowledge'" in result["message"]
    stored = (await admin_client.get(f"/v1/knowledge-connections/{connection['id']}")).json()
    assert stored["status"] == "error" and "lkap_knowledge" in stored["last_error"]


async def test_test_ok_fills_capabilities(admin_client: httpx.AsyncClient) -> None:
    connection = await _qdrant(admin_client)

    result = (await admin_client.post(f"/v1/knowledge-connections/{connection['id']}/test")).json()

    assert result["ok"] is True, result
    assert result["target"] == "lkap_knowledge" and result["target_exists"] is False
    assert result["capabilities"]["version"] == "1.15.0"
    assert (await admin_client.get(f"/v1/knowledge-connections/{connection['id']}")).json()["status"] == "ok"


async def test_pinecone_test_names_the_index_on_a_dimension_mismatch(
    admin_client: httpx.AsyncClient, vendors: FakeVendors
) -> None:
    from fakes.vector_stores import PineconeIndex

    vendors.pinecone.indexes["policies"] = PineconeIndex(dimension=3072)
    key = await _key(admin_client, "pinecone", "pinecone-test-key")
    connection = await _connection(admin_client, "pinecone", {"index": "policies"}, key)

    result = (await admin_client.post(f"/v1/knowledge-connections/{connection['id']}/test")).json()

    assert result["ok"] is False and "Pinecone index 'policies'" in result["message"]
    assert result["collections"] == ["policies"]


async def test_a_wrong_key_is_an_error_with_no_secret_in_it(admin_client: httpx.AsyncClient) -> None:
    key = await _key(admin_client, "weaviate", "not-the-right-key-0123456789abcdef0123")
    connection = await _connection(admin_client, "weaviate", {"url": WEAVIATE_URL}, key)

    result = (await admin_client.post(f"/v1/knowledge-connections/{connection['id']}/test")).json()

    assert result["ok"] is False and "HTTP 401" in result["message"]
    assert "not-the-right-key" not in str(result)


@pytest.mark.parametrize(
    ("kind", "provider_id", "key"),
    [
        ("cohere_rerank", "cohere-rerank", "cohere-test-key"),
        ("voyage_rerank", "voyage-rerank", "voyage-test-key"),
    ],
)
async def test_a_reranker_test_checks_the_key(
    admin_client: httpx.AsyncClient, vendors: FakeVendors, kind: str, provider_id: str, key: str
) -> None:
    credential = await _key(admin_client, provider_id, key)
    connection = await _connection(admin_client, kind, {}, credential)

    result = (await admin_client.post(f"/v1/knowledge-connections/{connection['id']}/test")).json()

    assert result["ok"] is True, result
    assert result["capabilities"]["rerank"] is True
    assert vendors.requests[-1].headers["Authorization"] == f"Bearer {key}"


# ------------------------------------------------------------------------------ a KB on a connection
async def test_a_kb_on_qdrant_ingests_queries_hybrid_and_deletes(
    admin_client: httpx.AsyncClient, vendors: FakeVendors
) -> None:
    connection = await _qdrant(admin_client)
    kb = await _kb(admin_client, connection["id"])
    assert kb["connection_id"] == connection["id"] and kb["external_ref"] == "lkap_knowledge"

    document = await _upload(admin_client, kb["id"])
    assert document["status"] == "ready", document
    points = vendors.qdrant.collections["lkap_knowledge"].points
    assert len(points) == document["chunk_count"] > 0
    assert all(set(point["payload"]) == {"kb_id", "workspace_id", "document_id"} for point in points.values())

    hits = (
        await admin_client.post(
            f"/v1/knowledge-bases/{kb['id']}/search", json={"query": "flood damage", "k": 2, "mode": "hybrid"}
        )
    ).json()
    assert hits["hits"] and "Flood" in hits["hits"][0]["text"]
    assert hits["hits"][0]["score_source"] == "fused"

    assert (
        await admin_client.delete(f"/v1/knowledge-bases/{kb['id']}/documents/{document['id']}")
    ).status_code == 204
    assert points == {}
    await _upload(admin_client, kb["id"])
    assert (await admin_client.delete(f"/v1/knowledge-bases/{kb['id']}")).status_code == 204
    assert vendors.qdrant.collections["lkap_knowledge"].points == {}


async def test_native_hybrid_after_a_reindex_fuses_inside_qdrant(
    admin_client: httpx.AsyncClient, vendors: FakeVendors, app: FastAPI
) -> None:
    connection = await _qdrant(admin_client, native_hybrid=True)
    kb = await _kb(admin_client, connection["id"])
    await _upload(admin_client, kb["id"])
    from lkap_api.kb.jobs import enqueue_kb_reindex

    await enqueue_kb_reindex(app.state.jobs, kb["id"])  # created by the upload route
    points = vendors.qdrant.collections["lkap_knowledge"].points.values()
    assert all("bm25" in point["vector"] for point in points)

    before = len(vendors.requests)
    hits = (
        await admin_client.post(
            f"/v1/knowledge-bases/{kb['id']}/search",
            json={"query": "stolen bicycles", "k": 2, "mode": "hybrid"},
        )
    ).json()
    queries = [r for r in vendors.requests[before:] if r.url.path.endswith("/points/query")]
    assert (
        queries
        and b'"prefetch"' in queries[0].content
        and b'"fusion":"rrf"' in queries[0].content.replace(b" ", b"")
    )
    assert "Stolen" in hits["hits"][0]["text"] and hits["hits"][0]["score"] == pytest.approx(1.0)


async def test_a_kb_on_pinecone_creates_the_index_and_uses_a_namespace(
    admin_client: httpx.AsyncClient, vendors: FakeVendors
) -> None:
    key = await _key(admin_client, "pinecone", "pinecone-test-key")
    connection = await _connection(admin_client, "pinecone", {"index": "policies"}, key)
    kb = await _kb(admin_client, connection["id"])

    await _upload(admin_client, kb["id"])

    index = vendors.pinecone.indexes["policies"]
    assert index.dimension == FAKE_EMBEDDER_DIMENSION and index.metric == "cosine"
    assert list(index.namespaces) == [f"kb_{kb['id']}"]
    assert kb["external_ref"] == f"policies/kb_{kb['id']}"
    assert all(
        r.headers["X-Pinecone-Api-Version"] == "2026-04" for r in vendors.requests if "pinecone" in r.url.host
    )


async def test_a_kb_on_weaviate_uses_one_tenant(
    admin_client: httpx.AsyncClient, vendors: FakeVendors
) -> None:
    key = await _key(admin_client, "weaviate", "weaviate-test-key")
    connection = await _connection(admin_client, "weaviate", {"url": WEAVIATE_URL}, key)
    kb = await _kb(admin_client, connection["id"])

    await _upload(admin_client, kb["id"])
    hits = (
        await admin_client.post(
            f"/v1/knowledge-bases/{kb['id']}/search", json={"query": "flood damage", "k": 1}
        )
    ).json()

    assert list(vendors.weaviate.objects) == [("LkapKnowledge", f"kb_{kb['id']}")]
    assert vendors.weaviate.classes["LkapKnowledge"]["multiTenancyConfig"]["enabled"] is True
    assert hits["hits"] and "Flood" in hits["hits"][0]["text"]


async def test_a_kb_whose_key_was_deleted_fails_closed(
    admin_client: httpx.AsyncClient, vendors: FakeVendors, database: Database
) -> None:
    connection = await _qdrant(admin_client)
    kb = await _kb(admin_client, connection["id"])
    async with database.session() as session:
        await session.execute(
            sa.update(KnowledgeConnection)
            .where(KnowledgeConnection.id == connection["id"])
            .values(credential_id=None)
        )
    vendors.qdrant.api_key = "qdrant-test-key"  # a keyless call is refused by the cluster

    document = await _upload(admin_client, kb["id"])

    assert document["status"] == "failed"
    assert "HTTP 401" in (document["error"] or "")
    async with database.session() as session:
        rows = (await session.execute(select(KnowledgeBase).where(KnowledgeBase.id == kb["id"]))).scalars()
        assert next(rows).chunk_count == 0  # nothing went to the platform store instead


async def test_search_skips_an_unusable_connection_with_a_warning(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient, vendors: FakeVendors
) -> None:
    connection = await _qdrant(admin_client)
    kb = await _kb(admin_client, connection["id"])
    await _upload(admin_client, kb["id"])
    platform_kb = await _kb(admin_client, None)
    await _upload(admin_client, platform_kb["id"])
    vendors.qdrant.api_key = "rotated-elsewhere"

    result = (
        await service_client.post(
            "/internal/v1/kb/search", json={"kb_ids": [kb["id"], platform_kb["id"]], "query": "flood damage"}
        )
    ).json()

    assert [warning["code"] for warning in result["warnings"]] == ["kb_error"]
    assert result["hits"] and {hit["kb_id"] for hit in result["hits"]} == {platform_kb["id"]}


# ------------------------------------------------------------------------------ hosted rerank
async def test_a_hosted_reranker_rescores_the_tool_path_with_a_cost_line(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient, vendors: FakeVendors
) -> None:
    credential = await _key(admin_client, "cohere-rerank", "cohere-test-key")
    reranker = await _connection(admin_client, "cohere_rerank", {}, credential)
    kb = await _kb(admin_client, None)
    await _upload(admin_client, kb["id"])

    result = (
        await service_client.post(
            "/internal/v1/kb/search",
            json={
                "kb_ids": [kb["id"]],
                "query": "stolen bicycles",
                "k": 2,
                "rerank": f"connection:{reranker['id']}",
                "purpose": "tool",
            },
        )
    ).json()

    assert result["rerank"] == f"connection:{reranker['id']}"
    assert result["hits"][0]["score_source"] == "rerank" and "Stolen" in result["hits"][0]["text"]
    assert 0.0 <= result["hits"][0]["rerank_score"] <= 1.0
    usage = result["rerank_usage"]
    assert usage["provider_id"] == "cohere-rerank" and usage["unit"] == "requests" and usage["quantity"] == 1
    assert usage["cost_usd"] is None and usage["note"] == "no price"
    body = vendors.requests[-1]
    assert body.url.path == "/v2/rerank" and b'"model":"rerank-v4.0-fast"' in body.content.replace(b" ", b"")


async def test_auto_inject_never_calls_a_hosted_reranker(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient, vendors: FakeVendors
) -> None:
    credential = await _key(admin_client, "voyage-rerank", "voyage-test-key")
    reranker = await _connection(admin_client, "voyage_rerank", {}, credential)
    kb = await _kb(admin_client, None)
    await _upload(admin_client, kb["id"])
    before = len(vendors.requests)

    result = (
        await service_client.post(
            "/internal/v1/kb/search",
            json={
                "kb_ids": [kb["id"]],
                "query": "flood",
                "rerank": f"connection:{reranker['id']}",
                "purpose": "auto_inject",
            },
        )
    ).json()

    assert [warning["code"] for warning in result["warnings"]] == ["rerank_refused"]
    assert result["rerank_usage"] is None and len(vendors.requests) == before


async def test_a_reranker_of_another_workspace_is_refused(
    app: FastAPI, database: Database, admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient
) -> None:
    other = await make_workspace(database, "harbor-lane")
    _, raw = await make_api_key(database, ["*"], workspace_id=other)
    async with key_client(app, raw) as foreign:
        credential = await _key(foreign, "cohere-rerank", "cohere-test-key")
        reranker = await _connection(foreign, "cohere_rerank", {}, credential)
    kb = await _kb(admin_client, None)
    await _upload(admin_client, kb["id"])

    result = (
        await service_client.post(
            "/internal/v1/kb/search",
            json={"kb_ids": [kb["id"]], "query": "flood", "rerank": f"connection:{reranker['id']}"},
        )
    ).json()

    assert [warning["code"] for warning in result["warnings"]] == ["rerank_failed"]
    assert "does not exist" in result["warnings"][0]["message"]


async def test_a_malformed_rerank_value_is_a_422(service_client: httpx.AsyncClient) -> None:
    response = await service_client.post(
        "/internal/v1/kb/search", json={"kb_ids": ["x"], "query": "q", "rerank": "connection:../x"}
    )
    assert response.status_code == 422


# ------------------------------------------------------------------------------ validator
def test_the_validator_refuses_a_hosted_reranker_with_auto_inject() -> None:
    from conftest import inference_config
    from lkap_contracts.agent_config import AgentConfig, KnowledgeConfig

    from lkap_api.config_service import ValidationContext, knowledge_retrieval_issues

    base: AgentConfig = inference_config()
    auto = base.model_copy(update={"knowledge": KnowledgeConfig(kb_ids=["k1"], rerank="connection:abc")})
    tool_only = base.model_copy(
        update={"knowledge": KnowledgeConfig(kb_ids=["k1"], auto_inject=False, rerank="connection:abc")}
    )
    bogus = base.model_copy(update={"knowledge": KnowledgeConfig(kb_ids=["k1"], rerank="cohere")})

    auto_issues = knowledge_retrieval_issues(ValidationContext(config=auto))
    assert [(i.path, i.severity) for i in auto_issues] == [("knowledge.rerank", "error")]
    assert "search tool" in auto_issues[0].message
    assert knowledge_retrieval_issues(ValidationContext(config=tool_only)) == []
    assert "connection:<id>" in knowledge_retrieval_issues(ValidationContext(config=bogus))[0].message


# ------------------------------------------------------------------------------ moving between stores
async def test_reindex_moves_a_kb_between_stores_and_keeps_it_searchable(
    admin_client: httpx.AsyncClient, vendors: FakeVendors, app: FastAPI, database: Database
) -> None:
    from lkap_api.kb.jobs import PLATFORM_TARGET, enqueue_kb_reindex

    connection = await _qdrant(admin_client)
    kb = await _kb(admin_client, None)
    await _upload(admin_client, kb["id"])
    jobs = app.state.jobs  # created by the upload route

    await enqueue_kb_reindex(jobs, kb["id"], move_to=connection["id"])
    moved = (await admin_client.get(f"/v1/knowledge-bases/{kb['id']}")).json()
    assert moved["connection_id"] == connection["id"]
    assert len(vendors.qdrant.collections["lkap_knowledge"].points) > 0
    hits = (
        await admin_client.post(f"/v1/knowledge-bases/{kb['id']}/search", json={"query": "flood damage"})
    ).json()
    assert hits["hits"]

    await enqueue_kb_reindex(jobs, kb["id"], move_to=PLATFORM_TARGET)
    back = (await admin_client.get(f"/v1/knowledge-bases/{kb['id']}")).json()
    assert back["connection_id"] is None and back["external_ref"] is None
    assert vendors.qdrant.collections["lkap_knowledge"].points == {}
    assert (
        await admin_client.post(f"/v1/knowledge-bases/{kb['id']}/search", json={"query": "flood damage"})
    ).json()["hits"]


# ------------------------------------------------------------------------------ transport safety
async def test_the_default_client_is_the_guarded_one(settings: Settings) -> None:
    saved = list(runtime._TRANSPORT_OVERRIDE)
    runtime._TRANSPORT_OVERRIDE.clear()
    client = runtime.http_client(NetPolicy())
    try:
        assert isinstance(client._transport, GuardedTransport)
        assert client.follow_redirects is False
        assert runtime.http_client(NetPolicy()) is client  # one per loop and policy
    finally:
        await client.aclose()
        runtime._TRANSPORT_OVERRIDE.extend(saved)


@pytest.mark.parametrize(
    "host",
    ["evil.example.com", "http://policies-abc.svc.pinecone.io", "https://pinecone.io.evil.example", "", None],
)
def test_a_pinecone_data_host_outside_pinecone_io_is_refused(host: object) -> None:
    with pytest.raises(ConnectorError):
        check_host(host)
    assert check_host("policies-abc.svc.aped-1.pinecone.io") == "https://policies-abc.svc.aped-1.pinecone.io"


async def test_a_redirect_is_never_followed(settings: Settings) -> None:
    from lkap_api.knowledge_connections.http import VendorHttp

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://169.254.169.254/"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ConnectorError, match="redirect"):
            await VendorHttp(client, vendor="Qdrant", base_url=QDRANT_URL).call("GET", "/collections")


def test_scrub_removes_urls_and_key_shaped_tokens() -> None:
    text = "bad key sk-abcdefghijklmnopqrstuvwxyz0123456789 at https://example.com/x?k=1"
    cleaned = scrub(text)
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in cleaned and "example.com" not in cleaned


# ------------------------------------------------------------------------------ migration
def _migration() -> ModuleType:
    path = API_ROOT / "alembic" / "versions" / "v5_005_knowledge_connections.py"
    spec = importlib.util.spec_from_file_location("v5_005", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_migration_chains_after_session_uploads() -> None:
    module = _migration()
    assert module.revision == "v5_005_knowledge_connections"
    assert module.down_revision == "v5_006_agent_tests"


async def test_existing_knowledge_bases_read_as_platform_managed(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    kb = await _kb(admin_client, None)
    async with database.session() as session:
        row = (await session.execute(select(KnowledgeBase).where(KnowledgeBase.id == kb["id"]))).scalar_one()
    assert (row.connection_id, row.kind, row.external_ref) == (None, "managed", None)
    assert (kb["connection_id"], kb["kind"], kb["external_ref"]) == (None, "managed", None)
