"""Authentication, tenancy and limits for `lkap_api` (CONTRACTS-V2 §3.1–3.3).

The package replaces v1's single ``auth`` module. The four v1 helpers below
keep their names and import path (``from lkap_api.auth import ...``) so the
worker-facing ``X-Service-Token`` guard in :mod:`lkap_api.deps` is unchanged.

Submodules:

* :mod:`~lkap_api.auth.passwords` — argon2id hashing (``hash_password`` is what
  :mod:`lkap_api.bootstrap` imports lazily).
* :mod:`~lkap_api.auth.sessions` — opaque cookie sessions stored as sha256.
* :mod:`~lkap_api.auth.api_keys` — ``lkap_…`` workspace keys and scopes.
* :mod:`~lkap_api.auth.roles` — the role matrix and the v1 route policy table.
* :mod:`~lkap_api.auth.deps` — principal resolution and ``WorkspaceContext``.
* :mod:`~lkap_api.auth.ratelimit` — token buckets (in-memory or Redis).
* :mod:`~lkap_api.auth.audit` — the ``audit_log`` writer.
* :mod:`~lkap_api.auth.invites` — signed one-time invite tokens.

Comparisons of presented secrets are constant-time; no token, password or key
is ever logged or echoed in an error body.
"""

from __future__ import annotations

import secrets

#: Header carrying the break-glass admin token (`LKAP_ALLOW_ADMIN_TOKEN`).
ADMIN_HEADER = "X-Admin-Token"
#: Header carrying the worker service token (`/internal/v1` routes).
SERVICE_HEADER = "X-Service-Token"
#: Header selecting the workspace of an admin request (slug or id).
WORKSPACE_HEADER = "X-Workspace"
#: Query parameter equivalent of :data:`WORKSPACE_HEADER`.
WORKSPACE_QUERY = "workspace"
#: The HttpOnly cookie carrying a user session token.
SESSION_COOKIE = "lkap_session"

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
    return secrets.compare_digest(provided.encode(), expected.encode())
