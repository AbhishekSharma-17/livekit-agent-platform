"""Round-trip and validation tests for every contract model (CONTRACTS §6, §8, §9, §10)."""

import json
from typing import Any

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from lkap_contracts import (
    ActivityEvent,
    AgentAction,
    AgentActionResult,
    AgentConfig,
    AgentPublicOut,
    AssetRef,
    CapabilitiesConfig,
    ChecklistItem,
    ConnectResponse,
    DispatchMetadata,
    HttpToolDefinition,
    InternalKbSearchRequest,
    KbSearchRequest,
    McpServerDefinition,
    Note,
    PackManifest,
    PipelineConfig,
    ProviderRef,
    ResolvedAgentConfig,
    ResolvedProvider,
    StatusStamp,
    ToolDefinition,
    ToolMeta,
    UiPatch,
    UiPatchOp,
    UiRequest,
    UiSnapshot,
    UiState,
)
from lkap_contracts.ui_protocol import (
    ACTIVITY_RING_SIZE,
    RPC_AGENT_ACTION,
    RPC_UI_REQUEST,
    SNAPSHOT_EVERY_N_PATCHES,
    TOPIC_UI_ACTIVITY,
    TOPIC_UI_ASSET,
    TOPIC_UI_STATE,
    TOPICS,
    UiStateMessage,
)


def _cascaded_pipeline() -> PipelineConfig:
    return PipelineConfig(
        mode="cascaded",
        stt=ProviderRef(provider_id="livekit-inference-stt"),
        llm=ProviderRef(provider_id="livekit-inference-llm"),
        tts=ProviderRef(provider_id="livekit-inference-tts"),
    )


def _agent_config() -> AgentConfig:
    return AgentConfig(instructions="You are a helpful agent.", pipeline=_cascaded_pipeline())


def _ui_state() -> UiState:
    return UiState(
        status=StatusStamp(label="In review", tone="warning", key="review"),
        progress=40,
        notes=[Note(id="n1", text="Policy H0-44721", ts=1.0, key="policy")],
        checklist=[ChecklistItem(id="c1", label="Photo of damage", blocking=True)],
        assets=[AssetRef(asset_id="a1", kind="photo", mime="image/jpeg", ts=2.0)],
        activity=[
            ActivityEvent(
                id="call-1",
                ts=3.0,
                source="lookup_policy",
                label="Policy desk",
                phase="done",
                headline="Policy verified",
            )
        ],
        custom={"route": "standard"},
    )


def _resolved_config() -> ResolvedAgentConfig:
    return ResolvedAgentConfig(
        session_id="s1",
        agent_id="a1",
        agent_slug="insurance-claim",
        config_version=3,
        pack_id="insurance_claim",
        ui_panel_id="insurance_notebook",
        config=_agent_config(),
        resolved={
            "llm": ResolvedProvider(
                provider_id="google-llm",
                python_class="livekit.plugins.google.LLM",
                model="gemini-2.5-flash",
                kwargs={"api_key": "sk-test", "temperature": 0.7},
            )
        },
        tools=[
            HttpToolDefinition(
                name="get_weather",
                description="Look up the weather.",
                parameters={"type": "object", "properties": {"city": {"type": "string"}}},
                url="https://api.example.com/weather",
                allowed_hosts=["api.example.com"],
            )
        ],
        kb_ids=["kb1"],
        participant_identity="user-abc",
    )


def _pack_manifest() -> PackManifest:
    return PackManifest(
        id="insurance_claim",
        version="0.1.0",
        name="Insurance claim",
        description="Voice claim intake.",
        ui_panel_id="insurance_notebook",
        default_instructions="Take the claim.",
        default_greeting="Hi, I can start your claim.",
        default_voice={"google-realtime": "Kore", "livekit-inference-tts": "Ashley"},
        recommended_pipeline=_cascaded_pipeline(),
        capabilities=CapabilitiesConfig(camera=True),
        builtin_tools_disabled=["pin_frame"],
        tool_names=["lookup_policy"],
        state_schema={"type": "object"},
    )


ROUND_TRIP_CASES: list[BaseModel] = [
    _agent_config(),
    _resolved_config(),
    DispatchMetadata(session_id="s1", agent_id="a1", config_version=2, participant_identity="user-abc"),
    _ui_state(),
    UiSnapshot(seq=1, session_id="s1", state=_ui_state()),
    UiPatch(seq=2, session_id="s1", ops=[UiPatchOp(op="set", path="/progress", value=60)]),
    UiRequest(method="open_dialog", payload={"dialog": "packet"}),
    AgentAction(action="set_video_source", payload={"source": "camera"}),
    AgentActionResult(ok=True),
    _pack_manifest(),
    ToolMeta(name="lookup_policy", silent_reply=True, activity_label="Policy desk"),
    McpServerDefinition(name="docs", url="https://mcp.example.com/sse"),
    AgentPublicOut(
        id="a1",
        slug="insurance-claim",
        name="Insurance claim",
        description="",
        ui_panel_id="insurance_notebook",
        capabilities=CapabilitiesConfig(camera=True),
        pipeline_mode="cascaded",
    ),
]


