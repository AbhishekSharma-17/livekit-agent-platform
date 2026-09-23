"""``DockerBackend``: one container per replica via the Docker Engine API (prod).

* Image ``LKAP_AGENT_IMAGE_SLIM`` / ``LKAP_AGENT_IMAGE_FULL`` by the pool's
  flavour (default ``lkap-agent:<flavor>-latest``); the image's own command
  runs the worker.
* Labels ``lkap.managed_by=supervisor``, ``lkap.connection_id``, ``lkap.hash``,
  ``lkap.replica_index``, ``lkap.started_at``. :meth:`DockerBackend.list`
  rediscovers containers by label, so a restarted supervisor adopts its pool.
* The environment goes in through the container config only (never a file,
  never a log). The container name doubles as the ``instance_key`` and is
  handed to the worker as ``LKAP_INSTANCE_KEY``.
* A worker in a container cannot reach an api on the supervisor host's
  loopback. ``LKAP_SUPERVISOR_WORKER_API_URL`` overrides ``LKAP_API_BASE_URL``;
  without it a loopback url is refused before anything is started.
* Drain: ``kill(SIGINT)`` explicitly (not relying on the image's
  ``STOPSIGNAL``), wait up to ``grace_s``, ``kill()`` (SIGKILL) only if it is
  still running, then remove. Containers already signalled are remembered in
  ``<state_dir>/docker-draining.json`` so a restarted supervisor never sends a
  second SIGINT.

The docker SDK is synchronous; every call runs in a worker thread.
"""

from __future__ import annotations

import asyncio
import builtins
import ipaddress
import secrets
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.parse import urlparse

from lkap_contracts.fleet import FleetDesired, ReplicaHandle, ReplicaState, WorkerEnv

from lkap_supervisor.backends.base import (
    INSTANCE_KEY_ENV,
    BackendError,
    JsonState,
    as_str_list,
    worker_environment,
)
from lkap_supervisor.logging import get_logger

log = get_logger(__name__)

LABEL_MANAGED = "lkap.managed_by"
LABEL_CONNECTION = "lkap.connection_id"
LABEL_HASH = "lkap.hash"
LABEL_INDEX = "lkap.replica_index"
LABEL_STARTED = "lkap.started_at"
DRAINING_FILE = "docker-draining.json"


class ContainerLike(Protocol):
    """The subset of ``docker.models.containers.Container`` this backend uses."""

    @property
    def id(self) -> str | None: ...
    @property
    def name(self) -> str | None: ...
    @property
    def status(self) -> str: ...
    @property
    def labels(self) -> dict[str, str]: ...

    attrs: dict[str, Any]

    def reload(self) -> None: ...
    def kill(self, signal: str | int | None = None) -> None: ...
    def wait(self, *, timeout: float | None = None) -> Any: ...
    def remove(self, *, force: bool = False) -> None: ...


class ContainersLike(Protocol):
    """The subset of ``DockerClient.containers`` this backend uses."""

    def run(self, image: str, **kwargs: Any) -> ContainerLike: ...
    def get(self, container_id: str) -> ContainerLike: ...
    def list(self, *, all: bool = False, filters: dict[str, Any] | None = None) -> list[ContainerLike]: ...


class DockerClientLike(Protocol):
    """The subset of ``docker.DockerClient`` this backend uses."""

    @property
    def containers(self) -> ContainersLike: ...

    def close(self) -> None: ...


def default_client_factory() -> DockerClientLike:
    """``docker.from_env()`` (imported lazily so tests never need a daemon)."""
    import docker

    return cast(DockerClientLike, docker.from_env())


def is_loopback_url(url: str) -> bool:
    """Whether ``url`` points at this host's loopback (unreachable from a container)."""
    host = (urlparse(url).hostname or "").lower()
    if host in {"localhost", ""} or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_not_found(exc: Exception) -> bool:
    return type(exc).__name__ == "NotFound"


