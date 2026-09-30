"""V6-34 on the worker: VAD silence, the fast preset, Inference Flux turns, sticky routing.

Plugins are constructed offline with placeholder keys; the one chat request is answered by
``respx`` and its body read back, so nothing opens a socket.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx
from fakes.fake_api import resolved_config
from livekit.agents import APIConnectOptions, inference, llm
from lkap_contracts import providers as provider_registry
from lkap_contracts.agent_config import ResolvedAgentConfig, ResolvedProvider
from lkap_contracts.common import ProviderRef
from lkap_contracts.connections import ConnectionCapabilities, ConnectionInfo
from lkap_contracts.turn_handling import CONVERSATION_PRESETS, FAST_PRESETS

from lkap_agent.providers.factory import BuiltProviders, ProviderFactory, inference_stt_flux_kwargs
from lkap_agent.session_builder import STT_TURN_DETECTION, SessionBuilder, prepare_resolved, stt_decides_turns

OPENROUTER_KEY = "sk-or-v1-test-not-real"
CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
AGENT_ID = "agent-7f3a"


@pytest.fixture(autouse=True)
def _inference_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIVEKIT_API_KEY", "test-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "test-secret")
    monkeypatch.setenv("LIVEKIT_URL", "wss://example.livekit.cloud")


def _provider(provider_id: str, *, model: str | None = None, **kwargs: Any) -> ResolvedProvider:
    spec = provider_registry.get(provider_id)
    return ResolvedProvider(
        provider_id=provider_id, python_class=spec.python_class, model=model, kwargs=kwargs
    )


def _agent(
    *,
    stt: ResolvedProvider | None = None,
    slots: dict[str, ResolvedProvider] | None = None,
    turn_detector_mode: str = "hosted",
    **pipeline: Any,
) -> ResolvedAgentConfig:
    """A cascaded agent on the fake api's config with the given stt slot and pipeline fields."""
    base = resolved_config(auto_inject=False, agent_id=AGENT_ID)
    resolved = dict(base.resolved)
    if stt is not None:
        resolved["stt"] = stt
        pipeline.setdefault("stt", ProviderRef(provider_id=stt.provider_id, model=stt.model))
    resolved.update(slots or {})  # type: ignore[typeddict-item]
    updated = base.config.pipeline.model_validate({**base.config.pipeline.model_dump(), **pipeline})
    caps = ConnectionCapabilities(turn_detector_mode=turn_detector_mode)  # type: ignore[arg-type]
    return base.model_copy(
        update={
            "config": base.config.model_copy(update={"pipeline": updated}),
            "resolved": resolved,
            "connection": ConnectionInfo(connection_id="c1", capabilities=caps),
        }
    )


def _build(resolved: ResolvedAgentConfig, *, default_detector: Any = None) -> Any:
    providers = BuiltProviders(stt=object(), llm=object(), tts=object())
    return SessionBuilder().build(resolved, providers, vad=object(), turn_detector=default_detector)


INFERENCE_FLUX = _provider("livekit-inference-stt", model="deepgram/flux-general-en", language="en")
INFERENCE_NOVA = _provider("livekit-inference-stt", model="deepgram/nova-3", language="en")
DIRECT_FLUX = _provider("deepgram-flux-stt", model="flux-general-en", api_key="dg-placeholder-key")


# ------------------------------------------------------------------ 1. VAD silence reaches VAD.load
def test_silero_vad_slot_passes_the_silence_and_thresholds_to_vad_load() -> None:
    vad = ProviderFactory().build(
        "vad",
        _provider(
            "silero-vad", min_silence_duration=0.3, activation_threshold=0.6, prefix_padding_duration=0.4
        ),
    )

    assert vad.min_silence_duration == 0.3
    assert vad._opts.activation_threshold == 0.6
    assert vad._opts.prefix_padding_duration == 0.4


def test_silero_vad_slot_without_the_fields_keeps_the_sdk_defaults() -> None:
    vad = ProviderFactory().build("vad", _provider("silero-vad"))

    assert vad.min_silence_duration == 0.55


def test_inference_vad_slot_passes_the_silence() -> None:
    vad = ProviderFactory().build("vad", _provider("inference-vad", min_silence_duration=0.35))

    assert isinstance(vad, inference.VAD)
    assert vad.min_silence_duration == 0.35


def test_vad_silence_below_the_floor_is_raised_when_the_turn_detector_runs() -> None:
    agent = _agent(stt=INFERENCE_NOVA, slots={"vad": _provider("silero-vad", min_silence_duration=0.1)})

    prepared = prepare_resolved(agent)

    assert prepared.resolved["vad"].kwargs["min_silence_duration"] == 0.25


@pytest.mark.parametrize(
    ("stt", "pipeline"),
    [(DIRECT_FLUX, {}), (INFERENCE_FLUX, {"conversation_preset": "fast"})],
)
def test_vad_silence_below_the_floor_is_kept_when_the_transcriber_ends_turns(
    stt: ResolvedProvider, pipeline: dict[str, Any]
) -> None:
    agent = _agent(stt=stt, slots={"vad": _provider("silero-vad", min_silence_duration=0.1)}, **pipeline)

    prepared = prepare_resolved(agent)

    assert prepared.resolved["vad"].kwargs["min_silence_duration"] == 0.1


