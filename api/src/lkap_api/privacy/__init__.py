"""Privacy after the call (V5-30, P §4.2 C10): the scrub job, file purge on delete, validators.

* :mod:`~lkap_api.privacy.redact` — deterministic masking (emails, card numbers, long numbers).
* :mod:`~lkap_api.privacy.llm` — the optional ``scrub_model`` pass (names, addresses, details).
* :mod:`~lkap_api.privacy.scrub` — the ``session_scrub`` job and its enqueue helpers.
* :mod:`~lkap_api.privacy.purge` — a deleted session's files and recording go at once (S5-36).
* :mod:`~lkap_api.privacy.validation` — save-time warnings, registered into
  ``config_service.VALIDATORS`` by importing this package.
"""

from __future__ import annotations

from lkap_api.privacy import validation as _validation  # noqa: F401 - registers the validator
from lkap_api.privacy.purge import PurgeResult, purge_session_files
from lkap_api.privacy.scrub import (
    PRIVACY_SCRUBBED_EVENT,
    ScrubResult,
    enqueue_scrub,
    enqueue_scrub_if_due,
    scrub_due,
    scrub_session,
    scrubbed_at,
)

__all__ = [
    "PRIVACY_SCRUBBED_EVENT",
    "PurgeResult",
    "ScrubResult",
    "enqueue_scrub",
    "enqueue_scrub_if_due",
    "purge_session_files",
    "scrub_due",
    "scrub_session",
    "scrubbed_at",
]
