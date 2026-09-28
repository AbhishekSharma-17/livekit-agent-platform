"""Agent configuration: what an admin saves, and what the worker receives resolved."""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator

from lkap_contracts.agent_tests import MAX_AGENT_TESTS, AgentTest, PublishGate
from lkap_contracts.common import Issue, ProviderRef, SessionChannel
from lkap_contracts.compliance import MAX_CONSENT_TEXT_CHARS, DisclosurePosition, ResolvedCompliance
from lkap_contracts.connections import ConnectionInfo
from lkap_contracts.flow import FlowSpec, QaNode
from lkap_contracts.guardrails import GuardrailsConfig
from lkap_contracts.providers import LANGUAGE_CODE_PATTERN, ModelCapabilities, base_language
from lkap_contracts.qa import MAX_QA_FIELDS, QaField
from lkap_contracts.telephony import TelephonyConfig, WarmTransferRoute
from lkap_contracts.tool_providers import AppsMode
from lkap_contracts.tools import ToolDefinition, ToolExecution, ToolExecutionMode
from lkap_contracts.turn_handling import (
    AMBIENT_SOUND_PATTERN,
    ConversationPreset,
    TurnDetectorSettings,
    TurnHandlingOptions,
)
from lkap_contracts.ui_protocol import BlockSpec

PipelineMode = Literal["realtime", "cascaded", "half_cascade"]

#: ``VoiceConfig.thinking_sound``: the three ``BuiltinAudioClip``s of livekit-agents 1.8.2 that
#: suit a wait (``KEYBOARD_TYPING``, ``KEYBOARD_TYPING2``, ``OFFICE_AMBIENCE``), or none.
ThinkingSound = Literal["none", "keyboard_typing", "keyboard_typing2", "office_ambience"]

#: Slots of :attr:`ResolvedAgentConfig.resolved`.
ProviderSlot = Literal[
    "realtime",
    "stt",
    "llm",
    "tts",
    "avatar",
    "image_gen",
    "workflow_llm",
    "qa_llm",
    "vad",
    "turn_detection",
    "noise_cancellation",
]
#: qa_llm (R-V2-6): resolved by the api from qa.model -> workflow_llm -> llm -> Inference LLM
#: default, present in ``resolved`` only when ``AgentConfig.qa.enabled``. The worker builds
#: its judge from this slot and never walks the chain itself.

#: Keys of :attr:`ResolvedAgentConfig.builtin_providers` (V5-25): the vendor a built-in tool
#: calls, resolved with its key. Kept out of ``resolved``, whose every slot the worker's
#: provider factory constructs.
#: V5-39 adds ``guardrails_llm`` (``guardrails.model``, the classifier's model) and
#: ``guardrails_moderation`` (the OpenAI key a moderation rule uses).
BuiltinProviderSlot = Literal["web_search", "sms", "notify_team", "guardrails_llm", "guardrails_moderation"]

__all__ = [
    "KNOWLEDGE_RERANK_VALUES",
    "MAX_AGENT_LANGUAGES",
    "MAX_MEMORY_CONSENT_CHARS",
    "MAX_MEMORY_RETENTION_DAYS",
    "NOTEBOOK_PRESET",
    "NOTEBOOK_PRESET_ID",
    "PANEL_PRESETS",
    "AgentConfig",
    "AgentLimits",
    "AppsMode",
    "AvatarOptions",
    "BuiltinProviderSlot",
    "CallerTimezoneMode",
    "CapabilitiesConfig",
    "ConnectionInfo",
    "ConversationPreset",
    "DisclosureConfig",
    "DisclosurePosition",
    "GuardrailsConfig",
    "KnowledgeConfig",
    "KnowledgeQueryMode",
    "KnowledgeSearchMode",
    "LocaleConfig",
    "McpOAuthAccess",
    "McpOAuthTokenIn",
    "McpOAuthTokenOut",
    "MemoryConfig",
    "MemoryScope",
    "NotifyTeamConfig",
    "NotifyTeamStyle",
    "PanelLayout",
    "PanelPreset",
    "PanelPresetsResponse",
    "PipelineConfig",
    "PipelineMode",
    "PrivacyConfig",
    "ProviderRef",
    "ProviderSlot",
    "QaConfig",
    "QaField",
    "RecordingConfig",
    "ResolvedAgentConfig",
    "ResolvedCompliance",
    "ResolvedProvider",
    "StorageTier",
    "SttRedaction",
    "TelephonyConfig",
    "ThinkingSound",
    "ToolsConfig",
    "TurnDetectorSettings",
    "TurnHandlingOptions",
    "VoiceConfig",
    "effective_languages",
    "panel_preset",
    "pipeline_issues",
    "voice_for_language",
]


