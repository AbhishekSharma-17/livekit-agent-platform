"""``python -m lkap_api.keys`` — master key helper.

``generate`` prints a fresh Fernet key for ``LKAP_MASTER_KEY``. The value is
written to stdout only (never to a file, never to a log) so the operator can
paste it into their launch config or ``.env``.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

from cryptography.fernet import Fernet

USAGE = "usage: python -m lkap_api.keys generate\n"


def generate_master_key() -> str:
    """Return a new urlsafe-base64 Fernet key suitable for ``LKAP_MASTER_KEY``."""
    return Fernet.generate_key().decode("utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the key CLI.

    Args:
        argv: Arguments after the program name; defaults to ``sys.argv[1:]``.

    Returns:
        A process exit code.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or args[0] != "generate":
        sys.stderr.write(USAGE)
        return 2
    sys.stdout.write(generate_master_key() + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
