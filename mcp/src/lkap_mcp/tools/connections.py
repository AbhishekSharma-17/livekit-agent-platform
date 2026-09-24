"""Connections and fleet tools (``AGENT-ACCESS.md`` §4.2)."""

from __future__ import annotations

from typing import Annotated, Any, Literal
from urllib.parse import urlparse

from pydantic import Field

from lkap_mcp.client import ApiFailure
from lkap_mcp.registry import DESTRUCTIVE, READ, WRITE, Registry
from lkap_mcp.results import ToolResult
from lkap_mcp.tools._common import forbidden, parse, placeholders, planned, request, resolve_all, seg, slugify

SecretArg = Annotated[
    str,
    Field(
        description=(
            "The secret: the value itself (pasted by the user), or a reference env:NAME, "
            "file:/abs/path, file:/abs/path#KEY; raw:<value> for a literal starting with env:/file:"
        )
    ),
]
DeploymentType = Literal["cloud", "self_hosted"]
DeploymentMode = Literal["external", "supervised", "cloud_hosted"]
EnvFormat = Literal["env", "compose", "lk"]
FleetAction = Literal["start", "stop", "restart"]


def infer_deployment_type(url: str) -> str:
    """``cloud`` for ``*.livekit.cloud`` urls, else ``self_hosted``."""
    host = (urlparse(url).hostname or "").lower()
    return "cloud" if host.endswith(".livekit.cloud") else "self_hosted"


def fleet_summary(fleet: dict[str, Any]) -> dict[str, Any]:
    """``{desired, ready, instances}`` from a ``FleetStatus``."""
    instances = fleet.get("instances") or []
    return {
        "desired_replicas": fleet.get("desired_replicas", 0),
        "instances": len(instances),
        "ready": sum(1 for instance in instances if instance.get("status") == "ready"),
    }


def _worker_steps(connection: dict[str, Any]) -> list[str]:
    cid = connection.get("id")
    if connection.get("deployment_mode") == "supervised":
        return [f'Start the pool: connection_fleet(id="{cid}", action="start").']
    return [
        f'Run a worker for it: connection_get(id="{cid}", include_worker_env=true) gives the env template.',
        "Bind agents to it with agent_create(connection_id=...) or agent_update(connection_id=...).",
    ]


