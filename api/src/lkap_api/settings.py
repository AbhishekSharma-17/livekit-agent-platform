"""Runtime settings for `lkap_api`.

Shapes and env var names follow docs/CONTRACTS.md §3. `.env` is optional and
human-created; ship `env.example` only. Never read `.env*` from tooling.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from lkap_api.db.constants import DEFAULT_OWNER_EMAIL

#: Minimum length of a static secret in ``LKAP_ENV=prod`` (REVIEW-FINAL F-08).
MIN_SECRET_LENGTH = 32


def weak_secret_problem(value: str) -> str | None:
    """Return why a static secret is too weak for production, or ``None``.

    Args:
        value: The configured secret (never logged).

    Returns:
        A short reason, or ``None`` when the value is acceptable.
    """
    if not value:
        return "is not set"
    if value.lower().startswith("dev-") or value.lower().startswith("dev_"):
        return "is a dev-* placeholder"
    if len(value) < MIN_SECRET_LENGTH:
        return f"is shorter than {MIN_SECRET_LENGTH} characters"
    return None


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
    env: Literal["dev", "prod"] = "dev"
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
    bootstrap_owner_email: str = DEFAULT_OWNER_EMAIL
    bootstrap_owner_password: str | None = None
    session_sweep_interval_s: int = 60
    session_stale_created_s: int = 600
    session_stale_active_s: int = 21600

    # --- V2-02 auth, tenancy and limits (CONTRACTS-V2 §3, §6) ---
    allow_admin_token: bool | None = None
    session_secret: str | None = None
    session_ttl_hours: int = Field(default=12, ge=1, le=24 * 30)
    web_base_url: str | None = None
    redis_url: str | None = None
    rate_limit_enabled: bool = True
    api_key_rate_per_min: int = Field(default=600, ge=1)
    login_rate_per_min: int = Field(default=10, ge=1)

    @model_validator(mode="after")
    def _enforce_production_secrets(self) -> Settings:
        """Refuse weak static secrets when ``LKAP_ENV=prod`` (REVIEW-FINAL F-08).

        In ``prod`` the service token, the session secret and — when break-glass
        is enabled — the admin token must be at least 32 characters and must not
        be a ``dev-*`` placeholder. ``dev`` accepts anything, as v1 did.
        """
        if self.env != "prod":
            return self
        problems = [
            f"{name}: {problem}"
            for name, value in (
                ("LKAP_SERVICE_TOKEN", self.service_token),
                ("LKAP_SESSION_SECRET", self.session_secret or ""),
                *((("LKAP_ADMIN_TOKEN", self.admin_token),) if self.admin_token_allowed else ()),
            )
            if (problem := weak_secret_problem(value)) is not None
        ]
        if problems:
            raise ValueError("refusing weak secrets in LKAP_ENV=prod: " + "; ".join(problems))
        return self

    @property
    def admin_token_allowed(self) -> bool:
        """Whether ``X-Admin-Token`` break-glass access is on (default: only in ``dev``)."""
        if self.allow_admin_token is not None:
            return self.allow_admin_token
        return self.env == "dev"

    @property
    def cookie_secure(self) -> bool:
        """Session cookies carry ``Secure`` everywhere except ``dev``."""
        return self.env != "dev"

    @property
    def web_origins(self) -> list[str]:
        """The platform's own web origins (CORS list plus ``LKAP_WEB_BASE_URL``)."""
        origins = list(self.cors_origins_list)
        if self.web_base_url:
            base = self.web_base_url.rstrip("/")
            if base not in origins:
                origins.append(base)
        return origins

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
