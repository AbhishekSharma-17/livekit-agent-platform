"""``v6_002_datasets``: the dataset tables and ``tools.kind`` accepting ``dataset`` (V6-16).

Synchronous on purpose (``alembic/env.py`` runs its own event loop). Every run
uses a copy of the v1 seed under ``tmp_path``, never the live database; the
rehearsal is upgrade → downgrade → upgrade (PLAN-V6 §0.1).
"""

from __future__ import annotations

import shutil
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command

API_ROOT = Path(__file__).resolve().parents[1]
REVISION = "v6_002_datasets"
PREVIOUS = "v5_008_memory"
WORKSPACE = "00000000000000000000000000000001"
TOOL_INSERT = (
    "INSERT INTO tools (id, agent_id, kind, name, definition, enabled, created_at, updated_at, workspace_id) "
    f"VALUES (?, NULL, ?, 'x_y', '{{}}', 1, '2026-09-28', '2026-09-28', '{WORKSPACE}')"
)
DATASET_INSERT = (
    "INSERT INTO datasets (id, workspace_id, name, slug, format, columns, key_columns, row_count, "
    "storage_key, sha256, status, created_at, updated_at) VALUES "
    f"(?, '{WORKSPACE}', 'Demo', ?, 'csv', '[]', '[]', 1, 'k', 'h', 'ready', '2026-09-28', '2026-09-28')"
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
    with pytest.MonkeyPatch.context() as env:
        env.setenv("LKAP_DATABASE_URL", url)
        (command.downgrade if downgrade else command.upgrade)(config, revision)


def _execute(database: Path, sql: str, params: tuple[object, ...] = ()) -> bool:
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(sql, params)
        connection.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        connection.close()


def _scalar(database: Path, sql: str) -> object:
    connection = sqlite3.connect(database)
    try:
        return connection.execute(sql).fetchone()[0]
    finally:
        connection.close()


def _names(database: Path, kind: str) -> set[str]:
    connection = sqlite3.connect(database)
    try:
        return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type=?", (kind,))}
    finally:
        connection.close()


def test_upgrade_creates_the_tables_and_accepts_the_dataset_kind(database: Path) -> None:
    _migrate(database, REVISION)

    assert {"datasets", "dataset_rows", "dataset_keys"} <= _names(database, "table")
    assert {"ix_datasets_workspace", "ix_dataset_rows_dataset_ordinal", "ix_dataset_keys_lookup"} <= _names(
        database, "index"
    )
    assert _execute(database, TOOL_INSERT, ("d1", "dataset"))
    assert _execute(database, TOOL_INSERT, ("p1", "provider"))
    assert not _execute(database, TOOL_INSERT, ("o1", "other"))
    assert "ck_tools_kind_valid" in str(_scalar(database, "SELECT sql FROM sqlite_master WHERE name='tools'"))


def test_rows_and_keys_cascade_from_their_dataset(database: Path) -> None:
    _migrate(database, REVISION)
    assert _execute(database, DATASET_INSERT, ("ds1", "demo"))
    assert not _execute(database, DATASET_INSERT, ("ds2", "demo")), "the slug is unique per workspace"
    assert _execute(
        database,
        "INSERT INTO dataset_rows (id, dataset_id, ordinal, keys, row) VALUES ('r1','ds1',0,'{}','{}')",
    )
    assert _execute(
        database,
        "INSERT INTO dataset_keys (row_id, column_name, dataset_id, value) "
        "VALUES ('r1', 'phone', 'ds1', '9876543210')",
    )

    assert _execute(database, "DELETE FROM datasets WHERE id = 'ds1'")

    assert _scalar(database, "SELECT count(*) FROM dataset_rows") == 0
    assert _scalar(database, "SELECT count(*) FROM dataset_keys") == 0


def test_downgrade_drops_the_tables_and_dataset_tools_and_restores_the_check(database: Path) -> None:
    _migrate(database, REVISION)
    before = _scalar(database, "SELECT count(*) FROM tools")
    _execute(database, TOOL_INSERT, ("d1", "dataset"))

    _migrate(database, PREVIOUS, downgrade=True)

    assert not {"datasets", "dataset_rows", "dataset_keys"} & _names(database, "table")
    assert _scalar(database, "SELECT count(*) FROM tools") == before
    assert not _execute(database, TOOL_INSERT, ("d2", "dataset"))
    assert _execute(database, TOOL_INSERT, ("p2", "provider"))


def test_upgrade_after_a_downgrade_reaches_head_again(database: Path) -> None:
    _migrate(database, REVISION)
    _migrate(database, PREVIOUS, downgrade=True)

    _migrate(database, REVISION)

    assert _execute(database, TOOL_INSERT, ("d3", "dataset"))
    assert _execute(database, DATASET_INSERT, ("ds3", "again"))
    assert "ix_tools_workspace" in _names(database, "index")
