"""Discovery and identity tools (``AGENT-ACCESS.md`` §4.1).

``lkap_guide``, ``lkap_explain``, ``lkap_describe`` and ``lkap_search_docs``
double the ``lkap://`` resources for clients that are tools-first; ``me``,
``workspace_get`` and ``activity`` describe the key and its workspace.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field

from lkap_mcp import content
from lkap_mcp.client import ApiFailure
from lkap_mcp.content import ConceptTopic
from lkap_mcp.registry import READ, KeyIdentity, Registry, ServerContext
from lkap_mcp.results import ToolResult

DescribeKind = Literal["schema", "provider", "block", "node", "pack", "builtin_tool", "recipe", "route"]

#: The ``settings.telephony`` keys ``workspace_get`` shows (the policy is never writable here).
TELEPHONY_POLICY_KEYS = (
    "allowed_prefixes",
    "allowed_sip_hosts",
    "max_calls_per_min",
    "max_concurrent_outbound",
)

_WORD = re.compile(r"[a-z0-9_:-]+")
_NODE_MODELS = {
    "start": "StartNode",
    "agent": "AgentNode",
    "end": "EndNode",
    "global": "GlobalNode",
    "transfer": "TransferNode",
    "qa": "QaNode",
}


def reduce_telephony(settings: Any) -> dict[str, Any]:
    """``settings`` with ``telephony`` reduced to the policy summary."""
    out = dict(settings) if isinstance(settings, dict) else {}
    telephony = out.get("telephony")
    if isinstance(telephony, dict):
        out["telephony"] = {key: telephony.get(key) for key in TELEPHONY_POLICY_KEYS if key in telephony}
    return out


async def own_workspace(ctx: ServerContext) -> dict[str, Any] | None:
    """The key's workspace from ``GET /v1/workspaces``."""
    rows = await ctx.client.items("/v1/workspaces")
    wanted = ctx.identity.workspace.get("id") if ctx.identity else None
    for row in rows:
        if isinstance(row, dict) and (wanted is None or row.get("id") == wanted):
            return dict(row)
    return None


def _snippet(text: str, terms: list[str], width: int = 160) -> str:
    lower = text.lower()
    positions = [lower.find(term) for term in terms if lower.find(term) >= 0]
    start = max(0, min(positions) - 40) if positions else 0
    return " ".join(text[start : start + width].split())


def search_documents(query: str, limit: int) -> list[dict[str, Any]]:
    """In-memory keyword search over docs, the provider registry, schemas and blocks."""
    terms = _WORD.findall(query.lower())
    if not terms:
        return []
    candidates: list[tuple[str, str, str]] = list(content.all_docs())
    for spec in content.providers_document().get("providers", []):
        label = f"{spec.get('label', '')} ({spec.get('vendor', '')}, {spec.get('kind', '')})"
        candidates.append((f"lkap://registry/providers/{spec.get('id')}", label, f"{spec.get('id')} {label}"))
    for name in content.schema_names():
        candidates.append((f"lkap://schemas/{name}", f"Schema {name}", name))
    for block in content.block_catalog():
        candidates.append(
            ("lkap://blocks", f"Block {block['type']}", f"{block['type']} {block['description']}")
        )
    hits: list[tuple[float, dict[str, Any]]] = []
    for uri, title, text in candidates:
        lower_title, lower_text = title.lower(), text.lower()
        score = sum(3 * lower_title.count(term) + min(lower_text.count(term), 10) for term in terms)
        if score:
            hits.append((score, {"uri": uri, "title": title, "snippet": _snippet(text, terms)}))
    hits.sort(key=lambda item: -item[0])
    return [hit for _, hit in hits[:limit]]


