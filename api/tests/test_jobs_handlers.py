"""Guard tests for ask #25 (`docs/v5/_asks.md`): the `arq` worker's job-handler
registry must never drift from the api's again.

Before the fix, `arq_worker.py` imported no handler module, so under
`LKAP_JOBS_BACKEND=arq` its registry held only `usage_daily_rollup`
(`lkap_api.jobs`'s own side-effect import) and every other kind (`kb_ingest`,
`kb_delete`, `webhook_delivery`, `qa_scoring`, `recording_finalize`,
`worker_sweep`, `call_event`, `session_orphan_event`) failed "no handler
registered for kind" in prod, even though the dev (`inline`) api process
worked, because it happens to import every router (and so every handler
module) as a side effect.

`jobs/handlers.py::load_all_handlers()` is the fix: one explicit list of every
handler-owning module, called by both `arq_worker.py` (module scope) and
`main.py::create_app` (explicitly).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

from conftest import REQUIRED_ENV

from lkap_api.jobs import kinds as jobs_kinds
from lkap_api.jobs.handlers import load_all_handlers
from lkap_api.jobs.registry import registered_kinds

# `SESSION_ORPHAN_EVENT_JOB` / `CALL_EVENT_JOB` are declared next to their own
# handler (not in `jobs.kinds`), imported by name so a rename shows up here as
# an import error rather than a silently-wrong string literal.
from lkap_api.sessions_sweep import SESSION_ORPHAN_EVENT_JOB
from lkap_api.telephony.calls import CALL_EVENT_JOB

#: `jobs.kinds` reserves these two names for packages that do not exist yet
#: (its own module docstring: "nothing here enqueues them itself"); no module
#: anywhere registers a handler for either. Any other unhandled kind is a bug.
INTENTIONALLY_HANDLER_LESS = frozenset({jobs_kinds.CONNECTION_PROBE, jobs_kinds.CATALOG_REFRESH})

#: Every kind that ought to have a registered handler once `load_all_handlers()`
#: (or, equivalently, the whole api app) has run. Kept as an explicit literal
#: set (rather than only "whatever `registered_kinds()` happens to return")
#: so a handler silently failing to import — an exception swallowed somewhere,
#: a typo'd module path — shows up as a missing kind, not a green test.
EXPECTED_HANDLED_KINDS = frozenset(
    {
        jobs_kinds.KB_INGEST,
        jobs_kinds.KB_DELETE,
        jobs_kinds.WEBHOOK_DELIVERY,
        jobs_kinds.QA_SCORING,
        jobs_kinds.USAGE_DAILY_ROLLUP,
        jobs_kinds.WORKER_SWEEP,
        jobs_kinds.RECORDING_FINALIZE,
        SESSION_ORPHAN_EVENT_JOB,
        CALL_EVENT_JOB,
    }
)


def _all_declared_kind_constants() -> frozenset[str]:
    """Every job-kind string this codebase declares.

    Most kinds are `jobs.kinds` module-level constants, picked up dynamically
    here so a *new* one added there is covered automatically. A few
    (`SESSION_ORPHAN_EVENT_JOB`, `CALL_EVENT_JOB`) are declared next to their
    own handler instead (`sessions_sweep.py`, `telephony/calls.py`) — add any
    future one of those to the literal set below too, since there is no single
    module this function can introspect for them.
    """
    from_kinds_module = {
        value for name, value in vars(jobs_kinds).items() if name.isupper() and isinstance(value, str)
    }
    return frozenset(from_kinds_module) | {SESSION_ORPHAN_EVENT_JOB, CALL_EVENT_JOB}


def test_load_all_handlers_registers_every_known_kind() -> None:
    """The shared loader used by both the api and the `arq` worker must cover
    every kind this codebase knows how to handle."""
    assert EXPECTED_HANDLED_KINDS <= load_all_handlers()


def test_every_declared_job_kind_has_a_handler_or_is_declared_handlerless() -> None:
    """A new `JobKind` with no handler and no entry in `INTENTIONALLY_HANDLER_LESS`
    fails here instead of silently shipping "no handler registered for kind"."""
    load_all_handlers()
    unhandled = _all_declared_kind_constants() - registered_kinds() - INTENTIONALLY_HANDLER_LESS
    assert not unhandled, (
        f"job kind(s) with no registered handler and not declared intentionally "
        f"handler-less in this test's INTENTIONALLY_HANDLER_LESS: {sorted(unhandled)}"
    )


def test_importing_arq_worker_in_a_fresh_interpreter_registers_every_kind() -> None:
    """The exact ask #25 repro (`import lkap_api.jobs.arq_worker; registered_kinds()`),
    run in a subprocess so an already-warm module cache can't hide a
    regression. This is the load-bearing drift guard: `registered` below comes
    from a cold interpreter that imported *only* `arq_worker` (transitively),
    so — unlike `test_every_declared_job_kind_has_a_handler_or_is_declared_handlerless`
    above, whose in-process `registered_kinds()` other test modules may have
    already populated for unrelated reasons — a kind `arq_worker.py` itself
    fails to register cannot hide behind this same pytest process's own
    imports. `_all_declared_kind_constants()` is computed here, in the parent
    process, so it is unaffected by what the subprocess imports.
    """
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""), **REQUIRED_ENV}
    script = (
        "import json\n"
        "import lkap_api.jobs.arq_worker\n"
        "from lkap_api.jobs.registry import registered_kinds\n"
        "print(json.dumps(sorted(registered_kinds())))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    registered = set(json.loads(result.stdout.strip().splitlines()[-1]))
    assert EXPECTED_HANDLED_KINDS <= registered, result.stdout
    declared = _all_declared_kind_constants() - INTENTIONALLY_HANDLER_LESS
    assert declared <= registered, (
        f"job kind(s) declared but not registered by importing `arq_worker` alone "
        f"(cold interpreter): {sorted(declared - registered)}"
    )
