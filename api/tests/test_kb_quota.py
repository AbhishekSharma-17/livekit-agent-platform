"""V5-30: the knowledge-base rows the security review handed to this package (S5-28, S5-30).

S5-28: re-index checks existence without reading bytes; a second re-index or
evaluation of the same knowledge base is a 409 while one is queued or running.
S5-30: the upload extension decides (415 otherwise), a url import's chosen
filename cannot pick another extractor, and per-KB / per-workspace quotas hold.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from lkap_api.db.models import Job, KbDocument
from lkap_api.db.session import Database
from lkap_api.jobs.kinds import KB_EVALUATE, KB_INGEST
from lkap_api.routers import knowledge
from lkap_api.routers.knowledge import import_filename, upload_media_type
from lkap_api.storage.local import LocalStorage


async def _new_kb(admin_client: httpx.AsyncClient) -> str:
    created = await admin_client.post("/v1/knowledge-bases", json={"name": "Demo — quota"})
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


async def _upload(
    admin_client: httpx.AsyncClient, kb_id: str, name: str, data: bytes, mime: str
) -> httpx.Response:
    return await admin_client.post(
        f"/v1/knowledge-bases/{kb_id}/documents", files={"file": (name, data, mime)}
    )


# --------------------------------------------------------------------------- S5-30


async def test_upload_unsupported_extension_is_415(admin_client: httpx.AsyncClient) -> None:
    kb_id = await _new_kb(admin_client)

    response = await _upload(admin_client, kb_id, "payload.exe", b"MZ fictional", "text/plain")
    no_extension = await _upload(admin_client, kb_id, "notes", b"text", "text/plain")

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_media_type"
    assert no_extension.status_code == 415
    assert (await admin_client.get(f"/v1/knowledge-bases/{kb_id}/documents")).json()["total"] == 0


async def test_the_extension_decides_the_stored_type_not_the_declared_one(
    admin_client: httpx.AsyncClient,
) -> None:
    kb_id = await _new_kb(admin_client)

    response = await _upload(admin_client, kb_id, "notes.md", b"# Notes\n\nText.", "application/pdf")

    assert response.status_code == 202, response.text
    assert response.json()["mime"] == "text/markdown"


@pytest.mark.parametrize(
    ("name", "expected"),
    [("a.MD", "text/markdown"), ("b.pdf", "application/pdf"), ("c.htm", "text/html"), ("d.csv", "text/csv")],
)
def test_upload_media_types(name: str, expected: str) -> None:
    assert upload_media_type(name) == expected


@pytest.mark.parametrize(
    ("filename", "mime", "expected"),
    [
        ("x.docx", "text/plain", "x.docx.txt"),  # a text body cannot pick the Office extractor
        ("x.pdf", "text/html", "x.pdf.html"),
        ("report.pdf", "application/pdf", "report.pdf"),
        ("page.htm", "text/html", "page.htm"),
        ("notes.md", "text/plain", "notes.md"),
        ("data.json", "text/plain", "data.json"),
    ],
)
def test_an_import_filename_cannot_pick_another_extractor(filename: str, mime: str, expected: str) -> None:
    assert import_filename("https://example.test/x", filename, mime) == expected


async def test_an_import_named_docx_over_a_text_body_is_stored_as_text(
    admin_client: httpx.AsyncClient,
) -> None:
    kb_id = await _new_kb(admin_client)

    with respx.mock:
        respx.get("https://example.test/export").respond(
            200, content=b"plain notes about flood cover", headers={"content-type": "text/plain"}
        )
        response = await admin_client.post(
            f"/v1/knowledge-bases/{kb_id}/documents/import",
            json={"url": "https://example.test/export", "filename": "x.docx"},
        )

    assert response.status_code == 201, response.text
    assert (response.json()["filename"], response.json()["mime"]) == ("x.docx.txt", "text/plain")


async def test_a_full_knowledge_base_refuses_another_document(
    admin_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(knowledge, "MAX_DOCUMENTS_PER_KB", 1)
    kb_id = await _new_kb(admin_client)

    first = await _upload(admin_client, kb_id, "a.md", b"# A\n\nText.", "text/markdown")
    second = await _upload(admin_client, kb_id, "b.md", b"# B\n\nText.", "text/markdown")

    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "quota_exceeded"
    assert second.json()["error"]["details"]["limit"] == "documents_per_kb"


async def test_the_workspace_byte_quota_spans_every_knowledge_base(
    admin_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(knowledge, "MAX_WORKSPACE_KB_BYTES", 30)
    first_kb, second_kb = await _new_kb(admin_client), await _new_kb(admin_client)

    ok = await _upload(admin_client, first_kb, "a.md", b"# A\n\n" + b"x" * 14, "text/markdown")  # 20 bytes
    full = await _upload(admin_client, second_kb, "b.md", b"# B\n\n" + b"x" * 14, "text/markdown")

    assert ok.status_code == 202
    assert full.status_code == 409 and full.json()["error"]["details"]["limit"] == "workspace_bytes"


# --------------------------------------------------------------------------- S5-28


async def test_reindex_checks_existence_without_reading_bytes(
    admin_client: httpx.AsyncClient, database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    kb_id = await _new_kb(admin_client)
    async with database.session() as session:
        session.add(
            KbDocument(kb_id=kb_id, filename="seeded.md", mime="text/markdown", bytes=10, status="ready")
        )

    async def _no_read(self: LocalStorage, key: str) -> bytes:
        raise AssertionError("re-index must not read a document's bytes to find out whether it exists")

    monkeypatch.setattr(LocalStorage, "get", _no_read)
    response = await admin_client.post(f"/v1/knowledge-bases/{kb_id}/reindex")

    assert response.status_code == 202, response.text
    assert [item["reason"] for item in response.json()["skipped"]] == ["source_not_stored"]


async def _pending_job(database: Database, kind: str, payload: dict[str, object]) -> None:
    async with database.session() as session:
        session.add(Job(kind=kind, payload=payload, status="running"))


async def test_evaluate_refuses_a_second_run_while_one_is_pending(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    kb_id = await _new_kb(admin_client)
    await _pending_job(database, KB_EVALUATE, {"kb_id": kb_id, "run_id": "r1"})

    response = await admin_client.post(f"/v1/knowledge-bases/{kb_id}/evaluate")

    assert response.status_code == 409
    assert "already running" in response.json()["error"]["message"]


async def test_reindex_refuses_a_second_run_while_one_is_pending(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    kb_id = await _new_kb(admin_client)
    other_kb = await _new_kb(admin_client)
    # An upload's ingest job does not block a re-index; another knowledge base's re-index neither.
    await _pending_job(database, KB_INGEST, {"kb_id": kb_id, "document_id": "d1"})
    await _pending_job(database, KB_INGEST, {"kb_id": other_kb, "document_id": "d2", "reindex": True})
    assert (await admin_client.post(f"/v1/knowledge-bases/{kb_id}/reindex")).status_code == 202

    await _pending_job(database, KB_INGEST, {"kb_id": kb_id, "document_id": "d3", "reindex": True})
    response = await admin_client.post(f"/v1/knowledge-bases/{kb_id}/reindex")

    assert response.status_code == 409


async def test_reindex_and_evaluate_share_a_per_workspace_rate_limit(
    admin_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(knowledge, "KB_JOBS_PER_MIN", 2)
    kb_id = await _new_kb(admin_client)

    statuses = [
        (await admin_client.post(f"/v1/knowledge-bases/{kb_id}/reindex")).status_code for _ in range(3)
    ]

    assert statuses == [202, 202, 429]
