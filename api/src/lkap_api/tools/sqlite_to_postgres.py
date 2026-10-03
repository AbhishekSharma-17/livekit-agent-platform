"""``python -m lkap_api.tools.sqlite_to_postgres``: copy a SQLite database into Postgres (V6-37).

Usage::

    python -m lkap_api.tools.sqlite_to_postgres --source /data/lkap.db [--target <url>]
        [--dry-run] [--truncate] [--batch-size 500] [--alembic-ini <path>]

``--source`` is a file path or a ``sqlite`` URL. ``--target`` is a Postgres URL and
defaults to ``LKAP_DATABASE_URL``, so the password never has to be on the command line
(``ps`` shows arguments). ``postgresql://`` and ``postgres://`` are turned into the
``postgresql+asyncpg://`` form the api uses.

What it does, in this order:

1. Refuses unless both databases carry the same ``alembic_version``. Migrate the target with
   ``alembic upgrade head`` first. A head that differs from the running code's is a warning.
2. Refuses a target that already holds data unless ``--truncate`` is passed. A freshly
   migrated database is not "data": migrations ``v2_001`` and ``v2_002`` seed the default
   workspace and, when ``LIVEKIT_*`` and ``LKAP_MASTER_KEY`` were set, a default
   connection. Those seed rows are deleted before the copy because the source has its own.
3. Copies every table of the api's metadata in foreign-key order, in batches, inside one
   target transaction. Values SQLite stores loosely are converted on the way: JSON text to
   JSON, ``0``/``1`` to booleans, text or naive datetimes to UTC (naive for the api's
   ``UtcDateTime`` columns, aware for any ``DateTime(timezone=True)`` column; the api has
   none today), text dates to dates, and text or memoryview to bytes and numbers.
4. Skips :data:`SKIPPED_TABLES`. Knowledge-base vectors are rebuilt after the cutover with
   ``python -m lkap_api.kb.jobs reindex --all``.
5. Resets every serial or identity sequence to the copied maximum.
6. Counts the target inside the same transaction and commits only when every table's
   source, copied and target counts agree. ``--dry-run`` does all of it and rolls back.

It prints a per-table count report and never prints row data: encrypted columns are copied
as they are (the master key is not needed) and database errors are shown without their
parameters or literal values. Exit codes: ``0`` copied (or dry run clean), ``1`` a count
mismatch or a failed copy (rolled back), ``2`` refused before anything was written.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Table,
    event,
    func,
    inspect,
    select,
    text,
)
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import ConnectionPoolEntry, NullPool
from sqlalchemy.types import TypeDecorator, TypeEngine

from lkap_api.db.constants import DEFAULT_CONNECTION_SLUG, DEFAULT_WORKSPACE_ID
from lkap_api.db.models import Base

#: Tables never copied, with the reason the report shows.
SKIPPED_TABLES: Final[Mapping[str, str]] = {
    "alembic_version": "migration bookkeeping, checked instead",
    "kb_vectors": "vectors, rebuilt by reindex",
    "lkap_memory": "Mem0 vectors, not carried over",
}
#: Where ``--target`` comes from when it is not passed.
TARGET_ENV: Final = "LKAP_DATABASE_URL"
DEFAULT_BATCH_SIZE: Final = 500
EXIT_OK: Final = 0
EXIT_FAILED: Final = 1
EXIT_REFUSED: Final = 2

#: Tables a fresh ``alembic upgrade head`` seeds (``v2_001``, ``v2_002``).
_SEED_TABLES: Final = ("livekit_connections", "workspaces")
#: A quoted literal inside a database message, possibly a stored value.
_QUOTED_LITERAL = re.compile(r"b?'(?:[^'\\]|\\.)*'")
_TRUE_TEXT: Final = frozenset({"1", "true", "t", "yes", "y", "on"})
_FALSE_TEXT: Final = frozenset({"0", "false", "f", "no", "n", "off"})


class CopyRefusedError(Exception):
    """A precondition failed before anything was written."""


class CopyFailedError(Exception):
    """The copy failed part way and the target transaction was rolled back."""


class CoercionError(ValueError):
    """A stored value could not be converted for its column. The message never holds the value."""

    def __init__(self, table: str, column: str, wanted: str, reason: str) -> None:
        """Name the table, the column and the wanted type, never the value itself."""
        super().__init__(f"{table}.{column}: a stored value is not a valid {wanted} ({reason})")


@dataclass(slots=True)
class TableReport:
    """Row counts of one table."""

    name: str
    source: int | None = None
    copied: int = 0
    target: int | None = None
    skipped: str | None = None

    @property
    def ok(self) -> bool:
        """Whether the three counts agree (a skipped table is always fine)."""
        return self.skipped is not None or (self.source == self.copied == self.target)


@dataclass(slots=True)
class CopyReport:
    """What :func:`copy_database` did."""

    source: str
    target: str
    revision: str
    dry_run: bool
    tables: list[TableReport] = field(default_factory=list)
    truncated: bool = False
    seed_rows_removed: int = 0
    sequences_reset: int = 0
    committed: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Whether every table's counts agree."""
        return all(table.ok for table in self.tables)


