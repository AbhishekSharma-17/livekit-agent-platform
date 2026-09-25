"""V4-18 (R-V4-65, ask #127): a starter's knowledge seeds are ingested by `kb_ingest` jobs after the commit.

The #127 wedge: creating an agent from ``receptionist`` on a cold fastembed
cache held SQLite's write lock through a ~90 s model download, so the create
never returned and every other writer (worker heartbeats, sweeps) failed with
``database is locked``. Here the embedder's model load blocks on an
:class:`asyncio.Event` for as long as the test likes; the create must still
answer 201 within a second, with the knowledge bases and ``pending`` documents
committed and one job per seed file queued, and a worker heartbeat must
succeed while the load is blocked. Once released, the jobs make the documents
``ready``.

The request is driven as raw ASGI (``test_commit_before_response._call``):
``httpx.ASGITransport`` also waits for the response's background tasks — the
inline jobs — before ``post()`` returns, which would hide the timing.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Sequence

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from test_commit_before_response import _call

from lkap_api.db.models import AuditLog, Job, KbDocument, KnowledgeBase
from lkap_api.db.session import Database
from lkap_api.jobs.kinds import KB_INGEST
from lkap_api.kb.embed import FakeEmbedder
from lkap_api.kb.ingest import upload_storage_key
from lkap_api.settings import Settings
from lkap_api.storage.resolve import default_storage
from lkap_api.templates.catalog import load_catalog

TEMPLATE_ID = "receptionist"
WORKER_KEY = "host:v418"


class BlockedEmbedder(FakeEmbedder):
    """A `FakeEmbedder` whose model "load" blocks until ``release`` is set (a cold-cache download)."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.loaded = False

    async def _get_model(self) -> None:
        self.entered.set()
        await self.release.wait()
        self.loaded = True

    async def warm(self) -> None:
        await self._get_model()

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        await self._get_model()
        return await super().embed(texts)


@pytest.fixture
def blocked(monkeypatch: pytest.MonkeyPatch) -> BlockedEmbedder:
    """One blocked embedder, returned by both the router's and the job's `resolve_embedder`."""
    embedder = BlockedEmbedder()

    async def _resolve(*_args: object, **_kwargs: object) -> BlockedEmbedder:
        return embedder

    monkeypatch.setattr("lkap_api.routers.agents.resolve_embedder", _resolve)
    monkeypatch.setattr("lkap_api.kb.ingest.resolve_embedder", _resolve)
    return embedder


def _seed_files() -> list[str]:
    [template] = [t for t in load_catalog() if t.id == TEMPLATE_ID]
    return [file for seed in template.kb_seeds for file in seed.files]


async def _documents(database: Database) -> list[KbDocument]:
    async with database.session() as session:
        return list((await session.execute(select(KbDocument))).scalars().all())


async def _ingest_jobs(database: Database) -> list[Job]:
    async with database.session() as session:
        return list((await session.execute(select(Job).where(Job.kind == KB_INGEST))).scalars().all())


