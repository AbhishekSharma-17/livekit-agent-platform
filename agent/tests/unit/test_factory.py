"""`ProviderFactory` builds every MVP provider offline, with secrets as kwargs."""

from __future__ import annotations

import importlib
import logging
import os
import sys
import types
from typing import Any

import pytest
from lkap_contracts import providers as registry
from lkap_contracts.agent_config import ProviderSlot, ResolvedProvider
from lkap_contracts.providers import ProviderSpec

from lkap_agent.providers.factory import (
    CONSTRUCTIBLE_KINDS,
    SLOT_KINDS,
    BuiltProviders,
    ProviderBuildError,
    ProviderFactory,
)

#: The registry ships 24 MVP entries but only these kinds have a class the agent
#: constructs: `embedding` providers live in `lkap_api.kb.embed` and the
#: `http-tool-secret` bag has no `python_class` at all.
CONSTRUCTIBLE_MVP = [
    spec for spec in registry.mvp_providers() if spec.kind in CONSTRUCTIBLE_KINDS and spec.python_class
]

#: One slot per constructible kind, so `_spec_for` accepts the entry.
_SLOT_FOR_KIND: dict[str, ProviderSlot] = {
    "realtime": "realtime",
    "stt": "stt",
    "llm": "llm",
    "tts": "tts",
    "avatar": "avatar",
    "image_gen": "image_gen",
}

SECRET_VALUE = "sk-SECRET123-do-not-log"


class _Recorder:
    """Stands in for a plugin class, capturing the kwargs it was built with."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


def _resolved_for(spec: ProviderSpec) -> ResolvedProvider:
    """A `ResolvedProvider` shaped the way the api would send it."""
    kwargs: dict[str, object] = {}
    for field in spec.secret_fields:
        kwargs[field.name] = SECRET_VALUE
    for field in spec.fields:
        if field.default is not None:
            kwargs[field.name] = field.default
        elif field.required:
            kwargs[field.name] = f"test-{field.name}"  # e.g. simli's required `simli_config.face_id`
    return ResolvedProvider(
        provider_id=spec.id,
        python_class=spec.python_class,
        model=spec.default_model,
        kwargs=kwargs,
    )


def _patch_constructor(monkeypatch: pytest.MonkeyPatch, python_class: str) -> None:
    """Replace a registry `python_class` with `_Recorder`.

    Handles both a plain class (`livekit.plugins.openai.TTS`) and a
    classmethod/staticmethod path (`livekit.plugins.openai.LLM.with_openrouter`),
    where the owner is a class rather than a module.
    """
    owner_path, _, attr = python_class.rpartition(".")
    try:
        owner: Any = importlib.import_module(owner_path)
    except ImportError:
        module_path, _, class_name = owner_path.rpartition(".")
        owner = getattr(importlib.import_module(module_path), class_name)
    monkeypatch.setattr(owner, attr, _Recorder)


def _secret_value(built: _Recorder, field_name: str) -> object:
    """The value a secret field reached the constructor with (dotted names are nested)."""
    outer, _, inner = field_name.partition(".")
    value = built.kwargs[outer]
    return getattr(value, inner) if inner else value


@pytest.fixture
def stub_image_gen_module() -> Any:
    """Provide `lkap_agent.providers.image_gen` if W1-AGENT-TOOLS has not landed."""
    name = "lkap_agent.providers.image_gen"
    try:
        return importlib.import_module(name)
    except ImportError:
        module = types.ModuleType(name)
        module.GoogleImageGen = _Recorder  # type: ignore[attr-defined]
        module.OpenAIImageGen = _Recorder  # type: ignore[attr-defined]
        sys.modules[name] = module
        return module


@pytest.mark.parametrize("spec", CONSTRUCTIBLE_MVP, ids=lambda s: s.id)
def test_build_constructs_every_constructible_mvp_provider_without_network(
    spec: ProviderSpec, monkeypatch: pytest.MonkeyPatch, stub_image_gen_module: Any
) -> None:
    """Every buildable MVP entry is importable and accepts its resolved kwargs.

    Constructors are monkeypatched to a recorder so no vendor SDK opens a
    socket: the contract under test is "the registry's class path resolves and
    the resolved kwargs are handed to it", not the vendor's own behaviour.
    """
    _patch_constructor(monkeypatch, spec.python_class)

    built = ProviderFactory().build(_SLOT_FOR_KIND[spec.kind], _resolved_for(spec))

    assert isinstance(built, _Recorder)
    if spec.kind == "avatar":
        assert "model" not in built.kwargs, "bey/tavus/simli AvatarSession take no model kwarg"
    else:
        assert built.kwargs.get("model") == spec.default_model


@pytest.mark.parametrize("spec", [s for s in CONSTRUCTIBLE_MVP if s.secret_fields], ids=lambda s: s.id)
def test_build_passes_secrets_as_kwargs_and_never_touches_the_environment(
    spec: ProviderSpec,
    monkeypatch: pytest.MonkeyPatch,
    stub_image_gen_module: Any,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Credentials reach the constructor as kwargs, not env vars, and are never logged.

    `ProviderFactory.build` emits a DEBUG line per provider; it must carry
    kwarg *names* only. structlog writes to stdout until `configure_logging()`
    runs, so both sinks are checked.
    """
    _patch_constructor(monkeypatch, spec.python_class)
    before = dict(os.environ)

    with caplog.at_level(logging.DEBUG):
        built = ProviderFactory().build(_SLOT_FOR_KIND[spec.kind], _resolved_for(spec))

    for field in spec.secret_fields:
        assert _secret_value(built, field.name) == SECRET_VALUE
    assert os.environ == before, "a provider must never set a process env var"
    captured = capsys.readouterr()
    assert SECRET_VALUE not in caplog.text
    assert SECRET_VALUE not in captured.out


