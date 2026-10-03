"""V6-37: `lkap_api.tools.sqlite_to_postgres`, the SQLite to Postgres copy tool.

The value conversions, url handling and error masking run everywhere. The copy itself needs
a Postgres server with pgvector, so those tests run only when ``LKAP_TEST_DATABASE_URL``
points at one (the CI ``test-postgres`` job). Locally, a ``pgserver`` wheel in a scratch venv
gives Postgres 16 plus pgvector without Docker: ``initdb``, then ``pg_ctl start`` on a TCP
port, then ``LKAP_TEST_DATABASE_URL=postgresql+asyncpg://<user>:<pw>@127.0.0.1:<port>/<db>``.

Both sides are built by the real migrations (``alembic upgrade head`` in a subprocess, since
``alembic/env.py`` runs its own event loop): one migrated SQLite file and one migrated
Postgres template database per module, copied per test (``CREATE DATABASE ... TEMPLATE``).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
import shutil
import sqlite3
import subprocess
import sys
import uuid
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from conftest import REQUIRED_ENV
from sqlalchemy import Column, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from lkap_api.db.constants import DEFAULT_WORKSPACE_ID
from lkap_api.db.models import (
    Agent,
    AgentConfigVersion,
    AgentKnowledgeBase,
    AuditLog,
    Base,
    Credential,
    Dataset,
    DatasetKey,
    DatasetRow,
    Job,
    KbChunk,
    KbDocument,
    KnowledgeBase,
    LiveKitConnection,
    Session,
    SessionAsset,
    SessionEvent,
    StorageConfig,
    Tool,
    UsageDaily,
    User,
    WebhookEndpoint,
    WorkspaceMember,
)
from lkap_api.tools import sqlite_to_postgres as s2p

API_DIR = Path(__file__).resolve().parents[1]
BASE_PG_URL = os.environ.get("LKAP_TEST_DATABASE_URL") or None
requires_postgres = pytest.mark.skipif(
    BASE_PG_URL is None, reason="needs Postgres with pgvector (LKAP_TEST_DATABASE_URL, the CI job)"
)

#: Ciphertext planted in the source, to prove it is copied byte for byte and never printed.
CIPHERTEXT = b"gAAAAAB-test-ciphertext-never-printed"
WEBHOOK_SECRET = b"gAAAAAB-webhook-secret-never-printed"
HEAD = "v6_003_credential_last_used"


def _column(name: str, column: str) -> Column[Any]:
    return Base.metadata.tables[name].c[column]


# --------------------------------------------------------------------------- conversions
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-10-01 12:30:00.123456", dt.datetime(2026, 10, 1, 12, 30, 0, 123456)),
        ("2026-10-01T12:30:00+02:00", dt.datetime(2026, 10, 1, 10, 30)),
        ("2026-10-01T12:30:00Z", dt.datetime(2026, 10, 1, 12, 30)),
        (dt.datetime(2026, 10, 1, 12, 30, tzinfo=dt.UTC), dt.datetime(2026, 10, 1, 12, 30)),
        (0, dt.datetime(1970, 1, 1)),
    ],
)
def test_coerce_value_utc_datetime_column_stores_naive_utc(value: object, expected: dt.datetime) -> None:
    assert s2p.coerce_value("sessions", _column("sessions", "created_at"), value) == expected


def test_coerce_value_timezone_aware_column_returns_aware_utc() -> None:
    from sqlalchemy import DateTime

    column: Column[Any] = Column("at", DateTime(timezone=True))
    result = s2p.coerce_value("t", column, "2026-10-01 12:30:00")
    assert result == dt.datetime(2026, 10, 1, 12, 30, tzinfo=dt.UTC)
    assert isinstance(result, dt.datetime) and result.tzinfo is not None


@pytest.mark.parametrize(
    ("value", "expected"),
    [(1, True), (0, False), ("true", True), ("0", False), ("F", False), (True, True)],
)
def test_coerce_value_boolean_column_accepts_sqlite_forms(value: object, expected: bool) -> None:
    assert s2p.coerce_value("agents", _column("agents", "published"), value) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ('{"a": [1, 2], "b": {"c": null}}', {"a": [1, 2], "b": {"c": None}}),
        ("[]", []),
        (b'{"x": 1}', {"x": 1}),
        (3, 3),
    ],
)
def test_coerce_value_json_column_parses_text(value: object, expected: object) -> None:
    assert s2p.coerce_value("agents", _column("agents", "config"), value) == expected


@pytest.mark.parametrize(
    ("table", "column", "value", "expected"),
    [
        ("credentials", "ciphertext", memoryview(b"abc"), b"abc"),
        ("credentials", "ciphertext", "abc", b"abc"),
        ("usage_daily", "day", "2026-10-01", dt.date(2026, 10, 1)),
        ("usage_daily", "day", "2026-10-01 00:00:00", dt.date(2026, 10, 1)),
        ("usage_daily", "minutes", "1.5", 1.5),
        ("usage_daily", "sessions", "7", 7),
        ("usage_daily", "sessions", 7.0, 7),
        ("agents", "name", 42, "42"),
        ("agents", "name", None, None),
    ],
)
def test_coerce_value_other_types_convert(table: str, column: str, value: object, expected: object) -> None:
    assert s2p.coerce_value(table, _column(table, column), value) == expected


def test_coerce_value_decimal_numeric_column_returns_decimal() -> None:
    from sqlalchemy import Numeric

    column: Column[Any] = Column("amount", Numeric(12, 2))
    assert s2p.coerce_value("t", column, 1.25) == Decimal("1.25")


@pytest.mark.parametrize(
    ("table", "column", "value"),
    [
        ("agents", "config", "{not json super-secret-value"),
        ("agents", "published", "super-secret-value"),
        ("sessions", "created_at", "super-secret-value"),
        ("usage_daily", "sessions", 1.5),
    ],
)
def test_coerce_value_bad_value_raises_without_the_value(table: str, column: str, value: object) -> None:
    with pytest.raises(s2p.CoercionError) as caught:
        s2p.coerce_value(table, _column(table, column), value)
    assert f"{table}.{column}" in str(caught.value)
    assert "super-secret-value" not in str(caught.value)
    assert caught.value.__cause__ is None


# --------------------------------------------------------------------------- urls and errors
def test_resolve_source_url_turns_a_path_into_an_aiosqlite_url(tmp_path: Path) -> None:
    db = tmp_path / "lkap.db"
    db.write_bytes(b"")
    assert s2p.resolve_source_url(str(db)) == f"sqlite+aiosqlite:///{db.resolve()}"


@pytest.mark.parametrize("value", ["/no/such/lkap.db", "postgresql://u@h/db"])
def test_resolve_source_url_refuses_missing_file_or_other_backend(value: str) -> None:
    with pytest.raises(s2p.CopyRefusedError):
        s2p.resolve_source_url(value)


@pytest.mark.parametrize(
    "value",
    ["postgres://lkap:pw@127.0.0.1:5433/lkap", "postgresql://lkap:pw@127.0.0.1:5433/lkap"],
)
def test_resolve_target_url_uses_asyncpg_driver(value: str) -> None:
    assert s2p.resolve_target_url(value) == "postgresql+asyncpg://lkap:pw@127.0.0.1:5433/lkap"


def test_resolve_target_url_falls_back_to_database_url_env() -> None:
    environ = {"LKAP_DATABASE_URL": "postgresql+asyncpg://lkap:pw@127.0.0.1:5433/lkap"}
    assert s2p.resolve_target_url(None, environ).endswith("@127.0.0.1:5433/lkap")


@pytest.mark.parametrize("environ", [{}, {"LKAP_DATABASE_URL": "sqlite+aiosqlite:///x.db"}])
def test_resolve_target_url_refuses_missing_or_non_postgres(environ: dict[str, str]) -> None:
    with pytest.raises(s2p.CopyRefusedError):
        s2p.resolve_target_url(None, environ)


def test_display_url_masks_the_password() -> None:
    shown = s2p.display_url("postgresql+asyncpg://lkap:hunter2-secret@127.0.0.1:5433/lkap")
    assert "hunter2-secret" not in shown


def test_safe_db_error_drops_parameters_and_literals() -> None:
    class DriverError(Exception):
        pass

    driver = DriverError(
        "invalid input for query argument $3: b'gAAAA-secret' (expected bytes)\nDETAIL: Key (id)=(abc)"
    )
    error = DBAPIError("INSERT INTO credentials ...", {"ciphertext": b"gAAAA-secret"}, driver)
    message = s2p.safe_db_error(error)
    assert "gAAAA-secret" not in message
    assert "DETAIL" not in message
    assert message.startswith("DriverError: invalid input for query argument $3")


def test_main_refuses_missing_source_with_exit_code_2(capsys: pytest.CaptureFixture[str]) -> None:
    code = s2p.main(["--source", "/no/such/lkap.db", "--target", "postgresql://u:p@127.0.0.1/db"])
    assert code == s2p.EXIT_REFUSED
    assert "refused" in capsys.readouterr().err


def test_code_head_matches_the_migration_chain() -> None:
    assert s2p.code_head() == HEAD


# --------------------------------------------------------------------------- fixtures (Postgres)
def _alembic_env(data_dir: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key != "LKAP_DATABASE_URL"}
    env.update(REQUIRED_ENV)
    env["LKAP_DATA_DIR"] = str(data_dir)
    return env


def _upgrade(url: str, data_dir: Path, revision: str = "head") -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", "-x", f"url={url}", "upgrade", revision],
        cwd=API_DIR,
        env=_alembic_env(data_dir),
        check=True,
        capture_output=True,
        timeout=300,
    )


def _admin(*statements: str) -> None:
    import asyncpg

    assert BASE_PG_URL is not None
    dsn = make_url(BASE_PG_URL).set(drivername="postgresql").render_as_string(hide_password=False)

    async def run() -> None:
        connection = await asyncpg.connect(dsn)
        try:
            for statement in statements:
                await connection.execute(statement)
        finally:
            await connection.close()

    asyncio.run(run())


def _pg_url(database: str) -> str:
    assert BASE_PG_URL is not None
    return make_url(BASE_PG_URL).set(database=database).render_as_string(hide_password=False)


@pytest.fixture(scope="module")
def sqlite_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One SQLite file migrated to head by the real migrations."""
    if BASE_PG_URL is None:
        pytest.skip("needs Postgres")
    folder = tmp_path_factory.mktemp("s2p_sqlite")
    path = folder / "template.db"
    _upgrade(f"sqlite+aiosqlite:///{path}", folder)
    return path


