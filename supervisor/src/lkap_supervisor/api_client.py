"""The supervisor's view of the api (``/internal/v1/*`` with ``X-Service-Token``).

* ``GET /internal/v1/fleet/desired`` → ``list[FleetDesired]``
* ``GET /internal/v1/connections/{id}/worker-env`` → ``WorkerEnv`` (decrypted —
  fetched just in time for a start and dropped right after, never logged)
* ``POST /internal/v1/workers/register`` / ``…/{key}/heartbeat`` — marks the
  replicas the supervisor starts and stops in ``worker_instances``.

Errors surface as :class:`ApiClientError` whose message carries only the
method, path and status code — never a response body (a worker-env body is
the connection's secret).
"""

from __future__ import annotations

from typing import Protocol

import httpx
from lkap_contracts.fleet import (
    FleetDesired,
    ReplicaHandle,
    WorkerEnv,
    WorkerHeartbeatIn,
    WorkerRegisterIn,
    WorkerStatus,
)
from pydantic import TypeAdapter, ValidationError

SERVICE_HEADER = "X-Service-Token"

_DESIRED: TypeAdapter[list[FleetDesired]] = TypeAdapter(list[FleetDesired])


class ApiClientError(Exception):
    """An api call failed; the message never contains a response body."""


class FleetApi(Protocol):
    """What the reconciler needs from the api."""

    async def fleet_desired(self) -> list[FleetDesired]:
        """Desired state of every supervised pool."""
        ...

    async def worker_env(self, connection_id: str) -> WorkerEnv:
        """The decrypted environment of one pool's replicas."""
        ...

    async def register_replica(self, handle: ReplicaHandle, desired: FleetDesired) -> None:
        """Record a freshly started replica as ``starting`` / ``managed_by=supervisor``."""
        ...

    async def report_status(self, instance_key: str, status: WorkerStatus) -> None:
        """Record a replica's lifecycle change (``draining``, ``gone``)."""
        ...

    async def aclose(self) -> None:
        """Release network resources."""
        ...


class HttpFleetApi:
    """:class:`FleetApi` over httpx."""

    def __init__(
        self,
        base_url: str,
        service_token: str,
        *,
        timeout_s: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Create the client.

        Args:
            base_url: The api root, e.g. ``http://127.0.0.1:8080``.
            service_token: ``LKAP_SERVICE_TOKEN``.
            timeout_s: Per-request timeout.
            transport: Test transport (``httpx.MockTransport`` or respx).
        """
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={SERVICE_HEADER: service_token},
            timeout=timeout_s,
            transport=transport,
            follow_redirects=False,
        )

    async def _request(self, method: str, path: str, *, json: object | None = None) -> httpx.Response:
        try:
            response = await self._client.request(method, path, json=json)
        except httpx.HTTPError as exc:
            raise ApiClientError(f"{method} {path} failed: {type(exc).__name__}") from None
        if response.status_code >= 400:
            raise ApiClientError(f"{method} {path} failed: HTTP {response.status_code}")
        return response

    async def fleet_desired(self) -> list[FleetDesired]:
        """``GET /internal/v1/fleet/desired``.

        Raises:
            ApiClientError: On a transport error, an error status or a malformed body.
        """
        response = await self._request("GET", "/internal/v1/fleet/desired")
        try:
            return _DESIRED.validate_json(response.content)
        except ValidationError:
            raise ApiClientError("GET /internal/v1/fleet/desired returned an invalid body") from None

    async def worker_env(self, connection_id: str) -> WorkerEnv:
        """``GET /internal/v1/connections/{id}/worker-env``. **The result contains secrets.**

        Raises:
            ApiClientError: On any failure (the message never includes the body).
        """
        path = f"/internal/v1/connections/{connection_id}/worker-env"
        response = await self._request("GET", path)
        try:
            return WorkerEnv.model_validate_json(response.content)
        except ValidationError:
            raise ApiClientError(f"GET {path} returned an invalid body") from None

    async def register_replica(self, handle: ReplicaHandle, desired: FleetDesired) -> None:
        """Register the replica, then mark it ``starting`` until the worker reports ``ready``.

        Raises:
            ApiClientError: On any failure.
        """
        body = WorkerRegisterIn(
            connection_id=handle.connection_id,
            instance_key=handle.instance_key,
            image=desired.image,
            managed_by="supervisor",
        )
        await self._request("POST", "/internal/v1/workers/register", json=body.model_dump(mode="json"))
        await self.report_status(handle.instance_key, "starting")

    async def report_status(self, instance_key: str, status: WorkerStatus) -> None:
        """``POST /internal/v1/workers/{key}/heartbeat`` with the given status.

        Raises:
            ApiClientError: On any failure.
        """
        body = WorkerHeartbeatIn(status=status, active_jobs=0)
        await self._request(
            "POST", f"/internal/v1/workers/{instance_key}/heartbeat", json=body.model_dump(mode="json")
        )

    async def aclose(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()
