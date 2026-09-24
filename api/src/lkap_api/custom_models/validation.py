"""Custom-model validation (docs/v4/CUSTOM-MODELS.md D-V4-23, D-V4-24, D-V4-26, R-V4-21, R-V4-26).

Registered into :data:`lkap_api.config_service.VALIDATORS` on import (the
``catalogs/validation.py`` pattern); ``lkap_api.routers.providers`` imports it.

**Errors** (the only two the rule allows): a model id, or the value of an
id-like field (``type="model"``/``"catalog"`` or a name in
``ID_LIKE_FIELD_NAMES``), that looks like an API key or breaks the syntax
rule. No message contains the value.

**Warnings**: the last "Test model" run with the slot's current key failed
(with its scrubbed reason); the vendor deprecated the model or it is gone from
the live catalog (``catalog_missing_since``); the resolved capabilities say a
cascaded LLM cannot see images while the agent wants video. The "unknown and
untested" warning is ``config_service._validate_model``'s own, so it fires once.
"""

from __future__ import annotations

from lkap_contracts.agent_config import ProviderRef, ProviderSlot
from lkap_contracts.api_models import Issue
from lkap_contracts.providers import ProviderSpec, get, id_like_field, validate_model_id, vision_support

from lkap_api.config_service import SLOT_KIND, ValidationContext, register_validator
from lkap_api.custom_models.capabilities import resolve_capabilities
from lkap_api.custom_models.records import current_test_result

#: Longest vendor reason quoted in a warning.
REASON_MAX = 200


def _path(slot: ProviderSlot) -> str:
    return "qa.model" if slot == "qa_llm" else f"pipeline.{slot}"


def _spec(ref: ProviderRef, slot: ProviderSlot) -> ProviderSpec | None:
    try:
        spec = get(ref.provider_id)
    except KeyError:
        return None
    return spec if spec.kind == SLOT_KIND[slot] else None


def id_rule_issues(path: str, ref: ProviderRef, spec: ProviderSpec) -> list[Issue]:
    """The secret/syntax errors of one slot's model id and id-like fields (never echoing a value)."""
    issues: list[Issue] = []
    if ref.model:
        reason = validate_model_id(ref.model)
        if reason is not None:
            issues.append(Issue(path=path, message=f"the model id {reason}", severity="error"))
    by_name = {field.name: field for field in spec.fields}
    for name, value in ref.fields.items():
        if not isinstance(value, str) or not value:
            continue
        field_spec = by_name.get(name)
        typed = field_spec is not None and field_spec.type in ("model", "catalog")
        if not (typed or id_like_field(name)):
            continue
        reason = validate_model_id(value)
        if reason is not None:
            issues.append(Issue(path=path, message=f"field '{name}': the value {reason}", severity="error"))
    return issues


def _record_issues(ctx: ValidationContext, path: str, ref: ProviderRef, spec: ProviderSpec) -> list[Issue]:
    model = ref.model or spec.default_model
    if not model or validate_model_id(model) is not None:
        return []
    record = ctx.record_for(spec, model)
    if record is None:
        return []
    issues: list[Issue] = []
    if current_test_result(record, fingerprint=ctx.fingerprint_for(ref)) is False:
        reason = (record.last_test_message or "no reason recorded")[:REASON_MAX]
        issues.append(
            Issue(
                path=path,
                message=f"the last Test model run of this model id failed with this key: {reason}",
                severity="warning",
            )
        )
    if record.catalog_missing_since is not None:
        when = record.catalog_missing_since.date().isoformat()
        if ctx.catalog_item(spec.id, model) is not None:
            message = f"this model id is deprecated by {spec.vendor} (on {when})"
        else:
            seen = record.catalog_seen_at.date().isoformat() if record.catalog_seen_at else when
            message = f"this model id no longer appears in {spec.vendor}'s catalog (last seen {seen})"
        issues.append(Issue(path=path, message=message, severity="warning"))
    return issues


def _vision_issue(ctx: ValidationContext, ref: ProviderRef, spec: ProviderSpec) -> Issue | None:
    config = ctx.config
    wants_video = config.capabilities.camera or config.capabilities.screen_share
    if config.pipeline.mode != "cascaded" or not wants_video:
        return None
    model = ref.model or spec.default_model
    if model is None or validate_model_id(model) is not None:
        return None
    if vision_support(spec.id, model) is False:
        return None  # `config_service._validate_modes` already says so for a listed model
    caps = resolve_capabilities(spec, model, ctx.record_for(spec, model), ctx.catalog_item(spec.id, model))
    if caps.vision is not False:
        return None
    return Issue(
        path="pipeline.llm",
        message=(
            f"this model cannot see images (per its {caps.source or 'record'} capabilities); camera/screen "
            "share still reach the UI and pin_frame, but per-turn vision and describe_current_frame "
            "need a vision model"
        ),
        severity="warning",
    )


def custom_model_issues(ctx: ValidationContext) -> list[Issue]:
    """Every custom-model error and warning of a config (see the module docstring).

    Public so a test can register it explicitly instead of relying on import order.
    """
    issues: list[Issue] = []
    for slot, ref in ctx.slots():
        spec = _spec(ref, slot)
        if spec is None:
            continue
        path = _path(slot)
        issues.extend(id_rule_issues(path, ref, spec))
        issues.extend(_record_issues(ctx, path, ref, spec))
        if slot == "llm":
            vision = _vision_issue(ctx, ref, spec)
            if vision is not None:
                issues.append(vision)
    return issues


register_validator(custom_model_issues)

__all__ = ["custom_model_issues", "id_rule_issues"]