# --------------------------------------------------------------------------- urls
def resolve_source_url(value: str) -> str:
    """Return the async SQLite URL for ``--source`` (a file path or a ``sqlite`` URL).

    Raises:
        CopyRefusedError: The URL is not SQLite, or the file does not exist.
    """
    if "://" in value:
        url = make_url(value)
        if url.get_backend_name() != "sqlite":
            raise CopyRefusedError("--source must be a SQLite file path or a sqlite URL")
        database = url.database or ""
        if database and database != ":memory:" and not Path(database).expanduser().is_file():
            raise CopyRefusedError("the --source database file does not exist")
        return url.set(drivername="sqlite+aiosqlite").render_as_string(hide_password=False)
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise CopyRefusedError("the --source database file does not exist")
    return f"sqlite+aiosqlite:///{path}"


def resolve_target_url(value: str | None, environ: Mapping[str, str] | None = None) -> str:
    """Return the asyncpg URL for ``--target``, falling back to ``LKAP_DATABASE_URL``.

    Raises:
        CopyRefusedError: No target is given, or it is not Postgres.
    """
    raw = value or (os.environ if environ is None else environ).get(TARGET_ENV, "")
    if not raw.strip():
        raise CopyRefusedError(f"no target: pass --target or set {TARGET_ENV} to the Postgres URL")
    url = make_url(raw.strip().replace("postgres://", "postgresql://", 1))
    if url.get_backend_name() != "postgresql":
        raise CopyRefusedError("the target must be a Postgres URL (postgresql+asyncpg://...)")
    return url.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False)


def display_url(url: str) -> str:
    """``url`` with its password masked, for the report."""
    return make_url(url).render_as_string(hide_password=True)


# --------------------------------------------------------------------------- coercion
def _base_type(type_: TypeEngine[Any]) -> TypeEngine[Any]:
    if isinstance(type_, TypeDecorator):
        impl: TypeEngine[Any] = type_.impl_instance
        return impl
    return type_


def _to_datetime(value: object, *, aware: bool) -> dt.datetime:
    if isinstance(value, dt.datetime):
        parsed = value
    elif isinstance(value, str):
        stripped = value.strip()
        if stripped.endswith(("Z", "z")):
            stripped = stripped[:-1] + "+00:00"
        parsed = dt.datetime.fromisoformat(stripped)
    elif isinstance(value, int | float) and not isinstance(value, bool):
        parsed = dt.datetime.fromtimestamp(value, dt.UTC)
    else:
        raise TypeError(type(value).__name__)
    # A naive value is UTC: that is how the api's `UtcDateTime` stores every timestamp.
    parsed = parsed.astimezone(dt.UTC) if parsed.tzinfo is not None else parsed.replace(tzinfo=dt.UTC)
    return parsed if aware else parsed.replace(tzinfo=None)


def _to_date(value: object) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        try:
            return dt.date.fromisoformat(stripped)
        except ValueError:
            return dt.datetime.fromisoformat(stripped).date()
    raise TypeError(type(value).__name__)


def _to_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float) and value in (0, 1):
        return bool(value)
    if isinstance(value, str | bytes):
        word = (value.decode() if isinstance(value, bytes) else value).strip().lower()
        if word in _TRUE_TEXT:
            return True
        if word in _FALSE_TEXT:
            return False
    raise ValueError("not a boolean")


