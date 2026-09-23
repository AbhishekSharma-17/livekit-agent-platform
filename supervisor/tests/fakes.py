"""In-memory fakes for the reconciler tests: a ``FakeBackend`` and a ``FakeApi``."""

from __future__ import annotations

import asyncio
import builtins
from collections.abc import Callable

from lkap_contracts.fleet import FleetDesired, ReplicaHandle, WorkerEnv, WorkerStatus

from lkap_supervisor.api_client import ApiClientError
from lkap_supervisor.backends.base import BackendError

SECRET = "sk-live-TOPSECRET-livekit-api-secret"
SERVICE_TOKEN = "service-token-TOPSECRET-0123456789abcdef"


def desired(connection_id: str = "conn-a", replicas: int = 2, digest: str = "a" * 64) -> FleetDesired:
    """A ``FleetDesired`` row."""
    return FleetDesired(
        connection_id=connection_id,
        agent_name=f"agent-{connection_id}",
        desired_replicas=replicas,
        desired_hash=digest,
        image="slim",
        packs=["packs.generic"],
    )


def worker_env(connection_id: str = "conn-a") -> WorkerEnv:
    """A secret-bearing worker env."""
    return WorkerEnv(
        env={
            "LIVEKIT_URL": "wss://example.livekit.cloud",
            "LIVEKIT_API_KEY": "APIkey1234",
            "LIVEKIT_API_SECRET": SECRET,
            "LKAP_SERVICE_TOKEN": SERVICE_TOKEN,
            "LKAP_CONNECTION_ID": connection_id,
            "LKAP_AGENT_NAME": f"agent-{connection_id}",
            "LKAP_API_BASE_URL": "http://127.0.0.1:8080",
        },
        image="slim",
        agent_name=f"agent-{connection_id}",
    )


class FakeApi:
    """Records calls; ``desired`` and ``env`` are set by the test."""

    def __init__(self, rows: list[FleetDesired] | None = None) -> None:
        self.rows = rows or []
        self.fail_desired = False
        self.fail_env = False
        self.env_calls: list[str] = []
        self.registered: list[str] = []
        self.statuses: list[tuple[str, WorkerStatus]] = []

    async def fleet_desired(self) -> list[FleetDesired]:
        if self.fail_desired:
            raise ApiClientError("GET /internal/v1/fleet/desired failed: ConnectError")
        return list(self.rows)

    async def worker_env(self, connection_id: str) -> WorkerEnv:
        self.env_calls.append(connection_id)
        if self.fail_env:
            raise ApiClientError(f"GET /internal/v1/connections/{connection_id}/worker-env failed: HTTP 500")
        return worker_env(connection_id)

    async def register_replica(self, handle: ReplicaHandle, desired: FleetDesired) -> None:
        self.registered.append(handle.instance_key)
        self.statuses.append((handle.instance_key, "starting"))

    async def report_status(self, instance_key: str, status: WorkerStatus) -> None:
        self.statuses.append((instance_key, status))

    async def aclose(self) -> None:
        return None


class FakeBackend:
    """Replicas as dict entries; drains can be held open with ``gate``."""

    name = "fake"
    stops_replicas_on_exit = True

    def __init__(self, wall: Callable[[], float]) -> None:
        self._wall = wall
        self.handles: dict[str, ReplicaHandle] = {}
        self.starts: list[tuple[str, int, dict[str, str]]] = []
        self.drained: list[str] = []
        self.gate: asyncio.Event | None = None
        self.draining_now = 0
        self.max_draining = 0
        self.fail_start = False
        self._n = 0

    def add(self, connection_id: str, index: int, digest: str, *, started_at: float = 0.0) -> str:
        self._n += 1
        key = f"fake-{self._n}"
        self.handles[key] = ReplicaHandle(
            connection_id=connection_id,
            replica_index=index,
            instance_key=key,
            desired_hash=digest,
            state="running",
            started_at=started_at,
            pid_or_container=str(1000 + self._n),
        )
        return key

    def set_state(self, key: str, state: str) -> None:
        self.handles[key] = self.handles[key].model_copy(update={"state": state})

    def live(self, connection_id: str | None = None) -> builtins.list[ReplicaHandle]:
        return [
            h
            for h in self.handles.values()
            if h.state == "running" and (connection_id is None or h.connection_id == connection_id)
        ]

    async def list(self) -> builtins.list[ReplicaHandle]:
        return [h.model_copy() for h in self.handles.values()]

    async def start(self, desired: FleetDesired, index: int, env: WorkerEnv) -> ReplicaHandle:
        if self.fail_start:
            raise BackendError("cannot spawn the worker: FileNotFoundError (No such file or directory)")
        key = self.add(desired.connection_id, index, desired.desired_hash, started_at=self._wall())
        self.starts.append((desired.connection_id, index, dict(env.env)))
        return self.handles[key]

    async def drain(self, handle: ReplicaHandle, grace_s: float) -> None:
        self.set_state(handle.instance_key, "draining")
        self.draining_now += 1
        self.max_draining = max(self.max_draining, self.draining_now)
        try:
            if self.gate is not None:
                await self.gate.wait()
        finally:
            self.draining_now -= 1
        self.drained.append(handle.instance_key)
        self.handles.pop(handle.instance_key, None)

    async def health(self, handle: ReplicaHandle) -> bool:
        current = self.handles.get(handle.instance_key)
        return current is not None and current.state == "running"

    async def remove(self, handle: ReplicaHandle) -> None:
        self.handles.pop(handle.instance_key, None)

    async def aclose(self) -> None:
        return None


class Clock:
    """A manual clock usable as both the monotonic and the wall clock."""

    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds
