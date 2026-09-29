"""Reasoning models and the request parameters a model does not accept (V6-31).

The api resolves each LLM slot's capability view (``ResolvedProvider.capabilities``: declared →
"Test model" → the cached live catalog → the registry). For OpenRouter the catalog item is the
model's own record, whose ``supported_parameters`` lists what its endpoints accept. OpenRouter
routes a request with ``provider.require_parameters`` (the platform's default, so tools never land
on an endpoint that ignores them) only to endpoints that accept **every** parameter sent, and
answers 404 "No endpoints found that can handle the requested parameters" when none does: on
2026-09-29 every turn on ``openai/gpt-6-luna`` failed that way because the registry's
``temperature`` (default 0.7) went out and no Luna endpoint accepts it.

:func:`llm_capability_kwargs` shapes an LLM's constructor kwargs from the view:

* a parameter in :data:`~lkap_contracts.providers.OPTIONAL_REQUEST_PARAMETERS` the model is known
  not to accept is left out (DEBUG log naming it), so ``require_parameters`` can stay on;
* ``reasoning_effort`` follows :func:`~lkap_contracts.providers.reasoning_effort_to_send`: on a
  reasoning model with no effort configured, the lowest effort it lists (every level above it
  adds seconds to a spoken reply); on a model that does not reason, nothing.

With no view (``capabilities is None``, or nothing known about reasoning and parameters) the
kwargs are unchanged: today's behaviour. :func:`filter_request_kwargs` is the same parameter rule
applied to one chat request's keyword arguments (``OpenRouterLLM.chat``), which also covers a
parameter a caller passes per request rather than at construction.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any, Final

from lkap_contracts.providers import (
    OPTIONAL_REQUEST_PARAMETERS,
    REASONING_EFFORT_FIELD,
    ModelCapabilities,
    ProviderSpec,
    accepts_parameter,
    reasoning_effort_to_send,
)

from lkap_agent.logging import get_logger

__all__ = ["filter_request_kwargs", "llm_capability_kwargs", "request_parameters_of"]

logger = get_logger(__name__)

#: ``tool_choice="auto"`` is what an endpoint does anyway when tools are sent, so it may be left
#: out for a model that does not list ``tool_choice``; any other choice is kept.
_DEFAULT_TOOL_CHOICE: Final[str] = "auto"


def request_parameters_of(capabilities: ModelCapabilities | None) -> frozenset[str] | None:
    """The chat request parameters the model accepts, or ``None`` when unknown."""
    if capabilities is None or capabilities.request_parameters is None:
        return None
    return frozenset(capabilities.request_parameters)


def llm_capability_kwargs(
    spec: ProviderSpec, kwargs: Mapping[str, Any], capabilities: ModelCapabilities | None
) -> dict[str, Any]:
    """An LLM's constructor kwargs with the capability view applied (see the module docstring).

    Args:
        spec: The LLM's registry entry.
        kwargs: The resolved constructor kwargs (``model`` already set).
        capabilities: ``ResolvedProvider.capabilities`` for the slot.

    Returns:
        A new dict: unsupported optional parameters removed, ``reasoning_effort`` set, changed or
        removed as the rule says. An empty stored effort is always removed.
    """
    converted = dict(kwargs)
    requested = converted.pop(REASONING_EFFORT_FIELD, None)
    wanted = requested if isinstance(requested, str) and requested.strip() else None
    dropped = sorted(
        name
        for name in OPTIONAL_REQUEST_PARAMETERS
        if name in converted and accepts_parameter(capabilities, name) is False
    )
    for name in dropped:
        converted.pop(name)
    effort = reasoning_effort_to_send(capabilities, wanted)
    if effort is not None:
        converted[REASONING_EFFORT_FIELD] = effort
    if dropped or effort != wanted:
        logger.debug(
            "llm options shaped by the model's capabilities",
            provider_id=spec.id,
            model=converted.get("model"),
            dropped=dropped,
            reasoning_effort=effort,
            configured_reasoning_effort=wanted,
        )
    return converted


def filter_request_kwargs(
    extra: Mapping[str, Any], accepted: Collection[str] | None, *, model: str | None = None
) -> dict[str, Any]:
    """One chat request's keyword arguments without the optional parameters the model refuses.

    Args:
        extra: The keyword arguments the plugin passes to ``chat.completions.create``.
        accepted: The parameters the model accepts; ``None`` = unknown, nothing is removed.
        model: For the log line only.

    Returns:
        A new dict (``extra`` itself when nothing is known).
    """
    if accepted is None:
        return dict(extra)
    dropped = sorted(name for name in extra if name in OPTIONAL_REQUEST_PARAMETERS and name not in accepted)
    if "tool_choice" not in accepted and extra.get("tool_choice") == _DEFAULT_TOOL_CHOICE:
        dropped.append("tool_choice")
    if not dropped:
        return dict(extra)
    logger.debug("request parameters the model does not accept were left out", model=model, dropped=dropped)
    return {key: value for key, value in extra.items() if key not in dropped}
