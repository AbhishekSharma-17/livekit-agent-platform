"""The ``lkap://`` resources (``AGENT-ACCESS.md`` §3.1).

Hand-written docs come from :mod:`lkap_mcp.content` (V3-03 writes them);
generated ones from ``generated/`` or the installed contracts; live ones from
the api through the same client (redacted and scrubbed like tool results).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from mcp.server.fastmcp import FastMCP

from lkap_mcp import content
from lkap_mcp.client import CURRENT_CALL, ApiFailure, CallInfo
from lkap_mcp.registry import ServerContext, current_client_name
from lkap_mcp.results import redact, scrub
from lkap_mcp.tools.discovery import own_workspace, reduce_telephony


def _json(value: Any) -> str:
    return json.dumps(scrub(redact(value)), indent=2, sort_keys=True, default=str)


async def _live(ctx: ServerContext, resource: str, path: str) -> str:
    """``GET`` a live api path for a resource, attributed like a tool call."""
    token = CURRENT_CALL.set(
        CallInfo(client=current_client_name(ctx.settings), tool=f"resource:{resource}", call=uuid.uuid4().hex)
    )
    try:
        return _json(await ctx.client.get(path))
    except ApiFailure as failure:
        return _json({"error": {"code": failure.code, "message": failure.message, "status": failure.status}})
    finally:
        CURRENT_CALL.reset(token)


def register_resources(server: FastMCP[Any], ctx: ServerContext) -> None:
    """Attach every ``lkap://`` resource to ``server``."""

    @server.resource("lkap://guide", name="guide", mime_type="text/markdown")
    def guide() -> str:
        """The platform guide: object model, workflow, safety rules and recipe index."""
        return content.guide()

    @server.resource("lkap://concepts/{topic}", name="concept", mime_type="text/markdown")
    def concept(topic: str) -> str:
        """One concept doc (see lkap_explain for the topics)."""
        return content.concept(topic)

    @server.resource("lkap://recipes/{name}", name="recipe", mime_type="text/markdown")
    def recipe(name: str) -> str:
        """One recipe: a numbered tool sequence with example arguments."""
        return (
            content.recipe(name)
            or f"# {name}\n\nNo such recipe. Known: {', '.join(content.recipe_names())}\n"
        )

    @server.resource("lkap://registry/providers", name="providers", mime_type="application/json")
    async def providers() -> str:
        """The provider registry, with this workspace's enablement when the key can read providers."""
        if ctx.allows("providers:read"):
            return await _live(ctx, "providers", "/v1/providers")
        return _json(content.providers_document())

    @server.resource("lkap://registry/providers/{provider_id}", name="provider", mime_type="application/json")
    def provider(provider_id: str) -> str:
        """One provider spec (fields, secret fields, models, capabilities, worker image)."""
        return _json(content.provider_spec(provider_id) or {"error": f"no provider {provider_id!r}"})

    @server.resource("lkap://schemas/{model}", name="schema", mime_type="application/schema+json")
    def schema(model: str) -> str:
        """One exported JSON schema (AgentConfig, FlowSpec, PanelLayout, HttpToolDefinition …)."""
        return _json(
            content.schema(model) or {"error": f"no schema {model!r}", "known": content.schema_names()}
        )

    @server.resource("lkap://blocks", name="blocks", mime_type="application/json")
    def blocks() -> str:
        """The panel block catalog with each block's config schema."""
        return _json(content.block_catalog())

    @server.resource("lkap://flows/node-specs", name="node-specs", mime_type="application/json")
    async def node_specs() -> str:
        """The flow node kinds and their schemas (live)."""
        return await _live(ctx, "node-specs", "/v1/flows/node-specs")

    @server.resource("lkap://packs", name="packs", mime_type="application/json")
    async def packs() -> str:
        """The installed packs: ids, what they seed, tool names and default panel (live)."""
        return await _live(ctx, "packs", "/v1/packs")

    @server.resource("lkap://openapi", name="openapi", mime_type="application/json")
    async def openapi() -> str:
        """The api's OpenAPI document (for api_request)."""
        return _json(await ctx.client.openapi())

    @server.resource("lkap://workspace", name="workspace", mime_type="application/json")
    async def workspace() -> str:
        """A live summary: workspace, key scopes, connections, agents, knowledge bases, tools."""
        token = CURRENT_CALL.set(
            CallInfo(
                client=current_client_name(ctx.settings), tool="resource:workspace", call=uuid.uuid4().hex
            )
        )
        try:
            summary: dict[str, Any] = {
                "key_scopes": sorted(ctx.identity.scopes) if ctx.identity else None,
                "workspace": None,
            }
            try:
                row = await own_workspace(ctx)
                if row is not None:
                    row["settings"] = reduce_telephony(row.get("settings"))
                summary["workspace"] = row
            except ApiFailure as failure:
                summary["workspace_error"] = failure.code
            reads = {
                "connections": ("connections:read", "/v1/connections"),
                "agents": ("agents:read", "/v1/agents"),
                "knowledge_bases": ("agents:read", "/v1/knowledge-bases"),
                "tools": ("agents:read", "/v1/tools"),
                "recent_sessions": ("sessions:read", "/v1/sessions"),
            }
            for key, (scope, path) in reads.items():
                if not ctx.allows(scope):
                    continue
                try:
                    rows = await ctx.client.items(
                        path, params={"limit": 10} if key == "recent_sessions" else None
                    )
                except ApiFailure as failure:
                    summary[key] = {"error": failure.code}
                    continue
                summary[key] = _summarise(key, rows)
            return _json(summary)
        finally:
            CURRENT_CALL.reset(token)


def _summarise(key: str, rows: list[Any]) -> Any:
    if key == "connections":
        return [
            {k: row.get(k) for k in ("id", "slug", "status", "is_default", "deployment_mode", "capabilities")}
            for row in rows
        ]
    if key == "agents":
        return {
            "n": len(rows),
            "published": sum(1 for row in rows if row.get("published")),
            "flow": sum(1 for row in rows if row.get("mode") == "flow"),
            "unbound": sum(1 for row in rows if not row.get("connection_id")),
        }
    if key == "recent_sessions":
        return [
            {k: row.get(k) for k in ("id", "agent_name", "status", "channel", "created_at")} for row in rows
        ]
    return [{k: row.get(k) for k in ("id", "name")} for row in rows]
