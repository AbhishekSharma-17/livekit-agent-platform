"""Runtime settings for the `lkap-agent` worker.

Shapes and env var names follow docs/CONTRACTS.md §3. `.env` is optional and
human-created; ship `env.example` only. Never read `.env*` from tooling.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Agent worker settings.

    Fields without the `LIVEKIT_` canonical alias are read from
    `LKAP_<FIELD_NAME>` (env_prefix="LKAP_"). LiveKit's own env var names are
    canonical and never carry the `LKAP_` prefix, so they use an explicit
    `validation_alias`.
    """

    model_config = SettingsConfigDict(
        env_prefix="LKAP_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- LiveKit canonical names (no LKAP_ prefix) ---
    livekit_url: str = Field(validation_alias="LIVEKIT_URL")
    livekit_api_key: str = Field(validation_alias="LIVEKIT_API_KEY")
    livekit_api_secret: str = Field(validation_alias="LIVEKIT_API_SECRET")
    livekit_agent_name: str = Field(default="lkap-agent", validation_alias="LIVEKIT_AGENT_NAME")

    # --- LKAP_-prefixed ---
    service_token: str
    api_base_url: str
    packs: str = "packs.insurance_claim,packs.generic"
    http_tool_allowed_hosts: str = ""
    log_level: str = "INFO"
    log_json: bool = False
    vision_max_frame_age_s: float = 8.0
    #: `LKAP_IDLE_HANGUP_S`: seconds a session may stay idle before the worker
    #: hangs up (REVIEW-FINAL F-02). The clock starts when the SDK reports the
    #: user `away` while the agent is listening or idle, and any other user state
    #: cancels it. `None` or `0` disables the hangup. The `away` state is only
    #: ever emitted when the agent's `voice.user_away_timeout_s` is set, so an
    #: agent with `user_away_timeout_s=None` never idles out; the effective idle
    #: time is `user_away_timeout_s + idle_hangup_s`.
    idle_hangup_s: float | None = 120.0

    @property
    def packs_list(self) -> list[str]:
        """`LKAP_PACKS` split into an ordered list of dotted module paths."""
        return [p.strip() for p in self.packs.split(",") if p.strip()]

    @property
    def http_tool_allowed_hosts_list(self) -> list[str]:
        """`LKAP_HTTP_TOOL_ALLOWED_HOSTS` split into a host allowlist (may be empty)."""
        return [h.strip() for h in self.http_tool_allowed_hosts.split(",") if h.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide cached `Settings` instance."""
    return Settings()
