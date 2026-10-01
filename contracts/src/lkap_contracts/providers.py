"""Provider registry: the catalogue of everything the platform can construct.

`REGISTRY` is the single source of truth for provider ids, constructor classes,
credential fields, configurable non-secret fields and suggested models. The api
validates `AgentConfig` against it, the agent's `ProviderFactory` builds plugin
objects from it, and the web console renders its forms from the exported
`generated/providers.json`.
"""

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

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
    "tool_provider",
    "web_search",
    "sms",
    "knowledge",
]
#: ``web_search`` and ``sms`` (V5-25, D-V5-7): vendors a built-in tool calls (``ToolsConfig.web_search``,
#: ``ToolsConfig.sms``). Nothing to construct, so no package or class; the worker's own adapters
#: (``lkap_agent.tools.vendors``) speak each vendor's API.
#: ``knowledge`` (V5-20, D-V5-16/19): the keys and non-secret fields of a knowledge connection (a
#: vector store a knowledge base can live in, or a hosted re-ranking service). The api speaks each
#: vendor's REST API itself (``lkap_api.kb.stores``, ``lkap_api.kb.rerankers``); nothing to construct.
FieldType = Literal["string", "secret", "number", "boolean", "enum", "json", "model", "file", "catalog"]

# ------------------------------------------------------------------ reasoning models (V6-31)
#: How long a reasoning model thinks before it answers, lowest first: the ``openai`` SDK's
#: ``ReasoningEffort`` values at the pinned version (``openai/types/shared/reasoning_effort.py``),
#: which is also the vocabulary of OpenRouter's ``reasoning.supported_efforts``. ``none`` turns
#: thinking off on the models that allow it.
ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"]

#: :data:`ReasoningEffort` in order, lowest first.
REASONING_EFFORTS: tuple[ReasoningEffort, ...] = ("none", "minimal", "low", "medium", "high", "xhigh", "max")

#: The registry field (on ``openrouter-llm``, ``openai-llm``, ``livekit-inference-llm``) that sets it.
REASONING_EFFORT_FIELD = "reasoning_effort"

#: Efforts that add seconds to every reply on a voice call (measured 2026-09-29: GPT-6 Luna at its
#: default ``medium`` took 8.2 s from end of speech to first audio against 3.6 s for GPT-4.1 mini).
SLOW_VOICE_EFFORTS: frozenset[str] = frozenset({"medium", "high", "xhigh", "max"})

#: Chat request parameters the worker leaves out when the model's capability view says the model
#: does not accept them (V6-31), instead of letting the request fail: OpenRouter with
#: ``provider.require_parameters`` (the platform's default) routes only to endpoints that accept
#: every parameter sent, and answers 404 when none does. ``tools`` is never on this list.
OPTIONAL_REQUEST_PARAMETERS: frozenset[str] = frozenset(
    {
        "temperature",
        "top_p",
        "top_k",
        "frequency_penalty",
        "presence_penalty",
        "seed",
        "stop",
        "parallel_tool_calls",
        "reasoning_effort",
        "verbosity",
    }
)

#: Whether the platform offers a provider at all (CONTRACTS-V2 §4.1).
#:
#: ``available`` — offered; ``deferred`` — catalogued but not shipped yet;
#: ``incompatible`` — cannot coexist with the pinned ``livekit-agents``;
#: ``removed`` — withdrawn by its vendor.
Availability = Literal["available", "deferred", "incompatible", "removed"]

#: The registry id of an MCP server's OAuth sign-in credential (V5-14).
MCP_OAUTH_PROVIDER_ID = "mcp-oauth"

#: Whether a provider has completed a live call on this platform.
Verification = Literal["verified", "unverified"]

#: Which worker image carries the provider's plugin.
WorkerImage = Literal["slim", "full", "isolated"]

#: What a vendor catalog adapter can list.
CatalogKind = Literal["models", "voices", "avatars", "personas"]

# ------------------------------------------------------------------ model ids (V4-07)
#: Registry kinds whose slot takes a model id (docs/v4/CUSTOM-MODELS.md D-V4-23, R-V4-22).
#: The console renders the model combobox for every one of them, even with no ``models``.
MODEL_KINDS: frozenset[ProviderKind] = frozenset({"realtime", "stt", "llm", "tts", "image_gen", "embedding"})

#: Key prefixes a model id never starts with (R-V4-21). Grows by test, never by guess.
#: Longer prefixes (``sk-or-``, ``sk-ant-``, ``sk_car_``) are listed for documentation and
#: for the console mirror; ``sk-``/``sk_`` already cover them.
SECRET_PREFIXES: tuple[str, ...] = (
    "sk-",
    "sk_",
    "sk-or-",
    "sk-ant-",
    "sk_car_",
    "AIza",
    "xai-",
    "gsk_",
    "hf_",
    "AKIA",
    "ghp_",
    "github_pat_",
    "ya29.",
    "xi-",
)

#: Longest model id the platform stores (the ``provider_models.model_id`` column).
MODEL_ID_MAX_LEN = 200

#: A value this long made of one character class with no separator is a bare token (a key).
BARE_TOKEN_MIN_LEN = 32

#: Field names whose values are ids, checked by the same rule as a model id (R-V4-21).
#: Dotted field names (``simli_config.face_id``) are matched on their last segment.
ID_LIKE_FIELD_NAMES: frozenset[str] = frozenset(
    {"voice", "voice_id", "avatar_id", "face_id", "pal_id", "persona_id", "voice_name", "emotion_id"}
)

#: The syntax rule as one JS-compatible regular expression (the console mirrors it).
#: 1-200 printable ASCII characters without whitespace, ``? # & = < > " ' ```, never
#: ``://`` and never starting with ``http``. The secret check is separate
#: (:data:`SECRET_PREFIXES`, the bare-token rule), and runs first.
MODEL_ID_PATTERN = (
    r"^(?![Hh][Tt][Tt][Pp])(?!.*://)[!$%()*+,\-./0-9:;@A-Z\[\\\]^_a-z{|}~]{1," + str(MODEL_ID_MAX_LEN) + r"}$"
)

_MODEL_ID_RE = re.compile(MODEL_ID_PATTERN)
_BARE_TOKEN_RE = re.compile(r"[A-Za-z0-9+]+={0,2}")
_FORBIDDEN_CHARS = frozenset("?#&=<>\"'`")

#: The one message a secret-looking value gets. It never contains any part of the value.
SECRET_LOOKING_REASON = "looks like an API key, not a model id"


#: The warning an id-like field gets for a bare token (R-V4-31). It never contains the value.
BARE_TOKEN_ID_REASON = "looks like an API key. If it is the vendor's id, ignore this"


def _has_secret_prefix(value: str) -> bool:
    text = value.strip()
    return any(text.startswith(prefix) for prefix in SECRET_PREFIXES)


def _is_bare_token(value: str) -> bool:
    text = value.strip()
    return len(text) >= BARE_TOKEN_MIN_LEN and _BARE_TOKEN_RE.fullmatch(text) is not None


def looks_like_secret(value: str) -> bool:
    """Whether ``value`` looks like an API key rather than an id (R-V4-21).

    True for a value starting with one of :data:`SECRET_PREFIXES`, or a bare
    token: at least :data:`BARE_TOKEN_MIN_LEN` characters of one class (hex or
    base64 alphanumerics) with no ``/ . : -`` separator.
    """
    return _has_secret_prefix(value) or _is_bare_token(value)


def _syntax_reason(value: str) -> str | None:
    """Why ``value`` breaks the id syntax rule, or ``None`` (no secret check; value-free)."""
    if not value:
        return "is empty"
    if len(value) > MODEL_ID_MAX_LEN:
        return f"is longer than {MODEL_ID_MAX_LEN} characters"
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        return "contains whitespace or a control character"
    if any(ord(ch) > 0x7E for ch in value):
        return "contains a non-ASCII character"
    if "://" in value or value[:4].lower() == "http":
        return "looks like a URL, not a model id"
    if any(ch in _FORBIDDEN_CHARS for ch in value):
        return "contains a character model ids never use (one of ? # & = < > quotes or backtick)"
    if _MODEL_ID_RE.fullmatch(value) is None:  # pragma: no cover - the checks above are exhaustive
        return "is not a valid model id"
    return None


def validate_model_id(value: str) -> str | None:
    """Return why ``value`` cannot be a model id, or ``None`` when it can (R-V4-21).

    The secret check runs first; no reason ever contains any part of
    ``value``, so callers may put the reason in errors, audit rows and logs.

    Args:
        value: The model id (or voice / avatar id) an admin typed.

    Returns:
        A short, value-free reason, or ``None``.
    """
    if looks_like_secret(value):
        return SECRET_LOOKING_REASON
    return _syntax_reason(value)


class IdIssue(BaseModel):
    """What :func:`validate_id_value` found wrong with an id-like value (R-V4-31). Value-free."""

    severity: Literal["error", "warning"]
    reason: str


def validate_id_value(value: str) -> IdIssue | None:
    """The id rule for an id-like field (``ID_LIKE_FIELD_NAMES``, ``type="catalog"``), R-V4-31.

    In order: a :data:`SECRET_PREFIXES` prefix is an ``error``; a syntax
    failure is an ``error`` (checked before the bare-token rule, so a
    ``=``-padded key stays an error); a bare token is only a ``warning``,
    because a vendor's real voice or avatar id may be 32 hex characters.
    A model id keeps :func:`validate_model_id`, where a bare token is an error.

    Args:
        value: The field value an admin typed.

    Returns:
        The issue with a value-free reason, or ``None``.
    """
    if _has_secret_prefix(value):
        return IdIssue(severity="error", reason=SECRET_LOOKING_REASON)
    reason = _syntax_reason(value)
    if reason is not None:
        return IdIssue(severity="error", reason=reason)
    if _is_bare_token(value):
        return IdIssue(severity="warning", reason=BARE_TOKEN_ID_REASON)
    return None


def id_like_field(name: str) -> bool:
    """Whether a (possibly dotted) field name carries an id checked by :func:`validate_model_id`."""
    return name.rsplit(".", 1)[-1] in ID_LIKE_FIELD_NAMES


class ModelCapabilities(BaseModel):
    """What one model can do, as far as the platform knows (D-V4-24, R-V4-23).

    ``None`` means unknown. Resolved per field from, in order: the admin's
    declaration, the last "Test model" probe, the live catalog item's
    metadata, the registry; ``source`` names the source of ``vision``.
    """

    vision: bool | None = None
    tools: bool | None = None
    audio_in: bool | None = None
    audio_out: bool | None = None
    streaming: bool | None = None
    context_tokens: int | None = None
    source: Literal["declared", "detected", "catalog", "registry"] | None = None
    reasoning: bool | None = Field(
        None,
        description=(
            "V6-31: the model thinks before it answers (a reasoning model). From OpenRouter's catalog "
            "(`reasoning` in `supported_parameters`, or a `reasoning` record) or the registry's "
            "`ModelSpec.reasoning`. `null` = unknown."
        ),
    )
    reasoning_efforts: list[ReasoningEffort] | None = Field(
        None,
        description=(
            "V6-31: the `reasoning_effort` values the model accepts, lowest first (OpenRouter's "
            "`reasoning.supported_efforts`, or the registry's `ModelSpec.reasoning_efforts`). Empty = "
            "the model takes no effort setting; `null` = unknown."
        ),
    )
    request_parameters: list[str] | None = Field(
        None,
        description=(
            "V6-31: the chat request parameters the model accepts (OpenRouter's `supported_parameters`, "
            "e.g. `tools`, `temperature`, `reasoning_effort`). The worker leaves out any of "
            "`OPTIONAL_REQUEST_PARAMETERS` missing from this list; `null` = unknown, and every "
            "parameter is sent as before."
        ),
    )


def accepts_parameter(capabilities: ModelCapabilities | None, name: str) -> bool | None:
    """Whether the model accepts the chat request parameter ``name`` (V6-31); ``None`` = unknown."""
    if capabilities is None or capabilities.request_parameters is None:
        return None
    return name in capabilities.request_parameters


def lowest_reasoning_effort(efforts: list[ReasoningEffort] | None) -> ReasoningEffort | None:
    """The lowest of ``efforts`` in :data:`REASONING_EFFORTS` order, or ``None`` when there is none."""
    known = [effort for effort in REASONING_EFFORTS if effort in (efforts or [])]
    return known[0] if known else None


def reasoning_effort_to_send(
    capabilities: ModelCapabilities | None, requested: str | None
) -> ReasoningEffort | str | None:
    """The ``reasoning_effort`` a request should carry for a configured value (V6-31).

    The one rule the worker applies and the api's validator explains:

    * nothing known about the model (``capabilities`` is ``None``, or both ``reasoning`` and
      ``reasoning_efforts`` are unknown): ``requested`` as it is, today's behaviour;
    * the model does not reason, or its known parameters leave out ``reasoning_effort``: nothing;
    * a value is configured: it, or when the model lists its efforts and not this one, the
      nearest listed effort above it (else the highest);
    * nothing configured on a reasoning model that lists its efforts: the lowest, because every
      level above it adds seconds to a spoken reply.

    Args:
        capabilities: The model's resolved capability view.
        requested: The agent's stored ``reasoning_effort`` (``None`` or ``""`` = not set).

    Returns:
        The effort to send, or ``None`` to send none.
    """
    wanted = requested or None
    if capabilities is None or (capabilities.reasoning is None and capabilities.reasoning_efforts is None):
        return wanted
    if capabilities.reasoning is False or accepts_parameter(capabilities, REASONING_EFFORT_FIELD) is False:
        return None
    efforts = capabilities.reasoning_efforts
    if wanted is not None:
        if not efforts or wanted in efforts:
            return wanted
        if wanted not in REASONING_EFFORTS:
            return None
        listed = [effort for effort in REASONING_EFFORTS if effort in efforts]
        above = [
            effort for effort in listed if REASONING_EFFORTS.index(effort) > REASONING_EFFORTS.index(wanted)
        ]
        return above[0] if above else listed[-1]
    return lowest_reasoning_effort(efforts)


