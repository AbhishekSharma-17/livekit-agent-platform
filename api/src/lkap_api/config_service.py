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
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import quote, urlparse

from lkap_contracts.agent_config import (
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
from lkap_contracts.api_models import Issue, Severity, ValidationResult
from lkap_contracts.connections import ConnectionCapabilities, DeploymentType
from lkap_contracts.packs import PackManifest
from lkap_contracts.providers import (
    ProviderKind,
    ProviderSpec,
    WorkerImage,
    credential_home,
    get,
    vision_support,
)
from lkap_contracts.tools import HttpToolDefinition, McpServerDefinition, ToolDefinition
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.connections.probe import effective_capabilities
from lkap_api.db.models import (
    Credential,
    KnowledgeBase,
    LiveKitConnection,
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

    _validate_modes(config, findings)

    for key in pipeline.turn_handling:
        if key == "turn_detection":
            findings.add(
                "error",
                "pipeline.turn_handling",
                "'turn_detection' is set by the platform and cannot be overridden",
            )
        elif key not in TURN_HANDLING_KEYS:
            findings.add("warning", "pipeline.turn_handling", f"unknown key '{key}' will be ignored")

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
    _validate_model(label, ref, spec, findings)
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


def _validate_modes(config: AgentConfig, findings: _Findings) -> None:
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
        llm_spec = _spec_or_none(pipeline.llm.provider_id)
        if llm_spec is not None and vision_support(pipeline.llm.provider_id, pipeline.llm.model) is False:
            model = pipeline.llm.model or llm_spec.default_model
            findings.add(
                "warning",
                "pipeline.llm",
                f"'{model}' cannot see images; camera/screen share still "
                "reach the UI and pin_frame, but per-turn vision and describe_current_frame "
                "are disabled — pick a model marked 'supports video' (e.g. google/gemini-3.5-flash)",
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


def _validate_model(label: str, ref: ProviderRef, spec: ProviderSpec, findings: _Findings) -> None:
    if ref.model and spec.models and ref.model not in {m.id for m in spec.models}:
        findings.add(
            "warning",
            label,
            f"model '{ref.model}' is not in the suggestion list for '{spec.id}' "
            "(free text is allowed; vendor model lists change often)",
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
                f"field '{name}' must be one of {', '.join(field_spec.options)} (got '{value}')",
            )
    for field_spec in spec.fields:
        if field_spec.required and field_spec.default is None and field_spec.name not in ref.fields:
            findings.add("error", label, f"field '{field_spec.name}' is required for provider '{spec.id}'")


def knowledge_auto_inject_issues(ctx: ValidationContext) -> list[Issue]:
    """Warn that knowledge auto-inject and preemptive generation do not mix.

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
    preemptive = ctx.config.pipeline.turn_handling.get("preemptive_generation")
    explicit = preemptive.get("enabled") if isinstance(preemptive, dict) else None
    if explicit is False:
        return []
    if explicit is True:
        message = (
            "auto-inject changes the conversation on every turn with a knowledge hit, which discards "
            "the preemptive reply this agent keeps enabled (turn_handling.preemptive_generation) — "
            "each such turn pays for two LLM calls; turn auto-inject off and rely on the "
            "search_knowledge tool to keep preemptive generation effective"
        )
    else:
        message = (
            "auto-inject turns off preemptive generation for this agent's sessions, so replies start "
            "only after the caller's turn ends; turn auto-inject off and rely on the search_knowledge "
            "tool to keep preemptive generation"
        )
    return [Issue(path="knowledge.auto_inject", message=message, severity="warning")]


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
    credential_providers = dict(
        (
            await db.execute(
                select(Credential.id, Credential.provider_id).where(Credential.workspace_id == workspace_id)
            )
        )
        .tuples()
        .all()
    )
    tool_names_by_id = dict(
        (await db.execute(select(Tool.id, Tool.name).where(Tool.workspace_id == workspace_id))).tuples().all()
    )
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
        telephony_policy=await _telephony_policy(db, workspace_id),
    )


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
    clears ``credential_id``. **The result contains secrets.**
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
    mcp: McpServerDefinition = definition
    return mcp.model_copy(
        update={
            "url": substitute_secrets(mcp.url, secrets),
            "headers": {k: substitute_secrets(v, secrets) for k, v in mcp.headers.items()},
            "credential_id": None,
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
