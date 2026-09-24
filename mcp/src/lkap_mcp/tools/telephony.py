"""Telephony tools (``AGENT-ACCESS.md`` §4.10, D-V3-8, R-V3-7).

Reads by default. ``call_place`` and ``call_control`` are registered only when
the key has ``calls:write`` **and** the process runs with
``LKAP_MCP_ALLOW_DIAL=1``, and each call needs ``confirm=true``. The dialing
policy is read-only here; the api enforces it unchanged.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field

from lkap_mcp.client import ApiFailure
from lkap_mcp.registry import DESTRUCTIVE, READ, Registry, ServerContext
from lkap_mcp.results import ToolResult
from lkap_mcp.tools._common import planned, request, seg
from lkap_mcp.tools.discovery import own_workspace, reduce_telephony

CallAction = Literal["hangup", "dtmf", "transfer"]


def hosted_number_warnings(numbers: list[dict[str, Any]]) -> list[str]:
    """One line per LiveKit-hosted number that does not reach its agent (V4-05, D-V4-21).

    Assigning an inbound agent (or re-attaching) stays console-only (R-V3-7).
    """
    return [
        f"number {n.get('e164', '?')}: {n.get('attach_state')}"
        for n in numbers
        if n.get("attach_state") != "routed"
    ]


def dial_enabled(ctx: ServerContext) -> bool:
    """The process-level dial gate (``LKAP_MCP_ALLOW_DIAL=1``)."""
    return ctx.settings.allow_dial


def register(registry: Registry) -> None:
    """Declare the telephony tools."""
    ctx = registry.ctx
    client = ctx.client

    @registry.tool(scopes={"connections:read"}, annotations=READ, data="TelephonyOverview")
    async def telephony_overview() -> ToolResult:
        """Trunks, numbers with their inbound agents, dispatch rules and the dialing-policy summary."""
        warnings: list[str] = []
        data: dict[str, Any] = {}
        for key, path in (
            ("trunks", "/v1/telephony/trunks"),
            ("numbers", "/v1/telephony/numbers"),
            ("dispatch_rules", "/v1/telephony/dispatch-rules"),
        ):
            try:
                data[key] = await client.items(path)
            except ApiFailure as failure:
                data[key] = []
                warnings.append(f"{key}: {failure.code}")
        hosted = [n for n in data["numbers"] if isinstance(n, dict) and n.get("source") == "livekit"]
        data["livekit_numbers"] = len(hosted)
        warnings.extend(hosted_number_warnings(hosted))
        workspace = await own_workspace(ctx)
        data["policy"] = reduce_telephony((workspace or {}).get("settings")).get("telephony", {})
        data["dial_enabled"] = dial_enabled(ctx) and ctx.allows("calls:write")
        return ToolResult.success(data, warnings=warnings)

    @registry.tool(scopes={"sessions:read"}, annotations=READ, data="CallOut[]")
    async def call_list(
        direction: Literal["inbound", "outbound"] | None = None,
        status: str | None = None,
        session_id: str | None = None,
        limit: Annotated[int, Field(ge=1, le=200)] = 25,
    ) -> ToolResult:
        """List phone calls (inbound and outbound) with status and timing."""
        return ToolResult.success(
            await client.items(
                "/v1/calls",
                params={"direction": direction, "status": status, "session_id": session_id, "limit": limit},
            )
        )

    @registry.tool(scopes={"sessions:read"}, annotations=READ, data="CallOut")
    async def call_get(call_id: str) -> ToolResult:
        """One phone call."""
        return ToolResult.success(await client.get(f"/v1/calls/{seg(call_id)}"))

    @registry.tool(scopes={"calls:write"}, annotations=DESTRUCTIVE, gated_by=dial_enabled, data="CallOut")
    async def call_place(
        agent_id: str,
        to_e164: Annotated[str, Field(pattern=r"^\+[1-9]\d{6,14}$")],
        trunk_id: str | None = None,
        variables: dict[str, Any] | None = None,
        confirm: bool = False,
        plan: bool = False,
    ) -> ToolResult:
        """Place an outbound phone call with an agent (needs confirm; the workspace dial policy applies)."""
        body = {"agent_id": agent_id, "to_e164": to_e164, "trunk_id": trunk_id, "variables": variables or {}}
        if plan:
            return planned(request("POST", "/v1/calls", body))
        if not confirm:
            return ToolResult.needs_confirmation(
                f"dial {to_e164} with agent {agent_id}; this places a real call"
            )
        return ToolResult.success(await client.post("/v1/calls", body))

    @registry.tool(scopes={"calls:write"}, annotations=DESTRUCTIVE, gated_by=dial_enabled, data="CallOut")
    async def call_control(
        call_id: str,
        action: CallAction,
        digits: Annotated[str | None, Field(pattern=r"^[0-9*#A-D]{1,32}$")] = None,
        to: Annotated[str | None, Field(description="Transfer target: +E.164, tel: or sip: uri")] = None,
        confirm: bool = False,
    ) -> ToolResult:
        """Hang up, send DTMF on, or transfer a live call (needs confirm)."""
        if action == "dtmf" and not digits:
            return ToolResult.fail("invalid_input", "dtmf needs digits")
        if action == "transfer" and not to:
            return ToolResult.fail("invalid_input", "transfer needs to")
        if not confirm:
            return ToolResult.needs_confirmation(f"{action} call {call_id}")
        path = f"/v1/calls/{seg(call_id)}/{action}"
        body: dict[str, Any] | None = (
            {"digits": digits} if action == "dtmf" else {"to": to} if action == "transfer" else None
        )
        return ToolResult.success(await client.post(path, body))
