"""The generic pack's manifest: pure Pydantic, importable by ``lkap_api`` without ``livekit``.

Provider ids and default models are read from ``lkap_contracts.providers.REGISTRY``
rather than hardcoded, so this manifest tracks the registry if its LiveKit
Inference defaults ever change.
"""

from __future__ import annotations

from lkap_contracts import providers
from lkap_contracts.agent_config import (
    CapabilitiesConfig,
    PanelLayout,
    PipelineConfig,
    ProviderRef,
)
from lkap_contracts.migrate import default_panel_for
from lkap_contracts.packs import PackManifest

_STT = providers.get("livekit-inference-stt")
_LLM = providers.get("livekit-inference-llm")
_TTS = providers.get("livekit-inference-tts")

DEFAULT_INSTRUCTIONS = (
    "You are a helpful voice assistant. Answer questions clearly and briefly, and ask a "
    "clarifying question when the user's request is ambiguous."
)
DEFAULT_GREETING = "Hello! How can I help you today?"

MANIFEST = PackManifest(
    id="generic",
    version="0.1.0",
    name="Generic assistant",
    description="A plain voice assistant with no pack-specific tools or UI state.",
    ui_panel_id="generic",
    # v2: the generic panel is the built-in composite panel with the four default
    # blocks, exactly what `migrate.agent_config_v1_to_v2` writes for v1 agents.
    default_panel=PanelLayout.model_validate(default_panel_for("generic")),
    default_instructions=DEFAULT_INSTRUCTIONS,
    default_greeting=DEFAULT_GREETING,
    default_voice={},
    recommended_pipeline=PipelineConfig(
        mode="cascaded",
        stt=ProviderRef(provider_id=_STT.id, model=_STT.default_model),
        llm=ProviderRef(provider_id=_LLM.id, model=_LLM.default_model),
        tts=ProviderRef(provider_id=_TTS.id, model=_TTS.default_model),
    ),
    capabilities=CapabilitiesConfig(),
    builtin_tools_disabled=[],
    tool_names=[],
    state_schema={"type": "object", "properties": {}, "additionalProperties": True},
    settings_schema={"type": "object"},
    kb_seeds=[],
    instructions_by_mode={},
)

__all__ = ["MANIFEST"]
