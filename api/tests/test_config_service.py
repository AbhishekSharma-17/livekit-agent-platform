"""`config_service.validate_agent_config` — the cascaded vision warning (DECISIONS-W2 D-W2-10 step 2)."""

from __future__ import annotations

from conftest import inference_config

from lkap_api.config_service import validate_agent_config


def test_camera_with_a_text_only_cascaded_llm_warns() -> None:
    config = inference_config()
    config.pipeline.llm.model = "google/gemma-4-31b-it"
    config.capabilities.camera = True

    result = validate_agent_config(config, credential_providers={})

    assert result.ok is True
    assert any(
        "cannot see images" in warning and "google/gemma-4-31b-it" in warning for warning in result.warnings
    )


def test_camera_with_a_vision_capable_cascaded_llm_does_not_warn() -> None:
    config = inference_config()
    config.pipeline.llm.model = "google/gemini-3.5-flash"
    config.capabilities.camera = True

    result = validate_agent_config(config, credential_providers={})

    assert result.ok is True
    assert not any("cannot see images" in warning for warning in result.warnings)


def test_camera_with_an_unlisted_free_text_model_does_not_warn() -> None:
    config = inference_config()
    config.pipeline.llm.model = "some-vendor/unlisted-model"
    config.capabilities.camera = True

    result = validate_agent_config(config, credential_providers={})

    assert result.ok is True
    assert not any("cannot see images" in warning for warning in result.warnings)


def test_text_only_cascaded_llm_without_camera_or_screen_share_does_not_warn() -> None:
    config = inference_config()
    config.pipeline.llm.model = "google/gemma-4-31b-it"

    result = validate_agent_config(config, credential_providers={})

    assert not any("cannot see images" in warning for warning in result.warnings)


def test_screen_share_with_a_text_only_cascaded_llm_also_warns() -> None:
    config = inference_config()
    config.pipeline.llm.model = "google/gemma-4-31b-it"
    config.capabilities.screen_share = True

    result = validate_agent_config(config, credential_providers={})

    assert any("cannot see images" in warning for warning in result.warnings)
