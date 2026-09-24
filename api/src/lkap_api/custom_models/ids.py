"""The model-id rule and the secret scrubber (docs/v4/CUSTOM-MODELS.md D-V4-23, D-V4-26).

The rule itself lives in :mod:`lkap_contracts.providers` (one rule for the api,
the console and the MCP); this module re-exports it next to :func:`scrub`, the
one function every vendor message passes through before it is stored,
returned or logged.
"""

from __future__ import annotations

from collections.abc import Iterable

from lkap_contracts.providers import (
    ID_LIKE_FIELD_NAMES,
    MODEL_ID_MAX_LEN,
    MODEL_ID_PATTERN,
    SECRET_LOOKING_REASON,
    SECRET_PREFIXES,
    id_like_field,
    looks_like_secret,
    validate_model_id,
)

#: What a scrubbed secret (or a fragment of one) is replaced with.
REDACTED = "•••"

#: Longest scrubbed message kept (the `provider_models.last_test_message` column).
MAX_MESSAGE_LEN = 500

#: Fragment length scrubbed from each end of a secret (a vendor may echo a prefix or suffix).
FRAGMENT_LEN = 6

#: Secrets shorter than this are scrubbed whole only: a 6-character fragment of a short
#: value would blank innocent words.
MIN_LEN_FOR_FRAGMENTS = 12


def scrub(text: str, secret_values: Iterable[str], *, max_len: int = MAX_MESSAGE_LEN) -> str:
    """Remove every secret value, and its first/last characters, from ``text``.

    Each value is replaced whole; for a value of at least
    :data:`MIN_LEN_FOR_FRAGMENTS` characters its first and last
    :data:`FRAGMENT_LEN` characters are replaced too (some vendor 401 bodies
    echo a truncated key). The result is cut to ``max_len`` characters.

    Args:
        text: A vendor message or any other string bound for storage or a log.
        secret_values: The decrypted secret values of the credential in use.
        max_len: Longest result.

    Returns:
        The scrubbed, truncated text.
    """
    values = sorted({v for v in secret_values if v}, key=len, reverse=True)
    out = text
    for value in values:
        out = out.replace(value, REDACTED)
    for value in values:
        if len(value) >= MIN_LEN_FOR_FRAGMENTS:
            out = out.replace(value[:FRAGMENT_LEN], REDACTED).replace(value[-FRAGMENT_LEN:], REDACTED)
    return out[:max_len]


__all__ = [
    "ID_LIKE_FIELD_NAMES",
    "MAX_MESSAGE_LEN",
    "MODEL_ID_MAX_LEN",
    "MODEL_ID_PATTERN",
    "REDACTED",
    "SECRET_LOOKING_REASON",
    "SECRET_PREFIXES",
    "id_like_field",
    "looks_like_secret",
    "scrub",
    "validate_model_id",
]
