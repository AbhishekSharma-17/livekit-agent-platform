"""V5-31: language validators, per-language voices in `/resolved`, per-turn language on transcripts."""

from __future__ import annotations

import json
from typing import Any

import httpx
from conftest import create_agent, inference_config
from lkap_contracts.agent_config import AgentConfig, ProviderRef, ResolvedAgentConfig
from lkap_contracts.api_models import Issue
from sqlalchemy import select

from lkap_api.config_service import (
    ValidationContext,
    language_issues,
    resolve_language_voices,
    validate_agent_config,
)
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.session import Database

API_KEY = "sk-voice-only-7e21"


def _config(
    languages: list[str],
    *,
    auto_detect: bool = False,
    stt: str = "livekit-inference-stt",
    tts: str = "livekit-inference-tts",
    voices: dict[str, ProviderRef] | None = None,
    mode: str = "cascaded",
) -> AgentConfig:
    config = inference_config()
    config.voice.languages = languages
    config.voice.auto_detect = auto_detect
    config.voice.voices_by_language = voices or {}
    if config.pipeline.stt is not None:
        config.pipeline.stt = ProviderRef(provider_id=stt)
    config.pipeline.tts = ProviderRef(provider_id=tts)
    config.pipeline.mode = mode  # type: ignore[assignment]
    return config


def _issues(config: AgentConfig) -> list[Issue]:
    return language_issues(ValidationContext(config=config))


def _by_path(config: AgentConfig) -> dict[str, list[Issue]]:
    found: dict[str, list[Issue]] = {}
    for issue in _issues(config):
        found.setdefault(issue.path, []).append(issue)
    return found


def test_one_language_draws_nothing() -> None:
    assert _issues(inference_config()) == []
    assert _issues(_config(["hi"])) == []


def test_a_language_the_transcriber_does_not_list_is_an_error() -> None:
    issues = _by_path(_config(["en", "sw"]))
    (error,) = [i for i in issues["voice.languages"] if i.severity == "error"]
    assert "cannot transcribe Swahili" in error.message


def test_an_unknown_capability_list_draws_no_error() -> None:
    issues = _issues(_config(["en", "sw"], stt="speechmatics-stt"))
    assert not [i for i in issues if i.severity == "error"]


def test_auto_detect_on_a_transcriber_without_detection_warns() -> None:
    issues = _by_path(_config(["en", "hi"], auto_detect=True, stt="speechmatics-stt"))
    assert "cannot detect the caller's language" in issues["voice.auto_detect"][0].message


def test_auto_detect_warns_about_languages_detection_does_not_cover() -> None:
    issues = _by_path(_config(["en", "ta"], auto_detect=True))
    assert "cannot detect Tamil automatically" in issues["voice.auto_detect"][0].message
    assert "voice.auto_detect" not in _by_path(_config(["en", "hi"], auto_detect=True))


def test_a_transcriber_that_cannot_switch_warns_without_detection() -> None:
    issues = _by_path(_config(["en", "hi"], stt="sarvam-stt"))
    (warning,) = issues["voice.languages"]
    assert "cannot change language during a call" in warning.message
    assert "Turn on automatic detection" in warning.message
    assert "voice.languages" not in _by_path(_config(["en", "hi"], stt="sarvam-stt", auto_detect=True))


def test_a_language_without_a_voice_warns_and_a_voice_silences_it() -> None:
    issues = _by_path(_config(["en", "hi"]))
    assert "Hindi has no voice of its own" in issues["voice.voices_by_language"][0].message
    hindi = ProviderRef(provider_id="livekit-inference-tts", fields={"voice": "Hana"})
    assert "voice.voices_by_language" not in _by_path(_config(["en", "hi"], voices={"hi": hindi}))


def test_a_voice_that_lists_its_language_needs_no_warning() -> None:
    issues = _by_path(_config(["en", "hi"], tts="sarvam-tts"))
    assert "voice.voices_by_language" not in issues


def test_a_voice_for_a_language_the_agent_does_not_speak_warns() -> None:
    french = ProviderRef(provider_id="livekit-inference-tts")
    issues = _by_path(_config(["en", "hi"], voices={"fr": french}))
    assert "French is not one of the agent's languages" in issues["voice.voices_by_language.fr"][0].message


def test_a_voice_that_does_not_list_its_language_warns() -> None:
    sarvam = ProviderRef(provider_id="sarvam-tts")
    issues = _by_path(_config(["en", "fr"], voices={"fr": sarvam}))
    assert "does not list French" in issues["voice.voices_by_language.fr"][0].message


