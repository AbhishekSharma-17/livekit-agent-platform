"""Login, cookie sessions, `/me`, password change and invites (V2-02, CONTRACTS-V2 §3.1)."""

from __future__ import annotations

import datetime as dt

import httpx
import pytest
from auth_helpers import (
    PASSWORD,
    client_for,
    cookie_client,
    login,
    make_api_key,
    make_user,
    make_workspace,
)
from fastapi import FastAPI
from sqlalchemy import select

from lkap_api.auth import SESSION_COOKIE
from lkap_api.auth.passwords import hash_password, verify_password
from lkap_api.auth.sessions import hash_token
from lkap_api.db.constants import DEFAULT_WORKSPACE_ID
from lkap_api.db.models import User, UserSession, WorkspaceMember, utcnow
from lkap_api.db.session import Database
from lkap_api.settings import get_settings


# ------------------------------------------------------------------ passwords
def test_hash_password_uses_argon2id_and_verifies() -> None:
    encoded = hash_password("s3cret-password")

    assert encoded.startswith("$argon2id$")
    assert verify_password(encoded, "s3cret-password")
    assert not verify_password(encoded, "wrong-password")


@pytest.mark.parametrize("stored", [None, "", "not-a-hash"])
def test_verify_password_without_a_usable_hash_is_false(stored: str | None) -> None:
    assert verify_password(stored, "anything") is False


# ---------------------------------------------------------------------- login
async def test_login_sets_an_httponly_lax_cookie_and_me_returns_the_user(
    app: FastAPI, database: Database
) -> None:
    await make_user(database, "Ada@Example.com", role="builder", name="Ada")
    async with client_for(app) as client:
        response = await client.post(
            "/v1/auth/login", json={"email": " ada@example.COM ", "password": PASSWORD}
        )
        cookie = response.headers["set-cookie"]
        me = await client.get("/v1/auth/me")

    assert response.status_code == 204
    assert cookie.startswith(f"{SESSION_COOKIE}=")
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Secure" not in cookie  # LKAP_ENV=dev
    assert me.status_code == 200
    body = me.json()
    assert body["user"]["email"] == "ada@example.com"
    assert body["workspaces"] == [
        {"id": DEFAULT_WORKSPACE_ID, "slug": "default", "name": "Default", "role": "builder"}
    ]


async def test_login_stores_only_the_token_hash(app: FastAPI, database: Database) -> None:
    await make_user(database, "hash@example.com")
    async with client_for(app) as client:
        await client.post("/v1/auth/login", json={"email": "hash@example.com", "password": PASSWORD})
        raw = client.cookies[SESSION_COOKIE]

    async with database.session() as session:
        stored = (await session.execute(select(UserSession.token_hash))).scalars().all()
    assert hash_token(raw) in stored
    assert raw not in stored


@pytest.mark.parametrize(
    ("email", "password"),
    [("nobody@example.com", PASSWORD), ("known@example.com", "wrong password")],
)
async def test_login_bad_credentials_get_one_generic_401(
    app: FastAPI, database: Database, client: httpx.AsyncClient, email: str, password: str
) -> None:
    await make_user(database, "known@example.com")

    response = await client.post("/v1/auth/login", json={"email": email, "password": password})

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "invalid email or password"
    assert "set-cookie" not in response.headers


async def test_login_disabled_user_is_refused(
    app: FastAPI, database: Database, client: httpx.AsyncClient
) -> None:
    user_id = await make_user(database, "gone@example.com")
    async with database.session() as session:
        user = await session.get(User, user_id)
        assert user is not None
        user.disabled_at = utcnow()

    response = await client.post("/v1/auth/login", json={"email": "gone@example.com", "password": PASSWORD})

    assert response.status_code == 401


async def test_login_is_rate_limited_per_address(
    app: FastAPI, database: Database, client: httpx.AsyncClient
) -> None:
    await make_user(database, "brute@example.com")
    statuses = [
        (
            await client.post("/v1/auth/login", json={"email": "brute@example.com", "password": "nope"})
        ).status_code
        for _ in range(11)
    ]

    assert statuses[:10] == [401] * 10
    assert statuses[10] == 429


