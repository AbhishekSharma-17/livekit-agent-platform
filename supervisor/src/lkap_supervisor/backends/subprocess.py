"""``SubprocessBackend``: one ``python -m lkap_agent.main start`` child per replica (dev).

* The child runs with ``cwd`` = the agent directory and the agent's interpreter
  (``agent/.venv/bin/python`` by default), exec'd directly — no ``uv run``
  wrapper — so its pid is the worker's pid and ``hostname:pid`` is the
  ``instance_key`` both the supervisor and the worker compute.
* The environment is an allowlist of the supervisor's own (``PATH``, ``HOME``,
  locale, TLS bundles, plus ``LKAP_SUPERVISOR_PASSTHROUGH_ENV``) overlaid with
  the connection's ``WorkerEnv``. Nothing else leaks in — in particular no
  ``LIVEKIT_*`` of another connection from the supervisor's shell.
* Each child gets its own session (``start_new_session``), so a Ctrl+C on the
  supervisor's terminal does not reach the workers uncontrolled; the
  supervisor drains them itself on shutdown.
* The replica table (pids and handles, never env) is persisted to
  ``<state_dir>/subprocess-replicas.json``. A restarted supervisor adopts the
  children that are still alive and still ``lkap_agent.main`` processes, so a
  supervisor crash never leads to a second pool under the same agent name.
"""

from __future__ import annotations

import asyncio
import builtins
import os
import signal
import socket
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from lkap_contracts.fleet import FleetDesired, ReplicaHandle, ReplicaState, WorkerEnv
from pydantic import ValidationError

from lkap_supervisor.backends.base import BackendError, JsonState, worker_environment
from lkap_supervisor.logging import get_logger

log = get_logger(__name__)

#: Read by `lkap_agent.main.worker_http_port` (the worker HTTP server port).
WORKER_HTTP_PORT_ENV = "LKAP_WORKER_HTTP_PORT"

#: Parent environment variables a worker may inherit (everything else is dropped).
BASE_ENV_KEYS: tuple[str, ...] = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "TMPDIR",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "HF_HOME",
    "HF_HUB_OFFLINE",
    "XDG_CACHE_HOME",
)

STATE_FILE = "subprocess-replicas.json"


@dataclass
class _Replica:
    handle: ReplicaHandle
    pid: int
    process: asyncio.subprocess.Process | None  # None: adopted from a previous supervisor
    draining: bool = False


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _proc_cmdline(pid: int) -> str | None:
    """``pid``'s full argv from ``/proc/<pid>/cmdline`` (Linux), or ``None`` without procfs."""
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    return raw.replace(b"\0", b" ").decode(errors="replace").strip()


def _open_log(path: Path) -> IO[bytes]:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open("ab")


