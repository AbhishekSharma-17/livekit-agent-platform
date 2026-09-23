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
well-known cloud metadata host names. :func:`check_url_allowed` does not
resolve names; that happens at connect time.

**DNS rebinding and names that resolve inward (V2-21).** An allowlisted *name*
can resolve to `169.254.169.254` (e.g. a `nip.io`-style record) or change its
answer between the check and the request. Both callers therefore build their
client with :func:`guarded_transport`, whose network backend resolves the
name, refuses it if **any** address is private, and connects to the checked
address itself, so the answer that was checked is the one that is used.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Callable, Iterable
from typing import Any, Final
from urllib.parse import urlsplit

import httpcore
import httpx

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


async def _getaddrinfo(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses: list[str] = []
    for _family, _type, _proto, _canon, sockaddr in infos:
        address = str(sockaddr[0]).split("%", 1)[0]
        if address not in addresses:
            addresses.append(address)
    return addresses


class _BlockedConnectError(httpcore.ConnectError):
    """Mapped by httpx to :class:`httpx.ConnectError` (the callers turn it into a `ToolError`)."""


class GuardedNetworkBackend(httpcore.AsyncNetworkBackend):
    """Resolve, refuse private answers, and connect to the checked address."""

    def __init__(
        self,
        *,
        resolve: Callable[[str, int], Any] = _getaddrinfo,
        inner: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
        """Wrap `inner` (default: httpcore's anyio backend) with the private-range check."""
        self._resolve = resolve
        self._inner = inner or httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore's backend interface
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        """Connect to the first reachable address of `host`, once every address is public."""
        if is_private_host(host):
            raise _BlockedConnectError(f"host {host!r} is in a private or local network range")
        if _ip_literal(host) is not None:
            addresses = [host.strip("[]")]
        else:
            try:
                addresses = list(await self._resolve(host, port))
            except OSError as exc:
                raise httpcore.ConnectError(f"could not resolve {host!r}: {type(exc).__name__}") from exc
            inward = [address for address in addresses if is_private_host(address)]
            if inward or not addresses:
                raise _BlockedConnectError(
                    f"host {host!r} resolves to a private or local network address"
                    if inward
                    else f"host {host!r} did not resolve"
                )
        last: Exception | None = None
        for address in addresses:
            try:
                return await self._inner.connect_tcp(
                    address, port, timeout=timeout, local_address=local_address, socket_options=socket_options
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last = exc
        assert last is not None  # noqa: S101 - `addresses` is never empty here
        raise last

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore's backend interface
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        """Unix sockets are never a tool destination."""
        raise _BlockedConnectError("unix sockets are not an allowed destination")

    async def sleep(self, seconds: float) -> None:
        """Delegate to the wrapped backend."""
        await self._inner.sleep(seconds)


class GuardedTransport(httpx.AsyncHTTPTransport):
    """An httpx transport whose every connection goes through :class:`GuardedNetworkBackend`.

    An explicit transport also turns off httpx's `HTTP(S)_PROXY` handling, so a
    proxy can never carry a tool request around the check.
    """

    def __init__(
        self,
        *,
        resolve: Callable[[str, int], Any] = _getaddrinfo,
        inner: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
        """Build the transport with httpx's default limits and TLS verification."""
        super().__init__()
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(),
            max_connections=100,
            max_keepalive_connections=20,
            keepalive_expiry=5.0,
            network_backend=GuardedNetworkBackend(resolve=resolve, inner=inner),
        )


def guarded_transport() -> GuardedTransport:
    """The transport every HTTP tool client must use (see the module docstring)."""
    return GuardedTransport()


def truncate(text: str, max_chars: int) -> str:
    """Truncate `text` to `max_chars`, appending a marker if it was cut."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "... [truncated]"
