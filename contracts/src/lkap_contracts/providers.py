"""Provider registry: the catalogue of everything the platform can construct.

`REGISTRY` is the single source of truth for provider ids, constructor classes,
credential fields, configurable non-secret fields and suggested models. The api
validates `AgentConfig` against it, the agent's `ProviderFactory` builds plugin
objects from it, and the web console renders its forms from the exported
`generated/providers.json`.
"""

from typing import Literal

from pydantic import BaseModel, Field

ProviderKind = Literal[
    "realtime",
    "stt",
    "llm",
    "tts",
    "avatar",
    "image_gen",
    "embedding",
    "secret_bag",
]
FieldType = Literal["string", "secret", "number", "boolean", "enum", "json", "model"]

#: Gemini Live prebuilt voice names offered in the console.
GEMINI_LIVE_VOICES: list[str] = [
    "Kore",
    "Puck",
    "Charon",
    "Aoede",
    "Fenrir",
    "Leda",
    "Orus",
    "Zephyr",
]


class FieldSpec(BaseModel):
    """One configurable constructor argument of a provider."""

    name: str
    label: str
    type: FieldType
    required: bool = False
    default: str | int | float | bool | None = None
    options: list[str] | None = None
    placeholder: str | None = None
    help: str | None = None
    condition: str | None = None
    env_fallback: str | None = None


class ModelSpec(BaseModel):
    """A suggested model id for a provider (a suggestion list, never an allowlist)."""

    id: str
    label: str
    supports_video: bool = Field(
        False,
        description=(
            "Accepts visual input: video frames for realtime models, image content parts for LLMs. "
            "Set only after the platform has verified it."
        ),
    )
    note: str | None = None


class ProviderCapabilities(BaseModel):
    """What a provider can do, used to gate UI affordances and runtime behaviour."""

    video_input: bool = False
    tool_calling: bool = True
    silent_tool_reply: bool = False
    voices: list[str] = []


class ProviderSpec(BaseModel):
    """Everything the platform needs to offer, configure and construct a provider."""

    v: Literal[1] = 1
    id: str
    kind: ProviderKind
    label: str
    vendor: str
    status: Literal["mvp", "deferred"] = "mvp"
    package: str
    python_class: str
    requires_credential: bool = True
    secret_fields: list[FieldSpec] = []
    fields: list[FieldSpec] = []
    models: list[ModelSpec] = []
    default_model: str | None = None
    capabilities: ProviderCapabilities = ProviderCapabilities()
    docs_url: str | None = None
    get_key_url: str | None = None


def _api_key(label: str = "API key", *, help_text: str | None = None, env: str | None = None) -> FieldSpec:
    return FieldSpec(
        name="api_key", label=label, type="secret", required=True, help=help_text, env_fallback=env
    )


def _deferred(
    provider_id: str,
    kind: ProviderKind,
    label: str,
    vendor: str,
    package: str,
    python_class: str,
    *,
    requires_credential: bool = True,
    fields: list[FieldSpec] | None = None,
) -> ProviderSpec:
    """Build a catalogue-only entry shown in the console as "coming soon"."""
    return ProviderSpec(
        id=provider_id,
        kind=kind,
        label=label,
        vendor=vendor,
        status="deferred",
        package=package,
        python_class=python_class,
        requires_credential=requires_credential,
        secret_fields=[_api_key()] if requires_credential else [],
        fields=fields or [],
    )


