"""``FakeComposio``: the offline Composio double (docs/v5/COMPOSIO.md §4, §8).

Answers from the fixtures in ``api/tests/fixtures/composio/`` and keeps a
little state (auth configs, connected accounts) so connect, callback,
refresh and disconnect can be exercised end to end. One
:class:`ComposioWorld` is shared by every adapter the factory builds, so a
test can see which key each call used and change what Composio "knows"
between requests. No network, no real key.

Usage::

    world = ComposioWorld(valid_keys={"ck_test_valid"})
    app.dependency_overrides[get_adapter_factory] = lambda: world.factory
"""

from __future__ import annotations

import copy
import itertools
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lkap_api.tool_providers.adapter import (
    ToolProviderAdapter,
    ToolProviderAuthError,
    ToolProviderError,
    ToolProviderNotFoundError,
    ToolProviderRequestError,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "composio"

#: A key shaped like a Composio key; never a real one.
VALID_KEY = "ak_fake_Zq9WkX7vRt3LmN8pYb2HcJ5d"


def _load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@dataclass
class Call:
    """One adapter call as the fake saw it."""

    method: str
    api_key: str
    kwargs: dict[str, Any]


@dataclass
class ComposioWorld:
    """What the fake Composio knows, shared by every adapter built from :attr:`factory`."""

    valid_keys: set[str] = field(default_factory=lambda: {VALID_KEY})
    toolkits: dict[str, Any] = field(default_factory=lambda: _load("toolkits.json"))
    toolkit_details: dict[str, Any] = field(default_factory=lambda: _load("toolkit_details.json"))
    tools: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: _load("tools.json"))
    responses: dict[str, Any] = field(default_factory=lambda: _load("responses.json"))
    auth_configs: list[dict[str, Any]] = field(default_factory=list)
    accounts: dict[str, dict[str, Any]] = field(default_factory=dict)
    calls: list[Call] = field(default_factory=list)
    #: ``method name -> exception`` raised on the next call(s) of that method.
    failures: dict[str, ToolProviderError] = field(default_factory=dict)
    #: Raised by ``session_info`` (to exercise the key test's fallback).
    session_info_error: ToolProviderError | None = None
    #: Live Tool Router sessions (V5-47): ``session id -> {subject, options}``.
    sessions: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: Where session MCP urls point (a test sets another host to exercise the pin).
    session_url_base: str = "https://backend.composio.dev/tool_router"
    _ids: itertools.count[int] = field(default_factory=lambda: itertools.count(1))

    def factory(self, api_key: str) -> ToolProviderAdapter:
        """The adapter factory the router depends on."""
        return FakeComposio(self, api_key)

    def next_id(self, prefix: str) -> str:
        """A fresh fake vendor id."""
        return f"{prefix}_{next(self._ids)}"

    def complete(
        self,
        account_id: str,
        *,
        status: str = "ACTIVE",
        user_id: str | None = None,
        display_name: str | None = None,
        account_type: str | None = None,
    ) -> None:
        """Pretend the human finished the vendor's consent page.

        ``display_name`` is what Composio reports at ``state.val.displayName`` once active
        (the inbox address, the user name); ``account_type`` sets ``experimental.account_type``.
        """
        account = self.accounts[account_id]
        account["status"] = status
        if user_id is not None:
            account["user_id"] = user_id
        if display_name is not None:
            account["state"] = {
                "authScheme": "OAUTH2",
                "val": {"status": status, "displayName": display_name},
            }
        if account_type is not None:
            account["experimental"] = {"account_type": account_type}

    def _toolkit_of(self, account: dict[str, Any]) -> str:
        auth_config_id = account.get("auth_config", {}).get("id")
        config = next((c for c in self.auth_configs if c["id"] == auth_config_id), None)
        return str(config["toolkit"]["slug"]) if config else ""

    def claim_alias(self, account_id: str, alias: str | None) -> None:
        """Composio's rule: an alias is unique per ``user_id`` and toolkit (409 otherwise)."""
        account = self.accounts[account_id]
        if alias:
            for other_id, other in self.accounts.items():
                if (
                    other_id != account_id
                    and other.get("alias") == alias
                    and other.get("user_id") == account.get("user_id")
                    and self._toolkit_of(other) == self._toolkit_of(account)
                ):
                    raise ToolProviderRequestError(
                        "Alias already in use for this user and toolkit", status=409
                    )
        account["alias"] = alias or None

    def calls_of(self, method: str) -> list[Call]:
        """Every recorded call of one adapter method."""
        return [call for call in self.calls if call.method == method]

    def seen_values(self) -> str:
        """Everything the fake received, as text (to assert a secret reached Composio)."""
        return json.dumps([{"key": c.api_key, **c.kwargs} for c in self.calls], default=str)


