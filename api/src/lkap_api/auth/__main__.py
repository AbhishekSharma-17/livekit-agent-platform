"""Operator commands: ``python -m lkap_api.auth set-password --email <email> [--force]``.

``set-password`` gives an existing user a password. It exists for the owner
row V2-01's bootstrap created before this package landed (``password_hash``
NULL, so nobody could sign in — ``docs/v2/_asks.md`` #12). The password comes
from ``LKAP_BOOTSTRAP_OWNER_PASSWORD`` when set; otherwise a random one is
generated and logged once at WARNING, exactly like bootstrap does. Without
``--force`` a user that already has a password is left alone.
"""

from __future__ import annotations

import argparse
import asyncio
import secrets

from sqlalchemy import select

from lkap_api.auth.passwords import hash_password_async, password_problem
from lkap_api.db.models import User
from lkap_api.db.session import Database
from lkap_api.logging import configure_logging, get_logger
from lkap_api.settings import Settings, get_settings

log = get_logger("lkap_api.auth")


async def set_password(settings: Settings, email: str, *, force: bool = False) -> int:
    """Set a user's password; return a process exit code.

    Args:
        settings: Runtime settings (database url, bootstrap password).
        email: The user's email (case-insensitive).
        force: Replace an existing password too.

    Returns:
        ``0`` when set or already set, ``1`` for an unknown user or a bad password.
    """
    database = Database(settings.resolved_database_url)
    try:
        async with database.session() as session:
            user = (
                await session.execute(select(User).where(User.email == email.strip().lower()))
            ).scalar_one_or_none()
            if user is None:
                log.error("set_password_unknown_user", email=email)
                return 1
            if user.password_hash and not force:
                log.info(
                    "set_password_skipped", email=user.email, reason="already has a password; use --force"
                )
                return 0
            plaintext = settings.bootstrap_owner_password
            generated = plaintext is None
            if plaintext is None:
                plaintext = secrets.token_urlsafe(24)
            if (problem := password_problem(plaintext)) is not None:
                log.error("set_password_rejected", email=user.email, reason=problem)
                return 1
            user.password_hash = await hash_password_async(plaintext)
        if generated:
            log.warning(
                "password_generated", email=user.email, password=plaintext, hint="shown once; store it now"
            )
        else:
            log.info("password_set", email=user.email, source="LKAP_BOOTSTRAP_OWNER_PASSWORD")
        return 0
    finally:
        await database.dispose()


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m lkap_api.auth``."""
    parser = argparse.ArgumentParser(prog="python -m lkap_api.auth")
    commands = parser.add_subparsers(dest="command", required=True)
    set_pw = commands.add_parser("set-password", help="give an existing user a password")
    set_pw.add_argument("--email", required=True)
    set_pw.add_argument("--force", action="store_true", help="replace an existing password")
    args = parser.parse_args(argv)
    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    return asyncio.run(set_password(settings, args.email, force=args.force))


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
