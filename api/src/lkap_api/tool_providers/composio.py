"""Composio over REST (docs/v5/COMPOSIO.md §1, verified 2026-09-25; brief ``docs/v5/_briefs/v5-18-live.md``).

Base ``https://backend.composio.dev/api/v3.1``, header ``x-api-key`` on every
call. The HTTP client is the caller's: routes pass the request's
:func:`lkap_api.net_guard.guarded_http_client` (private destinations refused
at connect time, redirects never followed), and tests pass one backed by
``httpx.MockTransport``. Nothing here logs a request body, the key or a
vendor URL; errors carry scrubbed vendor text only.

Wire shapes that differ from the SDK's names: a custom auth config sends
``authScheme`` (camel case) inside ``auth_config``; a key-based connection is
``POST /connected_accounts`` with ``connection.state = {authScheme, val:
{status: "ACTIVE", …fields}}`` (``val.status`` is required).
"""

from __future__ import annotations

from typing import Any, Final
from urllib.parse import quote

import httpx
from lkap_contracts.tool_providers import COMPOSIO_HOST

from lkap_api.logging import get_logger
from lkap_api.tool_providers.adapter import (
    ToolProviderAuthError,
    ToolProviderError,
    ToolProviderNotFoundError,
    ToolProviderRateLimitedError,
    ToolProviderRequestError,
    ToolProviderUnavailableError,
    scrub_vendor_text,
)

log = get_logger(__name__)

#: The REST base every call uses.
BASE_URL: Final = f"https://{COMPOSIO_HOST}/api/v3.1"

#: Per-request timeout (CONTRACTS-V2 D-V2-9: vendor calls get 10 s).
TIMEOUT_S: Final = 10.0

#: The redirect URI a custom OAuth app must register with its vendor (Composio's docs).
OAUTH_REDIRECT_URI: Final = f"https://{COMPOSIO_HOST}/api/v3/toolkits/auth/callback"


def _seg(value: str) -> str:
    return quote(value, safe="")


