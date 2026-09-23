"""Outbound network guard: the api's one SSRF defence (V2-21, asks S1).

Every url an admin, builder or credential can put in front of the api process
goes through here: LiveKit connection urls (create, update, both ``test``
routes and every ``ConnectionClientFactory.api`` call), webhook endpoint urls
(save time and every delivery), HTTP-tool dry runs and OpenAI-compatible QA
judge ``base_url`` s.

Rules:

* Addresses in a loopback, private (RFC 1918 / ULA), link-local, CGNAT,
  multicast, reserved or unspecified range are refused, as are ``localhost``,
  ``*.localhost`` and the well-known cloud metadata host names.
* Cloud metadata addresses (``169.254.169.254``, ``fd00:ec2::254``, the ECS
  task endpoint and Alibaba's ``100.100.100.200``) are refused **always**, even
  when an allowlist entry would cover them.
* ``LKAP_NET_ALLOW_PRIVATE_HOSTS`` (comma-separated host names, IPs or CIDRs)
  exempts named destinations from the private-range rule. It defaults to
  ``localhost,127.0.0.1,::1`` in ``LKAP_ENV=dev`` (a self-hosted LiveKit on
  ``ws://localhost:7880``, live stage L14) and to nothing in ``prod``.
* **DNS rebinding.** Names are resolved at connect time and the connection is
  made to the checked address itself: :class:`GuardedTransport` (httpx) and
  :class:`GuardedResolver` (aiohttp, the LiveKit server SDK) never hand a name
  back to the socket layer, so a name that resolves to a public address during
  validation and to ``169.254.169.254`` a second later cannot slip through.

:func:`check_url` is the cheap, offline save-time check (scheme, host, IP
literal, blocked names). The connect-time check is the authoritative one.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Final
from urllib.parse import urlsplit

import aiohttp
import httpcore
import httpx
from aiohttp.abc import AbstractResolver, ResolveResult
from aiohttp.resolver import DefaultResolver

from lkap_api.errors import UnprocessableEntityError
from lkap_api.settings import Settings

__all__ = [
    "DEV_DEFAULT_ALLOW",
    "BlockedDestinationError",
    "GuardedNetworkBackend",
    "GuardedResolver",
    "GuardedTransport",
    "NetPolicy",
    "address_problem",
    "blocked_reason_of",
    "check_url",
    "guarded_aiohttp_session",
    "guarded_http_client",
    "host_problem",
    "policy_from_settings",
    "validate_url",
]

IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IpNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

#: What ``LKAP_NET_ALLOW_PRIVATE_HOSTS`` means when unset in ``LKAP_ENV=dev``.
DEV_DEFAULT_ALLOW: Final[tuple[str, ...]] = ("localhost", "127.0.0.1", "::1")

#: Host names that always point inside the platform's own network.
BLOCKED_HOST_NAMES: Final[frozenset[str]] = frozenset(
    {
        "localhost",
        "metadata",
        "metadata.google.internal",
        "metadata.goog",
        "instance-data",
        "instance-data.ec2.internal",
    }
)

#: Cloud metadata endpoints: refused even when an allowlist entry covers them.
METADATA_ADDRESSES: Final[frozenset[IpAddress]] = frozenset(
    {
        ipaddress.ip_address("169.254.169.254"),  # AWS, GCP, Azure, OCI, DO
        ipaddress.ip_address("169.254.170.2"),  # AWS ECS task metadata
        ipaddress.ip_address("fd00:ec2::254"),  # AWS IMDS over IPv6
        ipaddress.ip_address("100.100.100.200"),  # Alibaba Cloud
    }
)

#: Shared address space (RFC 6598); ``ipaddress`` does not count it as private.
_CGNAT: Final[ipaddress.IPv4Network] = ipaddress.IPv4Network("100.64.0.0/10")

#: Schemes a caller may name, per surface.
HTTP_SCHEMES: Final[frozenset[str]] = frozenset({"http", "https"})
LIVEKIT_SCHEMES: Final[frozenset[str]] = frozenset({"ws", "wss", "http", "https"})


class BlockedDestinationError(OSError):
    """A destination was refused by the network guard (subclass of ``OSError`` for aiohttp)."""


class _BlockedConnectError(httpcore.ConnectError):
    """httpx maps this to :class:`httpx.ConnectError`, which every caller already handles."""


@dataclass(frozen=True)
class NetPolicy:
    """Which private destinations are exempt (everything else private is refused)."""

    allow_hosts: frozenset[str] = frozenset()
    allow_networks: tuple[IpNetwork, ...] = field(default=())

    @classmethod
    def from_entries(cls, entries: Iterable[str]) -> NetPolicy:
        """Build a policy from host names, IP literals and CIDRs (blank entries ignored)."""
        hosts: set[str] = set()
        networks: list[IpNetwork] = []
        for raw in entries:
            entry = raw.strip().lower().strip("[]").rstrip(".")
            if not entry:
                continue
            try:
                networks.append(ipaddress.ip_network(entry, strict=False))
            except ValueError:
                hosts.add(entry)
        return cls(allow_hosts=frozenset(hosts), allow_networks=tuple(networks))

    def host_exempt(self, host: str) -> bool:
        """Whether a host *name* was allowlisted by name."""
        return _normalise_host(host) in self.allow_hosts

    def address_exempt(self, address: IpAddress) -> bool:
        """Whether an address falls in an allowlisted network (metadata never does)."""
        if address in METADATA_ADDRESSES:
            return False
        return any(address in network for network in self.allow_networks)


def policy_from_settings(settings: Settings) -> NetPolicy:
    """The process's policy: ``LKAP_NET_ALLOW_PRIVATE_HOSTS``, or the dev default."""
    raw = settings.net_allow_private_hosts
    if raw is None:
        return NetPolicy.from_entries(DEV_DEFAULT_ALLOW if settings.env == "dev" else ())
    return NetPolicy.from_entries(raw.split(","))


