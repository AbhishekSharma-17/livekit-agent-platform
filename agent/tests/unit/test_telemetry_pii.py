"""V5-30: `privacy.telemetry_pii` drives `LIVEKIT_TELEMETRY_ALLOW_PII` when an OTLP exporter is set."""

from __future__ import annotations

import os

import pytest
from fakes.fake_api import FakeApi

from lkap_agent.observability import (
    TELEMETRY_ALLOW_PII_ENV,
    SessionObserver,
    apply_telemetry_pii,
)


@pytest.mark.parametrize(("allow", "expected"), [(True, "1"), (False, "0")])
def test_the_flag_is_set_when_an_otlp_endpoint_is_configured(allow: bool, expected: str) -> None:
    env = {"OTEL_EXPORTER_OTLP_ENDPOINT": "https://otel.example.com"}

    assert apply_telemetry_pii(allow, env) is True
    assert env[TELEMETRY_ALLOW_PII_ENV] == expected


def test_a_traces_only_endpoint_also_counts() -> None:
    env = {"OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "https://otel.example.com/v1/traces"}

    assert apply_telemetry_pii(False, env) is True
    assert env[TELEMETRY_ALLOW_PII_ENV] == "0"


def test_without_an_exporter_nothing_is_set() -> None:
    """No third-party exporter: nothing receives spans, so the environment is left alone."""
    env: dict[str, str] = {}

    assert apply_telemetry_pii(False, env) is False
    assert env == {}


def test_the_observer_applies_the_agents_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://otel.example.com")
    # set (not deleted) so monkeypatch restores the original state after the observer writes it
    monkeypatch.setenv(TELEMETRY_ALLOW_PII_ENV, "1")

    SessionObserver(session_id="sess-1", client=FakeApi(), telemetry_pii=False)

    assert os.environ[TELEMETRY_ALLOW_PII_ENV] == "0"


def test_the_observer_leaves_the_environment_alone_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Compatibility: `main.py` today passes no setting, and nothing changes."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://otel.example.com")
    monkeypatch.setenv(TELEMETRY_ALLOW_PII_ENV, "operator-choice")

    SessionObserver(session_id="sess-1", client=FakeApi())

    assert os.environ[TELEMETRY_ALLOW_PII_ENV] == "operator-choice"