class AvatarOptions(BaseModel):
    """Publisher-side options applied to whichever avatar plugin is selected."""

    participant_name: str = "Avatar"
    video_quality: Literal["low", "medium", "high", "very_high"] | None = None
    idle_timeout_s: int | None = None
    max_duration_s: int | None = None


class PipelineConfig(BaseModel):
    """Which providers fill which slot, and how turns are handled.

    Slot requirements per mode are reported by :func:`pipeline_issues` rather
    than raised here: the api returns them as a ``ValidationResult`` so a
    half-finished pipeline can still be saved as a draft.
    """

    mode: PipelineMode = "cascaded"
    realtime: ProviderRef | None = None
    stt: ProviderRef | None = None
    llm: ProviderRef | None = None
    tts: ProviderRef | None = None
    avatar: ProviderRef | None = None
    avatar_options: AvatarOptions = AvatarOptions()
    image_gen: ProviderRef | None = None
    workflow_llm: ProviderRef | None = None
    vad: ProviderRef | None = None
    turn_detection: ProviderRef | None = None
    noise_cancellation: ProviderRef | None = None
    turn_handling: Annotated[TurnHandlingOptions | dict[str, Any], Field(union_mode="left_to_right")] = {}
    """``AgentSession(turn_handling=...)`` (V5-07). Validated against the typed model and kept
    as a plain dict of the keys that were set; a dict whose typed keys do not validate is kept
    as it is (compatibility). Used as is only with ``conversation_preset == "custom"``."""
    conversation_preset: ConversationPreset = "custom"
    """A named preset replaces the turn-taking keys of ``turn_handling`` when a session starts
    (``turn_handling.resolve_turn_handling``); it is never stored expanded."""
    turn_detector: TurnDetectorSettings | None = None
    """Where the end-of-turn model runs and how sensitive it is; unset = as before V5-07."""

    @field_validator("turn_handling", mode="after")
    @classmethod
    def _turn_handling_as_dict(cls, value: TurnHandlingOptions | dict[str, Any]) -> dict[str, Any]:
        """Keep the runtime value a plain dict of the stored keys (one shape for every consumer)."""
        if isinstance(value, TurnHandlingOptions):
            dumped: dict[str, Any] = value.model_dump()
            return dumped
        return value


#: The most languages one agent may list (``VoiceConfig.languages``, V5-31). Enforced by a
#: validator rather than ``max_length`` so the TypeScript type stays a plain array.
MAX_AGENT_LANGUAGES = 10


class VoiceConfig(BaseModel):
    """Greeting and conversational behaviour."""

    greeting: str = "Hello! How can I help you today?"
    greeting_mode: Literal["say", "generate"] = "say"
    language: str = "en"
    allow_interruptions: bool = True
    user_away_timeout_s: float | None = 15.0
    first_speaker: Literal["agent", "user"] = "agent"
    thinking_sound: ThinkingSound = "none"
    """A built-in clip played while the agent waits on a blocking tool (never on the text channel)."""
    ambient_sound: Annotated[str, Field(pattern=AMBIENT_SOUND_PATTERN)] = "none"
    """A background clip played for the whole call (V5-07): ``none``, a built-in name
    (``turn_handling.AMBIENT_SOUNDS``) or ``asset:<id>`` (an uploaded clip, played from a later
    package). Needs audio output; never on the text channel."""
    languages: list[Annotated[str, Field(pattern=LANGUAGE_CODE_PATTERN)]] = Field(
        default=[],
        description=(
            "The languages the agent may speak (V5-31); the first is the default. Empty = only "
            "`language`. With more than one, the agent can switch mid-call (`switch_language`). "
            "At most 10."
        ),
    )
    auto_detect: bool = Field(
        default=False,
        description=(
            "Ask the transcriber to detect the caller's language (`multi` on Deepgram and LiveKit "
            "Inference) and follow it when it is one of `languages` (V5-31)."
        ),
    )
    voices_by_language: dict[Annotated[str, Field(pattern=LANGUAGE_CODE_PATTERN)], ProviderRef] = Field(
        default={},
        description=(
            "A text-to-speech voice per language (V5-31), keyed like `languages`; a language without "
            "one keeps the pipeline's voice."
        ),
    )

    @field_validator("languages", mode="after")
    @classmethod
    def _unique_languages(cls, value: list[str]) -> list[str]:
        if len(value) > MAX_AGENT_LANGUAGES:
            raise ValueError(f"list at most {MAX_AGENT_LANGUAGES} languages")
        seen: set[str] = set()
        for code in value:
            if code.lower() in seen:
                raise ValueError(f"'{code}' is listed twice")
            seen.add(code.lower())
        return value


