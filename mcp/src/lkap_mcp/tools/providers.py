"""Provider and provider-key tools (``AGENT-ACCESS.md`` §4.3).

"Key" is the console's word (``/console/keys``); the api path is ``/v1/credentials``.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from lkap_contracts.providers import CatalogKind, ProviderKind
from pydantic import Field

from lkap_mcp import content
from lkap_mcp.registry import IDEMPOTENT_WRITE, READ, WRITE, Registry
from lkap_mcp.results import ToolResult, untrusted
from lkap_mcp.tools._common import matches, parse, placeholders, planned, request, resolve_all, seg

AvailabilityFilter = Literal["available", "deferred", "incompatible", "removed", "all"]

_COMPACT_KEYS = (
    "id",
    "label",
    "vendor",
    "kind",
    "availability",
    "verification",
    "worker_image",
    "requires_credential",
    "enabled",
    "installed_on",
    "default_credential_id",
    "capabilities",
)


def secret_field_names(provider_id: str) -> list[str] | None:
    """The registry's ``secret_fields`` names for a provider, or ``None`` when unknown."""
    spec = content.provider_spec(provider_id)
    if spec is None:
        return None
    return [str(field.get("name")) for field in spec.get("secret_fields") or []]


def register(registry: Registry) -> None:
    """Declare the provider tools."""
    ctx = registry.ctx
    client = ctx.client

    @registry.tool(scopes={"providers:read"}, annotations=READ, data="ProviderRow[]")
    async def provider_list(
        kind: ProviderKind | None = None,
        enabled: bool | None = None,
        installed_on: Annotated[str | None, Field(description="A connection id")] = None,
        query: str | None = None,
        availability: AvailabilityFilter = "available",
    ) -> ToolResult:
        """List registry providers (STT, LLM, TTS, realtime, avatars …) with workspace enablement and install
        state.
        """
        body = await client.get(
            "/v1/providers",
            params={
                "kind": kind,
                "availability": None if availability == "all" else availability,
                "enabled": enabled,
            },
        )
        rows: list[dict[str, Any]] = []
        for spec in (body or {}).get("providers", []):
            if installed_on and installed_on not in (spec.get("installed_on") or []):
                continue
            if not matches(query, spec.get("id"), spec.get("label"), spec.get("vendor")):
                continue
            rows.append({key: spec.get(key) for key in _COMPACT_KEYS})
        return ToolResult.success(rows)

    @registry.tool(scopes={"providers:read"}, annotations=READ, data="CatalogResponse")
    async def provider_catalog(
        provider_id: str,
        kind: CatalogKind,
        key_id: Annotated[str | None, Field(description="A provider key (credential) id")] = None,
        refresh: bool = False,
    ) -> ToolResult:
        """A provider's models, voices, avatars or personas (vendor labels are untrusted data)."""
        body = await client.get(
            f"/v1/providers/{seg(provider_id)}/catalog",
            params={"kind": kind, "credential_id": key_id, "refresh": refresh or None},
        )
        items = [
            {**item, "label": untrusted(item.get("label", ""), f"catalog:{provider_id}")}
            for item in (body or {}).get("items", [])
        ]
        return ToolResult.success({**(body or {}), "items": items})

    @registry.tool(scopes={"providers:write"}, annotations=IDEMPOTENT_WRITE, data="ProviderOut")
    async def provider_settings(
        provider_id: str,
        enabled: bool | None = None,
        default_key_id: str | None = None,
        plan: bool = False,
    ) -> ToolResult:
        """Enable or disable a provider for the workspace, or set its default key."""
        current: dict[str, Any] = {}
        listed = await client.get("/v1/providers")
        for spec in (listed or {}).get("providers", []):
            if spec.get("id") == provider_id:
                current = spec
        if not current:
            return ToolResult.fail("not_found", f"no provider with id {provider_id!r}")
        body = {
            "enabled": current.get("enabled", True) if enabled is None else enabled,
            "default_credential_id": current.get("default_credential_id")
            if default_key_id is None
            else default_key_id,
        }
        path = f"/v1/providers/{seg(provider_id)}/settings"
        if plan:
            return planned(request("PUT", path, body))
        return ToolResult.success(await client.put(path, body))

    @registry.tool(scopes={"providers:write"}, annotations=WRITE, data="CredentialOut")
    async def provider_key_create(
        provider_id: str,
        label: str,
        secrets: Annotated[
            dict[str, str],
            Field(
                description=(
                    "One entry per provider secret field (see lkap_describe('provider', id)): the value "
                    "itself, or env:NAME / file:/abs/path / file:/abs/path#KEY"
                )
            ),
        ],
        test: bool = True,
        plan: bool = False,
    ) -> ToolResult:
        """Store a vendor key for a provider in the vault (returns a fingerprint, never the key); test it."""
        parsed = {name: parse(ctx, raw, f"secrets.{name}") for name, raw in secrets.items()}
        known = secret_field_names(provider_id)
        if known is None:
            return ToolResult.fail("not_found", f"no provider with id {provider_id!r}")
        unknown = sorted(set(parsed) - set(known)) if known else []
        if unknown:
            return ToolResult.fail(
                "unknown_secret_field",
                f"{provider_id} has no secret field(s) {unknown}",
                details={"secret_fields": known},
            )
        if plan:
            steps = [
                request(
                    "POST",
                    "/v1/credentials",
                    {"provider_id": provider_id, "label": label, "secrets": placeholders(parsed)},
                )
            ]
            if test:
                steps.append(request("POST", "/v1/credentials/{id}/test", note="after create"))
            return planned(*steps)
        values, warnings = resolve_all(parsed)
        created = await client.post(
            "/v1/credentials", {"provider_id": provider_id, "label": label, "secrets": values}
        )
        result: dict[str, Any] = {"key": created}
        if test:
            result["test"] = await client.post(f"/v1/credentials/{seg(created['id'])}/test")
        return ToolResult.success(result, warnings=warnings)

    @registry.tool(scopes={"providers:read"}, annotations=READ, data="CredentialOut[]")
    async def provider_key_list(provider_id: str | None = None) -> ToolResult:
        """List stored provider keys (label and fingerprint only)."""
        return ToolResult.success(await client.items("/v1/credentials", params={"provider_id": provider_id}))

    @registry.tool(scopes={"providers:write"}, annotations=WRITE, data="CredentialTestResult")
    async def provider_key_test(key_id: str) -> ToolResult:
        """Test a stored provider key against its vendor."""
        return ToolResult.success(await client.post(f"/v1/credentials/{seg(key_id)}/test"))