_MVP: list[ProviderSpec] = [
    # ---------------------------------------------------------------- LiveKit Inference
    ProviderSpec(
        id="livekit-inference-stt",
        kind="stt",
        label="LiveKit Inference (STT)",
        vendor="LiveKit",
        package="livekit-agents",
        python_class="livekit.agents.inference.STT",
        requires_credential=False,
        fields=[
            FieldSpec(
                name="language",
                label="Language",
                type="string",
                default="en",
                help="BCP-47 language code passed to the transcriber.",
            )
        ],
        models=[
            ModelSpec(id="deepgram/nova-3", label="Deepgram Nova 3"),
            ModelSpec(id="deepgram/flux-general-en", label="Deepgram Flux (general, en)"),
            ModelSpec(id="assemblyai/universal-streaming", label="AssemblyAI Universal Streaming"),
            ModelSpec(id="cartesia/ink-whisper", label="Cartesia Ink Whisper"),
            ModelSpec(id="google/gemini-3.5-transcribe-live", label="Gemini 3.5 Transcribe Live"),
        ],
        default_model="deepgram/nova-3",
        docs_url="https://docs.livekit.io/agents/models/stt/",
    ),
    ProviderSpec(
        id="livekit-inference-llm",
        kind="llm",
        label="LiveKit Inference (LLM)",
        vendor="LiveKit",
        package="livekit-agents",
        python_class="livekit.agents.inference.LLM",
        requires_credential=False,
        fields=[
            FieldSpec(
                name="temperature",
                label="Temperature",
                type="number",
                default=0.7,
                help="Sampling temperature (0 = deterministic).",
            )
        ],
        models=[
            ModelSpec(
                id="google/gemma-4-31b-it",
                label="Gemma 4 31B Instruct",
                note="text-only on LiveKit Inference: ignores image parts silently",
            ),
            ModelSpec(id="google/gemini-3.5-flash", label="Gemini 3.5 Flash", supports_video=True),
            ModelSpec(id="openai/gpt-4.1", label="GPT-4.1"),
            ModelSpec(id="openai/gpt-4o-mini", label="GPT-4o mini"),
            ModelSpec(id="openai/gpt-oss-120b", label="GPT-OSS 120B"),
        ],
        default_model="google/gemma-4-31b-it",
        docs_url="https://docs.livekit.io/agents/models/llm/",
    ),
    ProviderSpec(
        id="livekit-inference-tts",
        kind="tts",
        label="LiveKit Inference (TTS)",
        vendor="LiveKit",
        package="livekit-agents",
        python_class="livekit.agents.inference.TTS",
        requires_credential=False,
        fields=[
            FieldSpec(name="voice", label="Voice", type="string", default="Ashley"),
            FieldSpec(name="language", label="Language", type="string", default="en"),
        ],
        models=[
            ModelSpec(id="inworld/inworld-tts-2", label="Inworld TTS 2"),
            ModelSpec(id="cartesia/sonic-3", label="Cartesia Sonic 3"),
            ModelSpec(id="deepgram/aura-2", label="Deepgram Aura 2"),
            ModelSpec(id="rime/mistv3", label="Rime Mist v3"),
        ],
        default_model="inworld/inworld-tts-2",
        capabilities=ProviderCapabilities(voices=["Ashley", "Brooke", "Cole", "Hana"]),
        docs_url="https://docs.livekit.io/agents/models/tts/",
    ),
    # ---------------------------------------------------------------- realtime
    ProviderSpec(
        id="google-realtime",
        kind="realtime",
        label="Gemini Live",
        vendor="Google",
        package="livekit-plugins-google",
        python_class="livekit.plugins.google.realtime.RealtimeModel",
        secret_fields=[_api_key("Google API key", env="GOOGLE_API_KEY")],
        fields=[
            FieldSpec(
                name="voice",
                label="Voice",
                type="enum",
                default="Kore",
                options=GEMINI_LIVE_VOICES,
            ),
            FieldSpec(name="temperature", label="Temperature", type="number", default=0.8),
            FieldSpec(
                name="tool_behavior",
                label="Tool behaviour",
                type="enum",
                default="NON_BLOCKING",
                options=["BLOCKING", "NON_BLOCKING"],
                help="Silent tool replies require NON_BLOCKING.",
            ),
            FieldSpec(
                name="tool_response_scheduling",
                label="Tool response scheduling",
                type="enum",
                default="WHEN_IDLE",
                options=["WHEN_IDLE", "INTERRUPT", "SILENT"],
                help="When a non-blocking tool result is spoken.",
            ),
            FieldSpec(
                name="enable_affective_dialog",
                label="Affective dialog",
                type="boolean",
                default=False,
            ),
        ],
        models=[
            ModelSpec(id="gemini-3.8-live", label="Gemini 3.8 Live", supports_video=True),
            ModelSpec(
                id="gemini-3.1-flash-live-preview",
                label="Gemini 3.1 Flash Live (preview)",
                supports_video=True,
            ),
            ModelSpec(
                id="gemini-2.5-flash-native-audio-preview-12-2025",
                label="Gemini 2.5 Flash native audio (preview)",
                supports_video=True,
            ),
        ],
        default_model="gemini-3.8-live",
        capabilities=ProviderCapabilities(
            video_input=True,
            tool_calling=True,
            silent_tool_reply=True,
            voices=GEMINI_LIVE_VOICES,
        ),
        docs_url="https://docs.livekit.io/agents/models/realtime/gemini/",
        get_key_url="https://aistudio.google.com/apikey",
    ),
    ProviderSpec(
        id="openai-realtime",
        kind="realtime",
        label="OpenAI Realtime",
        vendor="OpenAI",
        package="livekit-plugins-openai",
        python_class="livekit.plugins.openai.realtime.RealtimeModel",
        secret_fields=[_api_key("OpenAI API key", env="OPENAI_API_KEY")],
        fields=[
            FieldSpec(name="voice", label="Voice", type="string", default="marin"),
            FieldSpec(
                name="base_url",
                label="Base URL",
                type="string",
                placeholder="https://api.openai.com/v1",
                help="Override for OpenAI-compatible realtime endpoints.",
            ),
        ],
        models=[ModelSpec(id="gpt-realtime", label="GPT Realtime")],
        default_model="gpt-realtime",
        capabilities=ProviderCapabilities(
            video_input=False,
            tool_calling=True,
            silent_tool_reply=True,
            voices=["marin", "cedar", "alloy", "ash", "ballad", "coral", "sage", "verse"],
        ),
        docs_url="https://docs.livekit.io/agents/models/realtime/openai/",
        get_key_url="https://platform.openai.com/api-keys",
    ),
    # ---------------------------------------------------------------- cascaded plugins
    ProviderSpec(
        id="deepgram-stt",
        kind="stt",
        label="Deepgram",
        vendor="Deepgram",
        package="livekit-plugins-deepgram",
        python_class="livekit.plugins.deepgram.STT",
        secret_fields=[_api_key("Deepgram API key", env="DEEPGRAM_API_KEY")],
        fields=[FieldSpec(name="language", label="Language", type="string", default="en-US")],
        models=[
            ModelSpec(id="nova-3", label="Nova 3"),
            ModelSpec(id="nova-2", label="Nova 2"),
            ModelSpec(id="flux-general-en", label="Flux (general, en)"),
        ],
        default_model="nova-3",
        docs_url="https://docs.livekit.io/agents/models/stt/deepgram/",
        get_key_url="https://console.deepgram.com/",
    ),
    ProviderSpec(
        id="openai-llm",
        kind="llm",
        label="OpenAI",
        vendor="OpenAI",
        package="livekit-plugins-openai",
        python_class="livekit.plugins.openai.LLM",
        secret_fields=[_api_key("OpenAI API key", env="OPENAI_API_KEY")],
        fields=[
            FieldSpec(
                name="base_url",
                label="Base URL",
                type="string",
                placeholder="https://api.openai.com/v1",
            ),
            FieldSpec(name="temperature", label="Temperature", type="number", default=0.7),
        ],
        models=[
            ModelSpec(id="gpt-4.1", label="GPT-4.1"),
            ModelSpec(id="gpt-4o", label="GPT-4o"),
            ModelSpec(id="gpt-4.1-mini", label="GPT-4.1 mini"),
        ],
        default_model="gpt-4.1",
        docs_url="https://docs.livekit.io/agents/models/llm/openai/",
        get_key_url="https://platform.openai.com/api-keys",
    ),
    ProviderSpec(
        id="google-llm",
        kind="llm",
        label="Google Gemini",
        vendor="Google",
        package="livekit-plugins-google",
        python_class="livekit.plugins.google.LLM",
        secret_fields=[_api_key("Google API key", env="GOOGLE_API_KEY")],
        fields=[FieldSpec(name="temperature", label="Temperature", type="number", default=0.7)],
        models=[
            ModelSpec(id="gemini-2.5-flash", label="Gemini 2.5 Flash"),
            ModelSpec(id="gemini-3.5-flash", label="Gemini 3.5 Flash"),
        ],
        default_model="gemini-2.5-flash",
        docs_url="https://docs.livekit.io/agents/models/llm/gemini/",
        get_key_url="https://aistudio.google.com/apikey",
    ),
    ProviderSpec(
        id="cartesia-tts",
        kind="tts",
        label="Cartesia",
        vendor="Cartesia",
        package="livekit-plugins-cartesia",
        python_class="livekit.plugins.cartesia.TTS",
        secret_fields=[_api_key("Cartesia API key", env="CARTESIA_API_KEY")],
        fields=[
            FieldSpec(name="voice", label="Voice id", type="string"),
            FieldSpec(name="language", label="Language", type="string", default="en"),
        ],
        models=[ModelSpec(id="sonic-3", label="Sonic 3")],
        default_model="sonic-3",
        docs_url="https://docs.livekit.io/agents/models/tts/cartesia/",
        get_key_url="https://play.cartesia.ai/keys",
    ),
    ProviderSpec(
        id="elevenlabs-tts",
        kind="tts",
        label="ElevenLabs",
        vendor="ElevenLabs",
        package="livekit-plugins-elevenlabs",
        python_class="livekit.plugins.elevenlabs.TTS",
        secret_fields=[_api_key("ElevenLabs API key", env="ELEVEN_API_KEY")],
        fields=[FieldSpec(name="voice_id", label="Voice id", type="string")],
        models=[
            ModelSpec(id="eleven_turbo_v2_5", label="Eleven Turbo v2.5"),
            ModelSpec(id="eleven_flash_v2_5", label="Eleven Flash v2.5"),
        ],
        default_model="eleven_turbo_v2_5",
        docs_url="https://docs.livekit.io/agents/models/tts/elevenlabs/",
        get_key_url="https://elevenlabs.io/app/settings/api-keys",
    ),
    ProviderSpec(
        id="openai-tts",
        kind="tts",
        label="OpenAI TTS",
        vendor="OpenAI",
        package="livekit-plugins-openai",
        python_class="livekit.plugins.openai.TTS",
        secret_fields=[_api_key("OpenAI API key", env="OPENAI_API_KEY")],
        fields=[FieldSpec(name="voice", label="Voice", type="string", default="ash")],
        models=[ModelSpec(id="gpt-4o-mini-tts", label="GPT-4o mini TTS")],
        default_model="gpt-4o-mini-tts",
        docs_url="https://docs.livekit.io/agents/models/tts/openai/",
        get_key_url="https://platform.openai.com/api-keys",
    ),
    # ---------------------------------------------------------------- avatars
    ProviderSpec(
        id="bey-avatar",
        kind="avatar",
        label="Beyond Presence",
        vendor="Beyond Presence",
        package="livekit-plugins-bey",
        python_class="livekit.plugins.bey.AvatarSession",
        secret_fields=[_api_key("Beyond Presence API key", env="BEY_API_KEY")],
        fields=[
            FieldSpec(
                name="avatar_id",
                label="Avatar id",
                type="string",
                default="b9be11b8-89fb-4227-8f86-4a881393cbdb",
                help="Defaults to the public Beyond Presence stock avatar.",
            )
        ],
        capabilities=ProviderCapabilities(tool_calling=False),
        docs_url="https://docs.livekit.io/agents/integrations/avatar/bey/",
        get_key_url="https://bey.chat/",
    ),
    ProviderSpec(
        id="tavus-avatar",
        kind="avatar",
        label="Tavus",
        vendor="Tavus",
        package="livekit-plugins-tavus",
        python_class="livekit.plugins.tavus.AvatarSession",
        secret_fields=[_api_key("Tavus API key", env="TAVUS_API_KEY")],
        fields=[
            FieldSpec(name="face_id", label="Replica (face) id", type="string"),
            FieldSpec(name="pal_id", label="Persona (pal) id", type="string"),
        ],
        capabilities=ProviderCapabilities(tool_calling=False),
        docs_url="https://docs.livekit.io/agents/integrations/avatar/tavus/",
        get_key_url="https://platform.tavus.io/api-keys",
    ),
    # ---------------------------------------------------------------- image generation
    ProviderSpec(
        id="google-image-gen",
        kind="image_gen",
        label="Gemini image generation",
        vendor="Google",
        package="google-genai",
        python_class="lkap_agent.providers.image_gen.GoogleImageGen",
        secret_fields=[_api_key("Google API key", env="GOOGLE_API_KEY")],
        models=[ModelSpec(id="gemini-3.1-flash-image", label="Gemini 3.1 Flash Image")],
        default_model="gemini-3.1-flash-image",
        capabilities=ProviderCapabilities(tool_calling=False),
        get_key_url="https://aistudio.google.com/apikey",
    ),
    ProviderSpec(
        id="openai-image-gen",
        kind="image_gen",
        label="OpenAI image generation",
        vendor="OpenAI",
        package="openai",
        python_class="lkap_agent.providers.image_gen.OpenAIImageGen",
        secret_fields=[_api_key("OpenAI API key", env="OPENAI_API_KEY")],
        fields=[FieldSpec(name="size", label="Image size", type="string", default="1024x1024")],
        models=[ModelSpec(id="gpt-image-1", label="GPT Image 1")],
        default_model="gpt-image-1",
        capabilities=ProviderCapabilities(tool_calling=False),
        get_key_url="https://platform.openai.com/api-keys",
    ),
    # ---------------------------------------------------------------- embeddings
    ProviderSpec(
        id="fastembed-embedding",
        kind="embedding",
        label="FastEmbed (local)",
        vendor="Qdrant",
        package="fastembed",
        python_class="lkap_api.kb.embed.FastEmbedEmbedder",
        requires_credential=False,
        models=[ModelSpec(id="BAAI/bge-small-en-v1.5", label="BGE small en v1.5")],
        default_model="BAAI/bge-small-en-v1.5",
        capabilities=ProviderCapabilities(tool_calling=False),
    ),
    ProviderSpec(
        id="openai-embedding",
        kind="embedding",
        label="OpenAI embeddings",
        vendor="OpenAI",
        package="openai",
        python_class="lkap_api.kb.embed.OpenAIEmbedder",
        secret_fields=[_api_key("OpenAI API key", env="OPENAI_API_KEY")],
        models=[ModelSpec(id="text-embedding-3-small", label="text-embedding-3-small")],
        default_model="text-embedding-3-small",
        capabilities=ProviderCapabilities(tool_calling=False),
        get_key_url="https://platform.openai.com/api-keys",
    ),
    # ---------------------------------------------------------------- tool secrets
    ProviderSpec(
        id="http-tool-secret",
        kind="secret_bag",
        label="Tool secrets",
        vendor="LKAP",
        package="",
        python_class="",
        requires_credential=True,
        secret_fields=[],
        capabilities=ProviderCapabilities(tool_calling=False),
    ),
]