@pytest.mark.parametrize("model", ROUND_TRIP_CASES, ids=lambda m: type(m).__name__)
def test_model_round_trips_through_json(model: BaseModel) -> None:
    restored = type(model).model_validate_json(model.model_dump_json())
    assert restored == model


@pytest.mark.parametrize("model", ROUND_TRIP_CASES, ids=lambda m: type(m).__name__)
def test_model_round_trips_through_python_dict(model: BaseModel) -> None:
    assert type(model).model_validate(model.model_dump()) == model


@pytest.mark.parametrize("model", ROUND_TRIP_CASES, ids=lambda m: type(m).__name__)
def test_versioned_models_pin_v_to_one(model: BaseModel) -> None:
    if "v" in type(model).model_fields:
        assert model.model_dump()["v"] == 1


def test_agent_config_applies_documented_defaults() -> None:
    config = _agent_config()
    assert config.v == 1
    assert config.timezone == "UTC"
    assert config.voice.greeting == "Hello! How can I help you today?"
    assert config.voice.greeting_mode == "say"
    assert config.voice.language == "en"
    assert config.voice.allow_interruptions is True
    assert config.voice.user_away_timeout_s == 15.0
    assert config.capabilities.camera is False
    assert config.capabilities.screen_share is False
    assert config.capabilities.chat_input is True
    assert config.capabilities.vision_inject_per_turn is True
    assert config.tools.max_tool_steps == 3
    assert config.tools.http_request_enabled is False
    assert config.knowledge.auto_inject is True
    assert config.knowledge.top_k == 4
    assert config.pipeline.mode == "cascaded"


def test_agent_config_mutable_defaults_are_not_shared_between_instances() -> None:
    first = _agent_config()
    second = _agent_config()
    first.tools.builtin_disabled.append("end_call")
    assert second.tools.builtin_disabled == []


def test_dispatch_metadata_carries_ids_only() -> None:
    meta = DispatchMetadata(session_id="s1", agent_id="a1", config_version=1, participant_identity="user-abc")
    payload: dict[str, Any] = json.loads(meta.model_dump_json())
    assert set(payload) == {"v", "session_id", "agent_id", "config_version", "participant_identity"}
    assert all(isinstance(value, str | int) for value in payload.values())


def test_dispatch_metadata_rejects_extra_smuggled_fields() -> None:
    parsed = DispatchMetadata.model_validate(
        {
            "v": 1,
            "session_id": "s1",
            "agent_id": "a1",
            "config_version": 1,
            "participant_identity": "u",
            "api_key": "sk-leak",
        }
    )
    assert not hasattr(parsed, "api_key")
    assert "sk-leak" not in parsed.model_dump_json()


def test_ui_state_message_discriminates_on_type() -> None:
    adapter: TypeAdapter[UiSnapshot | UiPatch] = TypeAdapter(UiStateMessage)
    snapshot = adapter.validate_json(UiSnapshot(seq=1, session_id="s", state=UiState()).model_dump_json())
    patch = adapter.validate_json(UiPatch(seq=2, session_id="s", ops=[]).model_dump_json())
    assert isinstance(snapshot, UiSnapshot)
    assert isinstance(patch, UiPatch)


def test_ui_state_message_rejects_an_unknown_type() -> None:
    adapter: TypeAdapter[UiSnapshot | UiPatch] = TypeAdapter(UiStateMessage)
    with pytest.raises(ValidationError):
        adapter.validate_python({"v": 1, "type": "nope", "seq": 1, "session_id": "s"})


def test_tool_definition_discriminates_on_kind() -> None:
    adapter: TypeAdapter[HttpToolDefinition | McpServerDefinition] = TypeAdapter(ToolDefinition)
    http = adapter.validate_python(
        {
            "kind": "http",
            "name": "get_weather",
            "description": "d",
            "parameters": {"type": "object"},
            "url": "https://api.example.com/w",
        }
    )
    mcp = adapter.validate_python({"kind": "mcp", "name": "docs", "url": "https://mcp.example.com"})
    assert isinstance(http, HttpToolDefinition)
    assert isinstance(mcp, McpServerDefinition)


def test_http_tool_definition_applies_documented_defaults() -> None:
    tool = HttpToolDefinition(
        name="get_weather", description="d", parameters={"type": "object"}, url="https://x.test/w"
    )
    assert tool.method == "POST"
    assert tool.timeout_s == 10
    assert tool.max_result_chars == 4000
    assert tool.silent_reply is False
    assert tool.result_path is None
    assert tool.allowed_hosts == []


