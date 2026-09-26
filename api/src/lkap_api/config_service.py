"""Agent configuration services: validation, pack seeding and resolution.

Three jobs over :mod:`lkap_contracts` models:

* :func:`validate_agent_config` / :func:`validate` — check an ``AgentConfig``
  against the provider registry, the stored credentials and the LiveKit
  connection the agent runs on (CONTRACTS §6, CONTRACTS-V2 §4.1, ARCHITECTURE-V2
  D-V2-4/D-V2-8). :func:`validate_in_db` builds the :class:`ValidationContext`
  from the database.
* :func:`seed_config_from_manifest` — turns a ``PackManifest`` into a runnable
  ``AgentConfig``, substituting LiveKit Inference for providers that are not
  constructible on the connection or whose credential is missing or ambiguous
  (CONTRACTS §8, "Seeding rule").
* :func:`resolve_providers` / :func:`resolve_tool_definition` — merge decrypted
  secrets into constructor kwargs and tool templates for the worker's
  ``ResolvedAgentConfig``. **Their output contains secrets and must never be
  logged or returned to an admin/browser caller.**

Provider gate (ruling R-V2-2, replacing v1's ``status == "mvp"``)
------------------------------------------------------------------
A slot's provider is usable when all of these hold:

1. ``availability == "available"`` (``verification`` never gates, R-V2-1);
2. it is **installed on the agent's connection**: in the union of
   ``installed_provider_ids`` its registered workers report, or — until any
   worker of that connection has registered — its ``worker_image`` is carried
   by the connection's image (``slim`` ⊂ ``full``; ``isolated`` never);
3. the connection's capability flags allow it (LiveKit Inference needs
   ``inference_available``; Krisp noise cancellation needs a Cloud connection);
4. the workspace has not disabled it — enforced by V2-06's validator, which
   reads :attr:`ValidationContext.disabled_provider_ids`.

Without a connection (the v1 call signature) rule 2 assumes the ``slim`` image
and rule 3 is skipped, which is exactly the v1 ``mvp`` set.

Extension point
---------------
:data:`VALIDATORS` is a list of ``Callable[[ValidationContext], list[Issue]]``.
Other packages append to it from their own modules (for example
``lkap_api.catalogs.validation`` registers the workspace-enablement check)
instead of editing this file; every registered validator runs after the
built-in checks, and its issues appear in ``issues`` and in the flat
``errors``/``warnings`` lists as ``"<path>: <message>"``.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, cast
from urllib.parse import quote, urlparse

from lkap_contracts.agent_config import (
    KNOWLEDGE_RERANK_VALUES,
    REQUIRED_SLOTS,
    AgentConfig,
    PipelineConfig,
    ProviderRef,
    ProviderSlot,
    ResolvedProvider,
    ToolsConfig,
    VoiceConfig,
    effective_qa,
)
from lkap_contracts.api_models import CatalogItem, Issue, ProviderModelOut, Severity, ValidationResult
from lkap_contracts.connections import ConnectionCapabilities, DeploymentType
from lkap_contracts.packs import PackManifest
from lkap_contracts.providers import (
    ProviderKind,
    ProviderSpec,
    WorkerImage,
    by_kind,
    credential_home,
    get,
    validate_model_id,
)
from lkap_contracts.tool_providers import COMPOSIO_PROVIDER_ID, TOOL_PROVIDER_ACCOUNT, action_risk
from lkap_contracts.tools import (
    BACKGROUNDABLE_BUILTINS,
    NON_BLOCKING_MODES,
    HttpToolDefinition,
    McpHeaderAuth,
    McpOAuthAuth,
    McpServerDefinition,
    ProviderToolDefinition,
    ToolDefinition,
    never_background,
)
from lkap_contracts.turn_handling import (
    CONVERSATION_PRESETS,
    REALTIME_IGNORED_KEYS,
    TurnHandlingOptions,
    resolve_turn_handling,
    turn_handling_dict,
)
from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.connections.probe import effective_capabilities
from lkap_api.custom_models.capabilities import resolve_capabilities
from lkap_api.db.models import (
    Credential,
    KnowledgeBase,
    LiveKitConnection,
    ProviderCatalogCache,
    Tool,
    WorkerInstance,
    WorkspaceProvider,
)

if TYPE_CHECKING:  # runtime import is deferred: `lkap_api.telephony` registers a validator from here
    from lkap_api.telephony.policy import TelephonyPolicy

#: Which registry `kind` may fill each pipeline slot. `workflow_llm` takes an llm.
#: `qa_llm` (R-V2-6) also takes an llm, but is resolved from `config.qa.model`, not
#: from a `pipeline.*` attribute — `_slots_in_use` (below) never sees it, and
#: `routers/internal.py::_credential_ids` collects its credential separately.
SLOT_KIND: dict[ProviderSlot, ProviderKind] = {
    "realtime": "realtime",
    "stt": "stt",
    "llm": "llm",
    "tts": "tts",
    "avatar": "avatar",
    "image_gen": "image_gen",
    "workflow_llm": "llm",
    "qa_llm": "llm",
    "vad": "vad",
    "turn_detection": "turn_detection",
    "noise_cancellation": "noise_cancellation",
}

#: The credential-free LiveKit Inference provider for each cascaded slot.
INFERENCE_DEFAULT: dict[str, str] = {
    "stt": "livekit-inference-stt",
    "llm": "livekit-inference-llm",
    "tts": "livekit-inference-tts",
}

#: Registry id prefix of the LiveKit Inference providers (Cloud-only).
INFERENCE_PREFIX = "livekit-inference-"

#: Slots that make up a cascaded pipeline, in construction order.
CASCADED_SLOTS: tuple[ProviderSlot, ...] = ("stt", "llm", "tts")

#: Optional slots resolved for the worker whenever they are configured.
OPTIONAL_SLOTS: tuple[ProviderSlot, ...] = (
    "avatar",
    "image_gen",
    "vad",
    "turn_detection",
    "noise_cancellation",
)

#: Keys accepted by `TurnHandlingOptions` (livekit-agents 1.8.2).
TURN_HANDLING_KEYS: frozenset[str] = frozenset(
    {"endpointing", "interruption", "preemptive_generation", "user_turn_limit"}
)

#: Field names that carry a voice selection, for `PackManifest.default_voice`.
VOICE_FIELD_NAMES: tuple[str, ...] = ("voice", "voice_id")

#: Which provider images a worker image carries (`slim` ⊂ `full`; `isolated` stands alone).
IMAGE_CARRIES: dict[str, frozenset[str]] = {
    "slim": frozenset({"slim"}),
    "full": frozenset({"slim", "full"}),
    "isolated": frozenset({"isolated"}),
}

#: Worker statuses whose `installed_provider_ids` count towards a connection's pool.
LIVE_WORKER_STATUSES: tuple[str, ...] = ("starting", "ready")

_SECRET_RE = re.compile(r"\{\{\s*secret\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_ARG_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


# ------------------------------------------------------------------------- context
@dataclass(frozen=True, slots=True)
class ConnectionContext:
    """What validation and seeding need to know about the agent's connection."""

    connection_id: str
    name: str = ""
    deployment_type: DeploymentType = "cloud"
    worker_image: WorkerImage = "slim"
    capabilities: ConnectionCapabilities = dataclasses.field(default_factory=ConnectionCapabilities)
    installed_provider_ids: frozenset[str] | None = None
    """Union reported by the pool's registered workers; ``None`` until one registers."""

    @classmethod
    def from_row(
        cls, row: LiveKitConnection, installed_provider_ids: frozenset[str] | None = None
    ) -> ConnectionContext:
        """Build the context from a stored connection."""
        return cls(
            connection_id=row.id,
            name=row.name,
            deployment_type=cast(DeploymentType, row.deployment_type),
            worker_image=cast(WorkerImage, row.worker_image),
            capabilities=effective_capabilities(
                row.deployment_type, bool(row.use_inference), row.capabilities
            ),
            installed_provider_ids=installed_provider_ids,
        )


