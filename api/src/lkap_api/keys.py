"""``python -m lkap_api.keys`` — master key helper.

``generate`` prints a fresh Fernet key for ``LKAP_MASTER_KEY``. The value is
written to stdout only (never to a file, never to a log) so the operator can
paste it into their launch config.

``rotate --old <key> --new <key>`` re-encrypts every ciphertext column in the
database with the new key: ``credentials.ciphertext``,
``livekit_connections.api_key_ct``/``api_secret_ct``,
``storage_configs.access_key_ct``/``secret_key_ct``,
``webhook_endpoints.secret_ct`` and ``sip_trunks.auth_password_ct``. Every column
holds a Fernet token, so rotation is a decrypt-with-old / encrypt-with-new pass
that never needs to understand the plaintext. The whole pass runs in one
transaction: either every row is on the new key or none is.

Run it with the api stopped, against a database you have just backed up, and
only then change ``LKAP_MASTER_KEY`` in the launch config.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Table, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.db.guard import CROSS_WORKSPACE_OPTION
from lkap_api.db.models import Base
from lkap_api.db.session import Database
from lkap_api.logging import get_logger
from lkap_api.settings import get_settings

log = get_logger(__name__)


@dataclass(frozen=True)
class CipherColumn:
    """One encrypted column: its table, its primary key columns and its name."""

    table: str
    column: str
    key_columns: tuple[str, ...]


#: Every Fernet column in the schema (CONTRACTS-V2 §1.2, §1.5, §1.6).
CIPHER_COLUMNS: tuple[CipherColumn, ...] = (
    CipherColumn("credentials", "ciphertext", ("id",)),
    CipherColumn("livekit_connections", "api_key_ct", ("id",)),
    CipherColumn("livekit_connections", "api_secret_ct", ("id",)),
    CipherColumn("storage_configs", "access_key_ct", ("id",)),
    CipherColumn("storage_configs", "secret_key_ct", ("id",)),
    CipherColumn("webhook_endpoints", "secret_ct", ("id",)),
    CipherColumn("sip_trunks", "auth_password_ct", ("id",)),
)


class RotationError(RuntimeError):
    """Raised when a ciphertext cannot be read with the old key."""


def generate_master_key() -> str:
    """Return a new urlsafe-base64 Fernet key suitable for ``LKAP_MASTER_KEY``."""
    return Fernet.generate_key().decode("utf-8")


def _fernet(key: str, *, label: str) -> Fernet:
    try:
        return Fernet(key.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise RotationError(f"--{label} is not a valid Fernet key") from exc


def _table(name: str) -> Table:
    return Base.metadata.tables[name]


async def rotate_column(session: AsyncSession, spec: CipherColumn, old: Fernet, new: Fernet) -> int:
    """Re-encrypt one column in place.

    Args:
        session: An open session; the caller commits.
        spec: The column to rotate.
        old: Fernet built from the current master key.
        new: Fernet built from the replacement key.

    Returns:
        The number of rows rewritten.

    Raises:
        RotationError: If a non-null value cannot be decrypted with `old`.
    """
    table = _table(spec.table)
    key_cols = [table.c[name] for name in spec.key_columns]
    value_col = table.c[spec.column]
    # Key rotation re-encrypts every workspace's rows (operator CLI).
    stmt = (
        select(*key_cols, value_col)
        .where(value_col.is_not(None))
        .execution_options(**{CROSS_WORKSPACE_OPTION: True})
    )
    rows = (await session.execute(stmt)).all()
    rewritten = 0
    for row in rows:
        ciphertext = row[-1]
        if ciphertext is None:
            continue
        try:
            plaintext = old.decrypt(bytes(ciphertext))
        except InvalidToken as exc:
            raise RotationError(
                f"{spec.table}.{spec.column} could not be decrypted with --old "
                f"(row {dict(zip(spec.key_columns, row[:-1], strict=True))})"
            ) from exc
        await session.execute(
            update(table)
            .where(*[col == row[index] for index, col in enumerate(key_cols)])
            .values({spec.column: new.encrypt(plaintext)})
        )
        rewritten += 1
    return rewritten


async def rotate(database: Database, old_key: str, new_key: str) -> dict[str, int]:
    """Re-encrypt every ciphertext column, all in one transaction.

    Args:
        database: The database to rewrite.
        old_key: The current ``LKAP_MASTER_KEY``.
        new_key: The replacement key.

    Returns:
        A mapping of ``"<table>.<column>"`` to the number of rows rewritten.

    Raises:
        RotationError: If either key is invalid or any value fails to decrypt;
            nothing is committed in that case.
    """
    old = _fernet(old_key, label="old")
    new = _fernet(new_key, label="new")
    if old_key == new_key:
        raise RotationError("--old and --new are the same key; nothing to rotate")

    counts: dict[str, int] = {}
    async with database.session() as session:
        for spec in CIPHER_COLUMNS:
            counts[f"{spec.table}.{spec.column}"] = await rotate_column(session, spec, old, new)
    log.info("master_key_rotated", rows=counts)
    return counts


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m lkap_api.keys")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("generate", help="print a fresh LKAP_MASTER_KEY")
    rotate_parser = sub.add_parser("rotate", help="re-encrypt every secret with a new master key")
    rotate_parser.add_argument("--old", required=True, help="the current LKAP_MASTER_KEY")
    rotate_parser.add_argument("--new", required=True, help="the replacement key")
    return parser


async def _rotate_command(old_key: str, new_key: str) -> int:
    database = Database(get_settings().resolved_database_url)
    try:
        counts = await rotate(database, old_key, new_key)
    finally:
        await database.dispose()
    total = sum(counts.values())
    sys.stdout.write(f"rotated {total} secret(s) across {len(counts)} column(s)\n")
    for name, count in counts.items():
        sys.stdout.write(f"  {name}: {count}\n")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the key CLI.

    Args:
        argv: Arguments after the program name; defaults to ``sys.argv[1:]``.

    Returns:
        A process exit code.
    """
    try:
        args = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    except SystemExit as exc:
        # `main` promises an exit code rather than an exception, so argparse's
        # "usage: ..." exit (code 2) is returned like any other failure.
        return int(exc.code) if isinstance(exc.code, int) else 2
    if args.command == "generate":
        sys.stdout.write(generate_master_key() + "\n")
        return 0
    try:
        return asyncio.run(_rotate_command(args.old, args.new))
    except RotationError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 1


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
