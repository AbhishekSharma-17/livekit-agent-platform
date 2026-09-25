"""V5-01 through the api: embedder record and mismatch, locators, progress, evals, re-index.

Also pins compatibility: a knowledge base created before V5-01 (``NULL``
dimension/model/chunking, chunks whose ``meta`` is ``{"filename"}`` only, no
``progress``) keeps searching, listing and accepting uploads exactly as before.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from auth_helpers import make_workspace
from conftest import inference_config
from fastapi import FastAPI
from sqlalchemy import select, update

from lkap_api.db.models import KbChunk, KbDocument, KbEval, KnowledgeBase, new_id
from lkap_api.db.session import Database
from lkap_api.kb.embed import FAKE_EMBEDDER_MODEL_ID, FakeEmbedder
from lkap_api.kb.store import VectorRecord, get_lancedb_store
from lkap_api.routers.knowledge import get_embedder
from lkap_api.settings import Settings

FIXTURES = Path(__file__).parent / "fixtures" / "kb"
MIMES = {
    ".md": "text/markdown",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".html": "text/html",
}


async def _fake_resolve_embedder(*args: object, **kwargs: object) -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture(autouse=True)
def _fake_embedder(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    app.dependency_overrides[get_embedder] = lambda: FakeEmbedder()
    monkeypatch.setattr("lkap_api.kb.ingest.resolve_embedder", _fake_resolve_embedder)
    yield
    app.dependency_overrides.pop(get_embedder, None)


async def _create_kb(admin_client: httpx.AsyncClient, name: str = "Harbor Lane policies") -> dict[str, Any]:
    response = await admin_client.post("/v1/knowledge-bases", json={"name": name})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def _upload_fixture(admin_client: httpx.AsyncClient, kb_id: str, name: str) -> dict[str, Any]:
    data = (FIXTURES / name).read_bytes()
    response = await admin_client.post(
        f"/v1/knowledge-bases/{kb_id}/documents",
        files={"file": (name, data, MIMES[Path(name).suffix])},
    )
    assert response.status_code == 202, response.text
    body: dict[str, Any] = response.json()
    return body


async def _chunk_metas(database: Database, document_id: str) -> list[dict[str, Any]]:
    async with database.session() as session:
        rows = (
            await session.execute(
                select(KbChunk).where(KbChunk.document_id == document_id).order_by(KbChunk.ordinal)
            )
        ).scalars()
        return [{"text": row.text, **row.meta} for row in rows]


# --------------------------------------------------------------------------- embedder record
async def test_create_kb_records_the_embedder_and_the_default_chunking(
    admin_client: httpx.AsyncClient,
) -> None:
    kb = await _create_kb(admin_client)
    assert kb["dimension"] == 32
    assert kb["embedder_model"] == FAKE_EMBEDDER_MODEL_ID
    assert kb["chunking"] == {"max_tokens": 256, "overlap": 32}
    listed = (await admin_client.get("/v1/knowledge-bases")).json()
    assert listed["items"][0]["dimension"] == 32


async def test_search_on_a_kb_built_by_another_embedder_is_a_422_naming_it(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient, database: Database
) -> None:
    kb = await _create_kb(admin_client, name="Wide vectors")
    async with database.session() as session:
        await session.execute(
            update(KnowledgeBase)
            .where(KnowledgeBase.id == kb["id"])
            .values(dimension=1536, embedder_model="text-embedding-3-small")
        )

    admin = await admin_client.post(f"/v1/knowledge-bases/{kb['id']}/search", json={"query": "x", "k": 4})
    assert admin.status_code == 422, admin.text
    body = admin.json()
    assert body["error"]["code"] == "kb_embedder_mismatch"
    assert "Wide vectors" in body["error"]["message"]
    assert body["error"]["details"]["kb_dimension"] == 1536
    assert body["error"]["details"]["embedder_dimension"] == 32

    # V5-04 (ask #13 b): the worker's cross-KB search skips that knowledge base
    # with a warning naming it instead of refusing the whole search.
    internal = await service_client.post(
        "/internal/v1/kb/search", json={"kb_ids": [kb["id"]], "query": "x", "k": 4}
    )
    assert internal.status_code == 200, internal.text
    body = internal.json()
    assert body["hits"] == []
    [warning] = body["warnings"]
    assert (warning["code"], warning["kb_id"]) == ("kb_embedder_mismatch", kb["id"])
    assert "Wide vectors" in warning["message"]


async def test_internal_search_ignores_unknown_kb_ids(service_client: httpx.AsyncClient) -> None:
    response = await service_client.post(
        "/internal/v1/kb/search", json={"kb_ids": ["no-such-kb"], "query": "x", "k": 4}
    )
    assert response.status_code == 200
    assert response.json()["hits"] == []


# --------------------------------------------------------------------------- locators and progress
async def test_uploaded_markdown_gets_heading_path_offsets_and_progress(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    kb = await _create_kb(admin_client)
    uploaded = await _upload_fixture(admin_client, kb["id"], "policy_guide.md")
    assert uploaded["progress"] == 0.0

    documents = (await admin_client.get(f"/v1/knowledge-bases/{kb['id']}/documents")).json()["items"]
    assert documents[0]["status"] == "ready"
    assert documents[0]["progress"] == 1.0
    metas = await _chunk_metas(database, uploaded["id"])
    assert len(metas) == 7
    source = (FIXTURES / "policy_guide.md").read_text()
    for meta in metas:
        assert meta["filename"] == "policy_guide.md"
        assert meta["heading_path"][0] == "Harbor Lane Mutual policy guide"
        assert meta["page"] is None
        assert source.strip()[meta["char_start"] : meta["char_end"]] == meta["text"]

    hits = (
        await admin_client.post(
            f"/v1/knowledge-bases/{kb['id']}/search", json={"query": "sewer backup rider", "k": 1}
        )
    ).json()["hits"]
    assert "backup rider" in hits[0]["text"]


async def test_uploaded_pdf_docx_and_html_carry_pages_headings_and_no_markup(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    kb = await _create_kb(admin_client)
    pdf = await _upload_fixture(admin_client, kb["id"], "policy_summary.pdf")
    docx = await _upload_fixture(admin_client, kb["id"], "claims_handbook.docx")
    html = await _upload_fixture(admin_client, kb["id"], "claims_faq.html")

    assert [meta["page"] for meta in await _chunk_metas(database, pdf["id"])] == [1, 2, 3]
    docx_paths = [meta["heading_path"] for meta in await _chunk_metas(database, docx["id"])]
    assert ["Claims handbook", "Reporting a loss", "Water damage"] in docx_paths
    html_metas = await _chunk_metas(database, html["id"])
    assert html_metas and all("<" not in meta["text"] for meta in html_metas)
    assert ["Claims FAQ", "What documents should I send?"] in [meta["heading_path"] for meta in html_metas]

    documents = (await admin_client.get(f"/v1/knowledge-bases/{kb['id']}/documents")).json()["items"]
    assert {document["status"] for document in documents} == {"ready"}


async def test_a_long_upload_commits_progress_mid_ingest_through_its_own_session(
    admin_client: httpx.AsyncClient, database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The job's progress writer commits on a second connection while the ingest session is open."""
    from lkap_api.kb import ingest

    seen: list[float | None] = []
    real_writer = ingest.progress_writer

    def spying_writer(db: Database, document_id: str) -> ingest.ProgressCallback:
        write = real_writer(db, document_id)

        async def spy(done: int, total: int) -> None:
            await write(done, total)
            async with database.session() as session:
                document = await session.get(KbDocument, document_id)
                seen.append(document.progress if document is not None else None)

        return spy

    monkeypatch.setattr(ingest, "progress_writer", spying_writer)
    kb = await _create_kb(admin_client)
    body = "\n\n".join(f"## Section {i}\n\nSection {i} covers exactly one thing." for i in range(60))
    response = await admin_client.post(
        f"/v1/knowledge-bases/{kb['id']}/documents",
        files={"file": ("long.md", body.encode(), "text/markdown")},
    )
    assert response.status_code == 202, response.text

    [document] = (await admin_client.get(f"/v1/knowledge-bases/{kb['id']}/documents")).json()["items"]
    assert (document["status"], document["chunk_count"], document["progress"]) == ("ready", 60, 1.0)
    assert seen == [pytest.approx(50 / 60, abs=1e-4)]


