"""Per-node LLM/TTS overrides for flow agents (CONTRACTS-V2 §4.5, cascaded only).

`AgentNode.providers` holds `ProviderRef`s, but the worker only ever receives
**resolved** providers from the api (`ResolvedAgentConfig.resolved`, keyed by
slot) and never resolves credentials itself (R-V2-6). Until the api resolves
node overrides (see the V2-15 report / `docs/v2/_asks.md`), a node override is
built only when that needs no new secret:

1. **Same provider and credential as a slot the api already resolved**
   (`llm`/`workflow_llm`/`qa_llm` for an LLM override, `tts` for TTS): the
   resolved kwargs (secrets included, already in this process) are reused with
   the node's `model` and `fields` applied on top — e.g. a cheaper model on the
   same OpenAI key.
2. **A provider that needs no credential** (`requires_credential=False`, e.g.
   LiveKit Inference): resolved from the registry exactly like the api's
   `resolve_provider_ref` does (spec defaults, then the ref's fields).
3. Anything else: a warning, and the node keeps the session's provider.

Realtime and half-cascade flows share one model (ARCHITECTURE-V2 D-V2-13), so
overrides there are ignored with a warning.
"""

from __future__ import annotations

from typing import Any, Final, Literal

from lkap_contracts import providers as provider_registry
from lkap_contracts.agent_config import PipelineMode, ResolvedAgentConfig, ResolvedProvider
from lkap_contracts.common import ProviderRef
from lkap_contracts.flow import AgentNode

from lkap_agent.logging import get_logger
from lkap_agent.providers.factory import ProviderFactory

__all__ = ["NodeProviders", "OverrideSlot", "resolve_override"]

logger = get_logger(__name__)

OverrideSlot = Literal["llm", "tts"]

#: Already-resolved slots whose secrets a node override may reuse, per override slot.
_REUSABLE_SLOTS: Final[dict[OverrideSlot, tuple[str, ...]]] = {
    "llm": ("llm", "workflow_llm", "qa_llm"),
    "tts": ("tts",),
}


def _assign_nested(target: dict[str, Any], dotted: str, value: Any) -> None:
    """`a.b = v` → `{"a": {"b": v}}` (mirrors the api's `config_service._assign_nested`)."""
    parts = dotted.split(".")
    node = target
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


def _credential_matches(ref: ProviderRef, pipeline_ref: ProviderRef | None) -> bool:
    if pipeline_ref is None or pipeline_ref.provider_id != ref.provider_id:
        return False
    return ref.credential_id is None or ref.credential_id == pipeline_ref.credential_id


def resolve_override(
    slot: OverrideSlot, ref: ProviderRef, resolved: ResolvedAgentConfig
) -> tuple[ResolvedProvider | None, str | None]:
    """Resolve one node override without any new secret path.

    Returns:
        `(provider, None)` when buildable, else `(None, reason)`.
    """
    try:
        spec = provider_registry.get(ref.provider_id)
    except KeyError:
        return None, f"unknown provider {ref.provider_id!r}"
    if spec.kind != slot:
        return None, f"{ref.provider_id!r} is a {spec.kind!r} provider, not {slot!r}"

    pipeline = resolved.config.pipeline
    for base_slot in _REUSABLE_SLOTS[slot]:
        base = resolved.resolved.get(base_slot)  # type: ignore[call-overload]
        if base is None or base.provider_id != ref.provider_id:
            continue
        base_ref: ProviderRef | None = getattr(pipeline, base_slot, None)
        if base_slot == "qa_llm":
            base_ref = resolved.config.qa.model
        if base_ref is not None and not _credential_matches(ref, base_ref):
            continue
        kwargs = dict(base.kwargs)
        for name, value in ref.fields.items():
            _assign_nested(kwargs, name, value)
        return (
            ResolvedProvider(
                provider_id=base.provider_id,
                python_class=base.python_class,
                model=ref.model or base.model,
                kwargs=kwargs,
            ),
            None,
        )

    if spec.requires_credential:
        return None, (
            f"{ref.provider_id!r} needs a credential the api has not resolved for this session "
            "(node overrides are resolved by the worker only for credential-less providers or "
            "the session's own provider)"
        )
    kwargs = {}
    for field in spec.fields:
        if field.default is not None:
            _assign_nested(kwargs, field.name, field.default)
    for name, value in ref.fields.items():
        _assign_nested(kwargs, name, value)
    return (
        ResolvedProvider(
            provider_id=spec.id,
            python_class=spec.python_class,
            model=ref.model or spec.default_model,
            kwargs=kwargs,
        ),
        None,
    )


class NodeProviders:
    """Builds and caches each node's LLM/TTS overrides for one session."""

    def __init__(
        self, *, factory: ProviderFactory, resolved: ResolvedAgentConfig, mode: PipelineMode
    ) -> None:
        self._factory = factory
        self._resolved = resolved
        self._mode = mode
        self._cache: dict[str, dict[str, Any]] = {}
        self.warnings: list[str] = []

    def agent_kwargs(self, node: AgentNode) -> dict[str, Any]:
        """`Agent(llm=..., tts=...)` kwargs for `node` (empty when it has no usable override)."""
        if node.id in self._cache:
            return dict(self._cache[node.id])
        built: dict[str, Any] = {}
        if node.providers and self._mode != "cascaded":
            self._warn(
                node.id,
                f"provider overrides on node {node.id!r} are ignored in {self._mode} mode "
                "(realtime and half-cascade flows share one model)",
            )
        elif node.providers:
            for slot, ref in node.providers.items():
                provider, reason = resolve_override(slot, ref, self._resolved)
                if provider is None:
                    self._warn(node.id, f"node {node.id!r} {slot} override not applied: {reason}")
                    continue
                try:
                    built[slot] = self._factory.build(slot, provider, mode=self._mode)
                except Exception as exc:
                    self._warn(node.id, f"node {node.id!r} {slot} override could not be built: {exc}")
                    continue
                logger.info(
                    "flow node provider override",
                    node=node.id,
                    slot=slot,
                    provider_id=provider.provider_id,
                    model=provider.model,
                )
        self._cache[node.id] = built
        return dict(built)

    def _warn(self, node_id: str, message: str) -> None:
        self.warnings.append(message)
        logger.warning("flow provider override skipped", node=node_id, detail=message)
