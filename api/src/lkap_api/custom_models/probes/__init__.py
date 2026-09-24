"""The "Test model" probe tree (docs/v4/CUSTOM-MODELS.md D-V4-26, R-V4-25).

:data:`PROBES` maps every ``ProviderSpec.probe`` name to its adapter. An
entry without a ``probe`` is not tested (``ok=None``); image models are never
probed. The budgets and the shared contract are in
:mod:`lkap_api.custom_models.probes.base`.
"""

from __future__ import annotations

from lkap_api.custom_models.probes.anthropic import AnthropicMessagesProbe
from lkap_api.custom_models.probes.avatars import (
    AnamAvatarProbe,
    BeyAvatarProbe,
    SimliFaceMemberProbe,
    TavusReplicaProbe,
)
from lkap_api.custom_models.probes.base import (
    Probe,
    ProbeContext,
    ProbeInputError,
    ProbeOutcome,
    Usage,
    WsConnector,
    WsSession,
)
from lkap_api.custom_models.probes.cartesia import CartesiaSttProbe, CartesiaTtsProbe
from lkap_api.custom_models.probes.deepgram import DeepgramListenProbe, DeepgramSpeakProbe
from lkap_api.custom_models.probes.elevenlabs import ElevenLabsSttProbe, ElevenLabsTtsProbe
from lkap_api.custom_models.probes.gemini import GeminiEmbedProbe, GeminiGenerateProbe
from lkap_api.custom_models.probes.openai_like import (
    OpenAiChatProbe,
    OpenAiEmbeddingsProbe,
    OpenAiSpeechProbe,
    OpenAiTranscriptionsProbe,
)
from lkap_api.custom_models.probes.realtime import (
    AiohttpWsConnector,
    GeminiLiveProbe,
    OpenAiRealtimeProbe,
    XaiRealtimeProbe,
)

_ALL: tuple[Probe, ...] = (
    OpenAiChatProbe(),
    AnthropicMessagesProbe(),
    GeminiGenerateProbe(),
    OpenAiTranscriptionsProbe(),
    DeepgramListenProbe(),
    ElevenLabsSttProbe(),
    CartesiaSttProbe(),
    OpenAiSpeechProbe(),
    DeepgramSpeakProbe(),
    ElevenLabsTtsProbe(),
    CartesiaTtsProbe(),
    OpenAiEmbeddingsProbe(),
    GeminiEmbedProbe(),
    OpenAiRealtimeProbe(),
    XaiRealtimeProbe(),
    GeminiLiveProbe(),
    BeyAvatarProbe(),
    TavusReplicaProbe(),
    SimliFaceMemberProbe(),
    AnamAvatarProbe(),
)

#: Every probe adapter, keyed by the name a registry entry's ``probe`` carries.
PROBES: dict[str, Probe] = {probe.name: probe for probe in _ALL}

__all__ = [
    "PROBES",
    "AiohttpWsConnector",
    "Probe",
    "ProbeContext",
    "ProbeInputError",
    "ProbeOutcome",
    "Usage",
    "WsConnector",
    "WsSession",
]
