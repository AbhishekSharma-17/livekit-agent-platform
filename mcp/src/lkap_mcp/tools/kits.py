"""Tool kits: a use case in one step — tools, panel blocks, instructions, variables, rules (V6-18).

``kit_list`` shows the catalogue (look a record up, open a case, take down details, verify the
caller, send a payment link, hand over to the team, log the call, book appointments) with each
kit's variants and settings; ``kit_add`` adds one kit to one agent in one new configuration
version, or previews it with ``dry_run=true``. Adding a kit again with the same prefix adds
nothing. No key is needed: without ``secret_key_id`` the HTTP tools carry no key header.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

from lkap_mcp.registry import READ, WRITE, Registry
from lkap_mcp.results import ToolResult
from lkap_mcp.tools._common import planned, request, seg

SETTINGS_HELP = "Values of the kit's settings (kit_list shows them), e.g. {'base_url': 'https://api.example.com/v1/records'}"


def _variant_summary(variant: dict[str, Any]) -> dict[str, Any]:
    tools = [
        (tool.get("definition") or {}).get("name") or tool.get("template")
        for tool in variant.get("tools", [])
    ]
    apps = [app.get("toolkit") for app in variant.get("apps", [])]
    return {
        "id": variant.get("id"),
        "label": variant.get("label"),
        "source": variant.get("source"),
        "summary": variant.get("summary"),
        "tools": [name for name in tools if name],
        "apps": apps,
        "requires": variant.get("requires"),
    }


def _summary(kit: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": kit.get("id"),
        "name": kit.get("name"),
        "summary": kit.get("summary"),
        "default_prefix": kit.get("default_prefix"),
        "default_variant": kit.get("default_variant"),
        "variants": [_variant_summary(v) for v in kit.get("variants", []) if isinstance(v, dict)],
        "settings": [
            {key: setting.get(key) for key in ("name", "label", "kind", "required", "example", "variants")}
            for setting in kit.get("defaults", [])
            if isinstance(setting, dict)
        ],
        "adds": {
            "blocks": [block.get("id") for block in kit.get("blocks", []) if isinstance(block, dict)],
            "rules": [rule.get("id") for rule in kit.get("rules", []) if isinstance(rule, dict)],
            "variables": [field.get("name") for field in kit.get("variables", []) if isinstance(field, dict)],
            "flow_steps": bool(kit.get("flow_nodes"))
            or any(isinstance(v, dict) and v.get("flow_nodes") for v in kit.get("variants", [])),
        },
    }


def register(registry: Registry) -> None:
    """Declare the tool kit tools."""
    client = registry.ctx.client

    @registry.tool(scopes={"agents:read"}, annotations=READ, data="ToolKitsResponse")
    async def kit_list(
        kit_id: Annotated[
            str | None, Field(description="One kit's full definition (blocks, rules, snippet, tools)")
        ] = None,
    ) -> ToolResult:
        """List the tool kits (ready-made use cases) with their variants and settings, or show one in full."""
        if kit_id is not None:
            return ToolResult.success(await client.get(f"/v1/tool-kits/{seg(kit_id)}"))
        body = await client.get("/v1/tool-kits")
        items = body.get("items", []) if isinstance(body, dict) else []
        return ToolResult.success(
            [_summary(item) for item in items if isinstance(item, dict)],
            next_steps=[
                "Preview one: kit_add(kit_id=..., agent_id=..., settings={...}, dry_run=true). Then run it "
                "without dry_run."
            ],
        )

    @registry.tool(scopes={"agents:write"}, annotations=WRITE, data="ToolKitInstantiated")
    async def kit_add(
        kit_id: str,
        agent_id: str,
        variant: Annotated[str | None, Field(description="A variant id (default: the kit's default)")] = None,
        block_prefix: Annotated[
            str | None,
            Field(
                pattern=r"^[a-z][a-z0-9_]{0,23}$",
                description="Names the tools, blocks and rules the kit adds (default: the kit's)",
            ),
        ] = None,
        settings: Annotated[
            dict[str, str | int | float | bool] | None, Field(description=SETTINGS_HELP)
        ] = None,
        secret_key_id: Annotated[
            str | None,
            Field(
                description="A tool-secret key holding the variant's secret names (binding it needs "
                "providers:write). Without it the HTTP tools carry no key header"
            ),
        ] = None,
        connection_id: Annotated[
            str | None, Field(description="A connected app (apps_connections) for a composio_action variant")
        ] = None,
        dataset_id: Annotated[str | None, Field(description="A lookup table for a dataset variant")] = None,
        key_columns: Annotated[
            list[str] | None, Field(description="The table's key columns to match on (default: all of them)")
        ] = None,
        flow_anchor: Annotated[
            str | None, Field(description="A flow step to add the kit's flow steps after (flow agents only)")
        ] = None,
        add_test_case: bool = True,
        dry_run: Annotated[
            bool, Field(description="List what would be added and validate it. Change nothing")
        ] = False,
        plan: bool = False,
    ) -> ToolResult:
        """Add a tool kit to an agent: tools, panel blocks, instructions, variables and rules."""
        body: dict[str, Any] = {
            "agent_id": agent_id,
            "settings": settings or {},
            "add_test_case": add_test_case,
            "dry_run": dry_run,
        }
        optional = {
            "variant": variant,
            "block_prefix": block_prefix,
            "credential_id": secret_key_id,
            "connection_id": connection_id,
            "dataset_id": dataset_id,
            "key_columns": key_columns,
            "flow_anchor": flow_anchor,
        }
        body.update({key: value for key, value in optional.items() if value is not None})
        path = f"/v1/tool-kits/{seg(kit_id)}/instantiate"
        if plan:
            return planned(request("POST", path, body))
        added = await client.post(path, body)
        data = added if isinstance(added, dict) else {}
        validation = data.get("validation") if isinstance(data.get("validation"), dict) else {}
        next_steps = [str(note) for note in data.get("notes", [])]
        if dry_run:
            next_steps.append("Run it again without dry_run to add it.")
        else:
            next_steps.append(f'Check the agent: agent_validate(id_or_slug="{agent_id}").')
        return ToolResult.success(
            added,
            warnings=[str(w) for w in (validation or {}).get("warnings", [])],
            next_steps=next_steps,
        )