def register(registry: Registry) -> None:
    """Declare the discovery tools."""
    ctx = registry.ctx
    client = ctx.client

    @registry.tool(annotations=READ, data="Markdown")
    async def lkap_guide() -> ToolResult:
        """Read the LKAP platform guide: object model, workflow, safety rules and recipe index. Call once per
        session first.
        """
        return ToolResult.success({"uri": "lkap://guide", "markdown": content.guide()})

    @registry.tool(annotations=READ, data="Markdown")
    async def lkap_explain(topic: ConceptTopic) -> ToolResult:
        """Read one concept doc (agents, pipeline modes, providers and keys, knowledge, tools, flows,
        telephony …).
        """
        return ToolResult.success({"uri": f"lkap://concepts/{topic}", "markdown": content.concept(topic)})

    @registry.tool(annotations=READ, data="JSON")
    async def lkap_describe(
        kind: DescribeKind,
        id: Annotated[
            str,
            Field(
                description=(
                    "Model name, provider id, block type, node kind, pack id, tool name, recipe name, "
                    "or 'METHOD /v1/path' for a route"
                )
            ),
        ],  # noqa: A002
    ) -> ToolResult:
        """Describe one thing precisely: a JSON schema, provider spec, panel block, flow node, pack, built-in
        tool, recipe or api route.
        """
        match kind:
            case "schema":
                found = content.schema(id)
                if found is None:
                    return ToolResult.fail(
                        "not_found",
                        f"no exported schema named {id!r}",
                        details={"known": content.schema_names()},
                    )
                return ToolResult.success(found)
            case "provider":
                spec = content.provider_spec(id)
                if spec is None:
                    return ToolResult.fail(
                        "not_found", f"no provider with id {id!r}", hint="provider_list shows the registry"
                    )
                warnings: list[str] = []
                if ctx.allows("providers:read"):
                    try:
                        body = await client.get("/v1/providers")
                        for live in (body or {}).get("providers", []):
                            if live.get("id") == id:
                                for key in ("enabled", "installed_on", "default_credential_id"):
                                    spec[key] = live.get(key)
                    except ApiFailure as failure:
                        warnings.append(f"workspace enablement unavailable: {failure.code}")
                return ToolResult.success(spec, warnings=warnings)
            case "block":
                for block in content.block_catalog():
                    if block["type"] == id:
                        return ToolResult.success(block)
                return ToolResult.fail(
                    "not_found", f"no block type {id!r}", details={"known": list(content.BLOCK_TYPES)}
                )
            case "node":
                if ctx.allows("agents:read"):
                    body = await client.get("/v1/flows/node-specs")
                    for node in (body or {}).get("nodes", []):
                        if node.get("kind") == id:
                            return ToolResult.success(node)
                model = _NODE_MODELS.get(id)
                found = content.schema(model) if model else None
                if found is None:
                    return ToolResult.fail(
                        "not_found", f"no flow node kind {id!r}", details={"known": list(content.NODE_KINDS)}
                    )
                return ToolResult.success({"kind": id, "json_schema": found})
            case "pack":
                body = await client.get("/v1/packs")
                for item in (body or {}).get("items", []):
                    manifest = item.get("manifest", {})
                    if manifest.get("id") == id:
                        return ToolResult.success(manifest)
                return ToolResult.fail(
                    "not_found", f"no pack {id!r}", hint="lkap://packs lists the installed packs"
                )
            case "builtin_tool":
                for tool in content.builtin_tools().get("tools", []):
                    if tool.get("name") == id:
                        return ToolResult.success(tool)
                return ToolResult.fail("not_found", f"no built-in tool {id!r}")
            case "recipe":
                text = content.recipe(id)
                if text is None:
                    return ToolResult.fail(
                        "not_found", f"no recipe {id!r}", details={"known": content.recipe_names()}
                    )
                return ToolResult.success({"uri": f"lkap://recipes/{id}", "markdown": text})
            case "route":
                return ToolResult.success(await describe_route(ctx, id))

    @registry.tool(annotations=READ, data="SearchHit[]")
    async def lkap_search_docs(query: str, limit: Annotated[int, Field(ge=1, le=50)] = 10) -> ToolResult:
        """Keyword search over the guide, concept docs, recipes, provider registry, schemas and blocks."""
        return ToolResult.success(search_documents(query, limit))

    @registry.tool(annotations=READ, data="Me")
    async def me() -> ToolResult:
        """Who am I: workspace, key scopes, read-only and dial state, api health. Call before any write."""
        try:
            body = await client.get("/v1/api-keys/self")
        except ApiFailure as failure:
            return failure.to_result()
        identity = KeyIdentity.from_api(body, read_only=ctx.settings.read_only)
        ctx.identity = identity
        health: dict[str, Any] = {}
        warnings: list[str] = []
        try:
            raw = await client.get("/v1/health")
            health = {"db": raw.get("db"), "agents_unbound": raw.get("agents_unbound", 0)}
            api_version = raw.get("version")
        except ApiFailure as failure:
            api_version = None
            warnings.append(f"health unavailable: {failure.code}")
        if identity.allows("connections:read"):
            try:
                rows = await client.items("/v1/connections")
                health["connections"] = {
                    "n": len(rows),
                    "ok": sum(1 for row in rows if row.get("status") == "ok"),
                }
            except ApiFailure as failure:
                warnings.append(f"connections unavailable: {failure.code}")
        return ToolResult.success(
            {
                "workspace": identity.workspace,
                "key": {
                    "name": identity.name,
                    "prefix": identity.prefix,
                    "kind": identity.kind,
                    "client": identity.client,
                    "scopes": sorted(identity.scopes),
                    "expires_at": identity.expires_at,
                },
                "read_only": ctx.settings.read_only,
                "api_version": api_version,
                "health": health,
                "dial_enabled": ctx.settings.allow_dial and identity.allows("calls:write"),
                "transport": ctx.settings.transport,
            },
            warnings=warnings,
        )

    @registry.tool(annotations=READ, data="WorkspaceOut")
    async def workspace_get() -> ToolResult:
        """Read this key's workspace; the telephony dialing policy is shown as a read-only summary."""
        row = await own_workspace(ctx)
        if row is None:
            return ToolResult.fail("not_found", "the key's workspace is not visible")
        row["settings"] = reduce_telephony(row.get("settings"))
        return ToolResult.success(row)

    @registry.tool(scopes={"audit:read"}, annotations=READ, data="AuditOut[]")
    async def activity(
        limit: Annotated[int, Field(ge=1, le=200)] = 50,
        since: datetime | None = None,
        mine: Annotated[bool, Field(description="Only changes made with this API key")] = True,
        action_prefix: str | None = None,
    ) -> ToolResult:
        """Recent audit rows (newest first) with the client attribution of agent-made changes."""
        rows: list[dict[str, Any]] = []
        offset = 0
        while len(rows) < limit and offset < 2000:
            page = await client.get("/v1/audit", params={"limit": 200, "offset": offset})
            items = list((page or {}).get("items", []))
            if not items:
                break
            offset += len(items)
            for row in items:
                if mine and not (
                    row.get("actor_type") == "api_key"
                    and ctx.identity
                    and row.get("actor_id") == ctx.identity.id
                ):
                    continue
                if action_prefix and not str(row.get("action", "")).startswith(action_prefix):
                    continue
                if since is not None and _before(row.get("ts"), since):
                    continue
                rows.append(row)
            if since is not None and items and _before(items[-1].get("ts"), since):
                break
        return ToolResult.success(rows[:limit])


def _before(ts: Any, since: datetime) -> bool:
    try:
        value = datetime.fromisoformat(str(ts))
    except ValueError:
        return False
    if value.tzinfo is None and since.tzinfo is not None:
        value = value.replace(tzinfo=since.tzinfo)
    elif value.tzinfo is not None and since.tzinfo is None:
        since = since.replace(tzinfo=value.tzinfo)
    return value < since


async def describe_route(ctx: ServerContext, spec: str) -> dict[str, Any]:
    """The OpenAPI operation(s) for ``"METHOD /v1/path"`` or a bare path (template or concrete)."""
    from lkap_mcp.tools.generic import match_route

    method, _, path = spec.strip().partition(" ")
    if not path:
        method, path = "", method
    document = await ctx.client.openapi()
    operations: list[dict[str, Any]] = []
    for verb in [method.upper()] if method else ["GET", "POST", "PUT", "DELETE"]:
        matched = match_route(document, verb, path)
        if matched is not None:
            operations.append(matched)
    if not operations:
        raise ApiFailure(None, "not_found", f"no api route matches {spec!r}")
    return {"operations": operations}
