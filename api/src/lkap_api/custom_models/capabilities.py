"""Model capabilities: declared → detected → live catalog → registry → unknown (D-V4-24, R-V4-23).

:func:`resolve_capabilities` takes each :class:`ModelCapabilities` field from
the first source that knows it. Catalog readers exist only where the vendor
publishes capabilities per model: OpenRouter, Anthropic, Mistral, xAI,
Bedrock, Gemini (generation methods), Fireworks and ElevenLabs. OpenAI, Groq
and Cerebras lists carry none, which is why "Test model" and the admin's
declaration exist. The readers go by the item's shape, so an unfamiliar or
partial item contributes nothing rather than raising.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from lkap_contracts.api_models import CatalogItem, ProviderModelOut
from lkap_contracts.providers import ModelCapabilities, ProviderSpec, vision_support

Source = Literal["declared", "detected", "catalog", "registry"]

#: The capability fields resolved one by one (``source`` is derived).
FIELDS: tuple[str, ...] = ("vision", "tools", "audio_in", "audio_out", "streaming", "context_tokens")


def _contains(values: Any, needle: str) -> bool | None:
    if not isinstance(values, list):
        return None
    return any(isinstance(v, str) and v.casefold() == needle.casefold() for v in values)


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def catalog_capabilities(meta: Mapping[str, Any]) -> ModelCapabilities:
    """What one live catalog item's raw ``meta`` says about the model (unknown stays ``None``)."""
    caps: dict[str, Any] = {}

    def put(name: str, value: bool | int | None) -> None:
        if value is not None and caps.get(name) is None:
            caps[name] = value

    # OpenRouter: architecture.input_modalities / supported_parameters / context_length.
    architecture = meta.get("architecture")
    if isinstance(architecture, dict):
        put("vision", _contains(architecture.get("input_modalities"), "image"))
        put("audio_in", _contains(architecture.get("input_modalities"), "audio"))
        put("audio_out", _contains(architecture.get("output_modalities"), "audio"))
    put("tools", _contains(meta.get("supported_parameters"), "tools"))
    put("context_tokens", _int(meta.get("context_length")))

    # Anthropic: capabilities.image_input.supported; Mistral: capabilities.vision/function_calling.
    capabilities = meta.get("capabilities")
    if isinstance(capabilities, dict):
        image_input = capabilities.get("image_input")
        if isinstance(image_input, dict):
            put("vision", _bool(image_input.get("supported")))
        put("vision", _bool(capabilities.get("vision")))
        put("tools", _bool(capabilities.get("function_calling")))
    put("context_tokens", _int(meta.get("max_input_tokens")))
    put("context_tokens", _int(meta.get("max_context_length")))

    # xAI: input_modalities/output_modalities at the top level.
    put("vision", _contains(meta.get("input_modalities"), "image"))
    put("audio_in", _contains(meta.get("input_modalities"), "audio"))
    put("audio_out", _contains(meta.get("output_modalities"), "audio"))

    # Bedrock: inputModalities / responseStreamingSupported.
    put("vision", _contains(meta.get("inputModalities"), "IMAGE"))
    put("streaming", _bool(meta.get("responseStreamingSupported")))

    # Gemini: supportedGenerationMethods (bidiGenerateContent = Live) / inputTokenLimit.
    methods = meta.get("supportedGenerationMethods")
    if isinstance(methods, list) and "bidiGenerateContent" in methods:
        put("audio_in", True)
        put("audio_out", True)
    put("context_tokens", _int(meta.get("inputTokenLimit")))

    # Fireworks: supportsTools / supportsImageInput / contextLength.
    put("tools", _bool(meta.get("supportsTools")))
    put("vision", _bool(meta.get("supportsImageInput")))
    put("context_tokens", _int(meta.get("contextLength")))

    # ElevenLabs models: can_do_text_to_speech.
    put("audio_out", _bool(meta.get("can_do_text_to_speech")))
    return ModelCapabilities.model_validate(caps)


def registry_capabilities(spec: ProviderSpec, model_id: str | None) -> ModelCapabilities:
    """What the registry knows: ``ModelSpec.supports_video`` → vision, ``tool_calling`` → tools.

    ``tools`` comes from the provider-level flag and only for the kinds that
    call tools (llm, realtime); ``vision`` only for a model the registry lists.
    """
    return ModelCapabilities(
        vision=vision_support(spec.id, model_id),
        tools=spec.capabilities.tool_calling if spec.kind in ("llm", "realtime") else None,
    )


def resolve_capabilities(
    spec: ProviderSpec,
    model_id: str | None,
    record: ProviderModelOut | None,
    catalog_item: CatalogItem | None,
) -> ModelCapabilities:
    """Resolve each capability from declared → detected → catalog → registry → unknown.

    Args:
        spec: The registry entry the model runs on.
        model_id: The configured id (``None`` resolves to ``spec.default_model``).
        record: The workspace's ``provider_models`` row for it, if any.
        catalog_item: The model's item in the cached live catalog, if listed.

    Returns:
        One :class:`ModelCapabilities`; ``source`` names where ``vision`` came from
        (``None`` when vision is unknown).
    """
    model = model_id or spec.default_model
    layers: list[tuple[Source, ModelCapabilities | None]] = [
        ("declared", record.declared if record else None),
        ("detected", record.detected if record else None),
        ("catalog", catalog_capabilities(catalog_item.meta) if catalog_item else None),
        ("registry", registry_capabilities(spec, model)),
    ]
    resolved: dict[str, Any] = {}
    vision_source: Source | None = None
    for source, caps in layers:
        if caps is None:
            continue
        for name in FIELDS:
            value = getattr(caps, name)
            if value is not None and resolved.get(name) is None:
                resolved[name] = value
                if name == "vision":
                    vision_source = source
    return ModelCapabilities.model_validate({**resolved, "source": vision_source})


__all__ = ["catalog_capabilities", "registry_capabilities", "resolve_capabilities"]
