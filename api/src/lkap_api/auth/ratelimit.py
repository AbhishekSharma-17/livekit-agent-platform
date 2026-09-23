"""Token-bucket rate limiting (CONTRACTS-V2 §3.3, ARCHITECTURE-V2 D-V2-18).

Two interchangeable backends behind :class:`RateLimiter`:

* :class:`InMemoryRateLimiter` — per process; the default and the dev/test path.
* :class:`RedisRateLimiter` — shared by every api replica when
  ``LKAP_REDIS_URL`` is set. The bucket update is one Lua script, so it is
  atomic across replicas and uses the Redis server clock.

A bucket of ``capacity`` tokens refills continuously at ``capacity`` per
``per_seconds``: with the ``rate_per_ip_per_min=6`` default, six calls pass
and the seventh inside the same minute is refused.

The limiter instance lives on ``app.state`` (one per application), created on
first use by :func:`get_rate_limiter`.
"""

from __future__ import annotations

import importlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, Protocol

from fastapi import Depends, Request

from lkap_api.errors import ApiError
from lkap_api.logging import get_logger
from lkap_api.settings import Settings, get_settings

log = get_logger(__name__)

_STATE_ATTR = "rate_limiter"
_PRUNE_ABOVE = 10_000


class RateLimitedError(ApiError):
    """429 — a rate limit bucket is empty."""

    status_code = 429
    code = "rate_limited"


class AgentBusyError(ApiError):
    """429 — the agent already runs ``max_concurrent_sessions`` sessions."""

    status_code = 429
    code = "agent_busy"


@dataclass(frozen=True)
class Decision:
    """The outcome of one bucket hit."""

    allowed: bool
    retry_after_s: float = 0.0


class RateLimiter(Protocol):
    """Consume one token from a named bucket."""

    async def hit(self, key: str, *, capacity: int, per_seconds: float = 60.0) -> Decision:
        """Take one token from ``key``'s bucket.

        Args:
            key: Bucket name, e.g. ``connect:ip:203.0.113.9``.
            capacity: Bucket size (and tokens refilled per ``per_seconds``).
            per_seconds: Refill window.

        Returns:
            Whether the call is allowed and, if not, when to retry.
        """
        ...


class InMemoryRateLimiter:
    """A process-local token bucket map with an injectable clock."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        """Create an empty limiter.

        Args:
            clock: Monotonic seconds; tests pass a fake.
        """
        self._clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}

    async def hit(self, key: str, *, capacity: int, per_seconds: float = 60.0) -> Decision:
        """Take one token from ``key``'s bucket (see :class:`RateLimiter`)."""
        if capacity <= 0:
            return Decision(False, per_seconds)
        now = self._clock()
        rate = capacity / per_seconds
        tokens, updated = self._buckets.get(key, (float(capacity), now))
        tokens = min(float(capacity), tokens + (now - updated) * rate)
        if tokens >= 1.0:
            self._buckets[key] = (tokens - 1.0, now)
            self._prune(now)
            return Decision(True)
        self._buckets[key] = (tokens, now)
        return Decision(False, (1.0 - tokens) / rate)

    def _prune(self, now: float) -> None:
        if len(self._buckets) <= _PRUNE_ABOVE:
            return
        # Buckets untouched for an hour are full again; forgetting them is lossless
        # for every capacity/window the api uses (windows are at most a minute).
        stale = [key for key, (_, updated) in self._buckets.items() if now - updated > 3600]
        for key in stale:
            del self._buckets[key]


#: Atomic token bucket; returns ``{allowed, retry_after_s}`` (as strings: Lua
#: truncates numbers returned to Redis to integers).
TOKEN_BUCKET_LUA = """
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
local data = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(data[1])
local ts = tonumber(data[2])
if tokens == nil or ts == nil then
  tokens = capacity
  ts = now
end
tokens = math.min(capacity, tokens + math.max(0, now - ts) * rate)
local allowed = 0
local retry = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
else
  retry = (1 - tokens) / rate
end
redis.call('HSET', KEYS[1], 'tokens', tostring(tokens), 'ts', tostring(now))
redis.call('EXPIRE', KEYS[1], math.ceil(capacity / rate) + 1)
return {tostring(allowed), tostring(retry)}
"""


class RedisRateLimiter:
    """Token buckets shared across api replicas through Redis."""

    def __init__(self, client: Any, *, namespace: str = "lkap:rl:") -> None:
        """Wrap an async Redis client.

        Args:
            client: A ``redis.asyncio.Redis`` (or compatible) instance exposing
                ``eval(script, numkeys, *keys_and_args)``.
            namespace: Key prefix.
        """
        self._client = client
        self._namespace = namespace

    async def hit(self, key: str, *, capacity: int, per_seconds: float = 60.0) -> Decision:
        """Take one token from ``key``'s bucket (see :class:`RateLimiter`)."""
        if capacity <= 0:
            return Decision(False, per_seconds)
        rate = capacity / per_seconds
        raw = await self._client.eval(TOKEN_BUCKET_LUA, 1, self._namespace + key, capacity, rate)
        allowed, retry = (_as_text(part) for part in raw)
        return Decision(allowed == "1", float(retry))


def _as_text(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def build_rate_limiter(settings: Settings) -> RateLimiter:
    """Return the Redis limiter when ``LKAP_REDIS_URL`` is usable, else in-memory.

    A configured url with no ``redis`` package installed logs a warning and
    falls back to in-memory buckets (per replica) rather than failing requests.
    """
    if settings.redis_url:
        try:
            redis_asyncio = importlib.import_module("redis.asyncio")
        except ImportError:
            log.warning("rate_limiter_redis_unavailable", reason="redis package not installed")
        else:
            client = redis_asyncio.from_url(settings.redis_url)
            return RedisRateLimiter(client)
    return InMemoryRateLimiter()


def get_rate_limiter(request: Request, settings: Annotated[Settings, Depends(get_settings)]) -> RateLimiter:
    """FastAPI dependency: the application's limiter, created on first use."""
    limiter: RateLimiter | None = getattr(request.app.state, _STATE_ATTR, None)
    if limiter is None:
        limiter = build_rate_limiter(settings)
        setattr(request.app.state, _STATE_ATTR, limiter)
    return limiter


RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]


async def enforce(
    limiter: RateLimiter, key: str, *, capacity: int, per_seconds: float = 60.0, what: str
) -> None:
    """Take a token or raise :class:`RateLimitedError`.

    Args:
        limiter: The application's limiter.
        key: Bucket name.
        capacity: Bucket size.
        per_seconds: Refill window.
        what: Human-readable name of the limit, for the error message.

    Raises:
        RateLimitedError: When the bucket is empty.
    """
    decision = await limiter.hit(key, capacity=capacity, per_seconds=per_seconds)
    if not decision.allowed:
        raise RateLimitedError(
            f"rate limit exceeded: {what}",
            details={"limit": what, "retry_after_s": round(decision.retry_after_s, 1)},
        )
