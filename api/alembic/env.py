"""Alembic environment for lkap_api.

Deliberately independent of `lkap_api.settings`: migrations must run without
LiveKit credentials or a master key. The url comes from `alembic -x url=...`,
then `LKAP_DATABASE_URL`, then the SQLite default under `LKAP_DATA_DIR`.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig
from pathlib import Path

from alembic.runtime.migration import MigrationContext
from sqlalchemy import Boolean, Column, Connection, Integer, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from lkap_api.db.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

DEFAULT_DATA_DIR = "./data"


def database_url() -> str:
    """Resolve the migration target url and make sure its directory exists."""
    override = context.get_x_argument(as_dictionary=True).get("url")
    url = override or os.environ.get("LKAP_DATABASE_URL")
    if not url:
        data_dir = os.environ.get("LKAP_DATA_DIR", DEFAULT_DATA_DIR)
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        url = f"sqlite+aiosqlite:///{data_dir}/lkap.db"
    if url.startswith("sqlite"):
        # SQLite will not create the parent directory of the database file.
        path = url.split(":///", 1)[-1]
        parent = Path(path).expanduser().parent
        if str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)
    return url


def compare_type(
    context: MigrationContext,
    inspected_column: Column[object],
    metadata_column: Column[object],
    inspected_type: object,
    metadata_type: object,
) -> bool | None:
    """Treat an SQLite INTEGER column as matching a model ``Boolean``; defer everything else.

    The flag columns were ``Integer`` until the first Postgres run (2026-09-24) and are
    ``Boolean`` now. SQLite stores a ``Boolean`` as 0/1 in an INTEGER-affinity column, so
    databases migrated before the change are correct as they are; without this, autogenerate
    and ``alembic check`` would report their unchanged storage as drift. Postgres is compared
    strictly (``None`` falls back to Alembic's own comparison).
    """
    if (
        context.dialect.name == "sqlite"
        and isinstance(metadata_type, Boolean)
        and isinstance(inspected_type, Integer)
    ):
        return False
    return None


#: Database objects the migrations create that are deliberately not ORM models
#: (v5_001_knowledge_p0's lexical index): the SQLite FTS5 table and the shadow
#: tables FTS5 creates for it, and the Postgres generated ``tsv`` column and its
#: GIN index on ``kb_chunks``. Autogenerate (and so ``alembic check``) skips them.
UNMODELLED_TABLE_PREFIX = "kb_chunks_fts"
UNMODELLED_COLUMNS = {("kb_chunks", "tsv")}
UNMODELLED_INDEXES = {"ix_kb_chunks_tsv"}


def include_object(
    obj: object, name: str | None, type_: str, reflected: bool, compare_to: object | None
) -> bool:
    """Leave the unmodelled lexical-index objects out of autogenerate comparisons."""
    if type_ == "table" and name is not None and name.startswith(UNMODELLED_TABLE_PREFIX):
        return False
    if type_ == "column" and name is not None:
        table = getattr(getattr(obj, "table", None), "name", None)
        if (table, name) in UNMODELLED_COLUMNS:
            return False
    if type_ == "index" and name in UNMODELLED_INDEXES:
        return False
    return True


def run_migrations_offline() -> None:
    """Emit SQL for the configured url without connecting."""
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations on an open, synchronous connection."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
        compare_type=compare_type,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Connect with the async engine and run the migrations."""
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = database_url()
    connectable = async_engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Entry point for `alembic upgrade`."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