class CatalogFilter(BaseModel):
    """Which vendor list items a registry entry keeps (D-V4-25, R-V4-28).

    Applied by the api after the adapter fetch, so one vendor adapter can
    serve several entries (OpenAI's one ``/models`` list feeds six). Every
    set condition must hold: ``id_include`` must match, ``id_exclude`` must
    not (both :func:`re.search`, case-insensitive), and the list at the dotted
    ``meta_path`` must contain ``meta_contains``.
    """

    id_include: str | None = None
    id_exclude: str | None = None
    meta_path: str | None = None
    meta_contains: str | None = None

    @field_validator("id_include", "id_exclude")
    @classmethod
    def _compiles(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError(f"not a valid regular expression: {exc}") from exc
        return value

    @model_validator(mode="after")
    def _meta_pair(self) -> "CatalogFilter":
        if (self.meta_path is None) != (self.meta_contains is None):
            raise ValueError("meta_path and meta_contains are set together")
        return self

    def matches_id(self, item_id: str) -> bool:
        """Whether ``item_id`` passes the id conditions (the meta condition is not checked)."""
        if self.id_include is not None and re.search(self.id_include, item_id, re.IGNORECASE) is None:
            return False
        return self.id_exclude is None or re.search(self.id_exclude, item_id, re.IGNORECASE) is None

    def matches(self, item_id: str, meta: dict[str, Any]) -> bool:
        """Whether a catalog item (its id and raw vendor ``meta``) passes every condition."""
        if not self.matches_id(item_id):
            return False
        if self.meta_path is None or self.meta_contains is None:
            return True
        node: Any = meta
        for part in self.meta_path.split("."):
            if not isinstance(node, dict):
                return False
            node = node.get(part)
        return isinstance(node, list) and self.meta_contains in node


class PageSpec(BaseModel):
    """How to follow a vendor list's pages (D-V4-25).

    * ``token`` / ``cursor``: send ``param=<value at next_path>`` until the
      value is missing or empty (Gemini ``pageToken``/``nextPageToken``,
      Anthropic ``after_id``/``last_id``, Cartesia ``starting_after``/``next_page``).
    * ``offset``: send ``param=<items so far>`` until a short or empty page.
    * ``page``: send ``param=<n>`` from 0 (``page_number``), stopping at the
      total page count read from ``next_path`` or at an empty page (Hume).

    ``more_path`` (optional) names a boolean such as ``has_more``; ``false``
    stops the loop before another request. ``size_param=size`` rides on
    every request. The loop never exceeds ``max_pages``.
    """

    kind: Literal["cursor", "token", "offset", "page"]
    param: str
    next_path: str | None = None
    more_path: str | None = None
    size_param: str | None = None
    size: int | None = None
    max_pages: int = Field(10, ge=1, le=50)


#: Catalog adapters whose vendor list answers without a key (R-V4-9, R-V4-28). They
#: may be a `CatalogSpec.adapter` but never a `ProviderSpec.test`: a list that
#: answers a bogus key cannot tell a good key from a bad one. (OpenRouter's
#: ``/models`` is public too, but its adapter probes ``/key`` first, so it is keyed.)
#: The api's adapters declare ``public=True`` for exactly these names.
PUBLIC_CATALOG_ADAPTERS: frozenset[str] = frozenset(
    {"deepgram_stt_models", "deepgram_tts_models", "rime_voices"}
)

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

#: The note a vendor-deprecated model id carries (V6-02, D-V6-4a); ``ModelSpec.deprecated`` is the flag.
DEPRECATED_BY_VENDOR = "deprecated by the vendor"

#: ElevenLabs output formats (``TTSEncoding`` in livekit-plugins-elevenlabs 1.8.3 ``models.py``).
ELEVENLABS_ENCODINGS: list[str] = [
    "mp3_22050_32",
    "mp3_24000_48",
    "mp3_44100",
    "mp3_44100_32",
    "mp3_44100_64",
    "mp3_44100_96",
    "mp3_44100_128",
    "mp3_44100_192",
    "opus_48000_32",
    "opus_48000_64",
    "opus_48000_96",
    "opus_48000_128",
    "opus_48000_192",
    "pcm_8000",
    "pcm_16000",
    "pcm_22050",
    "pcm_24000",
    "pcm_32000",
    "pcm_44100",
    "pcm_48000",
]

#: MiniMax output formats (``TTSAudioFormat`` in livekit-plugins-minimax-ai 1.8.3 ``tts.py``).
MINIMAX_AUDIO_FORMATS: list[str] = ["pcm", "mp3", "flac", "wav"]


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
    recommended: str | int | float | bool | None = Field(
        None,
        description=(
            "The value the console pre-selects for a new agent (V6-02, D-V6-4c). `default` stays the "
            "value a stored reference without the field resolves to (the plugin's own behaviour), so an "
            "existing agent never changes; `recommended` is what a new one should use."
        ),
    )


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
    deprecated: bool = Field(
        False,
        description=(
            "The vendor deprecated this id (V6-02, D-V6-4a). It stays listed so a stored reference "
            "still resolves; the validator warns and the console offers the entry's `default_model`."
        ),
    )
    reasoning: bool | None = Field(
        None,
        description=(
            "V6-31: the model thinks before it answers. Set only where the plugin source or the "
            "vendor's published listing says so; `null` = unknown (the live catalog decides)."
        ),
    )
    reasoning_efforts: list[ReasoningEffort] = Field(
        default=[],
        description=(
            "V6-31: the `reasoning_effort` values the model accepts through this provider, lowest "
            "first. Empty = not recorded."
        ),
    )
    end_of_turn: bool = Field(
        False,
        description=(
            "STT only (V6-34): this model can decide when the caller's turn ends (LiveKit Inference's "
            "Deepgram Flux models). Unlike `ProviderCapabilities.end_of_turn` it is an opt-in: the "
            'worker runs `turn_detection="stt"` only when the agent asks for it '
            '(`pipeline.turn_detector.mode: "stt"` or the `fast` conversation preset), so a stored '
            "agent keeps the turn detector it had. See `stt_end_of_turn()`."
        ),
    )


class CatalogSpec(BaseModel):
    """How to list a provider's models, voices, avatars or personas from the vendor.

    ``filter`` and ``page`` (V4-07, D-V4-25) are registry data the api applies
    around the adapter: ``filter`` keeps this entry's items out of a shared
    vendor list, ``page`` follows the vendor's pagination.
    """

    adapter: str
    kinds: list[CatalogKind] = []
    ttl_s: int = 3600
    filter: CatalogFilter | None = None
    page: PageSpec | None = None


class ProviderCapabilities(BaseModel):
    """What a provider can do, used to gate UI affordances and runtime behaviour."""

    video_input: bool = False
    tool_calling: bool = True
    silent_tool_reply: bool = False
    voices: list[str] = []
    text_modality: bool = False
    audio_input: bool = True
    languages: list[str] = []
    """The languages this provider transcribes (STT) or speaks (TTS), as base codes (``hi``,
    not ``hi-IN``) from the vendor's documentation (V5-31). Empty = not recorded, never "none":
    validators treat an empty list as unknown and say nothing."""
    vision: bool | None = None
    voices_dynamic: bool = False
    cloud_only: bool = False
    platforms: list[str] = []
    language_detection: str | None = Field(
        None,
        description=(
            "STT only (V5-31): the value of the entry's `language` field that makes the transcriber "
            "detect the language itself (`multi` for Deepgram and LiveKit Inference, `unknown` for "
            "Sarvam; the worker maps `multi` to `detect_language` for the OpenAI transcriptions "
            "class). Unset = the entry cannot be asked to detect the language."
        ),
    )
    detect_languages: list[str] = Field(
        default=[],
        description=(
            "STT only (V5-31): the base codes automatic detection covers when it is narrower than "
            "`languages` (Deepgram's `multi` covers ten). Empty = the same as `languages`."
        ),
    )
    language_switch: bool = Field(
        False,
        description=(
            "STT only (V5-31): `update_options(language=...)` changes the language of a running "
            "transcriber (checked against the plugin source for livekit-agents 1.8.3). False = a "
            "mid-call switch leaves the transcriber as it is."
        ),
    )
    redaction: list[str] = []
    """STT only (V5-30): the ``PrivacyConfig.stt_redact`` classes the plugin's ``redact`` argument
    accepts. Empty = the provider cannot mask while transcribing; the setting is then ignored
    with a validation warning."""
    streaming: bool | None = Field(
        None,
        description=(
            "STT and TTS only (V6-02, D-V6-2): whether the entry streams with its registry defaults, "
            "from the plugin's `STTCapabilities`/`TTSCapabilities(streaming=...)` at livekit-agents "
            "1.8.3. A transcriber that streams gives live partial transcripts; a voice that streams "
            "starts speaking before the whole sentence is written. `false` = one whole request per "
            "utterance or sentence (the SDK wraps it in a `StreamAdapter`). `null` = not recorded; "
            "`streaming_note` then says why. See `speech_streams()` for a stored reference."
        ),
    )
    streaming_field: str | None = Field(
        None,
        description=(
            "STT and TTS only (V6-02): the boolean field that turns streaming on when it is off by "
            "default (`use_realtime` on `openai-stt`, `use_websocket` on `rime-tts`). A reference "
            "streams when it sets that field true."
        ),
    )
    streaming_note: str | None = Field(
        None,
        description=(
            "STT and TTS only (V6-02): when streaming depends on something else (a model, a voice, an "
            "endpoint), in plain words."
        ),
    )
    end_of_turn: bool = Field(
        False,
        description=(
            "STT only (V6-02, D-V6-5): the transcriber decides when the caller's turn ends (Deepgram "
            'Flux). The worker then runs the session with the SDK\'s `turn_detection="stt"` instead of '
            "the platform's default turn detector. V6-34: a gateway entry whose models differ records "
            "it per model instead (`ModelSpec.end_of_turn`, an opt-in)."
        ),
    )
    avatar_aspect: Literal["portrait", "landscape", "square"] | None = Field(
        None,
        description=(
            "Avatar only (V6-26): the vendor's documented native video aspect, cited in "
            "`avatar_aspect_note`. The console (and the session stage, once a package threads it "
            "through `AgentPublicOut` — docs/v6/_asks.md #151) sizes the video well to this before "
            "the first frame arrives, so the layout does not jump. `None` = not documented (most "
            "vendors' resolution is configurable per call, or undocumented): the well waits for the "
            "real track dimensions instead of guessing."
        ),
    )
    avatar_aspect_note: str | None = Field(
        None,
        description=(
            "Avatar only (V6-26): one line citing where `avatar_aspect` came from (the vendor's "
            "documented default resolution or aspect), e.g. 'Tavus replica video defaults to "
            "1280x720 (docs.tavus.io)'. Unset when `avatar_aspect` is unset."
        ),
    )


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
    probe: str | None = Field(
        None,
        description=(
            "The api's 'Test model' probe adapter for this entry (docs/v4/CUSTOM-MODELS.md D-V4-26): "
            "a capped POST with a payload, deliberately separate from `test` (a GET that lists). "
            "Unset means no model test; never set on vad, turn_detection or noise_cancellation."
        ),
    )
    price_ref: str | None = Field(
        None,
        description=(
            "Price this entry with that registry entry's rows (docs/v4/COSTS.md D-V4-39), e.g. "
            "`openai-responses-llm` -> `openai-llm`. Never a self-reference; unset = the entry's own rows."
        ),
    )
    notes: str | None = None
    telephony_variant: str | None = Field(
        None,
        description=(
            "noise_cancellation only (V5-07, D-V5-30): the dotted path of the variant tuned for phone "
            "audio. The worker builds it instead of `python_class` on a phone call when the agent uses "
            "the `telephony` conversation preset. Unset = the entry has no telephony variant."
        ),
    )
    price_note: str | None = Field(
        None,
        description=(
            "A plain-language price line the console shows beside the provider when it is metered "
            "outside the price table (V5-07: LiveKit Cloud noise cancellation)."
        ),
    )
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
            "declares the same secret field names. V6-32: every entry of a vendor whose entries take "
            "the same account key names one home (`credential_family`), and a row stored under any "
            "member of the family serves the whole family."
        ),
    )
    listed: bool = Field(
        True,
        description=(
            "V6-32: whether the console offers this entry in its pickers and counts it on a key's "
            "tags. `False` keeps the entry valid and runnable for a stored agent that already uses "
            "it (validation, resolution and the worker are unchanged); `unlisted_note` says why in "
            "plain words."
        ),
    )
    unlisted_note: str | None = Field(
        None,
        description="V6-32: the plain-words reason an entry is not offered (`listed` is `False`).",
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


def _reasoning_effort_field() -> FieldSpec:
    """The ``reasoning_effort`` option of an LLM whose plugin sends it (V6-31); no default."""
    return FieldSpec(
        name=REASONING_EFFORT_FIELD,
        label="Reasoning effort",
        type="enum",
        options=list(REASONING_EFFORTS),
        help="How long a reasoning model thinks before it answers. Lower is faster. On a live call, "
        "medium or higher adds several seconds to every reply. Left empty, the agent uses the lowest "
        "level the model supports. Models that do not reason ignore it.",
    )


#: The LiveKit turn detector needs at least this much VAD silence (seconds): livekit-agents 1.8.3
#: `voice/audio_recognition.py` `_check_vad_silence_requirement` raises below
#: `(MIN_SILENCE_DURATION_MS + 50) / 1000` (`inference/eot/base.py`: 200 ms), and LiveKit's
#: turn-detector page says "at least 0.25 seconds" (2026-09-30).
VAD_MIN_SILENCE_WITH_TURN_DETECTOR = 0.25

#: The VAD field that sets it (V6-34).
VAD_MIN_SILENCE_FIELD = "min_silence_duration"

#: ``openrouter-llm``'s boolean that sends the agent's id as ``user`` and ``prompt_cache_key`` (V6-34).
STICKY_ROUTING_FIELD = "sticky_routing"


def _vad_fields(*, silence_default: str) -> list[FieldSpec]:
    """The VAD timing fields shared by ``silero-vad`` and ``inference-vad`` (V6-34)."""
    return [
        FieldSpec(
            name=VAD_MIN_SILENCE_FIELD,
            label="Silence before speech ends (s)",
            type="number",
            placeholder=silence_default,
            help="How long the caller must be quiet before their speech counts as ended. Lower lets the "
            "agent reply sooner. With the turn detector it must be at least 0.25. Default "
            f"{silence_default}.",
        ),
        FieldSpec(
            name="activation_threshold",
            label="Speech threshold",
            type="number",
            placeholder="0.5",
            help="How confident the detector must be that a sound is speech (0-1). Higher ignores more "
            "background noise but can miss quiet callers. Default 0.5.",
        ),
        FieldSpec(
            name="prefix_padding_duration",
            label="Audio kept before speech (s)",
            type="number",
            placeholder="0.5",
            help="How much audio before the detected start of speech is kept, so the first word is not "
            "clipped. Default 0.5.",
        ),
    ]


#: Deepgram Flux's end-of-turn options (V6-02 `deepgram-flux-stt`; V6-34 LiveKit Inference Flux).
FLUX_OPTION_FIELDS: tuple[str, ...] = ("eot_threshold", "eager_eot_threshold", "eot_timeout_ms")


def _flux_option_fields(
    *,
    eot_placeholder: str,
    eager_placeholder: str,
    eager_help: str,
    timeout_placeholder: str,
    suffix: str = "",
) -> list[FieldSpec]:
    """Flux's three end-of-turn fields (:data:`FLUX_OPTION_FIELDS`), worded the same everywhere."""
    return [
        FieldSpec(
            name="eot_threshold",
            label="End-of-turn confidence",
            type="number",
            placeholder=eot_placeholder,
            help="How sure Flux must be that the caller finished (0.5-0.9). Higher waits longer." + suffix,
        ),
        FieldSpec(
            name="eager_eot_threshold",
            label="Early reply confidence",
            type="number",
            placeholder=eager_placeholder,
            help=eager_help + suffix,
        ),
        FieldSpec(
            name="eot_timeout_ms",
            label="End-of-turn timeout (ms)",
            type="number",
            placeholder=timeout_placeholder,
            help="End the turn after this much silence even if Flux is unsure." + suffix,
        ),
    ]


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
    probe: str | None = None,
    price_ref: str | None = None,
    notes: str | None = None,
    docs_url: str | None = None,
    get_key_url: str | None = None,
    telephony_variant: str | None = None,
    price_note: str | None = None,
) -> ProviderSpec:
    """Build a full-breadth entry added by V2-05 (CONTRACTS-V2 §7, PLAN-V2 §8 R-V2-1).

    Every entry built here carries ``worker_image="full"`` (the field's own
    default) so the ``status`` alias stays ``"deferred"`` for all of them and
    the v1 ``mvp`` id set is untouched, regardless of ``availability``. Most
    calls leave ``availability`` at its default ``"available"``; the explicit
    corrections from the catalog (PlayAI, NVIDIA PersonaPlex, the two
    out-of-tree noise-cancellation packages with an unread class path) pass
    ``availability="deferred"``; Fireworks STT passes ``"removed"`` (V6-02). MiniMax passed
    ``"incompatible"`` until V6-02 found its 1.8.x package (``livekit-plugins-minimax-ai``).

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
        probe=probe,
        price_ref=price_ref,
        notes=notes,
        docs_url=docs_url,
        get_key_url=get_key_url,
        telephony_variant=telephony_variant,
        price_note=price_note,
    )


# ------------------------------------------------ live catalog wiring (V4-07, D-V4-25)
#: TTLs: a vendor list rarely changes within a day; OpenRouter's churns faster.
TTL_PUBLIC_LIST_S = 86_400
TTL_OPENROUTER_S = 21_600

#: OpenAI's one ``/v1/models`` list mixes every kind; each entry keeps its own slice.
_OPENAI_LLM_FILTER = CatalogFilter(
    id_include=r"^(gpt-|o\d|chatgpt-)",
    id_exclude=r"-(tts|transcribe|realtime|audio|search|instruct)|embedding|image|whisper|dall-e",
)
_OPENAI_TTS_FILTER = CatalogFilter(id_include=r"(^|-)tts(-|$)")
_OPENAI_STT_FILTER = CatalogFilter(id_include=r"transcribe|^whisper")
_OPENAI_REALTIME_FILTER = CatalogFilter(id_include=r"realtime")
_OPENAI_EMBEDDING_FILTER = CatalogFilter(id_include=r"^text-embedding")
_OPENAI_IMAGE_FILTER = CatalogFilter(id_include=r"^(gpt-image|dall-e)")

#: Gemini's ``/v1beta/models``: ``pageSize`` <= 1000 with ``pageToken``/``nextPageToken``.
_GEMINI_PAGE = PageSpec(
    kind="token", param="pageToken", next_path="nextPageToken", size_param="pageSize", size=1000
)
_GEMINI_LLM_FILTER = CatalogFilter(
    id_exclude=r"-tts|embedding|image|live|native-audio",
    meta_path="supportedGenerationMethods",
    meta_contains="generateContent",
)
_GEMINI_LIVE_FILTER = CatalogFilter(
    meta_path="supportedGenerationMethods", meta_contains="bidiGenerateContent"
)
_GEMINI_IMAGE_FILTER = CatalogFilter(id_include=r"-image")

#: Anthropic's ``/v1/models``: ``limit`` <= 1000, ``after_id`` = the previous page's ``last_id``.
_ANTHROPIC_PAGE = PageSpec(
    kind="cursor", param="after_id", next_path="last_id", more_path="has_more", size_param="limit", size=1000
)


def _openai_catalog(filter_: CatalogFilter) -> CatalogSpec:
    return CatalogSpec(adapter="openai_models", kinds=["models"], filter=filter_)


def _gemini_catalog(filter_: CatalogFilter) -> CatalogSpec:
    return CatalogSpec(adapter="gemini_models", kinds=["models"], filter=filter_, page=_GEMINI_PAGE)


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
            ),
            # V6-34: Deepgram Flux's end-of-turn options, sent in `extra_kwargs` (livekit-agents 1.8.3
            # `inference/stt.py` `DeepgramFluxOptions`; docs.livekit.io/agents/models/stt/deepgram,
            # 2026-09-30). The worker sends them only with a Flux model.
            *_flux_option_fields(
                eot_placeholder="0.7",
                eager_placeholder="0.5",
                eager_help="Start preparing a reply before the turn is certainly over (0.3-0.9, at most "
                "the end-of-turn confidence). LiveKit's default is 0.5.",
                timeout_placeholder="5000",
                suffix=" Deepgram Flux models only.",
            ),
        ],
        models=[
            # V6-02 (D-V6-8): every id checked against docs.livekit.io/agents/models/inference
            # (rendered 2026-09-27T22:10Z, fetched 2026-09-28); none of these is deprecated there.
            ModelSpec(id="deepgram/nova-3", label="Deepgram Nova 3"),
            # V6-34: Flux ends turns itself through Inference too (`turn_detection="stt"`, the LiveKit
            # Deepgram page, 2026-09-30); an opt-in per agent (`ModelSpec.end_of_turn`). Only Flux: the
            # gateway's other models send a final transcript per segment, which the SDK would take as
            # the end of every turn (`inference/stt.py` `_process_transcript`).
            ModelSpec(id="deepgram/flux-general-en", label="Deepgram Flux (general, en)", end_of_turn=True),
            ModelSpec(
                id="deepgram/flux-general-multi", label="Deepgram Flux (multilingual)", end_of_turn=True
            ),
            ModelSpec(id="deepgram/nova-3-medical", label="Deepgram Nova 3 Medical"),
            ModelSpec(id="assemblyai/universal-streaming", label="AssemblyAI Universal Streaming"),
            ModelSpec(id="assemblyai/universal-3-6-pro", label="AssemblyAI Universal-3.6 Pro Streaming"),
            ModelSpec(id="cartesia/ink-2", label="Cartesia Ink 2"),
            ModelSpec(id="cartesia/ink-whisper", label="Cartesia Ink Whisper"),
            ModelSpec(id="google/gemini-3.5-transcribe-live", label="Gemini 3.5 Transcribe Live"),
            ModelSpec(id="speechmatics/linden-1", label="Speechmatics Linden-1"),
            ModelSpec(id="xai/stt-2", label="xAI Speech to Text 2"),
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
            ),
            # V6-31: sent as `extra_kwargs["reasoning_effort"]` (`inference.ChatCompletionOptions`),
            # the way livekit-agents 1.8.3's own `evals/evaluation.py` sends it.
            _reasoning_effort_field(),
        ],
        models=[
            ModelSpec(
                id="google/gemma-4-31b-it",
                label="Gemma 4 31B Instruct",
                note="text-only on LiveKit Inference, and ignores image parts silently",
            ),
            ModelSpec(id="google/gemini-3.5-flash", label="Gemini 3.5 Flash", supports_video=True),
            # V6-02 (D-V6-8): a curated pick of the current ids on docs.livekit.io/agents/models/inference
            # (rendered 2026-09-27T22:10Z). Vision is not flagged until the platform verifies it.
            ModelSpec(id="google/gemini-3.8-flash", label="Gemini 3.8 Flash"),
            ModelSpec(id="google/gemini-3.1-flash-lite", label="Gemini 3.1 Flash Lite"),
            # V6-31: reasoning is recorded only for OpenAI's GPT-5 family, which livekit-agents 1.8.3
            # itself treats as reasoning models (`inference/llm.py` `_MIN_REASONING_EFFORT`,
            # `plugins/openai/models.py` `_supports_reasoning_effort`); the effort lists are OpenRouter's
            # public listing for the same ids (2026-09-29). The Gemini ids stay unknown: whether
            # Inference passes `reasoning_effort` to them is not verified. Note: with tools, the SDK
            # strips `reasoning_effort` for `gpt-5.2*`/`gpt-5.4*` ids itself
            # (`_REASONING_EFFORT_TOOL_INCOMPATIBLE_PREFIXES`), so GPT-5.4 mini runs at its own default.
            ModelSpec(id="openai/gpt-4.1", label="GPT-4.1", reasoning=False),
            # V6-34: small, non-reasoning ids from the same docs table (2026-09-30), quick to answer.
            ModelSpec(id="openai/gpt-4.1-mini", label="GPT-4.1 mini", reasoning=False),
            ModelSpec(id="openai/gpt-4.1-nano", label="GPT-4.1 nano", reasoning=False),
            ModelSpec(id="openai/gpt-4o-mini", label="GPT-4o mini", reasoning=False),
            ModelSpec(
                id="openai/gpt-5.5",
                label="GPT-5.5",
                reasoning=True,
                reasoning_efforts=["none", "low", "medium", "high", "xhigh"],
            ),
            ModelSpec(
                id="openai/gpt-5.4-mini",
                label="GPT-5.4 mini",
                reasoning=True,
                reasoning_efforts=["none", "low", "medium", "high", "xhigh"],
            ),
            ModelSpec(
                id="openai/gpt-5.6-luna",
                label="GPT-5.6 Luna",
                reasoning=True,
                reasoning_efforts=["none", "low", "medium", "high", "xhigh", "max"],
            ),
            ModelSpec(id="openai/gpt-oss-120b", label="GPT-OSS 120B"),
            ModelSpec(id="xai/grok-4.7", label="Grok 4.7"),
            ModelSpec(id="deepseek-ai/deepseek-v4.1-flash", label="DeepSeek V4.1 Flash"),
        ],
        default_model="google/gemma-4-31b-it",
        capabilities=ProviderCapabilities(cloud_only=True),
        docs_url="https://docs.livekit.io/agents/models/llm/",
        probe="openai_chat",
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
            # V6-02 (D-V6-8): checked against docs.livekit.io/agents/models/inference (rendered
            # 2026-09-27T22:10Z). ElevenLabs is not in that table and is not offered here.
            ModelSpec(id="inworld/inworld-tts-2", label="Inworld TTS 2"),
            # V6-34: in the docs table (2026-09-30) and priced on its own row (`pricing.py`, $15 per
            # 1M characters on livekit.com/pricing/inference); the lowest independently measured
            # time to first audio of the Inference voices (docs/research-v6/low-latency-stack.md §3.3).
            ModelSpec(
                id="inworld/inworld-tts-2-flash",
                label="Inworld TTS 2 Flash",
                note="fastest to start speaking. Slightly lower quality than Inworld TTS 2",
            ),
            ModelSpec(id="cartesia/sonic-3.6", label="Cartesia Sonic 3.6"),
            ModelSpec(id="cartesia/sonic-3", label="Cartesia Sonic 3"),
            ModelSpec(id="deepgram/aura-2", label="Deepgram Aura 2"),
            ModelSpec(id="deepgram/flux-tts", label="Deepgram Flux TTS"),
            ModelSpec(id="fishaudio/s2.1-pro", label="Fish Audio S2.1 Pro"),
            ModelSpec(id="gradium/default", label="Gradium TTS"),
            ModelSpec(id="rime/coda", label="Rime Coda"),
            ModelSpec(id="rime/mistv3", label="Rime Mist v3"),
            ModelSpec(id="xai/tts-1", label="xAI Text to Speech"),
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
        catalog=_gemini_catalog(_GEMINI_LIVE_FILTER),
        test="gemini_models",
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
        probe="gemini_live_ws",
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
        catalog=_openai_catalog(_OPENAI_REALTIME_FILTER),
        test="openai_models",
        notes="Its base_url override is for OpenAI-compatible realtime endpoints, and OpenRouter has none.",
        capabilities=ProviderCapabilities(
            video_input=False,
            tool_calling=True,
            silent_tool_reply=True,
            voices=["marin", "cedar", "alloy", "ash", "ballad", "coral", "sage", "verse"],
            text_modality=True,
        ),
        docs_url="https://docs.livekit.io/agents/models/realtime/openai/",
        get_key_url="https://platform.openai.com/api-keys",
        probe="openai_realtime_ws",
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
        ],
        default_model="nova-3",
        # V5-30: `livekit.plugins.deepgram.STT(redact=[...])` (plugin signature snapshot).
        capabilities=ProviderCapabilities(redaction=["pci", "pii", "phi", "numbers"]),
        # Public list (R-V4-9): a catalog, never the credential test.
        catalog=CatalogSpec(adapter="deepgram_stt_models", kinds=["models"], ttl_s=TTL_PUBLIC_LIST_S),
        # V6-02 (D-V6-5): Flux is a different class (`STTv2`) with its own end-of-turn detection,
        # so it has its own entry, `deepgram-flux-stt`; a stored ref naming a Flux model here gets
        # a validator error that points at it (api `config_service`).
        docs_url="https://docs.livekit.io/agents/models/stt/deepgram/",
        get_key_url="https://console.deepgram.com/",
        probe="deepgram_listen",
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
            # V6-31: `openai.LLM(reasoning_effort=...)`; unset, livekit-plugins-openai 1.8.3 picks
            # `none`/`minimal` itself for the GPT-5 ids it knows (`_supports_reasoning_effort`).
            _reasoning_effort_field(),
        ],
        models=[
            ModelSpec(id="gpt-4.1", label="GPT-4.1", reasoning=False),
            ModelSpec(id="gpt-4o", label="GPT-4o", reasoning=False),
            ModelSpec(id="gpt-4.1-mini", label="GPT-4.1 mini", reasoning=False),
        ],
        default_model="gpt-4.1",
        catalog=_openai_catalog(_OPENAI_LLM_FILTER),
        test="openai_models",
        docs_url="https://docs.livekit.io/agents/models/llm/openai/",
        get_key_url="https://platform.openai.com/api-keys",
        probe="openai_chat",
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
        catalog=_gemini_catalog(_GEMINI_LLM_FILTER),
        test="gemini_models",
        docs_url="https://docs.livekit.io/agents/models/llm/gemini/",
        get_key_url="https://aistudio.google.com/apikey",
        probe="gemini_generate",
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
        # V6-02 (D-V6-4b): `sonic-3.6` is the newer default; `sonic-3` still works.
        models=[ModelSpec(id="sonic-3.6", label="Sonic 3.6"), ModelSpec(id="sonic-3", label="Sonic 3")],
        default_model="sonic-3.6",
        catalog=CatalogSpec(adapter="cartesia_voices", kinds=["voices"]),
        test="cartesia_voices",
        docs_url="https://docs.livekit.io/agents/models/tts/cartesia/",
        get_key_url="https://play.cartesia.ai/keys",
        probe="cartesia_tts",
    ),
    ProviderSpec(
        id="elevenlabs-tts",
        kind="tts",
        label="ElevenLabs",
        vendor="ElevenLabs",
        package="livekit-plugins-elevenlabs",
        python_class="livekit.plugins.elevenlabs.TTS",
        secret_fields=[_api_key("ElevenLabs API key", env="ELEVEN_API_KEY")],
        fields=[
            FieldSpec(name="voice_id", label="Voice id", type="string"),
            # V6-02 (D-V6-4c): the plugin's kwarg is `encoding` (`TTSEncoding` in
            # livekit-plugins-elevenlabs 1.8.3 `models.py`); unset keeps its `mp3_22050_32`.
            FieldSpec(
                name="encoding",
                label="Audio format",
                type="enum",
                options=ELEVENLABS_ENCODINGS,
                recommended="pcm_24000",
                placeholder="mp3_22050_32",
                help="Uncompressed audio (pcm_24000) starts playing sooner than MP3. Unset keeps MP3.",
            ),
        ],
        # V6-02 (D-V6-4b): Flash v2.5 is the low-latency default; Turbo v2.5 still works.
        models=[
            ModelSpec(id="eleven_flash_v2_5", label="Eleven Flash v2.5"),
            ModelSpec(id="eleven_turbo_v2_5", label="Eleven Turbo v2.5"),
        ],
        default_model="eleven_flash_v2_5",
        catalog=CatalogSpec(
            adapter="elevenlabs_voices",
            kinds=["voices"],
            page=PageSpec(
                kind="token",
                param="next_page_token",
                next_path="next_page_token",
                more_path="has_more",
                size_param="page_size",
                size=100,
            ),
        ),
        test="elevenlabs_voices",
        docs_url="https://docs.livekit.io/agents/models/tts/elevenlabs/",
        get_key_url="https://elevenlabs.io/app/settings/api-keys",
        probe="elevenlabs_tts",
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
        catalog=_openai_catalog(_OPENAI_TTS_FILTER),
        test="openai_models",
        docs_url="https://docs.livekit.io/agents/models/tts/openai/",
        get_key_url="https://platform.openai.com/api-keys",
        probe="openai_speech",
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
        probe="bey_avatar_get",
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
        probe="tavus_replica_get",
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
                help="Nested under simli_config, because this avatar has no top-level api_key kwarg.",
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
        notes="No top-level api_key or conn_options. Credential and face_id both live in the "
        "nested SimliConfig dataclass.",
        docs_url="https://docs.livekit.io/agents/integrations/avatar/simli/",
        get_key_url="https://www.simli.com/",
        probe="simli_face_member",
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
        catalog=_gemini_catalog(_GEMINI_IMAGE_FILTER),
        test="gemini_models",
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
        catalog=_openai_catalog(_OPENAI_IMAGE_FILTER),
        test="openai_models",
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
        catalog=_openai_catalog(_OPENAI_EMBEDDING_FILTER),
        test="openai_models",
        capabilities=ProviderCapabilities(tool_calling=False),
        get_key_url="https://platform.openai.com/api-keys",
        probe="openai_embeddings",
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

_OPENROUTER_SPEECH_UNLISTED = (
    "Not offered for new agents. OpenRouter's speech-to-text and text-to-speech wait for a whole "
    "turn or sentence instead of streaming, which adds seconds to every reply on a live call. Use "
    "the OpenRouter key for language models, image generation and embeddings. Agents that already "
    "use this entry keep working."
)


def _openrouter_key() -> FieldSpec:
    return _api_key("OpenRouter API key", env="OPENROUTER_API_KEY")


def _openrouter_base_url() -> FieldSpec:
    return FieldSpec(
        name="base_url",
        label="Base URL",
        type="string",
        default=OPENROUTER_BASE_URL,
        help="OpenRouter's OpenAI-compatible API root. Change it only for a proxy in front of OpenRouter.",
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
            # V6-31: `LLM.with_openrouter(reasoning_effort=...)`, sent as the top-level
            # `reasoning_effort` OpenRouter lists in each reasoning model's `supported_parameters`.
            _reasoning_effort_field(),
            FieldSpec(
                name="fallback_models",
                label="Fallback models",
                type="json",
                placeholder='["openai/gpt-4o-mini"]',
                help="Model ids tried in order when the primary is unavailable. Sent as OpenRouter's "
                "`models` array.",
            ),
            FieldSpec(
                name="provider",
                label="Provider preferences",
                type="json",
                placeholder='{"sort": "latency"}',
                help="OpenRouter provider preferences: `order`, `only`, `ignore`, `sort` "
                "(price/throughput/latency), `allow_fallbacks`, `require_parameters`, "
                "`data_collection`, `preferred_max_latency` and more. `require_parameters` defaults to "
                "true so "
                "a request with tools never lands on an endpoint that cannot call them. By default "
                "OpenRouter favours cheaper hosts. For a live call on a model several hosts serve, "
                '`{"sort": "latency"}` tries the fastest host first, or `{"order": ["groq", "cerebras"]}` '
                "pins the hosts you want in order.",
            ),
            # V6-34: `LLM.with_openrouter(user=..., prompt_cache_key=...)` (livekit-plugins-openai 1.8.3
            # `llm.py:454,460`, sent at `:976-977,:994-995`). Off for a stored agent (D-V6-31), on for a
            # new one (`recommended`).
            FieldSpec(
                name=STICKY_ROUTING_FIELD,
                label="Keep the same host during a call",
                type="boolean",
                default=False,
                recommended=True,
                help="Sends the agent's id with each request so OpenRouter keeps routing it to the same "
                "host, whose cache of the conversation is already warm. Later replies start sooner. "
                "Nothing about the caller is sent.",
            ),
            FieldSpec(
                name="site_url",
                label="Site URL",
                type="string",
                help="Sent as `HTTP-Referer` for OpenRouter's app rankings. The app name below only "
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
            # V6-31: only "does not reason" is recorded here (it changes nothing the worker sends);
            # whether a model reasons, and at which efforts, comes from OpenRouter's live catalog.
            ModelSpec(id="openai/gpt-4.1-mini", label="GPT-4.1 mini", supports_video=True, reasoning=False),
            ModelSpec(id="openai/gpt-4.1", label="GPT-4.1", supports_video=True, reasoning=False),
            ModelSpec(id="openai/gpt-4o-mini", label="GPT-4o mini", supports_video=True, reasoning=False),
            ModelSpec(id="google/gemini-3.5-flash", label="Gemini 3.5 Flash", supports_video=True),
            ModelSpec(id="anthropic/claude-sonnet-4.6", label="Claude Sonnet 4.6", supports_video=True),
        ],
        default_model="openai/gpt-4.1-mini",
        catalog=CatalogSpec(adapter="openrouter_llm_models", kinds=["models"], ttl_s=TTL_OPENROUTER_S),
        test="openrouter_llm_models",
        notes="Routes to hundreds of models on one key. `openrouter/auto` is not tool-safe and is "
        "deliberately not the default. Tool schemas go out with OpenAI's `strict` flag. If a routed "
        "non-OpenAI model rejects a tool call, pick an OpenAI model or an `order` of providers known "
        "to support strict tools.",
        docs_url="https://docs.livekit.io/agents/models/llm/openrouter/",
        get_key_url=_OPENROUTER_KEY_URL,
        probe="openai_chat",
    ),
    ProviderSpec(
        id="openrouter-stt",
        kind="stt",
        label="OpenRouter (STT)",
        vendor="OpenRouter",
        package="livekit-plugins-openai",
        python_class="livekit.plugins.openai.STT",
        credential_provider=OPENROUTER_CREDENTIAL_HOME,
        listed=False,
        unlisted_note=_OPENROUTER_SPEECH_UNLISTED,
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
        catalog=CatalogSpec(adapter="openrouter_stt_models", kinds=["models"], ttl_s=TTL_OPENROUTER_S),
        test="openrouter_stt_models",
        notes="Batch transcription over HTTP: no interim results. Each turn is transcribed after "
        "end-of-speech, so expect roughly half a second to two seconds more per turn than a streaming "
        "STT. For low latency prefer LiveKit Inference STT or Deepgram.",
        docs_url="https://docs.livekit.io/agents/models/stt/openai/",
        get_key_url=_OPENROUTER_KEY_URL,
        probe="openai_transcriptions",
    ),
    ProviderSpec(
        id="openrouter-tts",
        kind="tts",
        label="OpenRouter (TTS)",
        vendor="OpenRouter",
        package="livekit-plugins-openai",
        python_class="lkap_agent.providers.openrouter.OpenRouterTTS",
        credential_provider=OPENROUTER_CREDENTIAL_HOME,
        listed=False,
        unlisted_note=_OPENROUTER_SPEECH_UNLISTED,
        secret_fields=[_openrouter_key()],
        fields=[
            _openrouter_base_url(),
            FieldSpec(
                name="voice",
                label="Voice",
                type="catalog",
                catalog_kind="voices",
                default="Kore",
                help="Voices are per model. Pick the model first, then a voice it lists.",
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
        catalog=CatalogSpec(
            adapter="openrouter_tts_models", kinds=["models", "voices"], ttl_s=TTL_OPENROUTER_S
        ),
        test="openrouter_tts_models",
        notes="Voices are per model. Pick the model first, then a voice it lists. Non-streaming, like "
        "OpenAI TTS: one request per sentence, so the first audio of every reply waits for a whole "
        "sentence to be synthesised. PCM for every model except Voxtral (MP3 only), at the sample "
        "rate OpenRouter declares. For low latency prefer LiveKit Inference TTS or Cartesia.",
        docs_url="https://docs.livekit.io/agents/models/tts/openai/",
        get_key_url=_OPENROUTER_KEY_URL,
        probe="openai_speech",
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
        catalog=CatalogSpec(adapter="openrouter_embedding_models", kinds=["models"], ttl_s=TTL_OPENROUTER_S),
        test="openrouter_embedding_models",
        notes="Platform-level. Select it with LKAP_EMBEDDER=openrouter-embedding:<credential_id>. Only "
        "the 1536-dimension model is offered. Another model would mean re-embedding every knowledge "
        "base.",
        get_key_url=_OPENROUTER_KEY_URL,
        probe="openai_embeddings",
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
        catalog=CatalogSpec(adapter="openrouter_image_models", kinds=["models"], ttl_s=TTL_OPENROUTER_S),
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
        notes="Subclasses openai.realtime.RealtimeModel. Audio-native only, no modalities/text-only mode.",
        docs_url="https://docs.livekit.io/agents/models/realtime/",
        probe="xai_realtime_ws",
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
            FieldSpec(name="model", label="Model", type="model", default="universal-3-5-pro"),
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
                help="No api_key kwarg exists on this class. Google auth is ADC only.",
            ),
            FieldSpec(name="languages", label="Language", type="string", default="en-US"),
            FieldSpec(name="model", label="Model", type="model", default="latest_long"),
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
        fields=[
            FieldSpec(name="model", label="Model", type="model", default="gpt-4o-mini-transcribe"),
            # V6-02 (D-V6-4c): unset keeps the batch transcriptions endpoint stored agents use.
            FieldSpec(
                name="use_realtime",
                label="Streams while the caller speaks",
                type="boolean",
                recommended=True,
                help="Streams while the caller speaks. Needed for live calls. Off sends one request "
                "after each utterance.",
            ),
        ],
        catalog=_openai_catalog(_OPENAI_STT_FILTER),
        test="openai_models",
        docs_url="https://docs.livekit.io/agents/models/stt/openai/",
        probe="openai_transcriptions",
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
        probe="elevenlabs_stt",
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
        probe="cartesia_stt",
    ),
    ProviderSpec(
        id="deepgram-flux-stt",
        kind="stt",
        label="Deepgram Flux",
        vendor="Deepgram",
        package="livekit-plugins-deepgram",
        # livekit-plugins-deepgram 1.8.3 `stt_v2.py`: `STTv2` speaks /v2/listen and reports the end
        # of the caller's turn itself (`eot_threshold`, `eager_eot_threshold`, `eot_timeout_ms`).
        python_class="livekit.plugins.deepgram.STTv2",
        # The full image, like every V2-05 entry, so the v1 `mvp` set is untouched (R-V2-1); the
        # dev venv carries livekit-plugins-deepgram, so a dev worker reports it installed anyway.
        credential_provider="deepgram-stt",
        secret_fields=[_api_key("Deepgram API key", env="DEEPGRAM_API_KEY")],
        fields=_flux_option_fields(
            eot_placeholder="0.7",
            eager_placeholder="off",
            eager_help="Start preparing a reply before the turn is certainly over (0.3-0.9). Off by default.",
            timeout_placeholder="3000",
        ),
        models=[
            ModelSpec(id="flux-general-en", label="Flux (general, en)"),
            ModelSpec(id="flux-general-multi", label="Flux (multilingual)"),
        ],
        default_model="flux-general-en",
        # No `redaction`: STTv2's `redact` takes one string ("numbers"/"aggressive_numbers"),
        # not the list `stt_redact` becomes, and Flux has no pci/pii/phi masking.
        capabilities=ProviderCapabilities(end_of_turn=True),
        # Priced with deepgram-stt's rows, which carry the Flux rate (docs/v4/COSTS.md D-V4-39).
        price_ref="deepgram-stt",
        notes="Decides when the caller has finished speaking by itself, so the session uses it for "
        "turn-taking instead of the turn detector. flux-general-multi covers English, Spanish, French, "
        "German, Hindi, Russian, Portuguese, Japanese, Italian and Dutch.",
        docs_url="https://docs.livekit.io/agents/models/stt/deepgram/",
        get_key_url="https://console.deepgram.com/",
    ),
    _full(
        "groq-stt",
        "stt",
        "Groq Whisper",
        "Groq",
        "livekit-plugins-groq",
        "livekit.plugins.groq.STT",
        secret_fields=[_api_key("Groq API key", env="GROQ_API_KEY")],
        fields=[FieldSpec(name="model", label="Model", type="model", default="whisper-large-v3-turbo")],
        catalog=CatalogSpec(
            adapter="groq_models", kinds=["models"], filter=CatalogFilter(id_include="whisper")
        ),
        test="groq_models",
        docs_url="https://docs.livekit.io/agents/models/stt/groq/",
        probe="openai_transcriptions",
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
        catalog=CatalogSpec(adapter="anthropic_models", kinds=["models"], page=_ANTHROPIC_PAGE),
        test="anthropic_models",
        docs_url="https://docs.livekit.io/agents/models/llm/anthropic/",
        probe="anthropic_messages",
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
        catalog=CatalogSpec(
            adapter="groq_models", kinds=["models"], filter=CatalogFilter(id_exclude="whisper|tts|guard")
        ),
        test="groq_models",
        docs_url="https://docs.livekit.io/agents/models/llm/groq/",
        probe="openai_chat",
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
        catalog=CatalogSpec(adapter="cerebras_models", kinds=["models"]),
        test="cerebras_models",
        notes="v1's stub pointed at a nonexistent openai.LLM.with_cerebras. Corrected to the real package.",
        docs_url="https://docs.livekit.io/agents/models/llm/",
        probe="openai_chat",
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
        probe="openai_chat",
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
        notes="Flat api_key/api_secret only this plugin family (+ aws.TTS) accepts. Falls back to "
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
                help="No api_key kwarg exists on this class. Google auth is ADC only.",
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
        # Public list (R-V4-9): a catalog, never the credential test.
        catalog=CatalogSpec(adapter="deepgram_tts_models", kinds=["models"], ttl_s=TTL_PUBLIC_LIST_S),
        docs_url="https://docs.livekit.io/agents/models/tts/deepgram/",
        probe="deepgram_speak",
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
            # V6-02 (D-V6-4c): livekit-plugins-rime 1.8.3 streams only over its WebSocket.
            FieldSpec(
                name="use_websocket",
                label="Streams while it speaks",
                type="boolean",
                recommended=True,
                help="Starts speaking before the whole sentence is ready. Needed for live calls. Off "
                "sends one request per sentence.",
            ),
        ],
        models=[ModelSpec(id="mistv3", label="Mist v3")],
        default_model="mistv3",
        # Public JSON (R-V4-9): one item per (model, voice), never the credential test.
        catalog=CatalogSpec(adapter="rime_voices", kinds=["voices", "models"], ttl_s=TTL_PUBLIC_LIST_S),
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
        # V6-02 (D-V6-4a): Inworld deprecated TTS 1.5; the old id still resolves.
        models=[
            ModelSpec(id="inworld-tts-2", label="Inworld TTS 2"),
            ModelSpec(id="inworld-tts-2-flash", label="Inworld TTS 2 Flash"),
            ModelSpec(
                id="inworld-tts-1.5-max",
                label="Inworld TTS 1.5 Max",
                note=DEPRECATED_BY_VENDOR,
                deprecated=True,
            ),
        ],
        default_model="inworld-tts-2",
        # No `test`: whether the voice list rejects a bad key is UNVERIFIED (asks).
        catalog=CatalogSpec(
            adapter="inworld_voices",
            kinds=["voices"],
            page=PageSpec(
                kind="token", param="pageToken", next_path="nextPageToken", size_param="pageSize", size=1000
            ),
        ),
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
        catalog=CatalogSpec(
            adapter="hume_voices",
            kinds=["voices"],
            page=PageSpec(
                kind="page", param="page_number", next_path="total_pages", size_param="page_size", size=100
            ),
        ),
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
        capabilities=ProviderCapabilities(
            tool_calling=False,
            avatar_aspect="landscape",
            avatar_aspect_note=(
                "Output dimensions are optional and per-persona-model. Left unset, Anam's default "
                "is landscape (Cara 3: 720x480, Cara 4: 1152x768) per "
                "anam.ai/docs/personas/session/video. Overridable per session (Cara 4 also offers "
                "a 768x1152 portrait size). A persona configured for portrait output is not "
                "reflected by this registry entry."
            ),
        ),
        docs_url="https://docs.livekit.io/agents/integrations/avatar/anam/",
        get_key_url="https://anam.ai/",
        probe="anam_avatar_get",
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
        notes="No Windows wheel, only manylinux_2_28 or macOS arm64. Also has a genuine "
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


#: The per-minute price line of LiveKit Cloud's noise cancellation (research-v4
#: panels-and-capabilities C5, the LiveKit pricing page at planning time).
_CLOUD_NC_PRICE_NOTE = (
    "Billed by LiveKit Cloud. The first 1,000 minutes a month are included, then about $0.0012 a minute."
)


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
        fields=[
            FieldSpec(name="min_speech_duration", label="Min speech duration (s)", type="number"),
            # V6-34: livekit-plugins-silero 1.8.3 `VAD.load` (`vad.py:60-71`); defaults 0.55 / 0.5 / 0.5.
            *_vad_fields(silence_default="0.55"),
        ],
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
        # V6-34: livekit-agents 1.8.3 `inference/vad.py:59-70`; its defaults are 0.25 / 0.5 / 0.5.
        fields=_vad_fields(silence_default="0.25"),
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
                "(routes billing through the bound LiveKit Cloud project, no separate vendor key). "
                "It is not a "
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
        telephony_variant="livekit.plugins.krisp.voice_isolation_telephony",
        price_note=_CLOUD_NC_PRICE_NOTE,
        notes="Deferred (asks #56): livekit-plugins-krisp is versioned independently of the agents "
        "release and is not in the worker image. V5-07 read 0.4.2 (requires livekit-agents>=1.8.2): "
        "`viva_filter.py` defines `voice_isolation_telephony(*, auth_provider, noise_suppression_level)`, "
        "the telephony variant, which takes no `mode`. Ships a native livekit-plugins-krisp-internal "
        "wheel. Re-enable after an import check in the full image.",
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
                help="No default. D-ID has no stock avatar.",
            )
        ],
        capabilities=ProviderCapabilities(tool_calling=False),
        notes="agent_id has no NotGivenOr wrapper, unlike every other avatar's id field. It is a "
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
        capabilities=ProviderCapabilities(
            tool_calling=False,
            avatar_aspect="portrait",
            avatar_aspect_note=(
                "LemonSlice avatars render as 368x560 pixel videos (~9:16 portrait). The vendor "
                "center-crops the source image to that aspect if it doesn't already match "
                "(docs.livekit.io/agents/integrations/avatar/lemonslice/)."
            ),
        ),
        notes="Exactly one of agent_id/agent_image_url/agent_image is required. The ctor also "
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
                help='Up to 5 gallery avatar ids ({"avatar_ids": [...]}). The first is active, the rest '
                "swappable in-session via swap_avatar().",
            )
        ],
        capabilities=ProviderCapabilities(tool_calling=False),
        notes="avatar_config is positional, not keyword-only. It is the only avatar ctor shaped "
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
        "auth param on this constructor). It is resolved purely through the standard boto3/AWS "
        "credential chain, "
        "unlike the catalog draft's claim of a flat api_key/api_secret pair. modalities is audio|mixed only, "
        "with no half-cascade path.",
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
        "realtime model. Most behavior lives in Phonic's own dashboard against phonic_agent, "
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
        catalog=CatalogSpec(
            adapter="mistral_models",
            kinds=["models"],
            filter=CatalogFilter(id_exclude="embed|ocr|moderation"),
        ),
        test="mistral_models",
        docs_url="https://docs.livekit.io/agents/models/llm/",
        probe="openai_chat",
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
        catalog=CatalogSpec(adapter="xai_models", kinds=["models"]),
        test="xai_models",
        docs_url="https://docs.livekit.io/agents/models/llm/",
        probe="openai_chat",
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
        catalog=_openai_catalog(_OPENAI_LLM_FILTER),
        test="openai_models",
        price_ref="openai-llm",
        notes="Distinct class from openai.LLM (chat completions). Uses the Responses API over "
        "a websocket by default.",
        docs_url="https://docs.livekit.io/agents/models/llm/openai/",
        probe="openai_chat",
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
            FieldSpec(name="model", label="Model", type="model", default="solaria-1"),
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
        # V6-02 (D-V6-6): Fireworks' changelog of 2026-06-10 deprecated audio inference; the
        # plugin's streaming endpoint is dead, so the entry is withdrawn and kept for stored refs.
        availability="removed",
        secret_fields=[_api_key("Fireworks API key", env="FIREWORKS_API_KEY")],
        fields=[FieldSpec(name="language", label="Language", type="string")],
        notes="Fireworks stopped its speech service on 2026-06-10. Pick another transcriber.",
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
        fields=[FieldSpec(name="model", label="Model", type="model", default="whisper")],
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
        fields=[FieldSpec(name="model", label="Model", type="model", default="voxtral-mini-latest")],
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
                type="model",
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
            FieldSpec(name="model", label="Model", type="model", default="saaras:v4"),
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
        fields=[FieldSpec(name="model", label="Model", type="model", default="muse-voice-transcribe-1.0")],
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
        notes=(
            "A router/gateway product that routes to a configured upstream STT model, "
            "not a fixed vendor model."
        ),
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
        fields=[FieldSpec(name="model", label="Model", type="model", default="pulse")],
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
            FieldSpec(name="model", label="Model", type="model", default="openai/whisper-large-v3-turbo")
        ],
        notes="Self-deployed inference. The api_key authenticates the tenant's Simplismart deployment.",
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
                help="boto-style Credentials object. Omit to use the standard AWS credential chain "
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
        fields=[FieldSpec(name="model", label="Model", type="model", default="async_flash_v1.0")],
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
        fields=[FieldSpec(name="model", label="Model", type="model", default="orpheus")],
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
            FieldSpec(name="model", label="Model", type="model", default="s2.1-pro"),
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
            FieldSpec(name="model", label="Model", type="model", default="canopylabs/orpheus-v1-english"),
            FieldSpec(name="voice", label="Voice", type="string", default="autumn"),
        ],
        notes="Groq speech takes at most 200 characters per request, so a long sentence from the "
        "model can fail to speak. It also waits for the whole sentence before playing.",
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
            FieldSpec(name="model", label="Model", type="model", default="blizzard"),
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
        fields=[FieldSpec(name="model", label="Model", type="model", default="FALCON")],
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
        fields=[FieldSpec(name="model", label="Model", type="model", default="/public/tts/en-rt")],
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
            FieldSpec(name="model", label="Model", type="model", default="bulbul:v3"),
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
        fields=[FieldSpec(name="model", label="Model", type="model", default="canopylabs/orpheus-3b-0.1-ft")],
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
        fields=[FieldSpec(name="model", label="Model", type="model", default="lightning_v3.1_pro")],
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
            FieldSpec(name="model", label="Model", type="model", default="tts-rt-v1-preview"),
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
            FieldSpec(name="model", label="Model", type="model", default="simba-3.2"),
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
        "minimax-tts",
        "tts",
        "MiniMax",
        "MiniMax",
        # V6-02: the 1.8.x plugin is published as `livekit-plugins-minimax-ai` (the monorepo's
        # `livekit-plugins/livekit-plugins-minimax/pyproject.toml` at livekit-agents@1.8.3); PyPI's
        # 1.8.3 wheel requires `livekit-agents[codecs]>=1.8.3` and resolves with 1.8.3 (uv pip
        # compile, 2026-09-28). The stale `livekit-plugins-minimax` 1.3.0 pins 1.2.9 and is never used.
        "livekit-plugins-minimax-ai",
        "livekit.plugins.minimax.TTS",
        secret_fields=[_api_key("MiniMax API key", env="MINIMAX_API_KEY")],
        fields=[
            FieldSpec(
                name="audio_format",
                label="Audio format",
                type="enum",
                options=MINIMAX_AUDIO_FORMATS,
                recommended="pcm",
                placeholder="mp3",
                help="Uncompressed audio (pcm) starts playing sooner than MP3. Unset keeps MP3.",
            ),
        ],
        # V6-02 (D-V6-4a): the plugin's default `speech-02-turbo` is deprecated by MiniMax.
        models=[
            ModelSpec(id="speech-2.8-turbo", label="Speech 2.8 Turbo"),
            ModelSpec(id="speech-2.8-hd", label="Speech 2.8 HD"),
            ModelSpec(
                id="speech-02-turbo", label="Speech 02 Turbo", note=DEPRECATED_BY_VENDOR, deprecated=True
            ),
        ],
        default_model="speech-2.8-turbo",
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
    # ------------------------------------------------ tool providers (V5-18, COMPOSIO.md)
    # The workspace's Composio key (D-V5-C1): nothing to construct, so no package or
    # class. Tested by `lkap_api.credential_tests` (the session-info call), not by a
    # catalog adapter, so `test` and `catalog` stay unset.
    _full(
        "composio",
        "tool_provider",
        "Composio (connected apps)",
        "Composio",
        "",
        "",
        secret_fields=[
            _api_key(
                "Composio API key",
                help_text="From your Composio project's settings. Used to list apps and connect them.",
                env="COMPOSIO_API_KEY",
            )
        ],
        capabilities=ProviderCapabilities(tool_calling=False, audio_input=False),
        notes="Connected apps (Tools -> Apps). Free tier: 100,000 tool calls a month. Apps that use "
        "Composio's shared sign-in include 20,000 of those, then a small per-call fee.",
        docs_url="https://docs.composio.dev/docs/authenticating-tools",
        get_key_url="https://platform.composio.dev",
    ),
    # ------------------------------------------------ MCP server sign-in (V5-14)
    # The tokens of one MCP server's OAuth sign-in (research-v4 tools §4.3.2). Written only by
    # the api's sign-in callback, never typed by an admin: no secret fields, and a tool binds
    # one only when the bag names that tool and its url. Listed after `http-tool-secret`, which
    # the console finds as "the" secret bag by kind, and outside the v1 slim set. The card's
    # `kind: "oauth"` would add a `ProviderKind` member that exhaustive web maps key on, so it
    # is a `secret_bag` until a ruling adds that kind.
    _full(
        MCP_OAUTH_PROVIDER_ID,
        "secret_bag",
        "MCP server sign-in",
        "LKAP",
        "",
        "",
        requires_credential=False,
        secret_fields=[],
        capabilities=ProviderCapabilities(tool_calling=False, audio_input=False),
        notes="Created by signing in to an MCP server from its tool page. Not added by hand.",
    ),
    # ------------------------------------------------ built-in tool vendors (V5-25, D-V5-7)
    # Keys for the `web_search` and `send_sms` built-ins. The worker speaks each vendor's API
    # through its own adapter (`lkap_agent.tools.vendors`), so there is no package or class, and
    # no catalog adapter tests the key yet (`test` unset; docs/v5/_asks.md, V5-25).
    _full(
        "tavily-search",
        "web_search",
        "Tavily",
        "Tavily",
        "",
        "",
        secret_fields=[_api_key("Tavily API key", help_text="From app.tavily.com (starts with tvly-).")],
        fields=[
            FieldSpec(
                name="search_depth",
                label="Search depth",
                type="enum",
                options=["basic", "advanced"],
                default="basic",
                help="`advanced` finds better passages and costs two credits a search.",
            )
        ],
        capabilities=ProviderCapabilities(tool_calling=False, audio_input=False),
        notes="The suggested web search service, with a free tier that needs no card.",
        price_note="1,000 free searches a month, then about $0.008 a search (Tavily's pricing page).",
        docs_url="https://docs.tavily.com/documentation/api-reference/endpoint/search",
        get_key_url="https://app.tavily.com",
    ),
    _full(
        "brave-search",
        "web_search",
        "Brave Search",
        "Brave",
        "",
        "",
        secret_fields=[
            _api_key("Brave Search API key", help_text="The subscription token of a Brave Search API plan.")
        ],
        fields=[
            FieldSpec(
                name="country",
                label="Country",
                type="string",
                placeholder="us",
                help="Two-letter country code the results favour. Empty lets Brave decide.",
            )
        ],
        capabilities=ProviderCapabilities(tool_calling=False, audio_input=False),
        notes="A fast web search index of its own.",
        price_note="About $5 per 1,000 searches, with $5 of free credit a month (Brave's pricing page).",
        docs_url="https://api-dashboard.search.brave.com/app/documentation/web-search/get-started",
        get_key_url="https://api-dashboard.search.brave.com",
    ),
    _full(
        "twilio-sms",
        "sms",
        "Twilio",
        "Twilio",
        "",
        "",
        secret_fields=[
            FieldSpec(
                name="account_sid",
                label="Account SID",
                type="secret",
                required=True,
                help="From the Twilio console (starts with AC).",
            ),
            FieldSpec(name="auth_token", label="Auth token", type="secret", required=True),
        ],
        fields=[
            FieldSpec(
                name="from_number",
                label="Sending number",
                type="string",
                required=True,
                placeholder="+15550100000",
                help="A Twilio number that can send text messages, in international format.",
            )
        ],
        capabilities=ProviderCapabilities(tool_calling=False, audio_input=False),
        notes="Texts a caller during or after a call (the `send_sms` tool).",
        price_note="Charged per message by Twilio, by destination country.",
        docs_url="https://www.twilio.com/docs/messaging/api/message-resource",
        get_key_url="https://console.twilio.com",
    ),
    _full(
        "telnyx-sms",
        "sms",
        "Telnyx",
        "Telnyx",
        "",
        "",
        secret_fields=[_api_key("Telnyx API key", help_text="From the Telnyx portal (starts with KEY).")],
        fields=[
            FieldSpec(
                name="from_number",
                label="Sending number",
                type="string",
                required=True,
                placeholder="+15550100000",
                help="A Telnyx number with a messaging profile, in international format.",
            ),
            FieldSpec(
                name="messaging_profile_id",
                label="Messaging profile id",
                type="string",
                help="Only when the number belongs to several profiles.",
            ),
        ],
        capabilities=ProviderCapabilities(tool_calling=False, audio_input=False),
        notes="Texts a caller during or after a call (the `send_sms` tool).",
        price_note="Charged per message by Telnyx, by destination country.",
        docs_url="https://developers.telnyx.com/api/messaging/send-message",
        get_key_url="https://portal.telnyx.com",
    ),
    # ------------------------------------------------ knowledge connections (V5-20, D-V5-16/19)
    # The keys and non-secret fields of `/v1/knowledge-connections`: `fields` is the per-kind
    # settings schema the console renders (the api validates the same names). The api speaks each
    # vendor's REST API itself, so there is no package or class; the key is checked by the
    # connection's `Test connection` (it needs the url or index the fields name). Every entry
    # keeps `requires_credential` so the add-key dialog offers it; a local Qdrant or Weaviate
    # connection may still have no key (the api decides, not the registry).
    _full(
        "qdrant",
        "knowledge",
        "Qdrant",
        "Qdrant",
        "",
        "",
        secret_fields=[
            _api_key(
                "Qdrant API key", help_text="A database API key of the cluster. None for a local cluster."
            )
        ],
        fields=[
            FieldSpec(
                name="url",
                label="Cluster address",
                type="string",
                required=True,
                placeholder="https://your-cluster.cloud.qdrant.io:6333",
                help="The cluster's REST address (https, or http://localhost:6333 for a local cluster).",
            ),
            FieldSpec(
                name="collection",
                label="Collection",
                type="string",
                default="lkap_knowledge",
                help="Created on first use. Every knowledge base of this connection shares it.",
            ),
            FieldSpec(
                name="native_hybrid",
                label="Keyword search in Qdrant",
                type="boolean",
                default=False,
                help="Let Qdrant combine keyword and meaning matches itself (applies to text indexed after "
                "it is on). Off: the platform combines them.",
            ),
        ],
        capabilities=ProviderCapabilities(tool_calling=False, audio_input=False),
        notes="Keeps a knowledge base's vectors in your own Qdrant cluster. The text stays on the platform.",
        price_note="Qdrant Cloud has a free cluster (1 GB memory, 4 GB disk). Larger clusters are billed "
        "by Qdrant.",
        docs_url="https://qdrant.tech/documentation/concepts/collections/",
        get_key_url="https://cloud.qdrant.io",
    ),
    _full(
        "pinecone",
        "knowledge",
        "Pinecone",
        "Pinecone",
        "",
        "",
        secret_fields=[_api_key("Pinecone API key", help_text="From the Pinecone console's API keys page.")],
        fields=[
            FieldSpec(
                name="index",
                label="Index",
                type="string",
                required=True,
                placeholder="lkap-knowledge",
                help="Lower-case letters, digits and hyphens. Created on first use (cosine, serverless) if "
                "it does not exist. Each knowledge base is its own namespace in it.",
            ),
            FieldSpec(
                name="cloud",
                label="Cloud",
                type="enum",
                options=["aws", "gcp", "azure"],
                default="aws",
                help="Where a new index is created.",
            ),
            FieldSpec(
                name="region",
                label="Region",
                type="string",
                default="us-east-1",
                help="Where a new index is created (the free plan offers aws us-east-1 only).",
            ),
        ],
        capabilities=ProviderCapabilities(tool_calling=False, audio_input=False),
        notes="Keeps a knowledge base's vectors in your own Pinecone index. The text stays on the platform.",
        price_note="The free Starter plan has 5 indexes, 100 namespaces per index and 2 GB. Paid plans bill "
        "storage, reads and writes.",
        docs_url="https://docs.pinecone.io/guides/index-data/indexing-overview",
        get_key_url="https://app.pinecone.io",
    ),
    _full(
        "weaviate",
        "knowledge",
        "Weaviate",
        "Weaviate",
        "",
        "",
        secret_fields=[
            _api_key("Weaviate API key", help_text="The cluster's API key. Leave out for a local cluster.")
        ],
        fields=[
            FieldSpec(
                name="url",
                label="Cluster address",
                type="string",
                required=True,
                placeholder="https://your-cluster.weaviate.cloud",
                help="The cluster's REST address (https, or http://localhost:8080 for a local cluster).",
            ),
            FieldSpec(
                name="collection",
                label="Collection",
                type="string",
                default="LkapKnowledge",
                help="Starts with a capital letter. Created on first use with one tenant per knowledge base.",
            ),
            FieldSpec(
                name="native_hybrid",
                label="Keyword search in Weaviate",
                type="boolean",
                default=False,
                help="Let Weaviate combine keyword and meaning matches itself (stores the text of chunks "
                "indexed after it is on). Off: the platform combines them.",
            ),
        ],
        capabilities=ProviderCapabilities(tool_calling=False, audio_input=False),
        notes="Keeps a knowledge base's vectors in your own Weaviate cluster. The text stays on the "
        "platform.",
        price_note="Weaviate Cloud has a free sandbox. Serverless clusters are billed by Weaviate.",
        docs_url="https://docs.weaviate.io/weaviate/manage-collections/multi-tenancy",
        get_key_url="https://console.weaviate.cloud",
    ),
    _full(
        "cohere-rerank",
        "knowledge",
        "Cohere Rerank",
        "Cohere",
        "",
        "",
        secret_fields=[_api_key("Cohere API key", help_text="From the Cohere dashboard's API keys page.")],
        fields=[
            FieldSpec(
                name="model",
                label="Model",
                type="model",
                default="rerank-v4.0-fast",
                help="`rerank-v4.0-fast` answers fastest, `rerank-v4.0-pro` ranks best and `rerank-v3.5` "
                "is the previous generation.",
            )
        ],
        capabilities=ProviderCapabilities(tool_calling=False, audio_input=False),
        notes="Re-orders the search tool's results with Cohere. Automatic knowledge never uses it.",
        price_note="Billed per search (one question with up to 100 passages). See Cohere's pricing page.",
        docs_url="https://docs.cohere.com/reference/rerank",
        get_key_url="https://dashboard.cohere.com/api-keys",
    ),
    _full(
        "voyage-rerank",
        "knowledge",
        "Voyage AI Rerank",
        "Voyage AI",
        "",
        "",
        secret_fields=[_api_key("Voyage AI API key", help_text="From the Voyage AI dashboard.")],
        fields=[
            FieldSpec(
                name="model",
                label="Model",
                type="model",
                default="rerank-2.5-lite",
                help="`rerank-2.5-lite` answers fastest, `rerank-2.5` ranks best, and `rerank-3-lite` and "
                "`rerank-3` are previews.",
            )
        ],
        capabilities=ProviderCapabilities(tool_calling=False, audio_input=False),
        notes="Re-orders the search tool's results with Voyage AI. Automatic knowledge never uses it.",
        price_note="Billed per token: $0.02 per million (lite models), $0.05 (the others). The preview "
        "rerank-3 models include 200 million free tokens (Voyage AI's pricing page).",
        docs_url="https://docs.voyageai.com/docs/reranker",
        get_key_url="https://dashboard.voyageai.com",
    ),
    # V5-45: the first managed search service (K §7). Ragie ingests and ranks its own documents;
    # a knowledge base of kind `external` names one of its partitions and is searched there. The
    # key is checked by the connection's `Test connection`, which lists the partitions.
    _full(
        "ragie",
        "knowledge",
        "Ragie",
        "Ragie",
        "",
        "",
        secret_fields=[_api_key("Ragie API key", help_text="From the Ragie dashboard's API keys page.")],
        fields=[
            FieldSpec(
                name="rerank",
                label="Re-rank in Ragie",
                type="boolean",
                default=False,
                help="Ragie keeps only the passages it judges relevant. More accurate, but slower on "
                "every search. Off answers fastest.",
            ),
            FieldSpec(
                name="recency_bias",
                label="Prefer recent documents",
                type="boolean",
                default=False,
                help="Ragie ranks newer documents higher.",
            ),
        ],
        capabilities=ProviderCapabilities(tool_calling=False, audio_input=False),
        notes="Searches documents you keep in Ragie (uploaded there or synced from your apps). The "
        "platform stores none of them. Each knowledge base reads one Ragie partition.",
        price_note="Ragie's free Developer plan includes a monthly allowance of pages and searches. Every "
        "search an agent makes counts against it. Paid plans are billed by Ragie.",
        docs_url="https://docs.ragie.ai/docs/retrievals-guide",
        get_key_url="https://secure.ragie.ai",
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
        "catalog's finding). Last released 2025-10-15 against a much older core. Compatibility with "
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
        "either. It is the least mature realtime integration of the set.",
    ),
    _full(
        "legacy-noise-cancellation",
        "noise_cancellation",
        "Noise Cancellation (LiveKit Cloud)",
        "LiveKit",
        "livekit-plugins-noise-cancellation",
        "livekit.plugins.noise_cancellation.BVC",
        availability="deferred",
        requires_credential=False,
        capabilities=ProviderCapabilities(cloud_only=True),
        telephony_variant="livekit.plugins.noise_cancellation.BVCTelephony",
        price_note=_CLOUD_NC_PRICE_NOTE,
        notes="Out-of-tree, LiveKit Cloud only. V5-07 read 0.3.2 (requires livekit>=0.21.3): "
        "`plugin.py` defines `NC()`, `BVC()` and `BVCTelephony()`, each returning an "
        "`rtc.NoiseCancellationOptions` that `AudioInputOptions.noise_cancellation` accepts "
        "(livekit-agents 1.8.3 `voice/room_io/types.py:253`). Deferred until the package is in the worker "
        "image (docs/v5/_asks.md).",
        docs_url="https://docs.livekit.io/agents/build/audio/#noise-cancellation",
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
        notes="Out-of-tree (not in the 1.8.2 monorepo clone). Only requires_dist (livekit-agents>=1.4.2) is "
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
            FieldSpec(name="model", label="Model", type="model", default="sommers_ko"),
            FieldSpec(name="language", label="Language", type="string", default="ko"),
        ],
        notes="__init__ has no api_key/credential kwarg at all (confirmed by AST snapshot). Auth "
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
        notes="Same gap as rtzr-stt: __init__ takes no credential kwarg (confirmed by AST snapshot). The "
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


# ------------------------------------------------------------------ languages (V5-31)
#: A language code as the platform stores it: a base code (ISO 639-1, or 639-3 when there is
#: none) and optional BCP-47 subtags (``en``, ``hi``, ``en-IN``, ``pt-BR``, ``kok-IN``).
LANGUAGE_CODE_PATTERN = r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$"

#: Base code → English name, for prompts and validator messages. Not an allowlist: a code
#: missing here is shown as itself.
LANGUAGE_NAMES: dict[str, str] = {
    "af": "Afrikaans", "ar": "Arabic", "as": "Assamese", "az": "Azerbaijani", "be": "Belarusian",
    "bg": "Bulgarian", "bn": "Bengali", "bs": "Bosnian", "ca": "Catalan", "cs": "Czech",
    "cy": "Welsh", "da": "Danish", "de": "German", "el": "Greek", "en": "English",
    "es": "Spanish", "et": "Estonian", "fa": "Persian", "fi": "Finnish", "fr": "French",
    "gl": "Galician", "gu": "Gujarati", "he": "Hebrew", "hi": "Hindi", "hr": "Croatian",
    "hu": "Hungarian", "hy": "Armenian", "id": "Indonesian", "is": "Icelandic", "it": "Italian",
    "ja": "Japanese", "kk": "Kazakh", "kn": "Kannada", "ko": "Korean", "lt": "Lithuanian",
    "lv": "Latvian", "mi": "Maori", "mk": "Macedonian", "ml": "Malayalam", "mr": "Marathi",
    "ms": "Malay", "ne": "Nepali", "nl": "Dutch", "no": "Norwegian", "od": "Odia",
    "or": "Odia", "pa": "Punjabi", "pl": "Polish", "pt": "Portuguese", "ro": "Romanian",
    "ru": "Russian", "sk": "Slovak", "sl": "Slovenian", "sr": "Serbian", "sv": "Swedish",
    "sw": "Swahili", "ta": "Tamil", "te": "Telugu", "th": "Thai", "tl": "Tagalog",
    "tr": "Turkish", "uk": "Ukrainian", "ur": "Urdu", "vi": "Vietnamese", "zh": "Chinese",
}  # fmt: skip


def base_language(code: str) -> str:
    """The base code of a language tag: ``hi-IN`` → ``hi``, ``pt_BR`` → ``pt``, ``EN`` → ``en``."""
    return code.strip().replace("_", "-").split("-", 1)[0].lower()


def language_name(code: str) -> str:
    """A language's English name (``hi-IN`` → ``Hindi``), else the code itself."""
    return LANGUAGE_NAMES.get(base_language(code), code.strip())