def _to_json(value: object) -> object:
    if isinstance(value, bytes | bytearray | memoryview):
        value = bytes(value).decode("utf-8")
    if isinstance(value, str):
        return json.loads(value)
    # SQLite gives a JSON column NUMERIC affinity, so a bare number comes back as a number.
    if isinstance(value, int | float | bool | dict | list):
        return value
    raise TypeError(type(value).__name__)


def _to_bytes(value: object) -> bytes:
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value)
    if isinstance(value, str):
        return value.encode("utf-8")
    raise TypeError(type(value).__name__)


def _to_number(value: object, *, asdecimal: bool) -> float | Decimal:
    if isinstance(value, bool):
        raise TypeError("bool")
    if isinstance(value, int | float | Decimal | str):
        return Decimal(str(value).strip()) if asdecimal else float(value)
    raise TypeError(type(value).__name__)


def _to_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError("not a whole number")
        return int(value)
    if isinstance(value, str):
        return int(value.strip())
    raise TypeError(type(value).__name__)


def _to_str(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value).decode("utf-8")
    if isinstance(value, int | float):
        return str(value)
    raise TypeError(type(value).__name__)


def coerce_value(table: str, column: Column[Any], value: object) -> object:
    """Convert one raw SQLite value into what ``column`` accepts on Postgres.

    Args:
        table: The table name (for the error message).
        column: The metadata column the value belongs to.
        value: The value as SQLite returned it (``int``, ``float``, ``str``, ``bytes`` or ``None``).

    Returns:
        The converted value; ``None`` stays ``None``.

    Raises:
        CoercionError: The value cannot be converted. The message never includes it.
    """
    if value is None:
        return None
    kind = _base_type(column.type)
    try:
        if isinstance(kind, DateTime):
            return _to_datetime(value, aware=bool(kind.timezone))
        if isinstance(kind, Date):
            return _to_date(value)
        if isinstance(kind, Boolean):
            return _to_bool(value)
        if isinstance(kind, JSON):
            return _to_json(value)
        if isinstance(kind, LargeBinary):
            return _to_bytes(value)
        if isinstance(kind, Numeric):
            return _to_number(value, asdecimal=bool(kind.asdecimal))
        if isinstance(kind, Integer):
            return _to_int(value)
        if isinstance(kind, String):
            return _to_str(value)
    except (ValueError, TypeError, ArithmeticError, UnicodeDecodeError) as exc:
        # `from None`: the original message can quote the value.
        raise CoercionError(table, column.name, type(kind).__name__, type(exc).__name__) from None
    return value


# --------------------------------------------------------------------------- errors
def safe_db_error(exc: DBAPIError) -> str:
    """A one-line description of a database error with no parameters and no literal values.

    ``str(exc)`` on a SQLAlchemy error appends the statement's parameters, which can hold
    ciphertext, so this reads the driver's own exception instead, keeps its first line and
    masks every single-quoted literal (Postgres quotes identifiers with double quotes, so
    table and constraint names survive).
    """
    inner: BaseException = exc.orig if isinstance(exc.orig, BaseException) else exc
    inner = inner.__cause__ or inner
    lines = str(inner).strip().splitlines()
    message = _QUOTED_LITERAL.sub("'<redacted>'", lines[0] if lines else "")
    return f"{type(inner).__name__}: {message[:300]}"


# --------------------------------------------------------------------------- database helpers
def _source_engine(url: str) -> AsyncEngine:
    engine = create_async_engine(url, poolclass=NullPool)

    def _read_only(dbapi_connection: DBAPIConnection, _record: ConnectionPoolEntry) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA query_only=ON")
        finally:
            cursor.close()

    event.listen(engine.sync_engine, "connect", _read_only)
    return engine


async def _table_names(conn: AsyncConnection) -> set[str]:
    def names(sync: Connection) -> set[str]:
        return set(inspect(sync).get_table_names())

    return await conn.run_sync(names)


async def _column_names(conn: AsyncConnection, table: str) -> set[str]:
    def names(sync: Connection) -> set[str]:
        return {str(column["name"]) for column in inspect(sync).get_columns(table)}

    return await conn.run_sync(names)