def _normalise_host(host: str) -> str:
    return host.strip().lower().strip("[]").rstrip(".")


def _ip_literal(host: str) -> IpAddress | None:
    try:
        address = ipaddress.ip_address(_normalise_host(host).split("%", 1)[0])
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def blocked_reason_of(address: IpAddress) -> str | None:
    """Why an address is not on the public internet, or ``None`` when it is."""
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if address in METADATA_ADDRESSES:
        return "a cloud metadata address"
    if isinstance(address, ipaddress.IPv4Address) and address in _CGNAT:
        return "a carrier-grade NAT address"
    if address.is_loopback:
        return "a loopback address"
    if address.is_link_local:
        return "a link-local address"
    if address.is_unspecified:
        return "an unspecified address"
    if address.is_multicast:
        return "a multicast address"
    if address.is_private:
        return "a private-network address"
    if address.is_reserved:
        return "a reserved address"
    return None


def address_problem(address: IpAddress, policy: NetPolicy, *, host_exempt: bool = False) -> str | None:
    """Why the guard refuses ``address``, or ``None``.

    Args:
        address: A resolved or literal address.
        policy: The allowlist.
        host_exempt: The *name* it was resolved from is allowlisted (metadata
            addresses are still refused).
    """
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    reason = blocked_reason_of(address)
    if reason is None:
        return None
    if address in METADATA_ADDRESSES:
        return f"{address} is {reason}"
    if host_exempt or policy.address_exempt(address):
        return None
    return f"{address} is {reason}"


def host_problem(host: str, policy: NetPolicy) -> str | None:
    """The offline check of a url host: IP literals and blocked names (no DNS)."""
    literal = _ip_literal(host)
    if literal is not None:
        return address_problem(literal, policy)
    name = _normalise_host(host)
    if policy.host_exempt(name):
        return None
    if name in BLOCKED_HOST_NAMES or name.endswith(".localhost"):
        return f"{name} names the platform's own network"
    return None