def test_build_strips_credentials_from_livekit_inference_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inference is billed through the worker's own LiveKit key, so api_key is dropped."""
    from livekit.agents import inference

    monkeypatch.setattr(inference, "LLM", _Recorder)
    provider = ResolvedProvider(
        provider_id="livekit-inference-llm",
        python_class="livekit.agents.inference.LLM",
        model="google/gemma-4-31b-it",
        kwargs={"api_key": SECRET_VALUE, "temperature": 0.7},
    )

    built = ProviderFactory().build("llm", provider)

    assert "api_key" not in built.kwargs
    assert "temperature" not in built.kwargs
    assert built.kwargs["extra_kwargs"] == {"temperature": 0.7}


def test_build_constructs_the_real_inference_llm_with_a_temperature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression (Stage 1): `inference.LLM.__init__` has no `temperature` kwarg.

    The catalog's `temperature` field must reach the SDK through `extra_kwargs`;
    a recorder class that accepts any kwarg cannot catch this, so build the real one.
    """
    from livekit.agents import inference

    monkeypatch.setenv("LIVEKIT_API_KEY", "test-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "test-secret")
    provider = ResolvedProvider(
        provider_id="livekit-inference-llm",
        python_class="livekit.agents.inference.LLM",
        model="google/gemma-4-31b-it",
        kwargs={"temperature": 0.3},
    )

    built = ProviderFactory().build("llm", provider)

    assert isinstance(built, inference.LLM)
    assert built.model == "google/gemma-4-31b-it"


def test_build_converts_gemini_live_tool_options_to_sdk_enums(monkeypatch: pytest.MonkeyPatch) -> None:
    """`tool_behavior`/`tool_response_scheduling` become `google.genai.types` enums.

    Silent tool replies only work when the model is constructed with
    `Behavior.NON_BLOCKING` (docs/ARCHITECTURE.md §6), and the plugin is typed
    against the enums, not the strings the registry stores.
    """
    from google.genai import types
    from livekit.plugins.google import realtime

    monkeypatch.setattr(realtime, "RealtimeModel", _Recorder)
    provider = ResolvedProvider(
        provider_id="google-realtime",
        python_class="livekit.plugins.google.realtime.RealtimeModel",
        model="gemini-3.8-live",
        kwargs={
            "api_key": SECRET_VALUE,
            "tool_behavior": "NON_BLOCKING",
            "tool_response_scheduling": "WHEN_IDLE",
        },
    )

    built = ProviderFactory().build("realtime", provider)

    assert built.kwargs["tool_behavior"] is types.Behavior.NON_BLOCKING
    assert built.kwargs["tool_response_scheduling"] is types.FunctionResponseScheduling.WHEN_IDLE
    assert built.kwargs["modalities"] == [types.Modality.AUDIO]


def test_build_drops_none_valued_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unset optional field must not become an explicit `None` argument."""
    from livekit.plugins import openai

    monkeypatch.setattr(openai, "LLM", _Recorder)
    provider = ResolvedProvider(
        provider_id="openai-llm",
        python_class="livekit.plugins.openai.LLM",
        model="gpt-4.1",
        kwargs={"api_key": SECRET_VALUE, "base_url": None},
    )

    built = ProviderFactory().build("llm", provider)

    assert "base_url" not in built.kwargs