def declares_language(declared: list[str], code: str) -> bool | None:
    """Whether a capability list covers ``code``, compared on base codes.

    Returns:
        ``None`` when ``declared`` is empty (the registry does not know), else whether
        the base code of ``code`` is in it.
    """
    if not declared:
        return None
    return base_language(code) in {base_language(item) for item in declared}


#: Deepgram Nova-3 monolingual languages (base codes), from Deepgram's models-and-languages
#: overview and LiveKit Inference's Deepgram page (accessed 2026-09-27).
_NOVA3_LANGUAGES: list[str] = (
    "ar be bn bs bg ca hr cs da nl en et fi fr de el he hi hu id it ja kn ko lv lt mk ms mr no fa "
    "pl pt ro ru sr sk sl es sv tl ta te tr uk ur vi zh"
).split()
#: What Nova-3 ``language=multi`` (code-switching) covers: English, Spanish, French, German,
#: Hindi, Russian, Portuguese, Japanese, Italian and Dutch (the same sources).
_DEEPGRAM_MULTI_LANGUAGES: list[str] = "en es fr de hi ru pt ja it nl".split()
#: The OpenAI transcription models' supported languages (the Whisper list of 57, which the
#: OpenAI speech-to-text guide refers to); OpenRouter's copy of the endpoint takes the same.
_WHISPER_LANGUAGES: list[str] = (
    "af ar hy az be bs bg ca zh hr cs da nl en et fi fr gl de el he hi hu is id it ja kn kk ko lv "
    "lt mk ms mr mi ne no fa pl pt ro ru sr sk sl es sw sv tl ta th tr uk ur vi cy"
).split()
#: Sarvam speech-to-text (``SpeechToTextLanguage`` in livekit-plugins-sarvam 1.8.3; ``unknown``
#: asks it to detect), base codes.
_SARVAM_STT_LANGUAGES: list[str] = (
    "hi bn kn ml mr od pa ta te en gu as ur ne kok ks sd sa sat mni brx mai doi"
).split()
#: Sarvam text-to-speech (``SarvamTTSLanguages`` in the same plugin), base codes.
_SARVAM_TTS_LANGUAGES: list[str] = "bn en gu hi kn ml mr od pa ta te".split()