def register(registry: Registry) -> None:
    """Declare the connection tools."""
    ctx = registry.ctx
    client = ctx.client

    @registry.tool(scopes={"connections:read"}, annotations=READ, data="ConnectionOut[]")
    async def connection_list() -> ToolResult:
        """List LiveKit connections: status, capabilities, fingerprints (never secrets), fleet summary."""
        rows = await client.items("/v1/connections", params={"limit": 200})
        warnings: list[str] = []
        for row in rows:
            try:
                row["fleet"] = fleet_summary(await client.get(f"/v1/connections/{seg(row['id'])}/fleet"))
            except ApiFailure as failure:
                warnings.append(f"fleet of {row.get('slug')}: {failure.code}")
        return ToolResult.success(rows, warnings=warnings)

    @registry.tool(scopes={"connections:read"}, annotations=READ, data="ConnectionOut")
    async def connection_get(
        id: str,  # noqa: A002
        include_worker_env: bool = False,
        env_format: EnvFormat = "env",
    ) -> ToolResult:
        """One connection with its fleet status and, on request, the worker env template (secrets as <NAME>
        placeholders).
        """
        row = await client.get(f"/v1/connections/{seg(id)}")
        row["fleet"] = await client.get(f"/v1/connections/{seg(id)}/fleet")
        if include_worker_env:
            row["worker_env"] = await client.get(
                f"/v1/connections/{seg(id)}/worker-env", params={"format": env_format}
            )
        return ToolResult.success(row)

    @registry.tool(scopes={"connections:write"}, annotations=WRITE, data="ConnectionOut")
    async def connection_create(
        name: str,
        url: Annotated[str, Field(description="The LiveKit server url, e.g. wss://<project>.livekit.cloud")],
        api_key: SecretArg,
        api_secret: SecretArg,
        slug: str | None = None,
        deployment_type: DeploymentType | None = None,
        agent_name: Annotated[
            str, Field(description="The worker's agent name; unique in the LiveKit project")
        ] = "lkap-agent",
        deployment_mode: DeploymentMode = "external",
        worker_image: Literal["slim", "full"] = "slim",
        use_inference: bool = True,
        is_default: bool = False,
        test_first: bool = True,
        plan: Annotated[bool, Field(description="Return the requests without sending them")] = False,
    ) -> ToolResult:
        """Connect a LiveKit project from its url, API key and secret (tested first); secrets go straight to
        the vault.
        """
        secrets = {
            "api_key": parse(ctx, api_key, "api_key"),
            "api_secret": parse(ctx, api_secret, "api_secret"),
        }
        body: dict[str, Any] = {
            "slug": slug or slugify(name),
            "name": name,
            "url": url,
            "deployment_type": deployment_type or infer_deployment_type(url),
            "agent_name": agent_name,
            "deployment_mode": deployment_mode,
            "worker_image": worker_image,
            "use_inference": use_inference,
            "is_default": is_default,
        }
        if plan:
            shown = {**body, **placeholders(secrets)}
            steps = [request("POST", "/v1/connections/test", shown, note="probe first")] if test_first else []
            return planned(*steps, request("POST", "/v1/connections", shown))
        values, warnings = resolve_all(secrets)
        payload = {**body, **values}
        test: dict[str, Any] | None = None
        if test_first:
            test = await client.post("/v1/connections/test", payload)
            if not (test or {}).get("ok"):
                return ToolResult.fail(
                    "connection_test_failed",
                    str((test or {}).get("message") or "the connection test failed"),
                    data={"test": test},
                    hint="Check the url, key and secret; nothing was saved.",
                )
        created = await client.post("/v1/connections", payload)
        return ToolResult.success(
            {"connection": created, "test": test}, warnings=warnings, next_steps=_worker_steps(created)
        )

    @registry.tool(scopes={"connections:write"}, annotations=WRITE, data="ConnectionTestResult")
    async def connection_test(id: str) -> ToolResult:  # noqa: A002
        """Probe a saved connection against LiveKit and refresh its capabilities."""
        return ToolResult.success(await client.post(f"/v1/connections/{seg(id)}/test"))

    @registry.tool(
        scopes={"connections:read"},
        annotations=DESTRUCTIVE,
        data="FleetStatus",
        write_scopes={"connections:write"},
    )
    async def connection_fleet(
        id: str,  # noqa: A002
        action: FleetAction | None = None,
        replicas: Annotated[int | None, Field(ge=0, le=50)] = None,
        confirm: bool = False,
        plan: bool = False,
    ) -> ToolResult:
        """Read a connection's worker pool, or start/stop/restart a supervised pool (stop and restart need
        confirm).
        """
        path = f"/v1/connections/{seg(id)}/fleet"
        if action is None:
            fleet = await client.get(path)
            return ToolResult.success({**fleet, "summary": fleet_summary(fleet)})
        if not ctx.allows("connections:write"):
            return forbidden("connections:write")
        body = {"action": action, "replicas": replicas}
        if plan:
            return planned(request("POST", path, body))
        if action in {"stop", "restart"} and not confirm:
            return ToolResult.needs_confirmation(
                f"{action} every worker of connection {id}: live sessions on this pool end"
            )
        return ToolResult.success(await client.post(path, body))

    @registry.tool(scopes={"connections:write"}, annotations=DESTRUCTIVE, data="ConnectionOut")
    async def connection_rotate(
        id: str,  # noqa: A002
        api_key: SecretArg,
        api_secret: SecretArg,
        confirm: bool = False,
        plan: bool = False,
    ) -> ToolResult:
        """Replace a connection's LiveKit key and secret (supervised pools roll onto the new credentials)."""
        secrets = {
            "api_key": parse(ctx, api_key, "api_key"),
            "api_secret": parse(ctx, api_secret, "api_secret"),
        }
        path = f"/v1/connections/{seg(id)}/rotate"
        if plan:
            return planned(request("POST", path, placeholders(secrets)))
        if not confirm:
            return ToolResult.needs_confirmation(
                f"replace the LiveKit key and secret of connection {id}; the old ones stop working here"
            )
        values, warnings = resolve_all(secrets)
        rotated = await client.post(path, values)
        return ToolResult.success(
            rotated,
            warnings=warnings,
            next_steps=[
                "Supervised pools restart onto the new credentials; external workers need the new env."
            ],
        )
