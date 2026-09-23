"""Unit tests for the V2-02 building blocks: buckets, settings, invites, guard opt-out, CLI."""

from __future__ import annotations

import datetime as dt
import os
from typing import Any

import pytest
from auth_helpers import make_user
from conftest import REQUIRED_ENV
from pydantic import ValidationError
from sqlalchemy import select

from lkap_api.auth import invites
from lkap_api.auth.__main__ import set_password
from lkap_api.auth.passwords import verify_password
from lkap_api.auth.ratelimit import (
    TOKEN_BUCKET_LUA,
    InMemoryRateLimiter,
    RedisRateLimiter,
    build_rate_limiter,
)
from lkap_api.db.constants import DEFAULT_WORKSPACE_ID
from lkap_api.db.guard import CROSS_WORKSPACE_OPTION, UnscopedTenantQuery, tenant_scope_guard
from lkap_api.db.models import ApiKey, User
from lkap_api.db.session import Database
from lkap_api.settings import Settings, weak_secret_problem

STRONG = "x" * 40


# ------------------------------------------------------------------ rate limit
class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def test_in_memory_bucket_allows_capacity_then_refuses() -> None:
    limiter = InMemoryRateLimiter(clock=FakeClock())

    decisions = [await limiter.hit("k", capacity=6) for _ in range(7)]

    assert [d.allowed for d in decisions] == [True] * 6 + [False]
    assert decisions[-1].retry_after_s == pytest.approx(10.0)


async def test_in_memory_bucket_refills_over_time_and_keys_are_independent() -> None:
    clock = FakeClock()
    limiter = InMemoryRateLimiter(clock=clock)
    for _ in range(6):
        await limiter.hit("a", capacity=6)

    blocked = await limiter.hit("a", capacity=6)
    other = await limiter.hit("b", capacity=6)
    clock.now += 10.0
    refilled = await limiter.hit("a", capacity=6)

    assert (blocked.allowed, other.allowed, refilled.allowed) == (False, True, True)


async def test_in_memory_bucket_with_zero_capacity_refuses() -> None:
    assert (await InMemoryRateLimiter().hit("k", capacity=0)).allowed is False


class FakeRedis:
    """Records the script call and answers like Redis (bytes)."""

    def __init__(self, reply: list[bytes]) -> None:
        self.reply = reply
        self.calls: list[tuple[Any, ...]] = []

    async def eval(self, script: str, numkeys: int, *args: Any) -> list[bytes]:
        self.calls.append((script, numkeys, *args))
        return self.reply


async def test_redis_limiter_runs_the_bucket_script_and_parses_the_reply() -> None:
    fake = FakeRedis([b"0", b"7.5"])
    limiter = RedisRateLimiter(fake)

    decision = await limiter.hit("connect:ip:1.2.3.4", capacity=6)

    assert (decision.allowed, decision.retry_after_s) == (False, 7.5)
    assert fake.calls == [(TOKEN_BUCKET_LUA, 1, "lkap:rl:connect:ip:1.2.3.4", 6, 0.1)]


def test_build_rate_limiter_is_in_memory_without_redis_url(settings: Settings) -> None:
    assert isinstance(build_rate_limiter(settings), InMemoryRateLimiter)


@pytest.mark.skipif(not os.environ.get("LKAP_TEST_REDIS_URL"), reason="set LKAP_TEST_REDIS_URL to run")
async def test_redis_limiter_against_a_real_server() -> None:
    redis_asyncio = pytest.importorskip("redis.asyncio")
    client = redis_asyncio.from_url(os.environ["LKAP_TEST_REDIS_URL"])
    key = f"test-{dt.datetime.now(dt.UTC).timestamp()}"
    limiter = RedisRateLimiter(client)
    try:
        decisions = [await limiter.hit(key, capacity=6) for _ in range(7)]
    finally:
        await client.aclose()

    assert [d.allowed for d in decisions] == [True] * 6 + [False]


# -------------------------------------------------------------------- settings
@pytest.mark.parametrize(
    ("value", "problem"),
    [
        ("", "is not set"),
        ("dev-service", "is a dev-* placeholder"),
        ("DEV_" + "x" * 40, "is a dev-* placeholder"),
        ("short-but-random", "is shorter than 32 characters"),
        (STRONG, None),
    ],
)
def test_weak_secret_problem(value: str, problem: str | None) -> None:
    assert weak_secret_problem(value) == problem


