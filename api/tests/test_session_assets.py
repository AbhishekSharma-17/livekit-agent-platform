"""V5-19: stored session files (uploads, frames, copied KB documents), signed downloads, retention.

Every route is exercised through the app; the storage backend is the local one
under the test's ``LKAP_DATA_DIR``. No network, no paid calls.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from alembic.config import Config
from auth_helpers import make_workspace
from conftest import captured_text, inference_config
from connection_fakes import add_agent
from lkap_contracts.agent_config import KnowledgeConfig, PanelLayout, RecordingConfig
from lkap_contracts.ui_protocol import BlockSpec
from sqlalchemy import select

from alembic import command
from lkap_api.db.models import KbDocument, KnowledgeBase, SessionAsset
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.session import Database
from lkap_api.kb.ingest import upload_storage_key
from lkap_api.session_assets import service as assets_service
from lkap_api.session_assets import sweep_session_assets
from lkap_api.settings import Settings
from lkap_api.storage.resolve import default_storage

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + bytes(range(64))
JPEG = b"\xff\xd8\xff\xe0" + bytes(range(200))
PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
HTML = b"<html><body><script>alert(1)</script></body></html>"
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"></svg>'

SESSION_ID = "a" * 32


@dataclass
class World:
    """One active session whose panel has an upload block and a form, and two knowledge bases."""

    session_id: str
    agent_id: str
    doc_id: str
    markdown_doc_id: str
    html_doc_id: str
    foreign_doc_id: str


def _panel() -> PanelLayout:
    return PanelLayout(
        blocks=[
            BlockSpec(
                id="docs",
                type="upload",
                config={"accept": ["image/*"], "max_files": 3, "max_bytes": 1000},
            ),
            BlockSpec(id="intake", type="form"),
            BlockSpec(id="notes", type="notes"),
        ]
    )


async def _document(session: Any, storage: Any, kb_id: str, filename: str, mime: str, data: bytes) -> str:
    doc = KbDocument(kb_id=kb_id, filename=filename, mime=mime, bytes=len(data), status="ready")
    session.add(doc)
    await session.flush()
    await storage.put(upload_storage_key(kb_id, doc.id, filename), data, content_type=mime)
    return str(doc.id)


@pytest.fixture
async def world(database: Database, settings: Settings) -> World:
    storage = default_storage(settings)
    async with database.session() as session:
        kb = KnowledgeBase(name="Policies")
        other = KnowledgeBase(name="Not this agent's")
        session.add_all([kb, other])
        await session.flush()
        config = inference_config(
            panel=_panel(),
            knowledge=KnowledgeConfig(kb_ids=[kb.id]),
            recording=RecordingConfig(retention_days=7),
        )
        agent = await add_agent(session, config, connection_id=None)
        session.add(
            SessionRow(
                id=SESSION_ID,
                agent_id=agent.id,
                config_version=1,
                room_name="lkap-assets",
                participant_identity="caller",
                participant_name="Caller",
                status="active",
                pipeline_mode="cascaded",
            )
        )
        doc_id = await _document(session, storage, kb.id, "policy.pdf", "application/pdf", PDF)
        md_id = await _document(session, storage, kb.id, "faq.md", "text/markdown", b"# FAQ\nCovered.\n")
        html_id = await _document(session, storage, kb.id, "page.html", "text/html", HTML)
        foreign_id = await _document(session, storage, other.id, "secret.pdf", "application/pdf", PDF)
        return World(SESSION_ID, agent.id, doc_id, md_id, html_id, foreign_id)


async def _upload(
    client: httpx.AsyncClient,
    data: bytes,
    *,
    name: str = "photo.png",
    kind: str = "upload",
    meta: dict[str, Any] | None = None,
    content_type: str = "image/png",
    session_id: str = SESSION_ID,
) -> httpx.Response:
    form: dict[str, str] = {"kind": kind}
    if meta is not None:
        form["meta"] = json.dumps(meta)
    return await client.post(
        f"/internal/v1/sessions/{session_id}/assets",
        files={"file": (name, data, content_type)},
        data=form,
    )


async def _rows(database: Database) -> list[SessionAsset]:
    async with database.session() as session:
        return list((await session.execute(select(SessionAsset).order_by(SessionAsset.created_at))).scalars())


# ------------------------------------------------------------------ uploads


async def test_three_uploads_within_limits_store_three_files_with_matching_sha256(
    service_client: httpx.AsyncClient, world: World, database: Database, settings: Settings
) -> None:
    files = [PNG, JPEG, PNG + b"x"]
    for i, data in enumerate(files):
        response = await _upload(service_client, data, name=f"damage-{i}.png", meta={"block_id": "docs"})
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["sha256"] == hashlib.sha256(data).hexdigest()
        assert body["size"] == len(data)
        assert body["meta"] == {"block_id": "docs"}
    rows = await _rows(database)
    assert [r.mime for r in rows] == ["image/png", "image/jpeg", "image/png"]
    storage = default_storage(settings)
    for row, data in zip(rows, files, strict=True):
        assert row.storage_key.startswith(f"sessions/{SESSION_ID}/")
        assert row.workspace_id == "00000000000000000000000000000001"
        assert await storage.get(row.storage_key) == data


async def test_the_type_is_sniffed_from_the_bytes_never_the_name_or_declared_type(
    service_client: httpx.AsyncClient, world: World
) -> None:
    for payload in (HTML, SVG):
        response = await _upload(service_client, payload, name="photo.png", meta={"block_id": "docs"})
        assert response.status_code == 415, response.text
    # A real JPEG declared as text is stored as the JPEG it is.
    response = await _upload(
        service_client, JPEG, name="notes.txt", content_type="text/plain", meta={"block_id": "docs"}
    )
    assert response.status_code == 201
    assert response.json()["mime"] == "image/jpeg"


@pytest.mark.parametrize(
    ("data", "meta", "kind", "status"),
    [
        (PDF, {"block_id": "docs"}, "upload", 415),  # the block accepts images only
        (PNG + bytes(2000), {"block_id": "docs"}, "upload", 413),  # over the block's 1000 bytes
        (b"", {"block_id": "docs"}, "upload", 422),
        (PNG, None, "upload", 422),  # an upload names its block
        (PNG, {"block_id": "nope"}, "upload", 422),
        (PNG, {"block_id": "notes"}, "upload", 422),  # not an upload or form block
        (PNG, {"block_id": "docs"}, "document", 422),  # copies come from from-document only
        (PDF, None, "frame", 415),  # a frame is an image
    ],
)
async def test_the_api_rechecks_every_limit(
    service_client: httpx.AsyncClient,
    world: World,
    database: Database,
    data: bytes,
    meta: dict[str, Any] | None,
    kind: str,
    status: int,
) -> None:
    response = await _upload(service_client, data, meta=meta, kind=kind)
    assert response.status_code == status, response.text
    assert await _rows(database) == []


async def test_a_form_file_field_falls_back_to_the_platform_limits(
    service_client: httpx.AsyncClient, world: World
) -> None:
    response = await _upload(
        service_client, PDF, name="claim.pdf", meta={"block_id": "intake", "field": "claim"}
    )
    assert response.status_code == 201, response.text
    assert response.json()["meta"] == {"block_id": "intake", "field": "claim"}


async def test_path_parts_and_unknown_meta_never_reach_the_store(
    service_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    response = await _upload(
        service_client,
        PNG,
        name="../../etc/x<>.png",
        meta={"block_id": "docs", "document_id": "spoofed", "caption": "c" * 500},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "x.png"
    assert "document_id" not in body["meta"]
    assert len(body["meta"]["caption"]) == 200
    (row,) = await _rows(database)
    assert ".." not in row.storage_key and "x.png" not in row.storage_key


async def test_a_frame_is_stored_without_a_block(service_client: httpx.AsyncClient, world: World) -> None:
    response = await _upload(service_client, JPEG, name="..", kind="frame", meta={"source": "camera"})
    assert response.status_code == 201, response.text
    assert response.json()["kind"] == "frame"
    assert response.json()["name"] == "frame.jpg"


async def test_the_session_caps_hold(
    service_client: httpx.AsyncClient, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(assets_service, "MAX_ASSETS_PER_SESSION", 2)
    for _ in range(2):
        assert (await _upload(service_client, PNG, meta={"block_id": "docs"})).status_code == 201
    assert (await _upload(service_client, PNG, meta={"block_id": "docs"})).status_code == 409


async def test_an_ended_session_takes_no_files(
    service_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    async with database.session() as session:
        row = await session.get(SessionRow, SESSION_ID)
        assert row is not None
        row.status = "ended"
    assert (await _upload(service_client, PNG, meta={"block_id": "docs"})).status_code == 409


async def test_the_worker_routes_need_the_service_token(client: httpx.AsyncClient, world: World) -> None:
    assert (await _upload(client, PNG, meta={"block_id": "docs"})).status_code == 401
    assert (
        await client.post(
            f"/internal/v1/sessions/{SESSION_ID}/assets/from-document", json={"document_id": "x"}
        )
    ).status_code == 401
    assert (await client.get(f"/internal/v1/sessions/{SESSION_ID}/assets/x/content")).status_code == 401


async def test_the_filename_is_never_logged(
    service_client: httpx.AsyncClient, world: World, log_capture: pytest.LogCaptureFixture
) -> None:
    response = await _upload(service_client, PNG, name="jane-doe-passport.png", meta={"block_id": "docs"})
    assert response.status_code == 201
    assert "jane-doe-passport" not in captured_text(log_capture)


# ------------------------------------------------------------------ from-document (R-V5-5)


async def test_from_document_copies_a_cited_document_once(
    service_client: httpx.AsyncClient, world: World, database: Database, settings: Settings
) -> None:
    url = f"/internal/v1/sessions/{SESSION_ID}/assets/from-document"
    first = await service_client.post(url, json={"document_id": world.doc_id})
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["kind"] == "document"
    assert body["mime"] == "application/pdf"
    assert body["meta"] == {"document_id": world.doc_id}
    assert body["sha256"] == hashlib.sha256(PDF).hexdigest()
    again = await service_client.post(url, json={"document_id": world.doc_id})
    assert again.status_code == 200
    assert again.json()["id"] == body["id"]
    assert len(await _rows(database)) == 1
    content = await service_client.get(f"/internal/v1/sessions/{SESSION_ID}/assets/{body['id']}/content")
    assert content.status_code == 200 and content.content == PDF


async def test_from_document_refuses_a_document_outside_the_agents_knowledge_bases(
    service_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    url = f"/internal/v1/sessions/{SESSION_ID}/assets/from-document"
    assert (await service_client.post(url, json={"document_id": world.foreign_doc_id})).status_code == 404
    assert (await service_client.post(url, json={"document_id": "0" * 32})).status_code == 404
    assert await _rows(database) == []


async def test_from_document_serves_markdown_as_text_and_refuses_html(
    service_client: httpx.AsyncClient, world: World
) -> None:
    url = f"/internal/v1/sessions/{SESSION_ID}/assets/from-document"
    markdown = await service_client.post(url, json={"document_id": world.markdown_doc_id})
    assert markdown.status_code == 201, markdown.text
    assert markdown.json()["mime"] == "text/markdown"
    content = await service_client.get(
        f"/internal/v1/sessions/{SESSION_ID}/assets/{markdown.json()['id']}/content"
    )
    assert content.headers["content-type"] == "text/plain; charset=utf-8"
    html = await service_client.post(url, json={"document_id": world.html_doc_id})
    assert html.status_code == 415
    assert html.json()["error"]["details"] == {"reason": "no_preview"}


# ------------------------------------------------------------------ console list + signed downloads


async def test_the_console_lists_files_with_signed_workspace_scoped_links(
    service_client: httpx.AsyncClient,
    admin_client: httpx.AsyncClient,
    client: httpx.AsyncClient,
    world: World,
) -> None:
    stored = (await _upload(service_client, PNG, name="damage.png", meta={"block_id": "docs"})).json()
    listed = await admin_client.get(f"/v1/sessions/{SESSION_ID}/assets")
    assert listed.status_code == 200, listed.text
    (item,) = listed.json()["items"]
    assert item["id"] == stored["id"] and item["expires_at"]
    url = urlsplit(item["url"])
    assert url.path == f"/v1/sessions/{SESSION_ID}/assets/{stored['id']}/content"
    query = parse_qs(url.query)

    # `client` has no cookie and no token: the signature is the authorisation.
    ok = await client.get(f"{url.path}?{url.query}")
    assert ok.status_code == 200
    assert ok.content == PNG
    assert ok.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in ok.headers["content-security-policy"]
    assert ok.headers["content-disposition"].startswith("inline;")
    tampered = await client.get(url.path, params={"exp": query["exp"][0], "sig": "0" * 64})
    assert tampered.status_code == 401
    other_asset = await client.get(
        f"/v1/sessions/{SESSION_ID}/assets/{'f' * 32}/content", params={k: v[0] for k, v in query.items()}
    )
    assert other_asset.status_code == 401
    expired = await client.get(url.path, params={"exp": int(time.time()) - 5, "sig": query["sig"][0]})
    assert expired.status_code == 401


async def test_signed_links_expire_and_are_bound_to_the_workspace(settings: Settings) -> None:
    now = time.time()
    exp = int(now) + 60
    sig = assets_service.sign_asset_url(
        settings.master_key, workspace_id="w1", session_id="s", asset_id="a", exp=exp
    )
    common = {"session_id": "s", "asset_id": "a", "exp": exp, "sig": sig}
    assert assets_service.verify_asset_url(settings.master_key, workspace_id="w1", now=now, **common)
    assert not assets_service.verify_asset_url(settings.master_key, workspace_id="w2", now=now, **common)
    assert not assets_service.verify_asset_url(settings.master_key, workspace_id="w1", now=exp + 1, **common)
    far = int(now) + 10 * 24 * 3600
    far_sig = assets_service.sign_asset_url(
        settings.master_key, workspace_id="w1", session_id="s", asset_id="a", exp=far
    )
    assert not assets_service.verify_asset_url(
        settings.master_key, workspace_id="w1", session_id="s", asset_id="a", exp=far, sig=far_sig, now=now
    )


async def test_another_workspace_cannot_list_the_files(
    admin_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    await make_workspace(database, "beta")
    response = await admin_client.get(f"/v1/sessions/{SESSION_ID}/assets", headers={"X-Workspace": "beta"})
    assert response.status_code == 404


async def test_a_pdf_downloads_as_an_attachment(
    service_client: httpx.AsyncClient, admin_client: httpx.AsyncClient, world: World
) -> None:
    await service_client.post(
        f"/internal/v1/sessions/{SESSION_ID}/assets/from-document", json={"document_id": world.doc_id}
    )
    (item,) = (await admin_client.get(f"/v1/sessions/{SESSION_ID}/assets")).json()["items"]
    url = urlsplit(item["url"])
    response = await admin_client.get(f"{url.path}?{url.query}")
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["content-type"] == "application/pdf"


# ------------------------------------------------------------------ retention


async def _end_session(database: Database, *, days_ago: float) -> None:
    async with database.session() as session:
        row = await session.get(SessionRow, SESSION_ID)
        assert row is not None
        row.status = "ended"
        row.ended_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=days_ago)


async def test_retention_deletes_the_files_with_the_session(
    service_client: httpx.AsyncClient, world: World, database: Database, settings: Settings, data_dir: Path
) -> None:
    stored = (await _upload(service_client, PNG, meta={"block_id": "docs"})).json()
    (row,) = await _rows(database)
    storage = default_storage(settings)
    assert await storage.get(row.storage_key) == PNG

    await _end_session(database, days_ago=3)
    assert await sweep_session_assets(database, settings) == 0  # 7-day retention not reached
    await _end_session(database, days_ago=8)
    assert await sweep_session_assets(database, settings) == 1
    assert await _rows(database) == []
    with pytest.raises(FileNotFoundError):
        await storage.get(row.storage_key)
    assert stored["id"]


async def test_no_retention_keeps_the_files(
    service_client: httpx.AsyncClient, world: World, database: Database, settings: Settings
) -> None:
    async with database.session() as session:
        from lkap_api.db.models import Agent

        agent = await session.get(Agent, world.agent_id)
        assert agent is not None
        agent.config = {**agent.config, "recording": {"retention_days": None}}
    await _upload(service_client, PNG, meta={"block_id": "docs"})
    await _end_session(database, days_ago=400)
    assert await sweep_session_assets(database, settings) == 0
    assert len(await _rows(database)) == 1


# ------------------------------------------------------------------ migration (v5_002_session_uploads)


def _migrate(database: Path, revision: str, *, downgrade: bool = False) -> None:
    api_root = Path(__file__).resolve().parents[1]
    config = Config(str(api_root / "alembic.ini"))
    config.set_main_option("script_location", str(api_root / "alembic"))
    config.cmd_opts = None  # type: ignore[assignment]
    url = f"sqlite+aiosqlite:///{database}"
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["configure_logger"] = False
    os.environ["LKAP_DATABASE_URL"] = url
    try:
        (command.downgrade if downgrade else command.upgrade)(config, revision)
    finally:
        os.environ.pop("LKAP_DATABASE_URL", None)


def _tables(database: Path) -> set[str]:
    connection = sqlite3.connect(database)
    try:
        return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        connection.close()


def test_v5_002_upgrades_downgrades_and_upgrades_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Up, down, up on a scratch copy of the v1 seed (never the live database)."""
    for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "LKAP_MASTER_KEY"):
        monkeypatch.delenv(name, raising=False)
    database = tmp_path / "lkap.db"
    shutil.copy(Path(__file__).resolve().parent / "fixtures" / "v1_seed.sqlite", database)
    _migrate(database, "v5_004_mcp_oauth")
    assert "session_assets" not in _tables(database)
    _migrate(database, "v5_002_session_uploads")
    assert "session_assets" in _tables(database)
    _migrate(database, "v5_004_mcp_oauth", downgrade=True)
    assert "session_assets" not in _tables(database)
    _migrate(database, "head")
    assert "session_assets" in _tables(database)
