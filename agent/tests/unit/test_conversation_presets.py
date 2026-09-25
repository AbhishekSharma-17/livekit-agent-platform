"""Conversation tuning in the worker (V5-07): presets, turn detector settings, telephony noise filter.

The builder expands `pipeline.conversation_preset` at build time; `prepare_resolved`
carries `pipeline.turn_detector` into the detector's constructor kwargs and swaps the
noise filter for its telephony variant on a phone call with the `telephony` preset.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fakes.fake_api import resolved_config
from livekit.agents import inference
from lkap_contracts import providers as provider_registry
from lkap_contracts.agent_config import ResolvedAgentConfig, ResolvedProvider
from lkap_contracts.connections import ConnectionCapabilities, ConnectionInfo
from lkap_contracts.turn_handling import CONVERSATION_PRESETS

from lkap_agent import session_builder
from lkap_agent.providers.factory import BuiltProviders, ProviderFactory
from lkap_agent.session_builder import SessionBuilder, prepare_resolved

SIGNATURES: dict[str, Any] = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "plugin_signatures.json").read_text()
)


@pytest.fixture(autouse=True)
def _inference_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIVEKIT_API_KEY", "test-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "test-secret")
    monkeypatch.setenv("LIVEKIT_URL", "wss://example.livekit.cloud")


def _configure(resolved: ResolvedAgentConfig, **pipeline: Any) -> ResolvedAgentConfig:
    updated = resolved.config.pipeline.model_validate({**resolved.config.pipeline.model_dump(), **pipeline})
    config = resolved.config.model_copy(update={"pipeline": updated})
    return resolved.model_copy(update={"config": config})


def _with_slot(
    resolved: ResolvedAgentConfig, slot: str, provider_id: str, **kwargs: Any
) -> ResolvedAgentConfig:
    spec = provider_registry.get(provider_id)
    slots = dict(resolved.resolved)
    slots[slot] = ResolvedProvider(  # type: ignore[index]
        provider_id=provider_id, python_class=spec.python_class, model=None, kwargs=kwargs
    )
    return resolved.model_copy(update={"resolved": slots})


def _on_connection(
    resolved: ResolvedAgentConfig, *, turn_detector_mode: str = "hosted", nc_tier: str = "none"
) -> ResolvedAgentConfig:
    caps = ConnectionCapabilities(
        turn_detector_mode=turn_detector_mode,  # type: ignore[arg-type]
        noise_cancellation_tier=nc_tier,  # type: ignore[arg-type]
    )
    return resolved.model_copy(update={"connection": ConnectionInfo(connection_id="c1", capabilities=caps)})


def _build(resolved: ResolvedAgentConfig) -> Any:
    providers = BuiltProviders(stt=object(), llm=object(), tts=object())
    return SessionBuilder().build(resolved, providers, vad=object(), turn_detector=None)


# ------------------------------------------------------------ presets reach the session


async def test_custom_preset_passes_the_stored_turn_handling_through() -> None:
    stored = {"endpointing": {"min_delay": 0.7, "max_delay": 2.5}, "interruption": {"min_duration": 0.9}}

    plan = _build(_configure(resolved_config(auto_inject=False), turn_handling=stored))

    options = plan.session.options
    assert plan.conversation_preset == "custom"
    assert options.endpointing["min_delay"] == 0.7
    assert options.endpointing["max_delay"] == 2.5
    assert options.interruption["min_duration"] == 0.9


@pytest.mark.parametrize("preset", sorted(CONVERSATION_PRESETS))
async def test_named_preset_reaches_the_session_options(preset: str) -> None:
    pinned = CONVERSATION_PRESETS[preset]
    resolved = _configure(
        resolved_config(auto_inject=False),
        conversation_preset=preset,
        turn_handling={"endpointing": {"min_delay": 9.0}},
    )

    plan = _build(resolved)

    options = plan.session.options
    assert plan.conversation_preset == preset
    for key, value in pinned["endpointing"].items():
        assert options.endpointing[key] == value, key
    for key, value in pinned["interruption"].items():
        assert options.interruption[key] == value, key
    assert options.preemptive_generation["enabled"] is True


async def test_named_preset_keeps_allow_interruptions_from_the_voice_settings() -> None:
    resolved = _configure(resolved_config(auto_inject=False), conversation_preset="patient")
    voice = resolved.config.voice.model_copy(update={"allow_interruptions": False})
    resolved = resolved.model_copy(update={"config": resolved.config.model_copy(update={"voice": voice})})

    plan = _build(resolved)

    assert plan.session.options.interruption["enabled"] is False
    assert plan.session.options.endpointing["min_delay"] == 1.0


async def test_patient_preset_with_knowledge_auto_inject_still_turns_preemptive_off() -> None:
    resolved = _configure(resolved_config(auto_inject=True, kb_ids=["kb-1"]), conversation_preset="patient")

    assert _build(resolved).session.options.preemptive_generation["enabled"] is False


async def test_snappy_preset_keeps_preemptive_on_even_with_knowledge_auto_inject() -> None:
    resolved = _configure(resolved_config(auto_inject=True, kb_ids=["kb-1"]), conversation_preset="snappy")

    assert _build(resolved).session.options.preemptive_generation["enabled"] is True


async def test_an_agent_saved_before_v5_07_builds_the_same_session() -> None:
    stored = {"endpointing": {"min_delay": 0.3}}
    before = _configure(resolved_config(auto_inject=False), turn_handling=stored)

    plan = _build(before)
    prepared = prepare_resolved(before)

    assert plan.session.options.endpointing["min_delay"] == 0.3
    assert plan.ambient_sound == "none"
    assert prepared.resolved == before.resolved


# ------------------------------------------------------------ turn detector settings


def test_unlikely_threshold_synthesizes_an_inference_detector_slot() -> None:
    resolved = _configure(resolved_config(), turn_detector={"unlikely_threshold": 0.2})

    slot = prepare_resolved(_on_connection(resolved)).resolved["turn_detection"]

    assert slot.provider_id == "inference-turn-detector"
    assert slot.python_class == provider_registry.get("inference-turn-detector").python_class
    assert slot.kwargs == {"unlikely_threshold": 0.2}


def test_unlikely_threshold_reaches_the_turn_detector_constructor() -> None:
    resolved = _configure(resolved_config(), turn_detector={"mode": "local", "unlikely_threshold": 0.25})
    prepared = prepare_resolved(_on_connection(resolved))

    detector = ProviderFactory().build("turn_detection", prepared.resolved["turn_detection"])

    assert isinstance(detector, inference.TurnDetector)
    assert detector.model == "turn-detector-v1-mini"
    assert detector.describe_options()["threshold_overrides"] == 0.25


@pytest.mark.parametrize("provider_id", ["inference-turn-detector", "turn-detector-plugin"])
def test_the_detector_constructors_take_unlikely_threshold(provider_id: str) -> None:
    """Signature snapshot (`scripts/snapshot_plugin_signatures.py`, livekit-agents 1.8.3)."""
    python_class = provider_registry.get(provider_id).python_class

    assert "unlikely_threshold" in SIGNATURES[python_class]["params"]


@pytest.mark.parametrize(
    ("settings", "connection", "expected"),
    [
        ({"mode": "local"}, "hosted", {"version": "v1-mini"}),
        ({"mode": "hosted"}, "local", {"version": "v1-mini"}),
        ({"mode": "hosted", "unlikely_threshold": 0.3}, "hosted", {"unlikely_threshold": 0.3}),
    ],
)
def test_turn_detector_mode_and_connection_pick_the_version(
    settings: dict[str, Any], connection: str, expected: dict[str, Any]
) -> None:
    resolved = _configure(resolved_config(), turn_detector=settings)

    slot = prepare_resolved(_on_connection(resolved, turn_detector_mode=connection)).resolved[
        "turn_detection"
    ]

    assert slot.kwargs == expected


def test_settings_that_change_nothing_leave_the_default_detector() -> None:
    resolved = _configure(resolved_config(), turn_detector={"mode": "hosted"})

    assert "turn_detection" not in prepare_resolved(_on_connection(resolved)).resolved


@pytest.mark.parametrize(("mode", "channel"), [("realtime", "web"), ("cascaded", "text")])
def test_no_detector_slot_without_client_side_turns(mode: str, channel: str) -> None:
    resolved = _configure(
        resolved_config(mode=mode, channel=channel),  # type: ignore[arg-type]
        turn_detector={"mode": "local", "unlikely_threshold": 0.2},
    )

    assert "turn_detection" not in prepare_resolved(_on_connection(resolved)).resolved


def test_a_configured_inference_detector_keeps_its_own_version_and_gains_the_threshold() -> None:
    resolved = _with_slot(resolved_config(), "turn_detection", "inference-turn-detector", version="v1")
    resolved = _configure(resolved, turn_detector={"mode": "local", "unlikely_threshold": 0.4})

    slot = prepare_resolved(_on_connection(resolved)).resolved["turn_detection"]

    assert slot.kwargs == {"version": "v1", "unlikely_threshold": 0.4}


def test_the_local_plugin_detector_gains_only_the_threshold() -> None:
    resolved = _with_slot(resolved_config(), "turn_detection", "turn-detector-plugin")
    resolved = _configure(resolved, turn_detector={"mode": "local", "unlikely_threshold": 0.4})

    slot = prepare_resolved(_on_connection(resolved)).resolved["turn_detection"]

    assert slot.provider_id == "turn-detector-plugin"
    assert slot.kwargs == {"unlikely_threshold": 0.4}


# ------------------------------------------------------------ telephony noise cancellation


@pytest.mark.parametrize(("channel", "expected"), [("sip_in", "BVCTelephony"), ("sip_out", "BVCTelephony")])
def test_telephony_preset_on_a_phone_call_selects_the_telephony_variant(channel: str, expected: str) -> None:
    resolved = _with_slot(resolved_config(channel=channel), "noise_cancellation", "legacy-noise-cancellation")
    resolved = _configure(resolved, conversation_preset="telephony")

    slot = prepare_resolved(resolved).resolved["noise_cancellation"]

    assert slot.python_class == f"livekit.plugins.noise_cancellation.{expected}"


def test_telephony_preset_on_a_web_session_keeps_the_default_filter() -> None:
    resolved = _with_slot(resolved_config(channel="web"), "noise_cancellation", "legacy-noise-cancellation")
    resolved = _configure(resolved, conversation_preset="telephony")

    slot = prepare_resolved(resolved).resolved["noise_cancellation"]

    assert slot.python_class == "livekit.plugins.noise_cancellation.BVC"


@pytest.mark.parametrize("preset", ["custom", "balanced", "snappy", "patient"])
def test_other_presets_keep_the_default_filter_on_a_phone_call(preset: str) -> None:
    resolved = _with_slot(
        resolved_config(channel="sip_in"), "noise_cancellation", "legacy-noise-cancellation"
    )
    resolved = _configure(resolved, conversation_preset=preset)

    slot = prepare_resolved(resolved).resolved["noise_cancellation"]

    assert slot.python_class == "livekit.plugins.noise_cancellation.BVC"


def test_telephony_preset_keeps_a_filter_without_a_telephony_variant() -> None:
    resolved = resolved_config(channel="sip_in")
    slots = dict(resolved.resolved)
    slots["noise_cancellation"] = ResolvedProvider(
        provider_id="ai-coustics-noise-cancellation", python_class="x.Filter", model=None, kwargs={}
    )
    resolved = _configure(resolved.model_copy(update={"resolved": slots}), conversation_preset="telephony")

    assert prepare_resolved(resolved).resolved["noise_cancellation"].python_class == "x.Filter"


def _offered_cloud_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend the LiveKit Cloud filter ships in the worker image (it is deferred today)."""
    offered = provider_registry.get("legacy-noise-cancellation").model_copy(
        update={"availability": "available"}
    )
    monkeypatch.setattr(session_builder, "_noise_cancellation_specs", lambda: [offered])


