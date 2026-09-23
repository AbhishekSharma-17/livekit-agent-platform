"""Supervisor settings, read from the environment (CONTRACTS-V2 §6).

Contract variables: ``LKAP_API_BASE_URL``, ``LKAP_SERVICE_TOKEN``,
``LKAP_SUPERVISOR_BACKEND``, ``LKAP_SUPERVISOR_INTERVAL_S`` (10),
``LKAP_SUPERVISOR_DRAIN_S`` (3600, D-W2-13), ``LKAP_REDIS_URL``,
``LKAP_AGENT_IMAGE_SLIM`` / ``LKAP_AGENT_IMAGE_FULL``. The rest are
``LKAP_SUPERVISOR_*`` knobs local to this service.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

#: D-W2-13: never SIGKILL a worker sooner than 15 s after its SIGINT.
MIN_GRACE_S = 15.0

#: The repository's ``agent/`` directory (``supervisor/src/lkap_supervisor/settings.py`` → root).
DEFAULT_AGENT_DIR = Path(__file__).resolve().parents[3] / "agent"

BackendName = Literal["subprocess", "docker"]


def _alias(name: str) -> AliasChoices:
    return AliasChoices(name)


class SupervisorSettings(BaseSettings):
    """Everything the supervisor process needs; secrets are ``SecretStr``."""

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    api_base_url: str = Field(default="http://127.0.0.1:8080", validation_alias=_alias("LKAP_API_BASE_URL"))
    service_token: SecretStr = Field(validation_alias=_alias("LKAP_SERVICE_TOKEN"))
    backend: BackendName = Field(default="subprocess", validation_alias=_alias("LKAP_SUPERVISOR_BACKEND"))
    interval_s: float = Field(default=10.0, gt=0, validation_alias=_alias("LKAP_SUPERVISOR_INTERVAL_S"))
    drain_s: float = Field(default=3600.0, ge=MIN_GRACE_S, validation_alias=_alias("LKAP_SUPERVISOR_DRAIN_S"))
    backoff_base_s: float = Field(
        default=5.0, gt=0, validation_alias=_alias("LKAP_SUPERVISOR_BACKOFF_BASE_S")
    )
    backoff_max_s: float = Field(
        default=300.0, gt=0, validation_alias=_alias("LKAP_SUPERVISOR_BACKOFF_MAX_S")
    )
    min_ready_s: float = Field(default=5.0, ge=0, validation_alias=_alias("LKAP_SUPERVISOR_MIN_READY_S"))
    metrics_port: int = Field(default=9105, ge=0, validation_alias=_alias("LKAP_SUPERVISOR_METRICS_PORT"))
    redis_url: SecretStr | None = Field(default=None, validation_alias=_alias("LKAP_REDIS_URL"))
    state_dir: Path = Field(
        default=Path(tempfile.gettempdir()) / "lkap-supervisor",
        validation_alias=_alias("LKAP_SUPERVISOR_STATE_DIR"),
    )
    # subprocess backend
    agent_dir: Path = Field(default=DEFAULT_AGENT_DIR, validation_alias=_alias("LKAP_SUPERVISOR_AGENT_DIR"))
    python: str | None = Field(default=None, validation_alias=_alias("LKAP_SUPERVISOR_PYTHON"))
    log_dir: Path | None = Field(default=None, validation_alias=_alias("LKAP_SUPERVISOR_LOG_DIR"))
    passthrough_env: str = Field(default="", validation_alias=_alias("LKAP_SUPERVISOR_PASSTHROUGH_ENV"))
    # docker backend
    agent_image_slim: str = Field(
        default="lkap-agent:slim-latest", validation_alias=_alias("LKAP_AGENT_IMAGE_SLIM")
    )
    agent_image_full: str = Field(
        default="lkap-agent:full-latest", validation_alias=_alias("LKAP_AGENT_IMAGE_FULL")
    )
    docker_network: str | None = Field(
        default=None, validation_alias=_alias("LKAP_SUPERVISOR_DOCKER_NETWORK")
    )
    worker_api_base_url: str | None = Field(
        default=None, validation_alias=_alias("LKAP_SUPERVISOR_WORKER_API_URL")
    )
    # logging
    log_level: str = Field(default="INFO", validation_alias=_alias("LKAP_LOG_LEVEL"))
    log_json: bool = Field(default=False, validation_alias=_alias("LKAP_LOG_JSON"))

    @property
    def python_executable(self) -> str:
        """The interpreter that runs ``lkap_agent.main``: the agent's venv, else this one."""
        if self.python:
            return self.python
        venv_python = self.agent_dir / ".venv" / "bin" / "python"
        return str(venv_python) if venv_python.exists() else sys.executable

    @property
    def passthrough_names(self) -> tuple[str, ...]:
        """Extra parent environment variable names handed to subprocess workers."""
        return tuple(name.strip() for name in self.passthrough_env.split(",") if name.strip())

    @property
    def image_refs(self) -> dict[str, str]:
        """Docker image reference per worker image flavour."""
        return {"slim": self.agent_image_slim, "full": self.agent_image_full}
