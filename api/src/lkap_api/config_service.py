"""Agent configuration services: validation, pack seeding and resolution.

Three jobs, all pure functions over :mod:`lkap_contracts` models so they can be
unit-tested without a database:

* :func:`validate_agent_config` — checks an ``AgentConfig`` against the provider
  registry and the credentials that actually exist (CONTRACTS §6).
* :func:`seed_config_from_manifest` — turns a ``PackManifest`` into a runnable
  ``AgentConfig``, substituting LiveKit Inference for providers whose credential
  is missing or ambiguous (CONTRACTS §8, "Seeding rule").
* :func:`resolve_providers` / :func:`resolve_tool_definition` — merge decrypted
  secrets into constructor kwargs and tool templates for the worker's
  ``ResolvedAgentConfig``. **Their output contains secrets and must never be
  logged or returned to an admin/browser caller.**
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, cast
from urllib.parse import quote, urlparse

from lkap_contracts.agent_config import (
    AgentConfig,
    PipelineConfig,
    ProviderRef,
    ProviderSlot,
    ResolvedProvider,
    ToolsConfig,
    VoiceConfig,
)
from lkap_contracts.api_models import ValidationResult
from lkap_contracts.packs import PackManifest
from lkap_contracts.providers import ProviderKind, ProviderSpec, get, vision_support
from lkap_contracts.tools import HttpToolDefinition, McpServerDefinition, ToolDefinition

#: Which registry `kind` may fill each pipeline slot. `workflow_llm` takes an llm.
SLOT_KIND: dict[ProviderSlot, ProviderKind] = {
    "realtime": "realtime",
    "stt": "stt",
    "llm": "llm",
    "tts": "tts",
    "avatar": "avatar",
    "image_gen": "image_gen",
    "workflow_llm": "llm",
}

#: The credential-free LiveKit Inference provider for each cascaded slot.
INFERENCE_DEFAULT: dict[str, str] = {
    "stt": "livekit-inference-stt",
    "llm": "livekit-inference-llm",
    "tts": "livekit-inference-tts",
}

#: Slots that make up a cascaded pipeline, in construction order.
CASCADED_SLOTS: tuple[ProviderSlot, ...] = ("stt", "llm", "tts")

#: Keys accepted by `TurnHandlingOptions` (livekit-agents 1.8.2).
TURN_HANDLING_KEYS: frozenset[str] = frozenset(
    {"endpointing", "interruption", "preemptive_generation", "user_turn_limit"}
)

#: Field names that carry a voice selection, for `PackManifest.default_voice`.
VOICE_FIELD_NAMES: tuple[str, ...] = ("voice", "voice_id")

_SECRET_RE = re.compile(r"\{\{\s*secret\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_ARG_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


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
    for field in spec.fields:
        if field.name in VOICE_FIELD_NAMES:
            return field.name
    return None


# ------------------------------------------------------------------------ validation
def validate_agent_config(
    config: AgentConfig,
    *,
    credential_providers: Mapping[str, str],
    known_tool_ids: set[str] | None = None,
    known_kb_ids: set[str] | None = None,
) -> ValidationResult:
    """Validate an ``AgentConfig`` against the registry and stored credentials.

    Args:
        config: The configuration an admin is trying to save.
        credential_providers: ``{credential_id: provider_id}`` for every stored credential.
        known_tool_ids: Existing tool row ids; skipped when ``None``.
        known_kb_ids: Existing knowledge-base ids; skipped when ``None``.

    Returns:
        A :class:`ValidationResult`; ``ok`` is false when ``errors`` is non-empty.
    """
    errors: list[str] = []
    warnings: list[str] = []
    pipeline = config.pipeline

    if pipeline.mode == "realtime":
        if pipeline.realtime is None:
            errors.append("pipeline.realtime is required when mode is 'realtime'")
    else:
        for slot in CASCADED_SLOTS:
            if getattr(pipeline, slot) is None:
                errors.append(f"pipeline.{slot} is required when mode is 'cascaded'")

    for slot, ref in _slots_in_use(pipeline):
        label = f"pipeline.{slot}"
        spec = _spec_or_none(ref.provider_id)
        if spec is None:
            errors.append(f"{label}: unknown provider '{ref.provider_id}'")
            continue
        if spec.kind != SLOT_KIND[slot]:
            errors.append(
                f"{label}: provider '{spec.id}' is a {spec.kind} provider, expected {SLOT_KIND[slot]}"
            )
            continue
        if spec.status != "mvp":
            errors.append(f"{label}: provider '{spec.id}' is not available yet (status={spec.status})")

        _validate_credential(label, ref, spec, credential_providers, errors, warnings)
        _validate_model(label, ref, spec, warnings)
        _validate_fields(label, ref, spec, errors, warnings)

    if pipeline.mode == "realtime" and pipeline.realtime is not None:
        spec = _spec_or_none(pipeline.realtime.provider_id)
        if spec is not None and not spec.capabilities.video_input:
            if config.capabilities.camera or config.capabilities.screen_share:
                warnings.append(
                    f"pipeline.realtime: '{spec.id}' cannot see video frames; camera/screen share "
                    "still reach the UI and the pin/describe tools, but not the model"
                )

    if pipeline.mode == "cascaded" and pipeline.llm is not None:
        if config.capabilities.camera or config.capabilities.screen_share:
            llm_spec = _spec_or_none(pipeline.llm.provider_id)
            if llm_spec is not None and vision_support(pipeline.llm.provider_id, pipeline.llm.model) is False:
                model = pipeline.llm.model or llm_spec.default_model
                warnings.append(
                    f"pipeline.llm: '{model}' cannot see images; camera/screen share still "
                    "reach the UI and pin_frame, but per-turn vision and describe_current_frame "
                    "are disabled — pick a model marked 'supports video' (e.g. google/gemini-3.5-flash)"
                )

    for key in config.pipeline.turn_handling:
        if key == "turn_detection":
            errors.append(
                "pipeline.turn_handling: 'turn_detection' is set by the platform and cannot be overridden"
            )
        elif key not in TURN_HANDLING_KEYS:
            warnings.append(f"pipeline.turn_handling: unknown key '{key}' will be ignored")

    if known_tool_ids is not None:
        for tool_id in config.tools.tool_ids:
            if tool_id not in known_tool_ids:
                errors.append(f"tools.tool_ids: unknown tool '{tool_id}'")
    if known_kb_ids is not None:
        for kb_id in config.knowledge.kb_ids:
            if kb_id not in known_kb_ids:
                errors.append(f"knowledge.kb_ids: unknown knowledge base '{kb_id}'")

    if not config.instructions.strip():
        warnings.append("instructions are empty; the agent will rely on the model's defaults")

    return ValidationResult(ok=not errors, errors=errors, warnings=warnings)


def _validate_credential(
    label: str,
    ref: ProviderRef,
    spec: ProviderSpec,
    credential_providers: Mapping[str, str],
    errors: list[str],
    warnings: list[str],
) -> None:
    if spec.requires_credential:
        if not ref.credential_id:
            errors.append(f"{label}: provider '{spec.id}' requires a credential")
            return
    elif ref.credential_id:
        warnings.append(f"{label}: provider '{spec.id}' needs no credential; the reference is ignored")
        return
    if ref.credential_id:
        owner = credential_providers.get(ref.credential_id)
        if owner is None:
            errors.append(f"{label}: unknown credential '{ref.credential_id}'")
        elif owner != spec.id:
            errors.append(
                f"{label}: credential '{ref.credential_id}' belongs to provider '{owner}', not '{spec.id}'"
            )


def _validate_model(label: str, ref: ProviderRef, spec: ProviderSpec, warnings: list[str]) -> None:
    if ref.model and spec.models and ref.model not in {m.id for m in spec.models}:
        warnings.append(
            f"{label}: model '{ref.model}' is not in the suggestion list for '{spec.id}' "
            "(free text is allowed; vendor model lists change often)"
        )


def _validate_fields(
    label: str,
    ref: ProviderRef,
    spec: ProviderSpec,
    errors: list[str],
    warnings: list[str],
) -> None:
    by_name = {f.name: f for f in spec.fields}
    for name, value in ref.fields.items():
        field = by_name.get(name)
        if field is None:
            warnings.append(f"{label}: unknown field '{name}' for provider '{spec.id}'")
            continue
        if field.type == "enum" and field.options and str(value) not in field.options:
            errors.append(
                f"{label}: field '{name}' must be one of {', '.join(field.options)} (got '{value}')"
            )
    for field in spec.fields:
        if field.required and field.default is None and field.name not in ref.fields:
            errors.append(f"{label}: field '{field.name}' is required for provider '{spec.id}'")


# --------------------------------------------------------------------------- seeding
def seed_config_from_manifest(
    manifest: PackManifest,
    *,
    credentials_by_provider: Mapping[str, list[str]],
) -> AgentConfig:
    """Build a runnable ``AgentConfig`` from a pack manifest.

    Applies the CONTRACTS §8 seeding rule: a slot whose provider needs a
    credential keeps it when **exactly one** credential exists for that
    provider; otherwise the slot falls back to the credential-free LiveKit
    Inference provider of the same kind (``avatar``/``image_gen`` are dropped,
    a ``realtime`` slot switches the pipeline to cascaded). The result always
    passes :func:`validate_agent_config`.

    Args:
        manifest: The pack's manifest.
        credentials_by_provider: ``{provider_id: [credential_id, ...]}``.

    Returns:
        A complete configuration ready to store.
    """
    pipeline = manifest.recommended_pipeline.model_copy(deep=True)

    if pipeline.mode == "realtime":
        credential = _unambiguous_credential(pipeline.realtime, credentials_by_provider)
        if pipeline.realtime is None or (_needs_credential(pipeline.realtime) and credential is None):
            pipeline.mode = "cascaded"
            pipeline.realtime = None
        elif pipeline.realtime is not None:
            pipeline.realtime.credential_id = credential

    if pipeline.mode == "cascaded":
        for slot in CASCADED_SLOTS:
            pipeline = _seed_cascaded_slot(pipeline, slot, credentials_by_provider)
    else:
        pipeline.stt = pipeline.llm = pipeline.tts = None

    for slot in ("avatar", "image_gen"):
        ref = cast(ProviderRef | None, getattr(pipeline, slot))
        if ref is None:
            continue
        credential = _unambiguous_credential(ref, credentials_by_provider)
        if _needs_credential(ref) and credential is None:
            setattr(pipeline, slot, None)
        else:
            ref.credential_id = credential

    if pipeline.workflow_llm is not None:
        credential = _unambiguous_credential(pipeline.workflow_llm, credentials_by_provider)
        if _needs_credential(pipeline.workflow_llm) and credential is None:
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


def _needs_credential(ref: ProviderRef) -> bool:
    spec = _spec_or_none(ref.provider_id)
    return spec is None or spec.requires_credential


def _unambiguous_credential(
    ref: ProviderRef | None, credentials_by_provider: Mapping[str, list[str]]
) -> str | None:
    """Return the single credential for the ref's provider, or ``None``."""
    if ref is None:
        return None
    candidates = credentials_by_provider.get(ref.provider_id, [])
    return candidates[0] if len(candidates) == 1 else None


def _seed_cascaded_slot(
    pipeline: PipelineConfig,
    slot: ProviderSlot,
    credentials_by_provider: Mapping[str, list[str]],
) -> PipelineConfig:
    ref = cast(ProviderRef | None, getattr(pipeline, slot))
    if ref is None:
        setattr(pipeline, slot, ProviderRef(provider_id=INFERENCE_DEFAULT[slot]))
        return pipeline
    credential = _unambiguous_credential(ref, credentials_by_provider)
    if _needs_credential(ref) and credential is None:
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
    mode, ``stt``/``llm``/``tts`` for cascaded, plus ``avatar``/``image_gen`` when
    configured. ``workflow_llm`` defaults to the cascaded ``llm`` or, in realtime
    mode, to the LiveKit Inference LLM.

    Args:
        config: The stored agent configuration.
        secrets_by_credential: ``{credential_id: {field: value}}``, already decrypted.

    Returns:
        The ``ResolvedAgentConfig.resolved`` mapping. **Contains secrets.**
    """
    pipeline = config.pipeline
    wanted: list[ProviderSlot] = ["realtime"] if pipeline.mode == "realtime" else list(CASCADED_SLOTS)
    wanted += ["avatar", "image_gen"]

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