def test_telephony_preset_turns_the_offered_cloud_filter_on_for_a_phone_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _offered_cloud_filter(monkeypatch)
    resolved = _configure(resolved_config(channel="sip_in"), conversation_preset="telephony")

    slot = prepare_resolved(_on_connection(resolved, nc_tier="krisp")).resolved["noise_cancellation"]

    assert slot.provider_id == "legacy-noise-cancellation"
    assert slot.python_class == "livekit.plugins.noise_cancellation.BVCTelephony"


@pytest.mark.parametrize(
    ("channel", "tier", "installed"),
    [
        ("web", "krisp", None),  # not a phone call
        ("sip_in", "none", None),  # no Cloud noise cancellation on this connection
        ("sip_in", "krisp", ["inference-turn-detector"]),  # not installed on the worker pool
    ],
)
def test_telephony_preset_adds_no_filter_when_none_is_offered(
    monkeypatch: pytest.MonkeyPatch, channel: str, tier: str, installed: list[str] | None
) -> None:
    _offered_cloud_filter(monkeypatch)
    resolved = _configure(resolved_config(channel=channel), conversation_preset="telephony")
    resolved = resolved.model_copy(update={"installed_provider_ids": installed})

    assert "noise_cancellation" not in prepare_resolved(_on_connection(resolved, nc_tier=tier)).resolved


def test_telephony_preset_adds_no_filter_while_every_entry_is_deferred() -> None:
    """Today's registry: neither Cloud filter is in the worker image, so nothing is added."""
    resolved = _configure(resolved_config(channel="sip_in"), conversation_preset="telephony")

    assert "noise_cancellation" not in prepare_resolved(_on_connection(resolved, nc_tier="krisp")).resolved
