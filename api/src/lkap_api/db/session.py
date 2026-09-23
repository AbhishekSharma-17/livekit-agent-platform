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


def _enable_sqlite_foreign_keys(engine: AsyncEngine) -> None:
    """Turn on ``PRAGMA foreign_keys`` so ``ON DELETE CASCADE`` actually cascades."""

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragma(dbapi_connection: DBAPIConnection, _record: ConnectionPoolEntry) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()


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
            _enable_sqlite_foreign_keys(self.engine)
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