async def alembic_revision(conn: AsyncConnection) -> str | None:
    """The database's ``alembic_version`` (heads joined by ``,``), or ``None`` when unmigrated."""
    if "alembic_version" not in await _table_names(conn):
        return None
    rows = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalars().all()
    return ",".join(sorted(str(row) for row in rows)) or None


def code_head(alembic_ini: Path | None = None) -> str | None:
    """The migration head of the running code, or ``None`` when no ``alembic.ini`` is found.

    Looks at ``alembic_ini``, then ``./alembic.ini`` (the api image's working directory),
    then the source checkout's ``api/alembic.ini``.
    """
    candidates = (
        [alembic_ini]
        if alembic_ini is not None
        else [Path.cwd() / "alembic.ini", Path(__file__).resolve().parents[3] / "alembic.ini"]
    )
    for candidate in candidates:
        if candidate.is_file() and (candidate.parent / "alembic").is_dir():
            config = Config(str(candidate))
            config.set_main_option("script_location", str(candidate.parent / "alembic"))
            return ",".join(sorted(ScriptDirectory.from_config(config).get_heads()))
    return None


async def _count(conn: AsyncConnection, table: Table) -> int:
    return int((await conn.execute(select(func.count()).select_from(table))).scalar_one())


async def _only_migration_seed(conn: AsyncConnection, counts: Mapping[str, int]) -> bool:
    """Whether the target's rows are exactly what a fresh ``alembic upgrade head`` seeds."""
    if any(count for name, count in counts.items() if name not in _SEED_TABLES):
        return False
    if counts.get("workspaces", 0) > 1 or counts.get("livekit_connections", 0) > 1:
        return False
    workspaces = Base.metadata.tables["workspaces"]
    ids = (await conn.execute(select(workspaces.c.id))).scalars().all()
    if ids and list(ids) != [DEFAULT_WORKSPACE_ID]:
        return False
    connections = Base.metadata.tables["livekit_connections"]
    rows = (await conn.execute(select(connections.c.workspace_id, connections.c.slug))).all()
    return not rows or [tuple(row) for row in rows] == [(DEFAULT_WORKSPACE_ID, DEFAULT_CONNECTION_SLUG)]


async def _remove_migration_seed(conn: AsyncConnection) -> int:
    removed = 0
    for name in _SEED_TABLES:
        result = await conn.execute(Base.metadata.tables[name].delete())
        removed += max(result.rowcount, 0)
    return removed


async def _truncate(conn: AsyncConnection, tables: Sequence[Table]) -> None:
    quote = conn.dialect.identifier_preparer.quote
    names = ", ".join(quote(table.name) for table in tables)
    # CASCADE also empties `kb_vectors`, whose rows point at `kb_chunks`.
    await conn.execute(text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))


async def _copy_table(
    source: AsyncConnection,
    target: AsyncConnection,
    table: Table,
    columns: Sequence[Column[Any]],
    batch_size: int,
) -> TableReport:
    report = TableReport(name=table.name, source=await _count(source, table))
    quote = source.dialect.identifier_preparer.quote
    selected = ", ".join(quote(column.name) for column in columns)
    order = ", ".join(quote(column.name) for column in table.primary_key.columns) or "rowid"
    result = await source.stream(text(f"SELECT {selected} FROM {quote(table.name)} ORDER BY {order}"))
    async for partition in result.partitions(batch_size):
        rows = [
            {column.key: coerce_value(table.name, column, row[index]) for index, column in enumerate(columns)}
            for row in partition
        ]
        try:
            await target.execute(table.insert(), rows)
        except DBAPIError as exc:
            raise CopyFailedError(f"{table.name}: {safe_db_error(exc)}") from None
        report.copied += len(rows)
    return report


async def _reset_sequences(conn: AsyncConnection, tables: Sequence[Table]) -> int:
    """Move each serial or identity sequence past the copied maximum; return how many moved."""
    quote = conn.dialect.identifier_preparer.quote
    reset = 0
    for table in tables:
        key = list(table.primary_key.columns)
        if len(key) != 1 or not isinstance(_base_type(key[0].type), Integer):
            continue
        column = key[0]
        sequence = (
            await conn.execute(
                text("SELECT pg_get_serial_sequence(:table, :column)"),
                {"table": table.name, "column": column.name},
            )
        ).scalar()
        if sequence is None:
            continue
        maximum = f"(SELECT MAX({quote(column.name)}) FROM {quote(table.name)})"
        statement = (
            f"SELECT setval(CAST(:sequence AS regclass), COALESCE({maximum}, 1), {maximum} IS NOT NULL)"
        )
        await conn.execute(
            text(statement),
            {"sequence": sequence},
        )
        reset += 1
    return reset


