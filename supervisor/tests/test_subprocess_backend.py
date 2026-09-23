"""``SubprocessBackend`` with a stub ``python -m lkap_agent.main`` (no LiveKit, no real worker)."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from lkap_contracts.fleet import ReplicaHandle, WorkerEnv

from conftest import Reaper
from fakes import desired
from lkap_supervisor.backends.subprocess import STATE_FILE, SubprocessBackend

STUB_MAIN = """
import json, os, signal, sys, time
from pathlib import Path

out = Path(os.environ["STUB_OUT"])

def write(event):
    with out.open("a") as f:
        f.write(json.dumps(event) + "\\n")

keys = ("LIVEKIT_URL", "LIVEKIT_API_SECRET", "LKAP_CONNECTION_ID", "LKAP_MANAGED_BY", "LEAK_ME")
write({
    "event": "started",
    "pid": os.getpid(),
    "argv": sys.argv[1:],
    "env": {k: os.environ.get(k) for k in keys},
    "has_path": "PATH" in os.environ,
})
mode = os.environ.get("STUB_MODE", "graceful")
if mode == "crash":
    sys.exit(3)

def on_int(signum, frame):
    write({"event": "sigint"})
    if mode == "graceful":
        sys.exit(0)

signal.signal(signal.SIGINT, on_int)
signal.signal(signal.SIGTERM, lambda *a: write({"event": "sigterm"}))
while True:
    time.sleep(0.02)
