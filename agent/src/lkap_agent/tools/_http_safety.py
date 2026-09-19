"""SSRF guards and small helpers shared by the built-in `http_request` tool
(`tools/builtin/http_request.py`) and the declarative HTTP tool builder
(`tools/declarative.py`).

docs/ARCHITECTURE.md §7.1: "Outbound allowlist: URL host must match
`tool.definition.allowed_hosts` or the platform
`LKAP_HTTP_TOOL_ALLOWED_HOSTS`." Both callers must also construct their
`httpx.AsyncClient` with `follow_redirects=False` — otherwise a 3xx response
from an allowed host could redirect the client to a disallowed one and this
allowlist check (which only ever sees the original URL) would never see it.
"""

from __future__ import annotations

from urllib.parse import urlsplit

_ALLOWED_SCHEMES = frozenset({"http", "https"})


class HttpToolSecurityError(Exception):
    """A URL failed the outbound scheme/host allowlist check."""


def check_url_allowed(
    url: str,
    *,
    tool_allowed_hosts: list[str] | None,
    platform_allowed_hosts: list[str] | None,
) -> None:
    """Reject `url` unless its host is on the tool or platform allowlist.

    Per docs/CONTRACTS.md §3 and §9, the effective allowlist is the *union*
    of the tool's own `allowed_hosts` and `LKAP_HTTP_TOOL_ALLOWED_HOSTS`: an
    empty list on either side simply contributes nothing, so two empty lists
    allow nothing (fail closed) rather than everything.

    Args:
        url: The fully-rendered request URL (after template substitution).
        tool_allowed_hosts: The tool definition's own `allowed_hosts`, if any.
        platform_allowed_hosts: `LKAP_HTTP_TOOL_ALLOWED_HOSTS`, if any.

    Raises:
        HttpToolSecurityError: If the scheme is not http/https, the URL has
            no host, or the host is on neither allowlist.
    """
    parsed = urlsplit(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise HttpToolSecurityError(f"unsupported URL scheme: {parsed.scheme!r}")

    host = parsed.hostname
    if not host:
        raise HttpToolSecurityError(f"URL has no host: {url!r}")

    allowed = {h.lower() for h in (tool_allowed_hosts or [])} | {
        h.lower() for h in (platform_allowed_hosts or [])
    }
    if host.lower() not in allowed:
        raise HttpToolSecurityError(f"host {host!r} is not on the outbound allowlist")


def truncate(text: str, max_chars: int) -> str:
    """Truncate `text` to `max_chars`, appending a marker if it was cut."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "... [truncated]"
