"""Runtime settings for `lkap_api`.

Shapes and env var names follow docs/CONTRACTS.md §3. `.env` is optional and
human-created; ship `env.example` only. Never read `.env*` from tooling.
"""

from __future__ import annotations

import json
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """API service settings.

    Fields without the `LIVEKIT_`/`PORT` canonical alias are read from
    `LKAP_<FIELD_NAME>` (env_prefix="LKAP_"). Canonical, non-prefixed env
    vars use an explicit `validation_alias`.
    """

    model_config = SettingsConfigDict(
        env_prefix="LKAP_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- canonical, non-prefixed ---
    livekit_url: str = Field(validation_alias="LIVEKIT_URL")
    livekit_api_key: str = Field(validation_alias="LIVEKIT_API_KEY")
    livekit_api_secret: str = Field(validation_alias="LIVEKIT_API_SECRET")
    port: int = Field(default=8080, validation_alias="PORT")

    # --- LKAP_-prefixed ---
    master_key: str
    agent_name: str = "lkap-agent"
    admin_token: str
    service_token: str
    data_dir: str = "./data"
    database_url: str | None = None
    cors_origins: str = "http://localhost:3000"
    public_base_url: str | None = None
    packs: str = "packs.insurance_claim,packs.generic"
    log_level: str = "INFO"
    log_json: bool = False
    embedder: str = "fastembed"
    bootstrap_credentials_json: str | None = None
    session_sweep_interval_s: int = 60
    session_stale_created_s: int = 600
    session_stale_active_s: int = 21600

    @field_validator("bootstrap_credentials_json")
    @classmethod
    def _validate_bootstrap_json(cls, value: str | None) -> str | None:
        """Fail fast on malformed `LKAP_BOOTSTRAP_CREDENTIALS_JSON` at startup."""
        if value:
            json.loads(value)
        return value

    @property
    def resolved_database_url(self) -> str:
        """`LKAP_DATABASE_URL`, or the SQLite default rooted at `data_dir`."""
        if self.database_url:
            return self.database_url
        return f"sqlite+aiosqlite:///{self.data_dir}/lkap.db"

    @property
    def cors_origins_list(self) -> list[str]:
        """`LKAP_CORS_ORIGINS` split into an ordered list of allowed origins."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def packs_list(self) -> list[str]:
        """`LKAP_PACKS` split into an ordered list of dotted module paths."""
        return [p.strip() for p in self.packs.split(",") if p.strip()]

    @property
    def bootstrap_credentials(self) -> dict[str, dict[str, str]] | None:
        """Parsed `LKAP_BOOTSTRAP_CREDENTIALS_JSON` (`{provider_id: {field: value}}`)."""
        if not self.bootstrap_credentials_json:
            return None
        parsed: dict[str, dict[str, str]] = json.loads(self.bootstrap_credentials_json)
        return parsed


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide cached `Settings` instance."""
    return Settings()