async def test_logout_revokes_the_session_and_clears_the_cookie(app: FastAPI, database: Database) -> None:
    await make_user(database, "bye@example.com")
    client = await login(app, "bye@example.com")
    async with client:
        raw = client.cookies[SESSION_COOKIE]
        response = await client.post("/v1/auth/logout")
    # Replay the old cookie from a fresh client: the row is gone, so it no longer works.
    async with cookie_client(app, raw) as replayer:
        replay = await replayer.get("/v1/auth/me")

    assert response.status_code == 204
    assert f'{SESSION_COOKIE}=""' in response.headers["set-cookie"]
    assert replay.status_code == 401


async def test_expired_session_is_rejected(app: FastAPI, database: Database) -> None:
    await make_user(database, "late@example.com")
    client = await login(app, "late@example.com")
    async with database.session() as session:
        row = (await session.execute(select(UserSession))).scalars().one()
        row.expires_at = utcnow() - dt.timedelta(seconds=1)
    async with client:
        response = await client.get("/v1/auth/me")

    assert response.status_code == 401


async def test_session_close_to_expiry_is_rotated(app: FastAPI, database: Database) -> None:
    await make_user(database, "slide@example.com")
    client = await login(app, "slide@example.com")
    async with database.session() as session:
        row = (await session.execute(select(UserSession))).scalars().one()
        row.expires_at = utcnow() + dt.timedelta(hours=1)  # less than half of 12 h left
    async with client:
        old = client.cookies[SESSION_COOKIE]
        response = await client.get("/v1/auth/me")
        new = client.cookies[SESSION_COOKIE]

    assert response.status_code == 200
    assert new != old
    async with database.session() as session:
        rows = (await session.execute(select(UserSession))).scalars().all()
    assert len(rows) == 2
    grace = min(row.expires_at for row in rows)
    assert grace <= utcnow() + dt.timedelta(seconds=61)


