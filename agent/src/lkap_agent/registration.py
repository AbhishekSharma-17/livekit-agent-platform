"""Worker registration and heartbeat against the api (CONTRACTS-V2 §3.4/§5, D-V2-8).

Once the `AgentServer` has registered with LiveKit (its `worker_registered`
event, emitted from the worker's own event loop in the main process), the
worker posts ``POST /internal/v1/workers/register`` with:

* ``connection_id`` — ``LKAP_CONNECTION_ID``; unset ⇒ the api attributes the
  worker to the default connection;
* ``instance_key`` — ``LKAP_INSTANCE_KEY`` if set, else ``hostname:pid``;
* ``image`` (``LKAP_IMAGE_FLAVOR``), ``sdk_version`` (livekit-agents);
* ``installed_provider_ids`` — the build-time import check's
  ``installed_providers.json`` (CONTRACTS-V2 §7) when present, else every
  ``available`` registry entry of a kind this worker constructs whose
  distribution is installed in the running environment (a dev venv);
* ``pack_ids`` — the packs ``LKAP_PACKS`` resolves to;
* ``managed_by`` — ``LKAP_MANAGED_BY`` (default ``external``; the supervisor
  sets ``supervisor``, and the api keeps a supervisor's claim sticky anyway).

Then it heartbeats every ``LKAP_HEARTBEAT_INTERVAL_S`` (30 s) with ``ready``,
or ``draining`` as soon as the SDK starts draining (the first SIGINT/SIGTERM in
``start`` mode), plus the number of running jobs. A 404 means the api lost the
row, so the worker registers again. Every failure is logged and retried on the
next beat; registration never affects call handling.

Nothing here reads or logs a secret; the service token only travels as a header.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.metadata
import json
import os
import socket
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Final, Protocol

import httpx
from lkap_contracts import providers as provider_registry
from lkap_contracts.fleet import (
    WorkerHeartbeatIn,
    WorkerRegisterIn,
    WorkerRegisterOut,
    WorkerStatus,
)

from lkap_agent.logging import get_logger
from lkap_agent.providers.factory import CONSTRUCTIBLE_KINDS
from lkap_agent.settings import Settings

__all__ = [
    "FleetApiError",
    "FleetClient",
    "FleetClientProtocol",
    "ServerLike",
    "WorkerRegistration",
    "default_instance_key",
    "discover_installed_provider_ids",
    "installed_provider_ids",
    "sdk_version",
]

logger = get_logger(__name__)

_SERVICE_TOKEN_HEADER: Final[str] = "X-Service-Token"

#: Keys a `installed_providers.json` object may carry its id list under.
_INSTALLED_JSON_KEYS: Final[tuple[str, ...]] = ("installed_provider_ids", "provider_ids", "installed")

#: How often the heartbeat loop wakes to notice a status change (draining) early.
_POLL_S: Final[float] = 1.0


class FleetApiError(RuntimeError):
    """The api's worker endpoints could not be reached or answered an error."""


class FleetClientProtocol(Protocol):
    """The two worker-fleet calls, so tests can supply a fake."""

    async def register(self, payload: WorkerRegisterIn) -> WorkerRegisterOut:
        """Register this process; raises :class:`FleetApiError` on failure."""
        ...

    async def heartbeat(self, instance_key: str, payload: WorkerHeartbeatIn) -> bool:
        """Heartbeat; `False` means the api does not know the key (register again)."""
        ...