class DockerBackend:
    """Replicas as Docker containers."""

    name = "docker"
    stops_replicas_on_exit = False

    def __init__(
        self,
        *,
        image_refs: Mapping[str, str],
        state_dir: Path,
        network: str | None = None,
        worker_api_base_url: str | None = None,
        client_factory: Callable[[], DockerClientLike] = default_client_factory,
    ) -> None:
        """Configure the backend.

        Args:
            image_refs: Image reference per flavour (``slim``, ``full``).
            state_dir: Where the set of already-signalled containers is persisted.
            network: Docker network to attach workers to (e.g. the compose network).
            worker_api_base_url: ``LKAP_API_BASE_URL`` as seen from inside a container.
            client_factory: Builds the docker client (tests inject a fake).
        """
        self._images = dict(image_refs)
        self._network = network
        self._worker_api = worker_api_base_url
        self._factory = client_factory
        self._client: DockerClientLike | None = None
        self._draining_state = JsonState(state_dir / DRAINING_FILE)
        self._draining: set[str] = set(as_str_list(self._draining_state.load().get("names")))

    async def _docker(self) -> DockerClientLike:
        if self._client is None:
            try:
                self._client = await asyncio.to_thread(self._factory)
            except Exception as exc:
                raise BackendError(f"cannot reach the Docker daemon: {type(exc).__name__}") from None
        return self._client

    def _save_draining(self) -> None:
        self._draining_state.save({"names": sorted(self._draining)})

    def _state_of(self, container: ContainerLike) -> ReplicaState:
        name = container.name or ""
        match container.status:
            case "running" | "paused":
                return "draining" if name in self._draining else "running"
            case "created" | "restarting":
                return "starting"
            case _:
                return "stopped" if name in self._draining else "failed"

    def _handle(self, container: ContainerLike) -> ReplicaHandle:
        labels = container.labels
        try:
            index = int(labels.get(LABEL_INDEX, "0"))
            started = float(labels.get(LABEL_STARTED, "0"))
        except ValueError:
            index, started = 0, 0.0
        return ReplicaHandle(
            connection_id=labels.get(LABEL_CONNECTION, ""),
            replica_index=index,
            instance_key=container.name or container.id or "",
            desired_hash=labels.get(LABEL_HASH, ""),
            state=self._state_of(container),
            started_at=started,
            pid_or_container=container.id or "",
        )

    # ---------------------------------------------------------------------- protocol
    async def list(self) -> builtins.list[ReplicaHandle]:
        """Every supervisor-managed container (running or exited).

        Raises:
            BackendError: If the daemon is unreachable.
        """
        client = await self._docker()
        try:
            containers = await asyncio.to_thread(
                client.containers.list, all=True, filters={"label": f"{LABEL_MANAGED}=supervisor"}
            )
        except Exception as exc:
            raise BackendError(f"listing containers failed: {type(exc).__name__}") from None
        return [self._handle(c) for c in containers]

    def _environment(self, env: WorkerEnv, name: str) -> dict[str, str]:
        environment = worker_environment(env, extra={INSTANCE_KEY_ENV: name})
        if self._worker_api:
            environment["LKAP_API_BASE_URL"] = self._worker_api
        elif is_loopback_url(environment.get("LKAP_API_BASE_URL", "")):
            raise BackendError(
                "the worker's LKAP_API_BASE_URL is a loopback address a container cannot reach; "
                "set LKAP_PUBLIC_BASE_URL on the api or LKAP_SUPERVISOR_WORKER_API_URL on the supervisor"
            )
        return environment

    async def start(self, desired: FleetDesired, index: int, env: WorkerEnv) -> ReplicaHandle:
        """Run a detached worker container for replica ``index``.

        Raises:
            BackendError: For a loopback api url, an unknown image flavour or a daemon error.
        """
        image = self._images.get(desired.image)
        if not image:
            raise BackendError(f"no image configured for flavour '{desired.image}'")
        name = f"lkap-{desired.connection_id[:12]}-{index}-{desired.desired_hash[:8]}-{secrets.token_hex(3)}"
        environment = self._environment(env, name)
        started_at = time.time()
        labels = {
            LABEL_MANAGED: "supervisor",
            LABEL_CONNECTION: desired.connection_id,
            LABEL_HASH: desired.desired_hash,
            LABEL_INDEX: str(index),
            LABEL_STARTED: f"{started_at:.3f}",
        }
        client = await self._docker()
        kwargs: dict[str, Any] = {
            "detach": True,
            "name": name,
            "environment": environment,
            "labels": labels,
            "stop_signal": "SIGINT",
            "restart_policy": {"Name": "no"},
        }
        if self._network:
            kwargs["network"] = self._network
        try:
            container = await asyncio.to_thread(client.containers.run, image, **kwargs)
        except Exception as exc:
            raise BackendError(f"starting a worker container failed: {type(exc).__name__}") from None
        finally:
            del environment, kwargs
        log.info(
            "container_started",
            connection_id=desired.connection_id,
            replica_index=index,
            container=name,
            image=image,
        )
        return ReplicaHandle(
            connection_id=desired.connection_id,
            replica_index=index,
            instance_key=name,
            desired_hash=desired.desired_hash,
            state="starting",
            started_at=started_at,
            pid_or_container=container.id or name,
        )

    async def _get(self, handle: ReplicaHandle) -> ContainerLike | None:
        client = await self._docker()
        try:
            return await asyncio.to_thread(
                client.containers.get, handle.pid_or_container or handle.instance_key
            )
        except Exception as exc:
            if _is_not_found(exc):
                return None
            raise BackendError(f"inspecting a container failed: {type(exc).__name__}") from None

    @staticmethod
    def _wait(container: ContainerLike, timeout_s: float) -> bool:
        """Block until the container exits; False on timeout."""
        try:
            container.wait(timeout=timeout_s)
        except Exception:
            container.reload()
            return container.status not in {"running", "paused", "restarting"}
        return True

    async def drain(self, handle: ReplicaHandle, grace_s: float) -> None:
        """SIGINT once, wait ``grace_s``, SIGKILL if still running, then remove the container."""
        container = await self._get(handle)
        if container is None:
            self._draining.discard(handle.instance_key)
            self._save_draining()
            return
        name = container.name or handle.instance_key
        if container.status in {"running", "paused", "restarting"}:
            if name in self._draining:
                log.info("container_drain_resumed", container=name)
            else:
                self._draining.add(name)
                self._save_draining()
                await asyncio.to_thread(container.kill, "SIGINT")
                log.info("container_sigint", container=name, grace_s=grace_s)
            if not await asyncio.to_thread(self._wait, container, grace_s):
                log.warning("container_sigkill_after_grace", container=name, grace_s=grace_s)
                await asyncio.to_thread(container.kill, "SIGKILL")
                await asyncio.to_thread(self._wait, container, 30.0)
        await asyncio.to_thread(container.remove, force=True)
        self._draining.discard(name)
        self._save_draining()
        log.info("container_stopped", container=name)

    async def health(self, handle: ReplicaHandle) -> bool:
        """Running, not draining, and not reported ``unhealthy`` by a HEALTHCHECK."""
        if handle.instance_key in self._draining:
            return False
        container = await self._get(handle)
        if container is None:
            return False
        await asyncio.to_thread(container.reload)
        health = (container.attrs.get("State") or {}).get("Health") or {}
        return container.status == "running" and health.get("Status") != "unhealthy"

    async def remove(self, handle: ReplicaHandle) -> None:
        """Delete an exited container."""
        container = await self._get(handle)
        if container is not None:
            if container.status in {"running", "paused", "restarting"}:
                log.warning("container_remove_refused_running", container=handle.instance_key)
                return
            await asyncio.to_thread(container.remove, force=True)
        self._draining.discard(handle.instance_key)
        self._save_draining()

    async def aclose(self) -> None:
        """Close the docker client (containers keep running)."""
        if self._client is not None:
            await asyncio.to_thread(self._client.close)
            self._client = None