class FakeComposio:
    """A :class:`~lkap_api.tool_providers.adapter.ToolProviderAdapter` over a :class:`ComposioWorld`."""

    def __init__(self, world: ComposioWorld, api_key: str) -> None:
        self.world = world
        self.api_key = api_key

    def _enter(self, method: str, **kwargs: Any) -> None:
        self.world.calls.append(Call(method, self.api_key, copy.deepcopy(kwargs)))
        failure = self.world.failures.pop(method, None)
        if failure is not None:
            raise failure
        if self.api_key not in self.world.valid_keys:
            raise ToolProviderAuthError("Invalid API key", status=401)

    # ------------------------------------------------------------------- key
    async def session_info(self) -> dict[str, Any]:
        self._enter("session_info")
        if self.world.session_info_error is not None:
            raise self.world.session_info_error
        return copy.deepcopy(self.world.responses["session_info"])

    async def list_auth_configs(
        self, *, toolkit: str | None = None, composio_managed: bool | None = None, limit: int = 50
    ) -> dict[str, Any]:
        self._enter("list_auth_configs", toolkit=toolkit, composio_managed=composio_managed, limit=limit)
        items = [
            config
            for config in self.world.auth_configs
            if (toolkit is None or config["toolkit"]["slug"] == toolkit)
            and (composio_managed is None or config["is_composio_managed"] is composio_managed)
        ]
        return {"items": copy.deepcopy(items[:limit]), "next_cursor": None, "total_items": len(items)}

    # ------------------------------------------------------------- catalogue
    async def list_toolkits(
        self,
        *,
        search: str | None = None,
        category: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        self._enter("list_toolkits", search=search, category=category, cursor=cursor, limit=limit)
        page = copy.deepcopy(self.world.toolkits)
        if search:
            page["items"] = [i for i in page["items"] if search.lower() in i["name"].lower()]
        if cursor:
            page["items"], page["next_cursor"] = [], None
        page["items"] = page["items"][:limit]
        return dict(page)

    async def get_toolkit(self, slug: str) -> dict[str, Any]:
        self._enter("get_toolkit", slug=slug)
        detail = self.world.toolkit_details.get(slug)
        if detail is None:
            raise ToolProviderNotFoundError("Toolkit not found", status=404)
        return dict(copy.deepcopy(detail))

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
        self._enter(
            "list_tools",
            toolkit=toolkit,
            search=search,
            important=important,
            tool_slugs=tool_slugs,
            cursor=cursor,
            limit=limit,
        )
        items = copy.deepcopy(self.world.tools.get(toolkit or "", []))
        if tool_slugs:
            items = [i for i in items if i["slug"] in tool_slugs]
        if important:
            items = [i for i in items if "important" in i.get("tags", [])]
        if search:
            items = [i for i in items if search.lower() in i["name"].lower()]
        return {"items": items[:limit], "next_cursor": None, "total_items": len(items)}

    # --------------------------------------------------------------- connect
    async def create_auth_config(
        self,
        *,
        toolkit: str,
        managed: bool,
        auth_scheme: str | None = None,
        credentials: dict[str, str] | None = None,
        name: str | None = None,
    ) -> dict[str, Any]:
        self._enter(
            "create_auth_config",
            toolkit=toolkit,
            managed=managed,
            auth_scheme=auth_scheme,
            credentials=credentials,
            name=name,
        )
        config = {
            "id": self.world.next_id("ac"),
            "toolkit": {"slug": toolkit},
            "auth_scheme": auth_scheme or "OAUTH2",
            "is_composio_managed": managed,
            "status": "ENABLED",
        }
        self.world.auth_configs.append(config)
        return {"toolkit": {"slug": toolkit}, "auth_config": {k: config[k] for k in ("id", "auth_scheme")}}

    async def start_link(
        self, *, auth_config_id: str, subject: str, callback_url: str, alias: str | None = None
    ) -> dict[str, Any]:
        self._enter(
            "start_link",
            auth_config_id=auth_config_id,
            subject=subject,
            callback_url=callback_url,
            alias=alias,
        )
        if not any(config["id"] == auth_config_id for config in self.world.auth_configs):
            raise ToolProviderNotFoundError("Auth config not found", status=404)
        account_id = self.world.next_id("ca")
        self.world.accounts[account_id] = {
            "id": account_id,
            "user_id": subject,
            "status": "INITIATED",
            "auth_config": {"id": auth_config_id},
        }
        try:
            self.world.claim_alias(account_id, alias)
        except ToolProviderError:
            del self.world.accounts[account_id]
            raise
        link = copy.deepcopy(self.world.responses["link"])
        link["connected_account_id"] = account_id
        link["redirect_url"] = f"https://connect.example.com/link/{account_id}"
        return dict(link)

    async def create_with_key(
        self,
        *,
        auth_config_id: str,
        subject: str,
        auth_scheme: str,
        fields: dict[str, str],
        alias: str | None = None,
    ) -> dict[str, Any]:
        self._enter(
            "create_with_key",
            auth_config_id=auth_config_id,
            subject=subject,
            auth_scheme=auth_scheme,
            fields=fields,
            alias=alias,
        )
        if not fields.get("api_key") and auth_scheme == "API_KEY":
            raise ToolProviderRequestError("api_key is required", status=400)
        account_id = self.world.next_id("ca")
        self.world.accounts[account_id] = {
            "id": account_id,
            "user_id": subject,
            "status": "ACTIVE",
            "auth_config": {"id": auth_config_id},
        }
        try:
            self.world.claim_alias(account_id, alias)
        except ToolProviderError:
            del self.world.accounts[account_id]
            raise
        return {"id": account_id, "status": "ACTIVE", "redirect_url": None}

    async def update_connection(self, connected_account_id: str, *, alias: str) -> dict[str, Any]:
        self._enter("update_connection", connected_account_id=connected_account_id, alias=alias)
        account = self.world.accounts.get(connected_account_id)
        if account is None:
            raise ToolProviderNotFoundError("Connected account not found", status=404)
        self.world.claim_alias(connected_account_id, alias)
        return {"success": True, "id": connected_account_id, "status": account["status"]}

    async def get_connection(self, connected_account_id: str) -> dict[str, Any]:
        self._enter("get_connection", connected_account_id=connected_account_id)
        account = self.world.accounts.get(connected_account_id)
        if account is None:
            raise ToolProviderNotFoundError("Connected account not found", status=404)
        return dict(copy.deepcopy(account))

    async def delete_connection(self, connected_account_id: str) -> None:
        self._enter("delete_connection", connected_account_id=connected_account_id)
        if self.world.accounts.pop(connected_account_id, None) is None:
            raise ToolProviderNotFoundError("Connected account not found", status=404)

    # ------------------------------------------------------------ V5-47 surface
    async def execute(
        self,
        tool_slug: str,
        *,
        subject: str,
        connected_account_id: str | None,
        arguments: dict[str, Any],
        version: str | None = None,
    ) -> dict[str, Any]:
        self._enter("execute", tool_slug=tool_slug, subject=subject, arguments=arguments)
        return {"data": {}, "error": None, "successful": True}

    async def create_mcp_server(
        self, *, name: str, auth_config_ids: list[str], allowed_tools: list[str] | None = None
    ) -> dict[str, Any]:
        self._enter("create_mcp_server", name=name, auth_config_ids=auth_config_ids)
        return {"id": self.world.next_id("mcp")}

    async def delete_mcp_server(self, server_id: str) -> None:
        self._enter("delete_mcp_server", server_id=server_id)

    async def create_router_session(self, *, subject: str, options: dict[str, Any]) -> dict[str, Any]:
        self._enter("create_router_session", subject=subject, options=options)
        session_id = self.world.next_id("trs")
        url = f"{self.world.session_url_base}/{session_id}/mcp"
        self.world.sessions[session_id] = {"subject": subject, "options": copy.deepcopy(options)}
        return {
            "session_id": session_id,
            "mcp": {"type": "http", "url": url},
            "tool_router_tools": ["COMPOSIO_SEARCH_TOOLS", "COMPOSIO_MULTI_EXECUTE_TOOL"],
            "config_version": 1,
            "warnings": [],
        }

    async def delete_router_session(self, session_id: str) -> None:
        self._enter("delete_router_session", session_id=session_id)
        if self.world.sessions.pop(session_id, None) is None:
            raise ToolProviderNotFoundError("Session not found", status=404)


__all__ = ["FIXTURES", "VALID_KEY", "Call", "ComposioWorld", "FakeComposio"]
