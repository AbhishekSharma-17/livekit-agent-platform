"""Agent tools (``AGENT-ACCESS.md`` §4.4).

``agent_update`` applies an RFC 7386 merge patch over the *stored* config,
validates the result locally against ``AgentConfig`` and sends the whole
config; the api validates again and answers 422 with ``issues``, which the
tool relays. ``mode`` is never sent: the api derives it from ``config.flow``
(R-V2-12), so ``patch={"flow": {...}}`` switches to a flow and
``patch={"flow": null}`` back to a prompt.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from lkap_contracts.agent_config import AgentConfig, AgentLimits
from lkap_contracts.common import Issue
from lkap_contracts.flow import FlowSpec
from pydantic import Field, ValidationError

from lkap_mcp.client import ApiFailure, LkapClient
from lkap_mcp.registry import DESTRUCTIVE, IDEMPOTENT_WRITE, READ, WRITE, Registry, ServerContext
from lkap_mcp.results import ToolResult
from lkap_mcp.tools._common import forbidden, matches, merge_patch, planned, request, resolve_agent, seg

Patch = Annotated[
    dict[str, Any],
    Field(description="RFC 7386 JSON merge patch over the agent's current config (null removes a key)"),
]

_LIST_KEYS = (
    "id",
    "slug",
    "name",
    "pack_id",
    "mode",
    "published",
    "connection_id",
    "config_version",
    "archived_at",
    "session_count",
    "last_session_at",
)


def config_issues(error: ValidationError) -> list[Issue]:
    """``AgentConfig`` validation errors as addressable issues (no input values)."""
    return [
        Issue(path=".".join(str(part) for part in err["loc"]), message=err["msg"]) for err in error.errors()
    ]


async def validate_saved(ctx: ServerContext, agent_id: str) -> tuple[Any, list[str]]:
    """``POST /v1/agents/{id}/validate`` when the key may (it is a write route)."""
    if not ctx.allows("agents:write"):
        return None, ["validation skipped: it needs the agents:write scope"]
    return await ctx.client.post(f"/v1/agents/{seg(agent_id)}/validate"), []


def session_path(agent: dict[str, Any]) -> str:
    """The console path of the public session page."""
    return f"/s/{agent.get('slug')}"


async def _update(client: LkapClient, agent_id: str, body: dict[str, Any]) -> dict[str, Any]:
    updated = await client.put(f"/v1/agents/{seg(agent_id)}", body)
    return dict(updated) if isinstance(updated, dict) else {}


#: ``agent_create``'s next steps when no starter supplies its own.
DEFAULT_CREATE_NEXT_STEPS = (
    "Attach knowledge and tools with agent_attach, then agent_validate.",
    "Test it with a chat before agent_publish.",
)


async def template_next_steps(client: LkapClient, template_id: str) -> list[str]:
    """The starter's ``next_steps`` labels (``GET /v1/templates/{id}``), plus the test-first reminder."""
    body = await client.get(f"/v1/templates/{seg(template_id)}")
    steps = ((body or {}).get("template") or {}).get("next_steps") or []
    labels = [str(step.get("label")) for step in steps if isinstance(step, dict) and step.get("label")]
    if not labels:
        return []
    return [*labels, DEFAULT_CREATE_NEXT_STEPS[1]]