#: STT entries whose ``update_options`` takes ``language=`` on the ``STT`` object in
#: livekit-agents 1.8.3 and reaches the running transcriber (open streams, or the next request
#: of a batch transcriber). Checked in each plugin's source: Google and Gladia take
#: ``languages=``, AssemblyAI ``language_codes=``, Sarvam only on its stream and with a
#: ``model``, Palabra on new streams only; ElevenLabs, Speechmatics, Soniox, AWS, Gnani, NVIDIA,
#: Telnyx, Meta, Gradium and Simplismart have no language update.
_LANGUAGE_SWITCH_STT: frozenset[str] = frozenset(
    {
        "livekit-inference-stt",
        "deepgram-stt",
        "openai-stt",
        "openrouter-stt",
        "azure-stt",
        "cartesia-stt",
        "clova-stt",
        "fal-wizper-stt",
        "fireworksai-stt",
        "mistral-stt",
        "slng-stt",
        "smallestai-stt",
        "xai-stt",
        "baseten-stt",
    }
)

#: Per-entry language capabilities (V5-31). Only what was checked against a vendor page or the
#: plugin source is recorded; every other entry keeps empty lists (unknown). The LiveKit
#: Inference and Deepgram rows describe their default model, Nova-3.
_LANGUAGE_CAPABILITIES: dict[str, dict[str, Any]] = {
    "livekit-inference-stt": {
        "languages": _NOVA3_LANGUAGES,
        "language_detection": "multi",
        "detect_languages": _DEEPGRAM_MULTI_LANGUAGES,
    },
    "deepgram-stt": {
        "languages": _NOVA3_LANGUAGES,
        "language_detection": "multi",
        "detect_languages": _DEEPGRAM_MULTI_LANGUAGES,
    },
    "openai-stt": {"languages": _WHISPER_LANGUAGES, "language_detection": "multi"},
    "openrouter-stt": {"languages": _WHISPER_LANGUAGES, "language_detection": "multi"},
    "sarvam-stt": {"languages": _SARVAM_STT_LANGUAGES, "language_detection": "unknown"},
    "sarvam-tts": {"languages": _SARVAM_TTS_LANGUAGES},
}


