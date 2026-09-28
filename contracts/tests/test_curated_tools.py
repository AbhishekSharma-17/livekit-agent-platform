"""V5-25 curated built-in tools: names, execution sets, config fields, registry entries, templates."""

from typing import Any

import pytest
from pydantic import ValidationError

from lkap_contracts.agent_config import AgentConfig, NotifyTeamConfig, ResolvedAgentConfig, ToolsConfig
from lkap_contracts.common import ProviderRef
from lkap_contracts.export import EXPORTED_MODELS
from lkap_contracts.providers import REGISTRY, by_kind, get
from lkap_contracts.telephony import SmsTarget, TelephonyConfig
from lkap_contracts.tools import (
    BACKGROUNDABLE_BUILTINS,
    BUILTIN_DEFAULT_MODES,
    BUILTIN_TOOL_NAMES,
    CONFIGURED_BUILTINS,
    NEVER_BACKGROUND_TOOLS,
    WRITE_BUILTINS,
    HttpToolDefinition,
    ToolTemplate,
    ToolTemplateInstantiate,
    builtin_tools_document,
)

NEW_BUILTINS = ("calculate", "spell_back", "web_search", "fetch_url", "send_sms", "notify_team")


def test_every_new_builtin_is_a_builtin_name() -> None:
    assert set(NEW_BUILTINS) <= set(BUILTIN_TOOL_NAMES)
    assert len(BUILTIN_TOOL_NAMES) == len(set(BUILTIN_TOOL_NAMES))


def test_each_new_builtin_is_backgroundable_or_never_background_not_both() -> None:
    for name in NEW_BUILTINS:
        assert (name in BACKGROUNDABLE_BUILTINS) != (name in NEVER_BACKGROUND_TOOLS), name
    assert {"calculate", "spell_back", "current_time", "convert_time"} <= NEVER_BACKGROUND_TOOLS
    assert {"send_sms", "notify_team", "web_search", "fetch_url"} <= BACKGROUNDABLE_BUILTINS


def test_write_and_configured_builtins_and_default_modes() -> None:
    # V6-06 adds the checklist tools and generate_image; V6-08 the notebook tools.
    assert WRITE_BUILTINS == {
        "send_sms",
        "notify_team",
        "set_checklist",
        "check_item",
        "generate_image",
        "notebook_write",
        "notebook_check",
    }
    assert CONFIGURED_BUILTINS == {"web_search", "fetch_url", "send_sms", "notify_team"}
    assert BUILTIN_DEFAULT_MODES == {
        "web_search": "auto",
        "fetch_url": "background",
        "send_sms": "background",
        "notify_team": "background",
    }
    assert set(BUILTIN_DEFAULT_MODES) <= BACKGROUNDABLE_BUILTINS


def test_builtin_tools_document_carries_the_new_sets() -> None:
    document = builtin_tools_document()
    assert document["configured_builtins"] == sorted(CONFIGURED_BUILTINS)
    assert document["write_builtins"] == sorted(WRITE_BUILTINS)
    assert {"notify_team", "send_sms"} <= set(document["write_builtins"])
    assert document["builtin_default_modes"]["web_search"] == "auto"


def test_tools_config_defaults_add_no_network_tool() -> None:
    tools = ToolsConfig()
    assert tools.web_search is None
    assert tools.sms is None
    assert tools.fetch_url_allowed_hosts == []
    assert tools.notify_team is None


def test_an_agent_saved_before_v5_25_still_loads() -> None:
    config = AgentConfig.model_validate(
        {"instructions": "x", "pipeline": {"mode": "cascaded"}, "tools": {"builtin_disabled": []}}
    )
    assert config.tools.web_search is None
    assert config.telephony.sms_targets == []


def test_tools_config_round_trips_the_new_fields() -> None:
    tools = ToolsConfig(
        web_search=ProviderRef(provider_id="tavily-search", credential_id="cred_1"),
        sms=ProviderRef(
            provider_id="twilio-sms", credential_id="cred_2", fields={"from_number": "+15550100000"}
        ),
        fetch_url_allowed_hosts=["docs.example.com"],
        notify_team=NotifyTeamConfig(credential_id="cred_3"),
    )
    again = ToolsConfig.model_validate(tools.model_dump(mode="json"))
    assert again == tools
    assert again.notify_team is not None
    assert again.notify_team.secret_name == "TEAM_WEBHOOK_URL"
    assert again.notify_team.include_transcript is False
    assert again.notify_team.style == "slack"


@pytest.mark.parametrize("bad", ["", "1bad", "has space", "x" * 65])
def test_notify_team_secret_name_is_a_placeholder_name(bad: str) -> None:
    with pytest.raises(ValidationError):
        NotifyTeamConfig(credential_id="c", secret_name=bad)


