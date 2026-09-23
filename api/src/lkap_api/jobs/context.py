"""`JobContext`: everything a job handler needs, threaded through by the service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

from lkap_api.db.session import Database
from lkap_api.settings import Settings
from lkap_api.vault import Vault

if TYPE_CHECKING:
    from lkap_api.jobs.service import JobsService


@dataclass(slots=True, frozen=True)
class JobContext:
    """Dependencies a job handler runs with; one is built per attempt.

    A handler opens its own session from `database.session()` (never the
    request's session — the row it acts on must already be durable, same
    rule as `kb.ingest.run_ingestion_task`) and never blocks on it for longer
    than the attempt. `jobs` is the service itself, so a handler that needs to
    schedule a follow-up (a webhook retry at its next backoff step) can call
    `ctx.jobs.enqueue(...)` rather than this package growing a second entry
    point for "enqueue from inside a handler".
    """

    database: Database
    settings: Settings
    vault: Vault
    http: httpx.AsyncClient
    jobs: JobsService