def _with_language_capabilities(spec: ProviderSpec) -> ProviderSpec:
    """Return ``spec`` with its recorded language capabilities (V5-31)."""
    update: dict[str, Any] = {
        key: list(value) if isinstance(value, list) else value
        for key, value in _LANGUAGE_CAPABILITIES.get(spec.id, {}).items()
    }
    if spec.id in _LANGUAGE_SWITCH_STT:
        update["language_switch"] = True
    if not update:
        return spec
    capabilities = spec.capabilities.model_copy(update=update)
    return spec.model_copy(update={"capabilities": capabilities})


# ------------------------------------------------------------------ streaming (V6-02, D-V6-2)
#: Whether each STT/TTS entry streams with its registry defaults: ``(streaming, streaming_field,
#: streaming_note)``. Read from each plugin's ``STTCapabilities``/``TTSCapabilities(streaming=...)``
#: at the livekit-agents@1.8.3 tag (``livekit-plugins/<plugin>/livekit/plugins/<name>/{stt,tts}.py``;
#: ``livekit-agents/livekit/agents/inference/{stt,tts}.py`` for LiveKit Inference) and, for
#: ``openrouter-tts``, from ``lkap_agent.providers.openrouter.OpenRouterTTS``. Grows by reading the
#: source, never by guess; a parity test keeps every available entry recorded.
_STREAMING: dict[str, tuple[bool | None, str | None, str | None]] = {
    # ---- speech-to-text
    "livekit-inference-stt": (True, None, None),
    "deepgram-stt": (True, None, None),
    "deepgram-flux-stt": (True, None, None),
    "openrouter-stt": (False, None, "OpenRouter transcribes each utterance as one whole file."),
    "openai-stt": (
        False,
        "use_realtime",
        "The realtime-only models (gpt-realtime-whisper, gpt-live-transcribe) stream without it.",
    ),
    "assemblyai-stt": (True, None, None),
    "google-stt": (True, None, None),
    "speechmatics-stt": (True, None, None),
    "elevenlabs-stt": (False, None, "Streams only with the scribe_v2_realtime model."),
    "cartesia-stt": (True, None, None),
    "groq-stt": (False, None, None),
    "azure-stt": (True, None, None),
    "gladia-stt": (True, None, None),
    "soniox-stt": (True, None, None),
    "fal-wizper-stt": (False, None, None),
    "fireworksai-stt": (True, None, "Withdrawn: the vendor's speech service is gone."),
    "baseten-stt": (True, None, None),
    "mistral-stt": (False, None, "Streams only with a realtime model (an id containing 'realtime')."),
    "nvidia-stt": (True, None, None),
    "sarvam-stt": (True, None, None),
    "meta-stt": (True, None, None),
    "gnani-stt": (True, None, None),
    "gradium-stt": (True, None, None),
    "clova-stt": (False, None, None),
    "slng-stt": (True, None, None),
    "smallestai-stt": (True, None, "Streams only with the pulse model (the default)."),
    "simplismart-stt": (False, None, "The plugin's streaming mode is off by default and not offered here."),
    "telnyx-stt": (True, None, None),
    "palabra-stt": (True, None, None),
    "aws-transcribe-stt": (True, None, None),
    "xai-stt": (True, None, None),
    # ---- text-to-speech
    "livekit-inference-tts": (True, None, None),
    "cartesia-tts": (True, None, None),
    "elevenlabs-tts": (True, None, None),
    "openai-tts": (False, None, None),
    "openrouter-tts": (False, None, "One request per sentence."),
    "google-tts": (True, None, "Streams with Chirp 3 HD voices. Pick one of those for live calls."),
    "deepgram-tts": (True, None, None),
    "rime-tts": (False, "use_websocket", None),
    "inworld-tts": (True, None, None),
    "hume-tts": (False, None, None),
    "azure-tts": (False, None, "Azure itself can stream, but the LiveKit plugin does not."),
    "asyncai-tts": (True, None, None),
    "aws-polly-tts": (False, None, None),
    "baseten-tts": (None, None, "Streams only when the deployment's endpoint is a WebSocket URL."),
    "bland-tts": (True, None, None),
    "cambai-tts": (False, None, None),
    "fishaudio-tts": (True, None, None),
    "gnani-tts": (True, None, None),
    "gradium-tts": (True, None, None),
    "groq-tts": (False, None, None),
    "lmnt-tts": (False, None, None),
    "minimax-tts": (True, None, None),
    "murf-tts": (True, None, None),
    "neuphonic-tts": (True, None, None),
    "nvidia-tts": (True, None, None),
    "palabra-tts": (True, None, None),
    "resemble-tts": (True, None, None),
    "respeecher-tts": (True, None, None),
    "sarvam-tts": (True, None, None),
    "simplismart-tts": (False, None, None),
    "slng-tts": (True, None, None),
    "smallestai-tts": (True, None, None),
    "soniox-tts": (True, None, None),
    "speechify-tts": (True, None, None),
    "speechmatics-tts": (False, None, None),
    "telnyx-tts": (True, None, None),
    "upliftai-tts": (True, None, None),
    "vakyam-tts": (True, None, None),
    "xai-tts": (True, None, None),
}


