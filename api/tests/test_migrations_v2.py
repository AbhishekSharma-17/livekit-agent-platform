"""The v2 chain must round-trip a real v1 database without losing a row.

Synchronous on purpose, like `test_migrations.py`: `alembic/env.py` drives the
async engine with `asyncio.run`, which cannot nest inside a running loop.

Each test copies `fixtures/v1_seed.sqlite` (2 agents, 3 sessions, 3 events, a
tool and a knowledge base, stamped at the v1 head) into `tmp_path` and migrates
the copy, so the committed fixture is never modified.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config
from lkap_contracts.agent_config import AgentConfig
from lkap_contracts.migrate import COMPOSITE_PANEL_ID

from alembic import command
from lkap_api.db.constants import DEFAULT_WORKSPACE_ID
from lkap_api.db.models import Base

API_ROOT = Path(__file__).resolve().parents[1]
SEED = API_ROOT / "tests" / "fixtures" / "v1_seed.sqlite"
V1_REVISION = "4135323c6ecc"

#: Row counts the fixture ships with; nothing may change them.
SEED_COUNTS: dict[str, int] = {
    "agents": 2,
    "sessions": 3,
    "session_events": 3,
    "tools": 1,
    "knowledge_bases": 1,
    "credentials": 0,
}


def _config(database: Path) -> Config:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    config.cmd_opts = None  # type: ignore[assignment]
    config.attributes["configure_logger"] = False
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database}")
    return config


def _migrate(database: Path, target: str, *, downgrade: bool = False) -> None:
    """Run alembic against `database` only, never the developer's data dir."""
    os.environ["LKAP_DATABASE_URL"] = f"sqlite+aiosqlite:///{database}"
    try:
        runner = command.downgrade if downgrade else command.upgrade
        runner(_config(database), target)
    finally:
        os.environ.pop("LKAP_DATABASE_URL", None)


def _counts(database: Path, tables: dict[str, int]) -> dict[str, int]:
    connection = sqlite3.connect(database)
    try:
        return {table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in tables}
    finally:
        connection.close()


def _rows(database: Path, sql: str) -> list[tuple[Any, ...]]:
    connection = sqlite3.connect(database)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


def _revision(database: Path) -> str:
    return str(_rows(database, "SELECT version_num FROM alembic_version")[0][0])


@pytest.fixture
def v1_database(tmp_path: Path) -> Iterator[Path]:
    """A writable copy of the committed v1 seed database."""
    target = tmp_path / "v1_copy.sqlite"
    shutil.copyfile(SEED, target)
    yield target
    target.unlink(missing_ok=True)


