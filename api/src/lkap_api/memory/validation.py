"""Save-time checks of ``AgentConfig.memory`` (V5-40), registered into ``config_service.VALIDATORS``.

Every finding is a warning: the setting is kept and memory simply does
nothing (or stores nothing) until the named condition holds.

* Memory on while the server has no memory backend (the ``lkap-api[memory]``
  extra is not installed).
* Memory on, not verbatim, and neither ``pipeline.llm`` nor
  ``pipeline.workflow_llm`` is an OpenAI or OpenRouter model with a key: nothing
  can pick the facts out after the call.
"""

from __future__ import annotations

import importlib.util

from lkap_contracts.api_models import Issue

from lkap_api.config_service import ValidationContext, register_validator
from lkap_api.memory.service import EXTRACTION_PROVIDERS

__all__ = ["NO_BACKEND_MESSAGE", "NO_MODEL_MESSAGE", "memory_issues"]

NO_BACKEND_MESSAGE = (
    "Memory is not installed on this server, so nothing is remembered yet (ask your administrator "
    "to install the memory add-on)"
)
NO_MODEL_MESSAGE = (
    "Nothing can pick out what to remember after the call. Use an OpenAI or OpenRouter language "
    "model with a key, or turn on 'store what the caller said as it was'"
)


def backend_installed() -> bool:
    """Whether the Mem0 backend can be imported in this process."""
    return importlib.util.find_spec("mem0") is not None


def memory_issues(ctx: ValidationContext) -> list[Issue]:
    """The V5-40 memory findings for ``ctx.config`` (registered validator)."""
    memory = ctx.config.memory
    if not memory.enabled:
        return []
    issues: list[Issue] = []
    if not backend_installed():
        issues.append(Issue(path="memory.enabled", message=NO_BACKEND_MESSAGE, severity="warning"))
    if not memory.verbatim:
        pipeline = ctx.config.pipeline
        usable = any(
            ref is not None
            and ref.provider_id in EXTRACTION_PROVIDERS
            and ref.credential_id is not None
            and ref.credential_id in ctx.credential_providers
            for ref in (pipeline.llm, pipeline.workflow_llm)
        )
        if not usable:
            issues.append(Issue(path="memory.verbatim", message=NO_MODEL_MESSAGE, severity="warning"))
    return issues


register_validator(memory_issues)
