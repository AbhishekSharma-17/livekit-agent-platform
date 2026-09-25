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

from lkap_agent.providers.factory import ProviderFactory, telephony_noise_cancellation, turn_detector_kwargs
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


def test_build_simli_from_the_api_resolved_shape_yields_a_real_simli_config(fake_plugin_module: Any) -> None:
    """Asks #26 / B-1: the api's `_assign_nested` pre-groups dotted fields into one plain key.

    `resolve_provider_ref` turns `simli_config.face_id` (a field) and
    `simli_config.api_key` (the credential's secret) into
    `{"simli_config": {"face_id": …, "api_key": …}}`; `session_builder` adds
    `avatar_participant_name`. The installed plugin's real `SimliConfig` must come
    out, because `AvatarSession.start()` calls `simli_config.create_json()`.
    """
    from livekit.plugins.simli import SimliConfig  # the real dataclass (agent/.venv, 1.8.2)

    fake_plugin_module("livekit.plugins.simli", AvatarSession=_Recorder, SimliConfig=SimliConfig)
    spec = get_spec("simli-avatar")
    provider = ResolvedProvider(
        provider_id=spec.id,
        python_class=spec.python_class,
        model=None,
        kwargs={
            "simli_config": {"face_id": "cace3ef7-face", "api_key": "sk-simli"},
            "avatar_participant_name": "Demo avatar",
        },
    )

    built = ProviderFactory().build("avatar", provider)

    config = built.kwargs["simli_config"]
    assert isinstance(config, SimliConfig)
    assert config.api_key == "sk-simli"
    assert config.face_id == "cace3ef7-face"
    assert config.create_json()["faceId"].startswith("cace3ef7-face/")
    assert "None" not in config.create_json()["faceId"]
    assert built.kwargs["avatar_participant_name"] == "Demo avatar"
    assert set(built.kwargs) == {"simli_config", "avatar_participant_name"}


def test_build_anam_from_the_api_resolved_shape_yields_a_persona_config(fake_plugin_module: Any) -> None:
    """The Anam analogue of B-1: flat `api_key` secret plus a pre-grouped `persona_config`.

    `PersonaConfig` is not installed in this venv; the stand-in mirrors the
    livekit-plugins-anam 1.8.2 dataclass (`name`, `avatarId` required;
    `avatarModel`, `directorNotes` optional).
    """
    from dataclasses import dataclass

    @dataclass
    class PersonaConfig:
        name: str
        avatarId: str  # noqa: N815 - the plugin's own field name
        avatarModel: str | None = None  # noqa: N815

    fake_plugin_module("livekit.plugins.anam", AvatarSession=_Recorder, PersonaConfig=PersonaConfig)
    spec = get_spec("anam-avatar")
    provider = ResolvedProvider(
        provider_id=spec.id,
        python_class=spec.python_class,
        model=None,
        kwargs={
            "api_key": "sk-anam",
            "persona_config": {"avatarId": "av-1", "name": "Rae"},
            "avatar_participant_name": "Demo avatar",
        },
    )

    built = ProviderFactory().build("avatar", provider)

    assert built.kwargs["api_key"] == "sk-anam"
    assert built.kwargs["persona_config"] == PersonaConfig(name="Rae", avatarId="av-1")


def test_unwrap_nested_fields_merges_dotted_keys_over_a_pre_grouped_dict_and_drops_none(
    fake_plugin_module: Any,
) -> None:
    class SimliConfig:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    fake_plugin_module("livekit.plugins.simli", AvatarSession=_Recorder, SimliConfig=SimliConfig)

    result = unwrap_nested_fields(
        get_spec("simli-avatar"),
        {
            "simli_config": {"face_id": "old", "api_key": "sk", "emotion_id": None},
            "simli_config.face_id": "new",
        },
    )

    assert set(result) == {"simli_config"}
    assert result["simli_config"].kwargs == {"face_id": "new", "api_key": "sk"}


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


# ------------------------------------------------ V5-07: noise-cancellation variants, detector kwargs


def _nc_slot(provider_id: str, **kwargs: Any) -> ResolvedProvider:
    spec = get_spec(provider_id)
    return ResolvedProvider(provider_id=spec.id, python_class=spec.python_class, model=None, kwargs=kwargs)


def test_telephony_noise_cancellation_swaps_livekit_cloud_bvc_for_bvc_telephony() -> None:
    variant = telephony_noise_cancellation(_nc_slot("legacy-noise-cancellation"))

    assert variant is not None
    assert variant.python_class == "livekit.plugins.noise_cancellation.BVCTelephony"
    assert variant.provider_id == "legacy-noise-cancellation"
    assert variant.kwargs == {}


def test_telephony_noise_cancellation_drops_the_krisp_mode_the_variant_fixes_itself() -> None:
    slot = _nc_slot("krisp-noise-cancellation", mode="noise_cancellation", noise_suppression_level=60)

    variant = telephony_noise_cancellation(slot)

    assert variant is not None
    assert variant.python_class == "livekit.plugins.krisp.voice_isolation_telephony"
    assert variant.kwargs == {"noise_suppression_level": 60}
    assert slot.kwargs == {"mode": "noise_cancellation", "noise_suppression_level": 60}


def test_telephony_noise_cancellation_is_idempotent() -> None:
    once = telephony_noise_cancellation(_nc_slot("legacy-noise-cancellation"))
    assert once is not None

    assert telephony_noise_cancellation(once) == once


@pytest.mark.parametrize("provider_id", ["ai-coustics-noise-cancellation", "no-such-provider", "silero-vad"])
def test_telephony_noise_cancellation_returns_none_without_a_variant(provider_id: str) -> None:
    provider = ResolvedProvider(provider_id=provider_id, python_class="x.Y", model=None, kwargs={})

    assert telephony_noise_cancellation(provider) is None


def test_the_factory_builds_the_telephony_variant_it_is_given(fake_plugin_module: Any) -> None:
    fake_plugin_module(
        "livekit.plugins.noise_cancellation",
        BVC=lambda: "bvc-options",
        BVCTelephony=lambda: "bvc-telephony-options",
    )
    default = _nc_slot("legacy-noise-cancellation")
    variant = telephony_noise_cancellation(default)
    assert variant is not None

    assert ProviderFactory().build("noise_cancellation", default) == "bvc-options"
    assert ProviderFactory().build("noise_cancellation", variant) == "bvc-telephony-options"


@pytest.mark.parametrize(
    ("mode", "threshold", "connection", "expected"),
    [
        (None, None, "hosted", {}),
        ("hosted", None, "hosted", {}),
        ("hosted", 0.2, "hosted", {"unlikely_threshold": 0.2}),
        ("local", None, "hosted", {"version": "v1-mini"}),
        ("hosted", None, "local", {"version": "v1-mini"}),
        (None, 0.0, "local", {"version": "v1-mini", "unlikely_threshold": 0.0}),
    ],
)
def test_turn_detector_kwargs(
    mode: str | None, threshold: float | None, connection: Any, expected: dict[str, Any]
) -> None:
    assert (
        turn_detector_kwargs(mode=mode, unlikely_threshold=threshold, connection_mode=connection) == expected
    )