def effective_languages(voice: VoiceConfig) -> list[str]:
    """The agent's languages, default first: ``voice.languages``, or ``[voice.language]`` (V5-31)."""
    return list(voice.languages) if voice.languages else [voice.language]


def voice_for_language(voice: VoiceConfig, code: str) -> ProviderRef | None:
    """The ``voices_by_language`` entry for ``code``: an exact key first, then the same base code."""
    if code in voice.voices_by_language:
        return voice.voices_by_language[code]
    wanted = base_language(code)
    for key, ref in voice.voices_by_language.items():
        if base_language(key) == wanted:
            return ref
    return None


class CapabilitiesConfig(BaseModel):
    """Which input affordances the session page offers."""

    camera: bool = False
    screen_share: bool = False
    chat_input: bool = True
    vision_inject_per_turn: bool = True
    dtmf: bool = False


#: ``NotifyTeamConfig.style``: a Slack incoming webhook (``{"text": ...}``) or any other
#: webhook (a JSON object with the fields).
NotifyTeamStyle = Literal["slack", "generic"]


class NotifyTeamConfig(BaseModel):
    """Where ``notify_team`` (and ``escalate_to_human``) posts a summary for the team (V5-25).

    The webhook URL is a secret: it is kept in an ``http-tool-secret`` key under
    ``secret_name`` and reaches the worker resolved, never in the stored config.
    """

    credential_id: str = Field(description="An `http-tool-secret` key holding the webhook URL")
    secret_name: str = Field(
        default="TEAM_WEBHOOK_URL",
        pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$",
        description="The name the webhook URL is stored under in that key",
    )
    style: NotifyTeamStyle = Field(
        default="slack", description="`slack` posts a Slack message; `generic` posts JSON fields"
    )
    on_escalation: bool = Field(
        default=True, description="Also post when the agent escalates the call to a person"
    )
    include_transcript: bool = Field(
        default=False,
        description="Add the recent conversation to the message. Off by default: a transcript may "
        "hold personal details.",
    )


class ToolsConfig(BaseModel):
    """Built-in tool gating plus references to admin-authored tool rows."""

    builtin_disabled: list[str] = []
    http_request_enabled: bool = False
    tool_ids: list[str] = []
    max_tool_steps: int = 3
    execution_default: ToolExecutionMode = "blocking"
    """How read tools run when they set no mode of their own: GET HTTP tools and the built-ins
    in ``BACKGROUNDABLE_BUILTINS``. Never reaches writes, MCP tools, telephony, forms or flow edges."""
    builtin_execution: dict[str, ToolExecution] = {}
    """Per built-in execution settings, keyed by a name in ``BACKGROUNDABLE_BUILTINS``."""
    apps: AppsMode = AppsMode()
    """Connected apps (docs/v5/COMPOSIO.md D-V5-C6): ``off`` by default. The api provisions the
    app server or tool finder on save and attaches it through ``tool_ids``."""
    web_search: ProviderRef | None = Field(
        default=None,
        description="The web search service (a `web_search` provider such as Tavily or Brave); "
        "set, the agent gets the `web_search` tool.",
    )
    sms: ProviderRef | None = Field(
        default=None,
        description="The text-message service (an `sms` provider such as Twilio or Telnyx) and its "
        "sending number; set, the agent gets the `send_sms` tool.",
    )
    fetch_url_allowed_hosts: list[str] = Field(
        default=[],
        max_length=50,
        description="Web sites the agent may read pages from with `fetch_url`; empty, it has no "
        "`fetch_url` tool.",
    )
    notify_team: NotifyTeamConfig | None = Field(
        default=None,
        description="A webhook the agent posts a short summary to (`notify_team`, and escalations).",
    )


#: ``KnowledgeConfig.mode``: how a knowledge search ranks chunks (V5-04's ``KnowledgeService``).
KnowledgeSearchMode = Literal["vector", "hybrid"]

#: ``KnowledgeConfig.rerank`` values the platform implements today. The field itself is a string so
#: a ``connection:<id>`` reranker can be accepted later (V5-20) without a contracts change; the api
#: validator refuses anything outside this set until then.
KNOWLEDGE_RERANK_VALUES: tuple[str, ...] = ("none", "local")

#: ``KnowledgeConfig.query_mode``: what the auto-inject search query is built from.
KnowledgeQueryMode = Literal["last_turn", "conversation"]


