"""``v5_010_tool_provider_kind``: ``tools.kind`` accepts ``provider`` (V5-47).

Synchronous on purpose (``alembic/env.py`` runs its own event loop). Every run
uses a copy of the v1 seed under ``tmp_path``, never the live database.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command

API_ROOT = Path(__file__).resolve().parents[1]
REVISION = "v5_010_tool_provider_kind"
PREVIOUS = "v5_001_knowledge_p0"
INSERT = (
    "INSERT INTO tools (id, agent_id, kind, name, definition, enabled, created_at, updated_at, workspace_id) "
    "VALUES (?, NULL, ?, 'x_y', '{}', 1, '2026-09-25', '2026-09-25', '00000000000000000000000000000001')"
)


@pytest.fixture
def database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A scratch copy of the v1 seed, with the developer's LiveKit env kept out."""
    for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "LKAP_MASTER_KEY"):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "lkap.db"
    shutil.copy(API_ROOT / "tests" / "fixtures" / "v1_seed.sqlite", path)
    yield path


def _migrate(database: Path, revision: str, *, downgrade: bool = False) -> None:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    config.cmd_opts = None  # type: ignore[assignment]
    url = f"sqlite+aiosqlite:///{database}"
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["configure_logger"] = False
    os.environ["LKAP_DATABASE_URL"] = url
    try:
        (command.downgrade if downgrade else command.upgrade)(config, revision)
    finally:
        os.environ.pop("LKAP_DATABASE_URL", None)


def _insert(database: Path, tool_id: str, kind: str) -> bool:
    connection = sqlite3.connect(database)
    try:
        connection.execute(INSERT, (tool_id, kind))
        connection.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        connection.close()


def _tools_ddl(database: Path) -> str:
    connection = sqlite3.connect(database)
    try:
        return str(connection.execute("SELECT sql FROM sqlite_master WHERE name='tools'").fetchone()[0])
    finally:
        connection.close()


def _count(database: Path, where: str = "1=1") -> int:
    connection = sqlite3.connect(database)
    try:
        return int(connection.execute(f"SELECT count(*) FROM tools WHERE {where}").fetchone()[0])  # noqa: S608
    finally:
        connection.close()


def test_upgrade_accepts_the_provider_kind_and_keeps_the_constraint_name(database: Path) -> None:
    _migrate(database, REVISION)

    assert _insert(database, "p1", "provider")
    assert not _insert(database, "p2", "other")
    assert "ck_tools_kind_valid" in _tools_ddl(database)


def test_downgrade_removes_provider_rows_restores_the_narrow_check_and_keeps_the_rest(
    database: Path,
) -> None:
    _migrate(database, REVISION)
    before = _count(database)
    _insert(database, "p1", "provider")

    _migrate(database, PREVIOUS, downgrade=True)

    assert _count(database, "kind = 'provider'") == 0
    assert _count(database) == before
    assert not _insert(database, "p2", "provider")
    assert _insert(database, "h1", "http")


def test_upgrade_after_a_downgrade_reaches_head_again(database: Path) -> None:
    _migrate(database, REVISION)
    _migrate(database, PREVIOUS, downgrade=True)

    _migrate(database, REVISION)

    assert _insert(database, "p3", "provider")
    connection = sqlite3.connect(database)
    try:
        indexes = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    finally:
        connection.close()
    assert "ix_tools_workspace" in indexes
