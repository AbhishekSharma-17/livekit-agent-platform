"""Async engine and session plumbing.

One :class:`Database` per process lives on ``app.state.db``; request handlers
receive an :class:`~sqlalchemy.ext.asyncio.AsyncSession` through the
:func:`get_db` dependency, which commits on success and rolls back on error.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Request
from sqlalchemy import event
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import ConnectionPoolEntry

from lkap_api.db.models import Base

#: How long (ms) a SQLite connection waits for another writer's lock before
#: failing with ``database is locked`` (V4-18, R-V4-65).
SQLITE_BUSY_TIMEOUT_MS = 10_000


def _set_sqlite_pragmas(dbapi_connection: DBAPIConnection, _record: ConnectionPoolEntry) -> None:
    """Configure every new SQLite connection (a ``connect`` event listener).

    * ``busy_timeout`` first: a short writer makes the others wait instead of
      failing at once (and the WAL switch below may itself meet a lock).
    * ``journal_mode=WAL`` (V4-18, R-V4-65): readers no longer block on a
      writer and a writer no longer waits for readers. The mode is persistent
      on the database file (``-wal``/``-shm`` sidecar files appear beside it);
      an in-memory database reports ``memory`` and is unaffected.
    * ``foreign_keys=ON`` so ``ON DELETE CASCADE`` actually cascades.
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


def _configure_sqlite(engine: AsyncEngine) -> None:
    """Register :func:`_set_sqlite_pragmas` on ``engine`` (SQLite engines only; Postgres is untouched)."""
    event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)


class Database:
    """Owns the async engine and session factory for one process."""

    def __init__(self, url: str, *, echo: bool = False, **engine_kwargs: Any) -> None:
        """Create the engine.

        Args:
            url: SQLAlchemy async URL, e.g. ``sqlite+aiosqlite:///./data/lkap.db``.
            echo: Echo SQL to the logger (debugging only).
            engine_kwargs: Extra keyword arguments for ``create_async_engine``.
        """
        self.url = url
        self.engine: AsyncEngine = create_async_engine(url, echo=echo, future=True, **engine_kwargs)
        if url.startswith("sqlite"):
            _configure_sqlite(self.engine)
        self.sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.engine, expire_on_commit=False, autoflush=False
        )

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield a session, committing on success and rolling back on failure."""
        async with self.sessionmaker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def create_all(self) -> None:
        """Create every table from the model metadata (tests and first-run dev only)."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def drop_all(self) -> None:
        """Drop every table from the model metadata (Postgres test teardown only)."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

    async def dispose(self) -> None:
        """Close all pooled connections."""
        await self.engine.dispose()


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a request-scoped :class:`AsyncSession`."""
    database: Database = request.app.state.db
    async with database.session() as session:
        yield session
