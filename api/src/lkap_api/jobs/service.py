"""`JobsService`: enqueue and run background jobs (`inline` or `arq`, CONTRACTS-V2 §6).

`inline` (the default, and the only mode that works with no Redis on the dev
host): a `Job` row is always written first, then run in-process — via the
caller's `BackgroundTasks` when given one (so tests using
`httpx.ASGITransport`, which drains background tasks before `post()` returns,
observe a finished job synchronously, exactly like the pre-V2-08 KB-ingest
behaviour), or awaited directly otherwise (predictable completion, and simple:
nothing else in this process would run it if it weren't awaited). A periodic
poller (`run_due`) also exists so a job scheduled for the future (a webhook
retry) still runs even with no request in flight.

`arq`: the `Job` row is still written (CONTRACTS §1.5: "with arq the queue
lives in Redis and this table records outcomes only"), and the work is hung
off Redis via `arq.enqueue_job`; a separate `arq` worker process
(`jobs/arq_worker.py`) — not started by this api process — pops it and calls
the same handler through `JobsService.run_one`.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
from typing import Any, cast

import httpx
import structlog
from arq.connections import ArqRedis
from fastapi import BackgroundTasks
from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult

from lkap_api import net_guard
from lkap_api.db.models import Job, utcnow
from lkap_api.db.session import Database
from lkap_api.jobs.context import JobContext
from lkap_api.jobs.kinds import USAGE_DAILY_ROLLUP
from lkap_api.jobs.registry import get_handler
from lkap_api.jobs.settings import JobsSettings, get_jobs_settings
from lkap_api.logging import get_logger
from lkap_api.settings import Settings
from lkap_api.vault import Vault

log = get_logger(__name__)

#: The `arq` job function name every enqueued job is posted under.
ARQ_FUNCTION_NAME = "run_job"

#: Error message stored on a `jobs` row whose kind has no registered handler.
NO_HANDLER_ERROR = "no handler registered for kind"


class JobsService:
    """Enqueues jobs and runs due ones; one instance lives on `app.state.jobs`."""

    def __init__(
        self,
        *,
        database: Database,
        settings: Settings,
        vault: Vault,
        jobs_settings: JobsSettings | None = None,
        http_client: httpx.AsyncClient | None = None,
        arq_redis: ArqRedis | None = None,
    ) -> None:
        """Build the service.

        Args:
            database: The process database (handlers open their own sessions from it).
            settings: The shared api `Settings` (data_dir, master_key, ...).
            vault: The process `Vault`, passed through to job handlers.
            jobs_settings: This package's own settings; defaults to the cached instance.
            http_client: Outbound HTTP client for handlers (webhook delivery, QA judge
                calls); defaults to a private `httpx.AsyncClient` this service owns.
            arq_redis: An already-connected `ArqRedis`; if omitted and the backend is
                `arq`, one is lazily created from `jobs_settings.redis_url` on first use.
                Tests inject a `fakeredis`-backed instance here (see `tests/test_jobs.py`).
        """
        self._database = database
        self._settings = settings
        self._vault = vault
        self._jobs_settings = jobs_settings or get_jobs_settings()
        self._owns_http = http_client is None
        # V2-21: webhook deliveries and QA judge calls reach admin-supplied urls,
        # so the default client refuses private and metadata addresses after DNS.
        self._http = http_client or net_guard.guarded_http_client(
            net_guard.policy_from_settings(settings), timeout=15.0
        )
        self._arq_redis = arq_redis
        self._poll_task: asyncio.Task[None] | None = None

    @property
    def backend(self) -> str:
        """`"inline"` or `"arq"` (`LKAP_JOBS_BACKEND`)."""
        return self._jobs_settings.jobs_backend

    def _context(self) -> JobContext:
        return JobContext(
            database=self._database, settings=self._settings, vault=self._vault, http=self._http, jobs=self
        )

    async def _arq(self) -> ArqRedis:
        if self._arq_redis is None:
            if not self._jobs_settings.redis_url:
                raise RuntimeError("LKAP_JOBS_BACKEND=arq requires LKAP_REDIS_URL")
            from lkap_api.jobs.backends import create_arq_pool

            self._arq_redis = await create_arq_pool(self._jobs_settings.redis_url)
        return self._arq_redis

    async def enqueue(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        run_at: dt.datetime | None = None,
        background_tasks: BackgroundTasks | None = None,
    ) -> str:
        """Create a `jobs` row and arrange for it to run.

        Args:
            kind: A job kind name (`lkap_api.jobs.kinds`).
            payload: JSON-serialisable arguments for the handler.
            run_at: When the job becomes due; defaults to now (run as soon as possible).
            background_tasks: When given (inline backend only) and the job is
                due now, it runs via `BackgroundTasks.add_task` — deferred
                until after the response is sent, but still awaited by an ASGI
                test client's `post()` before it returns. Without one, a
                due-now job is awaited synchronously right here (predictable,
                and simple: nothing else in this process runs it otherwise);
                pass `background_tasks` from a request handler whenever the
                job does real I/O you don't want on the response's critical path.

        Returns:
            The new job's id.
        """
        due_at = run_at or utcnow()
        row = Job(kind=kind, payload=payload, status="pending", run_at=due_at)
        async with self._database.session() as session:
            session.add(row)
            await session.flush()
            job_id = row.id

        if self.backend == "arq":
            redis = await self._arq()
            await redis.enqueue_job(ARQ_FUNCTION_NAME, job_id, _defer_until=due_at if run_at else None)
            log.info("job_enqueued", job_id=job_id, kind=kind, backend="arq")
            return job_id

        log.info("job_enqueued", job_id=job_id, kind=kind, backend="inline")
        if due_at <= utcnow():
            if background_tasks is not None:
                background_tasks.add_task(self.run_one, job_id)
            else:
                await self.run_one(job_id)
        return job_id

    async def _claim(self, job_id: str) -> tuple[str, dict[str, Any], int] | None:
        """Atomically move a `pending` row to `running`; return `(kind, payload, attempts)`."""
        async with self._database.session() as session:
            row = await session.get(Job, job_id)
            if row is None:
                return None
            if row.status not in ("pending", "running"):
                return None
            kind, payload, attempts = row.kind, dict(row.payload), row.attempts
            result = await session.execute(
                update(Job).where(Job.id == job_id, Job.status == row.status).values(status="running")
            )
        if (cast(CursorResult[Any], result).rowcount or 0) == 0:
            return None
        return kind, payload, attempts

    async def _finish(
        self, job_id: str, *, status: str, error: str | None, attempts: int | None = None
    ) -> None:
        async with self._database.session() as session:
            row = await session.get(Job, job_id)
            if row is None:
                return
            row.status = status
            row.last_error = error
            if attempts is not None:
                row.attempts = attempts
            row.updated_at = utcnow()

    async def run_one(self, job_id: str) -> None:
        """Run one job by id, claiming it first (safe to call concurrently)."""
        claimed = await self._claim(job_id)
        if claimed is None:
            return
        kind, payload, attempts = claimed
        structlog.contextvars.bind_contextvars(job_id=job_id, job_kind=kind)
        try:
            handler = get_handler(kind)
            if handler is None:
                log.warning("job_no_handler", job_id=job_id, kind=kind)
                await self._finish(job_id, status="failed", error=NO_HANDLER_ERROR)
                return
            await handler(self._context(), payload)
            await self._finish(job_id, status="done", error=None)
            log.info("job_done", job_id=job_id, kind=kind)
        except Exception as exc:  # noqa: BLE001 - persisted on the row, the poller must survive it
            new_attempts = attempts + 1
            message = str(exc)[:500]
            if new_attempts >= self._jobs_settings.jobs_max_attempts:
                await self._finish(job_id, status="dead", error=message, attempts=new_attempts)
                log.warning("job_dead", job_id=job_id, kind=kind, attempts=new_attempts, error=message)
            else:
                backoff_s = min(2**new_attempts, 300)
                async with self._database.session() as session:
                    row = await session.get(Job, job_id)
                    if row is not None:
                        row.status = "pending"
                        row.attempts = new_attempts
                        row.last_error = message
                        row.run_at = utcnow() + dt.timedelta(seconds=backoff_s)
                log.warning(
                    "job_retry_scheduled",
                    job_id=job_id,
                    kind=kind,
                    attempts=new_attempts,
                    backoff_s=backoff_s,
                )
        finally:
            structlog.contextvars.unbind_contextvars("job_id", "job_kind")

    async def run_due(self, *, now: dt.datetime | None = None) -> int:
        """Run every `pending` row already due (inline backend's poller tick).

        Args:
            now: Injected clock for tests; defaults to the real current time.

        Returns:
            How many jobs were run this tick.
        """
        ts = now or utcnow()
        async with self._database.session() as session:
            due_ids = (
                (await session.execute(select(Job.id).where(Job.status == "pending", Job.run_at <= ts)))
                .scalars()
                .all()
            )
        ran = 0
        for job_id in due_ids:
            await self.run_one(job_id)
            ran += 1
        return ran

    def ensure_poller_started(self) -> None:
        """Lazily start the background due-job poller (idempotent).

        `main.py` is frozen and starts no ticker for this package (PLAN-V2 §
        "Exclusive ownership"), so the poller is started the first time a
        request depends on `JobsDep` (see `jobs/deps.py`) rather than at
        `lifespan` startup.
        """
        if self._poll_task is not None and not self._poll_task.done():
            return
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def _poll_loop(self) -> None:
        while True:
            try:
                await self.run_due()
                await self._ensure_usage_rollup_scheduled()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one bad tick must never kill the poller
                log.exception("jobs_poll_failed")
            await asyncio.sleep(self._jobs_settings.jobs_poll_interval_s)

    async def _ensure_usage_rollup_scheduled(self) -> None:
        """Enqueue one `usage_daily_rollup` job per calendar day (CONTRACTS-V2 §1.4).

        No cron exists in this process (`main.py` starts no ticker for this
        package), so the poller doubles as one: each tick checks whether a
        rollup job has already been created today and, if not, enqueues one
        for yesterday (UTC). Idempotent across `--reload` restarts and across
        however many api processes are running, aside from a benign duplicate
        row on an exact race — `jobs.rollup.rollup_day` recomputes buckets
        rather than accumulating, so running it twice is harmless.
        """
        today_start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        async with self._database.session() as session:
            exists = (
                await session.execute(
                    select(Job.id)
                    .where(Job.kind == USAGE_DAILY_ROLLUP, Job.created_at >= today_start)
                    .limit(1)
                )
            ).scalar_one_or_none()
        if exists is None:
            await self.enqueue(USAGE_DAILY_ROLLUP, {})

    async def aclose(self) -> None:
        """Stop the poller and close owned resources (app shutdown / test teardown)."""
        if self._poll_task is not None:
            self._poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._poll_task
        if self._owns_http:
            await self._http.aclose()
        if self._arq_redis is not None:
            await self._arq_redis.aclose()