async def test_template_create_returns_before_the_embedder_loads_and_the_jobs_ingest_after(
    app: FastAPI,
    database: Database,
    settings: Settings,
    service_client: httpx.AsyncClient,
    admin_client: httpx.AsyncClient,
    blocked: BlockedEmbedder,
) -> None:
    registered = await service_client.post(
        "/internal/v1/workers/register",
        json={
            "connection_id": None,
            "instance_key": WORKER_KEY,
            "image": "slim",
            "sdk_version": "1.8.3",
            "installed_provider_ids": [],
            "pack_ids": ["generic"],
            "managed_by": "external",
        },
    )
    assert registered.status_code == 200, registered.text
    files = _seed_files()
    assert files, "the receptionist starter must seed at least one file"
    at_response: dict[str, object] = {}
    during_embed: dict[str, object] = {}

    async def heartbeat_while_blocked() -> None:
        await asyncio.wait_for(blocked.entered.wait(), timeout=5)
        response = await service_client.post(
            f"/internal/v1/workers/{WORKER_KEY}/heartbeat", json={"status": "ready", "active_jobs": 0}
        )
        during_embed["heartbeat"] = response.status_code
        during_embed["statuses"] = {document.status for document in await _documents(database)}
        during_embed["released"] = blocked.release.is_set()
        blocked.release.set()

    tasks: list[asyncio.Task[None]] = []
    started = time.perf_counter()

    async def on_response_start() -> None:
        at_response["elapsed_s"] = time.perf_counter() - started
        async with database.session() as session:
            at_response["kbs"] = len((await session.execute(select(KnowledgeBase))).scalars().all())
        at_response["documents"] = sorted((d.filename, d.status) for d in await _documents(database))
        at_response["jobs"] = [job.status for job in await _ingest_jobs(database)]
        tasks.append(asyncio.create_task(heartbeat_while_blocked()))

    body = json.dumps({"name": "Front desk", "template_id": TEMPLATE_ID}).encode()
    status = await asyncio.wait_for(
        _call(app, "POST", "/v1/agents", settings.admin_token, on_response_start, body), timeout=30
    )
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=10)

    # The create answered before any model load finished, with everything committed.
    assert status == 201
    assert isinstance(at_response["elapsed_s"], float) and at_response["elapsed_s"] < 1.0, at_response
    assert at_response["kbs"] == 1
    assert at_response["documents"] == sorted((file, "pending") for file in files)
    assert len(at_response["jobs"]) == len(files)  # type: ignore[arg-type]
    # A concurrent writer (the worker heartbeat) succeeded while the load was blocked.
    assert during_embed == {"heartbeat": 204, "statuses": {"pending"}, "released": False}
    # Released, the jobs embedded the seeds.
    assert blocked.loaded
    documents = await _documents(database)
    assert {(d.filename, d.status) for d in documents} == {(file, "ready") for file in files}
    assert all(d.chunk_count > 0 for d in documents)
    jobs = await _ingest_jobs(database)
    assert {job.status for job in jobs} == {"done"}
    storage = default_storage(settings)
    by_id = {document.id: document for document in documents}
    for job in jobs:
        document = by_id[job.payload["document_id"]]
        assert job.payload["origin"] == f"template:{TEMPLATE_ID}"
        assert job.payload["storage_key"] == upload_storage_key(
            document.kb_id, document.id, document.filename
        )
        assert await storage.get(job.payload["storage_key"])
    async with database.session() as session:
        audits = (
            (await session.execute(select(AuditLog).where(AuditLog.action == "kb.seed_ingest")))
            .scalars()
            .all()
        )
    assert sorted(audit.target_id or "" for audit in audits) == sorted(by_id)
    assert {audit.payload["origin"] for audit in audits} == {f"template:{TEMPLATE_ID}"}
    assert {(audit.actor_type, audit.payload["status"]) for audit in audits} == {("system", "ready")}

    # A second create of the same starter reuses the knowledge base and enqueues nothing.
    second = await admin_client.post("/v1/agents", json={"name": "Front desk 2", "template_id": TEMPLATE_ID})
    assert second.status_code == 201, second.text
    assert len(await _ingest_jobs(database)) == len(files)
    assert len(await _documents(database)) == len(files)


async def test_a_failed_model_load_marks_the_seed_documents_failed_without_failing_the_create(
    admin_client: httpx.AsyncClient, database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    class BrokenEmbedder(FakeEmbedder):
        async def warm(self) -> None:
            raise OSError("no network")

    async def _resolve(*_args: object, **_kwargs: object) -> BrokenEmbedder:
        return BrokenEmbedder()

    monkeypatch.setattr("lkap_api.routers.agents.resolve_embedder", _resolve)
    monkeypatch.setattr("lkap_api.kb.ingest.resolve_embedder", _resolve)

    created = await admin_client.post("/v1/agents", json={"name": "Front desk", "template_id": TEMPLATE_ID})

    assert created.status_code == 201, created.text
    documents = await _documents(database)
    assert documents and {d.status for d in documents} == {"failed"}
    assert all(d.error == "the embedding model could not be loaded (OSError)" for d in documents)
    async with database.session() as session:
        audits = (
            (await session.execute(select(AuditLog).where(AuditLog.action == "kb.seed_ingest")))
            .scalars()
            .all()
        )
    assert {audit.payload["status"] for audit in audits} == {"failed"}