def register(registry: Registry) -> None:
    """Declare the agent tools."""
    ctx = registry.ctx
    client = ctx.client

    @registry.tool(scopes={"agents:read"}, annotations=READ, data="AgentRow[]")
    async def agent_list(
        query: str | None = None,
        mode: Literal["prompt", "flow"] | None = None,
        published: bool | None = None,
        archived: bool = False,
        connection_id: str | None = None,
        limit: Annotated[int, Field(ge=1, le=500)] = 50,
    ) -> ToolResult:
        """List agents (compact rows: id, slug, name, pack, mode, published, connection, config version,
        sessions).
        """
        rows = await client.items(
            "/v1/agents",
            params={
                "mode": mode,
                "published": published,
                "archived": archived,
                "connection_id": connection_id,
            },
        )
        compact = [
            {key: row.get(key) for key in _LIST_KEYS}
            for row in rows
            if matches(query, row.get("name"), row.get("slug"), row.get("description"))
        ]
        return ToolResult.success(compact[:limit])

    @registry.tool(scopes={"agents:read"}, annotations=READ, data="AgentOut")
    async def agent_get(
        id_or_slug: str, include_config: bool = True, include_validation: bool = False
    ) -> ToolResult:
        """One agent with its full config; optionally its validation result."""
        agent = await resolve_agent(client, id_or_slug)
        warnings: list[str] = []
        result: dict[str, Any] = {"agent": agent}
        if not include_config:
            agent.pop("config", None)
        if include_validation:
            result["validation"], warnings = await validate_saved(ctx, agent["id"])
        return ToolResult.success(result, warnings=warnings)

    @registry.tool(scopes={"agents:write"}, annotations=WRITE, data="AgentOut")
    async def agent_create(
        name: str,
        template_id: Annotated[
            str | None,
            Field(
                description=(
                    "A starter id from lkap://templates (blank, knowledge_assistant, receptionist, "
                    "vision_assistant, phone_agent, lead_qualification, survey_intake, insurance_claim); "
                    "it seeds the config, knowledge bases and HTTP tools and wins over pack_id"
                )
            ),
        ] = None,
        pack_id: Annotated[
            str,
            Field(description="A pack without a starter (rare); ignored when template_id is set"),
        ] = "generic",
        description: str = "",
        connection_id: str | None = None,
        config: Annotated[
            AgentConfig | None,
            Field(description="A whole config; omit to seed it from the starter or the pack"),
        ] = None,
        patch: Annotated[
            dict[str, Any] | None,
            Field(description="A merge patch applied after seeding (e.g. instructions)"),
        ] = None,
        plan: bool = False,
    ) -> ToolResult:
        """Create an agent from a starter template (lkap://templates) or a pack, then validate it."""
        body: dict[str, Any] = {"name": name, "pack_id": pack_id, "description": description}
        if template_id:
            body["template_id"] = template_id
        if connection_id:
            body["connection_id"] = connection_id
        if config is not None:
            body["config"] = config.model_dump(mode="json")
        if plan:
            steps = [request("POST", "/v1/agents", body)]
            if patch:
                steps.append(
                    request(
                        "PUT", "/v1/agents/{id}", {"config": "<seeded config + patch>"}, note="after create"
                    )
                )
            return planned(*steps)
        agent = await client.post("/v1/agents", body)
        warnings: list[str] = []
        issues: list[Issue] = []
        if patch:
            merged = merge_patch(agent.get("config") or {}, patch)
            try:
                AgentConfig.model_validate(merged)
            except ValidationError as error:
                issues = config_issues(error)
                warnings.append("the patch was not applied: the patched config is invalid (see issues)")
            else:
                try:
                    agent = await _update(client, agent["id"], {"config": merged})
                except ApiFailure as failure:
                    issues = failure.to_result().issues
                    warnings.append(f"the patch was not applied: {failure.message}")
        validation, more = await validate_saved(ctx, agent["id"])
        next_steps = list(DEFAULT_CREATE_NEXT_STEPS)
        if template_id:
            try:
                next_steps = await template_next_steps(client, template_id) or next_steps
            except ApiFailure as failure:
                more.append(f"the starter's next steps are unavailable: {failure.code}")
        return ToolResult.success(
            {"agent": agent, "validation": validation},
            warnings=warnings + more,
            issues=issues,
            next_steps=next_steps,
        )

    @registry.tool(scopes={"agents:write"}, annotations=IDEMPOTENT_WRITE, data="AgentOut")
    async def agent_update(
        id_or_slug: str,
        patch: Patch | None = None,
        config: Annotated[
            AgentConfig | None, Field(description="A whole config (instead of a patch)")
        ] = None,
        name: str | None = None,
        description: str | None = None,
        connection_id: str | None = None,
        validate_first: bool = True,
        save_with_errors: bool = False,
        plan: bool = False,
    ) -> ToolResult:
        """Change an agent: merge-patch (or replace) its config, rename, rebind; validated before saving.

        Background tools (docs ``concepts/tools``): ``patch={"tools": {"execution_default": "auto"}}``
        lets the read tools (GET HTTP tools, ``search_knowledge``, ``http_request`` GETs,
        ``describe_current_frame``) announce and finish in the background; writes, MCP tools,
        telephony, forms and flow edges never follow it. ``tools.builtin_execution[name]`` sets
        one read built-in; ``voice.thinking_sound`` plays a clip during blocking waits. Keep
        ``tools.max_tool_steps`` at 4 or more with a background default.
        """
        if patch is not None and config is not None:
            return ToolResult.fail("invalid_input", "pass either patch or config, not both")
        agent = await resolve_agent(client, id_or_slug)
        body: dict[str, Any] = {
            key: value
            for key, value in {
                "name": name,
                "description": description,
                "connection_id": connection_id,
            }.items()
            if value is not None
        }
        if patch is not None or config is not None:
            candidate = (
                merge_patch(agent.get("config") or {}, patch)
                if patch is not None
                else config.model_dump(mode="json")  # type: ignore[union-attr]
            )
            if validate_first and not save_with_errors:
                try:
                    AgentConfig.model_validate(candidate)
                except ValidationError as error:
                    return ToolResult.fail(
                        "invalid_config",
                        "the resulting config is invalid; nothing was saved",
                        issues=config_issues(error),
                    )
            body["config"] = candidate
        if not body:
            return ToolResult.fail(
                "invalid_input", "nothing to change: pass patch, config, name, description or connection_id"
            )
        path = f"/v1/agents/{seg(agent['id'])}"
        if plan:
            return planned(request("PUT", path, body))
        try:
            updated = await _update(client, agent["id"], body)
        except ApiFailure as failure:
            if failure.status != 422:
                raise
            # The api validates every saved config; save_with_errors cannot override it.
            result = failure.to_result()
            if result.error is not None:
                result.error.message = f"{failure.message}; nothing was saved"
            return result
        validation, warnings = await validate_saved(ctx, updated["id"])
        if agent.get("config_version") != updated.get("config_version"):
            warnings.append(
                f"config_version {agent.get('config_version')} -> {updated.get('config_version')}; "
                "agent_versions restores an earlier one"
            )
        return ToolResult.success({"agent": updated, "validation": validation}, warnings=warnings)

    @registry.tool(scopes={"agents:write"}, annotations=READ, data="ValidationResult")
    async def agent_validate(id_or_slug: str) -> ToolResult:
        """Validate an agent's saved config (providers, keys, tools, knowledge, flow, panel)."""
        agent = await resolve_agent(client, id_or_slug)
        return ToolResult.success(await client.post(f"/v1/agents/{seg(agent['id'])}/validate"))

    @registry.tool(scopes={"agents:write"}, annotations=IDEMPOTENT_WRITE, data="AgentOut")
    async def agent_publish(id_or_slug: str, published: bool = True, plan: bool = False) -> ToolResult:
        """Publish (or unpublish) an agent so its public session page accepts callers."""
        agent = await resolve_agent(client, id_or_slug)
        path = f"/v1/agents/{seg(agent['id'])}"
        if plan:
            return planned(request("PUT", path, {"published": published}))
        updated = await _update(client, agent["id"], {"published": published})
        data: dict[str, Any] = {"agent": updated}
        if published:
            data["session_path"] = session_path(updated)
        return ToolResult.success(data)

    @registry.tool(scopes={"agents:write"}, annotations=DESTRUCTIVE, data="AgentOut")
    async def agent_archive(id_or_slug: str, archive: bool = True, confirm: bool = False) -> ToolResult:
        """Archive an agent (blocks new sessions; needs confirm) or unarchive it."""
        agent = await resolve_agent(client, id_or_slug)
        if archive and not confirm:
            return ToolResult.needs_confirmation(
                f"archive agent {agent.get('name')!r}: new sessions are refused until it is unarchived"
            )
        action = "archive" if archive else "unarchive"
        return ToolResult.success(await client.post(f"/v1/agents/{seg(agent['id'])}/{action}"))

    @registry.tool(
        scopes={"agents:read"},
        annotations=DESTRUCTIVE,
        data="ConfigVersionOut[]",
        write_scopes={"agents:write"},
    )
    async def agent_versions(
        id_or_slug: str,
        get: Annotated[int | None, Field(description="Return this config version in full")] = None,
        restore: Annotated[
            int | None, Field(description="Restore this config version (needs confirm)")
        ] = None,
        confirm: bool = False,
        plan: bool = False,
    ) -> ToolResult:
        """List an agent's config versions, read one, or restore one (restore needs confirm)."""
        agent = await resolve_agent(client, id_or_slug)
        base = f"/v1/agents/{seg(agent['id'])}/versions"
        if restore is not None:
            if not ctx.allows("agents:write"):
                return forbidden("agents:write")
            path = f"{base}/{restore}/restore"
            if plan:
                return planned(request("POST", path))
            if not confirm:
                return ToolResult.needs_confirmation(
                    f"replace the current config (v{agent.get('config_version')}) with version {restore}"
                )
            return ToolResult.success(await client.post(path))
        if get is not None:
            return ToolResult.success(await client.get(f"{base}/{get}"))
        return ToolResult.success(await client.items(base, params={"limit": 100}))

    @registry.tool(scopes={"agents:write"}, annotations=IDEMPOTENT_WRITE, data="AgentOut")
    async def agent_attach(
        id_or_slug: str,
        kb_ids: list[str] | None = None,
        tool_ids: list[str] | None = None,
        remove: bool = False,
        plan: bool = False,
    ) -> ToolResult:
        """Attach (or detach with remove=true) knowledge bases and tools to an agent, then validate."""
        if not kb_ids and not tool_ids:
            return ToolResult.fail("invalid_input", "pass kb_ids and/or tool_ids")
        agent = await resolve_agent(client, id_or_slug)
        config = dict(agent.get("config") or {})

        def combine(current: list[str], change: list[str] | None) -> list[str]:
            if not change:
                return current
            if remove:
                return [item for item in current if item not in change]
            return current + [item for item in change if item not in current]

        knowledge = dict(config.get("knowledge") or {})
        tools = dict(config.get("tools") or {})
        knowledge["kb_ids"] = combine(list(knowledge.get("kb_ids") or []), kb_ids)
        tools["tool_ids"] = combine(list(tools.get("tool_ids") or []), tool_ids)
        config["knowledge"], config["tools"] = knowledge, tools
        path = f"/v1/agents/{seg(agent['id'])}"
        if plan:
            return planned(request("PUT", path, {"config": config}))
        updated = await _update(client, agent["id"], {"config": config})
        validation, warnings = await validate_saved(ctx, updated["id"])
        return ToolResult.success({"agent": updated, "validation": validation}, warnings=warnings)

    @registry.tool(
        scopes={"agents:read"},
        annotations=IDEMPOTENT_WRITE,
        data="AgentLimits",
        write_scopes={"agents:write"},
    )
    async def agent_limits(
        id_or_slug: str,
        limits: AgentLimits | None = None,
        allowed_origins: list[str] | None = None,
        plan: bool = False,
    ) -> ToolResult:
        """Read an agent's session limits and embed origins, or set them."""
        agent = await resolve_agent(client, id_or_slug)
        path = f"/v1/agents/{seg(agent['id'])}/limits"
        if limits is None and allowed_origins is None:
            current = await client.get(path)
            return ToolResult.success(
                {"limits": current, "allowed_origins": agent.get("allowed_origins", [])}
            )
        if not ctx.allows("agents:write"):
            return forbidden("agents:write")
        steps = []
        if limits is not None:
            steps.append(request("PUT", path, limits.model_dump(mode="json")))
        if allowed_origins is not None:
            steps.append(
                request("PUT", f"/v1/agents/{seg(agent['id'])}", {"allowed_origins": allowed_origins})
            )
        if plan:
            return planned(*steps)
        out: dict[str, Any] = {"limits": None, "allowed_origins": agent.get("allowed_origins", [])}
        if limits is not None:
            out["limits"] = await client.put(path, limits.model_dump(mode="json"))
        else:
            out["limits"] = await client.get(path)
        if allowed_origins is not None:
            updated = await _update(client, agent["id"], {"allowed_origins": allowed_origins})
            out["allowed_origins"] = updated.get("allowed_origins", [])
        return ToolResult.success(out)

    @registry.tool(scopes={"agents:write"}, annotations=READ, data="ValidationResult")
    async def agent_flow_validate(id_or_slug: str, flow: FlowSpec) -> ToolResult:
        """Validate a flow for an agent without saving it."""
        agent = await resolve_agent(client, id_or_slug)
        return ToolResult.success(
            await client.post(
                f"/v1/agents/{seg(agent['id'])}/flow/validate", {"flow": flow.model_dump(mode="json")}
            )
        )
