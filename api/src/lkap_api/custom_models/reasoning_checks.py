"""Validator warnings for reasoning models and parameters a model does not accept (V6-31).

The worker shapes each LLM request from the model's capability view (declared → "Test model" →
the cached live catalog → the registry, :func:`lkap_api.custom_models.capabilities.resolve_capabilities`):
it leaves out a parameter the model refuses and, on a reasoning model with no effort set, sends the
lowest effort the model lists (``lkap_contracts.providers.reasoning_effort_to_send``). Nothing here
is an error — a save never fails on it; each warning says what the agent will actually do:

* a stored option the model does not accept (``temperature`` on GPT-6 Luna) is left out;
* a reasoning effort on a model that does not reason is ignored; one the model does not offer is
  replaced by the next level it offers;
* a reasoning model thinking at ``medium`` or more on a voice pipeline adds several seconds to
  every reply (measured 2026-09-29: 8.2 s against 3.6 s from end of speech to first audio), and a
  model that always reasons with no effort setting gets a tip.

Only the slots the worker shapes are checked (``llm`` and ``workflow_llm``); a model id that fails
the id rule is never quoted (R-V4-21) and gets no finding.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from lkap_contracts.agent_config import ProviderRef, ProviderSlot
from lkap_contracts.common import Issue
from lkap_contracts.providers import (
    OPTIONAL_REQUEST_PARAMETERS,
    REASONING_EFFORT_FIELD,
    SLOW_VOICE_EFFORTS,
    ModelCapabilities,
    ProviderSpec,
    accepts_parameter,
    get,
    lowest_reasoning_effort,
    reasoning_effort_to_send,
    validate_model_id,
)

from lkap_api.custom_models.capabilities import resolve_capabilities

if TYPE_CHECKING:
    from lkap_api.config_service import ValidationContext

__all__ = [
    "REASONING_SLOTS",
    "SLOW_VOICE_EFFORT_MESSAGE",
    "reasoning_issues",
]

#: The slots whose requests the worker shapes from the capability view (``CAPABILITY_SLOTS`` less
#: ``realtime``, which has no chat request).
REASONING_SLOTS: Final[tuple[ProviderSlot, ...]] = ("llm", "workflow_llm")

#: The voice-latency warning; ``{name}``, ``{effort}`` and ``{lowest}`` are filled in.
SLOW_VOICE_EFFORT_MESSAGE: Final[str] = (
    "{name} thinks at '{effort}' effort before every answer, which adds several seconds to each "
    "reply on a voice call; set Reasoning effort to '{lowest}' (or leave it empty) for a faster "
    "conversation"
)

#: The tip for a model that always reasons and takes no effort setting.
ALWAYS_REASONS_TIP: Final[str] = (
    "Tip: {name} always thinks before it answers and has no effort setting, which can add seconds "
    "to each reply on a voice call; a model without reasoning, or one whose effort can be lowered, "
    "answers faster"
)


def _stored(ref: ProviderRef, name: str) -> object | None:
    value = ref.fields.get(name)
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return value


def _field_label(spec: ProviderSpec, name: str) -> str:
    return next((field.label for field in spec.fields if field.name == name), name)


def _slot_issues(
    ctx: ValidationContext, slot: ProviderSlot, ref: ProviderRef, spec: ProviderSpec, model: str
) -> list[Issue]:
    caps: ModelCapabilities = resolve_capabilities(
        spec, model, ctx.record_for(spec, model), ctx.catalog_item(spec.id, model)
    )
    listed = model in {m.id for m in spec.models} or model == spec.default_model
    name = f"'{model}'" if listed or ctx.catalog_item(spec.id, model) is not None else "this model"
    base = f"pipeline.{slot}"
    issues: list[Issue] = []

    for parameter in sorted(OPTIONAL_REQUEST_PARAMETERS - {REASONING_EFFORT_FIELD}):
        if _stored(ref, parameter) is not None and accepts_parameter(caps, parameter) is False:
            issues.append(
                Issue(
                    path=f"{base}.fields.{parameter}",
                    message=f"{name} does not accept '{_field_label(spec, parameter)}', so the agent "
                    "leaves it out of each request",
                    severity="warning",
                )
            )

    stored_effort = _stored(ref, REASONING_EFFORT_FIELD)
    requested = stored_effort if isinstance(stored_effort, str) else None
    effective = reasoning_effort_to_send(caps, requested)
    effort_path = f"{base}.fields.{REASONING_EFFORT_FIELD}"
    if requested is not None and effective != requested:
        if effective is None:
            reason = "does not reason" if caps.reasoning is False else "has no reasoning effort setting"
            message = f"{name} {reason}, so Reasoning effort is ignored"
        else:
            message = f"{name} does not offer '{requested}' reasoning effort; '{effective}' is used instead"
        issues.append(Issue(path=effort_path, message=message, severity="warning"))

    if slot != "llm" or ctx.config.pipeline.mode != "cascaded" or caps.reasoning is False:
        return issues
    if isinstance(effective, str) and effective in SLOW_VOICE_EFFORTS:
        lowest = lowest_reasoning_effort(caps.reasoning_efforts) or "low"
        issues.append(
            Issue(
                path=effort_path if requested is not None else base,
                message=SLOW_VOICE_EFFORT_MESSAGE.format(name=name, effort=effective, lowest=lowest),
                severity="warning",
            )
        )
    elif caps.reasoning is True and caps.reasoning_efforts == [] and effective is None:
        issues.append(Issue(path=base, message=ALWAYS_REASONS_TIP.format(name=name), severity="warning"))
    return issues


def reasoning_issues(ctx: ValidationContext) -> list[Issue]:
    """V6-31 warnings for the ``llm`` and ``workflow_llm`` slots (see the module docstring).

    Args:
        ctx: The validation context (its cached catalog items and model records feed the view).

    Returns:
        Warnings only; empty when nothing about the models is known.
    """
    issues: list[Issue] = []
    pipeline = ctx.config.pipeline
    for slot in REASONING_SLOTS:
        ref = getattr(pipeline, slot, None)
        if not isinstance(ref, ProviderRef):
            continue
        try:
            spec = get(ref.provider_id)
        except KeyError:
            continue
        model = ref.model or spec.default_model
        if spec.kind != "llm" or not model or validate_model_id(model) is not None:
            continue
        issues.extend(_slot_issues(ctx, slot, ref, spec, model))
    return issues