@dataclass(frozen=True, slots=True)
class ValidationContext:
    """Everything a validator may look at. Validators never touch the database."""

    config: AgentConfig
    credential_providers: Mapping[str, str] = dataclasses.field(default_factory=dict)
    """``{credential_id: provider_id}`` for every credential of the workspace."""
    connection: ConnectionContext | None = None
    workspace_id: str | None = None
    disabled_provider_ids: frozenset[str] = frozenset()
    """Providers the workspace switched off (``workspace_providers.enabled`` false)."""
    known_tool_ids: frozenset[str] | None = None
    known_kb_ids: frozenset[str] | None = None
    tool_names_by_id: Mapping[str, str] | None = None
    """``{tool_id: name}`` for every tool row of the workspace (R-V2-10: flow nodes reference
    tools by name); ``None`` skips the flow tool-reference check."""
    pack_tool_names: frozenset[str] | None = None
    """Tool names of the agent's own pack (asks V2-16-2); ``None`` (pack unknown) falls back to
    the union over every installed pack. Set by :func:`validate_in_db` callers that know the pack."""
    telephony_policy: TelephonyPolicy | None = None
    """The workspace's outbound dialing policy (R-V2-23; ``settings["telephony"]``, default deny);
    ``None`` skips the transfer-destination policy check (``lkap_api.telephony.validation``)."""
    credential_fingerprints: Mapping[str, str] = dataclasses.field(default_factory=dict)
    """``{credential_id: fingerprint}`` (V4-07): a model test counts only with the slot's current key."""
    model_records: Mapping[tuple[str, str, str], ProviderModelOut] = dataclasses.field(default_factory=dict)
    """The workspace's ``provider_models`` rows keyed ``(provider_home, kind, model_id)`` (V4-07)."""
    catalog_items: Mapping[str, Mapping[str, CatalogItem]] = dataclasses.field(default_factory=dict)
    """``{provider_id: {item_id: item}}`` from the workspace's cached live ``models`` catalogs,
    fresh or stale, for the providers the config uses (V4-07)."""
    tool_definitions_by_id: Mapping[str, Mapping[str, Any]] | None = None
    """``{tool_id: definition JSON}`` for every tool row of the workspace (V4-12, the execution
    checks of :func:`tool_execution_issues`); ``None`` skips the per-tool checks."""
    connection_statuses: Mapping[str, str] = dataclasses.field(default_factory=dict)
    """``{connection_id: status}`` of every connected-app row (V5-47, COMPOSIO.md §4): the status
    V5-18 mirrors into ``credentials.last_test_message`` (``active``, ``expired`` …)."""

    def fingerprint_for(self, ref: ProviderRef) -> str | None:
        """The fingerprint of the credential ``ref`` uses, if it uses one."""
        return self.credential_fingerprints.get(ref.credential_id) if ref.credential_id else None

    def record_for(self, spec: ProviderSpec, model_id: str) -> ProviderModelOut | None:
        """The workspace's ``provider_models`` row for ``model_id`` under ``spec``'s home and kind."""
        return self.model_records.get((credential_home(spec), spec.kind, model_id))

    def catalog_item(self, provider_id: str, model_id: str) -> CatalogItem | None:
        """``model_id``'s item in ``provider_id``'s cached live catalog, if listed."""
        return self.catalog_items.get(provider_id, {}).get(model_id)

    def slots(self) -> list[tuple[ProviderSlot, ProviderRef]]:
        """The (slot, ref) pairs the pipeline declares, plus `qa.model` when set (R-V2-6).

        `qa.model` is not a `pipeline.*` field, so it never comes out of
        `_slots_in_use`; folding it in here means every check that walks
        `ctx.slots()` — `_validate_slot` below and any `VALIDATORS`-registered
        check (e.g. V2-06's workspace-disabled check) — validates it exactly
        like a pipeline slot (kind, credential, availability, installed-on,
        enabled) for free. QA counts as on when ``effective_qa`` says so (R-V2-11:
        a flow ``qa`` node turns it on).
        """
        pairs = _slots_in_use(self.config.pipeline)
        qa = effective_qa(self.config)
        if qa.enabled and qa.model is not None:
            pairs.append(("qa_llm", qa.model))
        return pairs


Validator = Callable[[ValidationContext], list[Issue]]

#: Extra checks registered by other packages; each runs after the built-in ones.
VALIDATORS: list[Validator] = []


def register_validator(validator: Validator) -> Validator:
    """Append ``validator`` to :data:`VALIDATORS` once (usable as a decorator)."""
    if validator not in VALIDATORS:
        VALIDATORS.append(validator)
    return validator


# --------------------------------------------------------------------------- helpers
def _slots_in_use(pipeline: PipelineConfig) -> list[tuple[ProviderSlot, ProviderRef]]:
    """Return the (slot, ref) pairs the pipeline actually declares."""
    pairs: list[tuple[ProviderSlot, ProviderRef]] = []
    for slot in cast(tuple[ProviderSlot, ...], tuple(SLOT_KIND)):
        ref = getattr(pipeline, slot, None)
        if isinstance(ref, ProviderRef):
            pairs.append((slot, ref))
    return pairs


def _assign_nested(target: dict[str, Any], dotted_name: str, value: object) -> None:
    """Assign ``value`` at a dotted kwarg path, creating intermediate dicts.

    ``"simli_config.face_id"`` becomes ``{"simli_config": {"face_id": value}}``.
    """
    parts = dotted_name.split(".")
    cursor = target
    for part in parts[:-1]:
        nxt = cursor.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cursor[part] = nxt
        cursor = cast(dict[str, Any], nxt)
    cursor[parts[-1]] = value


def _spec_or_none(provider_id: str) -> ProviderSpec | None:
    try:
        return get(provider_id)
    except KeyError:
        return None


def _voice_field_name(spec: ProviderSpec) -> str | None:
    """Return the spec field that selects a voice, if it has one."""
    for field_spec in spec.fields:
        if field_spec.name in VOICE_FIELD_NAMES:
            return field_spec.name
    return None


def image_carries(image: str, spec: ProviderSpec) -> bool:
    """Whether a worker image of flavour ``image`` ships ``spec``'s plugin."""
    return spec.worker_image in IMAGE_CARRIES.get(image, frozenset())


def installed_on(spec: ProviderSpec, connection: ConnectionContext | None) -> bool:
    """Whether the connection's worker pool can construct ``spec`` (rule 2 above).

    Registered workers' ``installed_provider_ids`` win; until one has
    registered the connection's ``worker_image`` decides (``slim`` without a
    connection, the v1 behaviour).
    """
    if connection is not None and connection.installed_provider_ids is not None:
        return spec.id in connection.installed_provider_ids
    return image_carries(connection.worker_image if connection else "slim", spec)


def requires_inference(spec: ProviderSpec) -> bool:
    """Whether ``spec`` only works through LiveKit Inference (a Cloud feature)."""
    if spec.id.startswith(INFERENCE_PREFIX):
        return True
    return spec.capabilities.cloud_only and spec.kind != "noise_cancellation"


def _connection_label(connection: ConnectionContext) -> str:
    return f"connection '{connection.name or connection.connection_id}'"


class _Findings:
    """Collects issues and the flat v1 ``errors``/``warnings`` strings in one pass."""

    def __init__(self) -> None:
        self.issues: list[Issue] = []
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def add(self, severity: Severity, path: str, message: str, *, flat: str | None = None) -> None:
        self.issues.append(Issue(path=path, message=message, severity=severity))
        text = flat if flat is not None else (f"{path}: {message}" if path else message)
        (self.errors if severity == "error" else self.warnings).append(text)

    def extend(self, issues: list[Issue]) -> None:
        for issue in issues:
            self.add(issue.severity, issue.path, issue.message)

    def result(self) -> ValidationResult:
        return ValidationResult(
            ok=not self.errors, errors=self.errors, warnings=self.warnings, issues=self.issues
        )


# ------------------------------------------------------------------------ validation
def validate_agent_config(
    config: AgentConfig,
    *,
    credential_providers: Mapping[str, str],
    known_tool_ids: set[str] | None = None,
    known_kb_ids: set[str] | None = None,
    connection: ConnectionContext | None = None,
    workspace_id: str | None = None,
    disabled_provider_ids: frozenset[str] = frozenset(),
) -> ValidationResult:
    """Validate an ``AgentConfig`` against the registry, credentials and connection.

    Args:
        config: The configuration an admin is trying to save.
        credential_providers: ``{credential_id: provider_id}`` for every stored credential.
        known_tool_ids: Existing tool row ids; skipped when ``None``.
        known_kb_ids: Existing knowledge-base ids; skipped when ``None``.
        connection: The agent's connection; ``None`` assumes the ``slim``
            image and skips capability checks (the v1 behaviour).
        workspace_id: The agent's workspace, for registered validators.
        disabled_provider_ids: Providers the workspace switched off.

    Returns:
        A :class:`ValidationResult`; ``ok`` is false when ``errors`` is non-empty.
    """
    return validate(
        ValidationContext(
            config=config,
            credential_providers=credential_providers,
            connection=connection,
            workspace_id=workspace_id,
            disabled_provider_ids=disabled_provider_ids,
            known_tool_ids=frozenset(known_tool_ids) if known_tool_ids is not None else None,
            known_kb_ids=frozenset(known_kb_ids) if known_kb_ids is not None else None,
        )
    )