class KnowledgeConfig(BaseModel):
    """Knowledge bases attached to the agent and how they are injected.

    The v2 fields (V5-06) default so an agent saved before them retrieves at
    least what it did: hybrid search on, no score floor, no rerank.
    """

    kb_ids: list[str] = []
    auto_inject: bool = True
    top_k: int = 4
    min_score: float | None = Field(
        default=None,
        description="Drop hits scoring below this (0-1); null keeps every hit. In hybrid mode without "
        "rerank the score is rank-derived, so a floor only trims the tail of the list.",
    )
    prefetch: bool = Field(
        default=True,
        description="Start the auto-inject search on the caller's interim transcript, so the result is "
        "usually ready when the turn ends.",
    )
    rerank: Literal["none", "local"] | str = Field(
        default="none",
        description="`local` rescores the candidates with the local cross-encoder (adds ~60-100 ms).",
    )
    mode: KnowledgeSearchMode = Field(
        default="hybrid", description="`hybrid` fuses keyword matches with embedding similarity."
    )
    max_inject_tokens: int = Field(
        default=1200,
        ge=1,
        description="Upper bound on the knowledge note added to a turn (approximate tokens).",
    )
    skip_short_turns: bool = Field(
        default=True,
        description="Skip the auto-inject search for backchannels and very short turns (yes, okay, digits).",
    )
    query_mode: KnowledgeQueryMode = Field(
        default="conversation",
        description="`conversation` searches with the last user turn plus the previous assistant sentence "
        "and flow variables; `last_turn` with the user's words only.",
    )


class RecordingConfig(BaseModel):
    """Whether and where the session is recorded through Egress."""

    enabled: bool = False
    audio_only: bool = True
    storage_config_id: str | None = None
    retention_days: int | None = None
    require_consent: bool = Field(
        default=False,
        description="Start the recording only after the caller agrees (a tap on a consent block, or out "
        "loud); a caller who declines is never recorded.",
    )
    consent_text: str | None = Field(
        default=None,
        max_length=MAX_CONSENT_TEXT_CHARS,
        description="The question asked before recording when no consent block supplies one; empty uses "
        "the workspace's recording question (Settings → Compliance).",
    )


class DisclosureConfig(BaseModel):
    """Telling callers they are talking to an AI (V5-15, D-V5-22): on by default.

    ``greeting`` puts the line at the start of the spoken greeting (a
    ``{disclosure}`` placeholder in the greeting marks where, otherwise it goes
    first); ``banner`` leaves it to the on-screen banner of a ``consent`` block;
    ``both`` does both. On a phone call there is no screen, so ``banner`` is
    spoken in the greeting too.
    """

    enabled: bool = True
    text: str | None = Field(
        default=None,
        max_length=MAX_CONSENT_TEXT_CHARS,
        description="The disclosure line; empty uses the workspace's (Settings → Compliance).",
    )
    position: DisclosurePosition = "both"


class QaConfig(BaseModel):
    """Post-call scoring of the session by an LLM judge."""

    enabled: bool = False
    rubric_prompt: str | None = None
    model: ProviderRef | None = None
    fields: list[QaField] = Field(
        default=[],
        description="Post-call fields (V5-30, at most 20): the judge fills each from the finished "
        "conversation into `session_qa.raw['fields']`; they run only when QA is on.",
    )

    @field_validator("fields")
    @classmethod
    def _unique_field_names(cls, fields: list[QaField]) -> list[QaField]:
        # A validator rather than `max_length`: the TS generator expands a small `maxItems`
        # into a tuple union the console could not assign an array to.
        if len(fields) > MAX_QA_FIELDS:
            raise ValueError(f"at most {MAX_QA_FIELDS} post-call fields")
        names = [field.name for field in fields]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"post-call field names must be unique: {', '.join(duplicates)}")
        return fields


#: ``PrivacyConfig.stt_redact``: what the speech-to-text provider masks before the transcript
#: exists (Deepgram's ``redact`` classes). Honoured only by providers whose registry entry lists
#: the class in ``capabilities.redaction``.
SttRedaction = Literal["pci", "pii", "phi", "numbers"]

#: ``PrivacyConfig.storage_tier``: what is kept once a session ends. ``full`` keeps everything;
#: ``redacted`` rewrites the transcript and the event texts with card numbers, emails, phone
#: numbers and long digit runs masked (plus the ``scrub_model`` pass when set); ``basic`` does
#: the same and also drops tool arguments and results from the events.
StorageTier = Literal["full", "redacted", "basic"]


