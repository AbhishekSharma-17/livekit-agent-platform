"""Vendor adapters the curated built-in tools call (V5-25, D-V5-7).

One module per vendor: :mod:`.tavily` and :mod:`.brave` (``web_search``),
:mod:`.twilio` and :mod:`.telnyx` (``send_sms``). Each speaks one fixed
``https`` endpoint of its vendor through :func:`vendor_call`, which applies the
same network guard as every other outbound tool call
(``tools._http_safety``): the url must be public, the connection goes through
:func:`~lkap_agent.tools._http_safety.guarded_transport` (resolve, refuse a
private answer, connect to the checked address, no proxy), redirects are not
followed, and every call has a timeout.

Keys come from the vault through the api (``ResolvedAgentConfig.builtin_providers``)
and are never logged or put in an error: :class:`VendorError` messages name the
vendor and the HTTP status only, so the model can say what went wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import urlsplit

import httpx

from lkap_agent.tools._http_safety import HttpToolSecurityError, check_url_public, guarded_transport

__all__ = [
    "DEFAULT_TIMEOUT_S",
    "SearchHit",
    "SmsReceipt",
    "VendorError",
    "json_body",
    "vendor_call",
]

#: Per-call timeout of a vendor request (seconds).
DEFAULT_TIMEOUT_S: Final[float] = 8.0


class VendorError(Exception):
    """A vendor call failed. The message is safe for the model: no key, no url, no body."""


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One web search result."""

    title: str
    url: str
    snippet: str


@dataclass(frozen=True, slots=True)
class SmsReceipt:
    """What the SMS vendor answered for one message."""

    message_id: str
    status: str


async def vendor_call(
    vendor: str,
    method: str,
    url: str,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    transport: httpx.AsyncBaseTransport | None = None,
    **request: Any,
) -> httpx.Response:
    """Send one guarded request to a vendor and return its successful response.

    Args:
        vendor: The vendor's display name, used in error messages.
        method: The HTTP method.
        url: The vendor endpoint; must be ``https`` and public.
        timeout_s: The whole call's timeout.
        transport: A test seam; defaults to :func:`guarded_transport`.
        **request: Passed to :meth:`httpx.AsyncClient.request` (``json``, ``data``,
            ``params``, ``headers``, ``auth``).

    Returns:
        The response, when its status is below 400.

    Raises:
        VendorError: The url is not a public ``https`` url, the vendor could not be
            reached or timed out, or it answered with an error status.
    """
    if urlsplit(url).scheme != "https":
        raise VendorError(f"{vendor} must be called over https")
    try:
        check_url_public(url)
    except HttpToolSecurityError as exc:
        raise VendorError(f"{vendor} is not a reachable address") from exc
    try:
        async with httpx.AsyncClient(
            follow_redirects=False, timeout=timeout_s, transport=transport or guarded_transport()
        ) as client:
            response = await client.request(method, url, **request)
    except httpx.TimeoutException as exc:
        raise VendorError(f"{vendor} did not answer in time") from exc
    except httpx.HTTPError as exc:
        raise VendorError(f"{vendor} could not be reached ({type(exc).__name__})") from exc
    status = response.status_code
    if status in (401, 403):
        raise VendorError(f"{vendor} refused the key (HTTP {status}); an admin needs to check it")
    if status == 429:
        raise VendorError(f"{vendor} is limiting requests right now (HTTP 429); try again shortly")
    if status >= 400:
        raise VendorError(f"{vendor} answered with an error (HTTP {status})")
    return response


def json_body(vendor: str, response: httpx.Response) -> Any:
    """The response's JSON, or a :class:`VendorError` when it is not JSON."""
    try:
        return response.json()
    except ValueError as exc:
        raise VendorError(f"{vendor} sent an answer that could not be read") from exc
