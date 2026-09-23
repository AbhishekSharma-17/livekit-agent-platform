"""Redis connection helper for the `arq` backend.

Kept separate from `service.py` so tests can hand `JobsService` a fake
`ArqRedis` (built over `fakeredis`, see `tests/test_jobs.py`) instead of
dialling a real Redis.
"""

from __future__ import annotations

from arq.connections import ArqRedis, RedisSettings, create_pool


async def create_arq_pool(redis_url: str) -> ArqRedis:
    """Create a real `ArqRedis` connection pool from a `redis://` URL."""
    return await create_pool(RedisSettings.from_dsn(redis_url))