def _clean(params: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in params.items():
        if value is None or value == "" or value == []:
            continue
        out[key] = ("true" if value else "false") if isinstance(value, bool) else value
    return out


def _error_for(response: httpx.Response) -> ToolProviderError:
    """Map a vendor error response to a scrubbed, typed error."""
    message = f"Composio answered HTTP {response.status_code}"
    vendor_code: str | None = None
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        error = body.get("error", body)
        if isinstance(error, dict):
            text = error.get("message") or error.get("detail")
            if text:
                message = scrub_vendor_text(text)
            slug = error.get("slug") or error.get("code")
            vendor_code = str(slug)[:64] if slug is not None else None
        elif isinstance(error, str):
            message = scrub_vendor_text(error)
    status = response.status_code
    if status in (401, 403):
        return ToolProviderAuthError(message, status=status, vendor_code=vendor_code)
    if status == 404:
        return ToolProviderNotFoundError(message, status=status, vendor_code=vendor_code)
    if status == 429:
        return ToolProviderRateLimitedError(message, status=status, vendor_code=vendor_code)
    if 400 <= status < 500:
        return ToolProviderRequestError(message, status=status, vendor_code=vendor_code)
    return ToolProviderUnavailableError(message, status=status, vendor_code=vendor_code)


class ComposioAdapter:
    """:class:`~lkap_api.tool_providers.adapter.ToolProviderAdapter` for Composio."""

    def __init__(self, client: httpx.AsyncClient, api_key: str, *, base_url: str = BASE_URL) -> None:
        self._client = client
        self._key = api_key
        self._base = base_url.rstrip("/")

    async def _call(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        url = f"{self._base}{path}"
        try:
            response = await self._client.request(
                method,
                url,
                params=_clean(params or {}) or None,
                json=json,
                headers={"x-api-key": self._key, "accept": "application/json"},
                timeout=TIMEOUT_S,
            )
        except httpx.TimeoutException as exc:
            raise ToolProviderUnavailableError("Composio did not answer in time") from exc
        except httpx.HTTPError as exc:
            raise ToolProviderUnavailableError(f"could not reach Composio ({type(exc).__name__})") from exc
        if response.status_code >= 400:
            error = _error_for(response)
            # The resource family only: ids in the path stay out of the log.
            log.info(
                "composio_call_failed",
                method=method,
                resource=path.strip("/").split("/", 1)[0],
                status=response.status_code,
                reason=error.reason,
            )
            raise error
        if response.status_code == 204 or not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise ToolProviderUnavailableError("Composio answered with something that is not JSON") from exc

    async def _object(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        body = await self._call(method, path, **kwargs)
        return body if isinstance(body, dict) else {"items": body} if isinstance(body, list) else {}

    # --------------------------------------------------------------------- key
    async def session_info(self) -> dict[str, Any]:
        """``GET /auth/session/info``: the key's project (and organisation, when shown)."""
        return await self._object("GET", "/auth/session/info")

    async def list_auth_configs(
        self, *, toolkit: str | None = None, composio_managed: bool | None = None, limit: int = 50
    ) -> dict[str, Any]:
        """``GET /auth_configs``."""
        return await self._object(
            "GET",
            "/auth_configs",
            params={"toolkit_slug": toolkit, "is_composio_managed": composio_managed, "limit": limit},
        )

    # ----------------------------------------------------------------- catalogue
    async def list_toolkits(
        self,
        *,
        search: str | None = None,
        category: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """``GET /toolkits`` sorted by usage."""
        return await self._object(
            "GET",
            "/toolkits",
            params={
                "search": search,
                "category": category,
                "cursor": cursor,
                "limit": limit,
                "sort_by": "usage",
            },
        )

    async def get_toolkit(self, slug: str) -> dict[str, Any]:
        """``GET /toolkits/{slug}``."""
        return await self._object("GET", f"/toolkits/{_seg(slug)}")

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
        """``GET /tools``."""
        return await self._object(
            "GET",
            "/tools",
            params={
                "toolkit_slug": toolkit,
                "search": search,
                "important": True if important else None,
                "tool_slugs": ",".join(tool_slugs) if tool_slugs else None,
                "cursor": cursor,
                "limit": limit,
            },
        )

    # ----------------------------------------------------------------- connect
    async def create_auth_config(
        self,
        *,
        toolkit: str,
        managed: bool,
        auth_scheme: str | None = None,
        credentials: dict[str, str] | None = None,
        name: str | None = None,
    ) -> dict[str, Any]:
        """``POST /auth_configs``: Composio's shared app, or the caller's own (credentials forwarded once)."""
        config: dict[str, Any]
        if managed:
            config = {"type": "use_composio_managed_auth"}
        else:
            config = {"type": "use_custom_auth", "authScheme": auth_scheme or "OAUTH2"}
            config["credentials"] = dict(credentials or {})
        if name:
            config["name"] = name
        return await self._object(
            "POST", "/auth_configs", json={"toolkit": {"slug": toolkit}, "auth_config": config}
        )

    async def start_link(
        self, *, auth_config_id: str, subject: str, callback_url: str, alias: str | None = None
    ) -> dict[str, Any]:
        """``POST /connected_accounts/link`` (``alias`` is a top-level field, R-V5-13)."""
        body: dict[str, Any] = {
            "auth_config_id": auth_config_id,
            "user_id": subject,
            "callback_url": callback_url,
        }
        if alias:
            body["alias"] = alias
        return await self._object("POST", "/connected_accounts/link", json=body)

    async def create_with_key(
        self,
        *,
        auth_config_id: str,
        subject: str,
        auth_scheme: str,
        fields: dict[str, str],
        alias: str | None = None,
    ) -> dict[str, Any]:
        """``POST /connected_accounts`` with the key fields (forwarded once, never kept).

        ``alias`` goes inside ``connection`` on this endpoint (R-V5-13).
        """
        connection: dict[str, Any] = {
            "user_id": subject,
            "state": {"authScheme": auth_scheme, "val": {**fields, "status": "ACTIVE"}},
        }
        if alias:
            connection["alias"] = alias
        return await self._object(
            "POST",
            "/connected_accounts",
            json={"auth_config": {"id": auth_config_id}, "connection": connection},
        )

    async def update_connection(self, connected_account_id: str, *, alias: str) -> dict[str, Any]:
        """``PATCH /connected_accounts/{id}`` with ``{alias}`` (an empty string clears it)."""
        return await self._object(
            "PATCH", f"/connected_accounts/{_seg(connected_account_id)}", json={"alias": alias}
        )

    async def get_connection(self, connected_account_id: str) -> dict[str, Any]:
        """``GET /connected_accounts/{id}``."""
        return await self._object("GET", f"/connected_accounts/{_seg(connected_account_id)}")

    async def delete_connection(self, connected_account_id: str) -> None:
        """``DELETE /connected_accounts/{id}``."""
        await self._call("DELETE", f"/connected_accounts/{_seg(connected_account_id)}")

    # --------------------------------------------------------- agents (V5-47)
    async def execute(
        self,
        tool_slug: str,
        *,
        subject: str,
        connected_account_id: str | None,
        arguments: dict[str, Any],
        version: str | None = None,
    ) -> dict[str, Any]:
        """``POST /tools/execute/{slug}``."""
        body: dict[str, Any] = {"user_id": subject, "arguments": arguments, "version": version or "latest"}
        if connected_account_id:
            body["connected_account_id"] = connected_account_id
        return await self._object("POST", f"/tools/execute/{_seg(tool_slug)}", json=body)

    async def create_mcp_server(
        self, *, name: str, auth_config_ids: list[str], allowed_tools: list[str] | None = None
    ) -> dict[str, Any]:
        """``POST /mcp/servers`` (Composio marks this API deprecated in favour of sessions)."""
        body: dict[str, Any] = {"name": name, "auth_config_ids": auth_config_ids}
        if allowed_tools is not None:
            body["allowed_tools"] = allowed_tools
        return await self._object("POST", "/mcp/servers", json=body)

    async def delete_mcp_server(self, server_id: str) -> None:
        """``DELETE /mcp/{id}``."""
        await self._call("DELETE", f"/mcp/{_seg(server_id)}")

    async def create_router_session(self, *, subject: str, options: dict[str, Any]) -> dict[str, Any]:
        """``POST /tool_router/session``."""
        return await self._object("POST", "/tool_router/session", json={**options, "user_id": subject})

    async def delete_router_session(self, session_id: str) -> None:
        """``DELETE /tool_router/session/{id}``."""
        await self._call("DELETE", f"/tool_router/session/{_seg(session_id)}")


__all__ = ["BASE_URL", "OAUTH_REDIRECT_URI", "TIMEOUT_S", "ComposioAdapter"]