def validate(ctx: ValidationContext) -> ValidationResult:
    """Run the built-in checks, the connection checks and every registered validator."""
    findings = _Findings()
    config = ctx.config
    pipeline = config.pipeline

    for slot in REQUIRED_SLOTS[pipeline.mode]:
        if getattr(pipeline, slot) is None:
            findings.add(
                "error",
                f"pipeline.{slot}",
                f"a {slot} provider is required in {pipeline.mode} mode",
                flat=f"pipeline.{slot} is required when mode is '{pipeline.mode}'",
            )

    for slot, ref in ctx.slots():
        _validate_slot(ctx, slot, ref, findings)

    _validate_modes(ctx, findings)

    stored_turn_handling = turn_handling_dict(pipeline.turn_handling)
    for key in stored_turn_handling:
        if key == "turn_detection":
            findings.add(
                "error",
                "pipeline.turn_handling",
                "'turn_detection' is set by the platform and cannot be overridden",
            )
        elif key not in TURN_HANDLING_KEYS:
            findings.add("warning", "pipeline.turn_handling", f"unknown key '{key}' will be ignored")
    try:
        TurnHandlingOptions.model_validate(stored_turn_handling)
    except ValidationError as exc:
        # V5-07: the contract keeps a dict whose typed keys do not validate (compatibility);
        # say which value the session may refuse instead of failing the save.
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"])
        findings.add(
            "warning",
            f"pipeline.turn_handling.{where}",
            f"'{where}' does not look right ({first['msg']}); the call may fail to start with it",
        )

    if ctx.known_tool_ids is not None:
        for tool_id in config.tools.tool_ids:
            if tool_id not in ctx.known_tool_ids:
                findings.add("error", "tools.tool_ids", f"unknown tool '{tool_id}'")
    if ctx.known_kb_ids is not None:
        for kb_id in config.knowledge.kb_ids:
            if kb_id not in ctx.known_kb_ids:
                findings.add("error", "knowledge.kb_ids", f"unknown knowledge base '{kb_id}'")

    if not config.instructions.strip():
        findings.add(
            "warning",
            "instructions",
            "instructions are empty; the agent will rely on the model's defaults",
            flat="instructions are empty; the agent will rely on the model's defaults",
        )

    findings.extend(connection_flag_issues(ctx))
    findings.extend(knowledge_auto_inject_issues(ctx))
    findings.extend(knowledge_retrieval_issues(ctx))
    findings.extend(tool_execution_issues(ctx))
    findings.extend(apps_issues(ctx))
    findings.extend(conversation_preset_issues(ctx))
    findings.extend(telephony_noise_cancellation_issues(ctx))
    findings.extend(speech_latency_issues(ctx))
    findings.extend(choices_on_phone_issues(ctx))
    for validator in list(VALIDATORS):
        findings.extend(validator(ctx))
    return findings.result()


def _slot_path(slot: ProviderSlot) -> str:
    """The config path a slot's issues are addressed at.

    `qa_llm` (R-V2-6) is `config.qa.model`, not `pipeline.qa_llm` — no such
    field exists, since it is resolved from `AgentConfig.qa`, not the pipeline.
    """
    return "qa.model" if slot == "qa_llm" else f"pipeline.{slot}"


def _validate_slot(ctx: ValidationContext, slot: ProviderSlot, ref: ProviderRef, findings: _Findings) -> None:
    label = _slot_path(slot)
    spec = _spec_or_none(ref.provider_id)
    if spec is None:
        findings.add("error", label, f"unknown provider '{ref.provider_id}'")
        return
    if spec.kind != SLOT_KIND[slot]:
        findings.add(
            "error", label, f"provider '{spec.id}' is a {spec.kind} provider, expected {SLOT_KIND[slot]}"
        )
        return
    if spec.availability != "available":
        findings.add(
            "error", label, f"provider '{spec.id}' is not available yet (availability={spec.availability})"
        )
    elif not installed_on(spec, ctx.connection):
        findings.add("error", label, _not_installed_message(spec, ctx.connection))

    _validate_credential(label, ref, spec, ctx.credential_providers, findings)
    _validate_model(ctx, label, ref, spec, findings)
    _validate_fields(label, ref, spec, findings)


def _not_installed_message(spec: ProviderSpec, connection: ConnectionContext | None) -> str:
    if connection is None:
        return (
            f"provider '{spec.id}' is not available yet on the default worker pool "
            f"(it needs the '{spec.worker_image}' worker image; the pool runs 'slim')"
        )
    where = _connection_label(connection)
    if connection.installed_provider_ids is not None:
        return f"provider '{spec.id}' is not installed on {where}'s worker pool"
    return (
        f"provider '{spec.id}' needs the '{spec.worker_image}' worker image; "
        f"{where} runs '{connection.worker_image}'"
    )


def _validate_modes(ctx: ValidationContext, findings: _Findings) -> None:
    config = ctx.config
    pipeline = config.pipeline
    wants_video = config.capabilities.camera or config.capabilities.screen_share

    if pipeline.mode in ("realtime", "half_cascade") and pipeline.realtime is not None:
        spec = _spec_or_none(pipeline.realtime.provider_id)
        if spec is not None and spec.kind == "realtime":
            if pipeline.mode == "half_cascade" and not spec.capabilities.text_modality:
                findings.add(
                    "error",
                    "pipeline.realtime",
                    f"'{spec.id}' cannot produce text-only output, so it cannot drive half-cascade "
                    "mode; pick a realtime model with text output, or use realtime or cascaded mode",
                )
            if not spec.capabilities.video_input and wants_video:
                findings.add(
                    "warning",
                    "pipeline.realtime",
                    f"'{spec.id}' cannot see video frames; camera/screen share "
                    "still reach the UI and the pin/describe tools, but not the model",
                )

    if pipeline.mode == "cascaded" and pipeline.llm is not None and wants_video:
        _validate_llm_vision(ctx, pipeline.llm, findings)


def _validate_llm_vision(ctx: ValidationContext, ref: ProviderRef, findings: _Findings) -> None:
    """Warn when the cascaded LLM is known not to see images; a tip when nobody knows (D-V4-24).

    Vision comes from the resolved capabilities (declared → "Test model" →
    the cached live catalog → the registry), the same answer the worker gets in
    ``ResolvedProvider.capabilities``, so an OpenRouter model whose catalog item
    lists ``image`` input is not flagged just because the registry's suggestion
    list carries no ``supports_video`` flag for it. The model id is quoted only
    when the registry lists it (a typed id may be a pasted key, R-V4-21), and
    the suggestion never names the configured model.
    """
    spec = _spec_or_none(ref.provider_id)
    model = ref.model or (spec.default_model if spec is not None else None)
    if spec is None or spec.kind != "llm" or not model or validate_model_id(model) is not None:
        return
    caps = resolve_capabilities(spec, model, ctx.record_for(spec, model), ctx.catalog_item(spec.id, model))
    if caps.vision is True:
        return
    listed = {m.id for m in spec.models}
    name = f"'{model}'" if model in listed or model == spec.default_model else "this model"
    if caps.vision is False:
        findings.add(
            "warning",
            "pipeline.llm",
            f"{name} cannot see images (per its {caps.source or 'registry'} capabilities); camera/screen "
            "share still reach the UI and pin_frame, but per-turn vision and describe_current_frame "
            f"are disabled — {_vision_suggestion(spec, model)}",
        )
        return
    findings.add(
        "warning",
        "pipeline.llm",
        f"Tip: whether {name} accepts images is unknown, so camera/screen share frames are still "
        "sent to it; run Test model with the vision probe, or declare its capabilities, to be sure",
    )


def _vision_suggestion(spec: ProviderSpec, model: str) -> str:
    """Vision models of the same provider (never ``model`` itself), else the vision probe."""
    others = [m.id for m in spec.models if m.supports_video and m.id != model]
    if others:
        return f"pick a model marked 'supports video' (e.g. {', '.join(others[:2])})"
    return (
        "if it does accept images, run Test model with the vision probe or declare its capabilities; "
        "otherwise pick a vision model"
    )


def _validate_credential(
    label: str,
    ref: ProviderRef,
    spec: ProviderSpec,
    credential_providers: Mapping[str, str],
    findings: _Findings,
) -> None:
    if spec.requires_credential:
        if not ref.credential_id:
            findings.add("error", label, f"provider '{spec.id}' requires a credential")
            return
    elif ref.credential_id:
        findings.add("warning", label, f"provider '{spec.id}' needs no credential; the reference is ignored")
        return
    if ref.credential_id:
        # R-V4-7: a provider with a credential home (every OpenRouter entry)
        # accepts the rows stored under that home.
        owner = credential_providers.get(ref.credential_id)
        home = credential_home(spec)
        if owner is None:
            findings.add("error", label, f"unknown credential '{ref.credential_id}'")
        elif owner != home:
            findings.add(
                "error",
                label,
                f"credential '{ref.credential_id}' belongs to provider '{owner}', not '{home}'",
            )


#: How long a passing "Test model" run silences the unknown-model warning (R-V4-24).
TESTED_WINDOW_S = 30 * 24 * 3600


def _validate_model(
    ctx: ValidationContext, label: str, ref: ProviderRef, spec: ProviderSpec, findings: _Findings
) -> None:
    """Warn once about a model id nobody vouches for (D-V4-23, R-V4-21, R-V4-26).

    The id is "known" when the registry suggests it, the workspace's cached
    live catalog lists it, or a "Test model" run with the slot's current key
    answered within :data:`TESTED_WINDOW_S`. The message never contains the id
    (it may be a pasted key). An id that fails the syntax/secret rule is left
    to the registered custom-model validator, which reports the error; a
    failed current test is reported there too, with its reason.
    """
    model = ref.model
    if not model or validate_model_id(model) is not None:
        return
    if model == spec.default_model or model in {m.id for m in spec.models}:
        return
    if ctx.catalog_item(spec.id, model) is not None:
        return
    record = ctx.record_for(spec, model)
    if record is not None and record.last_test_at is not None and record.last_test_ok is not None:
        fingerprint = ctx.fingerprint_for(ref)
        fresh = (dt.datetime.now(dt.UTC) - record.last_test_at).total_seconds() <= TESTED_WINDOW_S
        if fresh and (fingerprint is None or record.last_test_fingerprint == fingerprint):
            return
    findings.add(
        "warning",
        label,
        f"this model id is not in the suggestion list or the live catalog for '{spec.id}' "
        "— run Test model, or pick a listed one",
    )


