"""Reconcile-loop acceptance tests (PLAN-V2 V2-04) against ``FakeBackend`` / ``FakeApi``."""

from __future__ import annotations

import asyncio

import pytest

from conftest import all_log_text
from fakes import SECRET, SERVICE_TOKEN, Clock, FakeApi, FakeBackend, desired
from lkap_supervisor.reconciler import Reconciler

HASH_A = "a" * 64
HASH_B = "b" * 64


def _reconciler(api: FakeApi, backend: FakeBackend, clock: Clock, **kwargs: float) -> Reconciler:
    return Reconciler(
        api,
        backend,
        drain_s=kwargs.pop("drain_s", 30.0),
        backoff_base_s=kwargs.pop("backoff_base_s", 5.0),
        backoff_max_s=kwargs.pop("backoff_max_s", 300.0),
        min_ready_s=kwargs.pop("min_ready_s", 5.0),
        clock=clock,
        wall=clock,
    )


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


async def test_two_replicas_from_zero_start_twice_with_the_worker_env() -> None:
    clock = Clock()
    api = FakeApi([desired(replicas=2)])
    backend = FakeBackend(clock)
    reconciler = _reconciler(api, backend, clock)

    assert await reconciler.reconcile_once() is True

    assert [(c, i) for c, i, _ in backend.starts] == [("conn-a", 0), ("conn-a", 1)]
    assert all(env["LIVEKIT_API_SECRET"] == SECRET for _, _, env in backend.starts)
    assert all(env["LKAP_CONNECTION_ID"] == "conn-a" for _, _, env in backend.starts)
    assert api.env_calls == ["conn-a"]
    assert len(api.registered) == 2
    assert {status for _, status in api.statuses} == {"starting"}


async def test_a_converged_pool_is_left_alone() -> None:
    clock = Clock()
    api = FakeApi([desired(replicas=2)])
    backend = FakeBackend(clock)
    reconciler = _reconciler(api, backend, clock)
    await reconciler.reconcile_once()

    await reconciler.reconcile_once()

    assert len(backend.starts) == 2
    assert api.env_calls == ["conn-a"]
    assert backend.drained == []


async def test_hash_change_rolls_the_pool_never_two_down_at_once() -> None:
    clock = Clock()
    backend = FakeBackend(clock)
    old = [backend.add("conn-a", i, HASH_A, started_at=clock()) for i in range(2)]
    api = FakeApi([desired(replicas=2, digest=HASH_B)])
    reconciler = _reconciler(api, backend, clock, min_ready_s=5.0)
    backend.gate = asyncio.Event()

    await reconciler.reconcile_once()  # surge: both replacements start, nothing drains yet
    assert [(i, env["LKAP_CONNECTION_ID"]) for _, i, env in backend.starts] == [(0, "conn-a"), (1, "conn-a")]
    assert backend.drained == [] and reconciler.draining_keys == set()

    clock.advance(6)
    await reconciler.reconcile_once()  # replacements healthy for 6 s: drain exactly one old replica
    await _settle()
    assert reconciler.draining_keys == {old[0]}

    await reconciler.reconcile_once()  # still in flight: no second drain
    await _settle()
    assert reconciler.draining_keys == {old[0]}

    backend.gate.set()
    await _settle()
    backend.gate = asyncio.Event()
    await reconciler.reconcile_once()
    await _settle()
    assert reconciler.draining_keys == {old[1]}
    backend.gate.set()
    await reconciler.wait_for_drains()

    assert backend.drained == old
    assert backend.max_draining == 1
    assert {h.desired_hash for h in backend.live()} == {HASH_B}
    assert len(backend.live()) == 2
    for key in old:
        assert [s for k, s in api.statuses if k == key] == ["draining", "gone"]


async def test_stale_replica_is_kept_while_its_replacement_is_not_ready() -> None:
    clock = Clock()
    backend = FakeBackend(clock)
    old = backend.add("conn-a", 0, HASH_A, started_at=clock())
    api = FakeApi([desired(replicas=1, digest=HASH_B)])
    reconciler = _reconciler(api, backend, clock, min_ready_s=5.0)

    await reconciler.reconcile_once()
    new = next(k for k in backend.handles if k != old)
    backend.set_state(new, "failed")  # the replacement dies (e.g. rotated secret is wrong)
    clock.advance(10)
    await reconciler.reconcile_once()
    await _settle()

    assert old in backend.handles and backend.handles[old].state == "running"
    assert backend.drained == []


async def test_failed_replica_restarts_with_exponential_backoff() -> None:
    clock = Clock()
    api = FakeApi([desired(replicas=1)])
    backend = FakeBackend(clock)
    reconciler = _reconciler(api, backend, clock, backoff_base_s=5.0)
    await reconciler.reconcile_once()
    first = next(iter(backend.handles))

    backend.set_state(first, "failed")
    await reconciler.reconcile_once()  # forgotten, restart scheduled in 5 s
    assert backend.handles == {} and len(backend.starts) == 1
    clock.advance(4.9)
    await reconciler.reconcile_once()
    assert len(backend.starts) == 1
    clock.advance(0.2)
    await reconciler.reconcile_once()
    assert len(backend.starts) == 2

    second = next(iter(backend.handles))
    backend.set_state(second, "failed")
    await reconciler.reconcile_once()  # second failure: 10 s
    clock.advance(9.9)
    await reconciler.reconcile_once()
    assert len(backend.starts) == 2
    clock.advance(0.2)
    await reconciler.reconcile_once()
    assert len(backend.starts) == 3
    assert (first, "gone") in api.statuses and (second, "gone") in api.statuses
    restarts = reconciler.metrics.registry.get_sample_value(
        "lkap_supervisor_restarts_total", {"connection": "conn-a"}
    )
    assert restarts == 2