def _with_streaming(spec: ProviderSpec) -> ProviderSpec:
    """Return ``spec`` with its recorded streaming capability (V6-02)."""
    recorded = _STREAMING.get(spec.id)
    if recorded is None or spec.kind not in ("stt", "tts"):
        return spec
    streaming, field, note = recorded
    capabilities = spec.capabilities.model_copy(
        update={"streaming": streaming, "streaming_field": field, "streaming_note": note}
    )
    return spec.model_copy(update={"capabilities": capabilities})


def speech_streams(spec: ProviderSpec, fields: dict[str, Any] | None = None) -> bool | None:
    """Whether a speech reference streams, given the fields it stores (V6-02, D-V6-2).

    Args:
        spec: An ``stt`` or ``tts`` registry entry.
        fields: The reference's stored ``fields`` (``ProviderRef.fields``).

    Returns:
        ``True`` when the entry streams by default or the reference turns its
        ``streaming_field`` on, ``False`` when it does not, ``None`` when the
        registry has not recorded it (or the entry is not speech).
    """
    if spec.kind not in ("stt", "tts"):
        return None
    capabilities = spec.capabilities
    field = capabilities.streaming_field
    if field is not None and (fields or {}).get(field) is True:
        return True
    return capabilities.streaming


#: V6-32: one vendor account key per family, stored under the family's **home** (R-V4-7's
#: mechanism, widened from OpenRouter and Deepgram Flux). ``{member: home}``; every member takes
#: the same secret fields as its home (``test_registry.py`` pins it). The home is the vendor's
#: language-model entry when it has one, else the entry most keys were already added from (the
#: first-shipped Deepgram STT, Cartesia and ElevenLabs TTS), else its speech-to-text entry.
#: Verified against the installed plugin source (``os.environ`` key lookup and the ``api_key``
#: kwarg): Deepgram, Cartesia, ElevenLabs, OpenAI, Google (Gemini API key only: ``google-stt`` and
#: ``google-tts`` take service-account credentials). The rest share the same ``env_fallback``
#: on every member (V2-05's check against the 1.8.2 source) and the same constructor kwarg
#: (``agent/tests/fixtures/plugin_signatures.json``). Palabra, Simplismart, Soniox, Telnyx, NVIDIA
#: and Azure speech record no environment variable on at least one member and are left separate
#: until their plugins can be read (docs/v6/_asks.md #313).
_FAMILY_HOMES: dict[str, str] = {
    # Deepgram: Nova (home), Flux (V6-02) and Aura TTS.
    "deepgram-tts": "deepgram-stt",
    # Cartesia, ElevenLabs.
    "cartesia-stt": "cartesia-tts",
    "elevenlabs-stt": "elevenlabs-tts",
    # OpenAI: one platform key for every OpenAI entry (Azure OpenAI is another vendor).
    "openai-realtime": "openai-llm",
    "openai-gptlive-realtime": "openai-llm",
    "openai-responses-llm": "openai-llm",
    "openai-stt": "openai-llm",
    "openai-tts": "openai-llm",
    "openai-embedding": "openai-llm",
    "openai-image-gen": "openai-llm",
    # Google: the Gemini API key.
    "google-realtime": "google-llm",
    "google-image-gen": "google-llm",
    # Same env_fallback on every member.
    "groq-stt": "groq-llm",
    "groq-tts": "groq-llm",
    "baseten-stt": "baseten-llm",
    "baseten-tts": "baseten-llm",
    "xai-realtime": "xai-llm",
    "xai-stt": "xai-llm",
    "xai-tts": "xai-llm",
    "mistral-stt": "mistral-llm",
    "aws-polly-tts": "aws-bedrock-llm",
    "gnani-tts": "gnani-stt",
    "gradium-tts": "gradium-stt",
    "sarvam-tts": "sarvam-stt",
    "slng-tts": "slng-stt",
    "smallestai-tts": "smallestai-stt",
    "speechmatics-tts": "speechmatics-stt",
}


