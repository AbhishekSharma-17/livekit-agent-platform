"""`config_service.validate_agent_config` — the cascaded vision warning (DECISIONS-W2 D-W2-10 step 2)
and the knowledge auto-inject / preemptive generation warning (research-v4 knowledge-and-memory P0-0)."""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
from typing import Any

import httpx
import pytest
from conftest import create_agent, inference_config
from lkap_contracts.agent_config import KnowledgeConfig, ProviderRef, ToolsConfig
from lkap_contracts.api_models import CatalogItem, ProviderModelOut
from lkap_contracts.tools import ToolExecution

from lkap_api.config_service import ValidationContext, register_validator, validate, validate_agent_config
from lkap_api.custom_models.validation import custom_model_issues


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


def test_camera_with_an_unlisted_free_text_model_gets_a_tip_not_a_cannot_see_warning() -> None:
    config = inference_config()
    config.pipeline.llm.model = "some-vendor/unlisted-model"
    config.capabilities.camera = True

    result = validate_agent_config(config, credential_providers={})

    assert result.ok is True
    assert not any("cannot see images" in warning for warning in result.warnings)
    vision = [i for i in result.issues if i.path == "pipeline.llm" and "images" in i.message]
    assert len(vision) == 1 and vision[0].message.startswith("Tip: ")


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


def _openrouter_vision_ctx(
    model: str, *, input_modalities: list[str] | None = None, declared: dict[str, Any] | None = None
) -> ValidationContext:
    """A cascaded agent with the camera on and ``openrouter-llm`` as its LLM."""
    config = inference_config()
    config.pipeline.llm = ProviderRef(provider_id="openrouter-llm", credential_id="cred-or", model=model)
    config.capabilities.camera = True
    catalog: dict[str, CatalogItem] = {}
    if input_modalities is not None:
        meta = {"architecture": {"input_modalities": input_modalities, "output_modalities": ["text"]}}
        catalog[model] = CatalogItem(id=model, label=model, meta=meta)
    records: dict[tuple[str, str, str], ProviderModelOut] = {}
    if declared is not None:
        now = dt.datetime.now(dt.UTC)
        records[("openrouter-llm", "llm", model)] = ProviderModelOut.model_validate(
            {
                "id": "r-or",
                "provider_id": "openrouter-llm",
                "provider_home": "openrouter-llm",
                "kind": "llm",
                "model_id": model,
                "created_at": now,
                "updated_at": now,
                "declared": declared,
            }
        )
    return ValidationContext(
        config=config,
        credential_providers={"cred-or": "openrouter-llm"},
        model_records=records,
        catalog_items={"openrouter-llm": catalog},
    )


def _vision_warnings(ctx: ValidationContext) -> list[str]:
    return [i.message for i in validate(ctx).issues if i.path == "pipeline.llm" and "image" in i.message]


@pytest.mark.parametrize("model", ["google/gemini-3.5-flash", "openai/gpt-4o"])
def test_an_openrouter_model_whose_catalog_lists_image_input_gets_no_vision_warning(model: str) -> None:
    assert _vision_warnings(_openrouter_vision_ctx(model, input_modalities=["text", "image", "file"])) == []


def test_an_openrouter_model_declared_text_only_gets_the_vision_warning() -> None:
    ctx = _openrouter_vision_ctx(
        "google/gemini-3.5-flash", input_modalities=["text", "image"], declared={"vision": False}
    )

    (warning,) = _vision_warnings(ctx)

    assert "cannot see images (per its declared capabilities)" in warning


def test_a_model_with_unknown_vision_gets_a_soft_tip_not_cannot_see() -> None:
    (warning,) = _vision_warnings(_openrouter_vision_ctx("some-lab/unlisted-model-7b"))

    assert warning.startswith("Tip: ")
    assert "cannot see images" not in warning
    assert "vision probe" in warning
    assert "unlisted-model-7b" not in warning, "a typed id is never echoed"


@pytest.mark.parametrize(
    ("provider_id", "model", "input_modalities"),
    [
        ("openrouter-llm", "google/gemini-3.5-flash", ["text"]),
        ("openrouter-llm", "openai/gpt-4.1-mini", None),
        ("livekit-inference-llm", "google/gemma-4-31b-it", None),
        ("livekit-inference-llm", "google/gemini-3.5-flash", None),
    ],
)
def test_the_vision_suggestion_never_names_the_configured_model(
    provider_id: str, model: str, input_modalities: list[str] | None
) -> None:
    ctx = _openrouter_vision_ctx(model, input_modalities=input_modalities)
    ctx.config.pipeline.llm = ProviderRef(provider_id=provider_id, credential_id=None, model=model)
    if input_modalities is not None:
        ctx = dataclasses.replace(ctx, catalog_items={provider_id: ctx.catalog_items["openrouter-llm"]})

    for warning in _vision_warnings(ctx):
        suggestion = warning.split("—", 1)[-1]
        assert model not in suggestion


