"""Telnyx SMS (registry ``telnyx-sms``): ``POST https://api.telnyx.com/v2/messages``.

Request shape as documented by Telnyx (JSON ``from``, ``to``, ``text``,
optional ``messaging_profile_id``; bearer key); the answer carries
``data.id`` and ``data.to[0].status``. Pinned by ``respx`` fixtures;
unverified against the live service until V5-25's live check.
"""

from __future__ import annotations

from typing import Any, Final

import httpx

from lkap_agent.tools.vendors import DEFAULT_TIMEOUT_S, SmsReceipt, VendorError, json_body, vendor_call

TELNYX_MESSAGES_URL: Final[str] = "https://api.telnyx.com/v2/messages"
VENDOR: Final[str] = "Telnyx"


async def send(
    *,
    to: str,
    body: str,
    from_number: str,
    api_key: str,
    messaging_profile_id: str | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    transport: httpx.AsyncBaseTransport | None = None,
) -> SmsReceipt:
    """Send one text message with Telnyx.

    Args:
        to: The destination, E.164.
        body: The message text.
        from_number: The sending Telnyx number, E.164.
        api_key: The workspace's Telnyx key.
        messaging_profile_id: Sent only when set.
        timeout_s: The call's timeout.
        transport: A test seam (see :func:`~lkap_agent.tools.vendors.vendor_call`).

    Returns:
        Telnyx's message id and the first recipient's status (``queued`` when accepted).

    Raises:
        VendorError: The key is missing or the call failed.
    """
    if not api_key:
        raise VendorError(f"{VENDOR} has no key")
    message: dict[str, Any] = {"from": from_number, "to": to, "text": body}
    if messaging_profile_id:
        message["messaging_profile_id"] = messaging_profile_id
    response = await vendor_call(
        VENDOR,
        "POST",
        TELNYX_MESSAGES_URL,
        json=message,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout_s=timeout_s,
        transport=transport,
    )
    payload: Any = json_body(VENDOR, response)
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise VendorError(f"{VENDOR} sent an answer that could not be read")
    status = "sent"
    recipients = data.get("to")
    if isinstance(recipients, list) and recipients and isinstance(recipients[0], dict):
        status = str(recipients[0].get("status") or status)
    return SmsReceipt(message_id=str(data.get("id") or ""), status=status)
