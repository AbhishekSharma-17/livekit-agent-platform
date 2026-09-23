"""Workspace API keys (CONTRACTS-V2 §1.1 ``api_keys``, §3.1).

A raw key is ``lkap_`` + 32 url-safe random bytes, returned exactly once at
creation. The table stores its sha256 (``key_hash``) and its first 8 characters
(``prefix``) so the console can tell keys apart. Callers present it as
``Authorization: Bearer lkap_…``.
"""

from __future__ import annotations

import datetime as dt
import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.auth.sessions import hash_token
from lkap_api.db.guard import CROSS_WORKSPACE_OPTION
from lkap_api.db.models import ApiKey, utcnow

#: Every raw key starts with this marker (also how the auth layer tells a key
#: from the break-glass admin token on ``Authorization: Bearer``).
KEY_PREFIX = "lkap_"
#: How many leading characters of the raw key are stored and shown.
SHOWN_PREFIX_LENGTH = 8
#: ``last_used_at`` is written at most this often per key.
LAST_USED_RESOLUTION = dt.timedelta(seconds=60)


def generate_api_key() -> tuple[str, str, str]:
    """Return ``(raw_key, prefix, key_hash)`` for a new key."""
    raw = KEY_PREFIX + secrets.token_urlsafe(32)
    return raw, raw[:SHOWN_PREFIX_LENGTH], hash_token(raw)


def looks_like_api_key(value: str | None) -> bool:
    """Return whether a bearer value has the API-key shape."""
    return bool(value) and str(value).startswith(KEY_PREFIX)


async def resolve_api_key(db: AsyncSession, raw: str, *, now: dt.datetime | None = None) -> ApiKey | None:
    """Return the live key for a raw value, or ``None`` if unknown, revoked or expired.

    The lookup is by hash across every workspace (the key *is* what selects the
    workspace), hence the explicit cross-workspace marker for the tenancy guard.
    """
    current = now or utcnow()
    row = (
        await db.execute(
            select(ApiKey)
            .where(ApiKey.key_hash == hash_token(raw))
            .execution_options(**{CROSS_WORKSPACE_OPTION: True})
        )
    ).scalar_one_or_none()
    if row is None or row.revoked_at is not None:
        return None
    if row.expires_at is not None and row.expires_at <= current:
        return None
    if row.last_used_at is None or current - row.last_used_at >= LAST_USED_RESOLUTION:
        row.last_used_at = current
    return row
