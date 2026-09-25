"""R-V5-10: the caller's timezone — `LocaleConfig`, the IANA helper and the api models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from lkap_contracts import tools
from lkap_contracts.agent_config import AgentConfig, LocaleConfig, PipelineConfig, ResolvedAgentConfig
from lkap_contracts.api_models import (
    ConnectRequest,
    LocaleEvent,
    SessionDetailOut,
    SessionOut,
    TextSessionCreate,
)
from lkap_contracts.common import CALLER_TIMEZONE_ATTRIBUTE, is_iana_timezone


def _config(**kwargs: Any) -> AgentConfig:
    return AgentConfig(instructions="Be brief.", pipeline=PipelineConfig(), **kwargs)


def test_locale_config_defaults_to_detect() -> None:
    assert LocaleConfig().caller_timezone == "detect"
    assert _config().locale == LocaleConfig()


def test_agent_config_saved_before_locale_validates_as_detect() -> None:
    """Compatibility: a stored config without `locale` behaves as `detect`."""
    stored = {"instructions": "Hi", "pipeline": {"mode": "cascaded"}, "timezone": "Europe/London"}

    config = AgentConfig.model_validate(stored)

    assert config.locale.caller_timezone == "detect"
    assert config.timezone == "Europe/London"


def test_locale_config_refuses_an_unknown_mode() -> None:
    with pytest.raises(ValidationError):
        LocaleConfig.model_validate({"caller_timezone": "guess"})


@pytest.mark.parametrize("name", ["Asia/Kolkata", "Europe/London", "America/New_York", "UTC"])
def test_is_iana_timezone_accepts_known_names(name: str) -> None:
    assert is_iana_timezone(name) is True


@pytest.mark.parametrize("value", ["", "asia/kolkata", "+05:30", "Mars/Olympus_Mons", "UTC ", None, 5])
def test_is_iana_timezone_refuses_anything_else(value: object) -> None:
    assert is_iana_timezone(value) is False


def test_caller_timezone_attribute_is_reserved_under_lkap() -> None:
    assert CALLER_TIMEZONE_ATTRIBUTE == "lkap.tz"


def test_resolved_config_defaults_keep_an_older_api_valid() -> None:
    """`business_timezone` is optional: an api before V5-51 still produces a valid document."""
    resolved = ResolvedAgentConfig(
        session_id="s",
        agent_id="a",
        agent_slug="a",
        config_version=1,
        pack_id="generic",
        ui_panel_id="composite",
        config=_config(),
        resolved={},
        tools=[],
        kb_ids=[],
        participant_identity="user-1",
    )

    assert resolved.business_timezone is None
    assert resolved.locale == LocaleConfig()


def test_text_session_create_is_a_connect_request_with_a_timezone() -> None:
    body = TextSessionCreate.model_validate({"participant_name": "Ana", "timezone": "Asia/Kolkata"})

    assert isinstance(body, ConnectRequest)
    assert body.timezone == "Asia/Kolkata"
    assert TextSessionCreate().timezone is None


def test_text_session_create_accepts_an_unknown_zone_for_the_api_to_drop() -> None:
    """Never a 422: the api, not the model, decides what an unknown name means."""
    assert TextSessionCreate(timezone="Nowhere/Land").timezone == "Nowhere/Land"


def _session_out(**kwargs: Any) -> dict[str, Any]:
    return {
        "id": "s",
        "agent_id": "a",
        "agent_name": "A",
        "config_version": 1,
        "room_name": "lkap-s",
        "status": "ended",
        "pipeline_mode": "cascaded",
        "created_at": datetime(2026, 9, 26, tzinfo=UTC),
        **kwargs,
    }


def test_session_out_reads_caller_timezone_from_the_summary_usage() -> None:
    out = SessionOut.model_validate(_session_out(usage={"caller_timezone": "Asia/Kolkata", "turns": 3}))

    assert out.caller_timezone == "Asia/Kolkata"


def test_session_detail_keeps_the_caller_timezone_of_its_base_row() -> None:
    base = SessionOut.model_validate(_session_out(usage={"caller_timezone": "Asia/Kolkata"}))

    detail = SessionDetailOut(**base.model_dump())

    assert detail.caller_timezone == "Asia/Kolkata"


@pytest.mark.parametrize("usage", [None, {}, {"caller_timezone": ""}, {"caller_timezone": 3}])
def test_session_out_caller_timezone_is_null_without_one(usage: dict[str, Any] | None) -> None:
    assert SessionOut.model_validate(_session_out(usage=usage)).caller_timezone is None


def test_locale_event_payload_shape() -> None:
    event = LocaleEvent(caller_timezone="Asia/Kolkata", source="browser", business_timezone="Europe/London")

    assert event.model_dump() == {
        "caller_timezone": "Asia/Kolkata",
        "source": "browser",
        "business_timezone": "Europe/London",
    }
    with pytest.raises(ValidationError):
        LocaleEvent.model_validate({**event.model_dump(), "source": "guess"})


def test_convert_time_is_a_builtin_that_never_runs_in_the_background() -> None:
    assert "convert_time" in tools.BUILTIN_TOOL_NAMES
    assert tools.never_background("convert_time")
    assert tools.never_background("current_time")
