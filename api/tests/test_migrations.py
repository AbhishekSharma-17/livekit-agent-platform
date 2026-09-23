"""Alembic migrations must build the model schema on an empty SQLite file.

Synchronous on purpose: `alembic/env.py` drives the async engine with
`asyncio.run`, which cannot be nested inside a running event loop.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command
from lkap_api.db.models import Base

API_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _no_livekit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's real credentials out of these runs.

    `v2_002_connections` reads `LIVEKIT_*` and `LKAP_MASTER_KEY` straight from the
    environment, so a shell that exports them would otherwise make this test
    encrypt the live key into its throwaway database.
    """
    for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "LKAP_MASTER_KEY"):
        monkeypatch.delenv(name, raising=False)


def _run(data_dir: Path, action: Callable[[Config], None]) -> Path:
    """Run an alembic command against `data_dir/lkap.db` only."""
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    config.cmd_opts = None  # type: ignore[assignment]
    url = f"sqlite+aiosqlite:///{data_dir}/lkap.db"
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["configure_logger"] = False
    os.environ["LKAP_DATABASE_URL"] = url
    try:
        action(config)
    finally:
        os.environ.pop("LKAP_DATABASE_URL", None)
    return data_dir / "lkap.db"


def _upgrade_head(data_dir: Path) -> Path:
    return _run(data_dir, lambda config: command.upgrade(config, "head"))


def test_upgrade_head_on_an_empty_directory_creates_the_model_schema(tmp_path: Path) -> None:
    data_dir = tmp_path / "fresh"
    data_dir.mkdir()

    database = _upgrade_head(data_dir)

    connection = sqlite3.connect(database)
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns = {
            table: {row[1] for row in connection.execute(f"PRAGMA table_info('{table}')")}
            for table in Base.metadata.tables
        }
    finally:
        connection.close()

    assert set(Base.metadata.tables) <= tables
    assert "alembic_version" in tables
    for name, table in Base.metadata.tables.items():
        assert columns[name] == {column.name for column in table.columns}


def test_upgrade_head_is_idempotent(tmp_path: Path) -> None:
    data_dir = tmp_path / "twice"
    data_dir.mkdir()

    _upgrade_head(data_dir)
    _upgrade_head(data_dir)

    connection = sqlite3.connect(data_dir / "lkap.db")
    try:
        revisions = [row[0] for row in connection.execute("SELECT version_num FROM alembic_version")]
    finally:
        connection.close()
    assert len(revisions) == 1


def test_upgrade_head_matches_the_models_exactly(tmp_path: Path) -> None:
    """Autogenerate against head must find nothing: no column, type or constraint drift."""
    data_dir = tmp_path / "drift"
    data_dir.mkdir()
    _upgrade_head(data_dir)

    _run(data_dir, command.check)
