"""Replica backends: ``subprocess`` (dev) and ``docker`` (prod)."""

from __future__ import annotations

from lkap_supervisor.backends.base import Backend, BackendError
from lkap_supervisor.backends.docker import DockerBackend
from lkap_supervisor.backends.subprocess import SubprocessBackend
from lkap_supervisor.settings import SupervisorSettings

__all__ = ["Backend", "BackendError", "DockerBackend", "SubprocessBackend", "build_backend"]


def build_backend(settings: SupervisorSettings) -> Backend:
    """Instantiate the backend ``LKAP_SUPERVISOR_BACKEND`` names."""
    match settings.backend:
        case "subprocess":
            return SubprocessBackend(
                agent_dir=settings.agent_dir,
                python=settings.python_executable,
                state_dir=settings.state_dir,
                log_dir=settings.log_dir,
                passthrough=settings.passthrough_names,
            )
        case "docker":
            return DockerBackend(
                image_refs=settings.image_refs,
                state_dir=settings.state_dir,
                network=settings.docker_network,
                worker_api_base_url=settings.worker_api_base_url,
            )
