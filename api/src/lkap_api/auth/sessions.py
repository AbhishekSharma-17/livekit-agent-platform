"""Opaque cookie sessions (CONTRACTS-V2 §3.1, ``user_sessions``).

The cookie ``lkap_session`` carries 32 random url-safe bytes; only their sha256
is stored, so a database dump cannot be replayed as a cookie. Sessions last
``LKAP_SESSION_TTL_HOURS`` (12 h, ARCHITECTURE-V2 D-V2-18).

Refresh rotates the row: once less than half the lifetime is left, the next
authenticated request gets a fresh token and row, and the old row is cut down
to a short grace period so requests already in flight with the old cookie
still succeed. Login always issues a new row (no session fixation); a password
change revokes every other session of the user.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import secrets
from dataclasses import dataclass

from fastapi import Response
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.auth import SESSION_COOKIE
from lkap_api.db.models import User, UserSession, new_id, utcnow
from lkap_api.settings import Settings

#: How long a rotated-out token keeps working for requests already in flight.
ROTATION_GRACE = dt.timedelta(seconds=60)
#: ``last_seen_at`` is written at most this often per session.
LAST_SEEN_RESOLUTION = dt.timedelta(seconds=60)


def hash_token(raw: str) -> str:
    """Return the hex sha256 of a raw session token or API key."""
    return hashlib.sha256(raw.encode()).hexdigest()


def new_session_token() -> str:
    """Return a fresh, unguessable session token."""
    return secrets.token_urlsafe(32)


@dataclass(frozen=True)
class ResolvedSession:
    """A valid session and its (enabled) user."""

    session: UserSession
    user: User


async def create_session(
    db: AsyncSession,
    user_id: str,
    *,
    ttl: dt.timedelta,
    user_agent: str = "",
    ip: str = "",
    now: dt.datetime | None = None,
) -> str:
    """Insert a session row and return the raw token for the cookie.

    Args:
        db: The request session; the caller commits.
        user_id: The signed-in user.
        ttl: Session lifetime.
        user_agent: The client's ``User-Agent`` (truncated to the column size).
        ip: The client address.
        now: Clock override for tests.

    Returns:
        The raw token; it is never stored.
    """
    current = now or utcnow()
    raw = new_session_token()
    db.add(
        UserSession(
            id=new_id(),
            user_id=user_id,
            token_hash=hash_token(raw),
            expires_at=current + ttl,
            created_at=current,
            last_seen_at=current,
            user_agent=user_agent[:512],
            ip=ip[:64],
        )
    )
    await db.flush()
    return raw


async def resolve_session(
    db: AsyncSession, raw: str | None, *, now: dt.datetime | None = None
) -> ResolvedSession | None:
    """Return the live session for a raw cookie value, or ``None``.

    Expired sessions and sessions of disabled users resolve to ``None``.
    """
    if not raw:
        return None
    current = now or utcnow()
    row = (
        await db.execute(
            select(UserSession, User)
            .join(User, User.id == UserSession.user_id)
            .where(UserSession.token_hash == hash_token(raw))
        )
    ).one_or_none()
    if row is None:
        return None
    session, user = row._tuple()
    if session.expires_at <= current or user.disabled_at is not None:
        return None
    if session.last_seen_at is None or current - session.last_seen_at >= LAST_SEEN_RESOLUTION:
        session.last_seen_at = current
    return ResolvedSession(session=session, user=user)


async def refresh_if_due(
    db: AsyncSession,
    resolved: ResolvedSession,
    *,
    ttl: dt.timedelta,
    now: dt.datetime | None = None,
) -> str | None:
    """Rotate the session when less than half its lifetime is left.

    Args:
        db: The request session; the caller commits.
        resolved: The session the request authenticated with.
        ttl: The full session lifetime.
        now: Clock override for tests.

    Returns:
        The new raw token to set as the cookie, or ``None`` when no rotation
        was due.
    """
    current = now or utcnow()
    old = resolved.session
    if old.expires_at - current > ttl / 2:
        return None
    raw = await create_session(
        db, resolved.user.id, ttl=ttl, user_agent=old.user_agent, ip=old.ip, now=current
    )
    old.expires_at = min(old.expires_at, current + ROTATION_GRACE)
    return raw


async def revoke_session(db: AsyncSession, raw: str | None) -> None:
    """Delete the session a raw token belongs to (logout); unknown tokens are ignored."""
    if raw:
        await db.execute(delete(UserSession).where(UserSession.token_hash == hash_token(raw)))


async def revoke_other_sessions(db: AsyncSession, user_id: str, *, keep_session_id: str | None) -> None:
    """Delete every session of ``user_id`` except ``keep_session_id`` (password change)."""
    stmt = delete(UserSession).where(UserSession.user_id == user_id)
    if keep_session_id is not None:
        stmt = stmt.where(UserSession.id != keep_session_id)
    await db.execute(stmt)


async def expire_session_now(db: AsyncSession, session_id: str, *, now: dt.datetime | None = None) -> None:
    """End one session immediately (used when a password change rotates the current one)."""
    await db.execute(
        update(UserSession).where(UserSession.id == session_id).values(expires_at=now or utcnow())
    )


def set_session_cookie(response: Response, raw: str, settings: Settings) -> None:
    """Attach the ``lkap_session`` cookie (HttpOnly, SameSite=Lax, Secure outside dev)."""
    response.set_cookie(
        SESSION_COOKIE,
        raw,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    """Remove the ``lkap_session`` cookie."""
    response.delete_cookie(
        SESSION_COOKIE, path="/", httponly=True, secure=settings.cookie_secure, samesite="lax"
    )
