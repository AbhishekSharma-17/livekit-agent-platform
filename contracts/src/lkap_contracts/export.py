"""Export the contracts to ``generated/``: providers.json, JSON Schemas and TypeScript.

Run with ``uv run python -m lkap_contracts.export``. The outputs are committed and
diff-tested, so every writer here must be deterministic: fixed model order, sorted
keys, two-space indent, trailing newline.

The TypeScript step shells out to ``pnpm dlx json-schema-to-typescript`` and is
skipped automatically when pnpm is unavailable (the JSON outputs never need node).
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, TypeAdapter

from lkap_contracts import api_models, providers
from lkap_contracts.agent_config import (
    AgentConfig,
    AgentLimits,
    AvatarOptions,
    DisclosureConfig,
    LocaleConfig,
    PanelLayout,
    QaConfig,
    RecordingConfig,
    ResolvedAgentConfig,
)
from lkap_contracts.blocks import BLOCK_CONFIG_MODELS, block_config_schema_name
from lkap_contracts.common import Issue
from lkap_contracts.compliance import (
    ComplianceOut,
    CompliancePreset,
    ComplianceSettings,
    ConsentEvent,
    ConsentState,
    ResolvedCompliance,
)
from lkap_contracts.connections import ConnectionCapabilities, ConnectionInfo
from lkap_contracts.dispatch import DispatchMetadata
from lkap_contracts.fleet import (
    FleetDesired,
    ReplicaHandle,
    WorkerEnv,
    WorkerHeartbeatIn,
    WorkerRegisterIn,
    WorkerRegisterOut,
)
from lkap_contracts.flow import (
    AgentNode,
    EndNode,
    FlowEdge,
    FlowNode,
    FlowSpec,
    FlowState,
    GlobalNode,
    QaNode,
    StartNode,
    TransferNode,
    VariableSpec,
)
from lkap_contracts.packs import KbSeed, PackManifest, ToolMeta
from lkap_contracts.pricing import Price, PriceQuote, WorkspacePrice
from lkap_contracts.providers import CatalogFilter, IdIssue, ModelCapabilities, PageSpec, ProviderSpec
from lkap_contracts.qa import QaVerdict, SessionQaIn
from lkap_contracts.telephony import TelephonyConfig, TransferTarget
from lkap_contracts.templates import StarterTemplate
from lkap_contracts.tool_providers import TOOL_PROVIDER_MODELS
from lkap_contracts.tools import (
    HttpToolDefinition,
    McpHeaderAuth,
    McpNoAuth,
    McpOAuthAuth,
    McpServerDefinition,
    McpServerOrigin,
    McpTestResult,
    McpToolSnapshot,
    ProviderToolDefinition,
    ToolDefinition,
    ToolExecution,
    builtin_tools_document,
)
from lkap_contracts.ui_protocol import (
    ActivityEvent,
    AgentAction,
    AgentActionResult,
    BlockRequestPayload,
    BlockSpec,
    BlockSubmitPayload,
    ChoicesBlockState,
    ConsentBlockState,
    DetailsBlockState,
    DocumentBlockState,
    FormBlockState,
    GalleryBlockState,
    KbCitationsBlockState,
    MarkdownBlockState,
    RequestableState,
    StepsBlockState,
    TableBlockState,
    TranscriptBlockState,
    UiPatch,
    UiRequest,
    UiRequestResult,
    UiSnapshot,
    UiState,
    VideoBlockState,
)

logger = logging.getLogger(__name__)

#: Pinned so regenerating on another machine produces a byte-identical .d.ts.
JSON_SCHEMA_TO_TYPESCRIPT_VERSION = "15.0.4"

#: Title of the combined schema document fed to json-schema-to-typescript.
COMBINED_TITLE = "LkapContracts"

#: Every model exported as ``generated/schemas/<name>.schema.json`` and to TypeScript.
#: Order is fixed and meaningful — do not sort.
EXPORTED_MODELS: dict[str, type[BaseModel]] = {
    # core config + dispatch
    "AgentConfig": AgentConfig,
    "AgentLimits": AgentLimits,
    "LocaleConfig": LocaleConfig,
    "AvatarOptions": AvatarOptions,
    "PanelLayout": PanelLayout,
    "RecordingConfig": RecordingConfig,
    "DisclosureConfig": DisclosureConfig,
    "QaConfig": QaConfig,
    "ResolvedAgentConfig": ResolvedAgentConfig,
    "DispatchMetadata": DispatchMetadata,
    "Issue": Issue,
    # connections and fleet
    "ConnectionCapabilities": ConnectionCapabilities,
    "ConnectionInfo": ConnectionInfo,
    "FleetDesired": FleetDesired,
    "WorkerEnv": WorkerEnv,
    "ReplicaHandle": ReplicaHandle,
    "WorkerRegisterIn": WorkerRegisterIn,
    "WorkerRegisterOut": WorkerRegisterOut,
    "WorkerHeartbeatIn": WorkerHeartbeatIn,
    # flows
    "FlowSpec": FlowSpec,
    "FlowEdge": FlowEdge,
    "FlowState": FlowState,
    "VariableSpec": VariableSpec,
    "StartNode": StartNode,
    "AgentNode": AgentNode,
    "EndNode": EndNode,
    "GlobalNode": GlobalNode,
    "TransferNode": TransferNode,
    "QaNode": QaNode,
    # ui protocol
    "UiState": UiState,
    "UiSnapshot": UiSnapshot,
    "UiPatch": UiPatch,
    "ActivityEvent": ActivityEvent,
    "BlockSpec": BlockSpec,
    "FormBlockState": FormBlockState,
    "DocumentBlockState": DocumentBlockState,
    "GalleryBlockState": GalleryBlockState,
    "TableBlockState": TableBlockState,
    "TranscriptBlockState": TranscriptBlockState,
    "VideoBlockState": VideoBlockState,
    "KbCitationsBlockState": KbCitationsBlockState,
    "ChoicesBlockState": ChoicesBlockState,
    "DetailsBlockState": DetailsBlockState,
    "MarkdownBlockState": MarkdownBlockState,
    "StepsBlockState": StepsBlockState,
    "ConsentBlockState": ConsentBlockState,
    "UiRequest": UiRequest,
    "UiRequestResult": UiRequestResult,
    "RequestableState": RequestableState,
    "BlockRequestPayload": BlockRequestPayload,
    "BlockSubmitPayload": BlockSubmitPayload,
    "AgentAction": AgentAction,
    "AgentActionResult": AgentActionResult,
    # packs
    "PackManifest": PackManifest,
    "KbSeed": KbSeed,
    "ToolMeta": ToolMeta,
    # starter templates (v4, docs/v4/TEMPLATES.md §2)
    "StarterTemplate": StarterTemplate,
    # providers and pricing
    "ProviderSpec": ProviderSpec,
    "ModelCapabilities": ModelCapabilities,
    "IdIssue": IdIssue,
    "CatalogFilter": CatalogFilter,
    "PageSpec": PageSpec,
    "Price": Price,
    "PriceQuote": PriceQuote,
    "WorkspacePrice": WorkspacePrice,
    # tools
    "HttpToolDefinition": HttpToolDefinition,
    "McpServerDefinition": McpServerDefinition,
    "McpServerOrigin": McpServerOrigin,
    "McpNoAuth": McpNoAuth,
    "McpHeaderAuth": McpHeaderAuth,
    "McpOAuthAuth": McpOAuthAuth,
    "McpToolSnapshot": McpToolSnapshot,
    "McpTestResult": McpTestResult,
    "ProviderToolDefinition": ProviderToolDefinition,
    "ToolExecution": ToolExecution,
    # api models
    "Page": api_models.Page,
    "ErrorBody": api_models.ErrorBody,
    "ErrorResponse": api_models.ErrorResponse,
    "ProvidersResponse": api_models.ProvidersResponse,
    "ProviderOut": api_models.ProviderOut,
    "ProviderSettingsIn": api_models.ProviderSettingsIn,
    "CatalogItem": api_models.CatalogItem,
    "CatalogResponse": api_models.CatalogResponse,
    "ModelIdRules": api_models.ModelIdRules,
    "ProviderModelOut": api_models.ProviderModelOut,
    "ProviderModelDeclare": api_models.ProviderModelDeclare,
    "ModelTestRequest": api_models.ModelTestRequest,
    "ProbeResult": api_models.ProbeResult,
    "ModelTestResult": api_models.ModelTestResult,
    "CredentialCreate": api_models.CredentialCreate,
    "CredentialUpdate": api_models.CredentialUpdate,
    "CredentialOut": api_models.CredentialOut,
    "CredentialTestResult": api_models.CredentialTestResult,
    "AgentCreate": api_models.AgentCreate,
    "AgentUpdate": api_models.AgentUpdate,
    "AgentOut": api_models.AgentOut,
    "AgentPublicOut": api_models.AgentPublicOut,
    "ValidationResult": api_models.ValidationResult,
    "ConfigVersionOut": api_models.ConfigVersionOut,
    "FlowValidateRequest": api_models.FlowValidateRequest,
    "NodeSpecSchema": api_models.NodeSpecSchema,
    "NodeSpecsResponse": api_models.NodeSpecsResponse,
    "ConnectRequest": api_models.ConnectRequest,
    "TextSessionCreate": api_models.TextSessionCreate,
    "ConnectResponse": api_models.ConnectResponse,
    "ToolCreate": api_models.ToolCreate,
    "ToolOut": api_models.ToolOut,
    "ToolDryRunRequest": api_models.ToolDryRunRequest,
    "ToolDryRunResult": api_models.ToolDryRunResult,
    "KbCreate": api_models.KbCreate,
    "KbOut": api_models.KbOut,
    "KbDocumentOut": api_models.KbDocumentOut,
    "KbImportIn": api_models.KbImportIn,
    "KbSearchRequest": api_models.KbSearchRequest,
    "KbHit": api_models.KbHit,
    "KbSearchResponse": api_models.KbSearchResponse,
    "KbSearchOptions": api_models.KbSearchOptions,
    "KbSearchWarning": api_models.KbSearchWarning,
    "KbEvalIn": api_models.KbEvalIn,
    "KbEvalOut": api_models.KbEvalOut,
    "KbEvalSetIn": api_models.KbEvalSetIn,
    "KbEvalSetOut": api_models.KbEvalSetOut,
    "KbReindexIn": api_models.KbReindexIn,
    "KbReindexSkipped": api_models.KbReindexSkipped,
    "KbReindexOut": api_models.KbReindexOut,
    "PackOut": api_models.PackOut,
    "PacksResponse": api_models.PacksResponse,
    "TemplateOut": api_models.TemplateOut,
    "TemplatesResponse": api_models.TemplatesResponse,
    "TranscriptTurn": api_models.TranscriptTurn,
    "SessionOut": api_models.SessionOut,
    "SessionDetailOut": api_models.SessionDetailOut,
    "LocaleEvent": api_models.LocaleEvent,
    # V5-15: consent and disclosure
    "ConsentEvent": ConsentEvent,
    "ConsentState": ConsentState,
    "ComplianceSettings": ComplianceSettings,
    "CompliancePreset": CompliancePreset,
    "ResolvedCompliance": ResolvedCompliance,
    "ComplianceOut": ComplianceOut,
    "SessionLatency": api_models.SessionLatency,
    "CostLine": api_models.CostLine,
    "SessionCost": api_models.SessionCost,
    "QaOut": api_models.QaOut,
    "RecordingOut": api_models.RecordingOut,
    "AnalyticsBucket": api_models.AnalyticsBucket,
    "AnalyticsSummary": api_models.AnalyticsSummary,
    "AnalyticsDriver": api_models.AnalyticsDriver,
    # cost estimates and price quotes (V4-15, docs/v4/COSTS.md §3.1)
    "TemplateEstimate": api_models.TemplateEstimate,
    "CostDriver": api_models.CostDriver,
    "Assumption": api_models.Assumption,
    "EstimateLine": api_models.EstimateLine,
    "MoneyRange": api_models.MoneyRange,
    "CostEstimate": api_models.CostEstimate,
    "CostEstimateRequest": api_models.CostEstimateRequest,
    "CostAssumptionsOut": api_models.CostAssumptionsOut,
    "PriceQuoteItemIn": api_models.PriceQuoteItemIn,
    "PriceQuotesRequest": api_models.PriceQuotesRequest,
    "PriceQuoteItem": api_models.PriceQuoteItem,
    "PriceQuotesResponse": api_models.PriceQuotesResponse,
    "WorkspacePricesIn": api_models.WorkspacePricesIn,
    "WorkspacePricesOut": api_models.WorkspacePricesOut,
    "SessionEventOut": api_models.SessionEventOut,
    "SessionEventIn": api_models.SessionEventIn,
    "SessionEventsIn": api_models.SessionEventsIn,
    "SessionSummaryIn": api_models.SessionSummaryIn,
    "InternalKbSearchRequest": api_models.InternalKbSearchRequest,
    "SessionStartIn": api_models.SessionStartIn,
    "SessionMetricsIn": api_models.SessionMetricsIn,
    "RecordingStartOut": api_models.RecordingStartOut,
    "SessionRecordingIn": api_models.SessionRecordingIn,
    "HealthResponse": api_models.HealthResponse,
    # QA (R-V2-5): the judge verdict schema and the worker's PUT body
    "QaVerdict": QaVerdict,
    "SessionQaIn": SessionQaIn,
    # auth, team and connections
    "UserOut": api_models.UserOut,
    "WorkspaceMembership": api_models.WorkspaceMembership,
    "Me": api_models.Me,
    "ConnectionOut": api_models.ConnectionOut,
    "ConnectionCreate": api_models.ConnectionCreate,
    "ConnectionUpdate": api_models.ConnectionUpdate,
    "ConnectionRotateIn": api_models.ConnectionRotateIn,
    "ConnectionTestResult": api_models.ConnectionTestResult,
    "WorkerInstanceOut": api_models.WorkerInstanceOut,
    "FleetStatus": api_models.FleetStatus,
    "FleetActionIn": api_models.FleetActionIn,
    # webhooks and telephony
    "WebhookEndpointCreate": api_models.WebhookEndpointCreate,
    "WebhookEndpointOut": api_models.WebhookEndpointOut,
    "WebhookDeliveryOut": api_models.WebhookDeliveryOut,
    "WebhookEvent": api_models.WebhookEvent,
    "CallCreate": api_models.CallCreate,
    "CallOut": api_models.CallOut,
    # telephony, promoted from the api by R-V2-25 (V2-19T); patterns + transfer targets R-V2-21
    "TransferTarget": TransferTarget,
    "TelephonyConfig": TelephonyConfig,
    "TrunkCreate": api_models.TrunkCreate,
    "TrunkUpdate": api_models.TrunkUpdate,
    "TrunkOut": api_models.TrunkOut,
    "DispatchRuleCreate": api_models.DispatchRuleCreate,
    "DispatchRuleOut": api_models.DispatchRuleOut,
    "PhoneNumberCreate": api_models.PhoneNumberCreate,
    "PhoneNumberUpdate": api_models.PhoneNumberUpdate,
    "PhoneNumberOut": api_models.PhoneNumberOut,
    "NumbersRefreshIn": api_models.NumbersRefreshIn,
    "NumbersRefreshOut": api_models.NumbersRefreshOut,
    "CallTransferIn": api_models.CallTransferIn,
    "CallDtmfIn": api_models.CallDtmfIn,
    "CallDtmfOut": api_models.CallDtmfOut,
    "CallReportIn": api_models.CallReportIn,
    "InternalTransferIn": api_models.InternalTransferIn,
    "InternalTransferOut": api_models.InternalTransferOut,
    # concrete page parametrisations
    "CredentialPage": api_models.CredentialPage,
    "AgentPage": api_models.AgentPage,
    "ToolPage": api_models.ToolPage,
    "KbPage": api_models.KbPage,
    "KbDocumentPage": api_models.KbDocumentPage,
    "SessionPage": api_models.SessionPage,
    "SessionEventPage": api_models.SessionEventPage,
    "ConnectionPage": api_models.ConnectionPage,
    "ConfigVersionPage": api_models.ConfigVersionPage,
    "CallPage": api_models.CallPage,
    "TrunkPage": api_models.TrunkPage,
    "DispatchRulePage": api_models.DispatchRulePage,
    "PhoneNumberPage": api_models.PhoneNumberPage,
    "WebhookEndpointPage": api_models.WebhookEndpointPage,
    "WebhookDeliveryPage": api_models.WebhookDeliveryPage,
    "ProviderModelPage": api_models.ProviderModelPage,
    **TOOL_PROVIDER_MODELS,  # V5-18: connected apps (docs/v5/COMPOSIO.md §3)
}

#: Discriminated unions are not ``BaseModel`` subclasses; they go through TypeAdapter.
EXPORTED_UNIONS: dict[str, Any] = {
    "ToolDefinition": ToolDefinition,
    "FlowNode": FlowNode,
}

#: Per-block-type config schemas (R-V2-17), JSON only: ``BlockConfig_<type>``.
#: They stay out of the TypeScript document (several types share one model, and
#: the composer's config forms are hand-written, kept equal by a vitest parity test).
EXPORTED_BLOCK_CONFIGS: dict[str, type[BaseModel]] = {
    block_config_schema_name(block_type): model for block_type, model in BLOCK_CONFIG_MODELS.items()
}


def default_output_dir() -> Path:
    """Return the repository's ``contracts/generated`` directory."""
    return Path(__file__).resolve().parents[2] / "generated"