@pytest.mark.parametrize("name", ["9bad", "has-dash", "has space", "", "a" * 65])
def test_http_tool_definition_rejects_invalid_tool_names(name: str) -> None:
    with pytest.raises(ValidationError):
        HttpToolDefinition(name=name, description="d", parameters={"type": "object"}, url="https://x.test/w")


def test_mcp_server_definition_applies_documented_defaults() -> None:
    server = McpServerDefinition(name="docs", url="https://mcp.example.com")
    assert server.timeout_s == 5
    assert server.sse_read_timeout_s == 300
    assert server.allowed_tools is None


def test_resolved_config_keeps_secret_kwargs_for_the_worker() -> None:
    resolved = _resolved_config()
    assert resolved.resolved["llm"].kwargs["api_key"] == "sk-test"


def test_resolved_config_slots_are_restricted_to_known_names() -> None:
    with pytest.raises(ValidationError):
        ResolvedAgentConfig.model_validate(
            {
                **_resolved_config().model_dump(),
                "resolved": {
                    "not_a_slot": {"provider_id": "x", "python_class": "y", "model": None, "kwargs": {}}
                },
            }
        )


def test_pack_manifest_defaults_match_contracts() -> None:
    manifest = _pack_manifest()
    assert manifest.settings_schema == {"type": "object"}
    assert manifest.kb_seeds == []
    assert manifest.instructions_by_mode == {}


def test_ui_patch_op_supports_every_documented_op() -> None:
    for op in ("set", "append", "remove", "upsert"):
        parsed = UiPatchOp.model_validate({"op": op, "path": "/notes", "value": None, "key": "policy"})
        assert parsed.op == op


def test_activity_event_phases_are_constrained() -> None:
    with pytest.raises(ValidationError):
        ActivityEvent(id="1", ts=0.0, source="s", label="l", phase="finished", headline="h")  # type: ignore[arg-type]


def test_topics_match_the_documented_wire_values() -> None:
    assert TOPIC_UI_STATE == "lkap.ui.state"
    assert TOPIC_UI_ACTIVITY == "lkap.ui.activity"
    assert TOPIC_UI_ASSET == "lkap.ui.asset"
    assert RPC_UI_REQUEST == "lkap.ui.request"
    assert RPC_AGENT_ACTION == "lkap.agent.action"
    assert TOPICS == {
        "TOPIC_UI_STATE": TOPIC_UI_STATE,
        "TOPIC_UI_ACTIVITY": TOPIC_UI_ACTIVITY,
        "TOPIC_UI_ASSET": TOPIC_UI_ASSET,
        "RPC_UI_REQUEST": RPC_UI_REQUEST,
        "RPC_AGENT_ACTION": RPC_AGENT_ACTION,
    }


def test_protocol_tuning_constants() -> None:
    assert SNAPSHOT_EVERY_N_PATCHES == 50
    assert ACTIVITY_RING_SIZE == 30


def test_connect_response_uses_camel_case_token_source_fields() -> None:
    response = ConnectResponse(
        serverUrl="wss://example.livekit.cloud",
        participantToken="jwt",
        roomName="room",
        participantName="Guest",
        sessionId="s1",
        agent=AgentPublicOut(
            id="a1",
            slug="s",
            name="n",
            description="",
            ui_panel_id="generic",
            capabilities=CapabilitiesConfig(),
            pipeline_mode="cascaded",
        ),
        uiPanelId="generic",
    )
    payload = json.loads(response.model_dump_json())
    assert {"serverUrl", "participantToken", "roomName", "participantName"} <= set(payload)
    assert payload["protocolVersion"] == 1


@pytest.mark.parametrize("model", [KbSearchRequest, InternalKbSearchRequest])
def test_kb_search_request_k_defaults_to_four(model: type[BaseModel]) -> None:
    kwargs: dict[str, Any] = {"query": "q"}
    if model is InternalKbSearchRequest:
        kwargs["kb_ids"] = ["kb1"]
    assert model(**kwargs).k == 4  # type: ignore[attr-defined]


@pytest.mark.parametrize("model", [KbSearchRequest, InternalKbSearchRequest])
@pytest.mark.parametrize("k", [0, -1, 21, 50])
def test_kb_search_request_k_is_clamped_to_one_through_twenty(model: type[BaseModel], k: int) -> None:
    """D-W2-5: the api enforces ``1 <= k <= 20`` via the contract model, not extra code."""
    kwargs: dict[str, Any] = {"query": "q", "k": k}
    if model is InternalKbSearchRequest:
        kwargs["kb_ids"] = ["kb1"]
    with pytest.raises(ValidationError):
        model(**kwargs)