_DEFERRED: list[ProviderSpec] = [
    _deferred(
        "azure-openai-realtime",
        "realtime",
        "Azure OpenAI Realtime",
        "Microsoft",
        "livekit-plugins-openai",
        "livekit.plugins.openai.realtime.RealtimeModel.with_azure",
    ),
    _deferred(
        "xai-realtime",
        "realtime",
        "xAI Realtime",
        "xAI",
        "livekit-plugins-xai",
        "livekit.plugins.xai.realtime.RealtimeModel",
    ),
    _deferred(
        "assemblyai-stt",
        "stt",
        "AssemblyAI",
        "AssemblyAI",
        "livekit-plugins-assemblyai",
        "livekit.plugins.assemblyai.STT",
    ),
    _deferred(
        "google-stt",
        "stt",
        "Google Speech-to-Text",
        "Google",
        "livekit-plugins-google",
        "livekit.plugins.google.STT",
    ),
    _deferred(
        "openai-stt",
        "stt",
        "OpenAI Whisper",
        "OpenAI",
        "livekit-plugins-openai",
        "livekit.plugins.openai.STT",
    ),
    _deferred(
        "speechmatics-stt",
        "stt",
        "Speechmatics",
        "Speechmatics",
        "livekit-plugins-speechmatics",
        "livekit.plugins.speechmatics.STT",
    ),
    _deferred(
        "elevenlabs-stt",
        "stt",
        "ElevenLabs Scribe",
        "ElevenLabs",
        "livekit-plugins-elevenlabs",
        "livekit.plugins.elevenlabs.STT",
    ),
    _deferred(
        "cartesia-stt",
        "stt",
        "Cartesia Ink",
        "Cartesia",
        "livekit-plugins-cartesia",
        "livekit.plugins.cartesia.STT",
    ),
    _deferred("groq-stt", "stt", "Groq Whisper", "Groq", "livekit-plugins-groq", "livekit.plugins.groq.STT"),
    _deferred(
        "azure-stt", "stt", "Azure Speech", "Microsoft", "livekit-plugins-azure", "livekit.plugins.azure.STT"
    ),
    _deferred(
        "anthropic-llm",
        "llm",
        "Anthropic Claude",
        "Anthropic",
        "livekit-plugins-anthropic",
        "livekit.plugins.anthropic.LLM",
    ),
    _deferred("groq-llm", "llm", "Groq", "Groq", "livekit-plugins-groq", "livekit.plugins.groq.LLM"),
    _deferred(
        "cerebras-llm",
        "llm",
        "Cerebras",
        "Cerebras",
        "livekit-plugins-openai",
        "livekit.plugins.openai.LLM.with_cerebras",
    ),
    _deferred(
        "openai-compatible-llm",
        "llm",
        "OpenAI-compatible endpoint",
        "Various",
        "livekit-plugins-openai",
        "livekit.plugins.openai.LLM",
        fields=[FieldSpec(name="base_url", label="Base URL", type="string", required=True)],
    ),
    _deferred(
        "aws-bedrock-llm", "llm", "AWS Bedrock", "Amazon", "livekit-plugins-aws", "livekit.plugins.aws.LLM"
    ),
    _deferred(
        "google-tts",
        "tts",
        "Google Cloud TTS",
        "Google",
        "livekit-plugins-google",
        "livekit.plugins.google.TTS",
    ),
    _deferred(
        "deepgram-tts",
        "tts",
        "Deepgram Aura",
        "Deepgram",
        "livekit-plugins-deepgram",
        "livekit.plugins.deepgram.TTS",
    ),
    _deferred("rime-tts", "tts", "Rime", "Rime", "livekit-plugins-rime", "livekit.plugins.rime.TTS"),
    _deferred(
        "inworld-tts", "tts", "Inworld", "Inworld", "livekit-plugins-inworld", "livekit.plugins.inworld.TTS"
    ),
    _deferred("hume-tts", "tts", "Hume Octave", "Hume", "livekit-plugins-hume", "livekit.plugins.hume.TTS"),
    _deferred(
        "azure-tts",
        "tts",
        "Azure Speech TTS",
        "Microsoft",
        "livekit-plugins-azure",
        "livekit.plugins.azure.TTS",
    ),
    _deferred(
        "simli-avatar",
        "avatar",
        "Simli",
        "Simli",
        "livekit-plugins-simli",
        "livekit.plugins.simli.AvatarSession",
        fields=[FieldSpec(name="simli_config.face_id", label="Face id", type="string")],
    ),
    _deferred(
        "anam-avatar", "avatar", "Anam", "Anam", "livekit-plugins-anam", "livekit.plugins.anam.AvatarSession"
    ),
    _deferred(
        "bithuman-avatar",
        "avatar",
        "bitHuman",
        "bitHuman",
        "livekit-plugins-bithuman",
        "livekit.plugins.bithuman.AvatarSession",
    ),
    _deferred(
        "liveavatar-avatar",
        "avatar",
        "LiveAvatar",
        "LiveAvatar",
        "livekit-plugins-liveavatar",
        "livekit.plugins.liveavatar.AvatarSession",
    ),
]