@pytest.fixture(autouse=True)
def _no_livekit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's real LiveKit credentials out of the migration run.

    Without this, a shell that exports `LIVEKIT_*` would make `v2_002` insert a
    real default connection and the "no credentials" assertions would pass for
    the wrong reason.
    """
    for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "LKAP_MASTER_KEY"):
        monkeypatch.delenv(name, raising=False)


def test_upgrade_head_on_a_v1_database_keeps_every_row(v1_database: Path) -> None:
    before = _counts(v1_database, SEED_COUNTS)
    assert before == SEED_COUNTS

    _migrate(v1_database, "head")

    assert _counts(v1_database, SEED_COUNTS) == SEED_COUNTS
    tables = {row[0] for row in _rows(v1_database, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert set(Base.metadata.tables) <= tables


def test_upgrade_head_on_a_v1_database_scopes_every_row_to_the_default_workspace(
    v1_database: Path,
) -> None:
    _migrate(v1_database, "head")

    for table in ("agents", "credentials", "tools", "knowledge_bases", "sessions"):
        workspaces = {row[0] for row in _rows(v1_database, f"SELECT DISTINCT workspace_id FROM {table}")}
        assert workspaces <= {DEFAULT_WORKSPACE_ID}, table
    assert _rows(v1_database, "SELECT count(*) FROM workspaces")[0][0] == 1


def test_upgrade_head_on_a_v1_database_reparses_every_config_as_agentconfig_v2(
    v1_database: Path,
) -> None:
    _migrate(v1_database, "head")

    for (config,) in _rows(v1_database, "SELECT config FROM agents"):
        parsed = AgentConfig.model_validate(json.loads(config))
        assert parsed.v == 2
    for (config,) in _rows(v1_database, "SELECT config FROM agent_config_versions"):
        assert AgentConfig.model_validate(json.loads(config)).v == 2


def test_upgrade_head_mirrors_the_panel_id_onto_the_agent_row(v1_database: Path) -> None:
    _migrate(v1_database, "head")

    panels = dict(_rows(v1_database, "SELECT slug, ui_panel_id FROM agents"))
    assert panels == {"claims-desk": COMPOSITE_PANEL_ID, "fnol-intake": "insurance_notebook"}
    blocks = {
        row[0]: json.loads(row[1])["panel"]["blocks"]
        for row in _rows(v1_database, "SELECT slug, config FROM agents")
    }
    assert [block["type"] for block in blocks["claims-desk"]] == [
        "status",
        "notes",
        "checklist",
        "activity",
    ]
    assert blocks["fnol-intake"] == []


def test_upgrade_head_backfills_one_config_version_per_agent(v1_database: Path) -> None:
    _migrate(v1_database, "head")

    rows = _rows(v1_database, "SELECT agent_id, config_version, note FROM agent_config_versions")
    assert len(rows) == SEED_COUNTS["agents"]
    assert {row[1] for row in rows} == {1}
    assert {row[2] for row in rows} == {"migrated from v1"}


def test_upgrade_head_sets_channel_web_on_every_migrated_session(v1_database: Path) -> None:
    _migrate(v1_database, "head")

    channels = {row[0] for row in _rows(v1_database, "SELECT DISTINCT channel FROM sessions")}
    statuses = {row[0] for row in _rows(v1_database, "SELECT DISTINCT recording_status FROM sessions")}
    assert channels == {"web"}
    assert statuses == {"none"}


def test_upgrade_head_without_livekit_env_leaves_the_connection_to_bootstrap(
    v1_database: Path,
) -> None:
    _migrate(v1_database, "head")

    assert _rows(v1_database, "SELECT count(*) FROM livekit_connections")[0][0] == 0
    unbound = _rows(v1_database, "SELECT count(*) FROM agents WHERE connection_id IS NULL")[0][0]
    assert unbound == SEED_COUNTS["agents"]


def test_upgrade_head_with_livekit_env_creates_the_default_connection(
    v1_database: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LIVEKIT_URL", "wss://example.livekit.cloud")
    monkeypatch.setenv("LIVEKIT_API_KEY", "APIexamplekey")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "secret-value-long-enough-for-hs256")
    monkeypatch.setenv("LKAP_MASTER_KEY", "TWk5rQ2mE4b3W6z8n1F0pQhV9xY7cJdKzL5aRtUvWo8=")

    _migrate(v1_database, "head")

    rows = _rows(
        v1_database, "SELECT slug, deployment_type, deployment_mode, is_default FROM livekit_connections"
    )
    assert rows == [("default", "cloud", "external", 1)]
    unbound = _rows(v1_database, "SELECT count(*) FROM agents WHERE connection_id IS NULL")[0][0]
    assert unbound == 0
    bound = _rows(v1_database, "SELECT count(*) FROM sessions WHERE connection_id IS NULL")[0][0]
    assert bound == 0


def test_upgrade_head_with_a_self_hosted_url_infers_the_deployment_type(
    v1_database: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LIVEKIT_URL", "ws://livekit.internal:7880")
    monkeypatch.setenv("LIVEKIT_API_KEY", "devkey")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "secret-value-long-enough-for-hs256")
    monkeypatch.setenv("LKAP_MASTER_KEY", "TWk5rQ2mE4b3W6z8n1F0pQhV9xY7cJdKzL5aRtUvWo8=")

    _migrate(v1_database, "head")

    assert _rows(v1_database, "SELECT deployment_type FROM livekit_connections") == [("self_hosted",)]


def test_downgrade_to_the_v1_head_restores_the_v1_schema_and_every_row(
    v1_database: Path,
) -> None:
    _migrate(v1_database, "head")

    _migrate(v1_database, V1_REVISION, downgrade=True)

    assert _revision(v1_database) == V1_REVISION
    assert _counts(v1_database, SEED_COUNTS) == SEED_COUNTS
    columns = {row[1] for row in _rows(v1_database, "PRAGMA table_info('agents')")}
    assert "workspace_id" not in columns
    assert "connection_id" not in columns
    tables = {row[0] for row in _rows(v1_database, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "livekit_connections" not in tables
    assert "workspaces" not in tables


def test_downgrade_rewrites_every_config_back_to_v1(v1_database: Path) -> None:
    _migrate(v1_database, "head")

    _migrate(v1_database, V1_REVISION, downgrade=True)

    for (config,) in _rows(v1_database, "SELECT config FROM agents"):
        document = json.loads(config)
        assert document["v"] == 1
        assert "panel" not in document
        assert "recording" not in document
    panels = dict(_rows(v1_database, "SELECT slug, ui_panel_id FROM agents"))
    assert panels == {"claims-desk": "generic", "fnol-intake": "insurance_notebook"}


def test_downgrade_keeps_the_v1_check_constraints(v1_database: Path) -> None:
    _migrate(v1_database, "head")

    _migrate(v1_database, V1_REVISION, downgrade=True)

    ddl = dict(_rows(v1_database, "SELECT name, sql FROM sqlite_master WHERE name IN ('sessions','tools')"))
    assert "ck_sessions_status_valid" in ddl["sessions"]
    assert "ck_tools_kind_valid" in ddl["tools"]


def test_upgrade_after_a_downgrade_reaches_head_again(v1_database: Path) -> None:
    _migrate(v1_database, "head")
    _migrate(v1_database, V1_REVISION, downgrade=True)

    _migrate(v1_database, "head")

    assert _counts(v1_database, SEED_COUNTS) == SEED_COUNTS
    for (config,) in _rows(v1_database, "SELECT config FROM agents"):
        assert AgentConfig.model_validate(json.loads(config)).v == 2


def test_upgrade_head_keeps_the_new_check_constraints_on_rebuilt_tables(
    v1_database: Path,
) -> None:
    _migrate(v1_database, "head")

    ddl = dict(
        _rows(
            v1_database,
            "SELECT name, sql FROM sqlite_master WHERE name IN ('sessions','tools','agents')",
        )
    )
    assert "ck_sessions_status_valid" in ddl["sessions"]
    assert "ck_sessions_channel_valid" in ddl["sessions"]
    assert "ck_sessions_recording_status_valid" in ddl["sessions"]
    assert "ck_tools_kind_valid" in ddl["tools"]
    assert "ck_agents_mode_valid" in ddl["agents"]
    indexes = {row[0] for row in _rows(v1_database, "SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"ix_sessions_agent", "ix_sessions_workspace", "ix_agents_workspace"} <= indexes


def test_upgrade_head_is_idempotent_on_a_v1_database(v1_database: Path) -> None:
    _migrate(v1_database, "head")
    first = _rows(v1_database, "SELECT config FROM agents ORDER BY slug")

    _migrate(v1_database, "head")

    assert _rows(v1_database, "SELECT config FROM agents ORDER BY slug") == first
    assert len(_rows(v1_database, "SELECT version_num FROM alembic_version")) == 1