def _validate_fields(label: str, ref: ProviderRef, spec: ProviderSpec, findings: _Findings) -> None:
    by_name = {f.name: f for f in spec.fields}
    for name, value in ref.fields.items():
        field_spec = by_name.get(name)
        if field_spec is None:
            findings.add("warning", label, f"unknown field '{name}' for provider '{spec.id}'")
            continue
        if field_spec.type == "enum" and field_spec.options and str(value) not in field_spec.options:
            findings.add(
                "error",
                label,
                # Never echo the value: it may be a pasted key (R-V4-21).
                f"field '{name}' must be one of {', '.join(field_spec.options)}",
            )
    for field_spec in spec.fields:
        if field_spec.required and field_spec.default is None and field_spec.name not in ref.fields:
            findings.add("error", label, f"field '{field_spec.name}' is required for provider '{spec.id}'")


#: Shown on a `choices` block of an agent set up for phone calls (V5-08).
CHOICES_ON_PHONE_MESSAGE: Final[str] = (
    "callers on a phone line cannot see or tap choices; on a call the agent asks the question "
    "out loud instead"
)


def choices_on_phone_issues(ctx: ValidationContext) -> list[Issue]:
    """Warn that a `choices` block is invisible to callers on a phone line (V5-08, B1).

    Validators never see which channels reach an agent (phone numbers and
    dispatch rules live elsewhere), so an agent counts as set up for phone
    calls when it has a phone-only setting: keypad input
    (``capabilities.dtmf``) or transfer destinations
    (``telephony.transfer_targets``). On such calls ``request_choice`` shows
    nothing and answers ``{"channel": "voice_only"}``; the agent still works
    on the web, so this is a warning. A built-in check called from
    :func:`validate` directly.

    Args:
        ctx: The validation context.

    Returns:
        One warning per ``choices`` block, at ``panel.blocks[i]``.
    """
    config = ctx.config
    if not (config.capabilities.dtmf or config.telephony.transfer_targets):
        return []
    return [
        Issue(path=f"panel.blocks[{index}]", message=CHOICES_ON_PHONE_MESSAGE, severity="warning")
        for index, block in enumerate(config.panel.blocks)
        if block.type == "choices"
    ]


def knowledge_auto_inject_issues(ctx: ValidationContext) -> list[Issue]:
    """A tip that knowledge auto-inject and preemptive generation do not mix.

    Advisory, not a problem: the contracts have only ``error``/``warning``
    severities, so it is a ``warning`` whose message starts with ``Tip:``.

    Auto-inject adds the retrieved text to the chat context in
    ``on_user_turn_completed``; livekit-agents 1.8.2 discards its preemptive
    (speculative) reply whenever that hook changes the context, and still pays
    for it. The worker therefore turns preemptive generation off for an agent
    with auto-inject on and a knowledge base attached, unless
    ``pipeline.turn_handling.preemptive_generation.enabled`` is set explicitly
    (research-v4 knowledge-and-memory P0-0). A built-in check with the
    :data:`Validator` signature, called from :func:`validate` directly so it
    never depends on import order.

    Args:
        ctx: The validation context.

    Returns:
        At most one warning, at ``knowledge.auto_inject``.
    """
    knowledge = ctx.config.knowledge
    if not knowledge.auto_inject or not knowledge.kb_ids:
        return []
    pipeline = ctx.config.pipeline
    preemptive = resolve_turn_handling(pipeline.conversation_preset, pipeline.turn_handling).get(
        "preemptive_generation"
    )
    explicit = preemptive.get("enabled") if isinstance(preemptive, dict) else None
    if explicit is False:
        return []
    if explicit is True:
        message = (
            "Tip: auto-inject changes the conversation on every turn with a knowledge hit, which "
            "discards the preemptive reply this agent keeps enabled (turn_handling.preemptive_generation), "
            "so each such turn pays for two LLM calls. Nothing is broken; for faster, cheaper replies, "
            "turn auto-inject off and let the agent call the search_knowledge tool"
        )
    else:
        message = (
            "Tip: auto-inject turns off preemptive generation for this agent's sessions, so replies "
            "start only after the caller's turn ends. Nothing is broken; for faster replies, turn "
            "auto-inject off and let the agent call the search_knowledge tool"
        )
    return [Issue(path="knowledge.auto_inject", message=message, severity="warning")]


def knowledge_retrieval_issues(ctx: ValidationContext) -> list[Issue]:
    """The ``KnowledgeConfig`` v2 retrieval settings (V5-06).

    * ``min_score`` outside [0, 1] is an error: every search score is on that
      range (cosine, normalised fusion, or the reranker's sigmoid).
    * ``rerank`` other than ``none`` / ``local`` is an error until connection
      rerankers exist (V5-20 widens the accepted set).
    * ``rerank="local"`` with ``prefetch`` off is a warning: the rerank then
      runs inside every turn instead of while the caller is still talking.

    Args:
        ctx: The validation context.

    Returns:
        The issues, at ``knowledge.min_score`` / ``knowledge.rerank``.
    """
    knowledge = ctx.config.knowledge
    issues: list[Issue] = []
    if knowledge.min_score is not None and not 0.0 <= knowledge.min_score <= 1.0:
        issues.append(
            Issue(
                path="knowledge.min_score",
                message="the minimum score must be between 0 and 1",
                severity="error",
            )
        )
    if knowledge.rerank not in KNOWLEDGE_RERANK_VALUES:
        issues.append(
            Issue(
                path="knowledge.rerank",
                message=f"unknown rerank '{knowledge.rerank}'; use 'none' or 'local'",
                severity="error",
            )
        )
    elif knowledge.rerank == "local" and not knowledge.prefetch:
        issues.append(
            Issue(
                path="knowledge.rerank",
                message="reranking without pre-fetch adds ~60 ms to every turn; turn pre-fetch on to "
                "rerank while the caller is still speaking",
                severity="warning",
            )
        )
    return issues


#: Plain names of the ``turn_handling`` keys a preset sets, for warnings the console shows.
_PLAIN_TURN_KEYS: dict[str, str] = {
    "endpointing": "how long to wait before replying",
    "interruption": "how easily the caller interrupts",
    "preemptive_generation": "replying while the caller is still finishing",
}


def conversation_preset_issues(ctx: ValidationContext) -> list[Issue]:
    """A conversation preset on a realtime pipeline: say which of its settings do nothing (V5-07).

    With ``mode == "realtime"`` the model decides when the caller has finished and
    handles interruptions itself, and preemptive generation runs only for a text LLM
    (livekit-agents 1.8.3 ``agent_activity.py:2319`` and ``:2574``), so every key a
    preset sets is ignored. In ``half_cascade`` only preemptive generation is (the
    realtime model still writes the reply); the wait times apply when the model hands
    turn-taking to the worker.

    Args:
        ctx: The validation context.

    Returns:
        At most one warning, at ``pipeline.conversation_preset``.
    """
    pipeline = ctx.config.pipeline
    preset = pipeline.conversation_preset
    if preset == "custom":
        return []
    set_by_preset = CONVERSATION_PRESETS[preset]
    if pipeline.mode == "realtime":
        ignored = [key for key in REALTIME_IGNORED_KEYS if key in set_by_preset]
        reason = "a realtime model decides when the caller has finished and handles interruptions itself"
    elif pipeline.mode == "half_cascade":
        ignored = [key for key in ("preemptive_generation",) if key in set_by_preset]
        reason = "early replies need a text model, and here the realtime model writes the reply"
    else:
        return []
    if not ignored:
        return []
    names = ", ".join(f"{_PLAIN_TURN_KEYS[key]} ({key})" for key in ignored)
    return [
        Issue(
            path="pipeline.conversation_preset",
            message=f"The '{preset}' preset changes less than it says here: {reason}, so these "
            f"settings are ignored: {names}",
            severity="warning",
        )
    ]


def _telephony_filter_offered() -> bool:
    """Whether any registry noise filter with a phone variant is offered (a test seam)."""
    return any(
        spec.telephony_variant is not None and spec.availability == "available"
        for spec in by_kind("noise_cancellation", status=None)
    )


