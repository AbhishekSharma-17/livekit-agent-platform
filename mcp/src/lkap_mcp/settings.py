"""Process settings of the MCP server, read from the environment only.

No ``.env`` file is ever read (HANDOFF rule 2): ``env_file`` is ``None`` and the
values come from the environment the coding agent spawns ``lkap-mcp`` with, or
from the compose service in HTTP mode (V3-06, ``AGENT-ACCESS.md`` §9).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class McpSettings(BaseSettings):
    """Every ``LKAP_*`` knob the MCP process reads."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore", populate_by_name=True)

    api_url: str = Field("http://127.0.0.1:8080", alias="LKAP_API_URL")
    api_key: SecretStr | None = Field(None, alias="LKAP_API_KEY")
    workspace: str | None = Field(None, alias="LKAP_WORKSPACE")

    read_only: bool = Field(False, alias="LKAP_MCP_READ_ONLY")
    inline_secrets: Literal["on", "off"] = Field("on", alias="LKAP_MCP_INLINE_SECRETS")
    allow_dial: bool = Field(False, alias="LKAP_MCP_ALLOW_DIAL")
    client_name: str | None = Field(None, alias="LKAP_MCP_CLIENT")
    max_chats: int = Field(3, alias="LKAP_MCP_MAX_CHATS", ge=1, le=20)
    log_level: str = Field("INFO", alias="LKAP_MCP_LOG_LEVEL")

    #: ``stdio`` (the default) or ``http`` (V3-06). HTTP mode refuses ``file:``
    #: references and ``webhook_create`` (D-V3-4).
    transport: Literal["stdio", "http"] = Field("stdio", alias="LKAP_MCP_TRANSPORT")
    #: Where ``webhook_create`` writes the one-time signing secret (stdio only).
    webhook_secret_dir: Path = Field(Path("~/.config/lkap/webhooks"), alias="LKAP_MCP_WEBHOOK_SECRET_DIR")
    #: Api client limits (D-V3-2).
    max_in_flight: int = Field(4, alias="LKAP_MCP_MAX_IN_FLIGHT", ge=1, le=32)
    request_timeout_s: float = Field(30.0, alias="LKAP_MCP_REQUEST_TIMEOUT_S", gt=0)
    max_retry_after_s: float = Field(10.0, alias="LKAP_MCP_MAX_RETRY_AFTER_S", ge=0)

    # Remote streamable-HTTP mode (V3-06 reads these; declared here so it needs no edit).
    http_host: str = Field("127.0.0.1", alias="LKAP_MCP_HTTP_HOST")
    http_port: int = Field(8090, alias="LKAP_MCP_HTTP_PORT")
    public_url: str | None = Field(None, alias="LKAP_MCP_PUBLIC_URL")
    max_sessions_per_key: int = Field(5, alias="LKAP_MCP_MAX_SESSIONS_PER_KEY")
    calls_per_min: int = Field(120, alias="LKAP_MCP_CALLS_PER_MIN")

    @field_validator("api_url")
    @classmethod
    def _strip_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @property
    def http_mode(self) -> bool:
        """True when the server runs as the remote streamable-HTTP service."""
        return self.transport == "http"

    @property
    def inline_allowed(self) -> bool:
        """Whether inline secret values are accepted (R-V3-3)."""
        return self.inline_secrets == "on"


def load_settings() -> McpSettings:
    """Read the settings from the process environment."""
    return McpSettings()