REGISTRY: list[ProviderSpec] = [*_MVP, *_DEFERRED]

_BY_ID: dict[str, ProviderSpec] = {spec.id: spec for spec in REGISTRY}


def get(provider_id: str) -> ProviderSpec:
    """Return the spec for ``provider_id``.

    Args:
        provider_id: A registry id such as ``"google-realtime"``.

    Returns:
        The matching :class:`ProviderSpec`.

    Raises:
        KeyError: If no provider with that id is registered.
    """
    try:
        return _BY_ID[provider_id]
    except KeyError as exc:  # pragma: no cover - re-raised with a clearer message
        raise KeyError(f"unknown provider id: {provider_id!r}") from exc


def by_kind(kind: ProviderKind, *, status: str | None = "mvp") -> list[ProviderSpec]:
    """Return all providers of a given kind, in registry order.

    Args:
        kind: The provider kind to filter on.
        status: Restrict to this status (``"mvp"`` by default); pass ``None`` for all.

    Returns:
        The matching provider specs.
    """
    return [s for s in REGISTRY if s.kind == kind and (status is None or s.status == status)]


def mvp_providers() -> list[ProviderSpec]:
    """Return every provider currently shipped (``status == "mvp"``)."""
    return [s for s in REGISTRY if s.status == "mvp"]


def vision_support(provider_id: str, model: str | None) -> bool | None:
    """True/False for a model in the provider's suggestion list, None for unknown ids/providers.

    The registry is a suggestion list, never an allowlist, so an admin may type any
    model id; for those the answer is ``None`` ("unknown") and callers decide how to
    degrade (DECISIONS-W2 §D-W2-10).

    Args:
        provider_id: A registry id such as ``"livekit-inference-llm"``.
        model: The configured model id; ``None`` resolves to the provider's ``default_model``.

    Returns:
        ``True`` if the model is flagged ``supports_video``, ``False`` if it is listed
        without the flag, ``None`` if the provider or the model id is not in the registry.
    """
    spec = _BY_ID.get(provider_id)
    if spec is None:
        return None
    model_id = model or spec.default_model
    if model_id is None:
        return None
    for candidate in spec.models:
        if candidate.id == model_id:
            return candidate.supports_video
    return None
