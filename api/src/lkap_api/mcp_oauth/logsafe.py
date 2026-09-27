"""A structlog processor that masks OAuth values in log events (V5-14).

The sign-in code never passes a token, code, ``state``, verifier or client secret to
a logger. This processor is the belt to those braces: any event field whose name is
one of :data:`OAUTH_SECRET_FIELDS` (or ends in ``_token``) is replaced with
``"[redacted]"``. ``lkap_api.logging.configure_logging`` runs it in ``shared_processors``
for every structlog and stdlib record (V5-27, S5-32; asks #111).
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any, Final

#: Event fields that never reach a log line with their value.
OAUTH_SECRET_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "access_token",
        "refresh_token",
        "id_token",
        "registration_access_token",
        "code",
        "code_verifier",
        "state",
        "client_secret",
        "authorization",
        "authorization_url",
    }
)
REDACTED: Final[str] = "[redacted]"


def redact_oauth_fields(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Mask every OAuth secret field of ``event_dict`` (structlog processor signature)."""
    for key in list(event_dict):
        lowered = key.lower()
        if lowered in OAUTH_SECRET_FIELDS or lowered.endswith("_token"):
            event_dict[key] = REDACTED
    return event_dict


__all__ = ["OAUTH_SECRET_FIELDS", "REDACTED", "redact_oauth_fields"]