def test_sms_targets_are_e164_only() -> None:
    config = TelephonyConfig(sms_targets=[SmsTarget(label="Claims desk", to="+15550100000")])
    assert config.sms_targets[0].to == "+15550100000"
    for bad in ("sip:desk@example.com", "tel:+15550100000", "5550100", "+0123456789"):
        with pytest.raises(ValidationError):
            SmsTarget(label="x", to=bad)


def test_resolved_config_carries_builtin_providers_with_a_default() -> None:
    payload: dict[str, Any] = {
        "session_id": "s",
        "agent_id": "a",
        "agent_slug": "a",
        "config_version": 1,
        "pack_id": "generic",
        "ui_panel_id": "composite",
        "config": {"instructions": "x", "pipeline": {"mode": "cascaded"}},
        "resolved": {},
        "tools": [],
        "kb_ids": [],
        "participant_identity": "p",
    }
    assert ResolvedAgentConfig.model_validate(payload).builtin_providers == {}
    payload["builtin_providers"] = {
        "web_search": {"provider_id": "tavily-search", "python_class": "", "model": None, "kwargs": {}}
    }
    assert ResolvedAgentConfig.model_validate(payload).builtin_providers["web_search"].provider_id == (
        "tavily-search"
    )


# ------------------------------------------------------------------ registry (D-V5-7)
def test_web_search_kind_has_tavily_first_then_brave() -> None:
    ids = [spec.id for spec in REGISTRY if spec.kind == "web_search"]
    assert ids == ["tavily-search", "brave-search"]


def test_sms_kind_has_twilio_and_telnyx() -> None:
    assert [spec.id for spec in REGISTRY if spec.kind == "sms"] == ["twilio-sms", "telnyx-sms"]


@pytest.mark.parametrize("provider_id", ["tavily-search", "brave-search", "twilio-sms", "telnyx-sms"])
def test_tool_vendor_entries_are_keyed_offered_and_construct_nothing(provider_id: str) -> None:
    spec = get(provider_id)
    assert spec.availability == "available"
    assert spec.requires_credential is True
    assert spec.secret_fields and all(f.type == "secret" and f.required for f in spec.secret_fields)
    assert spec.package == "" and spec.python_class == ""
    # No catalog adapter tests these keys yet (docs/v5/_asks.md, V5-25).
    assert spec.test is None and spec.catalog is None
    assert spec.price_note
    assert spec.get_key_url and spec.get_key_url.startswith("https://")


@pytest.mark.parametrize("provider_id", ["twilio-sms", "telnyx-sms"])
def test_sms_entries_need_a_sending_number(provider_id: str) -> None:
    fields = {f.name: f for f in get(provider_id).fields}
    assert fields["from_number"].required is True


def test_tool_vendor_kinds_are_not_in_the_mvp_status_set() -> None:
    assert by_kind("web_search") == []
    assert [s.id for s in by_kind("web_search", status=None)] == ["tavily-search", "brave-search"]


# ------------------------------------------------------------------ tool templates
def _template(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "cal_com.booking_get",
        "group": "cal_com",
        "group_label": "Cal.com bookings",
        "label": "Look up a booking",
        "summary": "Finds one booking.",
        "secret_names": ["CAL_API_KEY"],
        "definition": HttpToolDefinition(
            name="booking_get",
            description="d",
            parameters={"type": "object", "properties": {}},
            method="GET",
            url="https://api.cal.com/v2/bookings/{{ uid }}",
            allowed_hosts=["api.cal.com"],
        ).model_dump(mode="json"),
    }
    base.update(overrides)
    return base


def test_tool_template_round_trips() -> None:
    template = ToolTemplate.model_validate(_template())
    assert ToolTemplate.model_validate(template.model_dump(mode="json")) == template


@pytest.mark.parametrize("bad_id", ["cal_com", "Cal.x", "cal_com.", "cal_com.Booking"])
def test_tool_template_ids_are_group_dot_name(bad_id: str) -> None:
    with pytest.raises(ValidationError):
        ToolTemplate.model_validate(_template(id=bad_id))


def test_instantiate_body_defaults() -> None:
    body = ToolTemplateInstantiate(credential_id="c")
    assert body.defaults == {} and body.names is None and body.enabled is True


def test_new_models_are_exported() -> None:
    for name in (
        "SmsTarget",
        "NotifyTeamConfig",
        "ToolTemplate",
        "ToolTemplatesResponse",
        "ToolTemplateInstantiate",
        "ToolTemplateInstantiated",
    ):
        assert name in EXPORTED_MODELS