def check_url(url: str, policy: NetPolicy, *, schemes: frozenset[str] = HTTP_SCHEMES) -> str | None:
    """Why ``url`` may not be called, or ``None`` (offline: names are not resolved).

    Args:
        url: The absolute url.
        policy: The allowlist.
        schemes: The schemes this surface accepts.
    """
    parsed = urlsplit(url.strip())
    if parsed.scheme.lower() not in schemes:
        return f"unsupported url scheme {parsed.scheme!r}"
    if not parsed.hostname:
        return "the url has no host"
    problem = host_problem(parsed.hostname, policy)
    if problem is None:
        return None
    return (
        f"{problem}; outbound requests to private or local networks are refused "
        "(an operator can allow a host with LKAP_NET_ALLOW_PRIVATE_HOSTS)"
    )


def validate_url(
    url: str, policy: NetPolicy, *, field_name: str, schemes: frozenset[str] = HTTP_SCHEMES
) -> None:
    """Raise a 422 ``destination_blocked``-style error when :func:`check_url` refuses ``url``.

    Raises:
        UnprocessableEntityError: With ``details.field`` and ``details.reason``.
    """
    problem = check_url(url, policy, schemes=schemes)
    if problem is not None:
        raise UnprocessableEntityError(
            problem, details={"field": field_name, "reason": "blocked_destination"}
        )


# --------------------------------------------------------------------------- resolution
async def _getaddrinfo(host: str, port: int) -> list[IpAddress]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses: list[IpAddress] = []
    for _family, _type, _proto, _canon, sockaddr in infos:
        address = ipaddress.ip_address(str(sockaddr[0]).split("%", 1)[0])
        if address not in addresses:
            addresses.append(address)
    return addresses


async def checked_addresses(
    host: str,
    port: int,
    policy: NetPolicy,
    *,
    resolve: Callable[[str, int], Any] = _getaddrinfo,
) -> list[IpAddress]:
    """Resolve ``host`` and return its addresses once **every** one passes the guard.

    One bad address fails the whole name: a round-robin record that mixes a
    public and a metadata address must not be tried until it hits the latter.

    Raises:
        BlockedDestinationError: A blocked name or address.
    """
    literal = _ip_literal(host)
    if literal is not None:
        problem = address_problem(literal, policy)
        if problem is not None:
            raise BlockedDestinationError(f"blocked destination: {problem}")
        return [literal]
    name_problem = host_problem(host, policy)
    if name_problem is not None:
        raise BlockedDestinationError(f"blocked destination: {name_problem}")
    exempt = policy.host_exempt(host)
    addresses: list[IpAddress] = list(await resolve(host, port))
    if not addresses:
        raise BlockedDestinationError(f"blocked destination: {host} did not resolve")
    for address in addresses:
        problem = address_problem(address, policy, host_exempt=exempt)
        if problem is not None:
            raise BlockedDestinationError(f"blocked destination: {host} resolves to {problem}")
    return addresses