class PrivacyConfig(BaseModel):
    """What the platform keeps and shares about a caller (V5-30, P §4.2 C10).

    The defaults keep today's behaviour: nothing is masked, every session is
    kept in full and third-party telemetry receives the conversation.
    """

    stt_redact: list[SttRedaction] = Field(
        default=[],
        description="Classes the speech-to-text provider masks as it transcribes (`pci` card numbers, "
        "`pii` personal details, `phi` health details, `numbers` every number). Only providers that "
        "support it apply it; others ignore it with a validation warning.",
    )
    storage_tier: StorageTier = Field(
        default="full",
        description="What is kept after the call: `full` everything; `redacted` the transcript and "
        "events with card numbers, emails, phone numbers and long numbers masked (and the "
        "`scrub_model` pass when set); `basic` also drops tool arguments and results.",
    )
    telemetry_pii: bool = Field(
        default=True,
        description="Whether a third-party OpenTelemetry exporter configured on the worker (an "
        "`OTEL_EXPORTER_OTLP_*` endpoint) receives the conversation text and tool payloads "
        "(`LIVEKIT_TELEMETRY_ALLOW_PII`). It does not change what LiveKit Cloud Insights "
        "receives (that is the project's setting in the LiveKit Cloud dashboard). The worker "
        "applies it once per process: the first session's setting holds for later sessions "
        "that reuse the same process.",
    )
    scrub_model: ProviderRef | None = Field(
        default=None,
        description="An LLM that also masks names, addresses and other personal details after the "
        "call when `storage_tier` is not `full`. Only an OpenAI-compatible provider with a key "
        "(OpenAI, OpenRouter) can run from the api; others are skipped with a warning.",
    )

    @field_validator("stt_redact")
    @classmethod
    def _dedupe(cls, values: list[SttRedaction]) -> list[SttRedaction]:
        return list(dict.fromkeys(values))


#: ``MemoryConfig.scope``: whose memories an agent reads and writes. ``agent`` keeps what this
#: agent learnt about a caller to itself; ``workspace`` shares one memory of the caller with every
#: agent of the workspace that also uses the ``workspace`` scope.
MemoryScope = Literal["agent", "workspace"]

#: Longest ``MemoryConfig.retention_days`` (ten years).
MAX_MEMORY_RETENTION_DAYS = 3650
#: Longest ``MemoryConfig.consent_line``.
MAX_MEMORY_CONSENT_CHARS = 500


class MemoryConfig(BaseModel):
    """What the agent remembers about a returning caller (V5-40, D-V5-17). Off by default.

    Callers are identified by a pseudonymous id (a keyed hash of their phone number or of
    the identity the embedding site passed), never by the number itself. The memories are
    read once when the session starts and written once after it ends, never during the call.
    """

    enabled: bool = Field(
        default=False,
        description="Remember callers across sessions. Nothing is read or written while this is off.",
    )
    scope: MemoryScope = Field(
        default="agent",
        description="`agent`: only this agent reads and writes its memories of a caller; `workspace`: "
        "the memories are shared with every agent of the workspace that also uses `workspace`.",
    )
    retention_days: int = Field(
        default=90,
        ge=1,
        le=MAX_MEMORY_RETENTION_DAYS,
        description="Days a caller's memories are kept after their last session; then they are deleted.",
    )
    consent_line: str | None = Field(
        default=None,
        max_length=MAX_MEMORY_CONSENT_CHARS,
        description="A sentence the agent says early in the call when the session will be remembered "
        "(for example that the conversation is remembered to help next time). `None`: no line.",
    )
    max_recall_tokens: int = Field(
        default=400,
        ge=50,
        le=2000,
        description="The most text (in tokens, about four characters each) of recalled memories added "
        "to the agent's instructions at the start of a session.",
    )
    verbatim: bool = Field(
        default=False,
        description="Store what the caller said as it was, without a model picking out the facts "
        "(no model call; the masking of `privacy.storage_tier` still applies). Off: the agent's "
        "language model extracts short facts after the call.",
    )

    @field_validator("consent_line")
    @classmethod
    def _blank_line_is_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = " ".join(value.split())
        return stripped or None


class PanelLayout(BaseModel):
    """Which panel renders the session, and (for ``composite``) its blocks."""

    panel_id: str = "composite"
    layout: Literal["side", "wide"] = "side"
    blocks: list[BlockSpec] = []
    accept_state_delta: bool = Field(
        default=False,
        description="Let the caller's page change what some panel blocks show (V5-43, ruling on "
        "ask #309). On, the page may send an AG-UI `STATE_DELTA` (`AgentAction.action == "
        '"state_delta"`) that writes the display blocks `update_block` may write, except '
        "`kb_citations` and `custom`; requestable, link, consent, upload, captions and handoff "
        "blocks are never writable from the page. Off (the default), every such change is refused, "
        "so the page cannot alter what `describe_panel` hands the model or what the console's "
        "live view shows.",
    )


class PanelPreset(BaseModel):
    """A ready-made panel a builder can start from (V6-08): ``GET /v1/panels/presets``.

    Choosing one replaces the agent's ``panel`` with ``panel`` (a copy); the builder
    then changes it like any other panel.
    """

    id: str
    name: str
    description: str
    panel: PanelLayout


class PanelPresetsResponse(BaseModel):
    """``GET /v1/panels/presets``: the ready-made panels, in the order the console offers them."""

    items: list[PanelPreset]