class FleetClient:
    """`httpx`-backed :class:`FleetClientProtocol`."""

    def __init__(
        self,
        base_url: str,
        service_token: str,
        *,
        timeout_s: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Create a client for the api's `/internal/v1/workers` surface.

        Args:
            base_url: e.g. `http://127.0.0.1:8080`.
            service_token: The static `LKAP_SERVICE_TOKEN`.
            timeout_s: Per-request timeout.
            client: An existing client to use (tests pass a `respx`-mounted one).
        """
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=timeout_s)
        self._headers = {_SERVICE_TOKEN_HEADER: service_token, "content-type": "application/json"}

    async def register(self, payload: WorkerRegisterIn) -> WorkerRegisterOut:
        """Post `POST /internal/v1/workers/register`.

        Raises:
            FleetApiError: On a transport error, an error status or an unparseable body.
        """
        url = f"{self._base_url}/internal/v1/workers/register"
        try:
            response = await self._client.post(url, content=payload.model_dump_json(), headers=self._headers)
        except httpx.HTTPError as exc:
            raise FleetApiError(f"api unreachable at {url}: {exc}") from exc
        if response.status_code >= httpx.codes.BAD_REQUEST:
            raise FleetApiError(f"workers/register answered HTTP {response.status_code}")
        try:
            return WorkerRegisterOut.model_validate_json(response.content)
        except ValueError as exc:
            raise FleetApiError("workers/register returned an unparseable payload") from exc

    async def heartbeat(self, instance_key: str, payload: WorkerHeartbeatIn) -> bool:
        """Post `POST /internal/v1/workers/{instance_key}/heartbeat`.

        Returns:
            `False` on 404 (unknown instance: register again), `True` otherwise.

        Raises:
            FleetApiError: On a transport error or any other error status.
        """
        url = f"{self._base_url}/internal/v1/workers/{instance_key}/heartbeat"
        try:
            response = await self._client.post(url, content=payload.model_dump_json(), headers=self._headers)
        except httpx.HTTPError as exc:
            raise FleetApiError(f"api unreachable at {url}: {exc}") from exc
        if response.status_code == httpx.codes.NOT_FOUND:
            return False
        if response.status_code >= httpx.codes.BAD_REQUEST:
            raise FleetApiError(f"heartbeat answered HTTP {response.status_code}")
        return True

    async def aclose(self) -> None:
        """Close the HTTP pool."""
        await self._client.aclose()


class ServerLike(Protocol):
    """The slice of `livekit.agents.AgentServer` registration reads."""

    @property
    def draining(self) -> bool:
        """Whether the server stopped accepting jobs."""
        ...

    @property
    def active_jobs(self) -> list[Any]:
        """The jobs currently running."""
        ...

    def on(self, event: Any, callback: Callable[..., Any] | None = None) -> Any:
        """Subscribe to a server event."""
        ...


# ------------------------------------------------------------------ discovery


def default_instance_key(settings: Settings) -> str:
    """`LKAP_INSTANCE_KEY`, else `hostname:pid` (CONTRACTS-V2 §1.2)."""
    if settings.instance_key and settings.instance_key.strip():
        return settings.instance_key.strip()
    return f"{socket.gethostname()}:{os.getpid()}"


def sdk_version() -> str:
    """The running livekit-agents version."""
    try:
        return importlib.metadata.version("livekit-agents")
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover - always installed
        return ""


def _distribution_installed(name: str) -> bool:
    try:
        importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError:
        return False
    return True


def discover_installed_provider_ids() -> list[str]:
    """Registry entries this environment can construct, from installed distributions.

    An entry counts when it is `available`, of a kind the worker's factory
    builds (`CONSTRUCTIBLE_KINDS`), and its `package` distribution is installed.
    The check reads package metadata only — it imports no plugin, so it is
    cheap enough to run on the worker's event loop. It is the dev-venv
    fallback; images ship the stricter import-checked `installed_providers.json`.

    Returns:
        Sorted provider ids.
    """
    found: list[str] = []
    for spec in provider_registry.REGISTRY:
        if spec.availability != "available" or spec.kind not in CONSTRUCTIBLE_KINDS:
            continue
        if spec.package and _distribution_installed(spec.package):
            found.append(spec.id)
    return sorted(found)


def _ids_from_json(raw: Any) -> list[str] | None:
    if isinstance(raw, list):
        return sorted({str(i) for i in raw})
    if isinstance(raw, dict):
        for key in _INSTALLED_JSON_KEYS:
            value = raw.get(key)
            if isinstance(value, list):
                return sorted({str(i) for i in value})
    return None


def installed_provider_ids(settings: Settings) -> list[str]:
    """The provider ids this worker reports as installed.

    Args:
        settings: Supplies `LKAP_INSTALLED_PROVIDERS_FILE`.

    Returns:
        The ids from the build-time `installed_providers.json` (a JSON list, or
        an object with an `installed_provider_ids`/`provider_ids`/`installed`
        list) when that file exists and parses; otherwise
        :func:`discover_installed_provider_ids`.
    """
    path = Path(settings.installed_providers_file)
    if path.is_file():
        try:
            ids = _ids_from_json(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            ids = None
        if ids is not None:
            return ids
        logger.warning("installed providers file is unreadable, discovering instead", path=str(path))
    return discover_installed_provider_ids()


# ---------------------------------------------------------------- registration


async def _sleep(delay_s: float) -> None:
    await asyncio.sleep(delay_s)


class WorkerRegistration:
    """Registers this worker process with the api and keeps it heartbeating."""

    def __init__(
        self,
        *,
        client: FleetClientProtocol,
        settings: Settings,
        server: ServerLike,
        pack_ids: Callable[[], list[str]] = list,
        installed: Callable[[], list[str]] | None = None,
        sleep: Callable[[float], Awaitable[None]] = _sleep,
        poll_s: float = _POLL_S,
    ) -> None:
        """Create the registration for one worker process.

        Args:
            client: The api client.
            settings: Worker settings (connection id, agent name, image, interval).
            server: The `AgentServer` (for `draining` / `active_jobs`).
            pack_ids: Returns the pack ids this worker serves (evaluated lazily).
            installed: Returns the installed provider ids; defaults to
                :func:`installed_provider_ids` (evaluated once, lazily).
            sleep: Clock seam for the heartbeat loop.
            poll_s: How often the loop checks for a status change between beats.
        """
        self._client = client
        self._settings = settings
        self._server = server
        self._pack_ids = pack_ids
        self._installed = installed or (lambda: installed_provider_ids(settings))
        self._sleep = sleep
        self._poll_s = poll_s
        self.instance_key = default_instance_key(settings)
        self.registered = False
        self.connection_id: str | None = None
        self._payload: WorkerRegisterIn | None = None
        self._task: asyncio.Task[None] | None = None
        self._closed = False

    # -- wiring -----------------------------------------------------------------

    def attach(self) -> None:
        """Subscribe to the server's `worker_registered` event."""
        self._server.on("worker_registered", self.on_worker_registered)

    def on_worker_registered(self, *_args: Any) -> None:
        """Synchronous `worker_registered` handler: start (or keep) the heartbeat loop.

        The SDK emits this again after it re-registers with LiveKit; the loop
        then simply registers once more on its next beat.
        """
        if self._closed:
            return
        self.registered = False
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self.run())

    def close(self) -> None:
        """Stop the heartbeat loop."""
        self._closed = True
        if self._task is not None and not self._task.done():
            self._task.cancel()

    # -- api calls --------------------------------------------------------------

    def _status(self) -> tuple[WorkerStatus, int]:
        status: WorkerStatus = "draining" if self._server.draining else "ready"
        try:
            active = len(self._server.active_jobs)
        except Exception:
            active = 0
        return status, active

    def _register_payload(self) -> WorkerRegisterIn:
        if self._payload is None:
            self._payload = WorkerRegisterIn(
                connection_id=self._settings.connection_id or None,
                instance_key=self.instance_key,
                image=self._settings.image_flavor,
                sdk_version=sdk_version(),
                installed_provider_ids=self._installed(),
                pack_ids=self._pack_ids(),
                managed_by=self._settings.managed_by,
            )
        return self._payload

    async def register(self) -> bool:
        """Register once; returns whether the api accepted it."""
        try:
            payload = self._register_payload()
            out = await self._client.register(payload)
        except FleetApiError as exc:
            logger.warning("worker registration failed; will retry", error=str(exc))
            return False
        except Exception:
            logger.warning("worker registration failed; will retry", exc_info=True)
            return False
        self.registered = True
        self.connection_id = out.connection_id
        if out.agent_name != self._settings.agent_name:
            logger.error(
                "the api dispatches this connection under another agent name; this worker will get no jobs",
                worker_agent_name=self._settings.agent_name,
                connection_agent_name=out.agent_name,
                connection_id=out.connection_id,
            )
        logger.info(
            "worker registered with the api",
            instance_key=self.instance_key,
            connection_id=out.connection_id,
            image=payload.image,
            installed_providers=len(payload.installed_provider_ids),
        )
        return True

    async def heartbeat(self) -> None:
        """Send one heartbeat, registering first (or again) when needed."""
        if not self.registered and not await self.register():
            return
        status, active = self._status()
        try:
            known = await self._client.heartbeat(
                self.instance_key, WorkerHeartbeatIn(status=status, active_jobs=active)
            )
        except FleetApiError as exc:
            logger.warning("worker heartbeat failed", error=str(exc))
            return
        except Exception:
            logger.warning("worker heartbeat failed", exc_info=True)
            return
        if not known:
            logger.info("api no longer knows this worker; registering again", instance_key=self.instance_key)
            self.registered = False
            if await self.register():
                with contextlib.suppress(Exception):
                    await self._client.heartbeat(
                        self.instance_key, WorkerHeartbeatIn(status=status, active_jobs=active)
                    )

    async def run(self) -> None:
        """Register, then heartbeat every interval (earlier when the status changes)."""
        if not self.registered:
            await self.register()
        interval = max(self._settings.heartbeat_interval_s, self._poll_s)
        last_status, _ = self._status()
        elapsed = 0.0
        while not self._closed:
            await self._sleep(self._poll_s)
            if self._closed:
                return
            elapsed += self._poll_s
            status, _ = self._status()
            if elapsed + 1e-9 >= interval or status != last_status:
                await self.heartbeat()
                elapsed = 0.0
                last_status = status
