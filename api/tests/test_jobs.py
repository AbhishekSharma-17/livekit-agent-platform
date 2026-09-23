"""Tests for `lkap_api.jobs`: the `inline`/`arq` job engine (CONTRACTS-V2 §6)."""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator, Iterator
from typing import Any

import fakeredis
import pytest
from arq.connections import ArqRedis
from fastapi import BackgroundTasks
from redis.asyncio import ConnectionPool

from lkap_api.db.models import Job, utcnow
from lkap_api.db.session import Database
from lkap_api.jobs.context import JobContext
from lkap_api.jobs.registry import job, unregister
from lkap_api.jobs.service import NO_HANDLER_ERROR, JobsService
from lkap_api.jobs.settings import JobsSettings
from lkap_api.settings import Settings
from lkap_api.vault import Vault

TEST_KIND = "test_kind_v2_08"


@pytest.fixture(autouse=True)
def _cleanup_test_kind() -> Iterator[None]:
    """Only unregister *this suite's* kind — other modules' handlers
    (`kb_ingest`, `webhook_delivery`, `qa_scoring`, `usage_daily_rollup`)
    registered earlier in the same pytest process must stay put.
    """
    yield
    unregister(TEST_KIND)


@pytest.fixture
def vault(settings: Settings) -> Vault:
    return Vault(settings.master_key)


@pytest.fixture
async def inline_jobs(database: Database, settings: Settings, vault: Vault) -> AsyncIterator[JobsService]:
    service = JobsService(database=database, settings=settings, vault=vault)
    try:
        yield service
    finally:
        await service.aclose()


async def _job_row(database: Database, job_id: str) -> Job:
    async with database.session() as session:
        row = await session.get(Job, job_id)
        assert row is not None
        session.expunge(row)
        return row


async def test_inline_backend_runs_a_job_to_completion(inline_jobs: JobsService, database: Database) -> None:
    calls: list[dict[str, Any]] = []

    @job(TEST_KIND)
    async def _handle(ctx: JobContext, payload: dict[str, Any]) -> None:
        calls.append(payload)

    job_id = await inline_jobs.enqueue(TEST_KIND, {"x": 1})

    assert calls == [{"x": 1}]
    row = await _job_row(database, job_id)
    assert row.status == "done"
    assert row.last_error is None


async def test_inline_backend_uses_background_tasks_when_given(
    inline_jobs: JobsService, database: Database
) -> None:
    calls: list[int] = []

    @job(TEST_KIND)
    async def _handle(ctx: JobContext, payload: dict[str, Any]) -> None:
        calls.append(1)

    tasks = BackgroundTasks()
    job_id = await inline_jobs.enqueue(TEST_KIND, {}, background_tasks=tasks)
    # Not run yet: BackgroundTasks defers until awaited (Starlette runs them
    # after the response; a test driving them directly awaits the object).
    assert calls == []
    await tasks()
    assert calls == [1]
    row = await _job_row(database, job_id)
    assert row.status == "done"


async def test_run_due_runs_a_future_job_once_it_is_due(inline_jobs: JobsService, database: Database) -> None:
    calls: list[int] = []

    @job(TEST_KIND)
    async def _handle(ctx: JobContext, payload: dict[str, Any]) -> None:
        calls.append(1)

    future = utcnow() + dt.timedelta(hours=1)
    job_id = await inline_jobs.enqueue(TEST_KIND, {}, run_at=future)
    assert calls == []
    row = await _job_row(database, job_id)
    assert row.status == "pending"

    ran = await inline_jobs.run_due(now=utcnow())
    assert ran == 0
    assert calls == []

    ran = await inline_jobs.run_due(now=future + dt.timedelta(seconds=1))
    assert ran == 1
    assert calls == [1]
    row = await _job_row(database, job_id)
    assert row.status == "done"


async def test_unregistered_kind_marks_the_job_failed_without_looping(
    inline_jobs: JobsService, database: Database
) -> None:
    job_id = await inline_jobs.enqueue("no_such_kind", {})
    row = await _job_row(database, job_id)
    assert row.status == "failed"
    assert row.last_error == NO_HANDLER_ERROR


async def test_handler_exception_retries_then_marks_dead(
    inline_jobs: JobsService, database: Database, settings: Settings, vault: Vault
) -> None:
    attempts: list[int] = []

    @job(TEST_KIND)
    async def _always_fails(ctx: JobContext, payload: dict[str, Any]) -> None:
        attempts.append(1)
        raise RuntimeError("boom")

    max_attempts = JobsSettings().jobs_max_attempts
    job_id = await inline_jobs.enqueue(TEST_KIND, {})  # attempt 1, already run inline
    for _ in range(max_attempts - 1):
        await inline_jobs.run_one(job_id)

    row = await _job_row(database, job_id)
    assert row.status == "dead"
    assert row.attempts == max_attempts
    assert len(attempts) == max_attempts
    assert row.last_error == "boom"


async def test_arq_backend_pushes_the_job_onto_a_fake_redis_queue(
    database: Database, settings: Settings, vault: Vault
) -> None:
    pool = ConnectionPool(connection_class=fakeredis.aioredis.FakeAsyncRedisConnection)
    redis = ArqRedis(connection_pool=pool)
    service = JobsService(
        database=database,
        settings=settings,
        vault=vault,
        jobs_settings=JobsSettings(jobs_backend="arq", redis_url="redis://fake"),
        arq_redis=redis,
    )
    try:
        job_id = await service.enqueue(TEST_KIND, {"a": 1})
        assert await redis.zcard("arq:queue") == 1
        row = await _job_row(database, job_id)
        # arq mode: the row records the outcome only; nothing has run it yet.
        assert row.status == "pending"
        assert row.kind == TEST_KIND
    finally:
        await service.aclose()