def test_realtime_gets_a_tip_and_no_stt_checks() -> None:
    config = inference_config()
    config.voice.languages = ["en", "sw"]
    config.voice.voices_by_language = {"sw": ProviderRef(provider_id="livekit-inference-tts")}
    config.pipeline.mode = "realtime"
    issues = _issues(config)
    assert all(i.severity == "warning" for i in issues)
    assert any(i.message.startswith("Tip: a realtime model") for i in issues)


def test_a_voice_of_the_wrong_kind_is_an_error() -> None:
    config = _config(["en", "hi"], voices={"hi": ProviderRef(provider_id="livekit-inference-stt")})
    result = validate_agent_config(config, credential_providers={})
    paths = {i.path: i for i in result.issues if i.severity == "error"}
    assert "is not a text-to-speech provider" in paths["voice.voices_by_language.hi"].message


def test_resolve_language_voices_only_speaks_through_a_tts() -> None:
    voice = ProviderRef(provider_id="openai-tts", credential_id="cred-1", fields={"voice": "alloy"})
    config = _config(["en", "hi"], voices={"hi": voice})
    resolved = resolve_language_voices(config, {"cred-1": {"api_key": API_KEY}})
    assert resolved["hi"].kwargs["api_key"] == API_KEY
    assert resolved["hi"].kwargs["voice"] == "alloy"
    config.pipeline.mode = "realtime"
    assert resolve_language_voices(config, {"cred-1": {"api_key": API_KEY}}) == {}


# ------------------------------------------------------------------ over the wire


async def _credential(client: httpx.AsyncClient, provider_id: str, secrets: dict[str, str]) -> str:
    response = await client.post(
        "/v1/credentials", json={"provider_id": provider_id, "label": provider_id, "secrets": secrets}
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def _session_for(admin_client: httpx.AsyncClient, config: dict[str, Any]) -> str:
    agent = await create_agent(admin_client, name="Languages agent", config=config)
    response = await admin_client.post(f"/v1/agents/{agent['id']}/connect", json={})
    return str(response.json()["sessionId"])


async def test_resolved_carries_the_voice_per_language_with_its_key(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient
) -> None:
    credential_id = await _credential(admin_client, "openai-tts", {"api_key": API_KEY})
    voice = ProviderRef(provider_id="openai-tts", credential_id=credential_id, fields={"voice": "alloy"})
    config = _config(["en", "hi"], voices={"hi": voice})
    session_id = await _session_for(admin_client, json.loads(config.model_dump_json()))

    response = await service_client.get(f"/internal/v1/sessions/{session_id}/resolved")

    assert response.status_code == 200, response.text
    resolved = ResolvedAgentConfig.model_validate(response.json())
    assert resolved.config.voice.languages == ["en", "hi"]
    assert resolved.voices_by_language["hi"].kwargs["api_key"] == API_KEY


async def test_an_agent_without_languages_resolves_no_voices(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient
) -> None:
    session_id = await _session_for(admin_client, json.loads(inference_config().model_dump_json()))
    resolved = (await service_client.get(f"/internal/v1/sessions/{session_id}/resolved")).json()
    assert resolved["voices_by_language"] == {}
    assert resolved["config"]["voice"]["languages"] == []


async def test_the_summary_keeps_each_turns_language(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient, database: Database
) -> None:
    session_id = await _session_for(admin_client, json.loads(inference_config().model_dump_json()))

    response = await service_client.put(
        f"/internal/v1/sessions/{session_id}/summary",
        json={
            "status": "ended",
            "usage": {},
            "transcript": [
                {"role": "user", "text": "namaste", "ts": 1758000000.0, "language": "hi"},
                {"role": "assistant", "text": "Namaste!", "ts": 1758000001.0, "language": "hi"},
                {"role": "user", "text": "hello", "ts": 1758000002.0},
            ],
        },
    )

    assert response.status_code == 204, response.text
    async with database.session() as session:
        row = (await session.execute(select(SessionRow))).scalar_one()
    assert row.transcript is not None
    assert [turn.get("language") for turn in row.transcript] == ["hi", "hi", None]
    detail = (await admin_client.get(f"/v1/sessions/{session_id}")).json()
    assert [turn.get("language") for turn in detail["transcript"]] == ["hi", "hi", None]
