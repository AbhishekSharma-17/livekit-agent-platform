"""Unit tests for `lkap_api.settings` (W0-SCAFFOLD)."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import REQUIRED_ENV
from pydantic import ValidationError

from lkap_api.settings import Settings, get_settings


def test_settings_reads_required_env_no_dotenv(settings: Settings, data_dir: Path) -> None:
    assert settings.livekit_url == "wss://example.livekit.cloud"
    assert settings.admin_token == "test-admin"
    assert settings.service_token == "test-service"
    assert settings.data_dir == str(data_dir)


def test_settings_defaults_are_documented_in_contracts(settings: Settings) -> None:
    assert settings.agent_name == "lkap-agent"
    assert settings.cors_origins == "http://localhost:3000"
    assert settings.packs == "packs.insurance_claim,packs.generic"
    assert settings.embedder == "fastembed"
    assert settings.log_level == "INFO"
    assert settings.log_json is False
    assert settings.session_sweep_interval_s == 60
    assert settings.session_stale_created_s == 600
    assert settings.session_stale_active_s == 21600


def test_resolved_database_url_defaults_to_sqlite_under_data_dir(settings: Settings, data_dir: Path) -> None:
    assert settings.resolved_database_url == f"sqlite+aiosqlite:///{data_dir}/lkap.db"


def test_resolved_database_url_honours_explicit_override(settings: Settings) -> None:
    settings.database_url = "postgresql+asyncpg://example/db"
    assert settings.resolved_database_url == "postgresql+asyncpg://example/db"


def test_cors_origins_list_splits_and_trims(settings: Settings) -> None:
    settings.cors_origins = " http://a.example , http://b.example "
    assert settings.cors_origins_list == ["http://a.example", "http://b.example"]


def test_packs_list_splits_and_trims(settings: Settings) -> None:
    assert settings.packs_list == ["packs.insurance_claim", "packs.generic"]


def test_bootstrap_credentials_none_by_default(settings: Settings) -> None:
    assert settings.bootstrap_credentials is None


def test_bootstrap_credentials_parses_json(settings: Settings) -> None:
    settings.bootstrap_credentials_json = '{"google-realtime": {"api_key": "x"}}'
    assert settings.bootstrap_credentials == {"google-realtime": {"api_key": "x"}}


def test_bootstrap_credentials_json_validated_eagerly(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("LKAP_BOOTSTRAP_CREDENTIALS_JSON", "{not json")
    get_settings.cache_clear()
    try:
        with pytest.raises(ValidationError):
            get_settings()
    finally:
        get_settings.cache_clear()


# --------------------------------------------------------- V2-20-2: worker callback url
def test_worker_callback_url_falls_back_to_a_loopback_guess_from_port(settings: Settings) -> None:
    settings.api_base_url = None
    settings.public_base_url = None
    settings.port = 8096
    assert settings.worker_callback_base_url == "http://127.0.0.1:8096"
    assert settings.worker_callback_url_is_derived is True


def test_worker_callback_url_prefers_public_base_url_over_the_port_guess(settings: Settings) -> None:
    settings.api_base_url = None
    settings.public_base_url = "https://api.example.com/"
    settings.port = 8096
    assert settings.worker_callback_base_url == "https://api.example.com"
    assert settings.worker_callback_url_is_derived is False


def test_worker_callback_url_prefers_the_explicit_setting_over_everything(settings: Settings) -> None:
    settings.api_base_url = "http://127.0.0.1:8096/"
    settings.public_base_url = "https://api.example.com"
    settings.port = 8080  # deliberately wrong/mismatched, to prove it's never consulted
    assert settings.worker_callback_base_url == "http://127.0.0.1:8096"
    assert settings.worker_callback_url_is_derived is False