def _with_family_home(spec: ProviderSpec) -> ProviderSpec:
    """Return ``spec`` with its V6-32 family home, unless it already names one."""
    home = _FAMILY_HOMES.get(spec.id)
    if home is None or spec.credential_provider is not None:
        return spec
    return spec.model_copy(update={"credential_provider": home})


REGISTRY: list[ProviderSpec] = [
    _with_family_home(_with_streaming(_with_language_capabilities(_live_verified(spec))))
    for spec in [*_MVP, *_OPENROUTER, *_FULL, *_NEW, *_DEFERRED]
]

_BY_ID: dict[str, ProviderSpec] = {spec.id: spec for spec in REGISTRY}


def _families() -> dict[str, frozenset[str]]:
    members: dict[str, set[str]] = {}
    for spec in REGISTRY:
        home = spec.credential_provider or spec.id
        members.setdefault(home, {home}).add(spec.id)
    return {home: frozenset(ids) for home, ids in members.items()}


_FAMILIES: dict[str, frozenset[str]] = _families()


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


def credential_family(spec_or_id: ProviderSpec | str) -> frozenset[str]:
    """Every registry id whose key is stored under the same home as this one (V6-32).

    A credential row keeps the ``provider_id`` it was stored with, and a home
    can move when a vendor's entries start sharing one key (``deepgram-tts``
    joined ``deepgram-stt``'s family in V6-32), so a lookup matches the whole
    family rather than the home alone: a row stored under any member serves
    every member. An id the registry does not know is its own one-member
    family.

    Args:
        spec_or_id: A :class:`ProviderSpec` or a registry id.

    Returns:
        The home and every entry that names it, as a frozen set.
    """
    home = credential_home(spec_or_id)
    return _FAMILIES.get(home, frozenset({home}))