async def test_backoff_is_capped_at_five_minutes() -> None:
    reconciler = _reconciler(FakeApi(), FakeBackend(Clock()), Clock(), backoff_base_s=5.0)

    delays = [reconciler._backoff(n) for n in (1, 2, 3, 7, 8, 20)]

    assert delays == [5.0, 10.0, 20.0, 300.0, 300.0, 300.0]


async def test_stop_and_removed_connections_drain_one_replica_at_a_time() -> None:
    clock = Clock()
    backend = FakeBackend(clock)
    stopped = [backend.add("conn-a", i, HASH_A) for i in range(2)]
    removed = [backend.add("conn-gone", i, HASH_A) for i in range(2)]
    api = FakeApi([desired("conn-a", replicas=0, digest=HASH_A)])
    reconciler = _reconciler(api, backend, clock)
    backend.gate = asyncio.Event()

    await reconciler.reconcile_once()
    await _settle()
    assert reconciler.draining_keys == {stopped[1], removed[0]}  # one per pool
    await reconciler.reconcile_once()
    await _settle()
    assert reconciler.draining_keys == {stopped[1], removed[0]}

    backend.gate.set()
    await reconciler.wait_for_drains()
    await reconciler.reconcile_once()
    await reconciler.wait_for_drains()

    assert sorted(backend.drained) == sorted(stopped + removed)
    assert backend.handles == {}
    assert backend.starts == []


async def test_api_outage_touches_nothing() -> None:
    clock = Clock()
    backend = FakeBackend(clock)
    key = backend.add("conn-a", 0, HASH_A)
    api = FakeApi()
    api.fail_desired = True
    reconciler = _reconciler(api, backend, clock)

    assert await reconciler.reconcile_once() is False
    await _settle()

    assert key in backend.handles and backend.drained == [] and backend.starts == []


async def test_worker_env_failure_skips_starts_and_retries_next_pass() -> None:
    clock = Clock()
    api = FakeApi([desired(replicas=1)])
    api.fail_env = True
    backend = FakeBackend(clock)
    reconciler = _reconciler(api, backend, clock)

    await reconciler.reconcile_once()
    assert backend.starts == []
    api.fail_env = False
    await reconciler.reconcile_once()

    assert len(backend.starts) == 1


async def test_shutdown_drains_every_replica_when_the_backend_owns_them() -> None:
    clock = Clock()
    api = FakeApi([desired(replicas=2)])
    backend = FakeBackend(clock)
    reconciler = _reconciler(api, backend, clock)
    await reconciler.reconcile_once()

    await reconciler.shutdown(stop_replicas=True)

    assert backend.handles == {}
    assert len(backend.drained) == 2


async def test_orphaned_draining_replica_is_resumed_not_restarted() -> None:
    clock = Clock()
    backend = FakeBackend(clock)
    key = backend.add("conn-a", 0, HASH_A)
    backend.set_state(key, "draining")  # signalled by a previous supervisor
    api = FakeApi([desired(replicas=0, digest=HASH_A)])
    reconciler = _reconciler(api, backend, clock)

    await reconciler.reconcile_once()
    await reconciler.wait_for_drains()

    assert backend.drained == [key]


async def test_metrics_report_replicas_per_state() -> None:
    clock = Clock()
    api = FakeApi([desired(replicas=2)])
    backend = FakeBackend(clock)
    reconciler = _reconciler(api, backend, clock)

    await reconciler.reconcile_once()

    registry = reconciler.metrics.registry
    labels = {"connection": "conn-a", "state": "running"}
    assert registry.get_sample_value("lkap_supervisor_replicas", labels) == 2
    assert registry.get_sample_value("lkap_supervisor_reconcile_seconds_count") == 1


async def test_secrets_never_reach_the_logs(
    log_capture: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = Clock()
    api = FakeApi([desired(replicas=2), desired("conn-b", replicas=1, digest=HASH_B)])
    backend = FakeBackend(clock)
    reconciler = _reconciler(api, backend, clock)

    await reconciler.reconcile_once()  # starts with env
    backend.fail_start = True
    backend.set_state(next(iter(backend.handles)), "failed")
    clock.advance(10)
    await reconciler.reconcile_once()  # failure + failed start paths
    api.rows = []
    await reconciler.reconcile_once()
    await reconciler.wait_for_drains()  # drain paths

    text = all_log_text(log_capture, capsys)
    assert "replica_started" in text and "replica_failed" in text and "replica_draining" in text
    assert SECRET not in text
    assert SERVICE_TOKEN not in text
    assert "APIkey1234" not in text