def _dumps(payload: object) -> str:
    """Serialise deterministically: sorted keys, two-space indent, trailing newline."""
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def build_providers_document() -> dict[str, Any]:
    """Build the ``providers.json`` payload from :data:`lkap_contracts.providers.REGISTRY`.

    The payload is exactly ``ProvidersResponse`` (dumped from the model, so the
    protocol version can never drift from the contract): no extra keys, so a
    strict parse of the file against the generated type succeeds. Topic
    constants live in :mod:`lkap_contracts.ui_protocol`, not here.

    ``ProvidersResponse.providers`` is ``ProviderOut`` (V2-06): this static,
    workspace-free export wraps every registry entry with the default
    settings (``enabled=True``, no installed connections, no default
    credential) rather than any one workspace's actual settings, which only
    ``GET /v1/providers`` (backed by the database) can know.

    ``model_id_rules`` (V4-07) carries the model-id rule the console mirrors.

    Returns:
        A mapping with the protocol version, every registry entry in registry
        order, and the model-id rule.
    """
    out = [api_models.ProviderOut(**spec.model_dump()) for spec in providers.REGISTRY]
    return api_models.ProvidersResponse(providers=out, model_id_rules=api_models.ModelIdRules()).model_dump(
        mode="json"
    )


def _schema_for(name: str) -> dict[str, Any]:
    """Return a self-contained JSON Schema for one exported name."""
    if name in EXPORTED_UNIONS:
        schema: dict[str, Any] = TypeAdapter(EXPORTED_UNIONS[name]).json_schema(mode="validation")
    elif name in EXPORTED_BLOCK_CONFIGS:
        schema = EXPORTED_BLOCK_CONFIGS[name].model_json_schema(mode="validation")
    else:
        schema = EXPORTED_MODELS[name].model_json_schema(mode="validation")
    schema["title"] = name
    schema["$id"] = f"lkap-contracts/{name}.schema.json"
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    return schema