# ------------------------------------------------------------------------- me
async def test_me_unauthenticated_is_401(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/auth/me")

    assert response.status_code == 401


async def test_me_with_the_admin_token_is_a_break_glass_owner_of_every_workspace(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    await make_workspace(database, "second")

    body = (await admin_client.get("/v1/auth/me")).json()

    assert body["user"]["id"] == "break-glass"
    assert body["user"]["is_platform_admin"] is True
    assert {w["slug"] for w in body["workspaces"]} == {"default", "second"}
    assert {w["role"] for w in body["workspaces"]} == {"owner"}


async def test_me_with_an_api_key_is_403(app: FastAPI, database: Database) -> None:
    _, raw = await make_api_key(database, ["*"])
    async with client_for(app, {"Authorization": f"Bearer {raw}"}) as client:
        response = await client.get("/v1/auth/me")

    assert response.status_code == 403


async def test_admin_token_is_refused_when_break_glass_is_off(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, admin_client: httpx.AsyncClient
) -> None:
    monkeypatch.setenv("LKAP_ALLOW_ADMIN_TOKEN", "false")
    get_settings.cache_clear()

    response = await admin_client.get("/v1/agents")

    assert response.status_code == 401


# ------------------------------------------------------------------- password
async def test_change_password_requires_the_current_one(app: FastAPI, database: Database) -> None:
    await make_user(database, "pw@example.com")
    async with await login(app, "pw@example.com") as client:
        response = await client.post("/v1/auth/password", json={"current": "wrong", "new": "a new password"})

    assert response.status_code == 400


async def test_change_password_rejects_a_short_new_password(app: FastAPI, database: Database) -> None:
    await make_user(database, "short@example.com")
    async with await login(app, "short@example.com") as client:
        response = await client.post("/v1/auth/password", json={"current": PASSWORD, "new": "short"})

    assert response.status_code == 422


async def test_change_password_signs_out_other_sessions_and_rotates_this_one(
    app: FastAPI, database: Database
) -> None:
    await make_user(database, "rotate@example.com")
    other = await login(app, "rotate@example.com")
    async with await login(app, "rotate@example.com") as client, other:
        before = client.cookies[SESSION_COOKIE]
        response = await client.post(
            "/v1/auth/password", json={"current": PASSWORD, "new": "an entirely new password"}
        )
        after = client.cookies[SESSION_COOKIE]
        me_here = await client.get("/v1/auth/me")
        me_other = await other.get("/v1/auth/me")
    async with cookie_client(app, before) as replayer:
        old_cookie = await replayer.get("/v1/auth/me")

    assert response.status_code == 204
    assert after != before
    assert me_here.status_code == 200
    assert me_other.status_code == 401
    assert old_cookie.status_code == 401
    async with client_for(app) as fresh:
        old_login = await fresh.post(
            "/v1/auth/login", json={"email": "rotate@example.com", "password": PASSWORD}
        )
        new_login = await fresh.post(
            "/v1/auth/login", json={"email": "rotate@example.com", "password": "an entirely new password"}
        )
    assert old_login.status_code == 401
    assert new_login.status_code == 204


async def test_change_password_with_the_admin_token_is_403(admin_client: httpx.AsyncClient) -> None:
    response = await admin_client.post("/v1/auth/password", json={"current": "x", "new": "yyyyyyyyyy"})

    assert response.status_code == 403


# -------------------------------------------------------------------- invites
async def _invite(admin: httpx.AsyncClient, email: str, role: str = "viewer") -> dict[str, str]:
    response = await admin.post("/v1/workspaces/default/invites", json={"email": email, "role": role})
    assert response.status_code == 201, response.text
    body: dict[str, str] = response.json()
    return body


async def test_invite_new_user_accepts_sets_password_and_signs_in(
    app: FastAPI, admin_client: httpx.AsyncClient, database: Database
) -> None:
    invite = await _invite(admin_client, "new@example.com", role="builder")
    assert invite["url"].startswith("http://localhost:3000/login?invite=")

    async with client_for(app) as client:
        accepted = await client.post(
            "/v1/auth/accept-invite",
            json={"token": invite["token"], "password": "brand new password", "name": "Newbie"},
        )
        me = await client.get("/v1/auth/me")

    assert accepted.status_code == 204
    assert me.json()["user"]["name"] == "Newbie"
    assert me.json()["workspaces"][0]["role"] == "builder"


async def test_invite_token_works_only_once(app: FastAPI, admin_client: httpx.AsyncClient) -> None:
    invite = await _invite(admin_client, "once@example.com")
    async with client_for(app) as client:
        first = await client.post(
            "/v1/auth/accept-invite", json={"token": invite["token"], "password": "first password"}
        )
        second = await client.post(
            "/v1/auth/accept-invite", json={"token": invite["token"], "password": "second password"}
        )

    assert first.status_code == 204
    assert second.status_code == 400
    assert "already been used" in second.json()["error"]["message"]


async def test_invite_forged_token_is_rejected(app: FastAPI, admin_client: httpx.AsyncClient) -> None:
    invite = await _invite(admin_client, "forged@example.com")
    body, signature = invite["token"].split(".")
    async with client_for(app) as client:
        response = await client.post(
            "/v1/auth/accept-invite",
            json={"token": f"{body}.{signature[::-1]}", "password": "some password"},
        )

    assert response.status_code == 400


async def test_invite_for_an_existing_account_requires_its_password(
    app: FastAPI, admin_client: httpx.AsyncClient, database: Database
) -> None:
    other = await make_workspace(database, "other")
    user_id = await make_user(database, "existing@example.com", role="viewer", workspace_id=other)
    invite = await _invite(admin_client, "existing@example.com", role="builder")

    async with client_for(app) as client:
        wrong = await client.post(
            "/v1/auth/accept-invite", json={"token": invite["token"], "password": "not my password"}
        )
        right = await client.post(
            "/v1/auth/accept-invite", json={"token": invite["token"], "password": PASSWORD}
        )

    assert wrong.status_code == 401
    assert right.status_code == 204
    async with database.session() as session:
        member = await session.get(WorkspaceMember, (DEFAULT_WORKSPACE_ID, user_id))
    assert member is not None and member.role == "builder"


async def test_invited_user_cannot_sign_in_before_accepting(
    admin_client: httpx.AsyncClient, client: httpx.AsyncClient
) -> None:
    await _invite(admin_client, "pending@example.com")

    response = await client.post("/v1/auth/login", json={"email": "pending@example.com", "password": ""})

    assert response.status_code in {401, 422}