# --------------------------------------------------------------------------- compatibility pin
async def _legacy_kb(database: Database, settings: Settings) -> tuple[str, str]:
    """A knowledge base exactly as V4 left it: no embedder record, old-style chunk meta, no progress."""
    kb_id, document_id = new_id(), new_id()
    chunk_ids = [new_id(), new_id()]
    texts = ["Flood damage is covered under the HO-4 line.", "Tell them to\ncontact emergency services"]
    async with database.session() as session:
        session.add(KnowledgeBase(id=kb_id, name="Insurance policy lines", embedder_id="fastembed-embedding"))
        await session.flush()
        session.add(
            KbDocument(
                id=document_id,
                kb_id=kb_id,
                filename="policy_lines.md",
                mime="text/markdown",
                bytes=100,
                status="ready",
                chunk_count=2,
            )
        )
        await session.flush()
        for ordinal, (chunk_id, text) in enumerate(zip(chunk_ids, texts, strict=True)):
            session.add(
                KbChunk(
                    id=chunk_id,
                    kb_id=kb_id,
                    document_id=document_id,
                    ordinal=ordinal,
                    text=text,
                    meta={"filename": "policy_lines.md"},
                )
            )
        await session.execute(update(KnowledgeBase).where(KnowledgeBase.id == kb_id).values(chunk_count=2))
    vectors = await FakeEmbedder().embed(texts)
    await get_lancedb_store(settings.data_dir).upsert(
        kb_id,
        [
            VectorRecord(id=chunk_id, vector=vector, document_id=document_id)
            for chunk_id, vector in zip(chunk_ids, vectors, strict=True)
        ],
    )
    return kb_id, document_id


