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
* **Redirects are never followed** (V2-22, R2-38): httpx clients are built with
  ``follow_redirects=False`` and the aiohttp session forces
  ``allow_redirects=False`` on every request; the aiohttp connector also checks
  IP-literal hosts itself (:class:`GuardedTCPConnector`), because aiohttp never
  hands a literal to a resolver.
* **Numeric-looking names** (V2-22, R2-39): a host that is not a canonical IP
  literal but whose last label is all digits or a ``0x`` number
  (``2130706433``, ``0x7f000001``, ``127.1``, ``0``) is refused offline; the
  resolver would turn it into an address (``127.0.0.1``) and no public TLD is
  numeric (RFC 1123).

:func:`check_url` is the cheap, offline save-time check (scheme, host, IP
literal, blocked names). The connect-time check is the authoritative one.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import warnings
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Final
from urllib.parse import urlsplit

import aiohttp
import httpcore
import httpx
from aiohttp.abc import AbstractResolver, ResolveResult
from aiohttp.resolver import DefaultResolver
from aiohttp.tracing import Trace

from lkap_api.errors import UnprocessableEntityError
from lkap_api.settings import Settings

__all__ = [
    "DEV_DEFAULT_ALLOW",
    "BlockedDestinationError",
    "GuardedNetworkBackend",
    "GuardedResolver",
    "GuardedTCPConnector",
    "GuardedTransport",
    "McpPolicy",
    "NoRedirectClientSession",
    "NetPolicy",
    "address_problem",
    "blocked_reason_of",
    "check_url",
    "guarded_aiohttp_session",
    "guarded_http_client",
    "host_problem",
    "mcp_policy",
    "numeric_host",
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


def numeric_host(host: str) -> bool:
    """Whether a host that is *not* a canonical IP literal still reads as a number.

    ``2130706433``, ``0x7f000001``, ``127.1`` and ``0`` are names to
    :mod:`ipaddress` but addresses to ``getaddrinfo`` (``inet_aton`` forms). A
    host whose last label is all digits or a ``0x`` hex number is one of those:
    no public top-level domain is numeric (RFC 1123), so nothing legitimate is
    refused (V2-22, R2-39).
    """
    name = _normalise_host(host)
    if not name or ":" in name or _ip_literal(name) is not None:
        return False
    last = name.rsplit(".", 1)[-1]
    return last.isdigit() or (last.startswith("0x") and len(last) > 2)


def host_problem(host: str, policy: NetPolicy) -> str | None:
    """The offline check of a url host: IP literals and blocked names (no DNS)."""
    literal = _ip_literal(host)
    if literal is not None:
        return address_problem(literal, policy)
    name = _normalise_host(host)
    if numeric_host(name):
        return f"{name} is a numeric address in a non-canonical form"
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


#: ``LKAP_MCP_ALLOWED_HOSTS`` value that reuses the HTTP-tool list (D-V5-4's override).
MCP_HOSTS_HTTP_ALIAS: Final[str] = "@http"


@dataclass(frozen=True)
class McpPolicy:
    """Where an MCP server may live (V5-09, D-V5-4): the guard, ``https``, and an optional ceiling."""

    net: NetPolicy
    allowed_hosts: frozenset[str] = frozenset()
    """``LKAP_MCP_ALLOWED_HOSTS``; a non-empty set is a ceiling."""
    fail_closed: bool = False
    """``@http``: the HTTP-tool list is the ceiling and, as for HTTP tools, empty allows nothing."""
    allow_http_loopback: bool = False
    """``LKAP_ENV=dev``: plain ``http`` is allowed to a loopback host (a local test server)."""

    def problem(self, url: str) -> str | None:
        """Why the api may not connect to the MCP server at ``url``, or ``None`` (offline check).

        The reason names the host or the setting, never the url's path or query.
        """
        base = check_url(url, self.net)
        if base is not None:
            return base
        parsed = urlsplit(url.strip())
        host = _normalise_host(parsed.hostname or "")
        if parsed.scheme.lower() != "https" and not (self.allow_http_loopback and _loopback_host(host)):
            return "an MCP server must use https (plain http only reaches a loopback host in LKAP_ENV=dev)"
        if (self.allowed_hosts or self.fail_closed) and host not in self.allowed_hosts:
            return f"{host} is not on LKAP_MCP_ALLOWED_HOSTS"
        return None


def _loopback_host(host: str) -> bool:
    literal = _ip_literal(host)
    if literal is not None:
        return literal.is_loopback
    return host == "localhost" or host.endswith(".localhost")


def _host_list(raw: str) -> frozenset[str]:
    return frozenset(_normalise_host(h) for h in raw.split(",") if h.strip())


def mcp_policy(settings: Settings) -> McpPolicy:
    """The MCP host policy: the process's :class:`NetPolicy`, ``https``, ``LKAP_MCP_ALLOWED_HOSTS``."""
    raw = settings.mcp_allowed_hosts.strip()
    if raw == MCP_HOSTS_HTTP_ALIAS:
        hosts, fail_closed = _host_list(settings.http_tool_allowed_hosts), True
    else:
        hosts, fail_closed = _host_list(raw), False
    return McpPolicy(
        net=policy_from_settings(settings),
        allowed_hosts=hosts,
        fail_closed=fail_closed,
        allow_http_loopback=settings.env == "dev",
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
    resolver in aiohttp: :class:`GuardedTCPConnector` checks those.
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


class GuardedTCPConnector(aiohttp.TCPConnector):
    """A ``TCPConnector`` that also checks IP-literal hosts (V2-22, R2-38).

    aiohttp's ``_resolve_host`` returns an IP-literal host as it is, without
    asking the resolver, so :class:`GuardedResolver` never sees one: a redirect
    (or a stored row) naming ``http://127.0.0.1/`` would connect unchecked. This
    connector runs every literal through :func:`address_problem` first and
    raises :class:`BlockedDestinationError` (an ``OSError``, which aiohttp
    reports as ``ClientConnectorError``; :func:`blocked_cause` finds it).
    """

    def __init__(self, policy: NetPolicy, *, resolver: AbstractResolver, **kwargs: Any) -> None:
        """Build the connector with the guard's policy and resolver."""
        super().__init__(resolver=resolver, **kwargs)
        self._guard_policy = policy

    async def _resolve_host(
        self, host: str, port: int, traces: Sequence[Trace] | None = None
    ) -> list[ResolveResult]:
        literal = _ip_literal(host)
        if literal is not None:
            problem = address_problem(literal, self._guard_policy)
            if problem is not None:
                raise BlockedDestinationError(f"blocked destination: {problem}")
        return await super()._resolve_host(host, port, traces)


#: Redirect statuses aiohttp would follow.
_REDIRECT_STATUSES: Final[frozenset[int]] = frozenset({301, 302, 303, 307, 308})

with warnings.catch_warnings():
    # aiohttp discourages subclassing ClientSession with a DeprecationWarning at
    # class-definition time; overriding `_request` is the one way to force the
    # redirect policy for every request a third-party client (LiveKit's Twirp
    # client) makes through the session.
    warnings.simplefilter("ignore", DeprecationWarning)

    class NoRedirectClientSession(aiohttp.ClientSession):
        """A ``ClientSession`` that never follows a redirect (V2-22, R2-38).

        ``allow_redirects=False`` is forced on every request, whatever the
        caller asks for. Not ``max_redirects=0``: aiohttp 3.14 reads that as
        unlimited (``if max_redirects and redirects >= max_redirects``). A
        redirect answer is then released and raised as
        :class:`BlockedDestinationError`: no LiveKit API call is ever
        redirected, and the probe can say why it failed instead of relaying a
        Twirp parse error. The ``Location`` is not repeated in the message.
        """

        async def _request(
            self, method: str, str_or_url: Any, *, allow_redirects: bool = False, **kwargs: Any
        ) -> aiohttp.ClientResponse:
            del allow_redirects
            response = await super()._request(method, str_or_url, allow_redirects=False, **kwargs)
            if response.status in _REDIRECT_STATUSES:
                response.release()
                raise BlockedDestinationError(
                    f"blocked destination: {response.url.host} answered {response.status} "
                    "with a redirect, and redirects are never followed"
                )
            return response


def guarded_aiohttp_session(
    policy: NetPolicy, *, timeout: aiohttp.ClientTimeout, inner_resolver: AbstractResolver | None = None
) -> aiohttp.ClientSession:
    """An aiohttp session behind the guard: checked literals, a guarded resolver, no redirects.

    ``inner_resolver`` replaces aiohttp's default name lookup (tests).
    """
    connector = GuardedTCPConnector(policy, resolver=GuardedResolver(policy, inner=inner_resolver))
    return NoRedirectClientSession(connector=connector, timeout=timeout)


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
