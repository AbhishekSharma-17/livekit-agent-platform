"""``v2_009_fleet_restart`` (R-V2-4): up/down on a fresh database, rows preserved."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config

from alembic import command

API_ROOT = Path(__file__).resolve().parents[1]
BEFORE = "v2_008_agentconfig_v2"
REVISION = "v2_009_fleet_restart"


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


def _query(database: Path, sql: str) -> list[tuple[Any, ...]]:
    connection = sqlite3.connect(database)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


def _columns(database: Path) -> set[str]:
    return {row[1] for row in _query(database, "PRAGMA table_info(fleet_desired)")}


def _seed_row(database: Path) -> None:
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "INSERT INTO fleet_desired (connection_id, desired_replicas, desired_hash, updated_at) "
            "VALUES ('c0ffee', 2, ?, '2026-09-23 00:00:00')",
            ("h" * 64,),
        )
        connection.commit()
    finally:
        connection.close()


def test_upgrade_adds_the_restart_columns_and_keeps_rows(tmp_path: Path) -> None:
    database = tmp_path / "fresh.sqlite"
    _migrate(database, BEFORE)
    _seed_row(database)

    _migrate(database, REVISION)

    assert {"restart_generation", "restart_requested_at"} <= _columns(database)
    assert _query(database, "SELECT version_num FROM alembic_version") == [(REVISION,)]
    rows = _query(
        database,
        "SELECT connection_id, desired_replicas, desired_hash, restart_generation, restart_requested_at "
        "FROM fleet_desired",
    )
    assert rows == [("c0ffee", 2, "h" * 64, 0, None)]


def test_downgrade_drops_the_columns_and_keeps_rows_then_upgrades_again(tmp_path: Path) -> None:
    database = tmp_path / "fresh.sqlite"
    _migrate(database, BEFORE)
    _seed_row(database)
    _migrate(database, REVISION)

    _migrate(database, BEFORE, downgrade=True)

    assert not {"restart_generation", "restart_requested_at"} & _columns(database)
    assert _query(database, "SELECT connection_id, desired_replicas FROM fleet_desired") == [("c0ffee", 2)]
    assert _query(database, "SELECT version_num FROM alembic_version") == [(BEFORE,)]
    _migrate(database, REVISION)
    assert "restart_generation" in _columns(database)


def test_the_revision_chains_directly_after_v2_008() -> None:
    from alembic.script import ScriptDirectory

    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    script = ScriptDirectory.from_config(config)

    revision = script.get_revision(REVISION)
    assert revision is not None and revision.down_revision == BEFORE
