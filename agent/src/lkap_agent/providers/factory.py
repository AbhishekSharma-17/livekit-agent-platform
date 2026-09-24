"""Construct plugin objects from a `ResolvedAgentConfig` (docs/ARCHITECTURE.md §6).

The factory is the only place in the worker that turns registry data into live
objects. Two invariants matter:

1. **Credentials are constructor kwargs, never process env vars.** Two jobs with
   different vendor keys run in the same worker process; setting `os.environ`
   would leak one job's key into the other.
2. **Plugin modules are imported lazily**, by the registry's `python_class`, so
   an offline test can build one provider without importing every vendor SDK.

LiveKit Inference classes (`livekit-inference-*`) are billed through the
worker's own `LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET` env vars, so they are the
one family that must *not* receive an `api_key` kwarg.

Constructor shapes the registry's generic field list cannot express on its
own (classmethod paths, nested avatar configs, positional args, uploaded
files, half-cascade modalities) are handled by `special_cases.py`, not here —
see that module's docstring for the full list and the one shape it
deliberately punts on (`aws-transcribe-stt`'s nested credentials object).

**Voice-activity, turn and noise slots (V2-07)**: `vad`, `turn_detection` and
`noise_cancellation` are constructible like any other slot (ask #33). None of
them takes a `model` kwarg (Silero's `VAD.load` would reject one, and the
registry's `default_model="silero"` is informational), and all three are
lenient by default: a failed build falls back to the session's defaults
(prewarmed Silero, the Inference turn detector, no noise filter) instead of
failing the call.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from dataclasses import fields as dataclass_fields
from typing import Any, Final

from lkap_contracts.agent_config import PipelineMode, ProviderSlot, ResolvedAgentConfig, ResolvedProvider
from lkap_contracts.providers import ProviderKind, ProviderSpec
from lkap_contracts.providers import get as get_spec

from lkap_agent.logging import get_logger
from lkap_agent.providers.special_cases import (
    ProviderBuildError,
    apply_pipeline_mode,
    extract_positional_arg,
    import_target,
    resolve_file_fields,
    unwrap_nested_fields,
)

__all__ = [
    "CONSTRUCTIBLE_KINDS",
    "DEFAULT_OPTIONAL_SLOTS",
    "BuiltProviders",
    "ProviderBuildError",
    "ProviderFactory",
    "SLOT_KINDS",
]

logger = get_logger(__name__)

#: Registry kinds the agent-side factory can construct. `embedding` providers are
#: built by `lkap_api.kb.embed` and `secret_bag` has no class at all.
CONSTRUCTIBLE_KINDS: Final[frozenset[ProviderKind]] = frozenset(
    {"realtime", "stt", "llm", "tts", "avatar", "image_gen", "vad", "turn_detection", "noise_cancellation"}
)

#: Which registry kind each `ResolvedAgentConfig.resolved` slot must hold.
SLOT_KINDS: Final[dict[ProviderSlot, ProviderKind]] = {
    "realtime": "realtime",
    "stt": "stt",
    "llm": "llm",
    "tts": "tts",
    "avatar": "avatar",
    "image_gen": "image_gen",
    "workflow_llm": "llm",
    "qa_llm": "llm",  # R-V2-6 (kind mapping only, same pattern as ask #1); `qa.build_judge` builds it.
    "vad": "vad",
    "turn_detection": "turn_detection",
    "noise_cancellation": "noise_cancellation",
}

#: Providers whose `model` is not a constructor kwarg: avatar sessions take an
#: `avatar_id`/`face_id` instead (verified against livekit-plugins-bey/tavus
#: 1.8.2); `silero.VAD.load`, `inference.eot.TurnDetector` and the noise filters
#: have no `model` parameter (`inference.VAD` does, but only accepts its default).
_NO_MODEL_KINDS: Final[frozenset[ProviderKind]] = frozenset(
    {"avatar", "vad", "turn_detection", "noise_cancellation"}
)

#: Slots whose build failure downgrades to `None` unless the caller says otherwise.
DEFAULT_OPTIONAL_SLOTS: Final[frozenset[str]] = frozenset(
    {"avatar", "image_gen", "workflow_llm", "vad", "turn_detection", "noise_cancellation"}
)

_INFERENCE_PREFIX: Final[str] = "livekit-inference-"

#: Secret kwargs an Inference provider must never receive (it authenticates with
#: the worker's own LiveKit credentials from env).
_INFERENCE_FORBIDDEN_KWARGS: Final[tuple[str, ...]] = ("api_key", "api_secret")


@dataclass(slots=True)
class BuiltProviders:
    """The constructed objects for one session, one field per slot.

    Fields are deliberately `Any`: the concrete classes live in optional plugin
    packages, and `SessionBuilder` only needs them to be duck-typed `llm.LLM` /
    `stt.STT` / `tts.TTS` / `llm.RealtimeModel` / `AvatarSession`.
    """

    realtime: Any = None
    stt: Any = None
    llm: Any = None
    tts: Any = None
    avatar: Any = None
    image_gen: Any = None
    workflow_llm: Any = None
    vad: Any = None
    turn_detection: Any = None
    noise_cancellation: Any = None

    def as_dict(self) -> dict[str, Any]:
        """Return the non-empty slots, for logging and assertions."""
        return {
            f.name: getattr(self, f.name) for f in dataclass_fields(self) if getattr(self, f.name) is not None
        }


def _google_realtime_kwargs(kwargs: dict[str, Any], mode: PipelineMode) -> dict[str, Any]:
    """Convert Gemini Live string options into `google.genai.types` enums.

    `tool_behavior` and `tool_response_scheduling` arrive from the registry as
    plain strings but the plugin's constructor is typed against the SDK enums.
    `modalities` is audio-native for `cascaded`/`realtime` mode
    (docs/ARCHITECTURE.md §6) and text-only for `half_cascade`, where a
    separate TTS plugin speaks instead (CONTRACTS-V2 §4.3).
    """
    from google.genai import types  # noqa: PLC0415  (heavy, vendor-specific import)

    converted = dict(kwargs)
    behavior = converted.get("tool_behavior")
    if isinstance(behavior, str):
        converted["tool_behavior"] = types.Behavior(behavior)
    scheduling = converted.get("tool_response_scheduling")
    if isinstance(scheduling, str):
        converted["tool_response_scheduling"] = types.FunctionResponseScheduling(scheduling)
    converted["modalities"] = [types.Modality("TEXT" if mode == "half_cascade" else "AUDIO")]
    return converted


class ProviderFactory:
    """Builds plugin instances from resolved provider entries."""

    def build(
        self, slot: ProviderSlot, provider: ResolvedProvider, *, mode: PipelineMode = "cascaded"
    ) -> Any:
        """Construct the plugin object for one slot.

        Args:
            slot: The `ResolvedAgentConfig.resolved` key being filled.
            provider: Class path, model and complete constructor kwargs.
            mode: The agent's pipeline mode; only affects `kind="realtime"`
                providers (`special_cases.apply_pipeline_mode`), selecting an
                audio-native vs. text-only (half-cascade) construction.

        Returns:
            The constructed provider object.

        Raises:
            ProviderBuildError: On an unknown/incompatible provider id, a kind the
                agent cannot construct, an unimportable class, a `half_cascade`
                request the provider has no text-only path for, or a
                constructor that rejects the resolved kwargs.
        """
        spec = self._spec_for(slot, provider)
        target = import_target(provider.python_class)
        kwargs = self._constructor_kwargs(spec, provider, mode)
        positional, kwargs = extract_positional_arg(spec, kwargs)
        args = (positional,) if positional is not None else ()
        logger.debug(
            "building provider",
            slot=slot,
            provider_id=provider.provider_id,
            python_class=provider.python_class,
            kwarg_names=sorted(kwargs),  # names only: values may be secrets
            positional=positional is not None,
        )
        try:
            return target(*args, **kwargs)
        except ProviderBuildError:
            raise
        except Exception as exc:
            raise ProviderBuildError(
                f"{provider.provider_id} ({provider.python_class}) rejected its configuration: {exc}"
            ) from exc

    def build_all(
        self, resolved: ResolvedAgentConfig, *, optional: frozenset[str] | None = None
    ) -> BuiltProviders:
        """Construct every slot present in `resolved.resolved`.

        Args:
            resolved: The config fetched from `/internal/v1/sessions/{id}/resolved`.
            optional: Slots whose build failure is logged and downgraded to `None`
                instead of raising. Defaults to :data:`DEFAULT_OPTIONAL_SLOTS` —
                the session can still run without any of them.

        Returns:
            A `BuiltProviders` with one attribute per filled slot.

        Raises:
            ProviderBuildError: If a non-optional slot fails to build.
        """
        lenient = optional if optional is not None else DEFAULT_OPTIONAL_SLOTS
        mode = resolved.config.pipeline.mode
        built: dict[str, Any] = {}
        for slot, provider in resolved.resolved.items():
            try:
                built[slot] = self.build(slot, provider, mode=mode)
            except ProviderBuildError:
                if slot not in lenient:
                    raise
                logger.warning(
                    "optional provider unavailable, continuing without it",
                    slot=slot,
                    provider_id=provider.provider_id,
                    exc_info=True,
                )
                built[slot] = None
        return BuiltProviders(**built)

    def _spec_for(self, slot: ProviderSlot, provider: ResolvedProvider) -> ProviderSpec:
        try:
            spec = get_spec(provider.provider_id)
        except KeyError as exc:
            raise ProviderBuildError(str(exc)) from exc
        if spec.kind not in CONSTRUCTIBLE_KINDS:
            raise ProviderBuildError(
                f"{provider.provider_id} has kind {spec.kind!r}, which the agent cannot construct"
            )
        expected = SLOT_KINDS.get(slot)
        if expected is not None and spec.kind != expected:
            raise ProviderBuildError(
                f"slot {slot!r} needs a {expected!r} provider but got {provider.provider_id!r} "
                f"of kind {spec.kind!r}"
            )
        return spec

    def _constructor_kwargs(
        self, spec: ProviderSpec, provider: ResolvedProvider, mode: PipelineMode
    ) -> dict[str, Any]:
        """Merge resolved kwargs with the model, dropping what the class cannot take."""
        kwargs: dict[str, Any] = {k: v for k, v in provider.kwargs.items() if v is not None}

        if spec.id.startswith(_INFERENCE_PREFIX):
            for forbidden in _INFERENCE_FORBIDDEN_KWARGS:
                if kwargs.pop(forbidden, None) is not None:
                    logger.warning(
                        "dropping credential kwarg from a LiveKit Inference provider",
                        provider_id=spec.id,
                        kwarg=forbidden,
                    )

        if spec.kind not in _NO_MODEL_KINDS:
            model = provider.model or spec.default_model
            if model is not None:
                kwargs["model"] = model

        # Special cases (special_cases.py): nested avatar configs, uploaded
        # files, then the per-engine half-cascade modality switch. Order
        # matters — nested grouping must run before the positional-arg split
        # (`build()`) can find a bare `avatar_config` key to pop.
        kwargs = unwrap_nested_fields(spec, kwargs)
        kwargs = resolve_file_fields(spec, kwargs)
        if spec.kind == "realtime":
            kwargs = apply_pipeline_mode(spec, kwargs, mode)

        if spec.id == "google-realtime":
            kwargs = _google_realtime_kwargs(kwargs, mode)
        if spec.id == "livekit-inference-llm" and "temperature" in kwargs:
            # `inference.LLM` takes sampling options only via `extra_kwargs`
            # (ChatCompletionOptions); the catalog exposes `temperature` flat.
            extra = dict(kwargs.pop("extra_kwargs", None) or {})
            extra.setdefault("temperature", kwargs.pop("temperature"))
            kwargs["extra_kwargs"] = extra
        if spec.id == "openrouter-llm":
            kwargs = _openrouter_llm_kwargs(kwargs)
        return kwargs


def _json_field(kwargs: dict[str, Any], name: str, expected: type) -> Any:
    """Decode a registry ``type="json"`` field, which reaches the worker as a string.

    `ProviderRef.fields` holds scalars only, so the console's JSON textarea
    arrives as text. An empty string means "unset".
    """
    value = kwargs.get(name)
    if isinstance(value, str):
        if not value.strip():
            return None
        try:
            value = json.loads(value)
        except ValueError as exc:
            raise ProviderBuildError(f"openrouter-llm field {name!r} is not valid JSON") from exc
    if value is not None and not isinstance(value, expected):
        raise ProviderBuildError(f"openrouter-llm field {name!r} must be a JSON {expected.__name__}")
    return value


def _openrouter_llm_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """`LLM.with_openrouter` options (docs/v4/OPENROUTER.md D-V4-12, R-V4-8).

    Decodes the two JSON fields and defaults ``provider.require_parameters``
    to true, so a request carrying tools is never routed to an endpoint that
    ignores them (the agent would "answer" instead of calling the tool). An
    admin who wants cheaper routing sets ``{"require_parameters": false}``.
    """
    converted = dict(kwargs)
    provider = _json_field(converted, "provider", dict)
    preferences: dict[str, Any] = dict(provider or {})
    preferences.setdefault("require_parameters", True)
    converted["provider"] = preferences
    fallback_models = _json_field(converted, "fallback_models", list)
    if fallback_models:
        converted["fallback_models"] = [str(model) for model in fallback_models]
    else:
        converted.pop("fallback_models", None)
    if not converted.get("site_url"):
        converted.pop("site_url", None)
    return converted
