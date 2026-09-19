"""Alembic migrations must build the model schema on an empty SQLite file.

Synchronous on purpose: `alembic/env.py` drives the async engine with
`asyncio.run`, which cannot be nested inside a running event loop.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic.config import Config

from alembic import command
from lkap_api.db.models import Base

API_ROOT = Path(__file__).resolve().parents[1]


def _upgrade_head(data_dir: Path) -> Path:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    config.cmd_opts = None  # type: ignore[assignment]
    url = f"sqlite+aiosqlite:///{data_dir}/lkap.db"
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["configure_logger"] = False
    import os

    os.environ["LKAP_DATABASE_URL"] = url
    try:
        command.upgrade(config, "head")
    finally:
        os.environ.pop("LKAP_DATABASE_URL", None)
    return data_dir / "lkap.db"


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