#: The "Notebook" preset's id.
NOTEBOOK_PRESET_ID = "notebook"

#: The "Notebook" preset (V6-08, D-V6-15): a wide panel with a status stamp, a notebook
#: (notes, a "still needed" checklist, a summary card and a drawing board, in a handwriting
#: theme; the caller may write in it too) and a gallery for pictures (``generate_image``).
#: Treat it as read-only: :func:`panel_preset` hands out copies.
NOTEBOOK_PRESET = PanelLayout(
    panel_id="composite",
    layout="wide",
    blocks=[
        BlockSpec(id="status", type="status", order=0),
        BlockSpec(
            id="notebook",
            type="notebook",
            title="Notebook",
            order=1,
            config={
                "paper": "ruled",
                "font": "handwritten",
                "sections": [
                    {"id": "notes", "title": "Notes", "kind": "text"},
                    {"id": "still_needed", "title": "Still needed", "kind": "checklist"},
                    {"id": "summary", "title": "Summary", "kind": "details"},
                    {"id": "sketch", "title": "Sketch", "kind": "ink"},
                ],
                "caller_can_write": True,
                "caller_can_draw": False,
            },
        ),
        BlockSpec(id="gallery", type="gallery", title="Pictures", order=2),
    ],
)

#: Every ready-made panel, in the order the console offers them. Treat as read-only.
PANEL_PRESETS: tuple[PanelPreset, ...] = (
    PanelPreset(
        id=NOTEBOOK_PRESET_ID,
        name="Notebook",
        description=(
            "A wide notebook the agent writes in as the call goes: notes, what is still needed, "
            "a summary and a drawing board, with pictures beside it. The caller can write in it too."
        ),
        panel=NOTEBOOK_PRESET,
    ),
)


def panel_preset(preset_id: str) -> PanelLayout | None:
    """A copy of the ready-made panel ``preset_id`` (``None`` when there is none), safe to change."""
    for preset in PANEL_PRESETS:
        if preset.id == preset_id:
            return preset.panel.model_copy(deep=True)
    return None


#: ``LocaleConfig.caller_timezone``: ``detect`` resolves the caller's own zone per session
#: (browser, then phone number, then the business zone); ``business`` always uses the
#: business timezone (R-V5-10).
CallerTimezoneMode = Literal["detect", "business"]


class LocaleConfig(BaseModel):
    """Whose clock the agent talks in (R-V5-10).

    ``AgentConfig.timezone`` stays the **business** timezone (opening hours, seeds,
    booking defaults); this block says how the **caller's** timezone is chosen at
    session start.
    """

    caller_timezone: CallerTimezoneMode = Field(
        default="detect",
        description="`detect` uses the caller's own timezone (from the browser, else the phone "
        "number, else the business timezone); `business` always uses the business timezone.",
    )


class AgentLimits(BaseModel):
    """Per-agent guard rails enforced by ``connect`` (CONTRACTS-V2 §3.3)."""

    max_concurrent_sessions: int = 5
    max_session_duration_s: int = 1800
    rate_per_ip_per_min: int = 6
    rate_per_agent_per_min: int = 60


class AgentConfig(BaseModel):
    """The full, admin-editable configuration of one agent (stored as JSON).

    ``v`` accepts ``1`` for one release: v1 rows are rewritten to v2 by the
    ``v2_008_agentconfig_v2`` migration, and the console still posts ``v: 1``
    until WP-3 lands.
    """

    v: Literal[1, 2] = 2
    instructions: str
    pipeline: PipelineConfig
    voice: VoiceConfig = VoiceConfig()
    capabilities: CapabilitiesConfig = CapabilitiesConfig()
    tools: ToolsConfig = ToolsConfig()
    knowledge: KnowledgeConfig = KnowledgeConfig()
    panel: PanelLayout = PanelLayout()
    recording: RecordingConfig = RecordingConfig()
    qa: QaConfig = QaConfig()
    flow: FlowSpec | None = None
    #: R-V2-21: the transfer destinations (``transfer_call`` registers only when non-empty).
    telephony: TelephonyConfig = TelephonyConfig()
    pack_settings: dict[str, Any] = {}
    timezone: str = Field(
        default="UTC",
        description="The business timezone (IANA name): opening hours and bookings are in this zone.",
    )
    locale: LocaleConfig = LocaleConfig()
    """How the caller's timezone is chosen (R-V5-10); agents saved before it behave as ``detect``."""
    disclosure: DisclosureConfig = DisclosureConfig()
    """The AI disclosure (V5-15). On by default: agents saved before it now open with the line."""
    privacy: PrivacyConfig = PrivacyConfig()
    """Redaction, storage tier and telemetry (V5-30); agents saved before it keep everything."""
    memory: MemoryConfig = MemoryConfig()
    """Caller memory across sessions (V5-40); off for every agent saved before it."""
    guardrails: GuardrailsConfig = GuardrailsConfig()
    """Checks on what the caller says, what the agent says and what tools return (V5-39); no rules
    for every agent saved before it, so nothing is checked and nothing is added to a turn."""
    tests: list[AgentTest] = Field(
        default=[],
        max_length=MAX_AGENT_TESTS,
        description="Simulated conversations the agent is tested with (V5-29); they version with the config.",
    )
    publish_gate: PublishGate = PublishGate()
    """V5-29: opt-in; when on, publishing needs a passing test run on the version being published."""

    @field_validator("tests")
    @classmethod
    def _unique_test_ids(cls, value: list[AgentTest]) -> list[AgentTest]:
        seen: set[str] = set()
        duplicates: set[str] = set()
        for case in value:
            (duplicates if case.id in seen else seen).add(case.id)
        if duplicates:
            raise ValueError(f"test ids must be unique: {', '.join(sorted(duplicates))}")
        return value


