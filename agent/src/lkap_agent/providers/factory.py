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
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from dataclasses import fields as dataclass_fields
from typing import Any, Final

from lkap_contracts.agent_config import ProviderSlot, ResolvedAgentConfig, ResolvedProvider
from lkap_contracts.providers import ProviderKind, ProviderSpec
from lkap_contracts.providers import get as get_spec

from lkap_agent.logging import get_logger

__all__ = [
    "CONSTRUCTIBLE_KINDS",
    "BuiltProviders",
    "ProviderBuildError",
    "ProviderFactory",
    "SLOT_KINDS",
]

logger = get_logger(__name__)

#: Registry kinds the agent-side factory can construct. `embedding` providers are
#: built by `lkap_api.kb.embed` and `secret_bag` has no class at all.
CONSTRUCTIBLE_KINDS: Final[frozenset[ProviderKind]] = frozenset(
    {"realtime", "stt", "llm", "tts", "avatar", "image_gen"}
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
    "vad": "vad",
    "turn_detection": "turn_detection",
    "noise_cancellation": "noise_cancellation",
}

#: Providers whose `model` is not a constructor kwarg (avatar sessions take an
#: `avatar_id`/`face_id` instead — verified against livekit-plugins-bey/tavus 1.8.2).
_NO_MODEL_KINDS: Final[frozenset[ProviderKind]] = frozenset({"avatar"})

_INFERENCE_PREFIX: Final[str] = "livekit-inference-"

#: Secret kwargs an Inference provider must never receive (it authenticates with
#: the worker's own LiveKit credentials from env).
_INFERENCE_FORBIDDEN_KWARGS: Final[tuple[str, ...]] = ("api_key", "api_secret")


class ProviderBuildError(RuntimeError):
    """A provider could not be constructed from its resolved configuration."""


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

    def as_dict(self) -> dict[str, Any]:
        """Return the non-empty slots, for logging and assertions."""
        return {
            f.name: getattr(self, f.name) for f in dataclass_fields(self) if getattr(self, f.name) is not None
        }


def _import_target(python_class: str) -> Any:
    """Import the dotted `module.attr` path named by a registry entry.

    Args:
        python_class: e.g. ``"livekit.plugins.google.realtime.RealtimeModel"``.

    Returns:
        The imported class (or any callable target).

    Raises:
        ProviderBuildError: If the module or attribute cannot be imported.
    """
    module_path, _, attr = python_class.rpartition(".")
    if not module_path or not attr:
        raise ProviderBuildError(f"malformed python_class: {python_class!r}")
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise ProviderBuildError(f"cannot import {module_path!r} for {python_class!r}: {exc}") from exc
    try:
        return getattr(module, attr)
    except AttributeError as exc:
        raise ProviderBuildError(f"{module_path!r} has no attribute {attr!r}") from exc


def _google_realtime_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Convert Gemini Live string options into `google.genai.types` enums.

    `tool_behavior` and `tool_response_scheduling` arrive from the registry as
    plain strings but the plugin's constructor is typed against the SDK enums.
    `modalities` is pinned to audio for the MVP (docs/ARCHITECTURE.md §6).
    """
    from google.genai import types  # noqa: PLC0415  (heavy, vendor-specific import)

    converted = dict(kwargs)
    behavior = converted.get("tool_behavior")
    if isinstance(behavior, str):
        converted["tool_behavior"] = types.Behavior(behavior)
    scheduling = converted.get("tool_response_scheduling")
    if isinstance(scheduling, str):
        converted["tool_response_scheduling"] = types.FunctionResponseScheduling(scheduling)
    converted["modalities"] = [types.Modality("AUDIO")]
    return converted


class ProviderFactory:
    """Builds plugin instances from resolved provider entries."""

    def build(self, slot: ProviderSlot, provider: ResolvedProvider) -> Any:
        """Construct the plugin object for one slot.

        Args:
            slot: The `ResolvedAgentConfig.resolved` key being filled.
            provider: Class path, model and complete constructor kwargs.

        Returns:
            The constructed provider object.

        Raises:
            ProviderBuildError: On an unknown/incompatible provider id, a kind the
                agent cannot construct, an unimportable class, or a constructor
                that rejects the resolved kwargs.
        """
        spec = self._spec_for(slot, provider)
        target = _import_target(provider.python_class)
        kwargs = self._constructor_kwargs(spec, provider)
        logger.debug(
            "building provider",
            slot=slot,
            provider_id=provider.provider_id,
            python_class=provider.python_class,
            kwarg_names=sorted(kwargs),  # names only: values may be secrets
        )
        try:
            return target(**kwargs)
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
                instead of raising. Defaults to `{"avatar", "image_gen", "workflow_llm"}`
                — the session can still run without any of them.

        Returns:
            A `BuiltProviders` with one attribute per filled slot.

        Raises:
            ProviderBuildError: If a non-optional slot fails to build.
        """
        lenient = optional if optional is not None else frozenset({"avatar", "image_gen", "workflow_llm"})
        built: dict[str, Any] = {}
        for slot, provider in resolved.resolved.items():
            try:
                built[slot] = self.build(slot, provider)
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

    def _constructor_kwargs(self, spec: ProviderSpec, provider: ResolvedProvider) -> dict[str, Any]:
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

        if spec.id == "google-realtime":
            kwargs = _google_realtime_kwargs(kwargs)
        if spec.id == "livekit-inference-llm" and "temperature" in kwargs:
            # `inference.LLM` takes sampling options only via `extra_kwargs`
            # (ChatCompletionOptions); the catalog exposes `temperature` flat.
            extra = dict(kwargs.pop("extra_kwargs", None) or {})
            extra.setdefault("temperature", kwargs.pop("temperature"))
            kwargs["extra_kwargs"] = extra
        return kwargs