def telephony_noise_cancellation_issues(ctx: ValidationContext) -> list[Issue]:
    """The ``telephony`` preset where phone noise cancellation cannot run (V5-07, D-V5-30).

    The preset tunes turn-taking everywhere and, on a phone call, switches the noise
    filter to its phone variant (turning the LiveKit Cloud one on when none is set).
    That filter is LiveKit Cloud only (``noise_cancellation_tier == "krisp"``), and it
    needs a registry entry with a phone variant that the workers carry. Nothing is
    checked without a connection.

    Args:
        ctx: The validation context.

    Returns:
        At most one warning, at ``pipeline.conversation_preset``.
    """
    connection = ctx.connection
    if connection is None or ctx.config.pipeline.conversation_preset != "telephony":
        return []
    where = _connection_label(connection)
    if connection.capabilities.noise_cancellation_tier != "krisp":
        message = (
            f"The phone call preset tunes turn-taking only: noise cancellation for phone calls "
            f"needs LiveKit Cloud, which {where} does not offer"
        )
    else:
        ref = ctx.config.pipeline.noise_cancellation
        spec = _spec_or_none(ref.provider_id) if ref is not None else None
        if spec is not None and spec.telephony_variant is not None:
            return []
        if spec is None and _telephony_filter_offered():
            return []
        message = (
            f"The phone call preset keeps the noise filter '{spec.label}' as it is: it has no phone version"
            if spec is not None
            else "The phone call preset tunes turn-taking only: no noise filter for phone calls is "
            "available on this platform yet"
        )
    return [Issue(path="pipeline.conversation_preset", message=message, severity="warning")]


#: Speech providers that answer one whole request at a time (no streaming), with the latency
#: that costs, in words an admin can act on (docs/v5/_briefs/openrouter-voice-diagnosis.md).
BATCH_SPEECH_MESSAGES: Final[dict[str, str]] = {
    "openrouter-stt": (
        "OpenRouter transcribes each turn in one request after you stop speaking (no live "
        "transcript), which adds roughly 0.5-2 s per turn; for a snappy voice agent use a streaming "
        "STT such as LiveKit Inference or Deepgram"
    ),
    "openrouter-tts": (
        "OpenRouter speech is not streamed: each sentence is synthesised in full before it starts "
        "playing, which adds roughly 1-2.5 s before the agent speaks; for a snappy voice agent use a "
        "streaming TTS such as LiveKit Inference or Cartesia"
    ),
}

#: STT classes whose ``language`` must be one ISO-639-1 code (OpenAI's and OpenRouter's
#: ``/audio/transcriptions``); the worker maps anything else (lkap_agent.providers.factory).
_ISO_639_1_STT_CLASSES: Final[frozenset[str]] = frozenset({"livekit.plugins.openai.STT"})
_ISO_639_1: Final[re.Pattern[str]] = re.compile(r"^[a-z]{2}$")


def speech_latency_issues(ctx: ValidationContext) -> list[Issue]:
    """Warn about batch-only speech providers and language codes they cannot take.

    Only the slots a voice session actually uses are checked: ``stt`` in cascaded
    mode, ``tts`` in cascaded and half-cascade mode.

    Args:
        ctx: The validation context.

    Returns:
        A warning per batch-only speech slot, plus one at ``pipeline.stt.fields.language``
        when an OpenAI-style transcriber is given a code it would reject.
    """
    pipeline = ctx.config.pipeline
    slots: list[tuple[str, ProviderRef | None]] = []
    if pipeline.mode == "cascaded":
        slots.append(("stt", pipeline.stt))
    if pipeline.mode in ("cascaded", "half_cascade"):
        slots.append(("tts", pipeline.tts))
    issues: list[Issue] = []
    for slot, ref in slots:
        if ref is None:
            continue
        message = BATCH_SPEECH_MESSAGES.get(ref.provider_id)
        if message is not None:
            issues.append(Issue(path=f"pipeline.{slot}", message=message, severity="warning"))
        spec = _spec_or_none(ref.provider_id)
        if slot != "stt" or spec is None or spec.python_class not in _ISO_639_1_STT_CLASSES:
            continue
        language = ref.fields.get("language")
        if isinstance(language, str) and not _ISO_639_1.match(language.strip()):
            code = language.strip()
            detail = (
                "the language will be auto-detected"
                if code.lower() in ("", "multi", "auto", "detect")
                else f"'{code.replace('_', '-').split('-', 1)[0].lower()}' will be sent instead"
            )
            issues.append(
                Issue(
                    path="pipeline.stt.fields.language",
                    message=f"'{code}' is not a two-letter language code this transcriber accepts; {detail}",
                    severity="warning",
                )
            )
    return issues


#: Below this many tool steps, a chain of background announcements can exhaust the budget
#: (each first update spends one step; docs/v4/BACKGROUND-TOOLS.md §5, R-V4-35).
MIN_TOOL_STEPS_FOR_BACKGROUND = 4


def _execution_mode(raw: object) -> str | None:
    """The ``mode`` of a raw ``ToolExecution`` JSON object, or ``None``."""
    if not isinstance(raw, Mapping):
        return None
    mode = raw.get("mode")
    return mode if isinstance(mode, str) else None


def tool_execution_issues(ctx: ValidationContext) -> list[Issue]:
    """The background-tool checks of V4-12 (docs/v4/BACKGROUND-TOOLS.md D-V4-32, R-V4-35/36).

    * ``tools.builtin_execution`` names a built-in outside ``BACKGROUNDABLE_BUILTINS`` → error.
    * ``tools.execution_default`` is not ``blocking`` while ``max_tool_steps`` is below
      :data:`MIN_TOOL_STEPS_FOR_BACKGROUND` → warning (chained announcements spend steps).
    * Per attached tool row (``tools[i]`` is the i-th ``tools.tool_ids`` entry), read from the
      stored JSON so a row that no longer parses is still reported:
      an HTTP tool with ``silent_reply`` and a non-blocking mode → error (the contract refuses it
      on save; a row written before the contract did is caught here); a never-list tool name
      with a non-blocking mode → error; an MCP ``tool_options`` name outside ``allowed_tools``
      → error; an MCP tool with a non-blocking mode and ``report_progress`` off → warning
      (the model is told nothing, so the tool is not announced).

    Args:
        ctx: The validation context.

    Returns:
        Issues at ``tools.builtin_execution.<name>``, ``tools.max_tool_steps`` and
        ``tools[i].definition...``.
    """
    tools = ctx.config.tools
    issues: list[Issue] = []
    for name in sorted(set(tools.builtin_execution) - BACKGROUNDABLE_BUILTINS):
        issues.append(
            Issue(
                path=f"tools.builtin_execution.{name}",
                message=f"'{name}' always runs blocking; only "
                f"{', '.join(sorted(BACKGROUNDABLE_BUILTINS))} can run in the background",
            )
        )
    if tools.execution_default != "blocking" and tools.max_tool_steps < MIN_TOOL_STEPS_FOR_BACKGROUND:
        issues.append(
            Issue(
                path="tools.max_tool_steps",
                message=f"read tools run '{tools.execution_default}' by default and each announcement "
                f"spends a tool step; with max_tool_steps {tools.max_tool_steps} a chain of lookups can "
                f"run out of steps — use {MIN_TOOL_STEPS_FOR_BACKGROUND} or more",
                severity="warning",
            )
        )
    definitions = ctx.tool_definitions_by_id
    if definitions is None:
        return issues
    for index, tool_id in enumerate(tools.tool_ids):
        definition = definitions.get(tool_id)
        if not isinstance(definition, Mapping):
            continue
        base = f"tools[{index}].definition"
        name = str(definition.get("name") or tool_id)
        if definition.get("kind", "http") == "http":
            mode = _execution_mode(definition.get("execution"))
            if mode in NON_BLOCKING_MODES and definition.get("silent_reply"):
                issues.append(
                    Issue(
                        path=f"{base}.execution.mode",
                        message=f"'{name}' has silent_reply on, which would swallow its background "
                        "announcement; turn one of them off",
                    )
                )
            if mode in NON_BLOCKING_MODES and never_background(name):
                issues.append(Issue(path=f"{base}.execution.mode", message=f"'{name}' always runs blocking"))
            continue
        options = definition.get("tool_options")
        if not isinstance(options, Mapping):
            continue
        allowed = definition.get("allowed_tools")
        for tool_name, raw in options.items():
            path = f"{base}.tool_options.{tool_name}"
            if isinstance(allowed, list) and tool_name not in allowed:
                issues.append(
                    Issue(path=path, message=f"'{tool_name}' is not one of this server's allowed_tools")
                )
            mode = _execution_mode(raw)
            if mode not in NON_BLOCKING_MODES:
                continue
            if never_background(str(tool_name)):
                issues.append(Issue(path=f"{path}.mode", message=f"'{tool_name}' always runs blocking"))
            elif not (isinstance(raw, Mapping) and raw.get("report_progress")):
                issues.append(
                    Issue(
                        path=f"{path}.report_progress",
                        message=f"'{tool_name}' runs in the background but forwards no progress "
                        "messages: the model will not announce this tool",
                        severity="warning",
                    )
                )
    return issues


#: Connection statuses that stop an app's actions (D-V5-C9).
BROKEN_CONNECTION_STATUSES: frozenset[str] = frozenset({"expired", "failed", "inactive"})


