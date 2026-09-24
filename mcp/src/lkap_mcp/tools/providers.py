"""Provider and provider-key tools (``AGENT-ACCESS.md`` §4.3), and the custom-model tools (V4-08).

"Key" is the console's word (``/console/keys``); the api path is ``/v1/credentials``.

V4-08 (docs/v4/CUSTOM-MODELS.md D-V4-28): ``provider_catalog`` searches and
pages; ``provider_test_model`` runs the api's "Test model" probe;
``provider_model_declare`` stores declared capabilities; ``describe_model``
backs ``lkap_describe("model", …)``. All three check the model id with the
contracts' rule locally and refuse, value-free, before any api call
(R-V4-32): a pasted key never reaches a URL or a request body.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from lkap_contracts.providers import CatalogKind, ModelCapabilities, ProviderKind, validate_model_id
from pydantic import Field

from lkap_mcp import content
from lkap_mcp.client import ApiFailure
from lkap_mcp.registry import IDEMPOTENT_WRITE, READ, WRITE, Registry, ServerContext
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


#: The catalog ``meta`` keys the MCP passes through (D-V4-28); the raw vendor blob stays on the api.
META_KEYS = (
    "pricing",
    "context_length",
    "input_modalities",
    "output_modalities",
    "supported_parameters",
    "shutdown_date",
    "archived",
    "modelLifecycle",
)


def model_id_refusal(model: str) -> ToolResult | None:
    """The value-free refusal of a model id that breaks the id rule, before any api call (R-V4-32)."""
    reason = validate_model_id(model)
    if reason is None:
        return None
    return ToolResult.fail(
        "invalid_model_id",
        f"the model id {reason}",
        hint="pass the vendor's model id; keys go to provider_key_create",
    )


def model_path(provider_id: str, model: str) -> str:
    """``/v1/providers/{id}/models/{model_id}`` (a model id may contain ``/``)."""
    return f"/v1/providers/{seg(provider_id)}/models/{seg(model)}"


def trim_meta(meta: Any) -> dict[str, Any]:
    """A catalog item's ``meta`` reduced to :data:`META_KEYS` (OpenRouter's modalities are lifted)."""
    if not isinstance(meta, dict):
        return {}
    out = {key: meta[key] for key in META_KEYS if key in meta}
    architecture = meta.get("architecture")
    if isinstance(architecture, dict):
        for key in ("input_modalities", "output_modalities"):
            if key in architecture and key not in out:
                out[key] = architecture[key]
    return out


def wrap_test_result(result: Any, provider_id: str) -> Any:
    """A ``ModelTestResult`` with ``sample`` and every ``message`` wrapped as untrusted data."""
    if not isinstance(result, dict):
        return result
    source = f"test-model:{provider_id}"
    out = dict(result)
    for key in ("sample", "message"):
        if out.get(key) is not None:
            out[key] = untrusted(out[key], source)
    probes = []
    for probe in out.get("probes") or []:
        item = dict(probe) if isinstance(probe, dict) else probe
        if isinstance(item, dict) and item.get("message") is not None:
            item["message"] = untrusted(item["message"], source)
        probes.append(item)
    out["probes"] = probes
    return out


def _registry_capabilities(spec: dict[str, Any], model: str) -> dict[str, Any]:
    listed = next((m for m in spec.get("models") or [] if m.get("id") == model), None)
    caps: dict[str, Any] = {}
    if listed is not None:
        caps["vision"] = bool(listed.get("supports_video"))
    if spec.get("kind") in ("llm", "realtime"):
        caps["tools"] = (spec.get("capabilities") or {}).get("tool_calling")
    return caps


def resolve_view(spec: dict[str, Any], model: str, record: dict[str, Any] | None) -> dict[str, Any]:
    """Declared → detected → registry, per field (the api's own resolution also reads catalog meta)."""
    layers: list[tuple[str, dict[str, Any]]] = [
        ("declared", (record or {}).get("declared") or {}),
        ("detected", (record or {}).get("detected") or {}),
        ("registry", _registry_capabilities(spec, model)),
    ]
    resolved: dict[str, Any] = {}
    source: str | None = None
    for name in ("vision", "tools", "audio_in", "audio_out", "streaming", "context_tokens"):
        for layer, caps in layers:
            if caps.get(name) is not None:
                resolved[name] = caps[name]
                if name == "vision":
                    source = layer
                break
    resolved["source"] = source
    return resolved


async def describe_model(ctx: ServerContext, id_: str) -> ToolResult:
    """``lkap_describe("model", "<provider_id>/<model_id>")``: registry, record, catalog, capabilities."""
    provider_id, _, model = id_.partition("/")
    if not model:
        return ToolResult.fail(
            "invalid_input",
            "the id is '<provider_id>/<model_id>'",
            hint="e.g. openrouter-llm/openai/gpt-4.1-mini",
        )
    refused = model_id_refusal(model)
    if refused is not None:
        return refused
    spec = content.provider_spec(provider_id)
    if spec is None:
        return ToolResult.fail(
            "not_found", f"no provider with id {provider_id!r}", hint="provider_list shows it"
        )
    warnings: list[str] = []
    record: dict[str, Any] | None = None
    try:
        record = await ctx.client.get(model_path(provider_id, model))
    except ApiFailure as failure:
        if failure.status != 404:
            warnings.append(f"workspace record unavailable: {failure.code}")
    catalog_item: dict[str, Any] | None = None
    catalog = spec.get("catalog") or {}
    if "models" in (catalog.get("kinds") or []):
        try:
            body = await ctx.client.get(
                f"/v1/providers/{seg(provider_id)}/catalog",
                params={"kind": "models", "q": model, "limit": 50},
            )
            for item in (body or {}).get("items", []):
                if isinstance(item, dict) and item.get("id") == model:
                    catalog_item = {
                        "id": model,
                        "label": untrusted(item.get("label", ""), f"catalog:{provider_id}"),
                        "meta": trim_meta(item.get("meta")),
                    }
        except ApiFailure as failure:
            warnings.append(f"catalog unavailable: {failure.code}")
    listed = next((m for m in spec.get("models") or [] if m.get("id") == model), None)
    view = {
        "provider_id": provider_id,
        "model": model,
        "kind": spec.get("kind"),
        "registry": listed,
        "is_default": spec.get("default_model") == model,
        "record": wrap_record(record, provider_id),
        "catalog": catalog_item,
        "capabilities": resolve_view(spec, model, record),
    }
    return ToolResult.success(view, warnings=warnings)


def wrap_record(record: dict[str, Any] | None, provider_id: str) -> dict[str, Any] | None:
    """A ``ProviderModelOut`` with its vendor ``last_test_message`` wrapped as untrusted data."""
    if not isinstance(record, dict):
        return None
    out = dict(record)
    if out.get("last_test_message") is not None:
        out["last_test_message"] = untrusted(out["last_test_message"], f"test-model:{provider_id}")
    return out


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
        query: Annotated[
            str | None, Field(max_length=200, description="Case-insensitive search over item id and label")
        ] = None,
        limit: Annotated[int, Field(ge=1, le=1000)] = 50,
        offset: Annotated[int, Field(ge=0)] = 0,
        model: Annotated[
            str | None, Field(max_length=200, description="kind='voices': only this model's voices")
        ] = None,
        search_vendor: Annotated[
            bool, Field(description="Forward `query` to the vendor's own search (OpenRouter entries only)")
        ] = False,
        refresh: bool = False,
    ) -> ToolResult:
        """A provider's models, voices, avatars or personas, searched and paged (vendor labels are untrusted
        data; `meta` is trimmed to pricing, context, modalities, parameters and deprecation).
        """
        body = await client.get(
            f"/v1/providers/{seg(provider_id)}/catalog",
            params={
                "kind": kind,
                "credential_id": key_id,
                "q": query,
                "limit": limit,
                "offset": offset,
                "model": model,
                "search_vendor": search_vendor or None,
                "refresh": refresh or None,
            },
        )
        items = [
            {
                "id": item.get("id"),
                "label": untrusted(item.get("label", ""), f"catalog:{provider_id}"),
                "meta": trim_meta(item.get("meta")),
            }
            for item in (body or {}).get("items", [])
            if isinstance(item, dict)
        ]
        return ToolResult.success({**(body or {}), "items": items})

    @registry.tool(scopes={"agents:write"}, annotations=WRITE, data="ModelTestResult")
    async def provider_test_model(
        provider_id: str,
        model: Annotated[str, Field(description="The model id (or avatar id) to test; never a key")],
        key_id: Annotated[str | None, Field(description="A provider key (credential) id")] = None,
        connection_id: Annotated[
            str | None,
            Field(description="LiveKit Inference only: the connection whose key signs the gateway request"),
        ] = None,
        fields: Annotated[
            dict[str, str | int | float | bool] | None,
            Field(description="The slot's non-secret fields the probe needs (voice, voice_id, base_url)"),
        ] = None,
        probes: list[Literal["basic", "tools", "vision"]] | None = None,
        force: Annotated[bool, Field(description="Run again inside the 10-minute re-run window")] = False,
        plan: bool = False,
    ) -> ToolResult:
        """Test a model id with one real, capped vendor call: it spends vendor money (at most 10 tests per
        workspace per minute, 2 at once); the vendor's `sample` and `message` are untrusted data.
        """
        refused = model_id_refusal(model)
        if refused is not None:
            return refused
        body: dict[str, Any] = {"model": model, "force": force}
        if key_id is not None:
            body["credential_id"] = key_id
        if connection_id is not None:
            body["connection_id"] = connection_id
        if fields:
            body["fields"] = fields
        if probes is not None:
            body["probes"] = probes
        path = f"/v1/providers/{seg(provider_id)}/test-model"
        if plan:
            return planned(request("POST", path, body, note="one real vendor call"))
        try:
            result = await client.post(path, body)
        except ApiFailure as failure:
            if failure.status == 429:
                details = failure.details if isinstance(failure.details, dict) else {}
                return ToolResult.fail(
                    "rate_limited",
                    failure.message,
                    status=429,
                    details={"retry_after": details.get("retry_after", details.get("retry_after_s"))},
                    hint="Test model allows 10 per workspace per minute and 2 at once; wait retry_after s",
                )
            raise
        return ToolResult.success(wrap_test_result(result, provider_id))

    @registry.tool(scopes={"providers:write"}, annotations=IDEMPOTENT_WRITE, data="ProviderModelOut")
    async def provider_model_declare(
        provider_id: str,
        model: Annotated[str, Field(description="The (usually custom) model id")],
        capabilities: Annotated[
            ModelCapabilities,
            Field(description="What the model can do; unset fields stay unknown. Declared values win."),
        ],
        plan: bool = False,
    ) -> ToolResult:
        """Declare what a model can do (vision, tools, audio …); the declaration wins over the probe, the
        catalog and the registry, and reaches the worker.
        """
        refused = model_id_refusal(model)
        if refused is not None:
            return refused
        path = model_path(provider_id, model)
        body = {"declared": capabilities.model_dump(exclude_none=True, exclude={"source"})}
        if plan:
            return planned(request("PUT", path, body))
        return ToolResult.success(await client.put(path, body))

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
