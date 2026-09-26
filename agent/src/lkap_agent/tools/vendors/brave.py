"""Brave Search (registry ``brave-search``, D-V5-7): ``GET https://api.search.brave.com/res/v1/web/search``.

Request shape as documented by Brave (``q``, ``count``, optional ``country``,
the key in ``X-Subscription-Token``); the response's ``web.results[]`` carry
``title``, ``url`` and ``description`` (which may hold ``<strong>`` markup,
stripped here). Pinned by ``respx`` fixtures; unverified against the live
service until V5-25's live check.
"""

from __future__ import annotations

import html
import re
from typing import Any, Final

import httpx

from lkap_agent.tools.vendors import DEFAULT_TIMEOUT_S, SearchHit, VendorError, json_body, vendor_call

BRAVE_SEARCH_URL: Final[str] = "https://api.search.brave.com/res/v1/web/search"
VENDOR: Final[str] = "Brave Search"

_TAG_RE = re.compile(r"<[^>]+>")
_COUNTRY_RE = re.compile(r"^[A-Za-z]{2}$")


def _plain(text: object) -> str:
    """Drop markup and entities from a vendor string."""
    return html.unescape(_TAG_RE.sub("", str(text or ""))).strip()


async def search(
    query: str,
    *,
    api_key: str,
    max_results: int = 3,
    country: str | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[SearchHit]:
    """Search the web with Brave.

    Args:
        query: What to search for.
        api_key: The workspace's Brave Search subscription token.
        max_results: How many results to ask for.
        country: A two-letter country code the results favour; anything else is left out.
        timeout_s: The call's timeout.
        transport: A test seam (see :func:`~lkap_agent.tools.vendors.vendor_call`).

    Returns:
        Up to ``max_results`` hits, in the vendor's order.

    Raises:
        VendorError: The call failed or the answer had no ``web.results`` list.
    """
    if not api_key:
        raise VendorError(f"{VENDOR} has no key")
    params: dict[str, str | int] = {"q": query, "count": max_results}
    if country and _COUNTRY_RE.match(country.strip()):
        params["country"] = country.strip().lower()
    response = await vendor_call(
        VENDOR,
        "GET",
        BRAVE_SEARCH_URL,
        params=params,
        headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
        timeout_s=timeout_s,
        transport=transport,
    )
    payload: Any = json_body(VENDOR, response)
    web = payload.get("web") if isinstance(payload, dict) else None
    results = web.get("results") if isinstance(web, dict) else None
    if results is None and isinstance(payload, dict):
        return []  # Brave leaves `web` out when nothing matched
    if not isinstance(results, list):
        raise VendorError(f"{VENDOR} sent an answer without results")
    hits: list[SearchHit] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        hits.append(
            SearchHit(
                title=_plain(item.get("title")),
                url=str(item.get("url") or "").strip(),
                snippet=_plain(item.get("description")),
            )
        )
    return hits[:max_results]
