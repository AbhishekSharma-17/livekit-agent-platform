"""The single list of every module that registers a `jobs.registry` handler.

ask #25 (`docs/v5/_asks.md`): the api process registers every job kind's
handler as a side effect of importing its owning package (`jobs/__init__.py`
imports `jobs/rollup.py`, `webhooks/__init__.py` imports `webhooks/delivery.py`,
`qa/__init__.py` imports `qa/job.py`, `recordings/__init__.py` imports
`recordings/job.py`) — plus, for `kb.ingest`/`kb.jobs`/`telephony.calls`/
`fleet.sweep`/`sessions_sweep`, as a side effect of whichever router happens to
import them. That works for the api only because `main.py` imports every
router. The `arq` worker (`arq_worker.py`) imports none of them, so under
`LKAP_JOBS_BACKEND=arq` its registry held only `usage_daily_rollup` and every
other kind failed "no handler registered for kind" (verified: `import
lkap_api.jobs.arq_worker; registered_kinds()` -> `['usage_daily_rollup']`).

:func:`load_all_handlers` is the fix: one explicit list of every
handler-owning module, called by both `arq_worker.py` (module scope, so the
bug's own repro command now registers everything) and `main.py::create_app`
(explicitly, so the api no longer depends on router import order either) —
the two processes can now never drift apart again. Add a new job kind's
handler module here the same day it is written; `tests/test_jobs_handlers.py`
fails CI if one is missing.
"""

from __future__ import annotations

from lkap_api.jobs.registry import registered_kinds


def load_all_handlers() -> frozenset[str]:
    """Import every job-handler module, then return the resulting registry.

    Each import is for its side effect only — the module's `@job(...)`
    decorator runs at import time — so every name below is otherwise unused.
    Safe to call more than once per process: Python caches imports, and
    re-registering the same kind with the same function is a no-op.

    Returns:
        Every job kind with a registered handler, straight from
        `jobs.registry.registered_kinds()`, for the caller's convenience.
    """
    from lkap_api import sessions_sweep as _sessions_sweep  # noqa: F401 - registers session_orphan_event
    from lkap_api.fleet import sweep as _fleet_sweep  # noqa: F401 - registers worker_sweep
    from lkap_api.jobs import reconcile as _jobs_reconcile  # noqa: F401 - registers cost_reconcile
    from lkap_api.jobs import rollup as _jobs_rollup  # noqa: F401 - registers usage_daily_rollup
    from lkap_api.kb import evals as _kb_evals  # noqa: F401 - registers kb_evaluate
    from lkap_api.kb import ingest as _kb_ingest  # noqa: F401 - registers kb_ingest
    from lkap_api.kb import jobs as _kb_jobs  # noqa: F401 - registers kb_delete
    from lkap_api.qa import job as _qa_job  # noqa: F401 - registers qa_scoring
    from lkap_api.recordings import job as _recordings_job  # noqa: F401 - registers recording_finalize
    from lkap_api.telephony import calls as _telephony_calls  # noqa: F401 - registers call_event
    from lkap_api.webhooks import delivery as _webhooks_delivery  # noqa: F401 - registers webhook_delivery

    return registered_kinds()
