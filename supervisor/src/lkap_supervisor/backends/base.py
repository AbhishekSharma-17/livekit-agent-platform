"""The backend protocol (CONTRACTS-V2 §5) and helpers shared by the backends.

A backend owns replica processes/containers and reports them as
:class:`~lkap_contracts.fleet.ReplicaHandle` s. The four contract methods are
``list``, ``start``, ``drain`` and ``health``; this package adds ``remove``
(forget a replica that exited) and ``aclose``, plus the
``stops_replicas_on_exit`` flag that tells the supervisor whether its own
shutdown must drain the pool (subprocess children) or leave it running
(containers, which a restarted supervisor rediscovers by label).

Drain rule (D-W2-13, binding): SIGINT, wait ``grace_s``, and SIGKILL only if
the worker is still alive. Never SIGKILL first, never a plain SIGTERM, and
never a second SIGINT (the SDK ``os._exit(1)`` s on the second signal, which
would abandon live calls).
"""

from __future__ import annotations

import builtins
import json
import os
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Protocol

from lkap_contracts.fleet import FleetDesired, ReplicaHandle, WorkerEnv

#: Env var every supervised worker receives, so it registers as ``managed_by=supervisor``.
MANAGED_BY_ENV = "LKAP_MANAGED_BY"

#: Env var carrying the instance key a worker should register under (docker backend).
INSTANCE_KEY_ENV = "LKAP_INSTANCE_KEY"


class BackendError(Exception):
    """A backend could not perform an operation; the message never contains env values."""


class Backend(Protocol):
    """Runs and stops pool replicas."""

    name: str
    stops_replicas_on_exit: bool

    async def list(self) -> builtins.list[ReplicaHandle]:
        """Every replica this backend is responsible for, with its current state."""
        ...

    async def start(self, desired: FleetDesired, index: int, env: WorkerEnv) -> ReplicaHandle:
        """Start replica ``index`` of a pool with the given (secret-bearing) environment."""
        ...

    async def drain(self, handle: ReplicaHandle, grace_s: float) -> None:
        """SIGINT, wait up to ``grace_s``, then SIGKILL if still alive; forget the replica."""
        ...

    async def health(self, handle: ReplicaHandle) -> bool:
        """Whether the replica is alive and not draining."""
        ...

    async def remove(self, handle: ReplicaHandle) -> None:
        """Forget a replica that has exited (``failed`` / ``stopped``)."""
        ...

    async def aclose(self) -> None:
        """Release backend resources (does not stop replicas)."""
        ...


def worker_environment(
    env: WorkerEnv, *, base: Mapping[str, str] | None = None, extra: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Merge a process environment: ``base`` < ``env.env`` < ``extra``, plus ``LKAP_MANAGED_BY``.

    The result contains secrets; hand it to the child and drop it.
    """
    merged = dict(base or {})
    merged.update(env.env)
    merged.update(extra or {})
    merged[MANAGED_BY_ENV] = "supervisor"
    return merged


class JsonState:
    """A tiny JSON file for backend bookkeeping that must survive a supervisor restart.

    Holds only ids, pids and flags — never an environment value.
    """

    def __init__(self, path: Path) -> None:
        """Bind to ``path`` (its directory is created on first write)."""
        self.path = path

    def load(self) -> dict[str, object]:
        """Return the stored object, or ``{}`` if missing or unreadable."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def save(self, data: Mapping[str, object]) -> None:
        """Atomically replace the stored object."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)


def as_str_list(value: object) -> list[str]:
    """Coerce a JSON value to a list of strings (anything else → empty)."""
    if isinstance(value, Iterable) and not isinstance(value, str | bytes | Mapping):
        return [str(item) for item in value]
    return []