def test_vad_silence_at_or_above_the_floor_is_untouched() -> None:
    agent = _agent(stt=INFERENCE_NOVA, slots={"vad": _provider("silero-vad", min_silence_duration=0.3)})

    assert prepare_resolved(agent).resolved["vad"].kwargs == {"min_silence_duration": 0.3}


# ------------------------------------------------------------------ 2. the fast preset per pipeline
@pytest.mark.parametrize(
    ("stt", "turns_by_stt"),
    [(DIRECT_FLUX, True), (INFERENCE_FLUX, True), (INFERENCE_NOVA, False)],
)
async def test_fast_preset_resolves_per_pipeline(stt: ResolvedProvider, turns_by_stt: bool) -> None:
    default_detector = object()
    plan = _build(_agent(stt=stt, conversation_preset="fast"), default_detector=default_detector)

    options = plan.session.options
    pinned = FAST_PRESETS["stt" if turns_by_stt else "detector"]
    assert plan.conversation_preset == "fast"
    assert (plan.session.turn_detection == STT_TURN_DETECTION) is turns_by_stt
    for key, value in pinned["endpointing"].items():
        assert options.endpointing[key] == value, key
    assert options.preemptive_generation["enabled"] is True
    assert options.preemptive_generation["preemptive_tts"] is turns_by_stt


async def test_fast_preset_keeps_preemptive_on_even_with_knowledge_auto_inject() -> None:
    base = _agent(stt=DIRECT_FLUX, conversation_preset="fast")
    knowledge = base.config.knowledge.model_copy(update={"auto_inject": True, "kb_ids": ["kb-1"]})
    agent = base.model_copy(update={"config": base.config.model_copy(update={"knowledge": knowledge})})

    assert _build(agent).session.options.preemptive_generation["enabled"] is True


async def test_fast_preset_with_an_explicit_detector_slot_uses_the_detector_values() -> None:
    agent = _agent(
        stt=DIRECT_FLUX,
        conversation_preset="fast",
        turn_detection=ProviderRef(provider_id="inference-turn-detector"),
    )
    slot_detector = object()
    providers = BuiltProviders(stt=object(), llm=object(), tts=object(), turn_detection=slot_detector)

    plan = SessionBuilder().build(agent, providers, vad=object(), turn_detector=None)

    assert plan.session.turn_detection is slot_detector
    assert (
        plan.session.options.endpointing["max_delay"] == FAST_PRESETS["detector"]["endpointing"]["max_delay"]
    )


# ------------------------------------------------------------------ 3. Inference Flux: opt-in turns
async def test_inference_flux_keeps_the_turn_detector_unless_the_agent_opts_in() -> None:
    default_detector = object()

    plan = _build(_agent(stt=INFERENCE_FLUX), default_detector=default_detector)

    assert stt_decides_turns(_agent(stt=INFERENCE_FLUX)) is False
    assert plan.session.turn_detection is default_detector


@pytest.mark.parametrize(
    "pipeline",
    [{"conversation_preset": "fast"}, {"turn_detector": {"mode": "stt"}}],
)
async def test_inference_flux_gets_stt_turn_detection_when_opted_in(pipeline: dict[str, Any]) -> None:
    agent = _agent(stt=INFERENCE_FLUX, **pipeline)

    plan = _build(prepare_resolved(agent), default_detector=object())

    assert stt_decides_turns(agent) is True
    assert plan.session.turn_detection == STT_TURN_DETECTION


async def test_turn_detector_mode_stt_synthesizes_no_detector_slot_on_a_local_connection() -> None:
    agent = _agent(stt=INFERENCE_FLUX, turn_detector={"mode": "stt"}, turn_detector_mode="local")

    assert "turn_detection" not in prepare_resolved(agent).resolved


async def test_turn_detector_mode_stt_on_nova_runs_the_detector_as_if_unset() -> None:
    agent = _agent(stt=INFERENCE_NOVA, turn_detector={"mode": "stt"}, turn_detector_mode="local")

    prepared = prepare_resolved(agent)

    assert prepared.resolved["turn_detection"].kwargs == {"version": "v1-mini"}
    assert stt_decides_turns(prepared) is False


def test_inference_flux_options_reach_the_plugin_extra_kwargs() -> None:
    stt = ProviderFactory().build(
        "stt",
        _provider(
            "livekit-inference-stt",
            model="deepgram/flux-general-en",
            language="en",
            eot_threshold=0.75,
            eager_eot_threshold=0.4,
            eot_timeout_ms=3000.0,
        ),
    )

    assert isinstance(stt, inference.STT)
    assert stt._opts.extra_kwargs == {
        "eot_threshold": 0.75,
        "eager_eot_threshold": 0.4,
        "eot_timeout_ms": 3000,
    }
    assert isinstance(stt._opts.extra_kwargs["eot_timeout_ms"], int)