def apps_issues(ctx: ValidationContext) -> list[Issue]:
    """The connected-apps checks of V5-47 (docs/v5/COMPOSIO.md §4).

    * ``tools.apps.mode`` other than ``off`` without a Composio key, or with Composio turned off
      for the workspace → error at ``tools.apps.mode``.
    * The tool finder with ``manage_connections`` on → warning: a phone caller cannot open a
      sign-in link (it only helps text and web chats).
    * An attached ``provider`` tool whose connection is gone, or expired, failed or disconnected
      → error naming the app; one whose key is not a Composio key → error.
    * The app server or tool finder with destructive actions in scope that are not in
      ``reviewed_actions`` → warning at ``tools.apps.denied_actions`` naming them: the api
      blocks them until the builder reviews them (R-V5-9). In scope here are the workspace's
      picked actions of the agent's apps (what an app server offers); a tool finder can also
      reach destructive actions nobody picked, which only provisioning sees (it reads the
      catalogue) and blocks all the same.

    Args:
        ctx: The validation context.

    Returns:
        Issues at ``tools.apps.*`` and ``tools[i].definition.*``.
    """
    tools = ctx.config.tools
    apps = tools.apps
    issues: list[Issue] = []
    has_key = COMPOSIO_PROVIDER_ID in set(ctx.credential_providers.values())
    if apps.mode != "off":
        if not has_key:
            issues.append(
                Issue(
                    path="tools.apps.mode",
                    message="connected apps need a Composio key: add one under Tools, Apps, Enable Composio",
                )
            )
        elif COMPOSIO_PROVIDER_ID in ctx.disabled_provider_ids:
            issues.append(
                Issue(
                    path="tools.apps.mode",
                    message="Apps are turned off for this workspace; enable Composio again under Tools, Apps",
                )
            )
    if apps.mode == "router" and apps.router.manage_connections:
        issues.append(
            Issue(
                path="tools.apps.router.manage_connections",
                message="a phone caller cannot open a sign-in link: letting the agent connect apps only "
                "helps text and web chats",
                severity="warning",
            )
        )
    definitions = ctx.tool_definitions_by_id
    if definitions is None:
        return issues
    if apps.mode in ("server", "router"):
        issues.extend(_unreviewed_destructive_issues(ctx, definitions))
    for index, tool_id in enumerate(tools.tool_ids):
        definition = definitions.get(tool_id)
        if not isinstance(definition, Mapping) or definition.get("kind") != "provider":
            continue
        base = f"tools[{index}].definition"
        app = str(definition.get("toolkit") or definition.get("name") or tool_id)
        connection_id = definition.get("connection_id")
        status = ctx.connection_statuses.get(connection_id) if isinstance(connection_id, str) else None
        if status is None:
            issues.append(
                Issue(path=f"{base}.connection_id", message=f"the '{app}' app is no longer connected")
            )
        elif status in BROKEN_CONNECTION_STATUSES:
            issues.append(
                Issue(
                    path=f"{base}.connection_id",
                    message=f"the '{app}' app needs to be reconnected (status {status}); reconnect it "
                    "under Tools, Apps",
                )
            )
        credential_id = definition.get("credential_id")
        if (
            isinstance(credential_id, str)
            and ctx.credential_providers.get(credential_id) != COMPOSIO_PROVIDER_ID
        ):
            issues.append(
                Issue(
                    path=f"{base}.credential_id", message=f"'{app}' actions need the workspace's Composio key"
                )
            )
    return issues


def _unreviewed_destructive_issues(
    ctx: ValidationContext, definitions: Mapping[str, Mapping[str, Any]]
) -> list[Issue]:
    """The warning naming picked destructive actions the builder has not reviewed (R-V5-9)."""
    apps = ctx.config.tools.apps
    allowed = {slug.lower() for slug in apps.allowed_toolkits}
    reviewed = {slug.upper() for slug in apps.reviewed_actions}
    unreviewed: set[str] = set()
    for definition in definitions.values():
        if definition.get("kind") != "provider":
            continue
        slug = str(definition.get("tool_slug") or "").upper()
        toolkit = str(definition.get("toolkit") or "").lower()
        connection_id = definition.get("connection_id")
        if not slug or (allowed and toolkit not in allowed):
            continue
        if not isinstance(connection_id, str) or connection_id not in ctx.connection_statuses:
            continue
        if action_risk(slug) == "destructive" and slug not in reviewed:
            unreviewed.add(slug)
    if not unreviewed:
        return []
    names = ", ".join(sorted(unreviewed))
    return [
        Issue(
            path="tools.apps.denied_actions",
            message=f"blocked until reviewed in the Connected apps card: {names} "
            "(these actions delete, remove or move money, so the agent cannot use them until you "
            "allow or deny each one)",
            severity="warning",
        )
    ]


def connection_flag_issues(ctx: ValidationContext) -> list[Issue]:
    """Capability-flag checks against the agent's connection (ARCHITECTURE-V2 D-V2-4).

    Flags gate validation, not just the UI: LiveKit Inference providers need
    ``inference_available``; Krisp noise cancellation needs a Cloud connection;
    DTMF needs ``sip_enabled``. Recording without Egress and an implicit
    Inference workflow LLM on a connection without Inference are warnings.

    Args:
        ctx: The validation context; nothing is checked without a connection.

    Returns:
        Issues addressed at the offending config path.
    """
    connection = ctx.connection
    if connection is None:
        return []
    caps = connection.capabilities
    where = _connection_label(connection)
    no_inference_reason = (
        "self-hosted connections cannot use LiveKit Inference — use your own STT/LLM/TTS keys"
        if connection.deployment_type == "self_hosted"
        else "its 'Use LiveKit Inference' setting is off"
    )
    issues: list[Issue] = []
    for slot, ref in ctx.slots():
        spec = _spec_or_none(ref.provider_id)
        if spec is None or spec.kind != SLOT_KIND[slot]:
            continue
        if requires_inference(spec) and not caps.inference_available:
            issues.append(
                Issue(
                    path=_slot_path(slot),
                    message=f"'{spec.id}' needs LiveKit Inference, which {where} does not offer "
                    f"({no_inference_reason})",
                )
            )
        if (
            spec.kind == "noise_cancellation"
            and spec.capabilities.cloud_only
            and caps.noise_cancellation_tier != "krisp"
        ):
            issues.append(
                Issue(
                    path=_slot_path(slot),
                    message=f"'{spec.id}' needs a LiveKit Cloud connection; {where} is self-hosted",
                )
            )

    pipeline = ctx.config.pipeline
    if pipeline.workflow_llm is None and pipeline.mode != "cascaded" and not caps.inference_available:
        issues.append(
            Issue(
                path="pipeline.workflow_llm",
                message=f"defaults to LiveKit Inference, which {where} does not offer; "
                "set a workflow LLM with your own key or tools that need it will fail",
                severity="warning",
            )
        )
    if ctx.config.capabilities.dtmf and not caps.sip_enabled:
        issues.append(
            Issue(path="capabilities.dtmf", message=f"DTMF needs SIP, which is not reachable on {where}")
        )
    if ctx.config.recording.enabled and not caps.egress_enabled:
        issues.append(
            Issue(
                path="recording.enabled",
                message=f"recording needs Egress, which is not reachable on {where}; "
                "sessions will not be recorded",
                severity="warning",
            )
        )
    return issues


# ------------------------------------------------------------------ database helper
async def installed_provider_ids(db: AsyncSession, connection_id: str) -> frozenset[str] | None:
    """Union of the ``installed_provider_ids`` the connection's live workers reported.

    Returns ``None`` when no worker has registered (or none reported a list),
    which makes validation fall back to the connection's ``worker_image``.
    """
    rows = (
        await db.execute(
            select(WorkerInstance.installed_provider_ids).where(
                WorkerInstance.connection_id == connection_id,
                WorkerInstance.status.in_(LIVE_WORKER_STATUSES),
            )
        )
    ).scalars()
    installed: set[str] = set()
    for ids in rows:
        if isinstance(ids, list):
            installed.update(str(i) for i in ids)
    return frozenset(installed) if installed else None


async def connection_context_for(
    db: AsyncSession, *, workspace_id: str, connection_id: str | None
) -> ConnectionContext | None:
    """Load the :class:`ConnectionContext` of a connection (or the workspace default).

    Returns ``None`` when neither exists, which keeps the v1 behaviour.
    """
    stmt = select(LiveKitConnection).where(LiveKitConnection.workspace_id == workspace_id)
    stmt = (
        stmt.where(LiveKitConnection.id == connection_id)
        if connection_id
        else stmt.where(LiveKitConnection.is_default.is_(True))
    )
    row = await db.scalar(stmt)
    if row is None:
        return None
    return ConnectionContext.from_row(row, await installed_provider_ids(db, row.id))


