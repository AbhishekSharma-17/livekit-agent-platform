"""The conversation's language: allowed languages, detection, and a mid-call switch (V5-31).

An agent lists the languages it may speak in `VoiceConfig.languages` (the first
is the default; empty = the single `voice.language`), optionally a voice per
language (`voices_by_language`) and `auto_detect`. This module keeps the
session's language state and does what a switch means for each part of the
pipeline, verified against livekit-agents 1.8.3:

* **Transcriber.** With `auto_detect` the STT slot is built with the registry's
  detection value (`capabilities.language_detection`: `multi` for Deepgram and
  LiveKit Inference, `unknown` for Sarvam; the factory maps `multi` to
  `detect_language` for the OpenAI transcriptions class) — see
  :func:`apply_stt_detection`, called from `session_builder.prepare_resolved`.
  The transcriber then keeps detecting and a switch never pins it. Without
  detection, a switch calls `stt.update_options(language=...)` when the entry
  says it can (`capabilities.language_switch`; `Agent.update_options` would
  replace the whole object, which is not needed for a language).
* **Voice.** `Agent.update_options(tts=...)` swaps the TTS on the running
  pipeline (`voice/agent.py`: "switching a component mid-call (e.g. a different
  STT language or TTS voice)"; `AgentActivity._update_models`). The card's
  handoff is not used: a handoff would re-run `on_enter` (the greeting and the
  UI snapshot). A language with its own voice (`voices_by_language`) gets that
  voice, built once through the provider factory; going back to a language
  without one restores the pipeline's voice. A flow node entered later
  re-applies the current language's voice (its own `tts=` would otherwise win).
* **Reply language.** A fixed rule joins the system prompt at session start
  (:func:`language_rule`, byte-identical across sessions of one config); a
  switch appends a short system note at the tail of the chat context
  (:func:`reply_note`) — the R-V5-10 pattern, which keeps a cached prompt prefix
  and works on flow nodes whose prompt the runtime owns.
* **Detection with hysteresis.** Code-switched speech (Hindi and English in one
  sentence) flips the detected language from turn to turn, so a switch from
  detection needs :data:`DETECTION_TURNS` consecutive caller turns in the same
  allowed language, each at least :data:`MIN_DETECTION_CHARS` long (the SDK
  itself ignores short detections, `audio_recognition.py`). The model can
  always switch at once with the `switch_language` tool.

Transliterated Hindi ("meri gaadi chori ho gayi") is often tagged as English by
a detector; nothing here corrects that. Replies in Hindi are asked for in
Devanagari (:data:`SCRIPT_HINTS`) because a Hindi voice mispronounces
romanised Hindi.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from livekit.agents import NOT_GIVEN
from lkap_contracts.agent_config import (
    AgentConfig,
    ResolvedAgentConfig,
    ResolvedProvider,
    effective_languages,
)
from lkap_contracts.api_models import LanguageSwitchedEvent
from lkap_contracts.providers import LANGUAGE_NAMES, ProviderSpec, base_language, language_name
from lkap_contracts.providers import get as get_spec

from lkap_agent.logging import get_logger
from lkap_agent.observability import LANGUAGE_EXTRA_KEY
from lkap_agent.providers.factory import (
    OPENAI_TRANSCRIPTION_STT_CLASS,
    ProviderFactory,
    openai_transcription_language_kwargs,
)

__all__ = [
    "DETECTION_TURNS",
    "LANGUAGE_EVENT",
    "LANGUAGE_EXTRA_KEY",
    "LANGUAGE_STATE_KEY",
    "LANGUAGE_VOICES_USERDATA_KEY",
    "MIN_DETECTION_CHARS",
    "SCRIPT_HINTS",
    "SessionLanguages",
    "SwitchResult",
    "apply_stt_detection",
    "ensure_session_languages",
    "language_rule",
    "reply_note",
    "session_languages",
    "switch_language_on",
]

logger = get_logger(__name__)

#: `SessionContext.userdata` key of the session's :class:`SessionLanguages`.
LANGUAGE_STATE_KEY: Final[str] = "lkap.languages"
#: `SessionContext.userdata` key of `ResolvedAgentConfig.voices_by_language` (the worker stores
#: it beside the built-in tools' vendors; resolved providers, **secrets inside**).
LANGUAGE_VOICES_USERDATA_KEY: Final[str] = "lkap.language_voices"
#: The session event a switch records (`LanguageSwitchedEvent`).
LANGUAGE_EVENT: Final[str] = "language_switched"

#: Consecutive caller turns in one other allowed language before detection switches.
DETECTION_TURNS: Final[int] = 2
#: A caller turn shorter than this (characters) does not count towards detection.
MIN_DETECTION_CHARS: Final[int] = 8

#: The script a reply is written in, for languages whose voice needs their own script.
SCRIPT_HINTS: Final[dict[str, str]] = {
    "hi": "Devanagari",
    "mr": "Devanagari",
    "ne": "Devanagari",
    "bn": "Bengali",
    "gu": "Gujarati",
    "pa": "Gurmukhi",
    "ta": "Tamil",
    "te": "Telugu",
    "kn": "Kannada",
    "ml": "Malayalam",
    "od": "Odia",
    "ur": "Urdu (Perso-Arabic)",
    "ar": "Arabic",
    "ru": "Cyrillic",
    "uk": "Cyrillic",
    "el": "Greek",
    "he": "Hebrew",
    "th": "Thai",
    "ja": "Japanese",
    "ko": "Hangul",
    "zh": "Chinese characters",
}

SwitchSource = Literal["tool", "detected"]


def _label(code: str) -> str:
    """`Hindi (hi)`: the name and the code as configured."""
    return f"{language_name(code)} ({code})"


def _script_clause(code: str) -> str:
    script = SCRIPT_HINTS.get(base_language(code))
    return f", written in {script} script so the voice pronounces it" if script else ""


def language_rule(languages: list[str], *, auto_detect: bool) -> str:
    """The fixed prompt block for a multilingual agent (empty for one language without detection).

    It depends only on the agent's config, so every session of one config
    composes the same prompt (provider prompt caching holds).
    """
    if len(languages) < 2 and not auto_detect:
        return ""
    default = languages[0]
    others = languages[1:]
    lines = [f"Languages: start in {_label(default)}."]
    if others:
        lines.append(f"You may also speak {', '.join(_label(code) for code in others)}.")
        lines.append(
            "When the caller speaks one of these languages or asks for one, call switch_language with "
            "its code first, then reply in it. Never switch to a language that is not listed; say "
            "which languages you can use instead."
        )
    scripts = [code for code in languages if base_language(code) in SCRIPT_HINTS]
    for code in scripts:
        lines.append(f"Write {language_name(code)}{_script_clause(code)}.")
    if len(languages) > 1:
        lines.append(
            "Callers often mix languages in one sentence; keep the words they use in English when "
            "that is how they said them."
        )
    return " ".join(lines)


def reply_note(code: str) -> str:
    """The tail note a switch appends: reply in `code` from now on."""
    return f"Language: from now on reply in {_label(code)}{_script_clause(code)}."


def _spec_or_none(provider_id: str | None) -> ProviderSpec | None:
    if not provider_id:
        return None
    try:
        return get_spec(provider_id)
    except KeyError:
        return None


def apply_stt_detection(resolved: ResolvedAgentConfig, slots: dict[Any, ResolvedProvider]) -> bool:
    """Build the STT slot with its detection value when the agent auto-detects (in place).

    Only the resolved kwargs change, never the stored config (the api's
    language-code warning reads the stored field). Nothing happens without
    `auto_detect`, without an STT slot, or when the registry records no
    detection value or no `language` field for the entry.

    Returns:
        Whether the slot was changed.
    """
    if not resolved.config.voice.auto_detect:
        return False
    stt = slots.get("stt")
    if stt is None:
        return False
    spec = _spec_or_none(stt.provider_id)
    value = spec.capabilities.language_detection if spec is not None else None
    if spec is None or value is None or not any(f.name == "language" for f in spec.fields):
        logger.info(
            "auto-detect is on but this transcriber cannot be asked to detect the language",
            provider_id=stt.provider_id,
        )
        return False
    slots["stt"] = stt.model_copy(update={"kwargs": {**stt.kwargs, "language": value}})
    logger.debug("stt built to detect the language", provider_id=stt.provider_id, value=value)
    return True


@dataclass(slots=True)
class SwitchResult:
    """What a switch did (also the `language_switched` event's payload)."""

    from_language: str
    to_language: str
    source: SwitchSource
    stt_switched: bool = False
    voice_switched: bool = False
    changed: bool = True

    def event_payload(self) -> dict[str, Any]:
        """The `language_switched` event payload (`LanguageSwitchedEvent`)."""
        return LanguageSwitchedEvent(
            from_language=self.from_language,
            to_language=self.to_language,
            source=self.source,
            stt_switched=self.stt_switched,
            voice_switched=self.voice_switched,
        ).model_dump()

    def message(self) -> str:
        """The tool's answer to the model."""
        if not self.changed:
            return f"Already speaking {_label(self.to_language)}. Carry on in it."
        script = _script_clause(self.to_language)
        return f"Switched to {_label(self.to_language)}. Reply in it from now on{script}."


@dataclass(slots=True)
class SessionLanguages:
    """One session's language state (shared by every flow node through `SessionContext.userdata`)."""

    allowed: list[str]
    current: str
    auto_detect: bool
    #: The STT entry can be switched with `update_options(language=...)` and is not detecting.
    switch_stt: bool
    stt_class: str | None
    voices: Mapping[str, ResolvedProvider] = field(default_factory=dict)
    factory: Any = None
    #: The `AgentSession` (its `stt`/`tts` are used when the agent has none of its own).
    session: Any = None
    built_voices: dict[str, Any] = field(default_factory=dict)
    #: The pipeline's own voice, kept on the first swap so a switch back can restore it.
    original_tts: Any = None
    has_original_tts: bool = False
    #: Detection votes of the caller turn in progress: base code -> characters.
    turn_votes: dict[str, int] = field(default_factory=dict)
    streak_language: str | None = None
    streak: int = 0

    @property
    def default(self) -> str:
        """The first allowed language."""
        return self.allowed[0]

    def match(self, requested: str) -> str | None:
        """The allowed code for a requested language: the code, its base code or its English name."""
        wanted = requested.strip()
        if not wanted:
            return None
        lowered = wanted.lower()
        for code in self.allowed:
            if code.lower() == lowered:
                return code
        base = base_language(wanted)
        for code in self.allowed:
            if base_language(code) == base:
                return code
        for code in self.allowed:
            if LANGUAGE_NAMES.get(base_language(code), "").lower() == lowered:
                return code
        return None

    def voice_ref(self, code: str) -> ResolvedProvider | None:
        """The resolved voice for `code`: the exact key, else one with the same base code."""
        if code in self.voices:
            return self.voices[code]
        base = base_language(code)
        for key, provider in self.voices.items():
            if base_language(key) == base:
                return provider
        return None

    # ---------------------------------------------------------------- detection

    def vote(self, language: str | None, text: str) -> None:
        """Count one final transcript towards the caller turn's language."""
        if not language or not text.strip():
            return
        base = base_language(str(language))
        if not base or base == "multi":
            return
        self.turn_votes[base] = self.turn_votes.get(base, 0) + len(text.strip())

    def close_turn(self, text: str) -> tuple[str | None, str | None]:
        """End the caller's turn: its detected language, and a language to switch to (or `None`).

        The turn's language is the one with the most transcribed characters. A
        switch is due after :data:`DETECTION_TURNS` consecutive turns (each at
        least :data:`MIN_DETECTION_CHARS` long) in the same allowed language
        other than the current one. Only with `auto_detect`.
        """
        votes, self.turn_votes = self.turn_votes, {}
        if not votes:
            return None, None
        base = max(votes, key=lambda code: votes[code])
        turn_language = self.match(base) or base
        if not self.auto_detect or len(text.strip()) < MIN_DETECTION_CHARS:
            return turn_language, None
        allowed = self.match(base)
        if allowed is None or base_language(allowed) == base_language(self.current):
            self.streak_language, self.streak = None, 0
            return turn_language, None
        if self.streak_language == allowed:
            self.streak += 1
        else:
            self.streak_language, self.streak = allowed, 1
        if self.streak >= DETECTION_TURNS:
            self.streak_language, self.streak = None, 0
            return turn_language, allowed
        return turn_language, None


def session_languages(ctx: Any) -> SessionLanguages | None:
    """The session's language state, when it has one."""
    userdata = getattr(ctx, "userdata", None)
    state = userdata.get(LANGUAGE_STATE_KEY) if isinstance(userdata, dict) else None
    return state if isinstance(state, SessionLanguages) else None


def _multilingual(config: AgentConfig) -> bool:
    return len(effective_languages(config.voice)) > 1 or config.voice.auto_detect


def ensure_session_languages(ctx: Any, *, factory: Any = None) -> SessionLanguages | None:
    """Create the session's language state once (the first agent of the session), or return it.

    `None` for a single-language agent without detection: nothing changes for it.
    """
    existing = session_languages(ctx)
    if existing is not None:
        return existing
    config: AgentConfig = ctx.config
    if not _multilingual(config):
        return None
    userdata = getattr(ctx, "userdata", None)
    voices_raw = userdata.get(LANGUAGE_VOICES_USERDATA_KEY) if isinstance(userdata, dict) else None
    voices = dict(voices_raw) if isinstance(voices_raw, Mapping) else {}
    stt_ref = config.pipeline.stt
    spec = _spec_or_none(stt_ref.provider_id if stt_ref is not None else None)
    detecting = (
        config.voice.auto_detect and spec is not None and spec.capabilities.language_detection is not None
    )
    allowed = effective_languages(config.voice)
    state = SessionLanguages(
        allowed=allowed,
        current=allowed[0],
        auto_detect=config.voice.auto_detect,
        switch_stt=(
            config.pipeline.mode == "cascaded"
            and spec is not None
            and spec.capabilities.language_switch
            and not detecting
        ),
        stt_class=spec.python_class if spec is not None else None,
        voices=voices,
        factory=factory,
        session=getattr(ctx, "session", None),
    )
    if isinstance(userdata, dict):
        userdata[LANGUAGE_STATE_KEY] = state
    logger.debug(
        "session languages",
        languages=allowed,
        auto_detect=state.auto_detect,
        switch_stt=state.switch_stt,
        voices=sorted(voices),
    )
    return state


def _given(value: Any) -> bool:
    return value is not None and value is not NOT_GIVEN


def _running(agent: Any, state: SessionLanguages, slot: Literal["stt", "tts"]) -> Any:
    """The agent's own `stt`/`tts`, else the session's (`Agent.stt` is `NOT_GIVEN` when unset)."""
    own = getattr(agent, slot, None)
    if _given(own):
        return own
    value = getattr(state.session, slot, None) if state.session is not None else None
    return value if _given(value) else None


def _stt_language_value(state: SessionLanguages, code: str) -> str | None:
    """The `language=` value this transcriber takes for `code` (`None` = leave it)."""
    if state.stt_class == OPENAI_TRANSCRIPTION_STT_CLASS:
        converted = openai_transcription_language_kwargs({"language": code})
        value = converted.get("language")
        return value if isinstance(value, str) else None
    return code


def _switch_stt(agent: Any, state: SessionLanguages, code: str) -> bool:
    if not state.switch_stt:
        return False
    stt = _running(agent, state, "stt")
    update = getattr(stt, "update_options", None)
    value = _stt_language_value(state, code)
    if not callable(update) or value is None:
        return False
    try:
        update(language=value)
    except Exception:
        logger.warning("the transcriber did not accept the new language", exc_info=True)
        return False
    return True


def _voice_for(state: SessionLanguages, code: str) -> Any:
    """The built TTS for `code`'s own voice (cached), or `None` when it has none or it fails."""
    provider = state.voice_ref(code)
    if provider is None:
        return None
    key = base_language(code)
    if key in state.built_voices:
        return state.built_voices[key]
    factory = state.factory or ProviderFactory()
    try:
        built = factory.build("tts", provider)
    except Exception:
        logger.warning("the voice for this language could not be built", language=code, exc_info=True)
        return None
    state.built_voices[key] = built
    return built


def apply_voice(agent: Any, state: SessionLanguages, code: str) -> bool:
    """Give `agent` the voice for `code` (its own, or back to the pipeline's). Returns whether it swapped."""
    current_tts = _running(agent, state, "tts")
    if current_tts is None:
        return False  # realtime or text: nothing speaks through a TTS
    update = getattr(agent, "update_options", None)
    if not callable(update):
        return False
    target = _voice_for(state, code)
    if target is None:
        if not state.has_original_tts or current_tts is state.original_tts:
            return False
        target = state.original_tts
    elif not state.has_original_tts:
        state.original_tts, state.has_original_tts = current_tts, True
    if target is current_tts:
        return False
    try:
        update(tts=target)
    except Exception:
        logger.warning("could not change the voice", language=code, exc_info=True)
        return False
    return True


async def switch_language_on(
    agent: Any,
    state: SessionLanguages,
    code: str,
    *,
    source: SwitchSource,
    record_event: Callable[[str, dict[str, Any]], None] | None = None,
) -> SwitchResult:
    """Switch the running conversation to the allowed language `code`.

    The transcriber (when it can switch and is not detecting), then the voice,
    then the state; the caller adds the reply note and updates the panel. A
    switch to the current language changes nothing.
    """
    previous = state.current
    if code == previous:
        return SwitchResult(previous, code, source, changed=False)
    stt_switched = _switch_stt(agent, state, code)
    voice_switched = apply_voice(agent, state, code)
    state.current = code
    state.streak_language, state.streak = None, 0
    result = SwitchResult(previous, code, source, stt_switched=stt_switched, voice_switched=voice_switched)
    if record_event is not None:
        with contextlib.suppress(Exception):
            record_event(LANGUAGE_EVENT, result.event_payload())
    logger.info(
        "language switched",
        from_language=previous,
        to_language=code,
        source=source,
        stt_switched=stt_switched,
        voice_switched=voice_switched,
    )
    return result
