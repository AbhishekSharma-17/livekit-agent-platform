"""``v5_009_consent``: ``sessions.consent_state`` (V5-15).

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
REVISION = "v5_009_consent"
PREVIOUS = "v5_003_pgvector"


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


def _columns(database: Path) -> set[str]:
    connection = sqlite3.connect(database)
    try:
        return {row[1] for row in connection.execute("PRAGMA table_info(sessions)")}
    finally:
        connection.close()


def _sessions_ddl(database: Path) -> str:
    connection = sqlite3.connect(database)
    try:
        return str(connection.execute("SELECT sql FROM sqlite_master WHERE name='sessions'").fetchone()[0])
    finally:
        connection.close()


def _session_count(database: Path) -> int:
    connection = sqlite3.connect(database)
    try:
        return int(connection.execute("SELECT count(*) FROM sessions").fetchone()[0])
    finally:
        connection.close()


def test_upgrade_adds_a_nullable_consent_state(database: Path) -> None:
    _migrate(database, PREVIOUS)
    before = _session_count(database)

    _migrate(database, REVISION)

    assert "consent_state" in _columns(database)
    assert _session_count(database) == before
    connection = sqlite3.connect(database)
    try:
        nulls = connection.execute("SELECT count(*) FROM sessions WHERE consent_state IS NULL").fetchone()[0]
    finally:
        connection.close()
    assert nulls == before


def test_downgrade_drops_the_column_and_keeps_the_constraints(database: Path) -> None:
    _migrate(database, REVISION)
    ddl_checks = ("status_valid", "channel_valid", "recording_status_valid")

    _migrate(database, PREVIOUS, downgrade=True)

    assert "consent_state" not in _columns(database)
    ddl = _sessions_ddl(database)
    for name in ddl_checks:
        assert name in ddl, name


def test_upgrade_after_a_downgrade_reaches_head_again(database: Path) -> None:
    _migrate(database, REVISION)
    _migrate(database, PREVIOUS, downgrade=True)

    _migrate(database, REVISION)

    assert "consent_state" in _columns(database)