def build_schema_documents() -> dict[str, dict[str, Any]]:
    """Build one JSON Schema document per exported model, union and block config.

    Returns:
        Mapping of exported name to its self-contained JSON Schema.
    """
    names = [*EXPORTED_MODELS, *EXPORTED_UNIONS, *EXPORTED_BLOCK_CONFIGS]
    return {name: _schema_for(name) for name in names}


def build_combined_schema() -> dict[str, Any]:
    """Build a single root schema that references every exported type.

    json-schema-to-typescript emits one interface per ``$defs`` entry, so feeding it
    one combined document avoids the duplicate declarations that per-file runs produce.

    Returns:
        A draft 2020-12 object schema whose properties reference every exported type.
    """
    defs: dict[str, Any] = {}
    properties: dict[str, Any] = {}
    for name in [*EXPORTED_MODELS, *EXPORTED_UNIONS]:
        schema = _schema_for(name)
        schema.pop("$id", None)
        schema.pop("$schema", None)
        for def_name, def_schema in schema.pop("$defs", {}).items():
            defs.setdefault(def_name, def_schema)
        defs[name] = schema
        properties[name] = {"$ref": f"#/$defs/{name}"}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "lkap-contracts/lkap-contracts.schema.json",
        "title": COMBINED_TITLE,
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "$defs": defs,
    }