async def test_a_kb_created_before_v5_01_keeps_working_unchanged(
    admin_client: httpx.AsyncClient,
    service_client: httpx.AsyncClient,
    database: Database,
    settings: Settings,
) -> None:
    kb_id, document_id = await _legacy_kb(database, settings)

    fetched = (await admin_client.get(f"/v1/knowledge-bases/{kb_id}")).json()
    assert (fetched["dimension"], fetched["embedder_model"], fetched["chunking"]) == (None, None, None)
    assert fetched["chunk_count"] == 2
    documents = (await admin_client.get(f"/v1/knowledge-bases/{kb_id}/documents")).json()["items"]
    assert [(d["id"], d["status"], d["progress"]) for d in documents] == [(document_id, "ready", None)]

    # Both search paths answer from the old chunks; nothing is refused or re-chunked.
    admin = await admin_client.post(
        f"/v1/knowledge-bases/{kb_id}/search", json={"query": "flood HO-4", "k": 2}
    )
    internal = await service_client.post(
        "/internal/v1/kb/search", json={"kb_ids": [kb_id], "query": "flood HO-4", "k": 2}
    )
    for response in (admin, internal):
        assert response.status_code == 200, response.text
        assert response.json()["hits"][0]["text"] == "Flood damage is covered under the HO-4 line."
    assert [
        meta["heading_path"] if "heading_path" in meta else None
        for meta in await _chunk_metas(database, document_id)
    ] == [
        None,
        None,
    ]

    # An agent attached to it saves exactly as before.
    config = json.loads(inference_config().model_dump_json())
    config["knowledge"]["kb_ids"] = [kb_id]
    created = await admin_client.post("/v1/agents", json={"name": "Legacy KB agent", "config": config})
    assert created.status_code == 201, created.text
    assert created.json()["config"]["knowledge"]["kb_ids"] == [kb_id]

    # A new upload is chunked the new way, and the KB adopts the embedder record then.
    uploaded = await _upload_fixture(admin_client, kb_id, "policy_guide.md")
    assert (await _chunk_metas(database, uploaded["id"]))[0]["heading_path"]
    fetched = (await admin_client.get(f"/v1/knowledge-bases/{kb_id}")).json()
    assert (fetched["dimension"], fetched["embedder_model"]) == (32, FAKE_EMBEDDER_MODEL_ID)
    assert len(await _chunk_metas(database, document_id)) == 2  # the old document is untouched


# --------------------------------------------------------------------------- evals
async def test_evals_round_trip_in_order(admin_client: httpx.AsyncClient) -> None:
    kb = await _create_kb(admin_client)
    document = await _upload_fixture(admin_client, kb["id"], "policy_guide.md")
    assert (await admin_client.get(f"/v1/knowledge-bases/{kb['id']}/evals")).json() == {
        "items": [],
        "total": 0,
    }

    items = [
        {"question": "Is a burst pipe covered?", "expected_document_id": document["id"], "tags": ["water"]},
        {"question": "What does the backup rider cost?", "expected_text": "forty dollars"},
        {
            "question": "How are claims paid?",
            "expected_document_id": document["id"],
            "expected_text": "bank transfer",
            "tags": [" payments ", "faq"],
        },
    ]
    put = await admin_client.put(f"/v1/knowledge-bases/{kb['id']}/evals", json={"items": items})
    assert put.status_code == 200, put.text
    got = (await admin_client.get(f"/v1/knowledge-bases/{kb['id']}/evals")).json()
    assert got["total"] == 3
    assert [item["question"] for item in got["items"]] == [item["question"] for item in items]
    assert got["items"][2]["tags"] == ["payments", "faq"]
    assert got["items"][1]["expected_document_id"] is None
    assert got == put.json()

    cleared = await admin_client.put(f"/v1/knowledge-bases/{kb['id']}/evals", json={"items": []})
    assert cleared.json() == {"items": [], "total": 0}


@pytest.mark.parametrize(
    "item",
    [
        {"question": "Nothing expected?"},
        {"question": "", "expected_text": "x"},
        {"question": "Extra field", "expected_text": "x", "score": 1},
    ],
)
async def test_put_evals_rejects_an_invalid_item(
    admin_client: httpx.AsyncClient, item: dict[str, Any]
) -> None:
    kb = await _create_kb(admin_client)
    response = await admin_client.put(f"/v1/knowledge-bases/{kb['id']}/evals", json={"items": [item]})
    assert response.status_code == 422


