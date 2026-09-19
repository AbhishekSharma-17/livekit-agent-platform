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

from sqlalchemy import Connection, pool
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
        compare_type=True,
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