class ResolvedProvider(BaseModel):
    """A provider ready to construct: class path plus complete constructor kwargs."""

    provider_id: str
    python_class: str
    model: str | None
    kwargs: dict[str, object]
    capabilities: ModelCapabilities | None = None
    """What the model can do (V4-08, D-V4-24): filled for ``llm``, ``workflow_llm`` and
    ``realtime`` from declared → detected → live catalog → registry; ``None`` = not resolved."""


class McpOAuthAccess(BaseModel):
    """V5-16: one signed-in MCP server's short-lived access, as the worker receives it.

    The api is the OAuth client (research-v4 tools §4.3.6): it keeps the refresh token,
    the client secret and the token endpoint, and hands the worker only an access token
    that expires minutes from now. ``name`` and ``url`` match the server's entry in
    :attr:`ResolvedAgentConfig.tools`; ``tool_id`` is what the worker names when it asks
    ``POST /internal/v1/tools/{tool_id}/oauth/token`` for a fresh token. ``access_token``
    is ``None`` when the api could not refresh it at session start (the worker fetches one
    before its first request). **Contains a secret.**
    """

    tool_id: str
    name: str
    url: str
    access_token: str | None = None
    expires_at: datetime | None = None
    """When ``access_token`` stops working (``None``: the provider did not say)."""


class McpOAuthTokenIn(BaseModel):
    """``POST /internal/v1/tools/{tool_id}/oauth/token`` (worker only, V5-16).

    The token is bound to the session's agent: the api answers only when the session is
    live and its agent uses the tool.
    """

    session_id: str = Field(min_length=1, max_length=64)
    rejected_token_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    """The hex SHA-256 of an access token the MCP server just refused (401). When it is
    still the current token the api refreshes; when another request already rotated it,
    the api returns the new one without a second refresh."""


class McpOAuthTokenOut(BaseModel):
    """A fresh access token for one MCP server (never a refresh token). **Contains a secret.**"""

    access_token: str
    expires_at: datetime | None = None