class SubprocessBackend:
    """Replicas as local child processes."""

    name = "subprocess"
    stops_replicas_on_exit = True

    def __init__(
        self,
        *,
        agent_dir: Path,
        python: str,
        state_dir: Path,
        log_dir: Path | None = None,
        passthrough: Sequence[str] = (),
        module: str = "lkap_agent.main",
        mode: str = "start",
        parent_env: Mapping[str, str] | None = None,
        poll_s: float = 0.2,
    ) -> None:
        """Configure the backend.

        Args:
            agent_dir: Working directory of the workers (the repo's ``agent/``).
            python: Interpreter that can import ``module``.
            state_dir: Where the replica table is persisted.
            log_dir: If set, each replica's stdout/stderr go to ``<log_dir>/<conn>-<index>.log``;
                otherwise they are inherited from the supervisor.
            passthrough: Extra parent env var names to hand to workers.
            module: The worker module (``python -m <module> <mode>``).
            mode: ``start`` (production mode: drains on SIGINT).
            parent_env: The environment the allowlist is taken from (default ``os.environ``).
            poll_s: Poll interval while waiting for an adopted process to exit.
        """
        self._agent_dir = agent_dir
        self._python = python
        self._log_dir = log_dir
        self._keys = (*BASE_ENV_KEYS, *passthrough)
        self._module = module
        self._mode = mode
        self._parent_env = parent_env if parent_env is not None else os.environ
        self._poll_s = poll_s
        self._state = JsonState(state_dir / STATE_FILE)
        self._replicas: dict[str, _Replica] = {}
        self._adopted = False

    # ------------------------------------------------------------------ bookkeeping
    def _save(self) -> None:
        self._state.save(
            {
                "replicas": [
                    {"handle": r.handle.model_dump(mode="json"), "pid": r.pid, "draining": r.draining}
                    for r in self._replicas.values()
                ]
            }
        )

    async def _is_our_worker(self, pid: int) -> bool:
        """Whether ``pid`` is alive and still runs ``self._module`` (guards against pid reuse)."""
        if not _pid_alive(pid):
            return False
        command = await asyncio.to_thread(_proc_cmdline, pid)
        source = "proc"
        if command is None:
            # No procfs (macOS). `-ww`: unlimited width, so a long interpreter path can
            # never push `-m lkap_agent.main` past a column limit.
            source = "ps"
            try:
                proc = await asyncio.create_subprocess_exec(
                    "ps",
                    "-ww",
                    "-o",
                    "command=",
                    "-p",
                    str(pid),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                out, _ = await proc.communicate()
            except OSError:
                log.warning("replica_adoption_unverifiable", pid=pid)
                return False
            command = out.decode(errors="replace")
        if self._module in command:
            return True
        # Never log the command itself: a reused pid may belong to any process.
        log.info("replica_adoption_rejected", pid=pid, source=source, command_chars=len(command.strip()))
        return False

    async def _adopt(self) -> None:
        if self._adopted:
            return
        self._adopted = True
        entries = self._state.load().get("replicas")
        for entry in entries if isinstance(entries, list) else []:
            try:
                handle = ReplicaHandle.model_validate(entry["handle"])
                pid = int(entry["pid"])
                draining = bool(entry.get("draining", False))
            except (KeyError, TypeError, ValueError, ValidationError):
                continue
            if await self._is_our_worker(pid):
                self._replicas[handle.instance_key] = _Replica(handle, pid, None, draining)
                log.info(
                    "replica_adopted",
                    connection_id=handle.connection_id,
                    replica_index=handle.replica_index,
                    pid=pid,
                    draining=draining,
                )
        self._save()

    def _alive(self, replica: _Replica) -> bool:
        if replica.process is not None:
            return replica.process.returncode is None
        return _pid_alive(replica.pid)

    def _state_of(self, replica: _Replica) -> ReplicaState:
        alive = self._alive(replica)
        if replica.draining:
            return "draining" if alive else "stopped"
        return "running" if alive else "failed"

    def _base_env(self) -> dict[str, str]:
        return {key: self._parent_env[key] for key in self._keys if key in self._parent_env}

    # ---------------------------------------------------------------------- protocol
    async def list(self) -> builtins.list[ReplicaHandle]:
        """Every known replica with its current state."""
        await self._adopt()
        return [r.handle.model_copy(update={"state": self._state_of(r)}) for r in self._replicas.values()]

    async def start(self, desired: FleetDesired, index: int, env: WorkerEnv) -> ReplicaHandle:
        """Spawn ``python -m lkap_agent.main start`` for replica ``index``.

        Raises:
            BackendError: If the process cannot be spawned.
        """
        await self._adopt()
        log_file: IO[bytes] | None = None
        if self._log_dir is not None:
            log_file = await asyncio.to_thread(
                _open_log, self._log_dir / f"{desired.connection_id}-{index}.log"
            )
        # Every replica shares this host's network: let the SDK's worker HTTP
        # server take an ephemeral port instead of `start` mode's fixed 8081,
        # or a second replica (rolling restart, replicas > 1) dies on bind (V2-20).
        child_env = worker_environment(env, base=self._base_env(), extra={WORKER_HTTP_PORT_ENV: "0"})
        try:
            process = await asyncio.create_subprocess_exec(
                self._python,
                "-m",
                self._module,
                self._mode,
                cwd=str(self._agent_dir),
                env=child_env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=log_file,
                stderr=asyncio.subprocess.STDOUT if log_file is not None else None,
                start_new_session=True,
            )
        except OSError as exc:
            raise BackendError(f"cannot spawn the worker: {type(exc).__name__} ({exc.strerror})") from None
        finally:
            del child_env
            if log_file is not None:
                log_file.close()
        key = f"{socket.gethostname()}:{process.pid}"
        handle = ReplicaHandle(
            connection_id=desired.connection_id,
            replica_index=index,
            instance_key=key,
            desired_hash=desired.desired_hash,
            state="running",
            started_at=time.time(),
            pid_or_container=str(process.pid),
        )
        self._replicas[key] = _Replica(handle, process.pid, process)
        self._save()
        log.info(
            "subprocess_started",
            connection_id=desired.connection_id,
            replica_index=index,
            pid=process.pid,
            instance_key=key,
        )
        return handle

    async def _wait_exit(self, replica: _Replica, timeout_s: float) -> bool:
        if replica.process is not None:
            try:
                await asyncio.wait_for(replica.process.wait(), timeout_s)
            except TimeoutError:
                return False
            return True
        deadline = time.monotonic() + timeout_s
        while _pid_alive(replica.pid):
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(self._poll_s)
        return True

    @staticmethod
    def _kill_group(pid: int) -> None:
        """SIGKILL the worker's process group (its job processes included), never our own."""
        try:
            pgid = os.getpgid(pid)
            if pgid != os.getpgrp():
                os.killpg(pgid, signal.SIGKILL)
            else:
                os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    async def drain(self, handle: ReplicaHandle, grace_s: float) -> None:
        """SIGINT once, wait ``grace_s``, SIGKILL only if still alive; then forget the replica."""
        replica = self._replicas.get(handle.instance_key)
        if replica is None:
            return
        if self._alive(replica):
            if replica.draining:
                log.info("replica_drain_resumed", instance_key=handle.instance_key, pid=replica.pid)
            else:
                replica.draining = True
                self._save()
                try:
                    os.kill(replica.pid, signal.SIGINT)
                except ProcessLookupError:
                    pass
                log.info(
                    "replica_sigint",
                    connection_id=handle.connection_id,
                    replica_index=handle.replica_index,
                    pid=replica.pid,
                    grace_s=grace_s,
                )
            if not await self._wait_exit(replica, grace_s):
                log.warning("replica_sigkill_after_grace", pid=replica.pid, grace_s=grace_s)
                self._kill_group(replica.pid)
                await self._wait_exit(replica, 10.0)
        exit_code = replica.process.returncode if replica.process is not None else None
        self._replicas.pop(handle.instance_key, None)
        self._save()
        log.info("replica_stopped", instance_key=handle.instance_key, pid=replica.pid, exit_code=exit_code)

    async def health(self, handle: ReplicaHandle) -> bool:
        """Alive and not draining."""
        replica = self._replicas.get(handle.instance_key)
        return replica is not None and not replica.draining and self._alive(replica)

    async def remove(self, handle: ReplicaHandle) -> None:
        """Forget an exited replica (a live one is kept: it must be drained, not forgotten)."""
        replica = self._replicas.get(handle.instance_key)
        if replica is None:
            return
        if self._alive(replica):
            log.warning("replica_remove_refused_alive", instance_key=handle.instance_key, pid=replica.pid)
            return
        exit_code = replica.process.returncode if replica.process is not None else None
        self._replicas.pop(handle.instance_key, None)
        self._save()
        log.info("replica_forgotten", instance_key=handle.instance_key, exit_code=exit_code)

    async def aclose(self) -> None:
        """Nothing to release; replicas are drained by the supervisor, not here."""
