"""Provider registry: the catalogue of everything the platform can construct.

`REGISTRY` is the single source of truth for provider ids, constructor classes,
credential fields, configurable non-secret fields and suggested models. The api
validates `AgentConfig` against it, the agent's `ProviderFactory` builds plugin
objects from it, and the web console renders its forms from the exported
`generated/providers.json`.
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

ProviderKind = Literal[
    "realtime",
    "stt",
    "llm",
    "tts",
    "avatar",
    "vad",
    "turn_detection",
    "noise_cancellation",
    "image_gen",
    "embedding",
    "secret_bag",
]
FieldType = Literal["string", "secret", "number", "boolean", "enum", "json", "model", "file", "catalog"]

#: Whether the platform offers a provider at all (CONTRACTS-V2 §4.1).
#:
#: ``available`` — offered; ``deferred`` — catalogued but not shipped yet;
#: ``incompatible`` — cannot coexist with the pinned ``livekit-agents``;
#: ``removed`` — withdrawn by its vendor.
Availability = Literal["available", "deferred", "incompatible", "removed"]

#: Whether a provider has completed a live call on this platform.
Verification = Literal["verified", "unverified"]

#: Which worker image carries the provider's plugin.
WorkerImage = Literal["slim", "full", "isolated"]

#: What a vendor catalog adapter can list.
CatalogKind = Literal["models", "voices", "avatars", "personas"]

#: Gemini Live prebuilt voice names offered in the console.
#:
#: The full 30-name ``Voice`` literal from ``livekit.plugins.google.realtime``
#: (``realtime/api_proto.py``, confirmed exhaustive), not the v1 8-name curated
#: subset (docs/v2/_asks.md ask #8; CONTRACTS-V2 §4.1 requires the full list).
GEMINI_LIVE_VOICES: list[str] = [
    "Achernar",
    "Achird",
    "Algenib",
    "Algieba",
    "Alnilam",
    "Aoede",
    "Autonoe",
    "Callirrhoe",
    "Charon",
    "Despina",
    "Enceladus",
    "Erinome",
    "Fenrir",
    "Gacrux",
    "Iapetus",
    "Kore",
    "Laomedeia",
    "Leda",
    "Orus",
    "Pulcherrima",
    "Puck",
    "Rasalgethi",
    "Sadachbia",
    "Sadaltager",
    "Schedar",
    "Sulafat",
    "Umbriel",
    "Vindemiatrix",
    "Zephyr",
    "Zubenelgenubi",
]

#: xAI Grok Realtime voice names (``GrokVoices`` literal, confirmed exhaustive
#: at 26 entries in ``livekit.plugins.xai.realtime``).
XAI_VOICES: list[str] = [
    "carina",
    "zagan",
    "helix",
    "orion",
    "luna",
    "iris",
    "altair",
    "zenith",
    "perseus",
    "helios",
    "lux",
    "kepler",
    "rigel",
    "cosmo",
    "celeste",
    "ursa",
    "sirius",
    "lumen",
    "castor",
    "naksh",
    "atlas",
    "ara",
    "eve",
    "leo",
    "rex",
    "sal",
]

#: AWS Nova Sonic voice names, both generations combined (``SONIC1_VOICES`` +
#: ``SONIC2_VOICES``, confirmed via ``livekit.plugins.aws.experimental.realtime``).
NOVA_SONIC_VOICES: list[str] = [
    "matthew",
    "tiffany",
    "amy",
    "lupe",
    "carlos",
    "ambre",
    "florian",
    "tina",
    "lennart",
    "beatrice",
    "lorenzo",
    "olivia",
    "carolina",
    "leo",
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
    catalog_kind: CatalogKind | None = None
    accept: str | None = None
    positional: bool = False
    nested_model: str | None = None


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


class CatalogSpec(BaseModel):
    """How to list a provider's models, voices, avatars or personas from the vendor."""

    adapter: str
    kinds: list[CatalogKind] = []
    ttl_s: int = 3600


class ProviderCapabilities(BaseModel):
    """What a provider can do, used to gate UI affordances and runtime behaviour."""

    video_input: bool = False
    tool_calling: bool = True
    silent_tool_reply: bool = False
    voices: list[str] = []
    text_modality: bool = False
    audio_input: bool = True
    languages: list[str] = []
    vision: bool | None = None
    voices_dynamic: bool = False
    cloud_only: bool = False
    platforms: list[str] = []


class ProviderSpec(BaseModel):
    """Everything the platform needs to offer, configure and construct a provider.

    ``v`` accepts ``1`` for one release so v1 documents (a stored
    ``providers.json`` snapshot, console test fixtures) keep validating; the
    registry itself always emits ``2``.
    """

    v: Literal[1, 2] = 2
    id: str
    kind: ProviderKind
    label: str
    vendor: str
    status: Literal["mvp", "deferred"] = "mvp"
    """Read-only v1 alias, kept for one release; see :meth:`_derive_status`."""
    availability: Availability = "available"
    verification: Verification = "unverified"
    verified_at: str | None = None
    verified_note: str | None = None
    worker_image: WorkerImage = "full"
    catalog: CatalogSpec | None = None
    test: str | None = None
    price_ref: str | None = None
    notes: str | None = None
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
    credential_provider: str | None = Field(
        None,
        description=(
            "The registry id whose credential rows this provider uses (its credential home, R-V4-7). "
            "Unset means the provider is its own home. The home exists, has no home of its own and "
            "declares the same secret field names."
        ),
    )

    @model_validator(mode="after")
    def _derive_status(self) -> "ProviderSpec":
        """Recompute the v1 ``status`` alias (ruling R-V2-1, PLAN-V2 §8).

        ``status == "mvp"`` iff the provider is offered *and* the slim worker
        image can construct it. ``verification`` is an informational chip and
        never takes part: it gates no validation, seeding, picker or
        construction. Because every v1 MVP entry is ``worker_image="slim"`` and
        every entry V2-05 adds is ``"full"``, the alias keeps exactly the v1
        ``mvp`` set until V2-03/V2-13 migrate the consumers. Anything passed in
        for ``status`` is ignored.

        Returns:
            This spec, with ``status`` set from the v2 fields.
        """
        self.status = (
            "mvp" if self.availability == "available" and self.worker_image == "slim" else "deferred"
        )
        return self


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
    notes: str | None = None,
) -> ProviderSpec:
    """Build a catalogue-only entry shown in the console as "coming soon"."""
    return ProviderSpec(
        id=provider_id,
        kind=kind,
        label=label,
        vendor=vendor,
        availability="deferred",
        package=package,
        python_class=python_class,
        requires_credential=requires_credential,
        secret_fields=[_api_key()] if requires_credential else [],
        fields=fields or [],
        notes=notes,
    )


def _full(
    provider_id: str,
    kind: ProviderKind,
    label: str,
    vendor: str,
    package: str,
    python_class: str,
    *,
    availability: Availability = "available",
    requires_credential: bool = True,
    secret_fields: list[FieldSpec] | None = None,
    fields: list[FieldSpec] | None = None,
    models: list[ModelSpec] | None = None,
    default_model: str | None = None,
    capabilities: "ProviderCapabilities | None" = None,
    catalog: CatalogSpec | None = None,
    test: str | None = None,
    price_ref: str | None = None,
    notes: str | None = None,
    docs_url: str | None = None,
    get_key_url: str | None = None,
) -> ProviderSpec:
    """Build a full-breadth entry added by V2-05 (CONTRACTS-V2 §7, PLAN-V2 §8 R-V2-1).

    Every entry built here carries ``worker_image="full"`` (the field's own
    default) so the ``status`` alias stays ``"deferred"`` for all of them and
    the v1 ``mvp`` id set is untouched, regardless of ``availability``. Most
    calls leave ``availability`` at its default ``"available"``; the explicit
    corrections from the catalog (PlayAI, NVIDIA PersonaPlex, the two
    out-of-tree noise-cancellation packages with an unread class path) pass
    ``availability="deferred"``, and MiniMax passes ``"incompatible"``.

    Every ``secret_fields``/``fields`` name here was checked against the real
    1.8.2 source (either the installed venv or the AST snapshot in
    ``agent/tests/fixtures/plugin_signatures.json`` — see
    ``scripts/snapshot_plugin_signatures.py``), not copied from the catalog
    doc unread; the few real corrections that verification turned up over the
    catalog's draft (Cerebras's actual package, AWS Nova Sonic having no
    credential kwarg at all, Google STT/TTS using ADC not ``api_key``) are
    applied here, not left for a later package to discover.
    """
    return ProviderSpec(
        id=provider_id,
        kind=kind,
        label=label,
        vendor=vendor,
        availability=availability,
        package=package,
        python_class=python_class,
        requires_credential=requires_credential,
        secret_fields=secret_fields or [],
        fields=fields or [],
        models=models or [],
        default_model=default_model,
        capabilities=capabilities or ProviderCapabilities(),
        catalog=catalog,
        test=test,
        price_ref=price_ref,
        notes=notes,
        docs_url=docs_url,
        get_key_url=get_key_url,
    )


