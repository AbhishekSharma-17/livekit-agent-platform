"""Twilio SMS (registry ``twilio-sms``): ``POST /2010-04-01/Accounts/{AccountSid}/Messages.json``.

Request shape as documented by Twilio (form fields ``To``, ``From``, ``Body``;
HTTP basic auth with the account SID and auth token); the answer carries
``sid`` and ``status``. Pinned by ``respx`` fixtures; unverified against the
live service until V5-25's live check.
"""

from __future__ import annotations

import re
from typing import Any, Final

import httpx

from lkap_agent.tools.vendors import DEFAULT_TIMEOUT_S, SmsReceipt, VendorError, json_body, vendor_call

TWILIO_API_BASE: Final[str] = "https://api.twilio.com/2010-04-01"
VENDOR: Final[str] = "Twilio"

#: An account SID: ``AC`` and 32 hex digits (checked so the path can hold nothing else).
_ACCOUNT_SID_RE = re.compile(r"^AC[0-9a-fA-F]{32}$")


def messages_url(account_sid: str) -> str:
    """The Messages resource of ``account_sid``.

    Raises:
        VendorError: The SID is not an account SID (``AC`` + 32 hex digits).
    """
    if not _ACCOUNT_SID_RE.match(account_sid):
        raise VendorError(f"{VENDOR} account SID is not valid; an admin needs to check the key")
    return f"{TWILIO_API_BASE}/Accounts/{account_sid}/Messages.json"


async def send(
    *,
    to: str,
    body: str,
    from_number: str,
    account_sid: str,
    auth_token: str,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    transport: httpx.AsyncBaseTransport | None = None,
) -> SmsReceipt:
    """Send one text message with Twilio.

    Args:
        to: The destination, E.164.
        body: The message text.
        from_number: The sending Twilio number, E.164.
        account_sid: The account SID (``AC…``).
        auth_token: The account's auth token.
        timeout_s: The call's timeout.
        transport: A test seam (see :func:`~lkap_agent.tools.vendors.vendor_call`).

    Returns:
        Twilio's message id and status (``queued`` for an accepted message).

    Raises:
        VendorError: A key is missing or the call failed.
    """
    if not auth_token:
        raise VendorError(f"{VENDOR} has no auth token")
    response = await vendor_call(
        VENDOR,
        "POST",
        messages_url(account_sid),
        data={"To": to, "From": from_number, "Body": body},
        auth=(account_sid, auth_token),
        timeout_s=timeout_s,
        transport=transport,
    )
    payload: Any = json_body(VENDOR, response)
    if not isinstance(payload, dict):
        raise VendorError(f"{VENDOR} sent an answer that could not be read")
    return SmsReceipt(message_id=str(payload.get("sid") or ""), status=str(payload.get("status") or "sent"))
