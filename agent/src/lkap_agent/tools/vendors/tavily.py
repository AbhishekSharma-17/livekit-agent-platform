"""Tavily web search (registry ``tavily-search``, D-V5-7): ``POST https://api.tavily.com/search``.

Request shape as documented by Tavily (``query``, ``max_results``,
``search_depth``, bearer key); the response's ``results[]`` carry ``title``,
``url`` and ``content``. Pinned by ``respx`` fixtures; unverified against the
live service until V5-25's live check.
"""

from __future__ import annotations

from typing import Any, Final, Literal

import httpx

from lkap_agent.tools.vendors import DEFAULT_TIMEOUT_S, SearchHit, VendorError, json_body, vendor_call

TAVILY_SEARCH_URL: Final[str] = "https://api.tavily.com/search"
VENDOR: Final[str] = "Tavily"

SearchDepth = Literal["basic", "advanced"]


async def search(
    query: str,
    *,
    api_key: str,
    max_results: int = 3,
    search_depth: str = "basic",
    timeout_s: float = DEFAULT_TIMEOUT_S,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[SearchHit]:
    """Search the web with Tavily.

    Args:
        query: What to search for.
        api_key: The workspace's Tavily key.
        max_results: How many results to ask for.
        search_depth: ``basic`` (default) or ``advanced``; anything else is sent as ``basic``.
        timeout_s: The call's timeout.
        transport: A test seam (see :func:`~lkap_agent.tools.vendors.vendor_call`).

    Returns:
        Up to ``max_results`` hits, in the vendor's order.

    Raises:
        VendorError: The call failed or the answer had no ``results`` list.
    """
    if not api_key:
        raise VendorError(f"{VENDOR} has no key")
    depth = search_depth if search_depth in ("basic", "advanced") else "basic"
    response = await vendor_call(
        VENDOR,
        "POST",
        TAVILY_SEARCH_URL,
        json={
            "query": query,
            "max_results": max_results,
            "search_depth": depth,
            "include_answer": False,
            "include_images": False,
        },
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout_s=timeout_s,
        transport=transport,
    )
    payload: Any = json_body(VENDOR, response)
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        raise VendorError(f"{VENDOR} sent an answer without results")
    hits: list[SearchHit] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        hits.append(
            SearchHit(
                title=str(item.get("title") or "").strip(),
                url=str(item.get("url") or "").strip(),
                snippet=str(item.get("content") or "").strip(),
            )
        )
    return hits[:max_results]
