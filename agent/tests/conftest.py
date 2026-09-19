"""Shared pytest fixtures for `lkap_agent` tests.

No `.env` file is read in tests: every required setting is supplied via
monkeypatched environment variables so the offline suite runs with no
vendor keys, no `.env`, and no network (`pytest -m "not live"`).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from lkap_agent.settings import Settings, get_settings

REQUIRED_ENV: dict[str, str] = {
    "LIVEKIT_URL": "wss://example.livekit.cloud",
    "LIVEKIT_API_KEY": "test-key",
    "LIVEKIT_API_SECRET": "test-secret",
    "LKAP_SERVICE_TOKEN": "test-service",
    "LKAP_API_BASE_URL": "http://127.0.0.1:8080",
}


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    """A `Settings` instance built entirely from env vars, no `.env` file."""
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()
