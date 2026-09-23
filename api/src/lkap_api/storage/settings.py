"""Storage-specific settings, read independently of `lkap_api.settings.Settings`.

Same rationale as `lkap_api.jobs.settings`: `LKAP_STORAGE_*` (CONTRACTS-V2 §6)
is not a field on the shared `Settings` (V2-01's exclusive file), so this
package reads it with its own `BaseSettings`. An ask to fold these into the
shared `Settings` is filed in `docs/v2/_asks.md`.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class StorageSettings(BaseSettings):
    """The platform default storage config (a workspace `StorageConfig` row overrides it)."""

    model_config = SettingsConfigDict(env_prefix="LKAP_", env_file=None, case_sensitive=False, extra="ignore")

    storage_kind: Literal["local", "s3"] = "local"
    storage_bucket: str = ""
    storage_endpoint_url: str | None = None
    storage_region: str | None = None
    storage_access_key: str | None = None
    storage_secret_key: str | None = None
    storage_public_base_url: str | None = None
    #: Signing key for local-backend signed URLs; falls back to `master_key`
    #: (`lkap_api.settings.Settings.master_key`) when unset, passed in by the
    #: caller rather than read here (this settings object has no master key).


@lru_cache(maxsize=1)
def get_storage_settings() -> StorageSettings:
    """Return the process-wide cached :class:`StorageSettings`."""
    return StorageSettings()
