"""Jobs-specific settings, read independently of `lkap_api.settings.Settings`.

`api/src/lkap_api/settings.py` is V2-01's exclusive file ("new fields only",
PLAN-V2 §4 V2-01 card) and is not in this package's file list, so
`LKAP_JOBS_BACKEND` / `LKAP_REDIS_URL` (CONTRACTS-V2 §6) are read here instead,
with the same `env_prefix="LKAP_"` convention. An ask to fold these into the
shared `Settings` is filed in `docs/v2/_asks.md`.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class JobsSettings(BaseSettings):
    """`LKAP_JOBS_BACKEND` / `LKAP_REDIS_URL` / tuning knobs."""

    model_config = SettingsConfigDict(env_prefix="LKAP_", env_file=None, case_sensitive=False, extra="ignore")

    jobs_backend: Literal["inline", "arq"] = "inline"
    redis_url: str | None = None
    #: How often the inline poller scans `jobs` for due rows.
    jobs_poll_interval_s: float = 2.0
    #: Generic retry policy for job kinds with no bespoke schedule (webhook
    #: deliveries use their own `RETRY_SCHEDULE_S` in `webhooks/delivery.py`).
    jobs_max_attempts: int = 5


@lru_cache(maxsize=1)
def get_jobs_settings() -> JobsSettings:
    """Return the process-wide cached :class:`JobsSettings`."""
    return JobsSettings()
