"""Single-instance guard (CONTRACTS-V2 §5).

With ``LKAP_REDIS_URL`` set the supervisor holds the Redis lease
``lkap:supervisor:lease`` (``SET NX PX``, renewed every pass, released on
exit); otherwise an exclusive ``flock`` on ``<state_dir>/supervisor.lock``
(dev, single host). A supervisor that does not hold the lease stays on
standby and does not touch any replica.
"""

from __future__ import annotations

import fcntl
import os
import uuid
from pathlib import Path
from typing import IO, Any, Protocol

from lkap_supervisor.logging import get_logger

log = get_logger(__name__)

LEASE_KEY = "lkap:supervisor:lease"

_RENEW = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('pexpire', KEYS[1], ARGV[2])
end
return 0
"""

_RELEASE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""


class Lease(Protocol):
    """Who may reconcile."""

    async def acquire(self) -> bool:
        """Try once, without blocking."""
        ...

    async def renew(self) -> bool:
        """Extend a held lease; False if it was lost."""
        ...

    async def release(self) -> None:
        """Give the lease up (no-op if not held)."""
        ...


class FileLease:
    """An exclusive, non-blocking ``flock`` held for the life of the process."""

    def __init__(self, path: Path) -> None:
        """Bind to the lock file ``path``."""
        self.path = path
        self._file: IO[str] | None = None

    async def acquire(self) -> bool:
        """Take the lock if nobody holds it."""
        if self._file is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        self._file = handle
        return True

    async def renew(self) -> bool:
        """A held flock cannot be lost."""
        return self._file is not None

    async def release(self) -> None:
        """Unlock and close."""
        if self._file is not None:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
            self._file.close()
            self._file = None


class RedisLease:
    """A token-guarded Redis key with a TTL, renewed each pass."""

    def __init__(self, client: Any, *, ttl_s: float, key: str = LEASE_KEY) -> None:
        """Bind to a ``redis.asyncio`` client.

        Args:
            client: ``redis.asyncio.Redis`` (or fakeredis in tests).
            ttl_s: Lease lifetime; must exceed the reconcile interval.
            key: The lease key.
        """
        self._redis = client
        self._ttl_ms = int(ttl_s * 1000)
        self._key = key
        self._token = uuid.uuid4().hex
        self._held = False

    async def acquire(self) -> bool:
        """``SET key token NX PX ttl``."""
        if self._held:
            return await self.renew()
        self._held = bool(await self._redis.set(self._key, self._token, nx=True, px=self._ttl_ms))
        return self._held

    async def renew(self) -> bool:
        """Extend the TTL if the key still carries our token."""
        if not self._held:
            return False
        self._held = bool(await self._redis.eval(_RENEW, 1, self._key, self._token, self._ttl_ms))
        if not self._held:
            log.error("supervisor_lease_lost", key=self._key)
        return self._held

    async def release(self) -> None:
        """Delete the key if it still carries our token."""
        if self._held:
            await self._redis.eval(_RELEASE, 1, self._key, self._token)
            self._held = False