@pytest.fixture(scope="module")
def pg_template(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """One Postgres database migrated to head, used as the template of each test's target."""
    if BASE_PG_URL is None:
        pytest.skip("needs Postgres")
    name = f"lkap_s2p_tpl_{uuid.uuid4().hex[:12]}"
    _admin(f'CREATE DATABASE "{name}"')
    try:
        _upgrade(_pg_url(name), tmp_path_factory.mktemp("s2p_pg"))
        yield name
    finally:
        _admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def source_db(sqlite_template: Path, tmp_path: Path) -> Path:
    path = tmp_path / "lkap.db"
    shutil.copyfile(sqlite_template, path)
    return path


@pytest.fixture
def target_url(pg_template: str) -> Iterator[str]:
    name = f"lkap_s2p_{uuid.uuid4().hex[:12]}"
    _admin(f'CREATE DATABASE "{name}" TEMPLATE "{pg_template}"')
    try:
        yield _pg_url(name)
    finally:
        _admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


async def _seed(path: Path) -> dict[str, str]:
    """Rows across most tables, written through the models like the api writes them."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}", poolclass=NullPool)
    ids: dict[str, str] = {}
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            connection_id = (await db.execute(select(LiveKitConnection.id))).scalar_one()
            user = User(email="owner@example.com")
            agent = Agent(
                slug="support",
                name="Support",
                published=True,
                connection_id=connection_id,
                config={"instructions": "Be kind.", "nested": {"list": [1, 2, 3], "none": None}},
            )
            db.add_all([user, agent])
            await db.flush()
            kb = KnowledgeBase(name="Handbook")
            dataset = Dataset(
                name="Policies", slug="policies", storage_key="datasets/x/policies.csv", sha256="0" * 64
            )
            db.add_all([kb, dataset])
            await db.flush()
            document = KbDocument(
                kb_id=kb.id, filename="a.md", mime="text/markdown", bytes=10, status="ready"
            )
            row = DatasetRow(dataset_id=dataset.id, ordinal=0, keys={"id": "p1"}, row={"id": "p1", "n": 2})
            session = Session(
                agent_id=agent.id,
                connection_id=connection_id,
                config_version=1,
                room_name="room-1",
                participant_identity="caller-1",
                participant_name="Caller",
                status="ended",
                pipeline_mode="cascaded",
            )
            db.add_all([document, row, session])
            await db.flush()
            db.add_all(
                [
                    WorkspaceMember(workspace_id=DEFAULT_WORKSPACE_ID, user_id=user.id, role="owner"),
                    AgentConfigVersion(agent_id=agent.id, config_version=1, config={"v": 1}),
                    AgentKnowledgeBase(agent_id=agent.id, kb_id=kb.id),
                    Credential(provider_id="openai", label="main", ciphertext=CIPHERTEXT, fingerprint="abcd"),
                    Tool(
                        kind="http", name="lookup", definition={"url": "https://example.com", "method": "GET"}
                    ),
                    DatasetKey(row_id=row.id, column_name="id", dataset_id=dataset.id, value="p1"),
                    KbChunk(kb_id=kb.id, document_id=document.id, ordinal=0, text="hello world"),
                    SessionEvent(session_id=session.id, ts=dt.datetime.now(dt.UTC), type="turn", payload={}),
                    SessionEvent(session_id=session.id, ts=dt.datetime.now(dt.UTC), type="end", payload={}),
                    SessionAsset(
                        session_id=session.id,
                        kind="upload",
                        name="photo.png",
                        mime="image/png",
                        size=3,
                        storage_key=f"sessions/{session.id}/abc.png",
                        sha256="1" * 64,
                    ),
                    StorageConfig(name="Recordings", kind="s3", bucket="lkap", access_key_ct=CIPHERTEXT),
                    WebhookEndpoint(url="https://hooks.example.com/x", secret_ct=WEBHOOK_SECRET),
                    Job(kind="kb_ingest", payload={"kb_id": kb.id}),
                    UsageDaily(
                        workspace_id=DEFAULT_WORKSPACE_ID,
                        day=dt.date(2026, 10, 1),
                        agent_id=agent.id,
                        sessions=3,
                        minutes=4.5,
                    ),
                    *(
                        AuditLog(actor_type="system", action=f"seed.{index}", payload={})
                        for index in range(3)
                    ),
                ]
            )
            await db.commit()
            ids.update(agent=agent.id, session=session.id, kb=kb.id)
    finally:
        await engine.dispose()
    # Values SQLite holds loosely, written the way older code or a hand edit could leave them.
    with sqlite3.connect(path) as raw:
        raw.execute(
            "UPDATE sessions SET created_at = '2026-10-01T12:30:00+02:00' WHERE id = ?", (ids["session"],)
        )
        raw.execute("UPDATE storage_configs SET is_default = 'true'")
        raw.execute("UPDATE audit_log SET ts = '2026-09-30T08:00:00Z' WHERE action = 'seed.0'")
    return ids


def _source_counts(path: Path) -> dict[str, int]:
    with sqlite3.connect(path) as raw:
        return {
            table.name: int(raw.execute(f'SELECT COUNT(*) FROM "{table.name}"').fetchone()[0])
            for table in Base.metadata.sorted_tables
        }


# --------------------------------------------------------------------------- the copy (Postgres)
@requires_postgres
async def test_copy_database_copies_every_table_with_converted_values(
    source_db: Path, target_url: str
) -> None:
    ids = await _seed(source_db)
    expected = _source_counts(source_db)

    report = await s2p.copy_database(str(source_db), target_url)

    assert report.ok and report.committed and not report.dry_run
    assert report.revision == HEAD
    assert report.seed_rows_removed == 2  # the migrated target's own default workspace and connection
    copied = {table.name: table for table in report.tables if table.skipped is None}
    assert set(copied) == set(expected)
    for name, count in expected.items():
        assert (copied[name].source, copied[name].copied, copied[name].target) == (count, count, count), name
    assert expected["agents"] == 1 and expected["session_events"] == 2 and expected["kb_chunks"] == 1
    skipped = {table.name for table in report.tables if table.skipped is not None}
    assert skipped == {"alembic_version", "kb_vectors", "lkap_memory"}
    assert report.sequences_reset >= 2  # audit_log.id and session_events.id

    engine = create_async_engine(target_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine) as db:
            agent = (await db.execute(select(Agent).where(Agent.id == ids["agent"]))).scalar_one()
            assert agent.config == {"instructions": "Be kind.", "nested": {"list": [1, 2, 3], "none": None}}
            assert agent.published is True
            session = (await db.execute(select(Session).where(Session.id == ids["session"]))).scalar_one()
            assert session.created_at == dt.datetime(2026, 10, 1, 10, 30, tzinfo=dt.UTC)
            assert (await db.execute(select(Credential.ciphertext))).scalar_one() == CIPHERTEXT
            storage = (await db.execute(select(StorageConfig))).scalar_one()
            assert storage.is_default is True and storage.access_key_ct == CIPHERTEXT
            audit_ts = (await db.execute(select(AuditLog.ts).where(AuditLog.action == "seed.0"))).scalar_one()
            assert audit_ts == dt.datetime(2026, 9, 30, 8, 0, tzinfo=dt.UTC)
            usage = (await db.execute(select(UsageDaily))).scalar_one()
            assert usage.day == dt.date(2026, 10, 1) and usage.minutes == 4.5
            # Foreign keys hold: the session's agent and connection, the chunk's document.
            joined = await db.execute(
                select(func.count())
                .select_from(Session)
                .join(Agent, Agent.id == Session.agent_id)
                .join(LiveKitConnection, LiveKitConnection.id == Session.connection_id)
            )
            assert joined.scalar_one() == 1
            # The Postgres-only keyword column is filled for copied chunks.
            assert (
                await db.execute(text("SELECT count(*) FROM kb_chunks WHERE tsv IS NOT NULL"))
            ).scalar_one() == 1
            # The sequence moved past the copied ids: a new audit row does not collide.
            highest = (await db.execute(select(func.max(AuditLog.id)))).scalar_one()
            db.add(AuditLog(actor_type="system", action="after.copy", payload={}))
            await db.commit()
            newest = (
                await db.execute(select(AuditLog.id).where(AuditLog.action == "after.copy"))
            ).scalar_one()
            assert newest == highest + 1
    finally:
        await engine.dispose()


@requires_postgres
async def test_copy_database_dry_run_writes_nothing(source_db: Path, target_url: str) -> None:
    await _seed(source_db)

    report = await s2p.copy_database(str(source_db), target_url, dry_run=True)

    assert report.ok and not report.committed
    engine = create_async_engine(target_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            assert (await conn.execute(text("SELECT count(*) FROM agents"))).scalar_one() == 0
            # The migration seed is still there: the dry run rolled its removal back too.
            assert (await conn.execute(text("SELECT count(*) FROM workspaces"))).scalar_one() == 1
    finally:
        await engine.dispose()


@requires_postgres
async def test_copy_database_non_empty_target_is_refused_unless_truncate(
    source_db: Path, target_url: str
) -> None:
    await _seed(source_db)
    first = await s2p.copy_database(str(source_db), target_url)
    assert first.committed

    with pytest.raises(s2p.CopyRefusedError, match="--truncate"):
        await s2p.copy_database(str(source_db), target_url)

    again = await s2p.copy_database(str(source_db), target_url, truncate=True, batch_size=1)
    assert again.ok and again.committed and again.truncated
    assert {table.name: table.target for table in again.tables if table.skipped is None} == _source_counts(
        source_db
    )


@requires_postgres
def test_main_mismatched_alembic_head_is_refused_and_writes_nothing(
    source_db: Path, target_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with sqlite3.connect(source_db) as raw:
        raw.execute("UPDATE alembic_version SET version_num = 'v6_002_datasets'")
    monkeypatch.setenv("LKAP_DATABASE_URL", target_url)

    code = s2p.main(["--source", str(source_db)])

    assert code == s2p.EXIT_REFUSED
    assert "alembic heads differ" in capsys.readouterr().err

    async def agents() -> int:
        engine = create_async_engine(target_url, poolclass=NullPool)
        try:
            async with engine.connect() as conn:
                return int((await conn.execute(text("SELECT count(*) FROM workspaces"))).scalar_one())
        finally:
            await engine.dispose()

    assert asyncio.run(agents()) == 1  # still only the migration seed


@requires_postgres
def test_main_prints_counts_and_never_row_data(
    source_db: Path, target_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    asyncio.run(_seed(source_db))

    code = s2p.main(["--source", str(source_db), "--target", target_url])

    assert code == s2p.EXIT_OK
    output = capsys.readouterr()
    assert "result: copied and committed" in output.out
    assert "credentials" in output.out and "MISMATCH" not in output.out
    for secret in (CIPHERTEXT.decode(), WEBHOOK_SECRET.decode(), "Be kind.", "owner@example.com"):
        assert secret not in output.out and secret not in output.err