def shares_credential(provider_id: str, other_id: str) -> bool:
    """Whether a key stored under ``provider_id`` serves ``other_id`` (same family, V6-32)."""
    return credential_home(provider_id) == credential_home(other_id)


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


def _model_spec(spec: ProviderSpec, model: str | None) -> ModelSpec | None:
    """The suggested model ``model`` names (``None`` = the entry's default), ignoring a ``:lang`` suffix."""
    model_id = model or spec.default_model
    if not model_id:
        return None
    for candidate in spec.models:
        if candidate.id == model_id or candidate.id == model_id.split(":", 1)[0]:
            return candidate
    return None


def stt_end_of_turn(provider_id: str, model: str | None) -> Literal["entry", "model"] | None:
    """Whether a transcriber can decide when the caller's turn ends (V6-02, V6-34).

    Args:
        provider_id: An ``stt`` registry id.
        model: The reference's model id; ``None`` = the entry's ``default_model``.

    Returns:
        ``"entry"`` when the whole entry always does (``capabilities.end_of_turn``: Deepgram
        Flux direct, as since V6-02); ``"model"`` when this model can once the agent opts in
        (``ModelSpec.end_of_turn``: LiveKit Inference Flux); ``None`` when it cannot or the
        entry is unknown.
    """
    spec = _BY_ID.get(provider_id)
    if spec is None or spec.kind != "stt":
        return None
    if spec.capabilities.end_of_turn:
        return "entry"
    listed = _model_spec(spec, model)
    return "model" if listed is not None and listed.end_of_turn else None
