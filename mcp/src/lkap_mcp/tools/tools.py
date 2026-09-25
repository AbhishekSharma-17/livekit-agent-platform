"""HTTP-tool and MCP-server tools (``AGENT-ACCESS.md`` §4.6).

Secrets for a tool are a separate ``provider_key_create(provider_id="http-tool-secret",
secrets={NAME: ...})``; the tool references them as ``{{ secret.NAME }}`` and binds the
key with ``secret_key_id`` (the api's ``credential_id``; needs ``providers:write``, R-V2-33).
An MCP server says how it authenticates with ``auth`` (V5-09): ``{"kind": "none"}``,
``{"kind": "header", "headers": {...}, "credential_id": ...}``; ``{"kind": "oauth"}`` is
refused by the api until sign-in ships. ``tool_test`` connects once and lists its tools.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from lkap_contracts.tools import HttpToolDefinition, McpAuth, McpServerDefinition, ToolExecution
from pydantic import Field

from lkap_mcp.registry import IDEMPOTENT_WRITE, READ, WRITE, Registry
from lkap_mcp.results import ToolResult, untrusted
from lkap_mcp.tools._common import merge_patch, planned, request, seg

HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]

#: The read-tool rule, in one sentence, for the `execution` / `tool_options` arguments.
EXECUTION_HELP = (
    "How the tool runs: mode 'blocking' (default), 'background' (announce at once, answer when the "
    "agent is idle) or 'auto' (inline if fast, else background). Only GET tools follow the agent's "
    "tools.execution_default; any other method runs blocking unless it sets a mode here, and then "
    "asks before running twice. Fillers are spoken as written and need a voice (a TTS)."
)
UPDATE_EXECUTION_HELP = (
    "HTTP tools only: replaces definition.execution (pass patch={} to change nothing else). " + EXECUTION_HELP
)
MCP_AUTH_HELP = (
    "How the worker authenticates: {'kind': 'none'} (default), or {'kind': 'header', 'headers': "
    "{...}, 'credential_id': <http-tool-secret key id>} where header values may use "
    "{{ secret.NAME }}. Sign-in ({'kind': 'oauth'}) is not available yet. Do not combine with "
    "headers/secret_key_id, which are the older spelling of header auth."
)
#: The deprecated top-level mirrors of an MCP definition's header auth (V5-09).
_MCP_LEGACY_AUTH_KEYS = ("headers", "credential_id")
MCP_TOOL_OPTIONS_HELP = (
    "Per MCP tool name: how it runs (a ToolExecution; names must be in allowed_tools when set). "
    "MCP tools never follow the agent default; a background tool is announced only through the "
    "server's progress messages (report_progress=true)."
)


def _dry_run(result: Any, tool_id: str) -> dict[str, Any]:
    """A ``ToolDryRunResult`` with its body wrapped as untrusted."""
    body = dict(result) if isinstance(result, dict) else {}
    body["result"] = untrusted(body.get("result", ""), f"tool:{tool_id}")
    return body


def register(registry: Registry) -> None:
    """Declare the tool-management tools."""
    client = registry.ctx.client

    @registry.tool(scopes={"agents:read"}, annotations=READ, data="ToolOut[]")
    async def tool_list(
        agent_id: str | None = None, kind: Literal["http", "mcp"] | None = None
    ) -> ToolResult:
        """List HTTP tools and MCP servers (optionally one agent's, or one kind)."""
        return ToolResult.success(
            await client.items("/v1/tools", params={"agent_id": agent_id, "kind": kind})
        )

    @registry.tool(scopes={"agents:read"}, annotations=READ, data="ToolOut")
    async def tool_get(tool_id: str) -> ToolResult:
        """One tool definition ({{ placeholders }} unsubstituted)."""
        return ToolResult.success(await client.get(f"/v1/tools/{seg(tool_id)}"))

    @registry.tool(scopes={"agents:write"}, annotations=WRITE, data="ToolOut")
    async def tool_create_http(
        name: Annotated[str, Field(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")],
        description: str,
        parameters: Annotated[dict[str, Any], Field(description="JSON schema of the tool's arguments")],
        url: Annotated[str, Field(description="May contain {{arg}} and {{ secret.NAME }} placeholders")],
        allowed_hosts: Annotated[
            list[str], Field(min_length=1, description="Hosts the tool may call (required)")
        ],
        method: HttpMethod = "POST",
        headers: dict[str, str] | None = None,
        body_template: str | None = None,
        result_path: str | None = None,
        timeout_s: Annotated[float, Field(gt=0, le=60)] = 10,
        max_result_chars: Annotated[int, Field(ge=100, le=100_000)] = 4000,
        silent_reply: bool = False,
        secret_key_id: Annotated[
            str | None, Field(description="An http-tool-secret provider key for {{ secret.NAME }}")
        ] = None,
        agent_id: str | None = None,
        dry_run_args: Annotated[
            dict[str, Any] | None, Field(description="Arguments for a dry run right after creating")
        ] = None,
        execution: Annotated[ToolExecution | None, Field(description=EXECUTION_HELP)] = None,
        plan: bool = False,
    ) -> ToolResult:
        """Create an HTTP tool an agent can call (hosts allowlisted), optionally dry-running it."""
        definition = HttpToolDefinition(
            name=name,
            description=description,
            parameters=parameters,
            method=method,
            url=url,
            headers=headers or {},
            credential_id=secret_key_id,
            body_template=body_template,
            allowed_hosts=allowed_hosts,
            timeout_s=timeout_s,
            max_result_chars=max_result_chars,
            result_path=result_path,
            silent_reply=silent_reply,
            execution=execution or ToolExecution(),
        )
        body = {
            "agent_id": agent_id,
            "kind": "http",
            "name": name,
            "definition": definition.model_dump(mode="json"),
        }
        if plan:
            steps = [request("POST", "/v1/tools", body)]
            if dry_run_args is not None:
                steps.append(
                    request(
                        "POST", "/v1/tools/{id}/dry-run", {"arguments": dry_run_args}, note="after create"
                    )
                )
            return planned(*steps)
        created = await client.post("/v1/tools", body)
        data: dict[str, Any] = {"tool": created}
        if dry_run_args is not None:
            result = await client.post(f"/v1/tools/{seg(created['id'])}/dry-run", {"arguments": dry_run_args})
            data["dry_run"] = _dry_run(result, created["id"])
        return ToolResult.success(
            data, next_steps=[f'Attach it: agent_attach(id_or_slug=..., tool_ids=["{created.get("id")}"]).']
        )

    @registry.tool(scopes={"agents:write"}, annotations=WRITE, data="ToolOut")
    async def tool_create_mcp(
        name: str,
        url: Annotated[str, Field(description="The server's https endpoint (streamable HTTP)")],
        auth: Annotated[McpAuth | None, Field(description=MCP_AUTH_HELP)] = None,
        headers: dict[str, str] | None = None,
        allowed_tools: list[str] | None = None,
        secret_key_id: str | None = None,
        timeout_s: Annotated[float, Field(gt=0, le=60)] = 5,
        agent_id: str | None = None,
        tool_options: Annotated[
            dict[str, ToolExecution] | None, Field(description=MCP_TOOL_OPTIONS_HELP)
        ] = None,
        plan: bool = False,
    ) -> ToolResult:
        """Attach a remote MCP server as a tool source; check it with tool_test before a chat."""
        if auth is not None and (headers or secret_key_id):
            return ToolResult.fail(
                "invalid_argument", "pass auth, or headers/secret_key_id (the older spelling), not both"
            )
        definition = McpServerDefinition(
            name=name,
            url=url,
            headers=headers or {},
            credential_id=secret_key_id,
            allowed_tools=allowed_tools,
            timeout_s=timeout_s,
            tool_options=tool_options or {},
            **({"auth": auth} if auth is not None else {}),
        )
        body = {
            "agent_id": agent_id,
            "kind": "mcp",
            "name": name,
            "definition": definition.model_dump(mode="json"),
        }
        if plan:
            return planned(request("POST", "/v1/tools", body))
        created = await client.post("/v1/tools", body)
        return ToolResult.success(
            created,
            next_steps=[
                f'Check it: tool_test(tool_id="{created.get("id")}") connects once and lists its tools.'
            ],
        )

    @registry.tool(scopes={"agents:write"}, annotations=IDEMPOTENT_WRITE, data="ToolOut")
    async def tool_update(
        tool_id: str,
        patch: Annotated[
            dict[str, Any],
            Field(description="Merge patch over {name, enabled, agent_id, definition}"),
        ],
        execution: Annotated[ToolExecution | None, Field(description=UPDATE_EXECUTION_HELP)] = None,
        plan: bool = False,
    ) -> ToolResult:
        """Change a tool with a merge patch over its name, enabled flag, agent and definition."""
        current = await client.get(f"/v1/tools/{seg(tool_id)}")
        if execution is not None and current.get("kind") != "http":
            return ToolResult.fail(
                "invalid_argument",
                "execution applies to HTTP tools; for an MCP server patch definition.tool_options",
            )
        base = {key: current.get(key) for key in ("agent_id", "kind", "name", "definition", "enabled")}
        if current.get("kind") == "mcp" and isinstance(base.get("definition"), dict):
            base["definition"] = _mcp_patch_base(base["definition"], patch.get("definition"))
        merged = merge_patch(base, {key: value for key, value in patch.items() if key != "kind"})
        merged["kind"] = current.get("kind")
        if isinstance(merged.get("definition"), dict):
            merged["definition"]["kind"] = current.get("kind")
            if execution is not None:
                merged["definition"]["execution"] = execution.model_dump(mode="json")
        path = f"/v1/tools/{seg(tool_id)}"
        if plan:
            return planned(request("PUT", path, merged))
        return ToolResult.success(await client.put(path, merged))

    @registry.tool(scopes={"agents:write"}, annotations=WRITE, data="McpTestResult")
    async def tool_test(tool_id: str) -> ToolResult:
        """Connect to an MCP server tool once: list its tools and store them for the console."""
        result = await client.post(f"/v1/tools/{seg(tool_id)}/test", {})
        body = dict(result) if isinstance(result, dict) else {}
        # Tool names come from a third-party server: data, never instructions.
        body["tool_names"] = untrusted(", ".join(body.get("tool_names") or []), f"mcp:{tool_id}")
        if body.get("ok"):
            return ToolResult.success(body)
        return ToolResult.success(body, warnings=[f"The server could not be listed: {body.get('error')}"])

    @registry.tool(scopes={"agents:write"}, annotations=WRITE, data="ToolDryRunResult")
    async def tool_dry_run(tool_id: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        """Call an HTTP tool once with arguments, as the worker would (result is untrusted)."""
        result = await client.post(f"/v1/tools/{seg(tool_id)}/dry-run", {"arguments": arguments or {}})
        return ToolResult.success(_dry_run(result, tool_id))


def _mcp_patch_base(definition: dict[str, Any], patch: object) -> dict[str, Any]:
    """The stored MCP definition to merge a patch over, with one spelling of its auth (V5-09).

    The api returns ``auth`` plus its deprecated ``headers``/``credential_id`` mirrors and
    refuses a pair that disagrees. A patch that sets the older fields drops the stored
    ``auth`` (they fold back into header auth); one that sets ``auth`` drops the mirrors.
    """
    if not isinstance(patch, dict):
        return definition
    base = dict(definition)
    if "auth" in patch:
        for key in _MCP_LEGACY_AUTH_KEYS:
            base.pop(key, None)
    elif any(key in patch for key in _MCP_LEGACY_AUTH_KEYS):
        base.pop("auth", None)
    return base
