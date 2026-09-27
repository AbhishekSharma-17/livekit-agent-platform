"""In-memory fakes of the five knowledge-connection services, as one ``httpx.MockTransport`` (V5-20).

Each fake implements the REST subset the api's clients call, with the wire
shapes verified on the vendors' docs (2026-09-27), so the real store and
re-ranker code runs against them unchanged:

* :class:`FakeQdrant` — ``GET /``, ``/collections``, ``/collections/{c}``
  (``PUT`` creates), ``/index``, ``/points`` (upsert), ``/points/query``
  (dense, or ``prefetch`` + ``{"fusion": "rrf"}`` with Qdrant's k = 2),
  ``/points/delete`` by filter; ``api-key`` header.
* :class:`FakePinecone` — the control plane (``/indexes``) and one data-plane
  host per index (``/vectors/upsert``, ``/query``, ``/vectors/delete``,
  ``DELETE /namespaces/{ns}``); ``Api-Key`` and ``X-Pinecone-Api-Version``.
* :class:`FakeWeaviate` — ``/v1/meta``, ``/v1/schema`` (+ tenants),
  ``/v1/batch/objects`` (insert; delete with ``?tenant=``), ``/v1/graphql``
  (``nearVector`` and ``hybrid`` parsed from the inline query text; scores
  come back as strings, like Weaviate's).
* :class:`FakeCohere` / :class:`FakeVoyage` — the re-rank endpoints (relevance =
  share of query words in the passage) and Cohere's key check.

Every request is recorded (``vendors.requests``) so tests can assert headers
and bodies. A wrong key answers 401.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

QDRANT_HOST = "qdrant.example.com"
WEAVIATE_HOST = "weaviate.example.com"
PINECONE_CONTROL = "api.pinecone.io"
COHERE_HOST = "api.cohere.com"
VOYAGE_HOST = "api.voyageai.com"


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def _json(status: int, body: Any) -> httpx.Response:
    return httpx.Response(status, json=body)


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


# ------------------------------------------------------------------------------ Qdrant
@dataclass
class QdrantCollection:
    size: int
    distance: str = "Cosine"
    points: dict[str, dict[str, Any]] = field(default_factory=dict)
    indexes: dict[str, dict[str, Any]] = field(default_factory=dict)


def _qdrant_match(payload: dict[str, Any], selector: dict[str, Any] | None) -> bool:
    for condition in (selector or {}).get("must", []):
        value = payload.get(condition["key"])
        match = condition["match"]
        if "value" in match and value != match["value"]:
            return False
        if "any" in match and value not in match["any"]:
            return False
    return True


class FakeQdrant:
    def __init__(self, api_key: str | None = "qdrant-test-key") -> None:
        self.api_key = api_key
        self.collections: dict[str, QdrantCollection] = {}

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.api_key is not None and request.headers.get("api-key") != self.api_key:
            return _json(
                401, {"status": {"error": "Must provide an API key or an Authorization bearer token"}}
            )
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        if path == "/" and request.method == "GET":
            return _json(200, {"title": "qdrant - vector search engine", "version": "1.15.0"})
        if path == "/collections":
            return _json(200, {"result": {"collections": [{"name": name} for name in self.collections]}})
        parts = path.strip("/").split("/")
        name = parts[1]
        collection = self.collections.get(name)
        if len(parts) == 2:
            if request.method == "PUT":
                dense = body["vectors"]["dense"]
                self.collections[name] = QdrantCollection(size=dense["size"], distance=dense["distance"])
                return _json(200, {"result": True, "status": "ok"})
            if collection is None:
                return _json(404, {"status": {"error": f"Collection `{name}` doesn't exist!"}})
            vectors = {"dense": {"size": collection.size, "distance": collection.distance}}
            config = {"params": {"vectors": vectors, "sparse_vectors": {"bm25": {"modifier": "idf"}}}}
            return _json(200, {"result": {"status": "green", "config": config}})
        if collection is None:
            return _json(404, {"status": {"error": f"Collection `{name}` doesn't exist!"}})
        action = "/".join(parts[2:])
        completed = {"result": {"operation_id": 1, "status": "completed"}, "status": "ok"}
        if action == "index":
            collection.indexes[body["field_name"]] = body["field_schema"]
            return _json(200, completed)
        if action == "points" and request.method == "PUT":
            for point in body["points"]:
                dense = point["vector"]["dense"]
                if len(dense) != collection.size:
                    return _json(400, {"status": {"error": "Wrong input: Vector dimension error"}})
                point_id = str(point["id"])
                if len(point_id) != 36:
                    return _json(400, {"status": {"error": "Unable to parse UUID"}})
                collection.points[point_id] = point
            return _json(200, completed)
        if action == "points/delete":
            for point_id in [
                pid
                for pid, point in collection.points.items()
                if _qdrant_match(point["payload"], body["filter"])
            ]:
                del collection.points[point_id]
            return _json(200, completed)
        if action == "points/query":
            return _json(200, {"result": {"points": self._query(collection, body)}, "status": "ok"})
        return _json(404, {"status": {"error": "not found"}})

    def _ranked(
        self, collection: QdrantCollection, query: Any, using: str, selector: Any, limit: int
    ) -> list[str]:
        scored: list[tuple[float, str]] = []
        for point_id, point in collection.points.items():
            if not _qdrant_match(point["payload"], selector):
                continue
            if using == "dense":
                scored.append((cosine(query, point["vector"]["dense"]), point_id))
            else:
                sparse = point["vector"].get("bm25")
                if sparse is None:
                    continue
                weights = dict(zip(sparse["indices"], sparse["values"], strict=True))
                score = sum(
                    weights.get(i, 0.0) * v for i, v in zip(query["indices"], query["values"], strict=True)
                )
                if score > 0:
                    scored.append((score, point_id))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [point_id for _score, point_id in scored[:limit]]

    def _query(self, collection: QdrantCollection, body: dict[str, Any]) -> list[dict[str, Any]]:
        limit = int(body.get("limit", 10))
        if "prefetch" in body:
            fused: dict[str, float] = {}
            for prefetch in body["prefetch"]:
                ranked = self._ranked(
                    collection,
                    prefetch["query"],
                    prefetch["using"],
                    prefetch.get("filter"),
                    prefetch["limit"],
                )
                for rank, point_id in enumerate(ranked):
                    fused[point_id] = fused.get(point_id, 0.0) + 1.0 / (2 + rank)
            order = sorted(fused.items(), key=lambda item: (-item[1], item[0]))[:limit]
            return [
                {"id": pid, "score": score, "payload": collection.points[pid]["payload"]}
                for pid, score in order
            ]
        ranked = self._ranked(
            collection, body["query"], body.get("using", "dense"), body.get("filter"), limit
        )
        return [
            {
                "id": pid,
                "score": cosine(body["query"], collection.points[pid]["vector"]["dense"]),
                "payload": collection.points[pid]["payload"],
            }
            for pid in ranked
        ]


# ------------------------------------------------------------------------------ Pinecone
@dataclass
class PineconeIndex:
    dimension: int
    metric: str = "cosine"
    namespaces: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)


class FakePinecone:
    def __init__(
        self, api_key: str = "pinecone-test-key", *, host_suffix: str = ".svc.aped-1.pinecone.io"
    ) -> None:
        self.api_key = api_key
        self.host_suffix = host_suffix
        self.indexes: dict[str, PineconeIndex] = {}

    def host_of(self, name: str) -> str:
        return f"{name}-abc123{self.host_suffix}"

    def owns(self, host: str) -> bool:
        return host == PINECONE_CONTROL or host.endswith(self.host_suffix)

    def _describe(self, name: str) -> dict[str, Any]:
        index = self.indexes[name]
        return {
            "name": name,
            "dimension": index.dimension,
            "metric": index.metric,
            "host": self.host_of(name),
            "spec": {"serverless": {"cloud": "aws", "region": "us-east-1"}},
            "status": {"ready": True, "state": "Ready"},
            "vector_type": "dense",
        }

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("Api-Key") != self.api_key:
            return _json(401, {"error": {"code": "UNAUTHENTICATED", "message": "Invalid API Key"}})
        if not request.headers.get("X-Pinecone-Api-Version"):
            return _json(400, {"error": {"message": "missing api version"}})
        body = json.loads(request.content) if request.content else {}
        path = request.url.path
        if request.url.host == PINECONE_CONTROL:
            if path == "/indexes" and request.method == "GET":
                return _json(200, {"indexes": [self._describe(name) for name in self.indexes]})
            if path == "/indexes" and request.method == "POST":
                assert body["spec"]["serverless"], body
                self.indexes[body["name"]] = PineconeIndex(dimension=body["dimension"], metric=body["metric"])
                return _json(201, self._describe(body["name"]))
            name = path.rsplit("/", 1)[-1]
            if name not in self.indexes:
                return _json(404, {"error": {"code": "NOT_FOUND", "message": f"Resource {name} not found"}})
            return _json(200, self._describe(name))
        index = next((idx for n, idx in self.indexes.items() if self.host_of(n) == request.url.host), None)
        if index is None:
            return _json(404, {"error": {"message": "no such host"}})
        if path == "/vectors/upsert":
            namespace = index.namespaces.setdefault(body["namespace"], {})
            for vector in body["vectors"]:
                if len(vector["values"]) != index.dimension:
                    return _json(400, {"error": {"message": "Vector dimension does not match the index"}})
                namespace[vector["id"]] = vector
            return _json(200, {"upsertedCount": len(body["vectors"])})
        if path == "/query":
            records = index.namespaces.get(body["namespace"], {})
            wanted = (body.get("filter") or {}).get("document_id", {}).get("$in")
            scored = sorted(
                (
                    (cosine(body["vector"], record["values"]), record)
                    for record in records.values()
                    if wanted is None or record["metadata"]["document_id"] in wanted
                ),
                key=lambda item: (-item[0], item[1]["id"]),
            )[: body["topK"]]
            return _json(
                200,
                {
                    "matches": [
                        {"id": record["id"], "score": score, "metadata": record["metadata"]}
                        for score, record in scored
                    ],
                    "namespace": body["namespace"],
                },
            )
        if path == "/vectors/delete":
            records = index.namespaces.get(body["namespace"], {})
            document = body["filter"]["document_id"]["$eq"]
            for record_id in [
                rid for rid, rec in records.items() if rec["metadata"]["document_id"] == document
            ]:
                del records[record_id]
            return _json(200, {})
        if path.startswith("/namespaces/") and request.method == "DELETE":
            namespace = path.rsplit("/", 1)[-1]
            if namespace not in index.namespaces:
                return _json(404, {"error": {"code": "NOT_FOUND", "message": "Namespace not found"}})
            del index.namespaces[namespace]
            return _json(200, {})
        return _json(404, {"error": {"message": "unknown route"}})


# ------------------------------------------------------------------------------ Weaviate
_GRAPHQL_RE = re.compile(
    r"Get \{ (?P<cls>\w+)\(tenant: (?P<tenant>\"[^\"]*\"), (?P<kind>nearVector|hybrid): \{(?P<args>.*?)\}"
    r"(?:, where: (?P<where>\{.*\}))?, limit: (?P<limit>\d+)\)",
    re.S,
)


class FakeWeaviate:
    def __init__(self, api_key: str | None = "weaviate-test-key") -> None:
        self.api_key = api_key
        self.classes: dict[str, dict[str, Any]] = {}
        self.objects: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.api_key is not None and request.headers.get("Authorization") != f"Bearer {self.api_key}":
            return _json(401, {"code": 401, "message": "anonymous access not enabled"})
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        if path == "/v1/meta":
            return _json(200, {"hostname": "http://[::]:8080", "version": "1.32.0", "modules": {}})
        if path == "/v1/schema" and request.method == "GET":
            return _json(200, {"classes": list(self.classes.values())})
        if path == "/v1/schema" and request.method == "POST":
            self.classes[body["class"]] = body
            return _json(200, body)
        match = re.fullmatch(r"/v1/schema/(\w+)(/tenants)?", path)
        if match:
            cls, tenants = match.group(1), match.group(2)
            if cls not in self.classes:
                return _json(404, {})
            if not tenants:
                return _json(200, self.classes[cls])
            if request.method == "POST":
                existing = {tenant for c, tenant in self.objects if c == cls}
                for tenant in body:
                    if tenant["name"] in existing:
                        return _json(422, {"error": [{"message": "tenant already exists"}]})
                    self.objects[(cls, tenant["name"])] = {}
                return _json(200, body)
            for name in body:
                self.objects.pop((cls, name), None)
            return _json(200, {})
        if path == "/v1/batch/objects" and request.method == "POST":
            results = []
            for obj in body["objects"]:
                key = (obj["class"], obj["tenant"])
                self.objects.setdefault(key, {})[obj["id"]] = obj
                results.append({"id": obj["id"], "result": {}})
            return _json(200, results)
        if path == "/v1/batch/objects" and request.method == "DELETE":
            tenant = request.url.params.get("tenant")
            cls = body["match"]["class"]
            where = body["match"]["where"]
            store = self.objects.get((cls, tenant or ""))
            if store is None:
                return _json(422, {"error": [{"message": f"tenant not found: {tenant}"}]})
            doomed = [
                oid for oid, obj in store.items() if obj["properties"][where["path"][0]] == where["valueText"]
            ]
            for oid in doomed:
                del store[oid]
            return _json(
                200, {"results": {"matches": len(doomed), "limit": 10000, "successful": len(doomed)}}
            )
        if path == "/v1/graphql":
            return self._graphql(body["query"])
        return _json(404, {})

    def _graphql(self, query: str) -> httpx.Response:
        match = _GRAPHQL_RE.search(query)
        assert match, query
        cls, tenant = match.group("cls"), json.loads(match.group("tenant"))
        store = self.objects.get((cls, tenant))
        if store is None:
            return _json(
                200, {"data": {"Get": {cls: None}}, "errors": [{"message": f"tenant not found: {tenant}"}]}
            )
        vector = json.loads(re.search(r"vector: (\[[^\]]*\])", match.group("args")).group(1))  # type: ignore[union-attr]
        documents = re.findall(r'valueText: "([^"]+)"', match.group("where") or "") or None
        limit = int(match.group("limit"))
        candidates = [
            obj
            for obj in store.values()
            if documents is None or obj["properties"]["document_id"] in documents
        ]
        rows: list[dict[str, Any]] = []
        if match.group("kind") == "nearVector":
            ordered = sorted(candidates, key=lambda obj: -cosine(vector, obj["vector"]))[:limit]
            for obj in ordered:
                extra = {"id": obj["id"], "distance": 1.0 - cosine(vector, obj["vector"])}
                rows.append(
                    {
                        **{k: obj["properties"][k] for k in ("chunk_id", "kb_id", "document_id")},
                        "_additional": extra,
                    }
                )
        else:
            text = json.loads(re.search(r"query: (\"(?:[^\"\\]|\\.)*\")", match.group("args")).group(1))  # type: ignore[union-attr]
            words = _words(text)
            by_vector = sorted(candidates, key=lambda obj: -cosine(vector, obj["vector"]))
            by_keyword = sorted(
                (obj for obj in candidates if words & _words(obj["properties"].get("text", ""))),
                key=lambda obj: -len(words & _words(obj["properties"].get("text", ""))),
            )
            fused: dict[str, float] = {}
            for ranking in (by_vector, by_keyword):
                for rank, obj in enumerate(ranking):
                    fused[obj["id"]] = fused.get(obj["id"], 0.0) + 0.5 / (rank + 60)
            ordered_ids = sorted(fused, key=lambda oid: -fused[oid])[:limit]
            for oid in ordered_ids:
                obj = store[oid]
                rows.append(
                    {
                        **{k: obj["properties"][k] for k in ("chunk_id", "kb_id", "document_id")},
                        "_additional": {"id": oid, "score": str(fused[oid])},
                    }
                )
        return _json(200, {"data": {"Get": {cls: rows}}})


# ------------------------------------------------------------------------------ re-rankers
def _relevance(query: str, document: str) -> float:
    words = _words(query)
    return len(words & _words(document)) / len(words) if words else 0.0


class FakeCohere:
    def __init__(self, api_key: str = "cohere-test-key") -> None:
        self.api_key = api_key

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("Authorization") != f"Bearer {self.api_key}":
            return _json(401, {"message": "invalid api token"})
        body = json.loads(request.content) if request.content else {}
        if request.url.path == "/v1/check-api-key":
            return _json(200, {"valid": True, "organization_id": "org-test"})
        if request.url.path == "/v2/rerank":
            results = [
                {"index": index, "relevance_score": _relevance(body["query"], doc)}
                for index, doc in enumerate(body["documents"])
            ]
            results.sort(key=lambda item: -item["relevance_score"])
            return _json(
                200, {"id": "r-1", "results": results, "meta": {"billed_units": {"search_units": 1}}}
            )
        return _json(404, {"message": "not found"})


class FakeVoyage:
    def __init__(self, api_key: str = "voyage-test-key") -> None:
        self.api_key = api_key

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("Authorization") != f"Bearer {self.api_key}":
            return _json(401, {"detail": "Provided API key is invalid."})
        body = json.loads(request.content)
        data = [
            {"index": index, "relevance_score": _relevance(body["query"], doc)}
            for index, doc in enumerate(body["documents"])
        ]
        data.sort(key=lambda item: -item["relevance_score"])
        tokens = sum(len(doc.split()) for doc in body["documents"]) + len(body["query"].split())
        return _json(
            200, {"object": "list", "data": data, "model": body["model"], "usage": {"total_tokens": tokens}}
        )


# ------------------------------------------------------------------------------ all of them
class FakeVendors:
    """Routes a request to the fake that owns its host; records every request."""

    def __init__(self) -> None:
        self.qdrant = FakeQdrant()
        self.pinecone = FakePinecone()
        self.weaviate = FakeWeaviate()
        self.cohere = FakeCohere()
        self.voyage = FakeVoyage()
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.url.host
        if host == QDRANT_HOST:
            return self.qdrant.handle(request)
        if host == WEAVIATE_HOST:
            return self.weaviate.handle(request)
        if self.pinecone.owns(host):
            return self.pinecone.handle(request)
        if host == COHERE_HOST:
            return self.cohere.handle(request)
        if host == VOYAGE_HOST:
            return self.voyage.handle(request)
        return _json(502, {"error": f"no fake for {urlsplit(str(request.url)).hostname}"})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self.transport(), follow_redirects=False)


class HashSparse:
    """A deterministic BM25 stand-in: each lower-case word hashes to one index, weight 1.0."""

    @staticmethod
    def _vector(text: str) -> tuple[list[int], list[float]]:
        indices = sorted({abs(hash(word)) % 100_000 for word in _words(text)})
        return indices, [1.0] * len(indices)

    async def embed_documents(self, texts: list[str]) -> list[tuple[list[int], list[float]]]:
        return [self._vector(text) for text in texts]

    async def embed_query(self, text: str) -> tuple[list[int], list[float]]:
        return self._vector(text)