_AVAILABLE: list[ProviderSpec] = [
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
        capabilities=ProviderCapabilities(cloud_only=True),
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
        capabilities=ProviderCapabilities(cloud_only=True),
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
        capabilities=ProviderCapabilities(voices=["Ashley", "Brooke", "Cole", "Hana"], cloud_only=True),
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
                id="gemini-3.8-live-extended-thinking",
                label="Gemini 3.8 Live (extended thinking)",
                supports_video=True,
            ),
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
            ModelSpec(
                id="gemini-live-2.5-flash-native-audio",
                label="Gemini 2.5 Flash native audio (Vertex AI, GA)",
                supports_video=True,
                note="Vertex AI only, not the Gemini API.",
            ),
        ],
        default_model="gemini-3.8-live",
        capabilities=ProviderCapabilities(
            video_input=True,
            tool_calling=True,
            silent_tool_reply=True,
            voices=GEMINI_LIVE_VOICES,
            text_modality=True,
            languages=[],
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
        notes="Its base_url override is for OpenAI-compatible realtime endpoints; OpenRouter has none.",
        capabilities=ProviderCapabilities(
            video_input=False,
            tool_calling=True,
            silent_tool_reply=True,
            voices=["marin", "cedar", "alloy", "ash", "ballad", "coral", "sage", "verse"],
            text_modality=True,
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
        price_ref="deepgram-stt",
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
        catalog=CatalogSpec(adapter="openai_models", kinds=["models"]),
        test="openai_models",
        price_ref="openai-llm",
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
        price_ref="google-llm",
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
        catalog=CatalogSpec(adapter="cartesia_voices", kinds=["voices"]),
        test="cartesia_voices",
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
        catalog=CatalogSpec(adapter="elevenlabs_voices", kinds=["voices"]),
        test="elevenlabs_voices",
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
        price_ref="openai-tts",
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
        catalog=CatalogSpec(adapter="bey_avatars", kinds=["avatars"]),
        test="bey_avatars",
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
        catalog=CatalogSpec(adapter="tavus_faces_pals", kinds=["avatars", "personas"]),
        test="tavus_faces_pals",
        docs_url="https://docs.livekit.io/agents/integrations/avatar/tavus/",
        get_key_url="https://platform.tavus.io/api-keys",
    ),
    # Added to V2-05's full image; moved to the slim image on 2026-09-25 when the
    # user picked Beyond Presence and Simli as the platform's avatars.
    ProviderSpec(
        id="simli-avatar",
        kind="avatar",
        label="Simli",
        vendor="Simli",
        package="livekit-plugins-simli",
        python_class="livekit.plugins.simli.AvatarSession",
        secret_fields=[
            FieldSpec(
                name="simli_config.api_key",
                label="Simli API key",
                type="secret",
                required=True,
                nested_model="SimliConfig",
                help="Nested under simli_config: this avatar has no top-level api_key kwarg.",
            ),
        ],
        fields=[
            FieldSpec(
                name="simli_config.face_id",
                label="Face id",
                type="string",
                required=True,
                nested_model="SimliConfig",
            ),
            FieldSpec(
                name="simli_config.emotion_id", label="Emotion id", type="string", nested_model="SimliConfig"
            ),
        ],
        catalog=CatalogSpec(adapter="simli_faces", kinds=["avatars"]),
        test="simli_faces",
        capabilities=ProviderCapabilities(tool_calling=False),
        notes="No top-level api_key or conn_options; credential and face_id both live in the "
        "nested SimliConfig dataclass.",
        docs_url="https://docs.livekit.io/agents/integrations/avatar/simli/",
        get_key_url="https://www.simli.com/",
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


#: The only providers that have completed a live call on this platform
#: (RUNBOOK stages 0-9). Everything else stays ``unverified`` until V2-20.
VERIFIED_IDS: frozenset[str] = frozenset(
    {"livekit-inference-stt", "livekit-inference-llm", "livekit-inference-tts", "fastembed-embedding"}
)


def _shipped(spec: ProviderSpec) -> ProviderSpec:
    """Return a copy of ``spec`` as the v1 MVP set shipped it.

    The v1 plugin set *is* the slim worker image (CONTRACTS-V2 §7), so every
    entry gets ``worker_image="slim"`` — which is what the ``status`` alias
    derives from (R-V2-1). ``verification`` is set honestly: only the four
    providers in :data:`VERIFIED_IDS` have passed a live call, and the rest
    flip in V2-20 without changing ``status``.

    Args:
        spec: The available entry to mark.

    Returns:
        A revalidated copy (so the ``status`` alias is recomputed).
    """
    payload = {
        **spec.model_dump(exclude={"status"}),
        "worker_image": "slim",
        "verification": "verified" if spec.id in VERIFIED_IDS else "unverified",
    }
    return ProviderSpec.model_validate(payload)


#: The v1 MVP set, all of which the alias must still report as ``status="mvp"``.
_MVP: list[ProviderSpec] = [_shipped(spec) for spec in _AVAILABLE]


#: OpenRouter's public API root; every OpenRouter entry below defaults to it.
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

#: The one registry entry that holds the OpenRouter credential (R-V4-7).
OPENROUTER_CREDENTIAL_HOME = "openrouter-llm"


def _openrouter_key() -> FieldSpec:
    return _api_key("OpenRouter API key", env="OPENROUTER_API_KEY")


def _openrouter_base_url() -> FieldSpec:
    return FieldSpec(
        name="base_url",
        label="Base URL",
        type="string",
        default=OPENROUTER_BASE_URL,
        help="OpenRouter's OpenAI-compatible API root; change it only for a proxy in front of OpenRouter.",
    )


_OPENROUTER_KEY_URL = "https://openrouter.ai/settings/keys"

#: OpenRouter, one key for five slots (docs/v4/OPENROUTER.md D-V4-9…13, R-V4-7…9).
#:
#: Every entry is built from packages the slim image already carries
#: (`livekit-plugins-openai` and the `openai` SDK), so the five ship through
#: :func:`_shipped` like the v1 set. The four non-LLM entries name
#: ``openrouter-llm`` as their credential home, so one stored key serves all
#: five. There is deliberately no ``realtime`` entry: OpenRouter has no
#: speech-to-speech or websocket endpoint (R-V4-8).
_OPENROUTER_AVAILABLE: list[ProviderSpec] = [
    ProviderSpec(
        id="openrouter-llm",
        kind="llm",
        label="OpenRouter",
        vendor="OpenRouter",
        package="livekit-plugins-openai",
        python_class="livekit.plugins.openai.LLM.with_openrouter",
        secret_fields=[_openrouter_key()],
        fields=[
            FieldSpec(name="temperature", label="Temperature", type="number", default=0.7),
            FieldSpec(
                name="fallback_models",
                label="Fallback models",
                type="json",
                placeholder='["openai/gpt-4o-mini"]',
                help="Model ids tried in order when the primary is unavailable; sent as OpenRouter's "
                "`models` array.",
            ),
            FieldSpec(
                name="provider",
                label="Provider preferences",
                type="json",
                placeholder='{"sort": "latency"}',
                help="OpenRouter provider preferences: `order`, `only`, `ignore`, `sort` "
                "(price/throughput/latency), `allow_fallbacks`, `require_parameters`, "
                "`data_collection`, `preferred_max_latency`…; `require_parameters` defaults to true so "
                "a request with tools never lands on an endpoint that cannot call them.",
            ),
            FieldSpec(
                name="site_url",
                label="Site URL",
                type="string",
                help="Sent as `HTTP-Referer` for OpenRouter's app rankings; the app name below only "
                "counts when this is set.",
            ),
            FieldSpec(
                name="app_name",
                label="App name",
                type="string",
                default="LKAP",
                help="Sent as `X-Title` for OpenRouter's app attribution.",
            ),
        ],
        models=[
            ModelSpec(id="openai/gpt-4.1-mini", label="GPT-4.1 mini"),
            ModelSpec(id="openai/gpt-4.1", label="GPT-4.1"),
            ModelSpec(id="openai/gpt-4o-mini", label="GPT-4o mini"),
            ModelSpec(id="google/gemini-3.5-flash", label="Gemini 3.5 Flash"),
            ModelSpec(id="anthropic/claude-sonnet-4.6", label="Claude Sonnet 4.6"),
        ],
        default_model="openai/gpt-4.1-mini",
        catalog=CatalogSpec(adapter="openrouter_llm_models", kinds=["models"]),
        test="openrouter_llm_models",
        notes="Routes to hundreds of models on one key; `openrouter/auto` is not tool-safe and is "
        "deliberately not the default. Tool schemas go out with OpenAI's `strict` flag; if a routed "
        "non-OpenAI model rejects a tool call, pick an OpenAI model or an `order` of providers known "
        "to support strict tools.",
        docs_url="https://docs.livekit.io/agents/models/llm/openrouter/",
        get_key_url=_OPENROUTER_KEY_URL,
    ),
    ProviderSpec(
        id="openrouter-stt",
        kind="stt",
        label="OpenRouter (STT)",
        vendor="OpenRouter",
        package="livekit-plugins-openai",
        python_class="livekit.plugins.openai.STT",
        credential_provider=OPENROUTER_CREDENTIAL_HOME,
        secret_fields=[_openrouter_key()],
        fields=[
            _openrouter_base_url(),
            FieldSpec(name="language", label="Language", type="string", default="en"),
        ],
        models=[
            ModelSpec(id="openai/gpt-4o-mini-transcribe", label="GPT-4o mini Transcribe"),
            ModelSpec(id="openai/whisper-large-v3-turbo", label="Whisper large v3 turbo"),
            ModelSpec(id="openai/gpt-4o-transcribe", label="GPT-4o Transcribe"),
            ModelSpec(id="deepgram/nova-3", label="Deepgram Nova 3 (batch)"),
            ModelSpec(id="mistralai/voxtral-mini-transcribe", label="Voxtral Mini Transcribe"),
        ],
        default_model="openai/gpt-4o-mini-transcribe",
        catalog=CatalogSpec(adapter="openrouter_stt_models", kinds=["models"]),
        test="openrouter_stt_models",
        notes="Batch transcription over HTTP: no interim results; each turn is transcribed after "
        "end-of-speech, so expect roughly half a second to two seconds more per turn than a streaming "
        "STT. For low latency prefer LiveKit Inference STT or Deepgram.",
        docs_url="https://docs.livekit.io/agents/models/stt/openai/",
        get_key_url=_OPENROUTER_KEY_URL,
    ),
    ProviderSpec(
        id="openrouter-tts",
        kind="tts",
        label="OpenRouter (TTS)",
        vendor="OpenRouter",
        package="livekit-plugins-openai",
        python_class="livekit.plugins.openai.TTS",
        credential_provider=OPENROUTER_CREDENTIAL_HOME,
        secret_fields=[_openrouter_key()],
        fields=[
            _openrouter_base_url(),
            FieldSpec(
                name="voice",
                label="Voice",
                type="catalog",
                catalog_kind="voices",
                default="Kore",
                help="Voices are per model: pick the model first, then a voice it lists.",
            ),
            FieldSpec(name="speed", label="Speed", type="number", default=1.0),
        ],
        models=[
            ModelSpec(id="google/gemini-3.8-flash-tts", label="Gemini 3.8 Flash TTS"),
            ModelSpec(id="google/gemini-3.8-flash-lite-tts", label="Gemini 3.8 Flash Lite TTS"),
            ModelSpec(id="deepgram/aura-2", label="Deepgram Aura 2"),
            ModelSpec(id="mistralai/voxtral-mini-tts-2603", label="Voxtral Mini TTS"),
            ModelSpec(id="x-ai/grok-voice-tts-1.0", label="Grok Voice TTS 1.0"),
        ],
        default_model="google/gemini-3.8-flash-tts",
        capabilities=ProviderCapabilities(voices_dynamic=True),
        catalog=CatalogSpec(adapter="openrouter_tts_models", kinds=["models", "voices"]),
        test="openrouter_tts_models",
        notes="Voices are per model — pick the model first, then a voice it lists. Non-streaming, like "
        "OpenAI TTS: one request per sentence.",
        docs_url="https://docs.livekit.io/agents/models/tts/openai/",
        get_key_url=_OPENROUTER_KEY_URL,
    ),
    ProviderSpec(
        id="openrouter-embedding",
        kind="embedding",
        label="OpenRouter embeddings",
        vendor="OpenRouter",
        package="openai",
        python_class="lkap_api.kb.embed.OpenAIEmbedder",
        credential_provider=OPENROUTER_CREDENTIAL_HOME,
        secret_fields=[_openrouter_key()],
        fields=[_openrouter_base_url()],
        models=[ModelSpec(id="openai/text-embedding-3-small", label="text-embedding-3-small")],
        default_model="openai/text-embedding-3-small",
        capabilities=ProviderCapabilities(tool_calling=False),
        catalog=CatalogSpec(adapter="openrouter_embedding_models", kinds=["models"]),
        test="openrouter_embedding_models",
        notes="Platform-level: select it with LKAP_EMBEDDER=openrouter-embedding:<credential_id>. Only "
        "the 1536-dimension model is offered; another model would mean re-embedding every knowledge "
        "base.",
        get_key_url=_OPENROUTER_KEY_URL,
    ),
    ProviderSpec(
        id="openrouter-image-gen",
        kind="image_gen",
        label="OpenRouter image generation",
        vendor="OpenRouter",
        package="openai",
        python_class="lkap_agent.providers.image_gen.OpenRouterImageGen",
        credential_provider=OPENROUTER_CREDENTIAL_HOME,
        secret_fields=[_openrouter_key()],
        fields=[
            FieldSpec(
                name="resolution",
                label="Resolution",
                type="enum",
                options=["512", "1K", "2K", "4K"],
                default="1K",
            ),
            FieldSpec(name="aspect_ratio", label="Aspect ratio", type="string", default="1:1"),
        ],
        models=[
            ModelSpec(id="openai/gpt-image-1", label="GPT Image 1"),
            ModelSpec(id="openai/gpt-image-1-mini", label="GPT Image 1 mini"),
            ModelSpec(id="google/gemini-3.1-flash-image", label="Gemini 3.1 Flash Image"),
        ],
        default_model="openai/gpt-image-1",
        capabilities=ProviderCapabilities(tool_calling=False),
        catalog=CatalogSpec(adapter="openrouter_image_models", kinds=["models"]),
        test="openrouter_image_models",
        get_key_url=_OPENROUTER_KEY_URL,
    ),
]

#: The OpenRouter entries as shipped on the slim image.
_OPENROUTER: list[ProviderSpec] = [_shipped(spec) for spec in _OPENROUTER_AVAILABLE]


#: The v1 catalogue-only entries (25 until ``simli-avatar`` moved to the slim
#: image in v4, 24 now), promoted to full availability and
#: enriched with source-verified fields (PLAN-V2 V2-05 card: "Add every entry
#: from the catalog"). Every one keeps ``worker_image="full"`` (the
#: ``ProviderSpec`` default), so promoting them changes nothing about the
#: ``status`` alias or the v1 ``mvp`` id set (R-V2-1) — they were never in
#: ``_MVP`` to begin with.
#:
#: Corrections this pass made over the v1 stub and the catalog's own draft,
#: both caught by checking the real 1.8.2 signature (never left as "close
#: enough"): ``cerebras-llm`` had the wrong ``python_class`` entirely (a
#: nonexistent ``openai.LLM.with_cerebras``; Cerebras is its own package with
#: its own ``LLM`` class); ``google-stt``/``google-tts`` have **no** ``api_key``
#: kwarg at all (ADC ``credentials_file``/``credentials_info`` only — modelled
#: as a ``type="file"`` field, not ``secret_fields``, since the field type
#: isn't ``"secret"``); ``azure-stt``/``azure-tts`` authenticate with
#: ``speech_key``, not ``api_key``.
_FULL: list[ProviderSpec] = [
    # ---------------------------------------------------------------- realtime
    _full(
        "azure-openai-realtime",
        "realtime",
        "Azure OpenAI Realtime",
        "Microsoft",
        "livekit-plugins-openai",
        "livekit.plugins.openai.realtime.RealtimeModel.with_azure",
        secret_fields=[
            _api_key("Azure API key", help_text="Or leave blank and use azure_ad_token / entra_token."),
        ],
        fields=[
            FieldSpec(name="azure_deployment", label="Azure deployment", type="string", required=True),
            FieldSpec(name="azure_endpoint", label="Azure endpoint", type="string", required=True),
            FieldSpec(name="api_version", label="API version", type="string"),
            FieldSpec(name="voice", label="Voice", type="string", default="marin"),
        ],
        capabilities=ProviderCapabilities(video_input=False, tool_calling=True, text_modality=True),
        notes="Same RealtimeModel class as OpenAI Realtime via its with_azure() classmethod overload.",
        docs_url="https://docs.livekit.io/agents/models/realtime/openai/",
    ),
    _full(
        "xai-realtime",
        "realtime",
        "xAI Grok Voice",
        "xAI",
        "livekit-plugins-xai",
        "livekit.plugins.xai.realtime.RealtimeModel",
        secret_fields=[_api_key("xAI API key", env="XAI_API_KEY")],
        fields=[
            FieldSpec(name="voice", label="Voice", type="enum", default="ara", options=XAI_VOICES),
            FieldSpec(name="max_session_duration", label="Max session duration (s)", type="number"),
        ],
        models=[
            ModelSpec(id="grok-voice-latest", label="Grok Voice (latest)"),
            ModelSpec(id="grok-voice-think-fast-2.0", label="Grok Voice Think Fast 2.0"),
            ModelSpec(id="grok-voice-think-fast-1.0", label="Grok Voice Think Fast 1.0"),
            ModelSpec(id="grok-voice-fast-1.0", label="Grok Voice Fast 1.0"),
        ],
        default_model="grok-voice-latest",
        capabilities=ProviderCapabilities(video_input=False, tool_calling=True, voices=XAI_VOICES),
        notes="Subclasses openai.realtime.RealtimeModel; audio-native only, no modalities/text-only mode.",
        docs_url="https://docs.livekit.io/agents/models/realtime/",
    ),
    # ---------------------------------------------------------------- STT
    _full(
        "assemblyai-stt",
        "stt",
        "AssemblyAI",
        "AssemblyAI",
        "livekit-plugins-assemblyai",
        "livekit.plugins.assemblyai.STT",
        secret_fields=[_api_key("AssemblyAI API key", env="ASSEMBLYAI_API_KEY")],
        fields=[
            FieldSpec(name="model", label="Model", type="string", default="universal-3-5-pro"),
            FieldSpec(name="language_code", label="Language code", type="string"),
            FieldSpec(name="speaker_labels", label="Speaker labels", type="boolean", default=False),
        ],
        docs_url="https://docs.livekit.io/agents/models/stt/",
    ),
    _full(
        "google-stt",
        "stt",
        "Google Speech-to-Text",
        "Google",
        "livekit-plugins-google",
        "livekit.plugins.google.STT",
        requires_credential=True,
        fields=[
            FieldSpec(
                name="credentials_file",
                label="Service account JSON",
                type="file",
                accept="application/json",
                required=True,
                help="No api_key kwarg exists on this class; Google auth is ADC only.",
            ),
            FieldSpec(name="languages", label="Language", type="string", default="en-US"),
            FieldSpec(name="model", label="Model", type="string", default="latest_long"),
        ],
        docs_url="https://docs.livekit.io/agents/models/stt/google/",
    ),
    _full(
        "openai-stt",
        "stt",
        "OpenAI Whisper",
        "OpenAI",
        "livekit-plugins-openai",
        "livekit.plugins.openai.STT",
        secret_fields=[_api_key("OpenAI API key", env="OPENAI_API_KEY")],
        fields=[FieldSpec(name="model", label="Model", type="string", default="gpt-4o-mini-transcribe")],
        catalog=CatalogSpec(adapter="openai_models", kinds=["models"]),
        test="openai_models",
        docs_url="https://docs.livekit.io/agents/models/stt/openai/",
    ),
    _full(
        "speechmatics-stt",
        "stt",
        "Speechmatics",
        "Speechmatics",
        "livekit-plugins-speechmatics",
        "livekit.plugins.speechmatics.STT",
        secret_fields=[_api_key("Speechmatics API key", env="SPEECHMATICS_API_KEY")],
        fields=[FieldSpec(name="language", label="Language", type="string", default="en")],
        docs_url="https://docs.livekit.io/agents/models/stt/",
    ),
    _full(
        "elevenlabs-stt",
        "stt",
        "ElevenLabs Scribe",
        "ElevenLabs",
        "livekit-plugins-elevenlabs",
        "livekit.plugins.elevenlabs.STT",
        secret_fields=[_api_key("ElevenLabs API key", env="ELEVEN_API_KEY")],
        fields=[FieldSpec(name="language_code", label="Language code", type="string")],
        docs_url="https://docs.livekit.io/agents/models/stt/elevenlabs/",
    ),
    _full(
        "cartesia-stt",
        "stt",
        "Cartesia Ink",
        "Cartesia",
        "livekit-plugins-cartesia",
        "livekit.plugins.cartesia.STT",
        secret_fields=[_api_key("Cartesia API key", env="CARTESIA_API_KEY")],
        fields=[FieldSpec(name="language", label="Language", type="string", default="en")],
        docs_url="https://docs.livekit.io/agents/models/stt/cartesia/",
    ),
    _full(
        "groq-stt",
        "stt",
        "Groq Whisper",
        "Groq",
        "livekit-plugins-groq",
        "livekit.plugins.groq.STT",
        secret_fields=[_api_key("Groq API key", env="GROQ_API_KEY")],
        fields=[FieldSpec(name="model", label="Model", type="string", default="whisper-large-v3-turbo")],
        catalog=CatalogSpec(adapter="groq_models", kinds=["models"]),
        test="groq_models",
        docs_url="https://docs.livekit.io/agents/models/stt/groq/",
    ),
    _full(
        "azure-stt",
        "stt",
        "Azure Speech",
        "Microsoft",
        "livekit-plugins-azure",
        "livekit.plugins.azure.STT",
        secret_fields=[FieldSpec(name="speech_key", label="Speech key", type="secret", required=True)],
        fields=[
            FieldSpec(name="speech_region", label="Speech region", type="string", required=True),
            FieldSpec(name="language", label="Language", type="string"),
        ],
        docs_url="https://docs.livekit.io/agents/models/stt/azure/",
    ),
    # ---------------------------------------------------------------- LLM
    _full(
        "anthropic-llm",
        "llm",
        "Anthropic Claude",
        "Anthropic",
        "livekit-plugins-anthropic",
        "livekit.plugins.anthropic.LLM",
        secret_fields=[_api_key("Anthropic API key", env="ANTHROPIC_API_KEY")],
        fields=[FieldSpec(name="max_tokens", label="Max tokens", type="number")],
        models=[
            ModelSpec(id="claude-sonnet-4-6", label="Claude Sonnet 4.6"),
            ModelSpec(id="claude-opus-4-6", label="Claude Opus 4.6"),
            ModelSpec(id="claude-3-5-haiku-20241022", label="Claude 3.5 Haiku"),
        ],
        default_model="claude-sonnet-4-6",
        catalog=CatalogSpec(adapter="anthropic_models", kinds=["models"]),
        test="anthropic_models",
        docs_url="https://docs.livekit.io/agents/models/llm/anthropic/",
    ),
    _full(
        "groq-llm",
        "llm",
        "Groq",
        "Groq",
        "livekit-plugins-groq",
        "livekit.plugins.groq.LLM",
        secret_fields=[_api_key("Groq API key", env="GROQ_API_KEY")],
        models=[
            ModelSpec(id="llama-3.3-70b-versatile", label="Llama 3.3 70B Versatile"),
            ModelSpec(id="openai/gpt-oss-120b", label="GPT-OSS 120B"),
        ],
        default_model="llama-3.3-70b-versatile",
        catalog=CatalogSpec(adapter="groq_models", kinds=["models"]),
        test="groq_models",
        docs_url="https://docs.livekit.io/agents/models/llm/groq/",
    ),
    _full(
        "cerebras-llm",
        "llm",
        "Cerebras",
        "Cerebras",
        "livekit-plugins-cerebras",
        "livekit.plugins.cerebras.LLM",
        secret_fields=[_api_key("Cerebras API key", env="CEREBRAS_API_KEY")],
        models=[ModelSpec(id="gpt-oss-120b", label="GPT-OSS 120B")],
        default_model="gpt-oss-120b",
        notes="v1's stub pointed at a nonexistent openai.LLM.with_cerebras; corrected to the real package.",
        docs_url="https://docs.livekit.io/agents/models/llm/",
    ),
    _full(
        "openai-compatible-llm",
        "llm",
        "OpenAI-compatible endpoint",
        "Various",
        "livekit-plugins-openai",
        "livekit.plugins.openai.LLM",
        secret_fields=[
            _api_key("API key", help_text="A dummy string is accepted by unauthenticated local endpoints.")
        ],
        fields=[FieldSpec(name="base_url", label="Base URL", type="string", required=True)],
        docs_url="https://docs.livekit.io/agents/models/llm/openai/",
    ),
    _full(
        "aws-bedrock-llm",
        "llm",
        "AWS Bedrock",
        "Amazon",
        "livekit-plugins-aws",
        "livekit.plugins.aws.LLM",
        secret_fields=[
            FieldSpec(
                name="api_key", label="AWS access key id", type="secret", env_fallback="AWS_ACCESS_KEY_ID"
            ),
            FieldSpec(
                name="api_secret",
                label="AWS secret access key",
                type="secret",
                env_fallback="AWS_SECRET_ACCESS_KEY",
            ),
        ],
        requires_credential=False,
        fields=[FieldSpec(name="region", label="Region", type="string", default="us-east-1")],
        models=[ModelSpec(id="amazon.nova-2-lite-v1:0", label="Nova 2 Lite")],
        default_model="amazon.nova-2-lite-v1:0",
        notes="Flat api_key/api_secret only this plugin family (+ aws.TTS) accepts; falls back to "
        "the standard AWS chain when omitted.",
        docs_url="https://docs.livekit.io/agents/models/llm/aws/",
    ),
    # ---------------------------------------------------------------- TTS
    _full(
        "google-tts",
        "tts",
        "Google Cloud TTS",
        "Google",
        "livekit-plugins-google",
        "livekit.plugins.google.TTS",
        fields=[
            FieldSpec(
                name="credentials_file",
                label="Service account JSON",
                type="file",
                accept="application/json",
                required=True,
                help="No api_key kwarg exists on this class; Google auth is ADC only.",
            ),
            FieldSpec(name="voice_name", label="Voice", type="string"),
            FieldSpec(name="language", label="Language", type="string", default="en-US"),
        ],
        docs_url="https://docs.livekit.io/agents/models/tts/google/",
    ),
    _full(
        "deepgram-tts",
        "tts",
        "Deepgram Aura",
        "Deepgram",
        "livekit-plugins-deepgram",
        "livekit.plugins.deepgram.TTS",
        secret_fields=[_api_key("Deepgram API key", env="DEEPGRAM_API_KEY")],
        models=[ModelSpec(id="aura-2-andromeda-en", label="Aura 2 Andromeda (en)")],
        default_model="aura-2-andromeda-en",
        price_ref="deepgram-tts",
        docs_url="https://docs.livekit.io/agents/models/tts/deepgram/",
    ),
    _full(
        "rime-tts",
        "tts",
        "Rime",
        "Rime",
        "livekit-plugins-rime",
        "livekit.plugins.rime.TTS",
        secret_fields=[_api_key("Rime API key", env="RIME_API_KEY")],
        fields=[
            FieldSpec(name="speaker", label="Speaker", type="string"),
            FieldSpec(name="lang", label="Language", type="string", default="eng"),
        ],
        models=[ModelSpec(id="mistv3", label="Mist v3")],
        default_model="mistv3",
        docs_url="https://docs.livekit.io/agents/models/tts/rime/",
    ),
    _full(
        "inworld-tts",
        "tts",
        "Inworld",
        "Inworld",
        "livekit-plugins-inworld",
        "livekit.plugins.inworld.TTS",
        secret_fields=[_api_key("Inworld API key", env="INWORLD_API_KEY")],
        fields=[FieldSpec(name="voice", label="Voice", type="string", default="Ashley")],
        models=[ModelSpec(id="inworld-tts-1.5-max", label="Inworld TTS 1.5 Max")],
        default_model="inworld-tts-1.5-max",
        docs_url="https://docs.livekit.io/agents/models/tts/inworld/",
    ),
    _full(
        "hume-tts",
        "tts",
        "Hume Octave",
        "Hume",
        "livekit-plugins-hume",
        "livekit.plugins.hume.TTS",
        secret_fields=[_api_key("Hume API key", env="HUME_API_KEY")],
        catalog=CatalogSpec(adapter="hume_voices", kinds=["voices"]),
        test="hume_voices",
        docs_url="https://docs.livekit.io/agents/models/tts/hume/",
    ),
    _full(
        "azure-tts",
        "tts",
        "Azure Speech TTS",
        "Microsoft",
        "livekit-plugins-azure",
        "livekit.plugins.azure.TTS",
        secret_fields=[FieldSpec(name="speech_key", label="Speech key", type="secret", required=True)],
        fields=[
            FieldSpec(name="speech_region", label="Speech region", type="string", required=True),
            FieldSpec(name="voice", label="Voice", type="string", default="en-US-JennyNeural"),
        ],
        docs_url="https://docs.livekit.io/agents/models/tts/azure/",
    ),
    # ---------------------------------------------------------------- avatars
    # `simli-avatar` moved to the slim image (`_AVAILABLE`) by the user's
    # avatar choice (Beyond Presence + Simli, 2026-09-25).
    _full(
        "anam-avatar",
        "avatar",
        "Anam",
        "Anam",
        "livekit-plugins-anam",
        "livekit.plugins.anam.AvatarSession",
        secret_fields=[_api_key("Anam API key", env="ANAM_API_KEY")],
        fields=[
            FieldSpec(
                name="persona_config.avatarId",
                label="Avatar id",
                type="string",
                required=True,
                nested_model="PersonaConfig",
            ),
            FieldSpec(
                name="persona_config.name", label="Persona name", type="string", nested_model="PersonaConfig"
            ),
        ],
        catalog=CatalogSpec(adapter="anam_avatars", kinds=["avatars", "personas"]),
        test="anam_avatars",
        capabilities=ProviderCapabilities(tool_calling=False),
        docs_url="https://docs.livekit.io/agents/integrations/avatar/anam/",
        get_key_url="https://anam.ai/",
    ),
    _full(
        "bithuman-avatar",
        "avatar",
        "bitHuman",
        "bitHuman",
        "livekit-plugins-bithuman",
        "livekit.plugins.bithuman.AvatarSession",
        secret_fields=[
            FieldSpec(name="api_secret", label="bitHuman API secret", type="secret", required=True)
        ],
        fields=[
            FieldSpec(name="api_token", label="API token", type="string"),
            FieldSpec(
                name="model",
                label="Runtime model",
                type="enum",
                default="essence",
                options=["expression", "essence"],
            ),
            FieldSpec(name="avatar_id", label="Avatar id", type="string"),
            FieldSpec(name="avatar_image", label="Avatar image", type="file", accept="image/*"),
        ],
        capabilities=ProviderCapabilities(
            tool_calling=False, platforms=["macos-arm64", "linux-aarch64", "linux-x86_64"]
        ),
        notes="No Windows wheel; manylinux_2_28 or macOS arm64 only. Also has a genuine "
        "offline/local mode outside the worker image.",
        docs_url="https://docs.livekit.io/agents/integrations/avatar/bithuman/",
        get_key_url="https://www.bithuman.ai/",
    ),
    _full(
        "liveavatar-avatar",
        "avatar",
        "LiveAvatar",
        "LiveAvatar",
        "livekit-plugins-liveavatar",
        "livekit.plugins.liveavatar.AvatarSession",
        secret_fields=[_api_key("LiveAvatar API key", env="LIVEAVATAR_API_KEY")],
        fields=[
            FieldSpec(
                name="avatar_id", label="Avatar id", type="string", env_fallback="LIVEAVATAR_AVATAR_ID"
            ),
            FieldSpec(
                name="video_quality",
                label="Video quality",
                type="enum",
                default="high",
                options=["very_high", "high", "medium", "low"],
            ),
        ],
        catalog=CatalogSpec(adapter="liveavatar_avatars", kinds=["avatars"]),
        test="liveavatar_avatars",
        capabilities=ProviderCapabilities(tool_calling=False),
        docs_url="https://docs.livekit.io/agents/integrations/avatar/",
        get_key_url="https://www.liveavatar.com/",
    ),
]


#: Entries with no v1 catalogue stub at all: the ~70 packages the research-v2
#: catalog (`docs/research-v2/livekit-plugins-catalog.md` §5) found beyond
#: what v1 already listed, plus the three new kinds (`vad`, `turn_detection`,
#: `noise_cancellation`). Same availability/verification rules as `_FULL`.
_NEW: list[ProviderSpec] = [
    # ---------------------------------------------------------------- VAD (new kind)
    _full(
        "silero-vad",
        "vad",
        "Silero (local)",
        "Silero",
        "livekit-plugins-silero",
        "livekit.plugins.silero.VAD.load",
        requires_credential=False,
        fields=[FieldSpec(name="min_speech_duration", label="Min speech duration (s)", type="number")],
        models=[ModelSpec(id="silero", label="Silero (local ONNX)")],
        default_model="silero",
        notes="Constructed via the VAD.load(...) classmethod factory, not __init__ directly.",
        docs_url="https://docs.livekit.io/agents/build/turns/vad/",
    ),
    _full(
        "inference-vad",
        "vad",
        "LiveKit Inference (VAD)",
        "LiveKit",
        "livekit-agents",
        "livekit.agents.inference.VAD",
        requires_credential=False,
        models=[ModelSpec(id="silero", label="Silero (LiveKit-hosted native inference)")],
        default_model="silero",
        capabilities=ProviderCapabilities(cloud_only=True),
        docs_url="https://docs.livekit.io/agents/build/turns/vad/",
    ),
    # ---------------------------------------------------------------- turn detection (new kind)
    _full(
        "turn-detector-plugin",
        "turn_detection",
        "Turn Detector (local)",
        "LiveKit",
        "livekit-plugins-turn-detector",
        "livekit.plugins.turn_detector.multilingual.MultilingualModel",
        requires_credential=False,
        fields=[FieldSpec(name="unlikely_threshold", label="Unlikely threshold", type="number")],
        notes="livekit.plugins.turn_detector.english.EnglishModel is the English-only sibling class.",
        docs_url="https://docs.livekit.io/agents/build/turns/turn-detector/",
    ),
    _full(
        "inference-turn-detector",
        "turn_detection",
        "LiveKit Inference (Turn Detector)",
        "LiveKit",
        "livekit-agents",
        "livekit.agents.inference.eot.TurnDetector",
        requires_credential=False,
        fields=[
            FieldSpec(name="version", label="Version", type="enum", options=["v1", "v1-mini"], default="v1"),
            FieldSpec(
                name="local_fallback",
                label="Fall back to local mini on gateway failure",
                type="boolean",
                default=True,
            ),
        ],
        capabilities=ProviderCapabilities(cloud_only=True),
        docs_url="https://docs.livekit.io/agents/build/turns/turn-detector/",
    ),
    # ---------------------------------------------------------------- noise cancellation (new kind)
    _full(
        "krisp-noise-cancellation",
        "noise_cancellation",
        "Krisp",
        "Krisp",
        "livekit-plugins-krisp",
        "livekit.plugins.krisp.KrispVivaFilterFrameProcessor",
        availability="deferred",
        requires_credential=True,
        fields=[
            FieldSpec(
                name="auth_provider",
                label="Krisp license or LiveKit Cloud auth",
                type="json",
                required=True,
                help="A KrispLicenseAuthProvider (self-hosted license key) or LiveKitCloudAuthProvider "
                "(routes billing through the bound LiveKit Cloud project, no separate vendor key) — not a "
                "single secret string, so it is modelled as a structured field rather than secret_fields.",
            ),
            FieldSpec(
                name="mode",
                label="Mode",
                type="enum",
                options=["noise_cancellation", "background_voice_cancellation"],
            ),
            FieldSpec(name="noise_suppression_level", label="Noise suppression level", type="number"),
        ],
        capabilities=ProviderCapabilities(cloud_only=True, platforms=["linux-x86_64", "linux-aarch64"]),
        notes="Deferred (asks #56): livekit-plugins-krisp has no 1.8.2 release on PyPI (it is versioned "
        "independently, latest 0.4.2), so the lockstep ==1.8.2 pin of CONTRACTS-V2 §7 cannot install, "
        "and 0.4.2 is unverified against the 1.8.2 plugin ABI. Ships a native "
        "livekit-plugins-krisp-internal wheel. Re-enable after an import check of a compatible release "
        "in the full image.",
        docs_url="https://docs.livekit.io/agents/build/audio/#noise-cancellation",
    ),
    # ---------------------------------------------------------------- avatars (new)
    _full(
        "avatario-avatar",
        "avatar",
        "Avatario",
        "Avatario",
        "livekit-plugins-avatario",
        "livekit.plugins.avatario.AvatarSession",
        secret_fields=[_api_key("Avatario API key", env="AVATARIO_API_KEY")],
        fields=[
            FieldSpec(
                name="avatar_id",
                label="Avatar id",
                type="string",
                required=True,
                env_fallback="AVATARIO_AVATAR_ID",
            )
        ],
        capabilities=ProviderCapabilities(tool_calling=False),
        docs_url="https://docs.livekit.io/agents/integrations/avatar/",
    ),
    _full(
        "avatartalk-avatar",
        "avatar",
        "AvatarTalk",
        "AvatarTalk",
        "livekit-plugins-avatartalk",
        "livekit.plugins.avatartalk.AvatarSession",
        secret_fields=[
            FieldSpec(
                name="api_secret",
                label="AvatarTalk API secret",
                type="secret",
                required=True,
                env_fallback="AVATARTALK_API_SECRET",
            )
        ],
        fields=[
            FieldSpec(name="avatar", label="Avatar", type="string", default="japanese_man"),
            FieldSpec(name="emotion", label="Emotion", type="string", default="expressive"),
        ],
        capabilities=ProviderCapabilities(tool_calling=False, platforms=["linux-x86_64", "macos-arm64"]),
        notes="Shortest ctor of the 16 in-tree avatars (no conn_options). Also has a genuine "
        "on-prem/on-device mode.",
        docs_url="https://docs.livekit.io/agents/integrations/avatar/",
    ),
    _full(
        "did-avatar",
        "avatar",
        "D-ID",
        "D-ID",
        "livekit-plugins-did",
        "livekit.plugins.did.AvatarSession",
        secret_fields=[_api_key("D-ID API key", env="DID_API_KEY")],
        fields=[
            FieldSpec(
                name="agent_id",
                label="Agent id",
                type="string",
                required=True,
                positional=False,
                help="No default — D-ID has no stock avatar.",
            )
        ],
        capabilities=ProviderCapabilities(tool_calling=False),
        notes="agent_id has no NotGivenOr wrapper, unlike every other avatar's id field: a "
        "true required kwarg.",
        docs_url="https://docs.livekit.io/agents/integrations/avatar/",
    ),
    _full(
        "keyframe-avatar",
        "avatar",
        "Keyframe",
        "Keyframe",
        "livekit-plugins-keyframe",
        "livekit.plugins.keyframe.AvatarSession",
        secret_fields=[_api_key("Keyframe API key", env="KEYFRAME_API_KEY")],
        fields=[
            FieldSpec(name="persona_id", label="Persona id", type="string"),
            FieldSpec(
                name="persona_slug",
                label="Persona slug",
                type="string",
                placeholder="public:cosmo_persona-1.5-live",
            ),
        ],
        capabilities=ProviderCapabilities(tool_calling=False),
        notes="Exactly one of persona_id/persona_slug is required.",
        docs_url="https://docs.livekit.io/agents/integrations/avatar/",
    ),
    _full(
        "lemonslice-avatar",
        "avatar",
        "LemonSlice",
        "LemonSlice",
        "livekit-plugins-lemonslice",
        "livekit.plugins.lemonslice.AvatarSession",
        secret_fields=[_api_key("LemonSlice API key", env="LEMONSLICE_API_KEY")],
        fields=[
            FieldSpec(name="agent_id", label="Agent id", type="string"),
            FieldSpec(name="agent_image_url", label="Agent image URL", type="string"),
            FieldSpec(name="agent_image", label="Agent image", type="file", accept="image/*"),
        ],
        capabilities=ProviderCapabilities(tool_calling=False),
        notes="Exactly one of agent_id/agent_image_url/agent_image is required; ctor also "
        "accepts **kwargs passthrough.",
        docs_url="https://docs.livekit.io/agents/integrations/avatar/",
    ),
    _full(
        "protoface-avatar",
        "avatar",
        "Protoface",
        "Protoface",
        "livekit-plugins-protoface",
        "livekit.plugins.protoface.AvatarSession",
        secret_fields=[_api_key("Protoface API key", env="PROTOFACE_API_KEY")],
        fields=[FieldSpec(name="avatar_id", label="Avatar id", type="string", default="av_stock_001")],
        capabilities=ProviderCapabilities(tool_calling=False),
        docs_url="https://docs.livekit.io/agents/integrations/avatar/",
    ),
    _full(
        "runway-avatar",
        "avatar",
        "Runway",
        "Runway",
        "livekit-plugins-runway",
        "livekit.plugins.runway.AvatarSession",
        secret_fields=[_api_key("Runway API secret", env="RUNWAYML_API_SECRET")],
        fields=[
            FieldSpec(name="avatar_id", label="Avatar id", type="string"),
            FieldSpec(name="preset_id", label="Preset id", type="string"),
        ],
        capabilities=ProviderCapabilities(tool_calling=False),
        notes="Exactly one of avatar_id/preset_id is required.",
        docs_url="https://docs.livekit.io/agents/integrations/avatar/",
    ),
    _full(
        "spatius-avatar",
        "avatar",
        "Spatius",
        "Spatius",
        "livekit-plugins-spatius",
        "livekit.plugins.spatius.AvatarSession",
        secret_fields=[_api_key("Spatius API key", env="SPATIUS_API_KEY")],
        fields=[
            FieldSpec(name="app_id", label="App id", type="string", required=True),
            FieldSpec(
                name="avatar_id",
                label="Avatar id",
                type="string",
                required=True,
                env_fallback="SPATIUS_AVATAR_ID",
            ),
        ],
        capabilities=ProviderCapabilities(tool_calling=False),
        notes="Requires two ids (app_id + avatar_id), unlike every other avatar plugin.",
        docs_url="https://docs.livekit.io/agents/integrations/avatar/",
    ),
    _full(
        "synthesia-avatar",
        "avatar",
        "Synthesia",
        "Synthesia",
        "livekit-plugins-synthesia",
        "livekit.plugins.synthesia.AvatarSession",
        secret_fields=[_api_key("Synthesia API key", env="SYNTHESIA_API_KEY")],
        fields=[
            FieldSpec(
                name="avatar_config",
                label="Avatar gallery ids",
                type="json",
                required=True,
                positional=True,
                nested_model="AvatarConfig",
                help='Up to 5 gallery avatar ids ({"avatar_ids": [...]}); the first is active, the rest '
                "swappable in-session via swap_avatar().",
            )
        ],
        capabilities=ProviderCapabilities(tool_calling=False),
        notes="avatar_config is positional, not keyword-only — the only avatar ctor shaped "
        "this way besides D-ID's required agent_id.",
        docs_url="https://docs.livekit.io/agents/integrations/avatar/",
    ),
    _full(
        "trugen-avatar",
        "avatar",
        "TruGen",
        "TruGen",
        "livekit-plugins-trugen",
        "livekit.plugins.trugen.AvatarSession",
        secret_fields=[_api_key("TruGen API key", env="TRUGEN_API_KEY")],
        fields=[FieldSpec(name="avatar_id", label="Avatar id", type="string")],
        capabilities=ProviderCapabilities(tool_calling=False),
        docs_url="https://docs.livekit.io/agents/integrations/avatar/",
    ),
    # ---------------------------------------------------------------- realtime
    _full(
        "aws-nova-sonic-realtime",
        "realtime",
        "AWS Nova Sonic",
        "Amazon",
        "livekit-plugins-aws",
        "livekit.plugins.aws.experimental.realtime.RealtimeModel",
        requires_credential=False,
        fields=[
            FieldSpec(name="region", label="Region", type="string"),
            FieldSpec(name="voice", label="Voice", type="enum", default="tiffany", options=NOVA_SONIC_VOICES),
            FieldSpec(
                name="turn_detection",
                label="Turn detection sensitivity",
                type="enum",
                default="MEDIUM",
                options=["HIGH", "MEDIUM", "LOW"],
            ),
            FieldSpec(
                name="generate_reply_timeout", label="Generate reply timeout (s)", type="number", default=10.0
            ),
        ],
        models=[
            ModelSpec(id="amazon.nova-2-sonic-v1:0", label="Nova 2 Sonic", supports_video=False),
            ModelSpec(id="amazon.nova-sonic-v1:0", label="Nova Sonic", supports_video=False),
        ],
        default_model="amazon.nova-2-sonic-v1:0",
        capabilities=ProviderCapabilities(video_input=False, tool_calling=True, voices=NOVA_SONIC_VOICES),
        notes="__init__ takes no api_key/api_secret/credentials kwarg at all (verified: AST snapshot has no "
        "auth param on this constructor) — resolved purely through the standard boto3/AWS credential chain, "
        "unlike the catalog draft's claim of a flat api_key/api_secret pair. modalities is audio|mixed only: "
        "no half-cascade path.",
        docs_url="https://docs.livekit.io/agents/models/realtime/",
    ),
    _full(
        "phonic-realtime",
        "realtime",
        "Phonic",
        "Phonic",
        "livekit-plugins-phonic",
        "livekit.plugins.phonic.realtime.RealtimeModel",
        secret_fields=[_api_key("Phonic API key", env="PHONIC_API_KEY")],
        fields=[
            FieldSpec(
                name="phonic_agent",
                label="Phonic agent id",
                type="string",
                help="Most behavior (voice, tools, prompts) is configured on Phonic's own "
                "dashboard against this agent id.",
            ),
            FieldSpec(name="welcome_message", label="Welcome message", type="string"),
        ],
        capabilities=ProviderCapabilities(video_input=False, tool_calling=True),
        notes="A fully hosted conversational-agent platform (40+ ctor kwargs) more than a plain "
        "realtime model; most behavior lives in Phonic's own dashboard against phonic_agent, "
        "not in these fields.",
        docs_url="https://docs.livekit.io/agents/models/realtime/",
    ),
    _full(
        "ultravox-realtime",
        "realtime",
        "Ultravox",
        "Fixie",
        "livekit-plugins-ultravox",
        "livekit.plugins.ultravox.realtime.RealtimeModel",
        secret_fields=[_api_key("Ultravox API key", env="ULTRAVOX_API_KEY")],
        fields=[
            FieldSpec(name="voice", label="Voice", type="enum", default="Mark", options=["Mark", "Jessica"]),
            FieldSpec(
                name="output_medium",
                label="Output medium",
                type="enum",
                default="voice",
                options=["text", "voice"],
            ),
        ],
        models=[
            ModelSpec(id="fixie-ai/ultravox", label="Ultravox (default)"),
            ModelSpec(id="fixie-ai/ultravox-llama3.3-70b", label="Ultravox Llama 3.3 70B"),
        ],
        default_model="fixie-ai/ultravox",
        capabilities=ProviderCapabilities(video_input=False, tool_calling=True, text_modality=True),
        docs_url="https://docs.livekit.io/agents/models/realtime/",
    ),
    _full(
        "openai-gptlive-realtime",
        "realtime",
        "OpenAI GPT-Live",
        "OpenAI",
        "livekit-plugins-openai",
        "livekit.plugins.openai.realtime.GPTLiveModel",
        secret_fields=[_api_key("OpenAI API key", env="OPENAI_API_KEY")],
        fields=[
            FieldSpec(name="voice", label="Voice", type="string"),
            FieldSpec(
                name="delegation",
                label="Delegation target",
                type="enum",
                default="responses",
                options=["responses"],
            ),
        ],
        capabilities=ProviderCapabilities(video_input=False, tool_calling=True),
        notes="Delegates response generation to the Responses API rather than the classic "
        "Realtime event stream.",
        docs_url="https://docs.livekit.io/agents/models/realtime/openai/",
    ),
    # ---------------------------------------------------------------- LLM
    _full(
        "perplexity-llm",
        "llm",
        "Perplexity",
        "Perplexity",
        "livekit-plugins-perplexity",
        "livekit.plugins.perplexity.LLM",
        secret_fields=[_api_key("Perplexity API key")],
        models=[ModelSpec(id="sonar-pro", label="Sonar Pro"), ModelSpec(id="sonar", label="Sonar")],
        default_model="sonar-pro",
        docs_url="https://docs.livekit.io/agents/models/llm/",
    ),
    _full(
        "mistral-llm",
        "llm",
        "Mistral",
        "Mistral AI",
        "livekit-plugins-mistralai",
        "livekit.plugins.mistralai.LLM",
        secret_fields=[_api_key("Mistral API key", env="MISTRAL_API_KEY")],
        models=[ModelSpec(id="ministral-8b-latest", label="Ministral 8B")],
        default_model="ministral-8b-latest",
        catalog=CatalogSpec(adapter="mistral_models", kinds=["models"]),
        test="mistral_models",
        docs_url="https://docs.livekit.io/agents/models/llm/",
    ),
    _full(
        "baseten-llm",
        "llm",
        "Baseten",
        "Baseten",
        "livekit-plugins-baseten",
        "livekit.plugins.baseten.LLM",
        secret_fields=[_api_key("Baseten API key", env="BASETEN_API_KEY")],
        fields=[
            FieldSpec(
                name="base_url", label="Base URL", type="string", default="https://inference.baseten.co/v1"
            )
        ],
        models=[ModelSpec(id="meta-llama/Llama-4-Maverick-17B-128E-Instruct", label="Llama 4 Maverick 17B")],
        default_model="meta-llama/Llama-4-Maverick-17B-128E-Instruct",
        docs_url="https://docs.livekit.io/agents/models/llm/",
    ),
    _full(
        "xai-llm",
        "llm",
        "xAI Grok",
        "xAI",
        "livekit-plugins-xai",
        "livekit.plugins.xai.responses.llm.LLM",
        secret_fields=[_api_key("xAI API key", env="XAI_API_KEY")],
        models=[ModelSpec(id="grok-4-1-fast-non-reasoning", label="Grok 4.1 Fast (non-reasoning)")],
        default_model="grok-4-1-fast-non-reasoning",
        docs_url="https://docs.livekit.io/agents/models/llm/",
    ),
    _full(
        "openai-responses-llm",
        "llm",
        "OpenAI (Responses API)",
        "OpenAI",
        "livekit-plugins-openai",
        "livekit.plugins.openai.responses.llm.LLM",
        secret_fields=[_api_key("OpenAI API key", env="OPENAI_API_KEY")],
        fields=[FieldSpec(name="use_websocket", label="Use websocket", type="boolean", default=True)],
        models=[ModelSpec(id="gpt-4.1", label="GPT-4.1")],
        default_model="gpt-4.1",
        catalog=CatalogSpec(adapter="openai_models", kinds=["models"]),
        test="openai_models",
        notes="Distinct class from openai.LLM (chat completions); uses the Responses API over "
        "a websocket by default.",
        docs_url="https://docs.livekit.io/agents/models/llm/openai/",
    ),
    # ---------------------------------------------------------------- STT (new)
    _full(
        "gladia-stt",
        "stt",
        "Gladia",
        "Gladia",
        "livekit-plugins-gladia",
        "livekit.plugins.gladia.STT",
        secret_fields=[_api_key("Gladia API key", env="GLADIA_API_KEY")],
        fields=[
            FieldSpec(name="model", label="Model", type="string", default="solaria-1"),
            FieldSpec(
                name="region", label="Region", type="enum", default="eu-west", options=["us-west", "eu-west"]
            ),
        ],
        docs_url="https://docs.livekit.io/agents/models/stt/",
    ),
    _full(
        "soniox-stt",
        "stt",
        "Soniox",
        "Soniox",
        "livekit-plugins-soniox",
        "livekit.plugins.soniox.STT",
        secret_fields=[_api_key("Soniox API key")],
        fields=[
            FieldSpec(
                name="base_url",
                label="Base URL",
                type="string",
                default="wss://stt-rt.soniox.com/transcribe-websocket",
            )
        ],
        docs_url="https://docs.livekit.io/agents/models/stt/",
    ),
    _full(
        "fal-wizper-stt",
        "stt",
        "Fal Wizper",
        "Fal",
        "livekit-plugins-fal",
        "livekit.plugins.fal.WizperSTT",
        secret_fields=[
            FieldSpec(
                name="api_key",
                label="Fal key",
                type="secret",
                required=True,
                help="FAL_KEY per fal-client convention.",
            )
        ],
        notes="Class is named WizperSTT, not STT.",
        docs_url="https://docs.livekit.io/agents/models/stt/",
    ),
    _full(
        "fireworksai-stt",
        "stt",
        "Fireworks AI",
        "Fireworks",
        "livekit-plugins-fireworksai",
        "livekit.plugins.fireworksai.STT",
        secret_fields=[_api_key("Fireworks API key", env="FIREWORKS_API_KEY")],
        fields=[FieldSpec(name="language", label="Language", type="string")],
        docs_url="https://docs.livekit.io/agents/models/stt/",
    ),
    _full(
        "baseten-stt",
        "stt",
        "Baseten",
        "Baseten",
        "livekit-plugins-baseten",
        "livekit.plugins.baseten.STT",
        secret_fields=[_api_key("Baseten API key", env="BASETEN_API_KEY")],
        fields=[FieldSpec(name="model", label="Model", type="string", default="whisper")],
        docs_url="https://docs.livekit.io/agents/models/stt/",
    ),
    _full(
        "mistral-stt",
        "stt",
        "Mistral Voxtral",
        "Mistral AI",
        "livekit-plugins-mistralai",
        "livekit.plugins.mistralai.STT",
        secret_fields=[_api_key("Mistral API key", env="MISTRAL_API_KEY")],
        fields=[FieldSpec(name="model", label="Model", type="string", default="voxtral-mini-latest")],
        docs_url="https://docs.livekit.io/agents/models/stt/",
    ),
    _full(
        "nvidia-stt",
        "stt",
        "NVIDIA Riva",
        "NVIDIA",
        "livekit-plugins-nvidia",
        "livekit.plugins.nvidia.STT",
        secret_fields=[_api_key("NVIDIA API key")],
        fields=[
            FieldSpec(
                name="model",
                label="Model",
                type="string",
                default="parakeet-1.1b-en-US-asr-streaming-silero-vad-sortformer",
            )
        ],
        docs_url="https://docs.livekit.io/agents/models/stt/",
    ),
    _full(
        "sarvam-stt",
        "stt",
        "Sarvam",
        "Sarvam AI",
        "livekit-plugins-sarvam",
        "livekit.plugins.sarvam.STT",
        secret_fields=[_api_key("Sarvam API key", env="SARVAM_API_KEY")],
        fields=[
            FieldSpec(name="model", label="Model", type="string", default="saaras:v4"),
            FieldSpec(name="language", label="Language", type="string", default="en-IN"),
        ],
        docs_url="https://docs.livekit.io/agents/models/stt/",
    ),
    _full(
        "meta-stt",
        "stt",
        "Meta",
        "Meta",
        "livekit-plugins-meta",
        "livekit.plugins.meta.STT",
        secret_fields=[_api_key("Meta API key")],
        fields=[FieldSpec(name="model", label="Model", type="string", default="muse-voice-transcribe-1.0")],
        docs_url="https://docs.livekit.io/agents/models/stt/",
    ),
    _full(
        "gnani-stt",
        "stt",
        "Gnani",
        "Gnani",
        "livekit-plugins-gnani",
        "livekit.plugins.gnani.STT",
        secret_fields=[_api_key("Gnani API key", env="GNANI_API_KEY")],
        fields=[FieldSpec(name="language", label="Language", type="string", default="en-IN")],
        docs_url="https://docs.livekit.io/agents/models/stt/",
    ),
    _full(
        "gradium-stt",
        "stt",
        "Gradium",
        "Gradium",
        "livekit-plugins-gradium",
        "livekit.plugins.gradium.STT",
        secret_fields=[_api_key("Gradium API key", env="GRADIUM_API_KEY")],
        fields=[
            FieldSpec(
                name="model_endpoint",
                label="Model endpoint",
                type="string",
                env_fallback="GRADIUM_MODEL_ENDPOINT",
            )
        ],
        requires_credential=True,
        notes="Self-deployed model endpoint (like Baseten/Simplismart), not a shared public model catalog.",
        docs_url="https://docs.livekit.io/agents/models/stt/",
    ),
    _full(
        "clova-stt",
        "stt",
        "Naver Clova",
        "Naver",
        "livekit-plugins-clova",
        "livekit.plugins.clova.STT",
        secret_fields=[
            FieldSpec(
                name="secret",
                label="Clova secret key",
                type="secret",
                required=True,
                env_fallback="CLOVA_STT_SECRET_KEY",
            )
        ],
        fields=[
            FieldSpec(
                name="invoke_url", label="Invoke URL", type="string", env_fallback="CLOVA_STT_INVOKE_URL"
            ),
            FieldSpec(name="language", label="Language", type="string", default="en-US"),
        ],
        docs_url="https://docs.livekit.io/agents/models/stt/",
    ),
    _full(
        "slng-stt",
        "stt",
        "Slng",
        "Slng",
        "livekit-plugins-slng",
        "livekit.plugins.slng.STT",
        secret_fields=[_api_key("Slng API key", env="SLNG_API_KEY")],
        fields=[FieldSpec(name="slng_base_url", label="Base URL", type="string", default="api.slng.ai")],
        notes="Router/gateway product: routes to a configured upstream STT model, not a fixed vendor model.",
        docs_url="https://docs.livekit.io/agents/models/stt/",
    ),
    _full(
        "smallestai-stt",
        "stt",
        "Smallest AI",
        "Smallest AI",
        "livekit-plugins-smallestai",
        "livekit.plugins.smallestai.STT",
        secret_fields=[_api_key("Smallest AI API key", env="SMALLEST_API_KEY")],
        fields=[FieldSpec(name="model", label="Model", type="string", default="pulse")],
        docs_url="https://docs.livekit.io/agents/models/stt/",
    ),
    _full(
        "simplismart-stt",
        "stt",
        "Simplismart",
        "Simplismart",
        "livekit-plugins-simplismart",
        "livekit.plugins.simplismart.STT",
        secret_fields=[_api_key("Simplismart API key", env="SIMPLISMART_API_KEY")],
        fields=[
            FieldSpec(name="model", label="Model", type="string", default="openai/whisper-large-v3-turbo")
        ],
        notes="Self-deployed inference; api_key authenticates the tenant's Simplismart deployment.",
        docs_url="https://docs.livekit.io/agents/models/stt/",
    ),
    _full(
        "telnyx-stt",
        "stt",
        "Telnyx",
        "Telnyx",
        "livekit-plugins-telnyx",
        "livekit.plugins.telnyx.STT",
        secret_fields=[_api_key("Telnyx API key")],
        fields=[FieldSpec(name="language", label="Language", type="string", default="en")],
        docs_url="https://docs.livekit.io/agents/models/stt/",
    ),
    _full(
        "palabra-stt",
        "stt",
        "Palabra",
        "Palabra",
        "livekit-plugins-palabra",
        "livekit.plugins.palabra.STT",
        secret_fields=[_api_key("Palabra API key", env="PALABRA_API_KEY")],
        fields=[FieldSpec(name="translate_languages", label="Translate languages", type="string")],
        notes="Real-time translation ASR.",
        docs_url="https://docs.livekit.io/agents/models/stt/",
    ),
    _full(
        "aws-transcribe-stt",
        "stt",
        "Amazon Transcribe",
        "Amazon",
        "livekit-plugins-aws",
        "livekit.plugins.aws.STT",
        requires_credential=False,
        fields=[
            FieldSpec(
                name="credentials",
                label="AWS credentials",
                type="json",
                help="boto-style Credentials object; omit to use the standard AWS credential chain "
                "(this class, unlike aws.LLM/aws.TTS, has no flat api_key/api_secret kwarg).",
            ),
            FieldSpec(name="region", label="Region", type="string", env_fallback="AWS_REGION"),
            FieldSpec(name="language", label="Language", type="string", default="en-US"),
        ],
        docs_url="https://docs.livekit.io/agents/models/stt/aws/",
    ),
    _full(
        "xai-stt",
        "stt",
        "xAI",
        "xAI",
        "livekit-plugins-xai",
        "livekit.plugins.xai.STT",
        secret_fields=[_api_key("xAI API key", env="XAI_API_KEY")],
        fields=[FieldSpec(name="language", label="Language", type="string", default="en")],
        docs_url="https://docs.livekit.io/agents/models/stt/",
    ),
    # ---------------------------------------------------------------- TTS (new)
    _full(
        "asyncai-tts",
        "tts",
        "AsyncAI",
        "AsyncAI",
        "livekit-plugins-asyncai",
        "livekit.plugins.asyncai.TTS",
        secret_fields=[_api_key("AsyncAI API key", env="ASYNCAI_API_KEY")],
        fields=[FieldSpec(name="model", label="Model", type="string", default="async_flash_v1.0")],
        docs_url="https://docs.livekit.io/agents/models/tts/",
    ),
    _full(
        "aws-polly-tts",
        "tts",
        "Amazon Polly",
        "Amazon",
        "livekit-plugins-aws",
        "livekit.plugins.aws.TTS",
        secret_fields=[
            FieldSpec(
                name="api_key", label="AWS access key id", type="secret", env_fallback="AWS_ACCESS_KEY_ID"
            ),
            FieldSpec(
                name="api_secret",
                label="AWS secret access key",
                type="secret",
                env_fallback="AWS_SECRET_ACCESS_KEY",
            ),
        ],
        requires_credential=False,
        fields=[
            FieldSpec(name="voice", label="Voice", type="string", default="Ruth"),
            FieldSpec(
                name="speech_engine",
                label="Speech engine",
                type="enum",
                default="generative",
                options=["generative", "standard", "neural"],
            ),
        ],
        docs_url="https://docs.livekit.io/agents/models/tts/aws/",
    ),
    _full(
        "baseten-tts",
        "tts",
        "Baseten",
        "Baseten",
        "livekit-plugins-baseten",
        "livekit.plugins.baseten.TTS",
        secret_fields=[_api_key("Baseten API key", env="BASETEN_API_KEY")],
        fields=[FieldSpec(name="model", label="Model", type="string", default="orpheus")],
        docs_url="https://docs.livekit.io/agents/models/tts/",
    ),
    _full(
        "bland-tts",
        "tts",
        "Bland",
        "Bland",
        "livekit-plugins-bland",
        "livekit.plugins.bland.TTS",
        secret_fields=[_api_key("Bland API key", env="BLAND_API_KEY")],
        fields=[FieldSpec(name="voice_id", label="Voice id", type="string")],
        docs_url="https://docs.livekit.io/agents/models/tts/",
    ),
    _full(
        "cambai-tts",
        "tts",
        "Camb.ai",
        "Camb.ai",
        "livekit-plugins-cambai",
        "livekit.plugins.cambai.TTS",
        secret_fields=[_api_key("Camb.ai API key", env="CAMB_API_KEY")],
        fields=[
            FieldSpec(name="voice_id", label="Voice id", type="number"),
            FieldSpec(name="language", label="Language", type="string"),
        ],
        docs_url="https://docs.livekit.io/agents/models/tts/",
    ),
    _full(
        "fishaudio-tts",
        "tts",
        "Fish Audio",
        "Fish Audio",
        "livekit-plugins-fishaudio",
        "livekit.plugins.fishaudio.TTS",
        secret_fields=[_api_key("Fish Audio API key")],
        fields=[
            FieldSpec(name="model", label="Model", type="string", default="s2.1-pro"),
            FieldSpec(name="voice_id", label="Voice id", type="string"),
        ],
        docs_url="https://docs.livekit.io/agents/models/tts/",
    ),
    _full(
        "gnani-tts",
        "tts",
        "Gnani",
        "Gnani",
        "livekit-plugins-gnani",
        "livekit.plugins.gnani.TTS",
        secret_fields=[_api_key("Gnani API key", env="GNANI_API_KEY")],
        fields=[FieldSpec(name="voice", label="Voice", type="string", default="Pranav")],
        docs_url="https://docs.livekit.io/agents/models/tts/",
    ),
    _full(
        "gradium-tts",
        "tts",
        "Gradium",
        "Gradium",
        "livekit-plugins-gradium",
        "livekit.plugins.gradium.TTS",
        secret_fields=[_api_key("Gradium API key", env="GRADIUM_API_KEY")],
        fields=[FieldSpec(name="voice_id", label="Voice id", type="string", default="4SZHfMpw-p46Ywgs")],
        docs_url="https://docs.livekit.io/agents/models/tts/",
    ),
    _full(
        "groq-tts",
        "tts",
        "Groq",
        "Groq",
        "livekit-plugins-groq",
        "livekit.plugins.groq.TTS",
        secret_fields=[_api_key("Groq API key", env="GROQ_API_KEY")],
        fields=[
            FieldSpec(name="model", label="Model", type="string", default="canopylabs/orpheus-v1-english"),
            FieldSpec(name="voice", label="Voice", type="string", default="autumn"),
        ],
        docs_url="https://docs.livekit.io/agents/models/tts/",
    ),
    _full(
        "lmnt-tts",
        "tts",
        "LMNT",
        "LMNT",
        "livekit-plugins-lmnt",
        "livekit.plugins.lmnt.TTS",
        secret_fields=[_api_key("LMNT API key", env="LMNT_API_KEY")],
        fields=[
            FieldSpec(name="model", label="Model", type="string", default="blizzard"),
            FieldSpec(name="voice", label="Voice", type="string", default="leah"),
        ],
        docs_url="https://docs.livekit.io/agents/models/tts/",
    ),
    _full(
        "murf-tts",
        "tts",
        "Murf",
        "Murf",
        "livekit-plugins-murf",
        "livekit.plugins.murf.TTS",
        secret_fields=[_api_key("Murf API key", env="MURF_API_KEY")],
        fields=[FieldSpec(name="model", label="Model", type="string", default="FALCON")],
        docs_url="https://docs.livekit.io/agents/models/tts/",
    ),
    _full(
        "neuphonic-tts",
        "tts",
        "Neuphonic",
        "Neuphonic",
        "livekit-plugins-neuphonic",
        "livekit.plugins.neuphonic.TTS",
        secret_fields=[_api_key("Neuphonic API key", env="NEUPHONIC_API_KEY")],
        fields=[
            FieldSpec(
                name="voice_id",
                label="Voice id",
                type="string",
                default="8e9c4bc8-3979-48ab-8626-df53befc2090",
            )
        ],
        docs_url="https://docs.livekit.io/agents/models/tts/",
    ),
    _full(
        "nvidia-tts",
        "tts",
        "NVIDIA",
        "NVIDIA",
        "livekit-plugins-nvidia",
        "livekit.plugins.nvidia.TTS",
        secret_fields=[_api_key("NVIDIA API key")],
        fields=[
            FieldSpec(name="voice", label="Voice", type="string", default="Magpie-Multilingual.EN-US.Leo")
        ],
        docs_url="https://docs.livekit.io/agents/models/tts/",
    ),
    _full(
        "palabra-tts",
        "tts",
        "Palabra",
        "Palabra",
        "livekit-plugins-palabra",
        "livekit.plugins.palabra.TTS",
        secret_fields=[_api_key("Palabra API key")],
        fields=[
            FieldSpec(name="voice_id", label="Voice id", type="string"),
            FieldSpec(name="language", label="Language", type="string"),
        ],
        docs_url="https://docs.livekit.io/agents/models/tts/",
    ),
    _full(
        "resemble-tts",
        "tts",
        "Resemble",
        "Resemble AI",
        "livekit-plugins-resemble",
        "livekit.plugins.resemble.TTS",
        secret_fields=[_api_key("Resemble API key", env="RESEMBLE_API_KEY")],
        fields=[FieldSpec(name="voice_uuid", label="Voice UUID", type="string")],
        docs_url="https://docs.livekit.io/agents/models/tts/",
    ),
    _full(
        "respeecher-tts",
        "tts",
        "Respeecher",
        "Respeecher",
        "livekit-plugins-respeecher",
        "livekit.plugins.respeecher.TTS",
        secret_fields=[_api_key("Respeecher API key", env="RESPEECHER_API_KEY")],
        fields=[FieldSpec(name="model", label="Model", type="string", default="/public/tts/en-rt")],
        docs_url="https://docs.livekit.io/agents/models/tts/",
    ),
    _full(
        "sarvam-tts",
        "tts",
        "Sarvam",
        "Sarvam AI",
        "livekit-plugins-sarvam",
        "livekit.plugins.sarvam.TTS",
        secret_fields=[_api_key("Sarvam API key", env="SARVAM_API_KEY")],
        fields=[
            FieldSpec(name="model", label="Model", type="string", default="bulbul:v3"),
            FieldSpec(name="target_language_code", label="Target language", type="string", default="en-IN"),
        ],
        docs_url="https://docs.livekit.io/agents/models/tts/",
    ),
    _full(
        "simplismart-tts",
        "tts",
        "Simplismart",
        "Simplismart",
        "livekit-plugins-simplismart",
        "livekit.plugins.simplismart.TTS",
        secret_fields=[_api_key("Simplismart API key")],
        fields=[
            FieldSpec(name="model", label="Model", type="string", default="canopylabs/orpheus-3b-0.1-ft")
        ],
        docs_url="https://docs.livekit.io/agents/models/tts/",
    ),
    _full(
        "slng-tts",
        "tts",
        "Slng",
        "Slng",
        "livekit-plugins-slng",
        "livekit.plugins.slng.TTS",
        secret_fields=[_api_key("Slng API key", env="SLNG_API_KEY")],
        fields=[FieldSpec(name="voice", label="Voice", type="string", required=True)],
        docs_url="https://docs.livekit.io/agents/models/tts/",
    ),
    _full(
        "smallestai-tts",
        "tts",
        "Smallest AI",
        "Smallest AI",
        "livekit-plugins-smallestai",
        "livekit.plugins.smallestai.TTS",
        secret_fields=[_api_key("Smallest AI API key", env="SMALLEST_API_KEY")],
        fields=[FieldSpec(name="model", label="Model", type="string", default="lightning_v3.1_pro")],
        docs_url="https://docs.livekit.io/agents/models/tts/",
    ),
    _full(
        "soniox-tts",
        "tts",
        "Soniox",
        "Soniox",
        "livekit-plugins-soniox",
        "livekit.plugins.soniox.TTS",
        secret_fields=[_api_key("Soniox API key", env="SONIOX_API_KEY")],
        fields=[
            FieldSpec(name="model", label="Model", type="string", default="tts-rt-v1-preview"),
            FieldSpec(name="voice", label="Voice", type="string", default="Maya"),
        ],
        docs_url="https://docs.livekit.io/agents/models/tts/",
    ),
    _full(
        "speechify-tts",
        "tts",
        "Speechify",
        "Speechify",
        "livekit-plugins-speechify",
        "livekit.plugins.speechify.TTS",
        secret_fields=[_api_key("Speechify API key", env="SPEECHIFY_API_KEY")],
        fields=[
            FieldSpec(name="voice_id", label="Voice id", type="string", default="dominic_32"),
            FieldSpec(name="model", label="Model", type="string", default="simba-3.2"),
        ],
        catalog=CatalogSpec(adapter="speechify_voices", kinds=["voices"]),
        test="speechify_voices",
        docs_url="https://docs.livekit.io/agents/models/tts/",
    ),
    _full(
        "speechmatics-tts",
        "tts",
        "Speechmatics",
        "Speechmatics",
        "livekit-plugins-speechmatics",
        "livekit.plugins.speechmatics.TTS",
        secret_fields=[_api_key("Speechmatics API key", env="SPEECHMATICS_API_KEY")],
        fields=[FieldSpec(name="voice", label="Voice", type="string", default="sarah")],
        docs_url="https://docs.livekit.io/agents/models/tts/",
    ),
    _full(
        "telnyx-tts",
        "tts",
        "Telnyx",
        "Telnyx",
        "livekit-plugins-telnyx",
        "livekit.plugins.telnyx.TTS",
        secret_fields=[_api_key("Telnyx API key")],
        fields=[FieldSpec(name="voice", label="Voice", type="string", default="Telnyx.NaturalHD.astra")],
        docs_url="https://docs.livekit.io/agents/models/tts/",
    ),
    _full(
        "upliftai-tts",
        "tts",
        "UpliftAI",
        "UpliftAI",
        "livekit-plugins-upliftai",
        "livekit.plugins.upliftai.TTS",
        secret_fields=[_api_key("UpliftAI API key", env="UPLIFTAI_API_KEY")],
        fields=[FieldSpec(name="voice_id", label="Voice id", type="string", default="v_meklc281")],
        docs_url="https://docs.livekit.io/agents/models/tts/",
    ),
    _full(
        "vakyam-tts",
        "tts",
        "Vakyam",
        "Vakyam",
        "livekit-plugins-vakyam",
        "livekit.plugins.vakyam.TTS",
        secret_fields=[_api_key("Vakyam API key", env="VAKYAM_API_KEY")],
        fields=[
            FieldSpec(name="voice", label="Voice", type="string"),
            FieldSpec(name="language", label="Language", type="string"),
        ],
        docs_url="https://docs.livekit.io/agents/models/tts/",
    ),
    _full(
        "xai-tts",
        "tts",
        "xAI",
        "xAI",
        "livekit-plugins-xai",
        "livekit.plugins.xai.TTS",
        secret_fields=[_api_key("xAI API key", env="XAI_API_KEY")],
        fields=[
            FieldSpec(name="voice", label="Voice", type="string", default="ara"),
            FieldSpec(name="language", label="Language", type="string", default="auto"),
        ],
        docs_url="https://docs.livekit.io/agents/models/tts/",
    ),
]


#: Genuinely not offered, per the catalog's own corrections (PLAN-V2 §2 V2-05
#: card) plus two this pass added on the same "cannot construct without
#: guessing" principle. Hedra is deliberately **absent even from this list**:
#: the task's own instruction ("Hedra's avatar plugin is vendor-disabled:
#: don't register it") is stronger than the catalog's suggestion to add it as
#: `availability="removed"` — flagged in the report for the coordinator, since
#: the two differ.
_DEFERRED: list[ProviderSpec] = [
    _full(
        "playai-tts",
        "tts",
        "PlayAI",
        "PlayAI",
        "livekit-plugins-playai",
        "livekit.plugins.playai.TTS",
        availability="deferred",
        secret_fields=[
            _api_key("PlayAI API key"),
            FieldSpec(
                name="user_id",
                label="User id",
                type="secret",
                required=True,
                help="PlayAI requires both an API key and a user id.",
            ),
        ],
        fields=[
            FieldSpec(
                name="voice",
                label="Voice manifest URL",
                type="string",
                help="A PlayAI voice-clone manifest URL, not a bare id.",
            )
        ],
        notes="Not in the livekit/agents 1.8.2 monorepo tree (absent from this pass's clone, confirming the "
        "catalog's finding); last released 2025-10-15 against a much older core. Compatibility with "
        "livekit-agents>=1.8.2 is unverified, not disproven.",
        docs_url="https://docs.livekit.io/agents/models/tts/",
    ),
    _full(
        "nvidia-personaplex-realtime",
        "realtime",
        "NVIDIA PersonaPlex",
        "NVIDIA",
        "livekit-plugins-nvidia",
        "livekit.plugins.nvidia.experimental.realtime.RealtimeModel",
        availability="deferred",
        requires_credential=False,
        fields=[FieldSpec(name="voice", label="Voice", type="string", default="NATF2")],
        notes="Explicit PLAN-V2 correction. Constructor takes no api_key kwarg (confirmed: base_url, "
        "http_session, seed, silence_threshold_ms, text_prompt, voice only) and no conn_options/tool_choice "
        "either — the least mature realtime integration of the set.",
    ),
    _full(
        "legacy-noise-cancellation",
        "noise_cancellation",
        "Noise Cancellation (legacy)",
        "LiveKit",
        "livekit-plugins-noise-cancellation",
        "UNVERIFIED",
        availability="deferred",
        requires_credential=False,
        notes="Out-of-tree (not in the 1.8.2 monorepo clone); only requires_dist (livekit>=0.21.3) is known "
        "from PyPI metadata. Likely the older client/room-level Krisp BVC wrapper, distinct from the in-tree "
        "livekit-plugins-krisp. Left deferred rather than guessing a class path.",
    ),
    _full(
        "ai-coustics-noise-cancellation",
        "noise_cancellation",
        "ai-coustics",
        "ai-coustics",
        "livekit-plugins-ai-coustics",
        "UNVERIFIED",
        availability="deferred",
        secret_fields=[
            FieldSpec(
                name="api_key",
                label="API key",
                type="secret",
                required=True,
                help="Vendor credential shape not confirmed.",
            )
        ],
        notes="Out-of-tree (not in the 1.8.2 monorepo clone); only requires_dist (livekit-agents>=1.4.2) is "
        "known from PyPI metadata. Left deferred rather than guessing a class path.",
    ),
    _full(
        "rtzr-stt",
        "stt",
        "RTZR (Vito)",
        "RTZR",
        "livekit-plugins-rtzr",
        "livekit.plugins.rtzr.STT",
        availability="deferred",
        requires_credential=False,
        fields=[
            FieldSpec(name="model", label="Model", type="string", default="sommers_ko"),
            FieldSpec(name="language", label="Language", type="string", default="ko"),
        ],
        notes="__init__ has no api_key/credential kwarg at all (confirmed by AST snapshot) — auth "
        "is resolved internally by an rtzrapi.py helper the factory cannot reach with per-tenant "
        "credentials, so a shared worker process could not keep two tenants' RTZR keys separate. "
        "Deferred until that's resolved upstream.",
    ),
    _full(
        "spitch-stt",
        "stt",
        "Spitch",
        "Spitch",
        "livekit-plugins-spitch",
        "livekit.plugins.spitch.STT",
        availability="deferred",
        requires_credential=False,
        fields=[FieldSpec(name="language", label="Language", type="string", default="en")],
        notes="Same gap as rtzr-stt: __init__ takes no credential kwarg (confirmed by AST snapshot) — the "
        "AsyncSpitch() client resolves its own env var internally, which the factory cannot pass per-tenant.",
    ),
    _full(
        "spitch-tts",
        "tts",
        "Spitch",
        "Spitch",
        "livekit-plugins-spitch",
        "livekit.plugins.spitch.TTS",
        availability="deferred",
        requires_credential=False,
        fields=[
            FieldSpec(name="language", label="Language", type="string", default="en"),
            FieldSpec(name="voice", label="Voice", type="string", default="lina"),
        ],
        notes="Same gap as spitch-stt.",
    ),
    _full(
        "minimax-tts",
        "tts",
        "MiniMax",
        "MiniMax",
        "livekit-plugins-minimax",
        "livekit.plugins.minimax.TTS",
        availability="incompatible",
        secret_fields=[_api_key("MiniMax API key", env="MINIMAX_API_KEY")],
        fields=[FieldSpec(name="model", label="Model", type="string", default="speech-02-turbo")],
        notes="livekit-plugins-minimax 1.3.0 pins livekit-agents==1.2.9 exactly, five releases behind this "
        "platform's 1.8.2 baseline; uv pip compile confirms the two cannot resolve together in one "
        "environment. Not installable until the vendor republishes against current core.",
    ),
]

#: Providers V2-20 saw serve a passing live session on LiveKit Cloud
#: (docs/v2/LIVE-RESULTS.md, stage L5: explicit `vad`/`turn_detection` slots
#: built from these entries on an audio round trip). Only ever grows from
#: recorded live evidence; ``status`` is unaffected (R-V2-1).
LIVE_VERIFIED_IDS: frozenset[str] = frozenset({"inference-vad", "inference-turn-detector"})


def _live_verified(spec: ProviderSpec) -> ProviderSpec:
    """Return ``spec`` with ``verification="verified"`` when V2-20 verified it live."""
    if spec.id not in LIVE_VERIFIED_IDS or spec.verification == "verified":
        return spec
    return ProviderSpec.model_validate({**spec.model_dump(exclude={"status"}), "verification": "verified"})


REGISTRY: list[ProviderSpec] = [
    _live_verified(spec) for spec in [*_MVP, *_OPENROUTER, *_FULL, *_NEW, *_DEFERRED]
]

_BY_ID: dict[str, ProviderSpec] = {spec.id: spec for spec in REGISTRY}


def credential_home(spec_or_id: ProviderSpec | str) -> str:
    """Return the registry id a provider's credential rows are stored under (R-V4-7).

    A provider that names a ``credential_provider`` shares that entry's key
    (every OpenRouter entry stores its key under ``openrouter-llm``); every
    other provider is its own home. An id the registry does not know is
    returned unchanged, so callers can filter by it and simply find nothing.

    Args:
        spec_or_id: A :class:`ProviderSpec` or a registry id.

    Returns:
        The id every credential lookup for this provider compares against.
    """
    if isinstance(spec_or_id, ProviderSpec):
        return spec_or_id.credential_provider or spec_or_id.id
    spec = _BY_ID.get(spec_or_id)
    if spec is None:
        return spec_or_id
    return spec.credential_provider or spec.id


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


def available_providers() -> list[ProviderSpec]:
    """Return every provider the platform offers (``availability == "available"``).

    Unlike :func:`mvp_providers` this ignores verification, so a provider that
    ships but has not yet passed a live call is included.
    """
    return [s for s in REGISTRY if s.availability == "available"]


def by_image(image: WorkerImage) -> list[ProviderSpec]:
    """Return every available provider carried by a worker image.

    The ``slim`` image is a subset of ``full``, so asking for ``full`` also
    returns the ``slim`` entries.

    Args:
        image: The worker image flavour to filter on.

    Returns:
        The matching provider specs, in registry order.
    """
    wanted = {"slim"} if image == "slim" else {"slim", "full"} if image == "full" else {"isolated"}
    return [s for s in available_providers() if s.worker_image in wanted]


def constructible(installed_ids: list[str] | set[str] | None) -> list[ProviderSpec]:
    """Return the available providers a worker pool can actually build.

    Args:
        installed_ids: ``installed_provider_ids`` reported by the pool's workers,
            or ``None`` when the pool has not registered yet (then every
            available provider is assumed constructible, as in v1).

    Returns:
        The matching provider specs, in registry order.
    """
    if installed_ids is None:
        return available_providers()
    installed = set(installed_ids)
    return [s for s in available_providers() if s.id in installed]


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
