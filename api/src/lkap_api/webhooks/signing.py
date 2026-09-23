"""HMAC signing for outbound webhook deliveries (CONTRACTS-V2 §4.6).

Header: ``X-LKAP-Signature: t=<unix>,v1=<hmac_sha256(secret, f"{t}.{body}")>``
computed over the *exact bytes* sent on the wire — callers sign a serialised
body once and send those same bytes (`content=`, never re-`json=`-encoded),
matching the shared `scripts/webhook_sink.py` verifier used at L7.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

SIGNATURE_HEADER = "X-LKAP-Signature"
EVENT_ID_HEADER = "X-LKAP-Event-Id"

#: How far a signature's `t` may drift from "now" and still verify.
DEFAULT_TOLERANCE_S = 300


def generate_secret() -> str:
    """Return a new webhook signing secret (shown to the caller once)."""
    return secrets.token_urlsafe(32)


def secret_prefix(secret: str) -> str:
    """Return the short, non-secret prefix shown in `WebhookEndpointOut`."""
    return secret[:8]


def sign(secret: str, body: bytes, *, t: int | None = None) -> str:
    """Return the `X-LKAP-Signature` header value for `body`.

    Args:
        secret: The endpoint's plaintext signing secret.
        body: The exact bytes that will be sent as the request body.
        t: Injected unix timestamp for tests; defaults to now.
    """
    ts = t if t is not None else int(time.time())
    mac = _mac(secret, ts, body)
    return f"t={ts},v1={mac}"


def _mac(secret: str, t: int, body: bytes) -> str:
    """`hmac_sha256(secret, f"{t}.{body}")` (CONTRACTS-V2 §4.6) over the literal body text."""
    signed = f"{t}.{body.decode('utf-8')}".encode()
    return hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()


def verify_signature(
    secret: str, header: str, body: bytes, *, tolerance_s: int = DEFAULT_TOLERANCE_S
) -> bool:
    """Verify an `X-LKAP-Signature` header against `body`.

    Args:
        secret: The endpoint's plaintext signing secret.
        header: The received `X-LKAP-Signature` value.
        body: The exact request body bytes that were received.
        tolerance_s: Maximum allowed drift between `t` and now.

    Returns:
        `True` iff the signature matches and `t` is within tolerance.
    """
    parts = dict(part.split("=", 1) for part in header.split(",") if "=" in part)
    t_raw, v1 = parts.get("t"), parts.get("v1")
    if t_raw is None or v1 is None:
        return False
    try:
        t = int(t_raw)
    except ValueError:
        return False
    if abs(time.time() - t) > tolerance_s:
        return False
    expected = _mac(secret, t, body)
    return hmac.compare_digest(expected, v1)
