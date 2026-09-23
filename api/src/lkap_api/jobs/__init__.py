"""Background jobs: `inline` (BackgroundTasks + the `jobs` table) or `arq` (Redis).

CONTRACTS-V2 §1.5 / §6, PLAN-V2 V2-08. Callers depend on :data:`JobsDep`
(:mod:`lkap_api.jobs.deps`) and call :meth:`JobsService.enqueue`; a job kind's
owning package registers its handler with :func:`lkap_api.jobs.registry.job`.

``LKAP_JOBS_BACKEND`` selects the backend (default ``inline``); this module
never touches ``lkap_api.settings`` (not this package's file, PLAN-V2 §
"Exclusive ownership") — see :mod:`lkap_api.jobs.settings` for its own,
independently-read env vars.
"""

from __future__ import annotations

# Side-effecting import: registers this package's own `usage_daily_rollup`
# handler as soon as `lkap_api.jobs` is imported (which every job-owning
# package already does for `JobContext`/`job`/`JobsService`).
from lkap_api.jobs import rollup as _rollup  # noqa: E402,F401
from lkap_api.jobs.context import JobContext
from lkap_api.jobs.registry import JobHandler, job
from lkap_api.jobs.service import JobsService

__all__ = ["JobContext", "JobHandler", "JobsService", "job"]
