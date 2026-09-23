"""The reconcile loop (CONTRACTS-V2 §5, ARCHITECTURE-V2 D-V2-2).

One pass:

1. ``GET /internal/v1/fleet/desired``. If the api is unreachable the pass stops
   here and **nothing** is touched: an api outage never stops a pool.
2. ``backend.list()``, grouped by connection. A connection that has replicas
   but no desired entry (deleted, or switched away from ``supervised``) has a
   desired size of 0.
3. Per pool:

   * ``failed`` replicas (exited without being drained) are forgotten and
     their slot is restarted after an exponential backoff
     (``base · 2^(n-1)``, capped at 5 min; reset once a replica in that slot
     has run for :data:`STABLE_AFTER_S`).
   * **Starts**: every slot ``0..desired-1`` without a replica on the desired
     hash gets one. The ``WorkerEnv`` is fetched just in time, once per pool
     per pass, and dropped when the starts are done. Stale-hash replicas keep
     serving meanwhile (surge first, so capacity never drops on rotation).
   * **Drains, one at a time per pool** (never two down at once): while a
     drain of this pool is in flight nothing else is drained. The next victim
     is an extra replica (slot ≥ desired, or a duplicate), else a stale-hash
     replica whose slot has been replaced by a healthy replica that has run
     for ``min_ready_s``. A drain runs in the background (SIGINT → grace →
     SIGKILL, see the backends), because the grace is up to
     ``LKAP_SUPERVISOR_DRAIN_S``.

4. ``worker_instances`` is updated through the api: ``register`` (+
   ``starting``) on start, ``draining`` when a drain begins, ``gone`` when a
   replica is stopped or found dead. Api failures here are logged, never fatal.

Secrets: the ``WorkerEnv`` exists only inside :meth:`Reconciler._start_missing`
and is passed straight to ``backend.start``; no log call ever receives it.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from lkap_contracts.fleet import FleetDesired, ReplicaHandle, WorkerStatus

from lkap_supervisor.api_client import ApiClientError, FleetApi
from lkap_supervisor.backends.base import Backend, BackendError
from lkap_supervisor.logging import get_logger
from lkap_supervisor.metrics import Metrics

log = get_logger(__name__)

#: A replica that has run this long resets its slot's failure count.
STABLE_AFTER_S = 60.0

_LIVE_STATES = frozenset({"starting", "running"})


@dataclass
class _Slot:
    failures: int = 0
    not_before: float = 0.0


class Reconciler:
    """Converges a backend on the api's desired fleet."""

    def __init__(
        self,
        api: FleetApi,
        backend: Backend,
        *,
        drain_s: float,
        backoff_base_s: float = 5.0,
        backoff_max_s: float = 300.0,
        min_ready_s: float = 5.0,
        metrics: Metrics | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
    ) -> None:
        """Wire the loop.

        Args:
            api: The api client.
            backend: Where replicas run.
            drain_s: Grace between SIGINT and SIGKILL (``LKAP_SUPERVISOR_DRAIN_S``).
            backoff_base_s: First restart delay of a failed slot.
            backoff_max_s: Restart delay cap (5 min).
            min_ready_s: How long a replacement must have been healthy before
                the stale replica it replaces is drained.
            metrics: Prometheus metrics (a private registry by default).
            clock: Monotonic clock for backoff (tests inject one).
            wall: Wall clock compared with ``ReplicaHandle.started_at``.
        """
        self._api = api
        self._backend = backend
        self._drain_s = drain_s
        self._backoff_base_s = backoff_base_s
        self._backoff_max_s = backoff_max_s
        self._min_ready_s = min_ready_s
        self.metrics = metrics or Metrics()
        self._clock = clock
        self._wall = wall
        self._slots: dict[tuple[str, int], _Slot] = defaultdict(_Slot)
        self._drains: dict[str, asyncio.Task[None]] = {}
        self._drain_owner: dict[str, str] = {}

    # ------------------------------------------------------------------ public API
    @property
    def draining_keys(self) -> set[str]:
        """Instance keys whose drain task is in flight."""
        return {key for key, task in self._drains.items() if not task.done()}

    async def reconcile_once(self) -> bool:
        """Run one pass.

        Returns:
            ``False`` when the desired state could not be fetched (nothing was touched).
        """
        started = time.perf_counter()
        try:
            self._reap_drains()
            try:
                desired = await self._api.fleet_desired()
            except ApiClientError as exc:
                log.warning("fleet_desired_unavailable", error=str(exc))
                return False
            try:
                handles = await self._backend.list()
            except BackendError as exc:
                log.error("backend_list_failed", backend=self._backend.name, error=str(exc))
                return False
            by_pool: dict[str, list[ReplicaHandle]] = defaultdict(list)
            for handle in handles:
                by_pool[handle.connection_id].append(handle)
            wanted = {d.connection_id: d for d in desired}
            for connection_id in sorted(set(by_pool) | set(wanted)):
                try:
                    await self._reconcile_pool(
                        connection_id, wanted.get(connection_id), by_pool[connection_id]
                    )
                except Exception:
                    log.exception("pool_reconcile_failed", connection_id=connection_id)
            try:
                self.metrics.set_replicas(await self._backend.list())
            except BackendError:
                pass
            return True
        finally:
            self.metrics.reconcile_seconds.observe(time.perf_counter() - started)

    async def wait_for_drains(self) -> None:
        """Wait for every in-flight drain to finish."""
        tasks = [task for task in self._drains.values() if not task.done()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._reap_drains()

    async def shutdown(self, *, stop_replicas: bool) -> None:
        """Stop reconciling.

        Args:
            stop_replicas: Drain every replica concurrently (the subprocess
                backend, whose children must not outlive the supervisor) and
                wait for them. Otherwise leave the pool running and only wait
                for drains already in flight.
        """
        if stop_replicas:
            try:
                handles = await self._backend.list()
            except BackendError as exc:
                log.error("backend_list_failed", backend=self._backend.name, error=str(exc))
                handles = []
            for handle in handles:
                if handle.state in _LIVE_STATES | {"draining"} and handle.instance_key not in self._drains:
                    self._start_drain(handle)
        await self.wait_for_drains()

    # -------------------------------------------------------------------- internals
    def _reap_drains(self) -> None:
        for key, task in list(self._drains.items()):
            if not task.done():
                continue
            self._drains.pop(key, None)
            self._drain_owner.pop(key, None)
            if not task.cancelled() and task.exception() is not None:
                log.error(
                    "replica_drain_failed", instance_key=key, error_type=type(task.exception()).__name__
                )

    def _backoff(self, failures: int) -> float:
        return float(min(self._backoff_base_s * 2 ** max(failures - 1, 0), self._backoff_max_s))

    async def _report(self, instance_key: str, status: WorkerStatus) -> None:
        try:
            await self._api.report_status(instance_key, status)
        except ApiClientError as exc:
            log.warning(
                "worker_status_report_failed", instance_key=instance_key, status=status, error=str(exc)
            )

    async def _reconcile_pool(
        self, connection_id: str, desired: FleetDesired | None, handles: Sequence[ReplicaHandle]
    ) -> None:
        target = desired.desired_replicas if desired is not None else 0
        want_hash = desired.desired_hash if desired is not None else None

        for handle in handles:
            if handle.state == "failed":
                await self._forget_failed(handle)
            elif handle.state == "stopped" and handle.instance_key not in self._drains:
                await self._backend.remove(handle)
                await self._report(handle.instance_key, "gone")

        live = [h for h in handles if h.state in _LIVE_STATES and h.instance_key not in self._drains]
        current = [h for h in live if h.desired_hash == want_hash]
        stale = [h for h in live if h.desired_hash != want_hash]

        now_wall = self._wall()
        for handle in current:
            if now_wall - handle.started_at >= STABLE_AFTER_S:
                self._slots[(connection_id, handle.replica_index)].failures = 0

        if desired is not None and target > 0:
            current.extend(await self._start_missing(desired, current))

        in_flight = any(owner == connection_id for owner in self._drain_owner.values()) or any(
            h.state == "draining" for h in handles
        )
        orphaned = [h for h in handles if h.state == "draining" and h.instance_key not in self._drains]
        for handle in orphaned:
            # Signalled by a previous supervisor: resume waiting (the backend never re-signals).
            self._start_drain(handle)
        if in_flight:
            return
        victim = await self._pick_victim(target, current, stale)
        if victim is not None:
            self._start_drain(victim)

    async def _forget_failed(self, handle: ReplicaHandle) -> None:
        slot = self._slots[(handle.connection_id, handle.replica_index)]
        slot.failures += 1
        delay = self._backoff(slot.failures)
        slot.not_before = self._clock() + delay
        log.warning(
            "replica_failed",
            connection_id=handle.connection_id,
            replica_index=handle.replica_index,
            instance_key=handle.instance_key,
            failures=slot.failures,
            restart_in_s=delay,
        )
        await self._backend.remove(handle)
        await self._report(handle.instance_key, "gone")

    async def _start_missing(
        self, desired: FleetDesired, current: Sequence[ReplicaHandle]
    ) -> list[ReplicaHandle]:
        occupied = {h.replica_index for h in current}
        now = self._clock()
        due = [
            index
            for index in range(desired.desired_replicas)
            if index not in occupied and self._slots[(desired.connection_id, index)].not_before <= now
        ]
        if not due:
            return []
        try:
            env = await self._api.worker_env(desired.connection_id)
        except ApiClientError as exc:
            log.error("worker_env_unavailable", connection_id=desired.connection_id, error=str(exc))
            return []
        started: list[ReplicaHandle] = []
        try:
            for index in due:
                slot = self._slots[(desired.connection_id, index)]
                try:
                    handle = await self._backend.start(desired, index, env)
                except BackendError as exc:
                    slot.failures += 1
                    delay = self._backoff(slot.failures)
                    slot.not_before = self._clock() + delay
                    log.error(
                        "replica_start_failed",
                        connection_id=desired.connection_id,
                        replica_index=index,
                        error=str(exc),
                        retry_in_s=delay,
                    )
                    continue
                if slot.failures:
                    self.metrics.restarts.labels(connection=desired.connection_id).inc()
                log.info(
                    "replica_started",
                    connection_id=desired.connection_id,
                    replica_index=index,
                    instance_key=handle.instance_key,
                    desired_hash=desired.desired_hash[:12],
                    restart=bool(slot.failures),
                )
                started.append(handle)
                try:
                    await self._api.register_replica(handle, desired)
                except ApiClientError as exc:
                    log.warning("replica_register_failed", instance_key=handle.instance_key, error=str(exc))
        finally:
            del env
        return started

    async def _ready(self, handle: ReplicaHandle) -> bool:
        if self._wall() - handle.started_at < self._min_ready_s:
            return False
        try:
            return await self._backend.health(handle)
        except BackendError:
            return False

    async def _pick_victim(
        self, target: int, current: Sequence[ReplicaHandle], stale: Sequence[ReplicaHandle]
    ) -> ReplicaHandle | None:
        by_slot: dict[int, list[ReplicaHandle]] = defaultdict(list)
        for handle in current:
            by_slot[handle.replica_index].append(handle)
        # Scale-down: the highest slot goes first, so a pool shrinks from the top.
        for index in sorted(by_slot, reverse=True):
            group = sorted(by_slot[index], key=lambda h: h.started_at)
            if index >= target or len(group) > 1:
                return group[-1]
        for handle in sorted(stale, key=lambda h: h.replica_index):
            if handle.replica_index >= target:
                return handle
            replacements = by_slot.get(handle.replica_index, [])
            if replacements and await self._ready(replacements[0]):
                return handle
        return None

    def _start_drain(self, handle: ReplicaHandle) -> None:
        async def run() -> None:
            await self._report(handle.instance_key, "draining")
            try:
                await self._backend.drain(handle, self._drain_s)
            finally:
                await self._report(handle.instance_key, "gone")

        log.info(
            "replica_draining",
            connection_id=handle.connection_id,
            replica_index=handle.replica_index,
            instance_key=handle.instance_key,
            grace_s=self._drain_s,
        )
        self._drain_owner[handle.instance_key] = handle.connection_id
        self._drains[handle.instance_key] = asyncio.create_task(run(), name=f"drain:{handle.instance_key}")