def test_an_openrouter_text_only_catalog_item_warns_and_suggests_the_vision_probe() -> None:
    (warning,) = _vision_warnings(
        _openrouter_vision_ctx("google/gemini-3.5-flash", input_modalities=["text"])
    )

    assert "cannot see images (per its catalog capabilities)" in warning
    assert "vision probe" in warning, "openrouter-llm lists no model marked 'supports video'"


def _knowledge_issues(result: Any) -> list[Any]:
    return [issue for issue in result.issues if issue.path == "knowledge.auto_inject"]


def test_validate_auto_inject_with_a_knowledge_base_warns_about_preemptive_generation() -> None:
    config = inference_config(knowledge=KnowledgeConfig(kb_ids=["kb-1"], auto_inject=True))

    result = validate_agent_config(config, credential_providers={})

    assert result.ok is True
    (issue,) = _knowledge_issues(result)
    assert issue.severity == "warning", "the contracts have no info severity"
    assert issue.message.startswith("Tip: "), "advisory, not a problem"
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
    assert issue.message.startswith("Tip: ")
    assert "discards the preemptive reply" in issue.message


# ---------------------------------------------- custom model ids (V4-07, D-V4-23/24/26, R-V4-26)
_FINGERPRINT = "fp-current"
_CREDENTIAL = "cred-openai"


def _custom_ctx(
    model: str = "gpt-4.1-nano-2026",
    *,
    record: dict[str, Any] | None = None,
    catalog_ids: tuple[str, ...] = (),
    camera: bool = False,
) -> ValidationContext:
    config = inference_config()
    config.pipeline.llm = ProviderRef(provider_id="openai-llm", credential_id=_CREDENTIAL, model=model)
    config.capabilities.camera = camera
    records: dict[tuple[str, str, str], ProviderModelOut] = {}
    if record is not None:
        now = dt.datetime.now(dt.UTC)
        records[("openai-llm", "llm", model)] = ProviderModelOut.model_validate(
            {
                "id": "r1",
                "provider_id": "openai-llm",
                "provider_home": "openai-llm",
                "kind": "llm",
                "model_id": model,
                "created_at": now,
                "updated_at": now,
                **record,
            }
        )
    return ValidationContext(
        config=config,
        credential_providers={_CREDENTIAL: "openai-llm"},
        credential_fingerprints={_CREDENTIAL: _FINGERPRINT},
        model_records=records,
        catalog_items={"openai-llm": {i: CatalogItem(id=i, label=i) for i in catalog_ids}},
    )


def _llm_findings(ctx: ValidationContext) -> tuple[list[str], list[str]]:
    register_validator(custom_model_issues)
    result = validate(ctx)
    errors = [i.message for i in result.issues if i.path == "pipeline.llm" and i.severity == "error"]
    warnings = [i.message for i in result.issues if i.path == "pipeline.llm" and i.severity == "warning"]
    return errors, warnings


def test_an_untested_custom_model_is_one_warning_without_the_id() -> None:
    errors, warnings = _llm_findings(_custom_ctx())

    assert errors == []
    assert len(warnings) == 1
    assert "not in the suggestion list or the live catalog for 'openai-llm'" in warnings[0]
    assert "run Test model" in warnings[0]
    assert "nano" not in warnings[0]


def test_an_id_the_cached_catalog_lists_is_not_warned_about() -> None:
    assert _llm_findings(_custom_ctx(catalog_ids=("gpt-4.1-nano-2026",))) == ([], [])


def test_a_passing_test_with_the_current_fingerprint_silences_the_warning() -> None:
    record = {"last_test_at": dt.datetime.now(dt.UTC) - dt.timedelta(days=29), "last_test_ok": True}
    assert _llm_findings(_custom_ctx(record={**record, "last_test_fingerprint": _FINGERPRINT})) == ([], [])


@pytest.mark.parametrize(
    "record",
    [
        {"last_test_at": "now", "last_test_ok": True, "last_test_fingerprint": "fp-rotated-away"},
        {"last_test_at": "31d", "last_test_ok": True, "last_test_fingerprint": _FINGERPRINT},
        {"last_test_at": None, "last_test_ok": None, "declared": {"vision": True}},
    ],
    ids=["mismatched-fingerprint", "outside-the-30-day-window", "declared-but-never-tested"],
)
def test_a_record_that_does_not_count_keeps_the_warning(record: dict[str, Any]) -> None:
    now = dt.datetime.now(dt.UTC)
    when = {"now": now, "31d": now - dt.timedelta(days=31), None: None}[record["last_test_at"]]
    _, warnings = _llm_findings(_custom_ctx(record={**record, "last_test_at": when}))

    assert len(warnings) == 1
    assert "not in the suggestion list" in warnings[0]


