"""Signed, one-time invite tokens (CONTRACTS-V2 §3.1: signup is invite-only).

A token is ``base64url(json).base64url(hmac_sha256(secret, json))`` with the
secret ``LKAP_SESSION_SECRET`` (in ``dev`` without one, a key derived from
``LKAP_MASTER_KEY``). It names the workspace, the invited user and the role,
expires after :data:`INVITE_TTL`, and carries a *state fingerprint*: a hash of
the user's current password hash and whether they already belong to the
workspace. Accepting the invite changes that state, so the same token can
never be used twice — without an invites table.

Creating an invite creates the user row (``password_hash`` NULL, so it cannot
sign in) if the email is new; the membership is only added on acceptance.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.auth.roles import ROLES, Role
from lkap_api.db.guard import CROSS_WORKSPACE_OPTION
from lkap_api.db.models import AuditLog, utcnow
from lkap_api.settings import Settings

#: How long an invite link stays valid.
INVITE_TTL = dt.timedelta(days=7)


class InvalidInvite(ValueError):
    """The token is malformed, forged, expired or already used."""


@dataclass(frozen=True)
class InviteClaims:
    """What a verified invite token says."""

    workspace_id: str
    user_id: str
    role: Role
    expires_at: dt.datetime
    fingerprint: str


def signing_secret(settings: Settings) -> bytes:
    """Return the key invite tokens are signed with."""
    if settings.session_secret:
        return settings.session_secret.encode()
    return hmac.new(settings.master_key.encode(), b"lkap-invite-signing-v1", hashlib.sha256).digest()


def state_fingerprint(password_hash: str | None, is_member: bool, joins: int = 0) -> str:
    """Hash of the account state an invite is valid for (see module docstring).

    Args:
        password_hash: The user's current password hash.
        is_member: Whether the user already belongs to the workspace.
        joins: How many times the user has joined this workspace by invite
            (``member.join`` audit rows). Accepting bumps it, so a token stays
            dead after its member is removed again (V2-21); ``0`` leaves the
            material of tokens issued before this counter existed unchanged.
    """
    material = f"{password_hash or ''}|{int(is_member)}" + (f"|{joins}" if joins else "")
    return hashlib.sha256(material.encode()).hexdigest()[:32]


async def join_count(db: AsyncSession, workspace_id: str, user_id: str) -> int:
    """How many ``member.join`` audit rows the user has in the workspace."""
    total = await db.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(
            AuditLog.workspace_id == workspace_id,
            AuditLog.action == "member.join",
            AuditLog.target_id == user_id,
        )
    )
    return int(total or 0)


async def invited_elsewhere(db: AsyncSession, workspace_id: str, user_id: str) -> bool:
    """Whether another workspace has already issued an invite to this user.

    Used for *pending* (password-less) accounts: the invite token sets the
    account's password, so a second workspace's admin must not be able to mint
    one for an account another workspace created and is waiting on (V2-21).
    """
    found = await db.scalar(
        select(AuditLog.id)
        .where(
            AuditLog.action == "member.invite",
            AuditLog.target_id == user_id,
            AuditLog.workspace_id != workspace_id,
        )
        .limit(1)
        .execution_options(**{CROSS_WORKSPACE_OPTION: True})  # deliberately cross-workspace
    )
    return found is not None


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue(
    settings: Settings,
    *,
    workspace_id: str,
    user_id: str,
    role: Role,
    fingerprint: str,
    now: dt.datetime | None = None,
) -> tuple[str, dt.datetime]:
    """Sign a new invite token.

    Returns:
        ``(token, expires_at)``.
    """
    expires_at = (now or utcnow()) + INVITE_TTL
    body = json.dumps(
        {
            "v": 1,
            "wid": workspace_id,
            "uid": user_id,
            "role": role,
            "exp": int(expires_at.timestamp()),
            "fp": fingerprint,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    signature = hmac.new(signing_secret(settings), body, hashlib.sha256).digest()
    return f"{_b64(body)}.{_b64(signature)}", expires_at


def verify(settings: Settings, token: str, *, now: dt.datetime | None = None) -> InviteClaims:
    """Check a token's signature and expiry and return its claims.

    Raises:
        InvalidInvite: When the token is malformed, forged or expired.
    """
    try:
        body_part, signature_part = token.strip().split(".", 1)
        body = _unb64(body_part)
        signature = _unb64(signature_part)
    except ValueError as exc:
        raise InvalidInvite("malformed invite token") from exc
    expected = hmac.new(signing_secret(settings), body, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        raise InvalidInvite("invite token signature does not match")
    try:
        claims: dict[str, Any] = json.loads(body)
        expires_at = dt.datetime.fromtimestamp(int(claims["exp"]), dt.UTC)
        role = str(claims["role"])
        parsed = InviteClaims(
            workspace_id=str(claims["wid"]),
            user_id=str(claims["uid"]),
            role=role,  # type: ignore[arg-type]  # checked against ROLES below
            expires_at=expires_at,
            fingerprint=str(claims["fp"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidInvite("malformed invite token") from exc
    if role not in ROLES:
        raise InvalidInvite("malformed invite token")
    if parsed.expires_at <= (now or utcnow()):
        raise InvalidInvite("invite has expired")
    return parsed