async def validation_context_for(
    db: AsyncSession,
    config: AgentConfig,
    *,
    workspace_id: str,
    connection_id: str | None = None,
) -> ValidationContext:
    """Build a :class:`ValidationContext` from the database.

    Args:
        db: Open session.
        config: The configuration to validate.
        workspace_id: The agent's workspace; every lookup is scoped to it.
        connection_id: The agent's connection; ``None`` means the workspace default.

    Returns:
        A context with credentials, tools, knowledge bases, disabled providers
        and the connection (with its pool's installed providers).
    """
    credential_rows = (
        (
            await db.execute(
                select(Credential.id, Credential.provider_id, Credential.fingerprint).where(
                    Credential.workspace_id == workspace_id
                )
            )
        )
        .tuples()
        .all()
    )
    credential_providers = {row[0]: row[1] for row in credential_rows}
    connection_statuses = {
        row[0]: row[1] or "unknown"
        for row in (
            await db.execute(
                select(Credential.id, Credential.last_test_message).where(
                    Credential.workspace_id == workspace_id, Credential.provider_id == TOOL_PROVIDER_ACCOUNT
                )
            )
        ).tuples()
    }
    tool_rows = (
        (
            await db.execute(
                select(Tool.id, Tool.name, Tool.definition).where(Tool.workspace_id == workspace_id)
            )
        )
        .tuples()
        .all()
    )
    tool_names_by_id = {row[0]: row[1] for row in tool_rows}
    tool_definitions_by_id = {row[0]: row[2] for row in tool_rows if isinstance(row[2], dict)}
    tool_ids = frozenset(tool_names_by_id)
    kb_ids = frozenset(
        (
            await db.execute(select(KnowledgeBase.id).where(KnowledgeBase.workspace_id == workspace_id))
        ).scalars()
    )
    disabled = frozenset(
        (
            await db.execute(
                select(WorkspaceProvider.provider_id).where(
                    WorkspaceProvider.workspace_id == workspace_id, WorkspaceProvider.enabled.is_(False)
                )
            )
        ).scalars()
    )
    return ValidationContext(
        config=config,
        credential_providers=credential_providers,
        connection=await connection_context_for(db, workspace_id=workspace_id, connection_id=connection_id),
        workspace_id=workspace_id,
        disabled_provider_ids=disabled,
        known_tool_ids=tool_ids,
        known_kb_ids=kb_ids,
        tool_names_by_id=tool_names_by_id,
        tool_definitions_by_id=tool_definitions_by_id,
        connection_statuses=connection_statuses,
        telephony_policy=await _telephony_policy(db, workspace_id),
        credential_fingerprints={row[0]: row[2] for row in credential_rows},
        model_records=await _model_records(db, workspace_id),
        catalog_items=await _catalog_items(
            db,
            provider_ids={ref.provider_id for _, ref in ValidationContext(config=config).slots()},
            credential_ids=list(credential_providers),
        ),
    )


async def _model_records(db: AsyncSession, workspace_id: str) -> dict[tuple[str, str, str], ProviderModelOut]:
    """The workspace's ``provider_models`` rows, in one query (V4-07)."""
    from lkap_api.custom_models.records import records_for_workspace  # noqa: PLC0415 - avoids an import cycle

    return await records_for_workspace(db, workspace_id=workspace_id)


async def _catalog_items(
    db: AsyncSession, *, provider_ids: set[str], credential_ids: list[str]
) -> dict[str, dict[str, CatalogItem]]:
    """Cached live ``models`` items for ``provider_ids``, fresh or stale, in one query (V4-07).

    ``provider_catalog_cache`` has no ``workspace_id``: the rows read are the
    workspace's own credentials' rows plus the keyless rows of public lists.
    """
    if not provider_ids:
        return {}
    rows = (
        await db.execute(
            select(ProviderCatalogCache.provider_id, ProviderCatalogCache.items).where(
                ProviderCatalogCache.provider_id.in_(provider_ids),
                ProviderCatalogCache.kind == "models",
                or_(
                    ProviderCatalogCache.credential_id.in_(credential_ids),
                    ProviderCatalogCache.credential_id.is_(None),
                ),
            )
        )
    ).tuples()
    out: dict[str, dict[str, CatalogItem]] = {}
    for provider_id, items in rows:
        bucket = out.setdefault(provider_id, {})
        for raw in items if isinstance(items, list) else []:
            if isinstance(raw, dict) and isinstance(raw.get("id"), str):
                bucket.setdefault(raw["id"], CatalogItem.model_validate(raw))
    return out


async def _telephony_policy(db: AsyncSession, workspace_id: str) -> TelephonyPolicy:
    """The workspace's dialing policy (R-V2-23), default deny when unset."""
    from lkap_api.telephony.policy import workspace_policy  # noqa: PLC0415 - see the TYPE_CHECKING import

    return await workspace_policy(db, workspace_id)


async def validate_in_db(
    db: AsyncSession,
    config: AgentConfig,
    *,
    workspace_id: str,
    connection_id: str | None = None,
    pack_tool_names: frozenset[str] | None = None,
) -> ValidationResult:
    """Validate a configuration against everything stored for its workspace and connection.

    This is the call the agents router should use on save/publish/validate
    (it replaces the v1 ``validate_stored_config`` helper there).
    ``pack_tool_names`` is the agent's own pack's tool names (asks V2-16-2).
    """
    context = await validation_context_for(db, config, workspace_id=workspace_id, connection_id=connection_id)
    return validate(dataclasses.replace(context, pack_tool_names=pack_tool_names))


# --------------------------------------------------------------------------- seeding
def seed_config_from_manifest(
    manifest: PackManifest,
    *,
    credentials_by_provider: Mapping[str, list[str]],
    connection: ConnectionContext | None = None,
) -> AgentConfig:
    """Build a runnable ``AgentConfig`` from a pack manifest.

    Applies the CONTRACTS §8 seeding rule, migrated per R-V2-2: a slot keeps
    its recommended provider when that provider is ``available``, installed on
    the connection (``worker_image`` ≤ the connection's image until workers
    register) and — if it needs one — has **exactly one** credential; otherwise
    the slot falls back to the credential-free LiveKit Inference provider of
    the same kind (``avatar``/``image_gen`` are dropped, a ``realtime`` or
    ``half_cascade`` pipeline whose realtime model is unusable switches to
    cascaded). Without a connection the ``slim`` image is assumed, which is the
    v1 ``mvp`` set.

    Args:
        manifest: The pack's manifest.
        credentials_by_provider: ``{provider_id: [credential_id, ...]}``.
        connection: The connection the new agent will be bound to.

    Returns:
        A complete configuration ready to store.
    """
    pipeline = manifest.recommended_pipeline.model_copy(deep=True)

    if pipeline.mode in ("realtime", "half_cascade"):
        credential = _unambiguous_credential(pipeline.realtime, credentials_by_provider)
        if pipeline.realtime is None or not _usable(pipeline.realtime, credential, connection):
            pipeline.mode = "cascaded"
            pipeline.realtime = None
        else:
            pipeline.realtime.credential_id = credential

    if pipeline.mode == "cascaded":
        for slot in CASCADED_SLOTS:
            pipeline = _seed_cascaded_slot(pipeline, slot, credentials_by_provider, connection)
    elif pipeline.mode == "half_cascade":
        pipeline.stt = pipeline.llm = None
        pipeline = _seed_cascaded_slot(pipeline, "tts", credentials_by_provider, connection)
    else:
        pipeline.stt = pipeline.llm = pipeline.tts = None

    for slot in ("avatar", "image_gen"):
        ref = cast(ProviderRef | None, getattr(pipeline, slot))
        if ref is None:
            continue
        credential = _unambiguous_credential(ref, credentials_by_provider)
        if not _usable(ref, credential, connection):
            setattr(pipeline, slot, None)
        else:
            ref.credential_id = credential

    if pipeline.workflow_llm is not None:
        credential = _unambiguous_credential(pipeline.workflow_llm, credentials_by_provider)
        if not _usable(pipeline.workflow_llm, credential, connection):
            pipeline.workflow_llm = ProviderRef(provider_id=INFERENCE_DEFAULT["llm"])
        else:
            pipeline.workflow_llm.credential_id = credential

    _apply_default_voices(pipeline, manifest)

    return AgentConfig(
        instructions=manifest.default_instructions,
        pipeline=pipeline,
        voice=VoiceConfig(greeting=manifest.default_greeting),
        capabilities=manifest.capabilities.model_copy(deep=True),
        tools=ToolsConfig(builtin_disabled=list(manifest.builtin_tools_disabled)),
    )


def seedable(spec: ProviderSpec, connection: ConnectionContext | None) -> bool:
    """Whether seeding may keep ``spec``: available and installed on the connection."""
    return spec.availability == "available" and installed_on(spec, connection)


def _usable(ref: ProviderRef, credential: str | None, connection: ConnectionContext | None) -> bool:
    spec = _spec_or_none(ref.provider_id)
    if spec is None or not seedable(spec, connection):
        return False
    return not (spec.requires_credential and credential is None)


def _unambiguous_credential(
    ref: ProviderRef | None, credentials_by_provider: Mapping[str, list[str]]
) -> str | None:
    """Return the single credential for the ref's provider, or ``None``.

    ``credentials_by_provider`` is keyed by each row's *stored* provider id,
    which for a provider with a credential home is the home (R-V4-7).
    """
    if ref is None:
        return None
    candidates = credentials_by_provider.get(credential_home(ref.provider_id), [])
    return candidates[0] if len(candidates) == 1 else None


