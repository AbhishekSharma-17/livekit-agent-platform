"""FastAPI dependency for `JobsService` — lazily built and cached on `app.state`.

`main.py` is frozen (PLAN-V2 §"Exclusive ownership"), so nothing wires
`JobsService` into the app's `lifespan`. It is instead created on first use
by any route that depends on :data:`JobsDep`, mirroring
`routers.knowledge.get_database`'s use of `request.app.state`.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from lkap_api.db.session import Database
from lkap_api.deps import SettingsDep, VaultDep
from lkap_api.jobs.service import JobsService
from lkap_api.settings import Settings
from lkap_api.vault import Vault


async def get_jobs(request: Request, settings: SettingsDep, vault: VaultDep) -> JobsService:
    """Return the process's `JobsService`, creating and starting it on first use.

    Must be `async def`: a sync dependency runs in FastAPI's worker thread
    pool (`run_in_threadpool`), which has no running event loop, and
    `ensure_poller_started` calls `asyncio.create_task`.
    """
    existing: JobsService | None = getattr(request.app.state, "jobs", None)
    if existing is not None:
        return existing
    service = JobsService(database=request.app.state.db, settings=settings, vault=vault)
    service.ensure_poller_started()
    request.app.state.jobs = service
    return service


JobsDep = Annotated[JobsService, Depends(get_jobs)]


def build_jobs_service(database: Database, settings: Settings, vault: Vault) -> JobsService:
    """Construct a `JobsService` outside a request (tests, scripts)."""
    return JobsService(database=database, settings=settings, vault=vault)
