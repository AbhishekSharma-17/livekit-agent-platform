"""The tool-provider adapter interface (docs/v5/COMPOSIO.md §4, D-V5-1).

One adapter per vendor. :class:`~lkap_api.tool_providers.composio.ComposioAdapter`
is the only real one; ``api/tests/fakes/composio.py`` is the offline double.
Every method returns the vendor's JSON object as a plain ``dict`` (the service
parses it leniently) and raises a :class:`ToolProviderError` subclass on
failure. No method, error or log line ever carries the API key or a value
from ``fields``.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, Protocol

#: Longest vendor error text passed on to a caller.
MAX_ERROR_CHARS = 200

_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


def scrub_vendor_text(text: object, *, limit: int = MAX_ERROR_CHARS) -> str:
    """Vendor text made safe to show: URLs removed, whitespace collapsed, capped (D-V5-C9)."""
    cleaned = _URL_RE.sub("[link removed]", str(text or ""))
    cleaned = " ".join(cleaned.split())
    return cleaned[:limit]


class ToolProviderError(Exception):
    """A vendor call failed. ``message`` is scrubbed vendor text, safe to show."""

    reason = "error"

    def __init__(self, message: str, *, status: int | None = None, vendor_code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.vendor_code = vendor_code


class ToolProviderAuthError(ToolProviderError):
    """The key was rejected (401/403)."""

    reason = "unauthorized"


class ToolProviderNotFoundError(ToolProviderError):
    """The vendor has no such object (404)."""

    reason = "not_found"


class ToolProviderRateLimitedError(ToolProviderError):
    """The vendor is rate limiting us (429)."""

    reason = "rate_limited"


class ToolProviderRequestError(ToolProviderError):
    """The vendor refused the request's content (400/409/422)."""

    reason = "bad_request"


class ToolProviderUnavailableError(ToolProviderError):
    """The vendor could not be reached, timed out or answered 5xx."""

    reason = "unavailable"


class ToolProviderAdapter(Protocol):
    """What LKAP needs from a tool provider. ``subject`` is the vendor's ``user_id``."""

    async def session_info(self) -> dict[str, Any]:
        """The key's project/organisation (used by the key test)."""
        ...

    async def list_auth_configs(
        self, *, toolkit: str | None = None, composio_managed: bool | None = None, limit: int = 50
    ) -> dict[str, Any]:
        """The project's auth configs, optionally for one toolkit."""
        ...

    async def list_toolkits(
        self,
        *,
        search: str | None = None,
        category: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """A page of toolkits (apps)."""
        ...

    async def get_toolkit(self, slug: str) -> dict[str, Any]:
        """One toolkit, with its auth schemes."""
        ...

    async def list_tools(
        self,
        *,
        toolkit: str | None = None,
        search: str | None = None,
        important: bool = False,
        tool_slugs: list[str] | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """A page of tools (actions)."""
        ...

    async def create_auth_config(
        self,
        *,
        toolkit: str,
        managed: bool,
        auth_scheme: str | None = None,
        credentials: dict[str, str] | None = None,
        name: str | None = None,
    ) -> dict[str, Any]:
        """Create an auth config: Composio's shared app (``managed``) or the caller's own."""
        ...

    async def start_link(
        self, *, auth_config_id: str, subject: str, callback_url: str, alias: str | None = None
    ) -> dict[str, Any]:
        """Start a hosted sign-in; returns ``redirect_url`` and ``connected_account_id``.

        ``alias`` names the account at the vendor (unique per subject and app, R-V5-13).
        """
        ...

    async def create_with_key(
        self,
        *,
        auth_config_id: str,
        subject: str,
        auth_scheme: str,
        fields: dict[str, str],
        alias: str | None = None,
    ) -> dict[str, Any]:
        """Create a connected account from key fields (forwarded once, never kept)."""
        ...

    async def update_connection(self, connected_account_id: str, *, alias: str) -> dict[str, Any]:
        """Rename a connected account at the vendor (its ``alias``; R-V5-13)."""
        ...

    async def get_connection(self, connected_account_id: str) -> dict[str, Any]:
        """One connected account (``status``, ``user_id``)."""
        ...

    async def delete_connection(self, connected_account_id: str) -> None:
        """Delete a connected account at the vendor."""
        ...

    async def execute(
        self,
        tool_slug: str,
        *,
        subject: str,
        connected_account_id: str | None,
        arguments: dict[str, Any],
        version: str | None = None,
    ) -> dict[str, Any]:
        """Run one action (V5-47 uses it; the worker has its own copy)."""
        ...

    async def create_mcp_server(
        self, *, name: str, auth_config_ids: list[str], allowed_tools: list[str] | None = None
    ) -> dict[str, Any]:
        """Create an app server (MCP) config (V5-47)."""
        ...

    async def delete_mcp_server(self, server_id: str) -> None:
        """Delete an app server config (V5-47)."""
        ...

    async def create_router_session(self, *, subject: str, options: dict[str, Any]) -> dict[str, Any]:
        """Create a tool-finder (Tool Router) session (V5-47)."""
        ...

    async def delete_router_session(self, session_id: str) -> None:
        """Delete a tool-finder session (V5-47)."""
        ...


#: Builds an adapter for one API key (routes depend on this, tests override it).
AdapterFactory = Callable[[str], ToolProviderAdapter]


__all__ = [
    "MAX_ERROR_CHARS",
    "AdapterFactory",
    "ToolProviderAdapter",
    "ToolProviderAuthError",
    "ToolProviderError",
    "ToolProviderNotFoundError",
    "ToolProviderRateLimitedError",
    "ToolProviderRequestError",
    "ToolProviderUnavailableError",
    "scrub_vendor_text",
]
