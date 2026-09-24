"""`config_service.validate_agent_config` — the cascaded vision warning (DECISIONS-W2 D-W2-10 step 2)
and the knowledge auto-inject / preemptive generation warning (research-v4 knowledge-and-memory P0-0)."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import inference_config
from lkap_contracts.agent_config import KnowledgeConfig

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


def _knowledge_issues(result: Any) -> list[Any]:
    return [issue for issue in result.issues if issue.path == "knowledge.auto_inject"]


def test_validate_auto_inject_with_a_knowledge_base_warns_about_preemptive_generation() -> None:
    config = inference_config(knowledge=KnowledgeConfig(kb_ids=["kb-1"], auto_inject=True))

    result = validate_agent_config(config, credential_providers={})

    assert result.ok is True
    (issue,) = _knowledge_issues(result)
    assert issue.severity == "warning"
    assert "turns off preemptive generation" in issue.message
    assert "search_knowledge" in issue.message
    assert any(w.startswith("knowledge.auto_inject: ") for w in result.warnings)
    assert not any("knowledge.auto_inject" in e for e in result.errors)


@pytest.mark.parametrize(
    "knowledge",
    [
        KnowledgeConfig(kb_ids=["kb-1"], auto_inject=False),
        KnowledgeConfig(kb_ids=[], auto_inject=True),
    ],
)
def test_validate_auto_inject_inactive_does_not_warn(knowledge: KnowledgeConfig) -> None:
    result = validate_agent_config(inference_config(knowledge=knowledge), credential_providers={})

    assert _knowledge_issues(result) == []


def test_validate_auto_inject_with_preemptive_explicitly_off_does_not_warn() -> None:
    config = inference_config(knowledge=KnowledgeConfig(kb_ids=["kb-1"], auto_inject=True))
    config.pipeline.turn_handling = {"preemptive_generation": {"enabled": False}}

    result = validate_agent_config(config, credential_providers={})

    assert _knowledge_issues(result) == []


def test_validate_auto_inject_with_preemptive_explicitly_on_warns_about_the_discarded_reply() -> None:
    config = inference_config(knowledge=KnowledgeConfig(kb_ids=["kb-1"], auto_inject=True))
    config.pipeline.turn_handling = {"preemptive_generation": {"enabled": True}}

    result = validate_agent_config(config, credential_providers={})

    assert result.ok is True
    (issue,) = _knowledge_issues(result)
    assert issue.severity == "warning"
    assert "discards the preemptive reply" in issue.message
