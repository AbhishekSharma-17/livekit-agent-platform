"""SSRF guards and small helpers shared by the built-in `http_request` tool
(`tools/builtin/http_request.py`) and the declarative HTTP tool builder
(`tools/declarative.py`).

Both callers must also construct their `httpx.AsyncClient` with
`follow_redirects=False` — otherwise a 3xx response from an allowed host could
redirect the client to a disallowed one and this check (which only ever sees
the original URL) would never see it.

**Allowlist semantics (REVIEW-FINAL F-14, PLAN-V2 V2-07).** v1 took the
*union* of a tool's `allowed_hosts` and `LKAP_HTTP_TOOL_ALLOWED_HOSTS`, so an
admin-authored tool could reach any host the platform list did not name. v2
uses *intersection*: every list that is set must contain the host, so a
non-empty platform list is a ceiling no tool can widen. An empty list on one
side imposes nothing (a tool without its own list uses the platform list; a
platform without a list trusts the tool's), and two empty lists allow nothing
(fail closed). The generic built-in `http_request` has no tool list, so only
the platform list applies.

**Private-range deny-list.** Regardless of any allowlist, a URL whose host is
an IP literal in a loopback, private (RFC 1918 / ULA), link-local (including
the `169.254.169.254` cloud metadata address), CGNAT, multicast, reserved or
unspecified range is refused, as are `localhost` and `*.localhost` and the
well-known cloud metadata host names. Host *names* are not resolved here (no
DNS on the hot path, and the callers' test transports never resolve); DNS
rebinding is out of scope for this check.
"""

from __future__ import annotations

import ipaddress
from typing import Final
from urllib.parse import urlsplit

_ALLOWED_SCHEMES = frozenset({"http", "https"})

#: Host names that always point inside the platform's own network.
_DENIED_HOST_NAMES: Final[frozenset[str]] = frozenset(
    {"localhost", "metadata", "metadata.google.internal", "metadata.goog", "instance-data"}
)

#: Shared address space (RFC 6598); `ipaddress` does not count it as private.
_CGNAT: Final[ipaddress.IPv4Network] = ipaddress.IPv4Network("100.64.0.0/10")


class HttpToolSecurityError(Exception):
    """A URL failed the outbound scheme/host allowlist check."""


def _ip_literal(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def is_private_host(host: str) -> bool:
    """Whether `host` names the platform's own network rather than the internet.

    Args:
        host: A URL host (IP literal or name), without a port.

    Returns:
        `True` for loopback/private/link-local/CGNAT/multicast/reserved/
        unspecified IP literals, `localhost` / `*.localhost`, and well-known
        cloud metadata names; `False` for every other name (names are not
        resolved).
    """
    lowered = host.lower().rstrip(".")
    if lowered in _DENIED_HOST_NAMES or lowered.endswith(".localhost"):
        return True
    address = _ip_literal(lowered)
    if address is None:
        return False
    if isinstance(address, ipaddress.IPv4Address) and address in _CGNAT:
        return True
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def effective_allowlist(
    tool_allowed_hosts: list[str] | None, platform_allowed_hosts: list[str] | None
) -> set[str]:
    """The hosts a tool may call: the intersection of every non-empty list.

    Args:
        tool_allowed_hosts: The tool definition's own `allowed_hosts`, if any.
        platform_allowed_hosts: `LKAP_HTTP_TOOL_ALLOWED_HOSTS`, if any.

    Returns:
        Lower-cased host names; empty when both lists are empty.
    """
    tool = {h.strip().lower() for h in (tool_allowed_hosts or []) if h.strip()}
    platform = {h.strip().lower() for h in (platform_allowed_hosts or []) if h.strip()}
    if tool and platform:
        return tool & platform
    return tool or platform


def check_url_allowed(
    url: str,
    *,
    tool_allowed_hosts: list[str] | None,
    platform_allowed_hosts: list[str] | None,
) -> None:
    """Reject `url` unless it is http(s), public, and on the effective allowlist.

    Args:
        url: The fully-rendered request URL (after template substitution).
        tool_allowed_hosts: The tool definition's own `allowed_hosts`, if any.
        platform_allowed_hosts: `LKAP_HTTP_TOOL_ALLOWED_HOSTS`, if any.

    Raises:
        HttpToolSecurityError: If the scheme is not http/https, the URL has no
            host, the host is in a private range (even when allowlisted), or it
            is not on :func:`effective_allowlist`.
    """
    parsed = urlsplit(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise HttpToolSecurityError(f"unsupported URL scheme: {parsed.scheme!r}")

    host = parsed.hostname
    if not host:
        raise HttpToolSecurityError(f"URL has no host: {url!r}")

    if is_private_host(host):
        raise HttpToolSecurityError(f"host {host!r} is in a private or local network range")

    allowed = effective_allowlist(tool_allowed_hosts, platform_allowed_hosts)
    if host.lower() not in allowed:
        raise HttpToolSecurityError(f"host {host!r} is not on the outbound allowlist")


def truncate(text: str, max_chars: int) -> str:
    """Truncate `text` to `max_chars`, appending a marker if it was cut."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "... [truncated]"
