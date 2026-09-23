"""Regression test for the `connections.webhooks` <-> `recordings` import cycle (2026-09-23).

`connections/webhooks.py` imports `lkap_api.sessions_sweep` (for
`NEVER_STARTED`) at module scope. `lkap_api.recordings` — importing *any* of
its submodules always runs `recordings/__init__.py` first — imports
`connections.webhooks` back via `recordings/finalize.py`'s
`register_webhook_handler`/`WebhookContext`. `sessions_sweep.py` briefly
imported `lkap_api.recordings.storage` at its own module scope too, closing
the cycle: `main.py` -> `routers/hooks.py` -> `connections/webhooks.py` ->
`sessions_sweep` -> `recordings` (package init) -> `finalize` ->
`connections.webhooks` (still mid-initialisation) -> `ImportError`. It only
ever showed up importing the *whole app* (`lkap_api.main`), because no
single-package test exercises that exact router-import order — hence this
test imports the app fresh, in a subprocess (so Python's already-warm module
cache in the current process can't hide a re-introduced cycle).
"""

from __future__ import annotations

import os
import subprocess
import sys

from conftest import REQUIRED_ENV


def test_lkap_api_main_imports_cleanly_in_a_fresh_interpreter() -> None:
    """`import lkap_api.main` must not raise, even from a cold module cache."""
    # A minimal, explicit environment (just enough to satisfy `Settings` and
    # locate the interpreter) rather than inheriting the test runner's own —
    # a stray `LKAP_ENV=prod` or similar in the ambient environment must not
    # turn this into a test of production validation instead of the import.
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""), **REQUIRED_ENV}
    result = subprocess.run(
        [sys.executable, "-c", "import lkap_api.main"],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert result.returncode == 0, result.stderr
