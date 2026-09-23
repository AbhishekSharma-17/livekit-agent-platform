"""``python -m lkap_supervisor``: ``--once`` exit codes, lease, config errors."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from fakes import Clock, FakeApi, FakeBackend, desired
from lkap_supervisor.__main__ import (
    EXIT_CONFIG,
    EXIT_DESIRED_UNAVAILABLE,
    EXIT_LEASE_HELD,
    EXIT_OK,
    main,
    run,
)
from lkap_supervisor.lease import FileLease
from lkap_supervisor.settings import SupervisorSettings


def _settings(tmp_path: Path, **overrides: object) -> SupervisorSettings:
    values: dict[str, object] = {
        "LKAP_SERVICE_TOKEN": "svc-token-0123456789",
        "LKAP_API_BASE_URL": "http://api.test",
        "LKAP_SUPERVISOR_STATE_DIR": str(tmp_path / "state"),
        "LKAP_SUPERVISOR_AGENT_DIR": str(tmp_path),
        "LKAP_SUPERVISOR_METRICS_PORT": "0",
    }
    values.update(overrides)
    return SupervisorSettings.model_validate(values)


async def test_once_runs_one_pass_and_exits_zero(tmp_path: Path) -> None:
    clock = Clock()
    api = FakeApi([desired(replicas=2)])
    backend = FakeBackend(clock)

    code = await run(_settings(tmp_path), once=True, api=api, backend=backend)

    assert code == EXIT_OK
    assert len(backend.starts) == 2
    assert len(backend.handles) == 2  # --once leaves the pool running


async def test_once_exits_one_when_the_desired_state_is_unavailable(tmp_path: Path) -> None:
    api = FakeApi()
    api.fail_desired = True

    code = await run(_settings(tmp_path), once=True, api=api, backend=FakeBackend(Clock()))

    assert code == EXIT_DESIRED_UNAVAILABLE


async def test_once_refuses_to_act_when_another_supervisor_holds_the_lease(tmp_path: Path) -> None:
    holder = FileLease(tmp_path / "state" / "supervisor.lock")
    assert await holder.acquire()
    backend = FakeBackend(Clock())

    code = await run(_settings(tmp_path), once=True, api=FakeApi([desired()]), backend=backend)

    await holder.release()
    assert code == EXIT_LEASE_HELD
    assert backend.starts == []


def test_cli_once_against_a_mocked_api_exits_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in {
        "LKAP_SERVICE_TOKEN": "svc-token-0123456789",
        "LKAP_API_BASE_URL": "http://api.test",
        "LKAP_SUPERVISOR_STATE_DIR": str(tmp_path / "state"),
        "LKAP_SUPERVISOR_AGENT_DIR": str(tmp_path),
        "LKAP_SUPERVISOR_BACKEND": "subprocess",
    }.items():
        monkeypatch.setenv(key, value)

    with respx.mock:
        route = respx.get("http://api.test/internal/v1/fleet/desired").mock(
            return_value=httpx.Response(200, json=[])
        )
        code = main(["--once"])

    assert code == EXIT_OK
    assert route.called


def test_cli_config_error_exits_two_without_echoing_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("LKAP_SERVICE_TOKEN", raising=False)
    monkeypatch.setenv("LKAP_SUPERVISOR_INTERVAL_S", "not-a-number-SECRETISH")

    code = main(["--once"])

    assert code == EXIT_CONFIG
    captured = capsys.readouterr()
    assert "SECRETISH" not in captured.out + captured.err
    assert "LKAP_SERVICE_TOKEN" in captured.out + captured.err


def test_drain_grace_below_fifteen_seconds_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="LKAP_SUPERVISOR_DRAIN_S"):
        _settings(tmp_path, LKAP_SUPERVISOR_DRAIN_S="5")


async def test_run_shutdown_drains_subprocess_style_backends(tmp_path: Path) -> None:
    import asyncio

    clock = Clock()
    backend = FakeBackend(clock)
    stop = asyncio.Event()
    settings = _settings(tmp_path, LKAP_SUPERVISOR_INTERVAL_S="0.01")

    task = asyncio.create_task(run(settings, api=FakeApi([desired(replicas=2)]), backend=backend, stop=stop))
    for _ in range(200):
        if len(backend.handles) == 2:
            break
        await asyncio.sleep(0.01)
    stop.set()
    code = await task

    assert code == EXIT_OK
    assert backend.handles == {}
    assert len(backend.drained) == 2