# ------------------------------------------------------------------------------ httpx
class GuardedNetworkBackend(httpcore.AsyncNetworkBackend):
    """An httpcore backend that resolves, checks, and connects to the checked address."""

    def __init__(
        self,
        policy: NetPolicy,
        *,
        inner: httpcore.AsyncNetworkBackend | None = None,
        resolve: Callable[[str, int], Any] = _getaddrinfo,
    ) -> None:
        """Wrap ``inner`` (default: httpcore's anyio backend)."""
        self._policy = policy
        self._inner = inner or httpcore.AnyIOBackend()
        self._resolve = resolve

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore's backend interface
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        """Connect to the first reachable checked address of ``host``.

        TLS still verifies against the url's host name: httpcore passes the
        origin host as ``server_hostname`` when it upgrades this stream.
        """
        try:
            addresses = await checked_addresses(host, port, self._policy, resolve=self._resolve)
        except BlockedDestinationError as exc:
            raise _BlockedConnectError(str(exc)) from exc
        except OSError as exc:
            raise httpcore.ConnectError(f"could not resolve {host}: {type(exc).__name__}") from exc
        last: Exception | None = None
        for address in addresses:
            try:
                return await self._inner.connect_tcp(
                    str(address),
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last = exc
        assert last is not None  # noqa: S101 - `addresses` is never empty
        raise last

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore's backend interface
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        """Unix sockets are never an outbound destination."""
        raise _BlockedConnectError("blocked destination: unix sockets are not allowed")

    async def sleep(self, seconds: float) -> None:
        """Delegate to the wrapped backend."""
        await self._inner.sleep(seconds)


class GuardedTransport(httpx.AsyncHTTPTransport):
    """``httpx.AsyncHTTPTransport`` whose connections go through :class:`GuardedNetworkBackend`.

    Passing an explicit transport also switches off httpx's environment proxies
    (``HTTP(S)_PROXY``), so no request can bypass the guard through one.
    """

    def __init__(
        self,
        policy: NetPolicy,
        *,
        resolve: Callable[[str, int], Any] = _getaddrinfo,
        inner: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
        """Build the transport with httpx's default limits and TLS verification."""
        super().__init__()
        limits = httpx.Limits(max_connections=100, max_keepalive_connections=20, keepalive_expiry=5.0)
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(),
            max_connections=limits.max_connections,
            max_keepalive_connections=limits.max_keepalive_connections,
            keepalive_expiry=limits.keepalive_expiry,
            network_backend=GuardedNetworkBackend(policy, inner=inner, resolve=resolve),
        )


def guarded_http_client(policy: NetPolicy, **kwargs: Any) -> httpx.AsyncClient:
    """An ``httpx.AsyncClient`` behind the guard; redirects are never followed."""
    kwargs.setdefault("follow_redirects", False)
    return httpx.AsyncClient(transport=GuardedTransport(policy), **kwargs)


# ---------------------------------------------------------------------------- aiohttp
class GuardedResolver(AbstractResolver):
    """An aiohttp resolver that refuses names resolving to blocked addresses.

    aiohttp connects to exactly the addresses a resolver returns, so the check
    and the connection see the same address. IP-literal hosts never reach a
    resolver in aiohttp: check them with :func:`check_url` before the request.
    """

    def __init__(self, policy: NetPolicy, *, inner: AbstractResolver | None = None) -> None:
        """Wrap ``inner`` (default: aiohttp's default resolver)."""
        self._policy = policy
        self._inner = inner or DefaultResolver()

    async def resolve(
        self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_INET
    ) -> list[ResolveResult]:
        """Resolve with the wrapped resolver, then refuse any blocked address."""
        name_problem = host_problem(host, self._policy)
        if name_problem is not None:
            raise BlockedDestinationError(f"blocked destination: {name_problem}")
        results = await self._inner.resolve(host, port, family)
        exempt = self._policy.host_exempt(host)
        for result in results:
            address = ipaddress.ip_address(str(result["host"]).split("%", 1)[0])
            problem = address_problem(address, self._policy, host_exempt=exempt)
            if problem is not None:
                raise BlockedDestinationError(f"blocked destination: {host} resolves to {problem}")
        return results

    async def close(self) -> None:
        """Close the wrapped resolver."""
        await self._inner.close()


def guarded_aiohttp_session(
    policy: NetPolicy, *, timeout: aiohttp.ClientTimeout, inner_resolver: AbstractResolver | None = None
) -> aiohttp.ClientSession:
    """An aiohttp session whose connector resolves through :class:`GuardedResolver`.

    ``inner_resolver`` replaces aiohttp's default name lookup (tests).
    """
    connector = aiohttp.TCPConnector(resolver=GuardedResolver(policy, inner=inner_resolver))
    return aiohttp.ClientSession(connector=connector, timeout=timeout)


def blocked_cause(exc: BaseException) -> BlockedDestinationError | None:
    """The :class:`BlockedDestinationError` behind a transport error, if any."""
    seen: set[int] = set()
    stack: list[BaseException] = [exc]
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, BlockedDestinationError):
            return current
        nested: Sequence[object] = (
            getattr(current, "os_error", None),
            current.__cause__,
            current.__context__,
        )
        stack.extend(candidate for candidate in nested if isinstance(candidate, BaseException))
    return None