@pytest.mark.parametrize("provider_id", ["fastembed-embedding", "openai-embedding", "http-tool-secret"])
def test_build_rejects_kinds_the_agent_cannot_construct(provider_id: str) -> None:
    """Embedding providers and the secret bag belong to the api, not the worker."""
    spec = registry.get(provider_id)
    provider = ResolvedProvider(provider_id=spec.id, python_class=spec.python_class, model=None, kwargs={})

    with pytest.raises(ProviderBuildError, match="cannot construct|malformed"):
        ProviderFactory().build("llm", provider)


def test_build_rejects_a_provider_of_the_wrong_kind_for_the_slot() -> None:
    """A TTS in the `stt` slot is a config bug the worker must not paper over."""
    spec = registry.get("openai-tts")
    provider = ResolvedProvider(provider_id=spec.id, python_class=spec.python_class, model=None, kwargs={})

    with pytest.raises(ProviderBuildError, match="slot 'stt' needs a 'stt' provider"):
        ProviderFactory().build("stt", provider)


def test_build_rejects_an_unknown_provider_id() -> None:
    """A provider id not in the registry cannot be built."""
    provider = ResolvedProvider(
        provider_id="not-a-provider", python_class="builtins.object", model=None, kwargs={}
    )

    with pytest.raises(ProviderBuildError, match="unknown provider id"):
        ProviderFactory().build("llm", provider)


def test_build_all_downgrades_optional_slots_and_raises_on_required_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken avatar degrades the session; a broken LLM fails the job."""
    from fakes.fake_api import resolved_config

    config = resolved_config(with_avatar=True)
    factory = ProviderFactory()

    def _build(slot: ProviderSlot, provider: ResolvedProvider, *, mode: str = "cascaded") -> Any:
        if slot == "avatar":
            raise ProviderBuildError("avatar vendor unavailable")
        return _Recorder(slot=slot)

    monkeypatch.setattr(factory, "build", _build)
    built = factory.build_all(config)

    assert built.avatar is None
    assert built.llm is not None

    def _always_fail(slot: ProviderSlot, provider: ResolvedProvider, *, mode: str = "cascaded") -> Any:
        raise ProviderBuildError("boom")

    monkeypatch.setattr(factory, "build", _always_fail)
    with pytest.raises(ProviderBuildError):
        factory.build_all(config)


def test_slot_kinds_covers_every_resolved_slot() -> None:
    """Every `ResolvedAgentConfig.resolved` key maps to a registry kind."""
    from typing import get_args

    assert set(SLOT_KINDS) == set(get_args(ProviderSlot))


def test_built_providers_as_dict_omits_empty_slots() -> None:
    """`as_dict` is a logging helper, so it must not report unfilled slots."""
    built = BuiltProviders(llm=object())

    assert list(built.as_dict()) == ["llm"]