def test_a_failed_current_test_is_one_warning_with_its_reason() -> None:
    record = {
        "last_test_at": dt.datetime.now(dt.UTC),
        "last_test_ok": False,
        "last_test_fingerprint": _FINGERPRINT,
        "last_test_message": "OpenAI: model_not_found",
    }
    _, warnings = _llm_findings(_custom_ctx(record=record))

    assert warnings == [
        "the last Test model run of this model id failed with this key: OpenAI: model_not_found"
    ]


def test_a_model_gone_from_the_catalog_or_deprecated_is_a_warning() -> None:
    now = dt.datetime.now(dt.UTC)
    ok = {"last_test_at": now, "last_test_ok": True, "last_test_fingerprint": _FINGERPRINT}
    gone = {**ok, "catalog_seen_at": now - dt.timedelta(days=3), "catalog_missing_since": now}
    deprecated = {**ok, "catalog_seen_at": now, "catalog_missing_since": now + dt.timedelta(days=60)}

    _, gone_warnings = _llm_findings(_custom_ctx(record=gone))
    _, deprecated_warnings = _llm_findings(_custom_ctx(record=deprecated, catalog_ids=("gpt-4.1-nano-2026",)))

    assert len(gone_warnings) == 1
    assert "no longer appears in OpenAI's catalog (last seen" in gone_warnings[0]
    assert len(deprecated_warnings) == 1
    assert "deprecated by OpenAI (on " in deprecated_warnings[0]


def test_a_custom_model_declared_text_only_warns_about_camera() -> None:
    record = {
        "declared": {"vision": False},
        "last_test_at": dt.datetime.now(dt.UTC),
        "last_test_ok": True,
        "last_test_fingerprint": _FINGERPRINT,
    }
    register_validator(custom_model_issues)

    warnings = validate(_custom_ctx(record=record, camera=True)).warnings

    assert any("cannot see images (per its declared capabilities)" in w for w in warnings)


def test_a_custom_model_declared_vision_capable_does_not_warn_about_camera() -> None:
    register_validator(custom_model_issues)

    warnings = validate(_custom_ctx(record={"declared": {"vision": True}}, camera=True)).warnings

    assert not any("cannot see images" in w for w in warnings)


@pytest.mark.parametrize(
    ("model", "reason"),
    [("sk-or-v1-deadbeefdeadbeefcafebabe00112233", "looks like an API key"), ("gpt 4", "whitespace")],
)
def test_a_bad_model_id_is_one_error_and_no_warning(model: str, reason: str) -> None:
    errors, warnings = _llm_findings(_custom_ctx(model))

    assert len(errors) == 1
    assert reason in errors[0]
    assert warnings == []
    assert "deadbeef" not in errors[0]


def test_the_enum_error_names_the_field_but_never_the_value() -> None:
    config = inference_config()
    config.pipeline.mode = "realtime"
    config.pipeline.stt = config.pipeline.llm = config.pipeline.tts = None
    config.pipeline.realtime = ProviderRef(
        provider_id="google-realtime", credential_id="c", fields={"voice": "NotAVoice99"}
    )

    result = validate_agent_config(config, credential_providers={"c": "google-realtime"})

    assert len([e for e in result.errors if "field 'voice' must be one of" in e]) == 1
    assert "NotAVoice99" not in " ".join(result.errors)


# -------------------------------------------------------- V4-12: background tools


def _tools_ctx(definitions: dict[str, dict[str, Any]], **tools_fields: Any) -> ValidationContext:
    config = inference_config()
    config.tools = ToolsConfig(tool_ids=list(definitions), **tools_fields)
    return ValidationContext(config=config, tool_definitions_by_id=definitions)


def _issues_at(result: Any, prefix: str) -> list[Any]:
    return [issue for issue in result.issues if issue.path.startswith(prefix)]


def _http(**overrides: Any) -> dict[str, Any]:
    return {
        "kind": "http",
        "name": "lookup_item",
        "description": "Look up an item.",
        "parameters": {"type": "object", "properties": {}},
        "method": "GET",
        "url": "https://api.example.com/items",
        **overrides,
    }


def _mcp(**overrides: Any) -> dict[str, Any]:
    return {"kind": "mcp", "name": "crm", "url": "https://mcp.example.com/mcp", **overrides}


