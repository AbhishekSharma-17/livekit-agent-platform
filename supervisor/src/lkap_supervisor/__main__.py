"""``python -m lkap_supervisor [--once] [--backend subprocess|docker]``.

Loop: take the lease (Redis or file lock), reconcile every
``LKAP_SUPERVISOR_INTERVAL_S`` seconds, renew the lease each pass. SIGINT or
SIGTERM stops the loop; the subprocess backend then drains its children
(SIGINT → ``LKAP_SUPERVISOR_DRAIN_S`` → SIGKILL), the docker backend leaves its
containers running for the next supervisor. A second signal stops waiting for
drains that are still in progress (their workers were already SIGINT'd).

``--once``: one pass, wait for drains that pass started, exit 0 (1 if the
desired state could not be fetched, 2 on a configuration error, 3 if another
supervisor holds the lease). It does not stop the replicas it started and does
not serve metrics.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from collections.abc import Sequence
from contextlib import suppress
from typing import Any

from pydantic import ValidationError

from lkap_supervisor.api_client import FleetApi, HttpFleetApi
from lkap_supervisor.backends import Backend, build_backend
from lkap_supervisor.lease import FileLease, Lease, RedisLease
from lkap_supervisor.logging import configure_logging, get_logger
from lkap_supervisor.reconciler import Reconciler
from lkap_supervisor.settings import SupervisorSettings

log = get_logger(__name__)

EXIT_OK = 0
EXIT_DESIRED_UNAVAILABLE = 1
EXIT_CONFIG = 2
EXIT_LEASE_HELD = 3


def build_lease(settings: SupervisorSettings) -> Lease:
    """Redis lease when ``LKAP_REDIS_URL`` is set, else a file lock in the state dir."""
    if settings.redis_url is not None:
        from redis.asyncio import Redis

        client: Any = Redis.from_url(settings.redis_url.get_secret_value())
        return RedisLease(client, ttl_s=max(3 * settings.interval_s, 30.0))
    return FileLease(settings.state_dir / "supervisor.lock")


async def run(
    settings: SupervisorSettings,
    *,
    once: bool = False,
    api: FleetApi | None = None,
    backend: Backend | None = None,
    lease: Lease | None = None,
    stop: asyncio.Event | None = None,
) -> int:
    """Run the supervisor until ``stop`` is set (or one pass with ``once``).

    Args:
        settings: Supervisor settings.
        once: A single pass.
        api: Api client (built from settings by default).
        backend: Replica backend (built from settings by default).
        lease: Single-instance guard (built from settings by default).
        stop: Set to stop the loop (signal handlers set it by default).

    Returns:
        The process exit code.
    """
    api = api or HttpFleetApi(settings.api_base_url, settings.service_token.get_secret_value())
    backend = backend or build_backend(settings)
    lease = lease or build_lease(settings)
    stop = stop or asyncio.Event()
    reconciler = Reconciler(
        api,
        backend,
        drain_s=settings.drain_s,
        backoff_base_s=settings.backoff_base_s,
        backoff_max_s=settings.backoff_max_s,
        min_ready_s=settings.min_ready_s,
    )
    log.info(
        "supervisor_starting",
        backend=backend.name,
        api=settings.api_base_url,
        interval_s=settings.interval_s,
        drain_s=settings.drain_s,
        once=once,
    )
    try:
        if once:
            if not await lease.acquire():
                log.error("supervisor_lease_held_elsewhere")
                return EXIT_LEASE_HELD
            ok = await reconciler.reconcile_once()
            await reconciler.wait_for_drains()
            return EXIT_OK if ok else EXIT_DESIRED_UNAVAILABLE
        if settings.metrics_port:
            reconciler.metrics.serve(settings.metrics_port)
        await _loop(settings, reconciler, lease, stop)
        log.info("supervisor_stopping", stop_replicas=backend.stops_replicas_on_exit)
        await reconciler.shutdown(stop_replicas=backend.stops_replicas_on_exit)
        return EXIT_OK
    finally:
        await lease.release()
        await backend.aclose()
        await api.aclose()


async def _loop(
    settings: SupervisorSettings, reconciler: Reconciler, lease: Lease, stop: asyncio.Event
) -> None:
    held = False
    while not stop.is_set():
        held = await lease.renew() if held else await lease.acquire()
        if held:
            await reconciler.reconcile_once()
        else:
            log.info("supervisor_standby", reason="lease held by another supervisor")
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), settings.interval_s)


def _install_signal_handlers(stop: asyncio.Event, main_task: asyncio.Task[int]) -> None:
    loop = asyncio.get_running_loop()
    received = 0

    def on_signal(signame: str) -> None:
        nonlocal received
        received += 1
        log.info("supervisor_signal", signal=signame, count=received)
        if received == 1:
            stop.set()
        else:
            main_task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, on_signal, sig.name)


async def _amain(settings: SupervisorSettings, once: bool) -> int:
    stop = asyncio.Event()
    task = asyncio.create_task(run(settings, once=once, stop=stop))
    _install_signal_handlers(stop, task)
    try:
        return await task
    except asyncio.CancelledError:
        log.warning("supervisor_forced_exit", note="drains still in progress were abandoned")
        return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    """Console entry point."""
    parser = argparse.ArgumentParser(prog="lkap_supervisor", description=__doc__.split("\n\n")[0])
    parser.add_argument("--once", action="store_true", help="run one reconcile pass and exit")
    parser.add_argument(
        "--backend", choices=["subprocess", "docker"], help="override LKAP_SUPERVISOR_BACKEND"
    )
    args = parser.parse_args(argv)
    try:
        overrides: dict[str, Any] = {"backend": args.backend} if args.backend else {}
        settings = SupervisorSettings(**overrides)
    except ValidationError as exc:
        configure_logging()
        problems = [f"{'.'.join(str(p) for p in err['loc'])}: {err['type']}" for err in exc.errors()]
        log.error("supervisor_config_invalid", problems=problems)
        return EXIT_CONFIG
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    return asyncio.run(_amain(settings, args.once))


if __name__ == "__main__":  # pragma: no cover - process entry
    sys.exit(main())
