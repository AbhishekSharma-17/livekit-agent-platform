"""``v6_003_credential_last_used``: ``credentials.last_used_at`` (V6-32).

Synchronous on purpose (``alembic/env.py`` runs its own event loop). Every run
uses a copy of the v1 seed under ``tmp_path``, never the live database.
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
REVISION = "v6_003_credential_last_used"
PREVIOUS = "v6_002_datasets"


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


def _query(database: Path, sql: str) -> list[tuple[object, ...]]:
    connection = sqlite3.connect(database)
    try:
        return list(connection.execute(sql).fetchall())
    finally:
        connection.close()


def _columns(database: Path) -> set[str]:
    return {str(row[1]) for row in _query(database, "PRAGMA table_info(credentials)")}


def test_upgrade_adds_a_nullable_last_used_at_and_keeps_every_row(database: Path) -> None:
    rows = "SELECT id, provider_id, fingerprint, last_test_at FROM credentials ORDER BY id"
    _migrate(database, PREVIOUS)
    before = _query(database, rows)

    _migrate(database, REVISION)

    assert "last_used_at" in _columns(database)
    after = _query(database, rows)
    assert after == before
    assert _query(database, "SELECT count(*) FROM credentials WHERE last_used_at IS NOT NULL") == [(0,)]


def test_downgrade_drops_the_column_and_keeps_the_rows_and_index(database: Path) -> None:
    _migrate(database, REVISION)
    count = _query(database, "SELECT count(*) FROM credentials")

    _migrate(database, PREVIOUS, downgrade=True)

    assert "last_used_at" not in _columns(database)
    assert _query(database, "SELECT count(*) FROM credentials") == count
    indexes = {str(row[1]) for row in _query(database, "PRAGMA index_list(credentials)")}
    assert "ix_credentials_workspace" in indexes


def test_upgrade_after_a_downgrade_reaches_head_again(database: Path) -> None:
    _migrate(database, REVISION)
    _migrate(database, PREVIOUS, downgrade=True)

    _migrate(database, REVISION)

    assert "last_used_at" in _columns(database)
