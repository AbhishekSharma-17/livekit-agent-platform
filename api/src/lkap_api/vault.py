"""Credential vault: symmetric encryption of provider secrets at rest.

Secrets are stored as a Fernet token over ``json.dumps({field: value})`` in
``credentials.ciphertext``. The key is ``LKAP_MASTER_KEY`` (urlsafe base64,
32 bytes) — generate one with ``uv run python -m lkap_api.keys generate``.

Decrypted values leave this process only through the internal, service-token
endpoint (:mod:`lkap_api.routers.internal`). They are never returned to an
admin or browser caller and never logged.
"""

from __future__ import annotations

import json

from cryptography.fernet import Fernet, InvalidToken

from lkap_api.errors import InternalError

#: Shown instead of a secret whenever a fingerprint cannot be derived.
UNKNOWN_FINGERPRINT = "…????"


class VaultError(InternalError):
    """Raised when a ciphertext cannot be decrypted with the current key."""

    code = "vault_error"


class Vault:
    """Encrypts and decrypts credential secret bags with a Fernet master key."""

    def __init__(self, master_key: str) -> None:
        """Build a vault.

        Args:
            master_key: The ``LKAP_MASTER_KEY`` value (urlsafe base64, 32 bytes).

        Raises:
            ValueError: If the key is not a valid Fernet key.
        """
        try:
            self._fernet = Fernet(master_key.encode("utf-8"))
        except (ValueError, TypeError) as exc:
            raise ValueError(
                "LKAP_MASTER_KEY is not a valid Fernet key "
                "(generate one with `uv run python -m lkap_api.keys generate`)"
            ) from exc

    def encrypt(self, secrets: dict[str, str]) -> bytes:
        """Encrypt a ``{field: value}`` secret bag.

        Args:
            secrets: Plaintext secret fields.

        Returns:
            The Fernet ciphertext to store in ``credentials.ciphertext``.
        """
        payload = json.dumps(secrets, separators=(",", ":"), sort_keys=True)
        return self._fernet.encrypt(payload.encode("utf-8"))

    def decrypt(self, ciphertext: bytes) -> dict[str, str]:
        """Decrypt a stored secret bag.

        Args:
            ciphertext: The value of ``credentials.ciphertext``.

        Returns:
            The ``{field: value}`` mapping.

        Raises:
            VaultError: If the ciphertext is corrupt or was written with another key.
        """
        try:
            raw = self._fernet.decrypt(ciphertext)
        except InvalidToken as exc:
            raise VaultError("credential could not be decrypted with the configured master key") from exc
        parsed: dict[str, str] = json.loads(raw.decode("utf-8"))
        return parsed


def fingerprint(secrets: dict[str, str], *, primary_field: str | None = None) -> str:
    """Return the display fingerprint for a secret bag.

    The fingerprint is ``"…"`` plus the last four characters of the primary
    secret value (CONTRACTS §5) — enough for an admin to tell two keys apart,
    useless to anyone else.

    Args:
        secrets: The plaintext secret bag.
        primary_field: The field to fingerprint, usually
            ``ProviderSpec.secret_fields[0].name``. Falls back to the first
            field of the bag.

    Returns:
        A short, non-reversible label such as ``"…4f9a"``.
    """
    if not secrets:
        return UNKNOWN_FINGERPRINT
    field = primary_field if primary_field in secrets else next(iter(sorted(secrets)))
    value = secrets.get(field or "", "")
    if not value:
        return UNKNOWN_FINGERPRINT
    return "…" + value[-4:]
