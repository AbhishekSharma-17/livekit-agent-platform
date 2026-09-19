"""The insurance_claim pack's manifest: pure Pydantic, importable by ``lkap_api``
without a ``livekit`` import (docs/CONTRACTS.md §8).

Provider ids and default models are read from ``lkap_contracts.providers.REGISTRY``
rather than hardcoded (matching ``packs.generic.manifest``'s convention), so this
manifest tracks the registry's LiveKit Inference defaults automatically.
``recommended_pipeline`` is the cascaded LiveKit Inference stack (deepgram/nova-3
-> google/gemini-3.5-flash -> inworld/inworld-tts-2) plus Gemini image generation, so
the pack seeds and demos with LiveKit credentials alone
(docs/INSURANCE_PACK_MAPPING.md #1); an admin switches ``pipeline.mode`` to
``"realtime"`` with ``google-realtime`` once a Google credential exists, at which
point ``default_voice["google-realtime"]`` supplies the voice (``"Kore"``).

The camera is on for this pack, so its LLM slot is the Inference provider's first
model flagged ``supports_video`` (DECISIONS-W2 §D-W2-10): the registry default
``google/gemma-4-31b-it`` is text-only on Inference and silently ignores images.
"""

from __future__ import annotations

from lkap_contracts import providers
from lkap_contracts.agent_config import CapabilitiesConfig, PipelineConfig, ProviderRef
from lkap_contracts.packs import KbSeed, PackManifest
from lkap_contracts.providers import ProviderSpec

from packs.insurance_claim.instructions import CASCADED_MODE_ADDENDUM, SYSTEM_INSTRUCTION
from packs.insurance_claim.ui_state import InsuranceState

_STT = providers.get("livekit-inference-stt")
_LLM = providers.get("livekit-inference-llm")
_TTS = providers.get("livekit-inference-tts")
_IMAGE_GEN = providers.get("google-image-gen")


def _vision_model(spec: ProviderSpec) -> str | None:
    """The provider's first model flagged ``supports_video``, else its default model.

    The fallback keeps the pack seedable if the registry ever loses its vision model.
    """
    return next((m.id for m in spec.models if m.supports_video), spec.default_model)


#: Ported verbatim from ``live_tools.py`` (docs/INSURANCE_PACK_MAPPING.md #3).
DEFAULT_GREETING = "I can start the claim while we talk. First, are you and everyone else in a safe place?"

DESCRIPTION = (
    "A live voice intake agent for a first notice of loss (FNOL) team: verifies the "
    "policy, extracts and classifies the claim, tracks a document checklist, tapes "
    "camera evidence and an incident sketch into a shared notebook, and escalates "
    "safety concerns."
)

MANIFEST = PackManifest(
    id="insurance_claim",
    version="0.1.0",
    name="Insurance claim intake",
    description=DESCRIPTION,
    ui_panel_id="insurance_notebook",
    default_instructions=SYSTEM_INSTRUCTION,
    default_greeting=DEFAULT_GREETING,
    # google-realtime's voice once an admin switches to Gemini Live (mapping #1).
    default_voice={"google-realtime": "Kore"},
    recommended_pipeline=PipelineConfig(
        mode="cascaded",
        stt=ProviderRef(provider_id=_STT.id, model=_STT.default_model),
        llm=ProviderRef(provider_id=_LLM.id, model=_vision_model(_LLM)),
        tts=ProviderRef(provider_id=_TTS.id, model=_TTS.default_model),
        image_gen=ProviderRef(provider_id=_IMAGE_GEN.id, model=_IMAGE_GEN.default_model),
    ),
    capabilities=CapabilitiesConfig(camera=True),
    # The pack's own tools cover pinning/notes/status/escalation; http_request stays
    # off by default for a demo pack (docs/INSURANCE_PACK_MAPPING.md #9 table row).
    builtin_tools_disabled=[
        "pin_frame",
        "push_note",
        "set_status",
        "escalate_to_human",
        "http_request",
    ],
    tool_names=[
        "lookup_policy",
        "sync_claim_packet",
        "pin_evidence_photo",
        "draw_incident_sketch",
    ],
    state_schema=InsuranceState.model_json_schema(),
    kb_seeds=[
        KbSeed(kb_name="Insurance policy lines", files=["policy_lines.md"]),
        KbSeed(kb_name="Intake playbook", files=["intake_playbook.md"]),
    ],
    instructions_by_mode={"cascaded": CASCADED_MODE_ADDENDUM},
)

__all__ = ["DEFAULT_GREETING", "DESCRIPTION", "MANIFEST"]
