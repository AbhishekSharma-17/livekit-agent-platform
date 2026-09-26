"""Outbound calls of the MCP sign-in: URL checks, bounded reads, value-free errors (V5-14).

Every URL the sign-in touches that a server supplied (``resource_metadata``,
``authorization_servers[]``, each metadata document, ``registration_endpoint``,
``token_endpoint``, ``revocation_endpoint``) passes :func:`url_problem` **before**
it is fetched: the network guard's offline check (private, loopback, metadata and
numeric hosts refused; ``LKAP_NET_ALLOW_PRIVATE_HOSTS`` honoured) plus ``https``,
with plain ``http`` only to a loopback host in ``LKAP_ENV=dev`` (the spec requires
HTTPS authorization-server endpoints). The connect-time guard of
:func:`lkap_api.net_guard.guarded_http_client` then re-checks every resolved
address. Redirects are never followed: a 3xx is reported, not chased.

Responses are read through a stream capped at :data:`MAX_BODY_BYTES`, so a hostile
server cannot make the api buffer an unbounded body, and no response body, header
value or URL query ever reaches an error message or a log line.
"""

from __future__ import annotations

import ipaddress
import json
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import urlsplit

import httpx

from lkap_api import net_guard
from lkap_api.settings import Settings

#: A response body larger than this is refused (metadata, registration and token answers are small).
MAX_BODY_BYTES: Final[int] = 64_000
#: Each outbound request of the sign-in gives up after this many seconds.
REQUEST_TIMEOUT_S: Final[float] = 10.0


class McpOauthError(Exception):
    """A sign-in step failed; ``reason`` is machine-readable and the message names no secret."""

    def __init__(self, reason: str, message: str, *, field: str | None = None) -> None:
        """Keep the reason (and the offending field, when there is one) next to the message."""
        super().__init__(message)
        self.reason = reason
        self.field = field


@dataclass(frozen=True)
class UrlPolicy:
    """Where the sign-in may send requests: the network guard plus ``https``."""

    net: net_guard.NetPolicy
    allow_http_loopback: bool = False

    @classmethod
    def from_settings(cls, settings: Settings) -> UrlPolicy:
        """The process policy; plain ``http`` reaches a loopback host only in ``LKAP_ENV=dev``."""
        return cls(net=net_guard.policy_from_settings(settings), allow_http_loopback=settings.env == "dev")


def loopback_host(host: str) -> bool:
    """Whether ``host`` is ``localhost`` (or ``*.localhost``) or a loopback literal."""
    name = host.strip().lower().strip("[]").rstrip(".")
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return name == "localhost" or name.endswith(".localhost")


def url_problem(url: str, policy: UrlPolicy) -> str | None:
    """Why the sign-in may not use ``url``, or ``None`` (offline; the reason never echoes the url)."""
    if len(url) > 2000:
        return "the url is too long"
    base = net_guard.check_url(url, policy.net)
    if base is not None:
        return base
    parsed = urlsplit(url.strip())
    if parsed.username is not None or parsed.password is not None:
        return "the url carries credentials"
    if parsed.fragment:
        return "the url has a fragment"
    if parsed.scheme.lower() != "https" and not (
        policy.allow_http_loopback and loopback_host(parsed.hostname or "")
    ):
        return "sign-in endpoints must use https (plain http only reaches a loopback host in LKAP_ENV=dev)"
    return None


def require_url(url: object, policy: UrlPolicy, *, field: str) -> str:
    """``url`` as a string when :func:`url_problem` accepts it.

    Raises:
        McpOauthError: ``blocked_destination`` naming ``field`` (never the url).
    """
    if not isinstance(url, str) or not url:
        raise McpOauthError("invalid_metadata", f"the sign-in provider sent no usable {field}", field=field)
    problem = url_problem(url, policy)
    if problem is not None:
        raise McpOauthError("blocked_destination", f"{field}: {problem}", field=field)
    return url


def host_of(url: str) -> str:
    """The host of ``url`` (for audit rows and fingerprints; never the path or query)."""
    return (urlsplit(url).hostname or "").lower()


@dataclass(frozen=True)
class Fetched:
    """A bounded response: status, the headers the sign-in reads, the raw body."""

    status: int
    headers: httpx.Headers
    body: bytes

    def json_object(self) -> dict[str, Any] | None:
        """The body as a JSON object, or ``None`` when it is not one."""
        try:
            parsed = json.loads(self.body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        return parsed if isinstance(parsed, dict) else None


async def fetch(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    data: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    auth: tuple[str, str] | None = None,
) -> Fetched:
    """One request of the sign-in, never following a redirect, reading at most :data:`MAX_BODY_BYTES`.

    The caller has already passed ``url`` through :func:`require_url`.

    Raises:
        McpOauthError: ``blocked_destination`` (the connect-time guard refused an address),
            ``unreachable`` (network error or timeout) or ``response_too_large``.
    """
    try:
        async with client.stream(
            method,
            url,
            headers={"Accept": "application/json", **(headers or {})},
            data=data,
            json=json_body,
            auth=httpx.BasicAuth(*auth) if auth is not None else httpx.USE_CLIENT_DEFAULT,
            timeout=REQUEST_TIMEOUT_S,
            follow_redirects=False,
        ) as response:
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_BODY_BYTES:
                    raise McpOauthError("response_too_large", "the sign-in provider's answer is too large")
                chunks.append(chunk)
            return Fetched(status=response.status_code, headers=response.headers, body=b"".join(chunks))
    except httpx.TimeoutException as exc:
        raise McpOauthError("unreachable", "the sign-in provider did not answer in time") from exc
    except httpx.HTTPError as exc:
        blocked = net_guard.blocked_cause(exc)
        if blocked is not None:
            raise McpOauthError("blocked_destination", str(blocked)) from exc
        raise McpOauthError(
            "unreachable", f"could not reach the sign-in provider ({type(exc).__name__})"
        ) from exc


__all__ = [
    "MAX_BODY_BYTES",
    "REQUEST_TIMEOUT_S",
    "Fetched",
    "McpOauthError",
    "UrlPolicy",
    "fetch",
    "host_of",
    "loopback_host",
    "require_url",
    "url_problem",
]