# --------------------------------------------------------------------------- the copy
async def copy_database(
    source: str,
    target: str | None,
    *,
    dry_run: bool = False,
    truncate: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
    alembic_ini: Path | None = None,
) -> CopyReport:
    """Copy every api table from a SQLite database into a migrated Postgres database.

    Args:
        source: A SQLite file path or URL (see :func:`resolve_source_url`).
        target: A Postgres URL, or ``None`` for ``LKAP_DATABASE_URL`` (see :func:`resolve_target_url`).
        dry_run: Do everything, then roll back.
        truncate: Empty a target that already holds data instead of refusing.
        batch_size: Rows read and inserted per batch.
        alembic_ini: Where to find the running code's migrations (for the head warning).

    Returns:
        The report. ``committed`` is true only when every count agreed and this was not a dry run.

    Raises:
        CopyRefusedError: A precondition failed. Nothing was written.
        CopyFailedError: The copy failed and was rolled back.
    """
    if batch_size < 1:
        raise CopyRefusedError("--batch-size must be at least 1")
    source_url, target_url = resolve_source_url(source), resolve_target_url(target)
    source_engine = _source_engine(source_url)
    target_engine = create_async_engine(target_url, poolclass=NullPool)
    try:
        async with source_engine.connect() as src, target_engine.connect() as tgt:
            return await _copy(
                src,
                tgt,
                source_url=source_url,
                target_url=target_url,
                dry_run=dry_run,
                truncate=truncate,
                batch_size=batch_size,
                alembic_ini=alembic_ini,
            )
    except DBAPIError as exc:
        raise CopyFailedError(safe_db_error(exc)) from None
    finally:
        await source_engine.dispose()
        await target_engine.dispose()


async def _copy(
    src: AsyncConnection,
    tgt: AsyncConnection,
    *,
    source_url: str,
    target_url: str,
    dry_run: bool,
    truncate: bool,
    batch_size: int,
    alembic_ini: Path | None,
) -> CopyReport:
    source_revision, target_revision = await alembic_revision(src), await alembic_revision(tgt)
    if source_revision is None:
        raise CopyRefusedError("the source has no alembic_version table, so its schema is unknown")
    if target_revision is None:
        raise CopyRefusedError(
            "the target has no alembic_version table. Run alembic upgrade head on it first"
        )
    if source_revision != target_revision:
        raise CopyRefusedError(
            f"alembic heads differ (source {source_revision}, target {target_revision}). "
            "Migrate both to the same head first"
        )
    report = CopyReport(
        source=display_url(source_url),
        target=display_url(target_url),
        revision=source_revision,
        dry_run=dry_run,
    )
    head = code_head(alembic_ini)
    if head is not None and head != source_revision:
        report.warnings.append(f"both databases are at {source_revision} but this code's head is {head}")

    tables = [table for table in Base.metadata.sorted_tables if table.name not in SKIPPED_TABLES]
    source_tables, target_tables = await _table_names(src), await _table_names(tgt)
    missing = sorted(table.name for table in tables if table.name not in target_tables)
    if missing:
        raise CopyRefusedError(f"the target is missing tables: {', '.join(missing)}")
    missing = sorted(table.name for table in tables if table.name not in source_tables)
    if missing:
        raise CopyRefusedError(f"the source is missing tables: {', '.join(missing)}")
    plan: list[tuple[Table, list[Column[Any]]]] = []
    for table in tables:
        present = await _column_names(src, table.name)
        absent = [column.name for column in table.columns if column.name not in present]
        if absent:
            raise CopyRefusedError(f"the source table {table.name} is missing columns: {', '.join(absent)}")
        plan.append((table, list(table.columns)))

    # The checks above ran in an autobegun read transaction. End it so the copy gets one of its own.
    await tgt.rollback()
    transaction = await tgt.begin()
    try:
        counts = {table.name: await _count(tgt, table) for table in tables}
        if any(counts.values()):
            if truncate:
                await _truncate(tgt, tables)
                report.truncated = True
            elif await _only_migration_seed(tgt, counts):
                report.seed_rows_removed = await _remove_migration_seed(tgt)
            else:
                filled = ", ".join(f"{name} ({count})" for name, count in counts.items() if count)
                raise CopyRefusedError(
                    f"the target already holds rows in: {filled}. Pass --truncate to empty it first"
                )
        for table, columns in plan:
            report.tables.append(await _copy_table(src, tgt, table, columns, batch_size))
        report.sequences_reset = await _reset_sequences(tgt, tables)
        for table_report, (table, _columns) in zip(report.tables, plan, strict=True):
            table_report.target = await _count(tgt, table)
        if dry_run or not report.ok:
            await transaction.rollback()
        else:
            await transaction.commit()
            report.committed = True
    except BaseException:
        if transaction.is_active:
            await transaction.rollback()
        raise
    for name, reason in SKIPPED_TABLES.items():
        if name in source_tables or name in target_tables:
            report.tables.append(TableReport(name=name, skipped=reason))
        else:
            report.tables.append(TableReport(name=name, skipped=f"{reason} (absent)"))
    return report


