"""`special_cases.py`: nested/positional avatar configs, file fields, half-cascade modalities.

Most of the ~120 registry entries this exercises live in plugin packages this
project's venv does not install (only the 8 v1 MVP packages are present), so
these tests inject a fake module into `sys.modules` rather than monkeypatching
an attribute of an already-imported one (the pattern `test_factory.py`'s
`stub_image_gen_module` fixture already uses for the same reason).
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest
from lkap_contracts.agent_config import ResolvedProvider
from lkap_contracts.providers import get as get_spec

from lkap_agent.providers.factory import ProviderFactory
from lkap_agent.providers.special_cases import (
    ProviderBuildError,
    apply_pipeline_mode,
    extract_positional_arg,
    import_target,
    resolve_file_fields,
    unwrap_nested_fields,
)


class _Recorder:
    """Stands in for a plugin class, capturing the args/kwargs it was built with."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs


@pytest.fixture
def fake_plugin_module(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Register a fake module at an arbitrary dotted path, restored after the test."""

    created: list[str] = []

    def _make(dotted_module: str, **attrs: Any) -> types.ModuleType:
        module = types.ModuleType(dotted_module)
        for name, value in attrs.items():
            setattr(module, name, value)
        monkeypatch.setitem(sys.modules, dotted_module, module)
        created.append(dotted_module)
        return module

    return _make


# ---------------------------------------------------------------------------- import_target


def test_import_target_resolves_a_plain_class(fake_plugin_module: Any) -> None:
    fake_plugin_module("livekit.plugins.fakevendor", AvatarSession=_Recorder)

    target = import_target("livekit.plugins.fakevendor.AvatarSession")

    assert target is _Recorder


def test_import_target_resolves_a_classmethod_on_a_class(fake_plugin_module: Any) -> None:
    class _Model:
        @classmethod
        def with_azure(cls, **kwargs: Any) -> _Model:
            return cls()

    fake_plugin_module("livekit.plugins.fakevendor.realtime", RealtimeModel=_Model)

    target = import_target("livekit.plugins.fakevendor.realtime.RealtimeModel.with_azure")

    assert target == _Model.with_azure


def test_import_target_raises_for_an_unimportable_module() -> None:
    """No leading segment of the path is an importable module at all."""
    with pytest.raises(ProviderBuildError, match="cannot import"):
        import_target("totally_fake_namespace_xyz.does_not_exist.Thing")


def test_import_target_raises_when_a_real_module_prefix_lacks_the_attribute() -> None:
    """`livekit.plugins` imports fine, but the rest of the chain does not exist on it."""
    with pytest.raises(ProviderBuildError, match="no attribute path"):
        import_target("livekit.plugins.does_not_exist_at_all.Thing")


def test_import_target_raises_for_a_missing_attribute(fake_plugin_module: Any) -> None:
    fake_plugin_module("livekit.plugins.fakevendor2")

    with pytest.raises(ProviderBuildError, match="no attribute path"):
        import_target("livekit.plugins.fakevendor2.NotThere")


# ---------------------------------------------------------------------------- nested configs (Simli/Anam)


def test_build_constructs_simli_with_a_nested_config_object(fake_plugin_module: Any) -> None:
    class SimliConfig:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    fake_plugin_module("livekit.plugins.simli", AvatarSession=_Recorder, SimliConfig=SimliConfig)
    spec = get_spec("simli-avatar")
    provider = ResolvedProvider(
        provider_id=spec.id,
        python_class=spec.python_class,
        model=None,
        kwargs={
            "simli_config.api_key": "sk-simli",
            "simli_config.face_id": "face-1",
        },
    )

    built = ProviderFactory().build("avatar", provider)

    assert isinstance(built, _Recorder)
    assert "simli_config" in built.kwargs
    nested = built.kwargs["simli_config"]
    assert isinstance(nested, SimliConfig)
    assert nested.kwargs == {"api_key": "sk-simli", "face_id": "face-1"}
    # No top-level api_key/face_id leaked past the grouping step.
    assert "simli_config.api_key" not in built.kwargs
    assert "api_key" not in built.kwargs


def test_build_constructs_anam_with_a_nested_persona_config(fake_plugin_module: Any) -> None:
    class PersonaConfig:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    fake_plugin_module("livekit.plugins.anam", AvatarSession=_Recorder, PersonaConfig=PersonaConfig)
    spec = get_spec("anam-avatar")
    provider = ResolvedProvider(
        provider_id=spec.id,
        python_class=spec.python_class,
        model=None,
        kwargs={"api_key": "sk-anam", "persona_config.avatarId": "av-1", "persona_config.name": "Rae"},
    )

    built = ProviderFactory().build("avatar", provider)

    assert built.kwargs["api_key"] == "sk-anam"
    nested = built.kwargs["persona_config"]
    assert isinstance(nested, PersonaConfig)
    assert nested.kwargs == {"avatarId": "av-1", "name": "Rae"}


def test_unwrap_nested_fields_leaves_a_provider_with_no_nested_fields_untouched() -> None:
    spec = get_spec("bey-avatar")
    kwargs = {"api_key": "sk", "avatar_id": "a-1"}

    assert unwrap_nested_fields(spec, kwargs) == kwargs


# ---------------------------------------------------------------------------- positional configs (Synthesia)


def test_build_passes_synthesia_avatar_config_positionally(fake_plugin_module: Any) -> None:
    class AvatarConfig:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    fake_plugin_module("livekit.plugins.synthesia", AvatarSession=_Recorder, AvatarConfig=AvatarConfig)
    spec = get_spec("synthesia-avatar")
    provider = ResolvedProvider(
        provider_id=spec.id,
        python_class=spec.python_class,
        model=None,
        kwargs={"api_key": "sk-synthesia", "avatar_config": {"avatar_ids": ["a1", "a2"]}},
    )

    built = ProviderFactory().build("avatar", provider)

    assert len(built.args) == 1
    assert isinstance(built.args[0], AvatarConfig)
    assert built.args[0].kwargs == {"avatar_ids": ["a1", "a2"]}
    assert "avatar_config" not in built.kwargs
    assert built.kwargs["api_key"] == "sk-synthesia"


def test_extract_positional_arg_is_a_noop_for_specs_without_one() -> None:
    spec = get_spec("bey-avatar")
    kwargs = {"api_key": "sk", "avatar_id": "a-1"}

    positional, remaining = extract_positional_arg(spec, kwargs)

    assert positional is None
    assert remaining == kwargs


# ------------------------------------------------------------------ file fields (Google credentials)


def test_build_writes_a_google_credentials_upload_to_a_temp_path(fake_plugin_module: Any) -> None:
    import base64

    fake_plugin_module("livekit.plugins.google", STT=_Recorder)
    spec = get_spec("google-stt")
    upload = {"name": "sa.json", "content_base64": base64.b64encode(b'{"type": "service_account"}').decode()}
    provider = ResolvedProvider(
        provider_id=spec.id, python_class=spec.python_class, model=None, kwargs={"credentials_file": upload}
    )

    built = ProviderFactory().build("stt", provider)

    path = built.kwargs["credentials_file"]
    assert isinstance(path, str)
    assert path.endswith(".json")
    with open(path, "rb") as f:
        assert f.read() == b'{"type": "service_account"}'


def test_resolve_file_fields_passes_through_a_bare_string_unmodified() -> None:
    spec = get_spec("google-stt")
    kwargs = {"credentials_file": "/already/a/path.json"}

    assert resolve_file_fields(spec, kwargs) == kwargs


def test_resolve_file_fields_is_a_noop_for_a_provider_with_no_file_field() -> None:
    spec = get_spec("bey-avatar")
    kwargs = {"api_key": "sk"}

    assert resolve_file_fields(spec, kwargs) == kwargs


# ---------------------------------------------------------------------------- half-cascade modalities


def test_apply_pipeline_mode_is_a_noop_outside_half_cascade() -> None:
    spec = get_spec("openai-realtime")
    kwargs = {"voice": "marin"}

    for mode in ("cascaded", "realtime"):
        assert apply_pipeline_mode(spec, kwargs, mode) == kwargs  # type: ignore[arg-type]


def test_apply_pipeline_mode_sets_text_modalities_for_openai_realtime() -> None:
    spec = get_spec("openai-realtime")

    result = apply_pipeline_mode(spec, {"voice": "marin"}, "half_cascade")

    assert result["modalities"] == ["text"]


def test_apply_pipeline_mode_sets_output_medium_for_ultravox() -> None:
    spec = get_spec("ultravox-realtime")

    result = apply_pipeline_mode(spec, {"voice": "Mark"}, "half_cascade")

    assert result["output_medium"] == "text"


def test_apply_pipeline_mode_rejects_half_cascade_for_a_provider_without_text_modality() -> None:
    spec = get_spec("xai-realtime")

    with pytest.raises(ProviderBuildError, match="text_modality"):
        apply_pipeline_mode(spec, {}, "half_cascade")


def test_build_gemini_live_selects_text_modality_in_half_cascade(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: half_cascade must flip Gemini's own AUDIO-only special case."""
    from google.genai import types
    from livekit.plugins.google import realtime

    monkeypatch.setattr(realtime, "RealtimeModel", _Recorder)
    spec = get_spec("google-realtime")
    provider = ResolvedProvider(
        provider_id=spec.id,
        python_class=spec.python_class,
        model="gemini-3.8-live",
        kwargs={"api_key": "sk-google"},
    )

    built = ProviderFactory().build("realtime", provider, mode="half_cascade")

    assert built.kwargs["modalities"] == [types.Modality.TEXT]


def test_build_gemini_live_defaults_to_audio_modality_outside_half_cascade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from google.genai import types
    from livekit.plugins.google import realtime

    monkeypatch.setattr(realtime, "RealtimeModel", _Recorder)
    spec = get_spec("google-realtime")
    provider = ResolvedProvider(
        provider_id=spec.id,
        python_class=spec.python_class,
        model="gemini-3.8-live",
        kwargs={"api_key": "sk-google"},
    )

    built = ProviderFactory().build("realtime", provider)

    assert built.kwargs["modalities"] == [types.Modality.AUDIO]
