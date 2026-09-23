"""Session recordings via LiveKit Egress (PLAN-V2 V2-12, ARCHITECTURE-V2 D-V2-16).

Importing this package registers the `egress_ended` finalize handler
(`finalize.py`) and the `recording_finalize` job handler (`job.py`) as side
effects — `routers/sessions.py` imports it for exactly that reason, mirroring
`routers/webhooks.py`'s `from lkap_api import qa as _qa` (`qa/__init__.py`).
`routers/internal.py` also imports `start_recording` directly, but only
lazily (`importlib`, ask #23) so it keeps working before this package exists.
"""

from __future__ import annotations

from lkap_api.recordings import finalize as _finalize  # noqa: F401 - registers the egress_ended handler
from lkap_api.recordings import job as _job  # noqa: F401 - registers the recording_finalize job handler
from lkap_api.recordings.service import start_recording
from lkap_api.recordings.storage import NoEgressStorageError, resolve_storage_row

__all__ = ["NoEgressStorageError", "resolve_storage_row", "start_recording"]