def test_flux_options_on_a_non_flux_inference_model_are_dropped() -> None:
    stt = ProviderFactory().build(
        "stt", _provider("livekit-inference-stt", model="deepgram/nova-3", eot_threshold=0.8)
    )

    assert stt._opts.extra_kwargs == {}


def test_inference_stt_flux_kwargs_leaves_a_ref_without_options_alone() -> None:
    kwargs = {"model": "deepgram/flux-general-en", "language": "en"}

    assert inference_stt_flux_kwargs(kwargs) is kwargs


# ------------------------------------------------------------------ 5. OpenRouter sticky routing
def _openrouter(**fields: Any) -> ResolvedProvider:
    """``openrouter-llm`` as the api resolves it: registry defaults plus ``fields``."""
    spec = provider_registry.get("openrouter-llm")
    kwargs: dict[str, Any] = {f.name: f.default for f in spec.fields if f.default is not None}
    kwargs.update(fields, api_key=OPENROUTER_KEY)
    return ResolvedProvider(
        provider_id=spec.id, python_class=spec.python_class, model="openai/gpt-4.1-mini", kwargs=kwargs
    )


async def _request_body(model: llm.LLM) -> dict[str, Any]:
    route = respx.post(CHAT_URL).mock(return_value=httpx.Response(400, json={"error": {"message": "stop"}}))
    chat_ctx = llm.ChatContext.empty()
    chat_ctx.add_message(role="user", content="hello")
    stream = model.chat(chat_ctx=chat_ctx, conn_options=APIConnectOptions(max_retry=0, timeout=5))
    with pytest.raises(Exception):  # noqa: B017 - the mocked 400 ends the stream
        async with stream:
            async for _ in stream:
                pass
    assert route.called
    body: dict[str, Any] = json.loads(route.calls.last.request.content)
    return body


def _prepared_llm(**fields: Any) -> ResolvedProvider:
    agent = _agent(slots={"llm": _openrouter(**fields), "workflow_llm": _openrouter(**fields)})
    return prepare_resolved(agent).resolved["llm"]


@respx.mock
async def test_openrouter_request_carries_the_agent_id_as_user_and_cache_key_when_on() -> None:
    slot = _prepared_llm(sticky_routing=True)

    body = await _request_body(ProviderFactory().build("llm", slot))

    assert body["user"] == AGENT_ID
    assert body["prompt_cache_key"] == AGENT_ID
    assert "sticky_routing" not in body


def test_sticky_routing_applies_to_every_openrouter_slot() -> None:
    agent = _agent(
        slots={"llm": _openrouter(sticky_routing=True), "workflow_llm": _openrouter(sticky_routing=True)}
    )

    prepared = prepare_resolved(agent)

    for slot in ("llm", "workflow_llm"):
        kwargs = prepared.resolved[slot].kwargs  # type: ignore[literal-required]
        assert kwargs["prompt_cache_key"] == AGENT_ID
        assert "sticky_routing" not in kwargs


@respx.mock
async def test_openrouter_request_of_a_stored_agent_is_unchanged() -> None:
    stored = await _request_body(ProviderFactory().build("llm", _prepared_llm()))
    explicit_off = await _request_body(ProviderFactory().build("llm", _prepared_llm(sticky_routing=False)))

    assert stored == explicit_off
    assert "user" not in stored
    assert "prompt_cache_key" not in stored
    assert stored["temperature"] == 0.7
    assert stored["provider"] == {"require_parameters": True}


# ------------------------------------------------------------------ compatibility (D-V6-31)
@pytest.mark.parametrize("preset", [*sorted(CONVERSATION_PRESETS), "custom"])
@pytest.mark.parametrize("stt", [DIRECT_FLUX, INFERENCE_FLUX, INFERENCE_NOVA], ids=["direct", "flux", "nova"])
async def test_a_stored_agent_builds_the_same_session_as_before_v6_34(
    preset: str, stt: ResolvedProvider
) -> None:
    """Every pre-V6-34 preset on every transcriber: same turn detection and turn handling as before.

    Before V6-34 only an entry flagged ``end_of_turn`` (Deepgram Flux direct) ended turns, and
    presets resolved without a pipeline. Inference Flux keeps the platform's detector.
    """
    default_detector = object()
    agent = _agent(stt=stt, conversation_preset=preset, turn_handling={"endpointing": {"min_delay": 0.4}})

    plan = _build(prepare_resolved(agent), default_detector=default_detector)

    expected_detection = STT_TURN_DETECTION if stt is DIRECT_FLUX else default_detector
    assert plan.session.turn_detection == expected_detection
    expected_endpointing = CONVERSATION_PRESETS.get(preset, {}).get("endpointing", {"min_delay": 0.4})
    for key, value in expected_endpointing.items():
        assert plan.session.options.endpointing[key] == value, key
    assert prepare_resolved(agent).resolved["stt"].kwargs == stt.kwargs
