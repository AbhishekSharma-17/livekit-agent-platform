"""argon2id password hashing (CONTRACTS-V2 §1.1: ``users.password_hash``).

Library: ``argon2-cffi``'s :class:`~argon2.PasswordHasher`, whose default type
is argon2id with the RFC 9106 "low memory" profile. :func:`hash_password` is
the name :mod:`lkap_api.bootstrap` imports lazily for the first owner.

Hashing is CPU-bound (~50 ms), so async callers use :func:`hash_password_async`
and :func:`verify_password_async`, which run it on a worker thread.
"""

from __future__ import annotations

import asyncio
from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

#: Shortest password the api accepts on set/change/accept-invite.
MIN_PASSWORD_LENGTH = 8
#: Longest password the api accepts (bounds hashing work per request).
MAX_PASSWORD_LENGTH = 1024


@lru_cache(maxsize=1)
def _hasher() -> PasswordHasher:
    return PasswordHasher()


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """A real hash of a random string, verified when the user does not exist.

    Verifying against it makes "unknown email" cost the same as "wrong
    password", so login timing does not reveal which emails are registered.
    """
    return _hasher().hash("lkap-dummy-password-for-timing")


def hash_password(password: str) -> str:
    """Return the argon2id hash of ``password`` (a PHC string).

    Args:
        password: The plaintext password.

    Returns:
        The encoded hash to store in ``users.password_hash``.
    """
    return _hasher().hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Check ``password`` against a stored hash in constant work.

    Args:
        password_hash: The stored hash, or ``None`` for a user without a password
            (invited but not accepted, or a future OIDC-only user).
        password: The presented plaintext.

    Returns:
        ``True`` only when the hash exists and matches.
    """
    if not password_hash:
        try:
            _hasher().verify(_dummy_hash(), password)
        except VerificationError:
            pass
        return False
    try:
        return _hasher().verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """Return whether a stored hash uses outdated parameters and should be upgraded."""
    try:
        return _hasher().check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


async def hash_password_async(password: str) -> str:
    """:func:`hash_password` on a worker thread."""
    return await asyncio.to_thread(hash_password, password)


async def verify_password_async(password_hash: str | None, password: str) -> bool:
    """:func:`verify_password` on a worker thread."""
    return await asyncio.to_thread(verify_password, password_hash, password)


def password_problem(password: str) -> str | None:
    """Return why ``password`` is unacceptable, or ``None`` when it is fine."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"password must be at least {MIN_PASSWORD_LENGTH} characters"
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"password must be at most {MAX_PASSWORD_LENGTH} characters"
    return None