def _rewrite_refs(node: Any) -> Any:
    """Rewrite ``#/$defs/...`` refs to ``#/definitions/...`` for json-schema-to-typescript."""
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                out[key] = value.replace("#/$defs/", "#/definitions/")
            elif key == "$defs":
                out["definitions"] = _rewrite_refs(value)
            else:
                out[key] = _rewrite_refs(value)
        return out
    if isinstance(node, list):
        return [_rewrite_refs(item) for item in node]
    return node


#: Keys whose value is a *map of names to subschemas*: inside one, ``title`` is a
#: property (or definition) name, never the schema keyword (R-V2-15, ask #72).
_SCHEMA_MAP_KEYS = frozenset({"properties", "patternProperties", "$defs", "definitions"})


def _strip_titles(node: Any, *, in_map: bool = False) -> Any:
    """Drop every ``title`` schema keyword, keeping properties that are named ``title``.

    Pydantic titles each individual property (``"title": "Ok"``), and
    json-schema-to-typescript turns any titled subschema into a standalone exported
    alias. Stripping them keeps the ``.d.ts`` namespace to real models only, instead
    of leaking names like ``Ok``, ``Id`` or ``Error`` that collide with globals.

    ``title`` is only stripped as a keyword: a key of a ``properties`` /
    ``patternProperties`` / ``$defs`` / ``definitions`` map names a field or a
    definition (``BlockSpec.title``), so it is kept and only its subschema is
    recursed into (R-V2-15).

    Args:
        node: A schema node (or any JSON value inside one).
        in_map: Whether ``node`` is itself a name → subschema map.

    Returns:
        A copy of ``node`` without ``title`` keywords.
    """
    if isinstance(node, dict):
        if in_map:
            return {key: _strip_titles(value) for key, value in node.items()}
        return {
            key: _strip_titles(value, in_map=key in _SCHEMA_MAP_KEYS)
            for key, value in node.items()
            if key != "title"
        }
    if isinstance(node, list):
        return [_strip_titles(item) for item in node]
    return node