def _seed_cascaded_slot(
    pipeline: PipelineConfig,
    slot: ProviderSlot,
    credentials_by_provider: Mapping[str, list[str]],
    connection: ConnectionContext | None = None,
) -> PipelineConfig:
    ref = cast(ProviderRef | None, getattr(pipeline, slot))
    if ref is None:
        setattr(pipeline, slot, ProviderRef(provider_id=INFERENCE_DEFAULT[slot]))
        return pipeline
    credential = _unambiguous_credential(ref, credentials_by_provider)
    if not _usable(ref, credential, connection):
        setattr(pipeline, slot, ProviderRef(provider_id=INFERENCE_DEFAULT[slot]))
    else:
        ref.credential_id = credential
    return pipeline


def _apply_default_voices(pipeline: PipelineConfig, manifest: PackManifest) -> None:
    """Copy `manifest.default_voice[provider_id]` into the slot's voice field."""
    for slot in ("realtime", "tts"):
        ref = cast(ProviderRef | None, getattr(pipeline, slot))
        if ref is None:
            continue
        voice = manifest.default_voice.get(ref.provider_id)
        spec = _spec_or_none(ref.provider_id)
        if not voice or spec is None:
            continue
        field_name = _voice_field_name(spec)
        if field_name and field_name not in ref.fields:
            ref.fields[field_name] = voice


# ------------------------------------------------------------------------ resolution
def resolve_provider_ref(ref: ProviderRef, secrets: Mapping[str, str]) -> ResolvedProvider:
    """Build the constructor-ready :class:`ResolvedProvider` for one slot.

    Args:
        ref: The stored provider reference.
        secrets: Decrypted secret fields for ``ref.credential_id`` (empty when none).

    Returns:
        A resolved provider whose ``kwargs`` merge spec defaults, admin fields
        and secret fields. **Contains secrets.**

    Raises:
        KeyError: If the provider id is not in the registry.
    """
    spec = get(ref.provider_id)
    kwargs: dict[str, Any] = {}
    for field in spec.fields:
        if field.default is not None:
            _assign_nested(kwargs, field.name, field.default)
    for name, value in ref.fields.items():
        _assign_nested(kwargs, name, value)
    if spec.requires_credential:
        for name, value in secrets.items():
            _assign_nested(kwargs, name, value)
    return ResolvedProvider(
        provider_id=spec.id,
        python_class=spec.python_class,
        model=ref.model or spec.default_model,
        kwargs=kwargs,
    )


def resolve_providers(
    config: AgentConfig, secrets_by_credential: Mapping[str, dict[str, str]]
) -> dict[ProviderSlot, ResolvedProvider]:
    """Resolve every slot the pipeline needs for the worker.

    Only the slots the selected mode uses are resolved: ``realtime`` for realtime
    mode, ``realtime``/``tts`` for half-cascade, ``stt``/``llm``/``tts`` for
    cascaded, plus ``avatar``/``image_gen``/``vad``/``turn_detection``/
    ``noise_cancellation`` when configured. ``workflow_llm`` defaults to the cascaded ``llm`` or, in realtime
    mode, to the LiveKit Inference LLM.

    Args:
        config: The stored agent configuration.
        secrets_by_credential: ``{credential_id: {field: value}}``, already decrypted.

    Returns:
        The ``ResolvedAgentConfig.resolved`` mapping. **Contains secrets.**
    """
    pipeline = config.pipeline
    wanted: list[ProviderSlot]
    match pipeline.mode:
        case "realtime":
            wanted = ["realtime"]
        case "half_cascade":
            wanted = ["realtime", "tts"]
        case _:
            wanted = list(CASCADED_SLOTS)
    wanted += list(OPTIONAL_SLOTS)

    resolved: dict[ProviderSlot, ResolvedProvider] = {}
    for slot in wanted:
        ref = cast(ProviderRef | None, getattr(pipeline, slot))
        if ref is None:
            continue
        secrets = secrets_by_credential.get(ref.credential_id or "", {})
        resolved[slot] = resolve_provider_ref(ref, secrets)

    workflow_ref = pipeline.workflow_llm
    if workflow_ref is None:
        workflow_ref = (
            pipeline.llm
            if pipeline.mode == "cascaded" and pipeline.llm is not None
            else ProviderRef(provider_id=INFERENCE_DEFAULT["llm"])
        )
    secrets = secrets_by_credential.get(workflow_ref.credential_id or "", {})
    resolved["workflow_llm"] = resolve_provider_ref(workflow_ref, secrets)

    if effective_qa(config).enabled:
        # R-V2-11: a flow `qa` node turns QA on even when `qa.enabled` is false.
        # R-V2-6: qa.model -> workflow_llm -> llm (cascaded only) -> Inference default.
        # `workflow_ref` above already implements exactly that fallback (and already
        # skips `pipeline.llm` outside cascaded mode), so reuse it verbatim.
        qa_ref = config.qa.model or workflow_ref
        qa_secrets = secrets_by_credential.get(qa_ref.credential_id or "", {})
        resolved["qa_llm"] = resolve_provider_ref(qa_ref, qa_secrets)
    return resolved


# ------------------------------------------------------------------- tool templating
def substitute_secrets(value: str, secrets: Mapping[str, str]) -> str:
    """Replace every ``{{ secret.NAME }}`` in ``value`` with its secret.

    Unknown names are replaced with an empty string so a half-configured tool
    fails at the vendor rather than leaking the template to the worker.
    """
    return _SECRET_RE.sub(lambda m: secrets.get(m.group(1), ""), value)


def resolve_tool_definition(definition: ToolDefinition, secrets: Mapping[str, str]) -> ToolDefinition:
    """Return a copy of a tool definition with its secret placeholders filled in.

    The worker never sees credential ids or the vault (CONTRACTS §9), so the api
    substitutes ``{{ secret.NAME }}`` in the url, headers and body template and
    clears ``credential_id``. A ``provider`` definition (V5-47) has only headers to fill
    (the Composio key); an origin-tagged MCP definition is filled like any MCP one.
    **The result contains secrets.**
    """
    if isinstance(definition, HttpToolDefinition):
        return definition.model_copy(
            update={
                "url": substitute_secrets(definition.url, secrets),
                "headers": {k: substitute_secrets(v, secrets) for k, v in definition.headers.items()},
                "body_template": (
                    substitute_secrets(definition.body_template, secrets)
                    if definition.body_template is not None
                    else None
                ),
                "credential_id": None,
            }
        )
    if isinstance(definition, ProviderToolDefinition):
        # V5-47 (COMPOSIO.md §4): the Composio key rides the `x-api-key` header, like an app
        # server's; `connection_id` and `subject` stay (the worker sends the subject).
        return definition.model_copy(
            update={
                "headers": {k: substitute_secrets(v, secrets) for k, v in definition.headers.items()},
                "credential_id": None,
            }
        )
    mcp: McpServerDefinition = definition
    # V5-09: `auth` is canonical and `headers`/`credential_id` mirror it, so both are filled
    # and cleared together (a disagreeing pair fails validation on the worker); the
    # `cached_tools` snapshot is for the console and stays out of the session.
    headers = {k: substitute_secrets(v, secrets) for k, v in mcp.headers.items()}
    auth = mcp.auth
    if isinstance(auth, McpHeaderAuth):
        auth = McpHeaderAuth(headers=headers, credential_id=None)
    elif isinstance(auth, McpOAuthAuth):
        auth = auth.model_copy(update={"credential_id": None})
    return mcp.model_copy(
        update={
            "url": substitute_secrets(mcp.url, secrets),
            "headers": headers,
            "credential_id": None,
            "auth": auth,
            "cached_tools": None,
            "cached_at": None,
        }
    )


def render_arguments(template: str, arguments: Mapping[str, Any], *, url_encode: bool) -> str:
    """Substitute ``{{ arg }}`` placeholders with tool-call arguments.

    Args:
        template: A url or body template.
        arguments: The tool-call arguments.
        url_encode: Percent-encode values (use for urls, not for JSON bodies).

    Returns:
        The rendered string; unknown placeholders become empty.
    """

    def _replace(match: re.Match[str]) -> str:
        raw = arguments.get(match.group(1), "")
        text = "" if raw is None else str(raw)
        return quote(text, safe="") if url_encode else text

    return _ARG_RE.sub(_replace, template)


def host_allowed(url: str, *, allowed_hosts: list[str], platform_hosts: list[str] | None = None) -> bool:
    """Whether ``url``'s host passes the per-tool and platform allowlists.

    An empty per-tool allowlist defers to the platform list; an empty platform
    list means the platform imposes no extra restriction. If both lists are
    empty there is nothing to defer to, so this fails closed (no host is
    allowed) — matching the worker's ``_http_safety.check_url_allowed``,
    which unions the two lists and refuses when the union is empty (F-05).
    """
    host = (urlparse(url).hostname or "").lower()
    normalized_allowed = {h.strip().lower() for h in allowed_hosts if h.strip()}
    normalized_platform = {h.strip().lower() for h in (platform_hosts or []) if h.strip()}
    if not host:
        return False
    if not normalized_allowed and not normalized_platform:
        return False
    if normalized_allowed and host not in normalized_allowed:
        return False
    if normalized_platform and host not in normalized_platform:
        return False
    return True
