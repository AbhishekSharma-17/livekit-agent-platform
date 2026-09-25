"""``v5_001_knowledge_p0`` (V5-01): up/down/up on SQLite, rows preserved, the FTS5 index in sync.

The Postgres branch (generated ``tsv`` + GIN) is executed by the CI ``test-postgres``
job's "Migrate up, down and up again on Postgres" step (not yet run: Docker is not
available locally); this file drives the SQLite branch directly.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config

from alembic import command

API_ROOT = Path(__file__).resolve().parents[1]
BEFORE = "v4_002_provider_models"
REVISION = "v5_001_knowledge_p0"
FTS_OBJECTS = {"kb_chunks_fts", "kb_chunks_fts_ai", "kb_chunks_fts_ad", "kb_chunks_fts_au"}


@pytest.fixture(autouse=True)
def _no_livekit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "LKAP_MASTER_KEY"):
        monkeypatch.delenv(name, raising=False)


def _migrate(database: Path, target: str, *, downgrade: bool = False) -> None:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    config.attributes["configure_logger"] = False
    url = f"sqlite+aiosqlite:///{database}"
    config.set_main_option("sqlalchemy.url", url)
    os.environ["LKAP_DATABASE_URL"] = url
    try:
        (command.downgrade if downgrade else command.upgrade)(config, target)
    finally:
        os.environ.pop("LKAP_DATABASE_URL", None)


def _query(database: Path, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        rows = connection.execute(sql, params).fetchall()
        connection.commit()
        return rows
    finally:
        connection.close()


def _columns(database: Path, table: str) -> set[str]:
    return {row[1] for row in _query(database, f"PRAGMA table_info({table})")}


def _objects(database: Path) -> set[str]:
    return {row[0] for row in _query(database, "SELECT name FROM sqlite_master WHERE name LIKE '%kb_%'")}


def _seed_v4_rows(database: Path) -> None:
    """A knowledge base, a document and two chunks as V4 writes them."""
    _query(
        database,
        "INSERT INTO knowledge_bases (id, workspace_id, name, description, embedder_id, chunk_count, "
        "created_at, updated_at) VALUES ('kb1', ?, 'Policies', '', 'fastembed-embedding', 2, "
        "'2026-09-24 00:00:00', '2026-09-24 00:00:00')",
        (_default_workspace(database),),
    )
    _query(
        database,
        "INSERT INTO kb_documents (id, kb_id, filename, mime, bytes, status, chunk_count, created_at) "
        "VALUES ('doc1', 'kb1', 'policy_lines.md', 'text/markdown', 10, 'ready', 2, '2026-09-24 00:00:00')",
    )
    for chunk_id, text in (
        ("c1", "Flood damage is covered under HO-4."),
        ("c2", "Call AUTO-11111 for towing."),
    ):
        _query(
            database,
            "INSERT INTO kb_chunks (id, kb_id, document_id, ordinal, text, meta) "
            "VALUES (?, 'kb1', 'doc1', 0, ?, '{\"filename\": \"policy_lines.md\"}')",
            (chunk_id, text),
        )


def _default_workspace(database: Path) -> str:
    rows = _query(database, "SELECT id FROM workspaces LIMIT 1")
    if rows:
        return str(rows[0][0])
    _query(
        database,
        "INSERT INTO workspaces (id, slug, name, settings, created_at) "
        "VALUES ('ws1', 'default', 'Default', '{}', '2026-09-24 00:00:00')",
    )
    return "ws1"


def _fts_match(database: Path, query: str) -> list[str]:
    rows = _query(database, "SELECT chunk_id FROM kb_chunks_fts WHERE kb_chunks_fts MATCH ?", (query,))
    return sorted(str(row[0]) for row in rows)


def test_upgrade_on_a_v4_database_keeps_rows_and_indexes_existing_chunks(tmp_path: Path) -> None:
    database = tmp_path / "lkap.db"
    _migrate(database, BEFORE)
    _seed_v4_rows(database)

    _migrate(database, REVISION)

    assert {"dimension", "embedder_model", "chunking"} <= _columns(database, "knowledge_bases")
    assert "progress" in _columns(database, "kb_documents")
    assert _columns(database, "kb_evals") == {
        "id",
        "kb_id",
        "question",
        "expected_document_id",
        "expected_text",
        "tags",
        "ordinal",
        "created_at",
    }
    # Existing rows are untouched and unrecorded (NULL = never refused).
    assert _query(database, "SELECT name, dimension, embedder_model, chunking FROM knowledge_bases") == [
        ("Policies", None, None, None)
    ]
    assert _query(database, "SELECT status, progress FROM kb_documents") == [("ready", None)]
    assert _query(database, "SELECT meta FROM kb_chunks WHERE id = 'c1'") == [
        ('{"filename": "policy_lines.md"}',)
    ]
    # The FTS index is backfilled from the existing chunks.
    assert _query(database, "SELECT count(*) FROM kb_chunks_fts") == [(2,)]
    assert _fts_match(database, 'text:"AUTO-11111"') == ["c2"]
    assert _fts_match(database, "text:flooding") == ["c1"]  # porter stemming
    assert "ix_kb_chunks_kb_document" in _objects(database)


def test_fts_index_follows_inserts_updates_and_cascading_deletes(tmp_path: Path) -> None:
    database = tmp_path / "lkap.db"
    _migrate(database, BEFORE)
    _seed_v4_rows(database)
    _migrate(database, REVISION)

    _query(
        database,
        "INSERT INTO kb_chunks (id, kb_id, document_id, ordinal, text, meta) "
        "VALUES ('c3', 'kb1', 'doc1', 2, 'Sewer backup needs a rider.', '{}')",
    )
    assert _fts_match(database, "text:rider") == ["c3"]
    _query(database, "UPDATE kb_chunks SET text = 'Sewer backup needs an endorsement.' WHERE id = 'c3'")
    assert _fts_match(database, "text:rider") == []
    assert _fts_match(database, "text:endorsement") == ["c3"]
    _query(database, "DELETE FROM kb_chunks WHERE id = 'c1'")
    assert _query(database, "SELECT count(*) FROM kb_chunks_fts") == [(2,)]
    # Deleting the knowledge base cascades to documents, chunks and their index rows.
    _query(database, "DELETE FROM knowledge_bases WHERE id = 'kb1'")
    assert _query(database, "SELECT count(*) FROM kb_chunks") == [(0,)]
    assert _query(database, "SELECT count(*) FROM kb_chunks_fts") == [(0,)]


def test_downgrade_removes_everything_and_upgrade_reaches_head_again(tmp_path: Path) -> None:
    database = tmp_path / "lkap.db"
    _migrate(database, BEFORE)
    _seed_v4_rows(database)
    _migrate(database, REVISION)
    _query(
        database,
        "INSERT INTO kb_evals (id, kb_id, question, expected_text, tags, ordinal, created_at) "
        "VALUES ('e1', 'kb1', 'Is flood covered?', 'HO-4', '[]', 0, '2026-09-25 00:00:00')",
    )

    _migrate(database, BEFORE, downgrade=True)

    assert not {"dimension", "embedder_model", "chunking"} & _columns(database, "knowledge_bases")
    assert "progress" not in _columns(database, "kb_documents")
    objects = _objects(database)
    assert not objects & FTS_OBJECTS
    assert not any(name.startswith("kb_chunks_fts") for name in objects)
    assert "kb_evals" not in objects
    assert "ix_kb_chunks_kb_document" not in objects
    assert _query(database, "SELECT id FROM knowledge_bases") == [("kb1",)]
    assert _query(database, "SELECT count(*) FROM kb_chunks") == [(2,)]

    _migrate(database, REVISION)
    assert _query(database, "SELECT count(*) FROM kb_chunks_fts") == [(2,)]
    assert _query(database, "SELECT version_num FROM alembic_version") == [(REVISION,)]


def test_fresh_database_upgrades_down_and_up(tmp_path: Path) -> None:
    database = tmp_path / "fresh.db"
    _migrate(database, "head")
    assert FTS_OBJECTS <= _objects(database)
    _migrate(database, BEFORE, downgrade=True)
    assert not _objects(database) & FTS_OBJECTS
    _migrate(database, "head")
    assert FTS_OBJECTS <= _objects(database)
