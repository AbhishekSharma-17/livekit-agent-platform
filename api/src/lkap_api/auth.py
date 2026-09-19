"""Static-token authentication helpers.

The MVP is operated by a single person: an admin bearer token guards the
console surface (``X-Admin-Token``) and a separate service token guards the
worker surface (``X-Service-Token``). Comparisons are constant-time; neither
token is ever logged or echoed in an error body.
"""

from __future__ import annotations

import secrets

#: Header carrying the admin token (console + any `/v1` admin route).
ADMIN_HEADER = "X-Admin-Token"
#: Header carrying the worker service token (`/internal/v1` routes).
SERVICE_HEADER = "X-Service-Token"

_BEARER_PREFIX = "bearer "


def bearer_value(authorization: str | None) -> str | None:
    """Return the token from an ``Authorization: Bearer <token>`` header, if any."""
    if not authorization:
        return None
    if authorization.lower().startswith(_BEARER_PREFIX):
        return authorization[len(_BEARER_PREFIX) :].strip() or None
    return None


def token_matches(provided: str | None, expected: str) -> bool:
    """Constant-time comparison of a presented token against the configured one.

    Args:
        provided: The token from the request, or ``None`` when absent.
        expected: The configured token; an empty value never matches.

    Returns:
        ``True`` only when both are non-empty and equal.
    """
    if not provided or not expected:
        return False
    return secrets.compare_digest(provided, expected)