"""


@pytest.fixture
def agent_dir(tmp_path: Path) -> Path:
    pkg = tmp_path / "agent" / "lkap_agent"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "main.py").write_text(STUB_MAIN)
    return tmp_path / "agent"


def _backend(agent_dir: Path, state_dir: Path) -> SubprocessBackend:
    return SubprocessBackend(
        agent_dir=agent_dir,
        python=sys.executable,
        state_dir=state_dir,
        parent_env={"PATH": os.environ.get("PATH", ""), "LEAK_ME": "parent-only", "HOME": "/tmp"},
        poll_s=0.05,
    )


def _env(out: Path, mode: str) -> WorkerEnv:
    return WorkerEnv(
        env={
            "LIVEKIT_URL": "wss://stub.invalid",
            "LIVEKIT_API_SECRET": "stub-secret",
            "LKAP_CONNECTION_ID": "conn-a",
            "STUB_OUT": str(out),
            "STUB_MODE": mode,
        }
    )


def _events(out: Path) -> list[dict[str, Any]]:
    if not out.exists():
        return []
    return [json.loads(line) for line in out.read_text().splitlines() if line.strip()]


async def _wait_for(out: Path, event: str, timeout_s: float = 10.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for item in _events(out):
            if item["event"] == event:
                return item
        await asyncio.sleep(0.02)
    raise AssertionError(f"stub never reported {event!r}: {_events(out)}")


async def _start(
    backend: SubprocessBackend, reaper: Reaper, out: Path, mode: str
) -> tuple[ReplicaHandle, dict[str, Any]]:
    handle = await backend.start(desired(replicas=1), 0, _env(out, mode))
    reaper.track(int(handle.pid_or_container))
    started = await _wait_for(out, "started")
    return handle, started


async def test_child_gets_the_worker_env_and_only_allowlisted_parent_env(
    agent_dir: Path, tmp_path: Path, reaper: Reaper
) -> None:
    backend = _backend(agent_dir, tmp_path / "state")
    out = tmp_path / "events.jsonl"

    handle, started = await _start(backend, reaper, out, "graceful")

    assert started["argv"] == ["start"]
    assert started["env"]["LIVEKIT_URL"] == "wss://stub.invalid"
    assert started["env"]["LKAP_CONNECTION_ID"] == "conn-a"
    assert started["env"]["LKAP_MANAGED_BY"] == "supervisor"
    assert started["env"]["LEAK_ME"] is None
    assert started["has_path"] is True
    assert handle.instance_key == f"{socket.gethostname()}:{started['pid']}"
    assert handle.pid_or_container == str(started["pid"])
    assert [h.state for h in await backend.list()] == ["running"]
    assert await backend.health(handle) is True
    state = (tmp_path / "state" / STATE_FILE).read_text()
    assert "stub-secret" not in state and "wss://stub.invalid" not in state
    await backend.drain(handle, grace_s=5.0)


async def test_drain_sends_sigint_and_a_cooperative_worker_exits_without_sigkill(
    agent_dir: Path, tmp_path: Path, reaper: Reaper
) -> None:
    backend = _backend(agent_dir, tmp_path / "state")
    out = tmp_path / "events.jsonl"
    handle, _ = await _start(backend, reaper, out, "graceful")

    begun = time.monotonic()
    await backend.drain(handle, grace_s=5.0)

    assert time.monotonic() - begun < 4.0
    assert [e["event"] for e in _events(out)] == ["started", "sigint"]
    assert await backend.list() == []


async def test_drain_sigkills_a_worker_that_ignores_sigint_only_after_the_grace(
    agent_dir: Path, tmp_path: Path, reaper: Reaper
) -> None:
    backend = _backend(agent_dir, tmp_path / "state")
    out = tmp_path / "events.jsonl"
    handle, started = await _start(backend, reaper, out, "stubborn")

    begun = time.monotonic()
    await backend.drain(handle, grace_s=0.6)
    elapsed = time.monotonic() - begun

    assert elapsed >= 0.6
    assert [e["event"] for e in _events(out)] == ["started", "sigint"]  # no SIGTERM, one SIGINT
    with pytest.raises(ProcessLookupError):
        os.kill(started["pid"], 0)


async def test_an_exited_replica_is_failed_and_can_be_removed(
    agent_dir: Path, tmp_path: Path, reaper: Reaper
) -> None:
    backend = _backend(agent_dir, tmp_path / "state")
    out = tmp_path / "events.jsonl"
    handle, _ = await _start(backend, reaper, out, "crash")

    for _ in range(100):
        if [h.state for h in await backend.list()] == ["failed"]:
            break
        await asyncio.sleep(0.02)

    assert [h.state for h in await backend.list()] == ["failed"]
    assert await backend.health(handle) is False
    await backend.remove(handle)
    assert await backend.list() == []


async def test_a_restarted_supervisor_adopts_live_children_and_never_signals_twice(
    agent_dir: Path, tmp_path: Path, reaper: Reaper
) -> None:
    state_dir = tmp_path / "state"
    first = _backend(agent_dir, state_dir)
    out = tmp_path / "events.jsonl"
    handle, started = await _start(first, reaper, out, "stubborn")
    interrupted = asyncio.create_task(first.drain(handle, grace_s=60.0))
    await _wait_for(out, "sigint")
    interrupted.cancel()  # the supervisor "crashes" mid-drain
    with pytest.raises(asyncio.CancelledError):
        await interrupted

    second = _backend(agent_dir, state_dir)
    adopted = await second.list()
    assert [(h.instance_key, h.state) for h in adopted] == [(handle.instance_key, "draining")]
    await second.drain(adopted[0], grace_s=0.5)

    assert [e["event"] for e in _events(out)] == ["started", "sigint"]
    with pytest.raises(ProcessLookupError):
        os.kill(started["pid"], 0)
    assert await second.list() == []


async def test_dead_or_foreign_pids_in_the_state_file_are_not_adopted(
    agent_dir: Path, tmp_path: Path
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    handle = ReplicaHandle(connection_id="c", replica_index=0, instance_key="h:1", desired_hash="x")
    entries = [
        {"handle": handle.model_dump(mode="json"), "pid": 2**22 + 12345, "draining": False},
        {"handle": handle.model_dump(mode="json"), "pid": os.getpid(), "draining": False},  # pytest ≠ worker
    ]
    (state_dir / STATE_FILE).write_text(json.dumps({"replicas": entries}))

    assert await _backend(agent_dir, state_dir).list() == []


async def test_spawn_failure_is_a_backend_error(tmp_path: Path) -> None:
    from lkap_supervisor.backends.base import BackendError

    backend = SubprocessBackend(
        agent_dir=tmp_path, python=str(tmp_path / "no-python"), state_dir=tmp_path / "state"
    )

    with pytest.raises(BackendError, match="cannot spawn"):
        await backend.start(desired(replicas=1), 0, _env(tmp_path / "o", "graceful"))