class ResolvedAgentConfig(BaseModel):
    """What the worker receives from ``/internal/v1/sessions/{id}/resolved``.

    Contains decrypted credentials in ``resolved[*].kwargs`` and substituted tool
    secrets in ``tools``. Never log this object.

    The v2 additions all carry defaults so a v1 api (before V2-01/V2-03 land)
    still produces a valid document.
    """

    v: Literal[2] = 2
    session_id: str
    agent_id: str
    agent_slug: str
    config_version: int
    pack_id: str
    ui_panel_id: str
    config: AgentConfig
    resolved: dict[ProviderSlot, ResolvedProvider]
    tools: list[ToolDefinition]
    kb_ids: list[str]
    participant_identity: str
    workspace_id: str = ""
    channel: SessionChannel = "web"
    connection: ConnectionInfo = ConnectionInfo()
    recording: RecordingConfig = RecordingConfig()
    panel: PanelLayout = PanelLayout()
    installed_provider_ids: list[str] | None = None
    #: R-V2-22: the session's seed variables (``sessions.variables``; an outbound call's
    #: ``CallCreate.variables``). Flow agents start their ``FlowState.variables`` with them;
    #: prompt agents get them appended to ``config.instructions`` by the worker.
    variables: dict[str, Any] = {}
    #: V4-17 (docs/v4/COSTS.md D-V4-45, R-V4-48): the vendors this workspace reconciles
    #: against their own per-request charge (``workspaces.settings["cost"]["reconcile"]``,
    #: e.g. ``["openrouter"]``). Non-empty makes the worker collect the per-request ids
    #: of its LLM/STT/TTS calls and post them as one ``metrics {kind: "provider_requests"}``
    #: event; empty (the default) collects nothing.
    cost_reconcile: list[str] = []
    #: R-V5-10: the effective business timezone: ``config.timezone`` when it is a valid IANA
    #: name, else the workspace default (``workspaces.settings.locale.timezone``), else
    #: ``"UTC"``. The api also writes it into this document's ``config.timezone``. ``None``
    #: (an api before V5-51) = the worker reads ``config.timezone`` itself.
    business_timezone: str | None = None
    #: R-V5-10: ``config.locale``, repeated here for the worker.
    locale: LocaleConfig = LocaleConfig()
    #: V5-15: the workspace's effective disclosure and recording wording (Settings → Compliance).
    #: ``None`` (an api before V5-15) = the worker uses the default jurisdiction's preset.
    compliance: ResolvedCompliance | None = None
    #: V5-16: the access of each MCP server that signs in (``auth.kind == "oauth"``), matched to
    #: its ``tools`` entry by ``name`` and ``url``. Never a refresh token, a client secret or a
    #: token endpoint. A server with no entry here has no usable sign-in; the worker skips it.
    mcp_oauth: list[McpOAuthAccess] = []
    #: V5-25: the vendor each configured network built-in calls, with its key (``web_search``
    #: from ``config.tools.web_search``, ``sms`` from ``config.tools.sms``, ``notify_team`` with
    #: ``kwargs={"webhook_url": ...}``). **Contains secrets.** Empty (an api before V5-25) = those
    #: tools are not registered.
    builtin_providers: dict[BuiltinProviderSlot, ResolvedProvider] = {}
    #: V5-31: ``config.voice.voices_by_language`` resolved with their keys, same keys. **Contains
    #: secrets.** Empty (an api before V5-31, or no per-language voice) = ``switch_language``
    #: keeps the pipeline's voice.
    voices_by_language: dict[str, ResolvedProvider] = {}
    #: V5-29: tool name → the fixture a mocked tool returns instead of calling out, for a test
    #: case's scratch session only (``AgentTest.mocks``). The worker honours it for HTTP tools
    #: and connected-app actions. Empty (every real session, and an api before V5-29) = no mocks.
    tool_mocks: dict[str, Any] = {}
    #: V5-32 (D-V5-21): how the worker places a warm transfer's private consult call: the
    #: connection's outbound trunk and the ``warm`` targets the dialing policy allows today.
    #: ``None`` (not a LiveKit Cloud connection, no single synced outbound trunk, no allowed warm
    #: target, not a phone call, or an api before V5-32) = every transfer runs cold.
    warm_transfer: WarmTransferRoute | None = None


#: Slots each pipeline mode requires, in the order the console renders them.
REQUIRED_SLOTS: dict[PipelineMode, tuple[str, ...]] = {
    "cascaded": ("stt", "llm", "tts"),
    "realtime": ("realtime",),
    "half_cascade": ("realtime", "tts"),
}


def pipeline_issues(pipeline: PipelineConfig) -> list[Issue]:
    """Return the slot-completeness problems of a pipeline.

    Only structural rules that need no database or registry lookup are checked;
    the api adds provider-level findings (unknown id, missing credential,
    ``text_modality`` for half-cascade) on top.

    Args:
        pipeline: The pipeline to inspect.

    Returns:
        One :class:`~lkap_contracts.common.Issue` per missing required slot, in
        slot order.
    """
    issues: list[Issue] = []
    for slot in REQUIRED_SLOTS[pipeline.mode]:
        if getattr(pipeline, slot) is None:
            issues.append(
                Issue(
                    path=f"pipeline.{slot}",
                    message=f"{pipeline.mode} mode requires a {slot} provider",
                    severity="error",
                )
            )
    return issues


def effective_qa(config: AgentConfig) -> QaConfig:
    """The QA settings a session actually runs with (ruling R-V2-11).

    A flow ``qa`` node is the author's intent to score the call, so it turns QA
    on without anybody also flipping ``qa.enabled``. The node's
    ``rubric_prompt`` (when set) replaces ``qa.rubric_prompt``; ``qa.model`` is
    unchanged. Prompt agents (and flows without a ``qa`` node) get
    ``config.qa`` back as-is. The api (provider resolution, validation slots)
    and the worker (``prepare_flow_resolved``) both call this one definition.

    Args:
        config: The agent configuration.

    Returns:
        The effective :class:`QaConfig`; ``config.qa`` itself when nothing changes.
    """
    flow = config.flow
    qa_node = next((n for n in flow.nodes if isinstance(n, QaNode)), None) if flow is not None else None
    if qa_node is None:
        return config.qa
    update: dict[str, Any] = {"enabled": True}
    if qa_node.rubric_prompt:
        update["rubric_prompt"] = qa_node.rubric_prompt
    return config.qa.model_copy(update=update)
