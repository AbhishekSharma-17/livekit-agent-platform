"""Unit tests for `lkap_agent.settings` (W0-SCAFFOLD)."""

from __future__ import annotations

import pytest
from conftest import REQUIRED_ENV

from lkap_agent.settings import DEFAULT_HTTP_TOOL_USER_AGENT, Settings


def test_settings_reads_required_env_no_dotenv(settings: Settings) -> None:
    assert settings.livekit_url == "wss://example.livekit.cloud"
    assert settings.livekit_api_key == "test-key"
    assert settings.service_token == "test-service"
    assert settings.api_base_url == "http://127.0.0.1:8080"


def test_settings_defaults_are_documented_in_contracts(settings: Settings) -> None:
    assert settings.agent_name == "lkap-agent"
    assert settings.livekit_agent_name is None
    assert settings.connection_id is None
    assert settings.heartbeat_interval_s == 30.0
    assert settings.reconnect_grace_s == 60.0
    assert settings.packs == "packs.insurance_claim,packs.generic"
    assert settings.log_level == "INFO"
    assert settings.log_json is False
    assert settings.vision_max_frame_age_s == 8.0


def test_packs_list_splits_and_trims(settings: Settings) -> None:
    assert settings.packs_list == ["packs.insurance_claim", "packs.generic"]


def test_http_tool_allowed_hosts_list_empty_by_default(settings: Settings) -> None:
    assert settings.http_tool_allowed_hosts_list == []


def test_http_tool_allowed_hosts_list_splits_and_trims(settings: Settings) -> None:
    settings.http_tool_allowed_hosts = " api.example.com , other.example.com "
    assert settings.http_tool_allowed_hosts_list == ["api.example.com", "other.example.com"]


def test_idle_hangup_defaults_to_two_minutes(settings: Settings) -> None:
    assert settings.idle_hangup_s == 120.0


@pytest.mark.parametrize(("raw", "expected"), [("0", 0.0), ("45.5", 45.5)])
def test_idle_hangup_reads_lkap_idle_hangup_s(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: float
) -> None:
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("LKAP_IDLE_HANGUP_S", raw)

    assert Settings().idle_hangup_s == expected


def test_http_tool_user_agent_defaults_to_a_contact_bearing_value(settings: Settings) -> None:
    """Asks #29: a project URL in the User-Agent satisfies Wikimedia's robot policy."""
    assert settings.http_tool_user_agent == DEFAULT_HTTP_TOOL_USER_AGENT
    assert settings.http_tool_user_agent.startswith("LKAP/") and "(+https://" in settings.http_tool_user_agent


def test_http_tool_user_agent_reads_lkap_http_tool_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("LKAP_HTTP_TOOL_USER_AGENT", "acme-voice/2 (ops@example.com)")

    assert Settings().http_tool_user_agent == "acme-voice/2 (ops@example.com)"
