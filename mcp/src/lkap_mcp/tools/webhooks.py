"""Webhook tools (``AGENT-ACCESS.md`` §4.9).

``POST /v1/webhooks`` is the one route that returns a secret (the signing
secret, once). In stdio mode ``webhook_create`` writes it to
``~/.config/lkap/webhooks/<endpoint_id>.secret`` (0600) and returns the path;
the value never enters a tool result. In HTTP mode the tool refuses (D-V3-4).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field

from lkap_mcp.registry import READ, WRITE, Registry
from lkap_mcp.results import ToolResult
from lkap_mcp.secrets import remember
from lkap_mcp.tools._common import planned, request, seg


def write_secret_file(directory: Path, endpoint_id: str, secret: str) -> Path:
    """Write ``<dir>/<endpoint_id>.secret`` with mode 0600 (dir 0700); return the path."""
    folder = directory.expanduser()
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = folder / f"{Path(endpoint_id).name}.secret"
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, secret.encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(target, 0o600)
    return target


def register(registry: Registry) -> None:
    """Declare the webhook tools."""
    ctx = registry.ctx
    client = ctx.client

    @registry.tool(scopes={"webhooks:write"}, annotations=READ, data="WebhookEndpointOut[]")
    async def webhook_list() -> ToolResult:
        """List webhook endpoints (the api has no read-only webhook scope)."""
        return ToolResult.success(await client.items("/v1/webhooks", params={"limit": 200}))

    @registry.tool(scopes={"webhooks:write"}, annotations=WRITE, data="WebhookEndpointOut")
    async def webhook_create(
        url: str,
        events: Annotated[
            list[str] | None, Field(description="Event types, e.g. session.ended; omitted = all")
        ] = None,
        description: str = "",
        plan: bool = False,
    ) -> ToolResult:
        """Create a webhook endpoint; the signing secret is written to a 0600 file, never returned."""
        if ctx.settings.http_mode:
            return ToolResult.fail(
                "unavailable_in_http_mode",
                "webhook_create is not available on the remote MCP service "
                "(its secret cannot be handed over safely)",
                hint="Create the webhook in the console (Settings -> Webhooks).",
            )
        body = {"url": url, "events": list(events or []), "description": description}
        if plan:
            return planned(request("POST", "/v1/webhooks", body))
        created: dict[str, Any] = dict(await client.post("/v1/webhooks", body))
        secret = str(created.pop("secret", "") or "")
        data: dict[str, Any] = {"endpoint": created}
        if secret:
            remember(secret)
            data["secret_file"] = str(
                write_secret_file(ctx.settings.webhook_secret_dir, created["id"], secret)
            )
        return ToolResult.success(
            data, next_steps=["Give the receiver the signing secret from secret_file; it is not shown again."]
        )

    @registry.tool(scopes={"webhooks:write"}, annotations=WRITE, data="WebhookDeliveryOut")
    async def webhook_test(endpoint_id: str) -> ToolResult:
        """Send a synthetic webhook.test event to an endpoint and report the delivery."""
        return ToolResult.success(await client.post(f"/v1/webhooks/{seg(endpoint_id)}/test", {"data": {}}))

    @registry.tool(scopes={"webhooks:write"}, annotations=WRITE, data="WebhookDeliveryOut[]")
    async def webhook_deliveries(
        endpoint_id: str,
        redeliver: Annotated[str | None, Field(description="A delivery id to send again")] = None,
        limit: Annotated[int, Field(ge=1, le=200)] = 25,
    ) -> ToolResult:
        """An endpoint's recent deliveries, or redeliver one."""
        if redeliver:
            return ToolResult.success(
                await client.post(f"/v1/webhooks/deliveries/{seg(redeliver)}/redeliver")
            )
        return ToolResult.success(
            await client.items(f"/v1/webhooks/{seg(endpoint_id)}/deliveries", params={"limit": limit})
        )
