"""`arq` worker process entrypoint: `arq lkap_api.jobs.arq_worker.WorkerSettings`.

Only used when `LKAP_JOBS_BACKEND=arq` (needs `LKAP_REDIS_URL`); the dev
environment runs `inline` and never imports this module. The worker shares no
process state with the api — it builds its own `Database`/`Vault`/`JobsService`
from `Settings` at startup and calls `JobsService.run_one` for each job id
`JobsService.enqueue` pushed onto the `arq` queue (see `service.py`).
"""

from __future__ import annotations

from typing import Any

from arq.connections import RedisSettings

from lkap_api.db.session import Database
from lkap_api.jobs.service import JobsService
from lkap_api.jobs.settings import get_jobs_settings
from lkap_api.logging import configure_logging, get_logger
from lkap_api.settings import get_settings
from lkap_api.vault import Vault

log = get_logger(__name__)


async def run_job(ctx: dict[str, Any], job_id: str) -> None:
    """The single `arq` job function every kind is enqueued under."""
    service: JobsService = ctx["jobs_service"]
    await service.run_one(job_id)


async def _startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    database = Database(settings.resolved_database_url)
    ctx["database"] = database
    ctx["jobs_service"] = JobsService(database=database, settings=settings, vault=Vault(settings.master_key))
    log.info("arq_worker_started")


async def _shutdown(ctx: dict[str, Any]) -> None:
    service: JobsService | None = ctx.get("jobs_service")
    if service is not None:
        await service.aclose()
    database: Database | None = ctx.get("database")
    if database is not None:
        await database.dispose()
    log.info("arq_worker_stopped")


_REDIS_URL = get_jobs_settings().redis_url or "redis://localhost:6379"


class WorkerSettings:
    """`arq` worker configuration; `redis_settings` reads `LKAP_REDIS_URL` at import time."""

    functions = [run_job]
    on_startup = _startup
    on_shutdown = _shutdown
    redis_settings = RedisSettings.from_dsn(_REDIS_URL)