# --------------------------------------------------------------------------- the CLI
def format_report(report: CopyReport) -> str:
    """The report as plain text: one line per table, counts only."""
    width = max(len(table.name) for table in report.tables) if report.tables else 10
    lines = [
        f"source:   {report.source}",
        f"target:   {report.target}",
        f"alembic:  {report.revision} (both)",
    ]
    lines += [f"warning:  {warning}" for warning in report.warnings]
    if report.truncated:
        lines.append("target:   emptied first (--truncate)")
    if report.seed_rows_removed:
        lines.append(f"target:   removed {report.seed_rows_removed} migration seed row(s) first")
    lines.append(f"{'table':<{width}}  {'source':>8}  {'copied':>8}  {'target':>8}  status")

    def number(value: int | None) -> str:
        return "-" if value is None else str(value)

    for table in report.tables:
        if table.skipped is not None:
            lines.append(f"{table.name:<{width}}  {'-':>8}  {'-':>8}  {'-':>8}  skipped, {table.skipped}")
            continue
        status = "ok" if table.ok else "MISMATCH"
        lines.append(
            f"{table.name:<{width}}  {number(table.source):>8}  {table.copied:>8}  "
            f"{number(table.target):>8}  {status}"
        )
    lines.append(f"sequences reset: {report.sequences_reset}")
    if not report.ok:
        lines.append("result: MISMATCH, rolled back")
    elif report.dry_run:
        lines.append("result: dry run clean, rolled back")
    else:
        lines.append("result: copied and committed")
    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m lkap_api.tools.sqlite_to_postgres",
        description="Copy the api's SQLite database into a Postgres database migrated to the same head.",
    )
    parser.add_argument("--source", required=True, help="SQLite file path or sqlite URL")
    parser.add_argument(
        "--target", default=None, help=f"Postgres URL (default: {TARGET_ENV}, keeps the password out of ps)"
    )
    parser.add_argument("--dry-run", action="store_true", help="copy inside a transaction, then roll back")
    parser.add_argument("--truncate", action="store_true", help="empty a target that already holds rows")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="rows per batch")
    parser.add_argument("--alembic-ini", type=Path, default=None, help="alembic.ini of the running code")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the copy CLI and return a process exit code (0 copied, 1 failed, 2 refused)."""
    try:
        args = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else EXIT_REFUSED
    try:
        report = asyncio.run(
            copy_database(
                args.source,
                args.target,
                dry_run=args.dry_run,
                truncate=args.truncate,
                batch_size=args.batch_size,
                alembic_ini=args.alembic_ini,
            )
        )
    except CopyRefusedError as exc:
        sys.stderr.write(f"refused: {exc}\n")
        return EXIT_REFUSED
    except (CopyFailedError, CoercionError) as exc:
        sys.stderr.write(f"failed, rolled back: {exc}\n")
        return EXIT_FAILED
    sys.stdout.write(format_report(report))
    return EXIT_OK if report.ok else EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