async def test_put_evals_rejects_a_document_of_another_kb(admin_client: httpx.AsyncClient) -> None:
    kb = await _create_kb(admin_client)
    other = await _create_kb(admin_client, name="Other")
    foreign = await _upload_fixture(admin_client, other["id"], "policy_guide.md")
    response = await admin_client.put(
        f"/v1/knowledge-bases/{kb['id']}/evals",
        json={"items": [{"question": "q", "expected_document_id": foreign["id"]}]},
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"]["unknown"] == [foreign["id"]]


async def test_evals_are_workspace_scoped(admin_client: httpx.AsyncClient, database: Database) -> None:
    other_workspace = await make_workspace(database, "globex")
    async with database.session() as session:
        foreign = KnowledgeBase(workspace_id=other_workspace, name="Globex policies")
        session.add(foreign)
        await session.flush()
        session.add(KbEval(kb_id=foreign.id, question="secret?", expected_text="x", tags=[]))
        foreign_id = foreign.id

    assert (await admin_client.get(f"/v1/knowledge-bases/{foreign_id}/evals")).status_code == 404
    put = await admin_client.put(
        f"/v1/knowledge-bases/{foreign_id}/evals", json={"items": [{"question": "q", "expected_text": "x"}]}
    )
    assert put.status_code == 404
    async with database.session() as session:
        questions = (
            await session.execute(select(KbEval.question).where(KbEval.kb_id == foreign_id))
        ).scalars()
        assert list(questions) == ["secret?"]


async def test_deleting_a_kb_deletes_its_evals(admin_client: httpx.AsyncClient, database: Database) -> None:
    kb = await _create_kb(admin_client)
    await admin_client.put(
        f"/v1/knowledge-bases/{kb['id']}/evals", json={"items": [{"question": "q", "expected_text": "x"}]}
    )
    assert (await admin_client.delete(f"/v1/knowledge-bases/{kb['id']}")).status_code == 204
    async with database.session() as session:
        assert (await session.execute(select(KbEval).where(KbEval.kb_id == kb["id"]))).first() is None


# --------------------------------------------------------------------------- re-index
async def test_reindex_rechunks_stored_documents_and_skips_the_rest(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    kb = await _create_kb(admin_client)
    stored = await _upload_fixture(admin_client, kb["id"], "policy_guide.md")
    seeded_id, pending_id = new_id(), new_id()
    async with database.session() as session:
        # Simulate chunks written before V5-01 on the stored document.
        await session.execute(
            update(KbChunk)
            .where(KbChunk.document_id == stored["id"])
            .values(meta={"filename": "policy_guide.md"})
        )
        # A seeded document (never put in storage) and one still being ingested.
        session.add(
            KbDocument(
                id=seeded_id,
                kb_id=kb["id"],
                filename="seed.md",
                mime="text/markdown",
                bytes=1,
                status="ready",
            )
        )
        session.add(
            KbDocument(
                id=pending_id,
                kb_id=kb["id"],
                filename="new.md",
                mime="text/markdown",
                bytes=1,
                status="pending",
            )
        )

    response = await admin_client.post(f"/v1/knowledge-bases/{kb['id']}/reindex")
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["queued"] == [stored["id"]]
    assert sorted((item["document_id"], item["reason"]) for item in body["skipped"]) == sorted(
        [(seeded_id, "source_not_stored"), (pending_id, "ingest_in_progress")]
    )

    metas = await _chunk_metas(database, stored["id"])
    assert len(metas) == 7
    assert all(meta["heading_path"] for meta in metas)
    documents = {
        d["id"]: d
        for d in (await admin_client.get(f"/v1/knowledge-bases/{kb['id']}/documents")).json()["items"]
    }
    assert (documents[stored["id"]]["status"], documents[stored["id"]]["progress"]) == ("ready", 1.0)
    assert documents[seeded_id]["status"] == "ready"


async def test_reindex_selected_documents_and_unknown_ids(admin_client: httpx.AsyncClient) -> None:
    kb = await _create_kb(admin_client)
    first = await _upload_fixture(admin_client, kb["id"], "policy_guide.md")
    await _upload_fixture(admin_client, kb["id"], "claims_faq.html")

    selected = await admin_client.post(
        f"/v1/knowledge-bases/{kb['id']}/reindex", json={"document_ids": [first["id"]]}
    )
    assert selected.status_code == 202
    assert selected.json() == {"queued": [first["id"]], "skipped": []}

    unknown = await admin_client.post(
        f"/v1/knowledge-bases/{kb['id']}/reindex", json={"document_ids": ["nope"]}
    )
    assert unknown.status_code == 404