@pytest.mark.parametrize("mode", ["background", "auto"])
def test_silent_reply_with_a_non_blocking_mode_is_an_error(mode: str) -> None:
    result = validate(_tools_ctx({"t1": _http(silent_reply=True, execution={"mode": mode})}))

    (issue,) = _issues_at(result, "tools[0]")
    assert issue.path == "tools[0].definition.execution.mode"
    assert issue.severity == "error"
    assert "silent_reply" in issue.message
    assert result.ok is False


def test_silent_reply_with_a_blocking_tool_is_fine() -> None:
    result = validate(_tools_ctx({"t1": _http(silent_reply=True, execution={"mode": "blocking"})}))

    assert _issues_at(result, "tools[") == []


def test_a_builtin_execution_key_outside_the_read_builtins_is_an_error() -> None:
    result = validate(
        _tools_ctx(
            {},
            builtin_execution={
                "search_knowledge": ToolExecution(mode="auto"),
                "end_call": ToolExecution(mode="background"),
            },
        )
    )

    (issue,) = _issues_at(result, "tools.builtin_execution")
    assert issue.path == "tools.builtin_execution.end_call"
    assert issue.severity == "error"


def test_an_mcp_tool_option_outside_allowed_tools_is_an_error() -> None:
    definition = _mcp(allowed_tools=["lookup"], tool_options={"search": {"mode": "blocking"}})

    result = validate(_tools_ctx({"m1": definition}))

    (issue,) = _issues_at(result, "tools[0]")
    assert issue.path == "tools[0].definition.tool_options.search"
    assert issue.severity == "error"


def test_a_background_mcp_tool_without_progress_is_a_warning() -> None:
    definition = _mcp(
        tool_options={
            "search": {"mode": "background"},
            "lookup": {"mode": "auto", "report_progress": True},
        }
    )

    result = validate(_tools_ctx({"m1": definition}))

    (issue,) = _issues_at(result, "tools[0]")
    assert issue.path == "tools[0].definition.tool_options.search.report_progress"
    assert issue.severity == "warning"
    assert "will not announce" in issue.message
    assert result.ok is True


def test_a_never_list_name_with_a_background_mode_is_an_error() -> None:
    result = validate(_tools_ctx({"m1": _mcp(tool_options={"end_call": {"mode": "background"}})}))

    (issue,) = _issues_at(result, "tools[0]")
    assert issue.path == "tools[0].definition.tool_options.end_call.mode"
    assert issue.severity == "error"


@pytest.mark.parametrize(
    ("default", "steps", "warns"), [("auto", 3, True), ("auto", 4, False), ("blocking", 1, False)]
)
def test_a_background_default_with_few_tool_steps_warns(default: str, steps: int, warns: bool) -> None:
    result = validate(_tools_ctx({}, execution_default=default, max_tool_steps=steps))

    issues = _issues_at(result, "tools.max_tool_steps")
    assert bool(issues) is warns
    if warns:
        assert issues[0].severity == "warning"
        assert "4 or more" in issues[0].message


def test_tool_checks_are_skipped_without_the_rows() -> None:
    config = inference_config()
    config.tools = ToolsConfig(tool_ids=["t1"])

    assert _issues_at(validate(ValidationContext(config=config)), "tools[") == []


async def test_the_validate_route_reads_the_attached_tool_rows(admin_client: httpx.AsyncClient) -> None:
    """`validation_context_for` hands the stored definitions to the checks (end to end)."""
    definition = _mcp(tool_options={"search": {"mode": "background"}})
    created = await admin_client.post(
        "/v1/tools", json={"kind": "mcp", "name": "crm", "definition": definition}
    )
    assert created.status_code == 201, created.text
    config = inference_config()
    config.tools = ToolsConfig(tool_ids=[created.json()["id"]], execution_default="auto")
    agent = await create_agent(admin_client, published=False, config=json.loads(config.model_dump_json()))

    response = await admin_client.post(f"/v1/agents/{agent['id']}/validate")

    assert response.status_code == 200
    paths = {issue["path"]: issue["severity"] for issue in response.json()["issues"]}
    assert paths["tools[0].definition.tool_options.search.report_progress"] == "warning"
    assert paths["tools.max_tool_steps"] == "warning"


async def test_a_tool_with_silent_reply_and_a_background_mode_is_refused_on_save(
    admin_client: httpx.AsyncClient,
) -> None:
    definition = _http(silent_reply=True, execution={"mode": "background"})

    response = await admin_client.post(
        "/v1/tools", json={"kind": "http", "name": "lookup_item", "definition": definition}
    )

    assert response.status_code == 422
    assert "silent_reply" in response.text