def _prod_env(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    env = {
        "LKAP_ENV": "prod",
        "LKAP_SERVICE_TOKEN": STRONG,
        "LKAP_ADMIN_TOKEN": STRONG,
        "LKAP_SESSION_SECRET": STRONG,
        **overrides,
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)


def test_prod_refuses_the_dev_service_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _prod_env(monkeypatch, LKAP_SERVICE_TOKEN="dev-service")

    with pytest.raises(ValidationError, match="LKAP_SERVICE_TOKEN: is a dev-\\* placeholder"):
        Settings()


def test_prod_requires_a_session_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    _prod_env(monkeypatch)
    monkeypatch.delenv("LKAP_SESSION_SECRET")

    with pytest.raises(ValidationError, match="LKAP_SESSION_SECRET: is not set"):
        Settings()


def test_prod_ignores_a_weak_admin_token_when_break_glass_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    _prod_env(monkeypatch, LKAP_ADMIN_TOKEN="dev-admin")

    settings = Settings()

    assert settings.admin_token_allowed is False
    assert settings.cookie_secure is True


def test_prod_checks_the_admin_token_when_break_glass_is_on(monkeypatch: pytest.MonkeyPatch) -> None:
    _prod_env(monkeypatch, LKAP_ADMIN_TOKEN="dev-admin", LKAP_ALLOW_ADMIN_TOKEN="true")

    with pytest.raises(ValidationError, match="LKAP_ADMIN_TOKEN"):
        Settings()


def test_dev_accepts_short_tokens_and_allows_break_glass(settings: Settings) -> None:
    assert settings.env == "dev"
    assert settings.admin_token_allowed is True
    assert settings.cookie_secure is False
    assert settings.session_ttl_hours == 12
    assert settings.rate_limit_enabled is True


def test_web_origins_include_the_web_base_url(settings: Settings) -> None:
    settings.web_base_url = "https://console.example/"

    assert settings.web_origins == ["http://localhost:3000", "https://console.example"]


# --------------------------------------------------------------------- invites
def test_invite_round_trip_and_expiry(settings: Settings) -> None:
    now = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    token, expires_at = invites.issue(
        settings, workspace_id="w", user_id="u", role="builder", fingerprint="fp", now=now
    )

    claims = invites.verify(settings, token, now=now + dt.timedelta(days=1))

    assert (claims.workspace_id, claims.user_id, claims.role, claims.fingerprint) == (
        "w",
        "u",
        "builder",
        "fp",
    )
    assert expires_at == now + invites.INVITE_TTL
    with pytest.raises(invites.InvalidInvite, match="expired"):
        invites.verify(settings, token, now=now + dt.timedelta(days=8))


@pytest.mark.parametrize("token", ["", "garbage", "a.b", "e30.AAAA"])
def test_invite_malformed_tokens_are_rejected(settings: Settings, token: str) -> None:
    with pytest.raises(invites.InvalidInvite):
        invites.verify(settings, token)


def test_invite_fingerprint_changes_once_accepted() -> None:
    before = invites.state_fingerprint(None, False)

    assert invites.state_fingerprint("$argon2id$...", True) != before
    assert invites.state_fingerprint(None, True) != before


# ---------------------------------------------------------------- guard opt-out
async def test_guard_honours_the_cross_workspace_option(database: Database) -> None:
    with tenant_scope_guard():
        async with database.session() as session:
            marked = select(ApiKey).execution_options(**{CROSS_WORKSPACE_OPTION: True})
            assert (await session.execute(marked)).scalars().all() == []
            with pytest.raises(UnscopedTenantQuery):
                await session.execute(select(ApiKey))


# ------------------------------------------------------------------------- CLI
async def test_set_password_fills_a_null_hash_from_the_bootstrap_password(
    settings: Settings, database: Database
) -> None:
    await make_user(database, "nohash@example.com", password=None, workspace_id=DEFAULT_WORKSPACE_ID)
    settings.bootstrap_owner_password = "from the environment"

    first = await set_password(settings, "NOHASH@example.com")
    settings.bootstrap_owner_password = "a different password"
    second = await set_password(settings, "nohash@example.com")

    async with database.session() as session:
        user = (await session.execute(select(User).where(User.email == "nohash@example.com"))).scalar_one()
    assert (first, second) == (0, 0)
    assert verify_password(user.password_hash, "from the environment")  # second run left it alone


async def test_set_password_force_replaces_and_unknown_user_fails(
    settings: Settings, database: Database
) -> None:
    await make_user(database, "force@example.com")
    settings.bootstrap_owner_password = "replacement password"

    forced = await set_password(settings, "force@example.com", force=True)
    unknown = await set_password(settings, "ghost@example.com")

    async with database.session() as session:
        user = (await session.execute(select(User).where(User.email == "force@example.com"))).scalar_one()
    assert (forced, unknown) == (0, 1)
    assert verify_password(user.password_hash, "replacement password")
