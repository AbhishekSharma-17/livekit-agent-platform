"""Map a worker-reported usage entry onto a registry provider id and price unit.

The worker's `session.usage` (`SessionSummaryIn.usage`, CONTRACTS-V2 §4.6) is
`dataclasses.asdict(AgentSessionUsage)` — `{"model_usage": [ModelUsage, ...]}`
where each entry is one of livekit-agents 1.8.2's ``LLMModelUsage`` /
``TTSModelUsage`` / ``STTModelUsage`` / ``InterruptionModelUsage`` /
``EOTModelUsage`` dataclasses (`livekit.agents.metrics.usage`), tagged by a
``type`` field (``"llm_usage"``, ``"tts_usage"``, ``"stt_usage"``, ...).

**Why this module exists rather than trusting `entry["provider"]`:** each
plugin's `.provider` property (the source of that field, via
`Metadata.model_provider` in `livekit.agents.metrics.base`) is a free-text
display string with no fixed vocabulary — OpenAI's LLM/TTS classes return the
API base url's netloc (``"api.openai.com"``), Google's `LLM.provider` returns
``"Gemini"`` or ``"Vertex AI"``, Deepgram/ElevenLabs/Cartesia hardcode their
display name (``"Deepgram"``, ``"ElevenLabs"``, ``"Cartesia"``). None of these
match a `lkap_contracts.providers` id (``"openai-llm"``, ``"google-llm"``,
``"deepgram-stt"``, ...), and there is no stable, documented mapping from one
to the other across the plugin catalog. Deriving a name-guessing table would
be exactly the kind of unverifiable heuristic CONTRACTS-V2 §4.2 forbids for
pricing.

Instead this maps by **slot**: a session's `AgentConfig.pipeline` already
names the exact registry provider bound to each kind (`llm`/`realtime` for
`llm_usage`, `tts` for `tts_usage`, `stt` for `stt_usage`) — that is the
provider the workspace actually configured and (for credentialed ones) pays
for, which is more precise than a display-string guess even when it happens
to match. The usage entry still contributes the real `model` id actually used
(falling back to the `ProviderRef.model` override, then the registry's
`default_model`) because a session's provider can serve more than one model.

`interruption_usage`/`eot_usage` entries (LiveKit-hosted VAD/turn-detector
inference counts) are deliberately not priced here: they are not a
usage-metered vendor cost in `pricing.PRICES` today (LiveKit Inference has no
published, single per-request figure — see `pricing.py`'s module docstring),
and CONTRACTS-V2's `Unit` literal has no "requests" unit for them anyway.
"""

from __future__ import annotations

from typing import Any

from lkap_contracts import providers
from lkap_contracts.agent_config import PipelineConfig
from lkap_contracts.common import ProviderRef

#: Which `PipelineConfig` slots can produce each usage `type`, in preference order.
_SLOTS_FOR_USAGE_TYPE: dict[str, tuple[str, ...]] = {
    "llm_usage": ("llm", "realtime"),
    "tts_usage": ("tts",),
    "stt_usage": ("stt",),
}


def ref_for_usage_type(usage_type: str, pipeline: PipelineConfig) -> ProviderRef | None:
    """Return the `ProviderRef` billed for one `ModelUsage.type`, or `None`.

    `None` means the session's pipeline has no slot that could have produced
    this usage kind (a misconfigured or stale entry) — callers skip it rather
    than guess a provider.
    """
    for slot in _SLOTS_FOR_USAGE_TYPE.get(usage_type, ()):
        ref = getattr(pipeline, slot, None)
        if isinstance(ref, ProviderRef):
            return ref
    return None


def resolve_model(entry: dict[str, Any], ref: ProviderRef | None) -> str | None:
    """The real model used: the usage entry's own value, then the ref, then the registry default."""
    reported = entry.get("model")
    if isinstance(reported, str) and reported:
        return reported
    if ref is not None and ref.model:
        return ref.model
    if ref is not None:
        try:
            return providers.get(ref.provider_id).default_model
        except KeyError:
            return None
    return None
