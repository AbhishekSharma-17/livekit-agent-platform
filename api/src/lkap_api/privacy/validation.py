"""Save-time checks of ``AgentConfig.privacy`` and ``qa.fields`` (V5-30).

:func:`privacy_issues` is registered into ``config_service.VALIDATORS`` (the
``telephony``/``catalogs`` pattern); importing :mod:`lkap_api.privacy`
registers it. Every finding is a warning: the setting is kept and simply has
no effect until the named condition holds.

* ``privacy.stt_redact`` on a speech-to-text provider whose registry entry has
  no ``capabilities.redaction`` (or no separate speech-to-text at all, a
  realtime pipeline): the provider ignores it.
* ``privacy.scrub_model`` with ``storage_tier="full"`` (nothing runs), a
  provider the api cannot call, or no key.
* ``qa.fields`` while QA is off: nothing fills them.
"""

from __future__ import annotations

from lkap_contracts.agent_config import effective_qa
from lkap_contracts.api_models import Issue
from lkap_contracts.providers import get

from lkap_api.config_service import ValidationContext, register_validator
from lkap_api.privacy.llm import SCRUB_MODEL_PROVIDERS

__all__ = [
    "FIELDS_WITHOUT_QA_MESSAGE",
    "SCRUB_MODEL_UNUSED_MESSAGE",
    "privacy_issues",
]

FIELDS_WITHOUT_QA_MESSAGE = (
    "Post-call fields are filled by the call review, which is off: turn on QA to fill them"
)
SCRUB_MODEL_UNUSED_MESSAGE = (
    "The cleanup model only runs when transcripts are not kept in full: choose 'redacted' or 'basic'"
)


def _stt_issues(ctx: ValidationContext) -> list[Issue]:
    privacy = ctx.config.privacy
    if not privacy.stt_redact:
        return []
    ref = ctx.config.pipeline.stt
    if ref is None:
        return [
            Issue(
                path="privacy.stt_redact",
                message="This pipeline has no separate speech-to-text provider, so nothing can mask "
                "while transcribing; use the storage tier to clean transcripts after the call",
                severity="warning",
            )
        ]
    try:
        spec = get(ref.provider_id)
    except KeyError:
        return []  # the slot check reports the unknown provider
    unsupported = [value for value in privacy.stt_redact if value not in spec.capabilities.redaction]
    if not unsupported:
        return []
    return [
        Issue(
            path="privacy.stt_redact",
            message=f"{spec.label} cannot mask {', '.join(unsupported)} while transcribing; it is "
            "ignored (Deepgram can)",
            severity="warning",
        )
    ]


def _scrub_model_issues(ctx: ValidationContext) -> list[Issue]:
    privacy = ctx.config.privacy
    ref = privacy.scrub_model
    if ref is None:
        return []
    path = "privacy.scrub_model"
    if privacy.storage_tier == "full":
        return [Issue(path=path, message=SCRUB_MODEL_UNUSED_MESSAGE, severity="warning")]
    try:
        spec = get(ref.provider_id)
    except KeyError:
        return [Issue(path=path, message=f"unknown provider '{ref.provider_id}'", severity="error")]
    if spec.kind != "llm":
        return [Issue(path=path, message=f"'{spec.label}' is not a language model", severity="error")]
    if ref.provider_id not in SCRUB_MODEL_PROVIDERS:
        return [
            Issue(
                path=path,
                message=f"{spec.label} cannot be called after the call; only OpenAI or OpenRouter with "
                "a key can. Transcripts are still cleaned of emails, card and long numbers",
                severity="warning",
            )
        ]
    if not ref.credential_id:
        return [
            Issue(path=path, message=f"{spec.label} needs a key to clean transcripts", severity="warning")
        ]
    owner = ctx.credential_providers.get(ref.credential_id)
    if owner is None:
        return [Issue(path=path, message="the selected key no longer exists", severity="error")]
    return []


def privacy_issues(ctx: ValidationContext) -> list[Issue]:
    """The V5-30 privacy and post-call field findings for ``ctx.config`` (registered validator)."""
    issues = [*_stt_issues(ctx), *_scrub_model_issues(ctx)]
    if ctx.config.qa.fields and not effective_qa(ctx.config).enabled:
        issues.append(Issue(path="qa.fields", message=FIELDS_WITHOUT_QA_MESSAGE, severity="warning"))
    return issues


register_validator(privacy_issues)