def _pure_refs(node: Any) -> Any:
    """Drop keys that sit next to a ``$ref``.

    Pydantic emits ``{"$ref": ..., "default": {...}}`` for a field whose type is
    another model. json-schema-to-typescript treats a ``$ref`` with siblings as a
    fresh anonymous schema and emits a duplicate interface (``CapabilitiesConfig1``),
    so the TypeScript document keeps the reference alone. Defaults stay intact in the
    committed JSON Schemas.
    """
    if isinstance(node, dict):
        if "$ref" in node and len(node) > 1:
            return {"$ref": node["$ref"]}
        return {key: _pure_refs(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_pure_refs(item) for item in node]
    return node


#: Keys ignored when deciding whether a schema node is "empty" (D-W2-3).
_UNKNOWN_SCHEMA_IGNORED_KEYS = {"default", "description", "title", "examples"}


#: json-schema-to-typescript already renders a bare, unconstrained branch of one of
#: these as TypeScript ``unknown`` on its own (an ``anyOf``/``oneOf``/``allOf`` member
#: with no keys means "matches anything"); injecting ``tsType`` into that branch turns
#: the otherwise-collapsed union back into a literal ``unknown | null``, which is worse.
#: So those arrays are copied through unchanged rather than recursed into for marking.
_UNION_KEYS = ("anyOf", "oneOf", "allOf")


def _mark_untyped_as_unknown(node: Any) -> Any:
    """Inject ``tsType: "unknown"`` into every property schema that carries no type info.

    Pydantic emits an empty object (``{}``, plus maybe ``default``/``title``) for an
    ``Any`` field (``UiPatchOp.value``). Left alone, json-schema-to-typescript renders a
    bare ``{}`` as an indexed object type (``{ [k: string]: unknown }``) instead of
    ``unknown``. ``json-schema-to-typescript`` honours an explicit ``tsType`` verbatim,
    so setting it here makes the contract's ``Any`` mean TypeScript's ``unknown``.

    A schema counts as empty when, after ignoring ``default``/``description``/``title``/
    ``examples``, it has none of ``type``, ``$ref``, ``anyOf``, ``oneOf``, ``allOf``,
    ``properties``, ``items``, ``enum``, ``const`` (equivalently: no keys remain at all,
    since those are the only other keys Pydantic's exporter emits). An
    ``Optional[Any]``-shaped field (``ErrorBody.details``) already reaches ``unknown``
    through the ``anyOf`` short-circuit above and needs no marking.

    Args:
        node: A (sub)tree of the combined schema, processed bottom-up.

    Returns:
        The same structure with ``tsType: "unknown"`` added to every empty schema node
        outside of ``anyOf``/``oneOf``/``allOf`` branches.
    """
    if isinstance(node, dict):
        walked = {
            key: (value if key in _UNION_KEYS else _mark_untyped_as_unknown(value))
            for key, value in node.items()
        }
        remaining = {key for key in walked if key not in _UNKNOWN_SCHEMA_IGNORED_KEYS}
        if not remaining:
            walked["tsType"] = "unknown"
        return walked
    if isinstance(node, list):
        return [_mark_untyped_as_unknown(item) for item in node]
    return node


def prepare_for_typescript(combined: dict[str, Any]) -> dict[str, Any]:
    """Turn the combined schema into the exact document json-schema-to-typescript reads.

    Args:
        combined: The combined root schema from :func:`build_combined_schema`.

    Returns:
        A ``definitions``-keyed copy whose only titles are the exported model names.
    """
    prepared: dict[str, Any] = _rewrite_refs(_mark_untyped_as_unknown(_pure_refs(_strip_titles(combined))))
    prepared["title"] = COMBINED_TITLE
    definitions: dict[str, Any] = prepared.get("definitions", {})
    for name, schema in definitions.items():
        if isinstance(schema, dict):
            schema["title"] = name
    return prepared


def typescript_available() -> bool:
    """Return True when ``pnpm`` is on PATH and can therefore run the TS codegen."""
    return shutil.which("pnpm") is not None


def generate_typescript(combined: dict[str, Any], out_file: Path) -> bool:
    """Generate ``lkap-contracts.d.ts`` from the combined schema.

    Args:
        combined: The combined root schema from :func:`build_combined_schema`.
        out_file: Destination ``.d.ts`` path; parent directories are created.

    Returns:
        True when the file was written, False when pnpm is unavailable or the
        codegen failed (the caller keeps the committed file in that case).
    """
    if not typescript_available():
        logger.warning("pnpm not found; skipping TypeScript generation for %s", out_file)
        return False
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        schema_path = Path(tmp) / "lkap-contracts.schema.json"
        schema_path.write_text(_dumps(prepare_for_typescript(combined)), encoding="utf-8")
        # Generate into the temp dir first: a failed or partial run must never clobber
        # the committed .d.ts that the diff test and the web build depend on.
        staged = Path(tmp) / "lkap-contracts.d.ts"
        cmd = [
            "pnpm",
            "dlx",
            f"json-schema-to-typescript@{JSON_SCHEMA_TO_TYPESCRIPT_VERSION}",
            "--input",
            str(schema_path),
            "--output",
            str(staged),
            "--bannerComment",
            "",
            "--additionalProperties",
            "false",
            "--unreachableDefinitions",
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=600)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            logger.warning("json-schema-to-typescript failed: %s", exc)
            return False
        if not staged.is_file():
            logger.warning("json-schema-to-typescript produced no output")
            return False
        text = staged.read_text(encoding="utf-8").lstrip("\n")
    out_file.write_text(_TS_BANNER + text, encoding="utf-8")
    return True


_TS_BANNER = (
    "/* eslint-disable */\n"
    "/**\n"
    " * GENERATED by `python -m lkap_contracts.export` from docs/CONTRACTS.md models.\n"
    " * Do not edit by hand. Copied into web/src/contracts/ by scripts/export_contracts.sh.\n"
    " */\n\n"
)


def write_json_outputs(out_dir: Path) -> list[Path]:
    """Write ``providers.json``, ``builtin_tools.json`` and ``schemas/*.schema.json`` (no node required).

    Args:
        out_dir: The ``generated/`` directory to write into.

    Returns:
        Every path written, sorted.
    """
    written: list[Path] = []
    out_dir.mkdir(parents=True, exist_ok=True)
    providers_path = out_dir / "providers.json"
    providers_path.write_text(_dumps(build_providers_document()), encoding="utf-8")
    written.append(providers_path)
    tools_path = out_dir / "builtin_tools.json"
    tools_path.write_text(_dumps(builtin_tools_document()), encoding="utf-8")
    written.append(tools_path)

    schemas_dir = out_dir / "schemas"
    schemas_dir.mkdir(parents=True, exist_ok=True)
    for name, schema in build_schema_documents().items():
        path = schemas_dir / f"{name}.schema.json"
        path.write_text(_dumps(schema), encoding="utf-8")
        written.append(path)
    return sorted(written)


def write_all(out_dir: Path, *, skip_ts: bool = False) -> list[Path]:
    """Write every generated artefact.

    Args:
        out_dir: The ``generated/`` directory to write into.
        skip_ts: Skip the TypeScript step (used by the offline diff test).

    Returns:
        Every path written, sorted.
    """
    written = write_json_outputs(out_dir)
    if not skip_ts:
        ts_path = out_dir / "ts" / "lkap-contracts.d.ts"
        if generate_typescript(build_combined_schema(), ts_path):
            written.append(ts_path)
    return sorted(written)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for ``python -m lkap_contracts.export``.

    Args:
        argv: Optional argument vector (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(description="Export lkap_contracts to generated/.")
    parser.add_argument(
        "--out", type=Path, default=None, help="output directory (default: contracts/generated)"
    )
    parser.add_argument("--skip-ts", action="store_true", help="skip TypeScript generation")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    out_dir = args.out or default_output_dir()
    written = write_all(out_dir, skip_ts=args.skip_ts)
    for path in written:
        logger.info("wrote %s", path)
    logger.info("exported %d files to %s", len(written), out_dir)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
