"""Single-instance guard: file lock (dev) and Redis lease (prod)."""

from __future__ import annotations

from pathlib import Path

import fakeredis

from lkap_supervisor.lease import LEASE_KEY, FileLease, RedisLease


async def test_file_lease_is_exclusive_until_released(tmp_path: Path) -> None:
    first = FileLease(tmp_path / "supervisor.lock")
    second = FileLease(tmp_path / "supervisor.lock")

    assert await first.acquire() is True
    assert await second.acquire() is False
    assert await first.renew() is True
    await first.release()
    assert await second.acquire() is True
    await second.release()


async def test_redis_lease_is_exclusive_renewable_and_released() -> None:
    redis = fakeredis.FakeAsyncRedis()
    first = RedisLease(redis, ttl_s=30)
    second = RedisLease(redis, ttl_s=30)

    assert await first.acquire() is True
    assert await second.acquire() is False
    assert await first.renew() is True
    assert 0 < await redis.pttl(LEASE_KEY) <= 30_000
    await first.release()
    assert await redis.get(LEASE_KEY) is None
    assert await second.acquire() is True


async def test_a_redis_lease_taken_over_is_reported_lost() -> None:
    redis = fakeredis.FakeAsyncRedis()
    lease = RedisLease(redis, ttl_s=30)
    assert await lease.acquire() is True

    await redis.set(LEASE_KEY, "someone-else")

    assert await lease.renew() is False
    await lease.release()
    assert await redis.get(LEASE_KEY) == b"someone-else"
