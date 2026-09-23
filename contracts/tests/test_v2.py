"""LKAP v2 contract tests: registry alias, AgentConfig v2, blocks, flows, pricing.

Covers the V2-00 acceptance criteria of `docs/v2/PLAN-V2.md`:
the ``ProviderSpec.status`` alias equals its v1 value for every existing entry,
``agent_config_v1_to_v2`` round-trips both pack manifests and the v1 fixtures,
``FlowSpec`` rejects the six invalid shapes of CONTRACTS-V2 §4.5, and a v2
``UiState`` patch on ``/blocks/x`` applies in a reducer fixture.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from lkap_contracts import pricing
from lkap_contracts.agent_config import (
    AgentConfig,
    AgentLimits,
    PanelLayout,
    PipelineConfig,
    ProviderRef,
    ResolvedAgentConfig,
    pipeline_issues,
)
from lkap_contracts.connections import ConnectionCapabilities, ConnectionInfo
from lkap_contracts.dispatch import DispatchMetadata
from lkap_contracts.fleet import FleetDesired, WorkerEnv
from lkap_contracts.flow import (
    AgentNode,
    EndNode,
    FlowEdge,
    FlowSpec,
    GlobalNode,
    QaNode,
    StartNode,
    edge_tool_name,
)
from lkap_contracts.migrate import (
    DEFAULT_COMPOSITE_BLOCKS,
    agent_config_v1_to_v2,
    agent_config_v2_to_v1,
    default_panel_for,
)
from lkap_contracts.pricing import Price
from lkap_contracts.providers import (
    REGISTRY,
    ProviderSpec,
    available_providers,
    by_image,
    constructible,
    get,
    mvp_providers,
)
from lkap_contracts.ui_protocol import (
    BlockSpec,
    FormBlockState,
    KbCitation,
    KbCitationsBlockState,
    TableBlockState,
    TableColumn,
    UiPatch,
    UiPatchOp,
    UiState,
)

# --------------------------------------------------------------------------- registry

#: Exactly the ids the v1 registry shipped as ``status="mvp"``, in registry
#: order (CONTRACTS §4). The alias must keep this set byte-identical until
#: V2-03/V2-13 migrate the consumers (ruling R-V2-1).
V1_MVP_ID_ORDER = [
    "livekit-inference-stt",
    "livekit-inference-llm",
    "livekit-inference-tts",
    "google-realtime",
    "openai-realtime",
    "deepgram-stt",
    "openai-llm",
    "google-llm",
    "cartesia-tts",
    "elevenlabs-tts",
    "openai-tts",
    "bey-avatar",
    "tavus-avatar",
    "google-image-gen",
    "openai-image-gen",
    "fastembed-embedding",
    "openai-embedding",
    "http-tool-secret",
]

V1_MVP_IDS = set(V1_MVP_ID_ORDER)


@pytest.mark.parametrize("spec", REGISTRY, ids=lambda s: s.id)
def test_provider_status_alias_keeps_its_v1_value(spec: ProviderSpec) -> None:
    assert spec.status == ("mvp" if spec.id in V1_MVP_IDS else "deferred")


@pytest.mark.parametrize("spec", REGISTRY, ids=lambda s: s.id)
def test_provider_status_alias_is_derived_from_availability_and_image(spec: ProviderSpec) -> None:
    expected = "mvp" if spec.availability == "available" and spec.worker_image == "slim" else "deferred"
    assert spec.status == expected


@pytest.mark.parametrize("spec", REGISTRY, ids=lambda s: s.id)
def test_provider_verification_never_affects_the_status_alias(spec: ProviderSpec) -> None:
    """R-V2-1: flipping ``verification`` must not move a provider in or out of ``mvp``."""
    flipped = "unverified" if spec.verification == "verified" else "verified"
    other = ProviderSpec.model_validate({**spec.model_dump(exclude={"status"}), "verification": flipped})
    assert other.status == spec.status


def test_the_mvp_id_set_is_byte_identical_to_the_v1_registry() -> None:
    """The alias's whole purpose: waves 0-1 consumers see exactly the v1 set."""
    assert [spec.id for spec in mvp_providers()] == V1_MVP_ID_ORDER


def test_only_the_four_live_verified_providers_are_marked_verified() -> None:
    assert {s.id for s in REGISTRY if s.verification == "verified"} == {
        "livekit-inference-stt",
        "livekit-inference-llm",
        "livekit-inference-tts",
        "fastembed-embedding",
    }


def test_an_available_full_image_provider_is_not_mvp() -> None:
    """V2-05 lands new entries as ``worker_image="full"``, so they stay out of ``mvp``."""
    spec = ProviderSpec.model_validate(
        {
            "id": "anthropic-llm",
            "kind": "llm",
            "label": "Anthropic",
            "vendor": "Anthropic",
            "package": "livekit-plugins-anthropic",
            "python_class": "livekit.plugins.anthropic.LLM",
            "availability": "available",
            "verification": "verified",
            "worker_image": "full",
        }
    )
    assert spec.status == "deferred"


@pytest.mark.parametrize("spec", REGISTRY, ids=lambda s: s.id)
def test_provider_spec_pins_v_to_two(spec: ProviderSpec) -> None:
    assert spec.v == 2


def test_provider_spec_still_accepts_a_v1_document_for_one_release() -> None:
    spec = ProviderSpec.model_validate(
        {"v": 1, "id": "x", "kind": "llm", "label": "X", "vendor": "X", "package": "p", "python_class": "p.L"}
    )
    assert spec.v == 1


def test_provider_status_input_is_ignored_because_the_alias_is_derived() -> None:
    spec = ProviderSpec.model_validate(
        {
            "id": "x",
            "kind": "llm",
            "label": "X",
            "vendor": "X",
            "package": "p",
            "python_class": "p.LLM",
            "status": "mvp",
            "availability": "deferred",
            "worker_image": "slim",
        }
    )
    assert spec.status == "deferred"


def test_available_providers_matches_the_registry_availability_field() -> None:
    assert [s.id for s in available_providers()] == [s.id for s in REGISTRY if s.availability == "available"]


def test_mvp_providers_still_returns_the_eighteen_v1_entries() -> None:
    assert {s.id for s in mvp_providers()} == V1_MVP_IDS


def test_by_image_slim_returns_the_v1_plugin_set() -> None:
    assert {s.id for s in by_image("slim")} == V1_MVP_IDS


def test_by_image_full_includes_the_slim_entries() -> None:
    slim = {s.id for s in by_image("slim")}
    assert slim <= {s.id for s in by_image("full")}


def test_constructible_without_a_registration_assumes_every_available_provider() -> None:
    assert constructible(None) == available_providers()


def test_constructible_filters_to_the_pools_installed_ids() -> None:
    assert [s.id for s in constructible(["openai-llm", "not-installed"])] == ["openai-llm"]


def test_registry_accepts_the_new_v2_kinds_and_field_types() -> None:
    spec = ProviderSpec.model_validate(
        {
            "id": "krisp-nc",
            "kind": "noise_cancellation",
            "label": "Krisp",
            "vendor": "LiveKit",
            "package": "livekit-plugins-noise-cancellation",
            "python_class": "livekit.plugins.noise_cancellation.BVC",
            "worker_image": "full",
            "capabilities": {"cloud_only": True, "platforms": ["linux-x86_64"]},
            "fields": [
                {"name": "voice", "label": "Voice", "type": "catalog", "catalog_kind": "voices"},
                {"name": "avatar_image", "label": "Image", "type": "file", "accept": "image/*"},
            ],
        }
    )
    assert spec.kind == "noise_cancellation"
    assert spec.capabilities.cloud_only is True
    assert [f.type for f in spec.fields] == ["catalog", "file"]


def test_inference_providers_are_available_on_slim_and_live_verified() -> None:
    spec = get("livekit-inference-llm")
    assert (spec.availability, spec.worker_image, spec.verification, spec.status) == (
        "available",
        "slim",
        "verified",
        "mvp",
    )


def test_a_vendor_key_provider_ships_unverified_but_still_reports_mvp() -> None:
    """Vendor plugins were never live-tested in v1 (F-34); V2-20 flips them."""
    spec = get("openai-llm")
    assert (spec.availability, spec.worker_image, spec.verification, spec.status) == (
        "available",
        "slim",
        "unverified",
        "mvp",
    )


# ----------------------------------------------------------------------- agent config


def _v1_config(panel_pipeline: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a minimal but complete v1 ``AgentConfig`` document."""
    return {
        "v": 1,
        "instructions": "Be helpful.",
        "pipeline": panel_pipeline
        or {
            "mode": "cascaded",
            "stt": {"provider_id": "livekit-inference-stt"},
            "llm": {"provider_id": "livekit-inference-llm"},
            "tts": {"provider_id": "livekit-inference-tts"},
        },
        "voice": {"greeting": "Hi"},
        "capabilities": {"camera": True},
        "tools": {"tool_ids": ["t1"]},
        "knowledge": {"kb_ids": ["kb1"]},
        "pack_settings": {"a": 1},
        "timezone": "Europe/Berlin",
    }


def test_agent_config_defaults_to_v2_with_the_new_sections() -> None:
    config = AgentConfig(instructions="hi", pipeline=PipelineConfig())
    assert config.v == 2
    assert config.panel.panel_id == "composite"
    assert config.recording.enabled is False
    assert config.qa.enabled is False
    assert config.flow is None


def test_agent_config_still_accepts_a_v1_document_for_one_release() -> None:
    config = AgentConfig.model_validate(_v1_config())
    assert config.v == 1
    assert config.panel.panel_id == "composite"


def test_agent_config_rejects_an_unknown_protocol_version() -> None:
    with pytest.raises(ValidationError):
        AgentConfig.model_validate({**_v1_config(), "v": 3})


def test_voice_config_adds_first_speaker_and_capabilities_add_dtmf() -> None:
    config = AgentConfig(instructions="hi", pipeline=PipelineConfig())
    assert config.voice.first_speaker == "agent"
    assert config.capabilities.dtmf is False


def test_agent_limits_carry_the_documented_defaults() -> None:
    limits = AgentLimits()
    assert (limits.max_concurrent_sessions, limits.max_session_duration_s) == (5, 1800)
    assert (limits.rate_per_ip_per_min, limits.rate_per_agent_per_min) == (6, 60)


@pytest.mark.parametrize(
    ("mode", "slots", "expected_paths"),
    [
        ("cascaded", {"stt": True, "llm": True, "tts": True}, []),
        ("cascaded", {"stt": True, "llm": True}, ["pipeline.tts"]),
        ("cascaded", {}, ["pipeline.stt", "pipeline.llm", "pipeline.tts"]),
        ("realtime", {"realtime": True}, []),
        ("realtime", {}, ["pipeline.realtime"]),
        ("half_cascade", {"realtime": True, "tts": True}, []),
        ("half_cascade", {"realtime": True}, ["pipeline.tts"]),
        ("half_cascade", {}, ["pipeline.realtime", "pipeline.tts"]),
    ],
)
def test_pipeline_issues_reports_missing_slots_per_mode(
    mode: str, slots: dict[str, bool], expected_paths: list[str]
) -> None:
    pipeline = PipelineConfig.model_validate(
        {"mode": mode, **{slot: {"provider_id": f"x-{slot}"} for slot in slots}}
    )
    assert [issue.path for issue in pipeline_issues(pipeline)] == expected_paths


def test_pipeline_issues_are_errors_not_warnings() -> None:
    assert all(issue.severity == "error" for issue in pipeline_issues(PipelineConfig(mode="realtime")))


def test_pipeline_accepts_the_new_vad_turn_and_noise_cancellation_slots() -> None:
    pipeline = PipelineConfig(
        mode="cascaded",
        vad=ProviderRef(provider_id="silero-vad"),
        turn_detection=ProviderRef(provider_id="livekit-turn-detector"),
        noise_cancellation=ProviderRef(provider_id="krisp-nc"),
    )
    assert pipeline.vad is not None
    assert pipeline.avatar_options.participant_name == "Avatar"


def test_resolved_agent_config_defaults_keep_a_v1_api_valid() -> None:
    resolved = ResolvedAgentConfig(
        session_id="s1",
        agent_id="a1",
        agent_slug="a",
        config_version=3,
        pack_id="generic",
        ui_panel_id="generic",
        config=AgentConfig(instructions="hi", pipeline=PipelineConfig()),
        resolved={},
        tools=[],
        kb_ids=[],
        participant_identity="user-1",
    )
    assert resolved.v == 2
    assert resolved.channel == "web"
    assert resolved.connection == ConnectionInfo()
    assert resolved.installed_provider_ids is None


def test_connection_capabilities_default_to_the_most_restrictive_target() -> None:
    caps = ConnectionCapabilities()
    assert caps.inference_available is False
    assert caps.turn_detector_mode == "local"
    assert caps.noise_cancellation_tier == "none"


def test_dispatch_metadata_v2_carries_channel_and_connection() -> None:
    meta = DispatchMetadata(
        session_id="s1",
        agent_id="a1",
        config_version=1,
        participant_identity="u",
        channel="sip_in",
        connection_id="c1",
    )
    assert meta.v == 2
    assert meta.model_dump()["channel"] == "sip_in"


def test_dispatch_metadata_session_id_is_optional_for_worker_created_sessions() -> None:
    """D-V2-5: server-created rooms (inbound SIP) dispatch without a session; the worker starts one."""
    meta = DispatchMetadata.model_validate(
        {"agent_id": "a1", "config_version": 1, "participant_identity": "", "channel": "sip_in"}
    )
    assert meta.session_id is None
    assert DispatchMetadata.model_validate_json(meta.model_dump_json()).session_id is None


def test_dispatch_metadata_defaults_channel_to_web_for_v1_producers() -> None:
    meta = DispatchMetadata.model_validate(
        {"session_id": "s1", "agent_id": "a1", "config_version": 1, "participant_identity": "u"}
    )
    assert (meta.channel, meta.connection_id) == ("web", "")


# -------------------------------------------------------------------------- migration


def test_default_panel_for_generic_becomes_the_composite_panel() -> None:
    panel = default_panel_for("generic")
    assert panel["panel_id"] == "composite"
    assert [b["id"] for b in panel["blocks"]] == ["status", "notes", "checklist", "activity"]


def test_default_panel_for_a_custom_panel_keeps_the_id_and_has_no_blocks() -> None:
    assert default_panel_for("insurance_notebook") == {
        "panel_id": "insurance_notebook",
        "layout": "side",
        "blocks": [],
    }


def test_migration_produces_a_valid_v2_agent_config() -> None:
    migrated = agent_config_v1_to_v2(_v1_config(), "generic")
    config = AgentConfig.model_validate(migrated)
    assert config.v == 2
    assert config.panel.panel_id == "composite"
    assert len(config.panel.blocks) == len(DEFAULT_COMPOSITE_BLOCKS)


def test_migration_carries_every_v1_section_unchanged() -> None:
    source = _v1_config()
    migrated = agent_config_v1_to_v2(source, "generic")
    for key in ("instructions", "pipeline", "voice", "capabilities", "tools", "knowledge"):
        assert migrated[key] == source[key]
    assert migrated["timezone"] == "Europe/Berlin"
    assert migrated["pack_settings"] == {"a": 1}


def test_migration_never_mutates_its_input() -> None:
    source = _v1_config()
    agent_config_v1_to_v2(source, "generic")
    assert source["v"] == 1
    assert "panel" not in source


def test_migration_is_idempotent() -> None:
    once = agent_config_v1_to_v2(_v1_config(), "generic")
    assert agent_config_v1_to_v2(once, "generic") == once


def test_migration_keeps_a_custom_panel_id_without_blocks() -> None:
    migrated = agent_config_v1_to_v2(_v1_config(), "insurance_notebook")
    assert migrated["panel"] == {"panel_id": "insurance_notebook", "layout": "side", "blocks": []}


@pytest.mark.parametrize("pack", ["generic", "insurance_claim"])
def test_migration_round_trips_the_shipped_pack_manifests(pack: str) -> None:
    """Both manifests' recommended pipelines survive v1 → v2 → v1."""
    manifests = {
        "generic": ("generic", {"mode": "cascaded", "stt": {"provider_id": "livekit-inference-stt"}}),
        "insurance_claim": (
            "insurance_notebook",
            {
                "mode": "cascaded",
                "stt": {"provider_id": "livekit-inference-stt"},
                "llm": {"provider_id": "livekit-inference-llm"},
                "tts": {"provider_id": "livekit-inference-tts"},
                "image_gen": {"provider_id": "google-image-gen"},
            },
        ),
    }
    panel_id, pipeline = manifests[pack]
    source = _v1_config(pipeline)
    upgraded = agent_config_v1_to_v2(source, panel_id)
    AgentConfig.model_validate(upgraded)
    assert agent_config_v2_to_v1(upgraded) == source


def test_downgrade_drops_every_v2_only_section() -> None:
    upgraded = agent_config_v1_to_v2(_v1_config(), "generic")
    upgraded["recording"] = {"enabled": True}
    downgraded = agent_config_v2_to_v1(upgraded)
    assert downgraded["v"] == 1
    for key in ("panel", "recording", "qa", "flow"):
        assert key not in downgraded


def test_downgrade_degrades_half_cascade_to_realtime() -> None:
    config = _v1_config({"mode": "half_cascade", "realtime": {"provider_id": "google-realtime"}})
    downgraded = agent_config_v2_to_v1(agent_config_v1_to_v2(config, "generic"))
    assert downgraded["pipeline"]["mode"] == "realtime"


def test_downgrade_drops_v2_only_pipeline_slots_and_flags() -> None:
    upgraded = agent_config_v1_to_v2(_v1_config(), "generic")
    upgraded["pipeline"]["vad"] = {"provider_id": "silero-vad"}
    upgraded["voice"]["first_speaker"] = "user"
    upgraded["capabilities"]["dtmf"] = True
    downgraded = agent_config_v2_to_v1(upgraded)
    assert "vad" not in downgraded["pipeline"]
    assert "first_speaker" not in downgraded["voice"]
    assert "dtmf" not in downgraded["capabilities"]


# ------------------------------------------------------------------------------ flows


def _valid_flow() -> FlowSpec:
    return FlowSpec(
        nodes=[
            StartNode(id="start", label="Start"),
            AgentNode(id="collect", label="Collect", instructions="Ask for the policy number."),
            EndNode(id="done", label="Done", disposition="completed"),
            GlobalNode(id="global", instructions="Be polite."),
            QaNode(id="qa"),
        ],
        edges=[
            FlowEdge(id="e1", source="start", target="collect", condition="always"),
            FlowEdge(id="e2", source="collect", target="done", condition="the policy is verified"),
        ],
    )


def test_a_well_formed_flow_validates() -> None:
    flow = _valid_flow()
    assert [n.id for n in flow.nodes][:2] == ["start", "collect"]


def test_an_empty_flow_is_allowed_as_a_draft() -> None:
    assert FlowSpec().nodes == []


def test_edge_tool_name_follows_the_runtime_contract() -> None:
    assert edge_tool_name("collect") == "go_to_collect"


def test_flow_rejects_a_missing_start_node() -> None:
    with pytest.raises(ValidationError, match="exactly one start node"):
        FlowSpec(nodes=[AgentNode(id="collect")])


def test_flow_rejects_two_start_nodes() -> None:
    with pytest.raises(ValidationError, match="exactly one start node"):
        FlowSpec(nodes=[StartNode(id="start"), StartNode(id="start2")])


def test_flow_rejects_two_global_nodes() -> None:
    with pytest.raises(ValidationError, match="at most one global node"):
        FlowSpec(nodes=[StartNode(id="start"), GlobalNode(id="g1"), GlobalNode(id="g2")])


def test_flow_rejects_an_edge_with_an_unknown_endpoint() -> None:
    with pytest.raises(ValidationError, match="unknown target"):
        FlowSpec(
            nodes=[StartNode(id="start")],
            edges=[FlowEdge(id="e1", source="start", target="nowhere")],
        )


def test_flow_rejects_an_edge_attached_to_a_global_node() -> None:
    with pytest.raises(ValidationError, match="may not attach to a global node"):
        FlowSpec(
            nodes=[StartNode(id="start"), GlobalNode(id="g1")],
            edges=[FlowEdge(id="e1", source="start", target="g1")],
        )


def test_flow_rejects_an_edge_attached_to_a_qa_node() -> None:
    with pytest.raises(ValidationError, match="may not attach to a qa node"):
        FlowSpec(
            nodes=[StartNode(id="start"), QaNode(id="qa")],
            edges=[FlowEdge(id="e1", source="start", target="qa")],
        )


def test_flow_rejects_an_outgoing_edge_from_an_end_node() -> None:
    with pytest.raises(ValidationError, match="may not have outgoing edges"):
        FlowSpec(
            nodes=[StartNode(id="start"), EndNode(id="done"), AgentNode(id="collect")],
            edges=[
                FlowEdge(id="e1", source="start", target="done"),
                FlowEdge(id="e2", source="done", target="collect"),
            ],
        )


def test_flow_rejects_an_agent_node_unreachable_from_start() -> None:
    with pytest.raises(ValidationError, match="unreachable from start"):
        FlowSpec(nodes=[StartNode(id="start"), AgentNode(id="orphan")])


def test_flow_rejects_duplicate_node_ids() -> None:
    with pytest.raises(ValidationError, match="duplicate node id"):
        FlowSpec(nodes=[StartNode(id="start"), AgentNode(id="start")])


@pytest.mark.parametrize("node_id", ["Start", "1start", "with-dash", "a" * 33, ""])
def test_flow_rejects_node_ids_outside_the_tool_name_pattern(node_id: str) -> None:
    with pytest.raises(ValidationError):
        AgentNode(id=node_id)


@pytest.mark.parametrize("name", ["Policy", "1var", "with-dash", "a" * 65])
def test_flow_rejects_variable_names_outside_the_pattern(name: str) -> None:
    from lkap_contracts.flow import VariableSpec

    with pytest.raises(ValidationError):
        VariableSpec(name=name)


def test_flow_nodes_discriminate_on_kind_when_parsed_from_json() -> None:
    parsed = FlowSpec.model_validate_json(_valid_flow().model_dump_json())
    assert [type(n).__name__ for n in parsed.nodes][:3] == ["StartNode", "AgentNode", "EndNode"]


def test_a_flow_can_be_attached_to_an_agent_config() -> None:
    config = AgentConfig(instructions="hi", pipeline=PipelineConfig(), flow=_valid_flow())
    assert config.flow is not None
    assert AgentConfig.model_validate_json(config.model_dump_json()).flow == config.flow


# ----------------------------------------------------------------------- panel blocks


def _split_path(path: str) -> list[str]:
    """Split a JSON-pointer-style patch path, mirroring ``web/src/lib/ui-state.ts``."""
    trimmed = path[1:] if path.startswith("/") else path
    return [] if trimmed == "" else trimmed.split("/")


def apply_op(state: dict[str, Any], op: UiPatchOp) -> dict[str, Any]:
    """Apply one patch op to a state dict, mirroring the web reducer's semantics.

    Args:
        state: The current state tree (mutated in place, then returned).
        op: The op to apply.

    Returns:
        The same tree, with the op applied.
    """
    segments = _split_path(op.path)
    cursor: dict[str, Any] = state
    for segment in segments[:-1]:
        nxt = cursor.get(segment)
        if not isinstance(nxt, dict):
            nxt = {}
            cursor[segment] = nxt
        cursor = nxt
    leaf = segments[-1]
    match op.op:
        case "set":
            cursor[leaf] = op.value
        case "append":
            cursor.setdefault(leaf, []).append(op.value)
        case "upsert":
            items = cursor.setdefault(leaf, [])
            key = op.key or (op.value or {}).get("id")
            for index, item in enumerate(items):
                if key is not None and key in (item.get("key"), item.get("id")):
                    items[index] = op.value
                    break
            else:
                items.append(op.value)
        case "remove":
            if op.key is not None:
                cursor[leaf] = [i for i in cursor.get(leaf, []) if op.key not in (i.get("key"), i.get("id"))]
            else:
                cursor.pop(leaf, None)
    return state


def test_ui_state_defaults_to_v2_with_an_empty_blocks_map() -> None:
    state = UiState()
    assert state.v == 2
    assert state.blocks == {}


def test_ui_state_still_accepts_a_v1_envelope() -> None:
    assert UiState.model_validate({"v": 1, "notes": []}).v == 1


def test_ui_state_patch_on_a_block_applies_in_the_reducer_fixture() -> None:
    state = UiState().model_dump()
    patch = UiPatch(
        seq=2,
        session_id="s1",
        ops=[
            UiPatchOp(op="set", path="/blocks/x", value={"status": "requested"}),
            UiPatchOp(op="set", path="/blocks/x/status", value="submitted"),
        ],
    )
    for op in patch.ops:
        state = apply_op(state, op)
    assert state["blocks"]["x"] == {"status": "submitted"}
    assert UiState.model_validate(state).blocks["x"]["status"] == "submitted"


def test_ui_state_patch_can_append_and_remove_rows_of_a_table_block() -> None:
    state = UiState(blocks={"claims": {"rows": []}}).model_dump()
    apply_op(state, UiPatchOp(op="append", path="/blocks/claims/rows", value={"id": "r1"}))
    apply_op(state, UiPatchOp(op="upsert", path="/blocks/claims/rows", value={"id": "r1", "n": 2}))
    assert state["blocks"]["claims"]["rows"] == [{"id": "r1", "n": 2}]
    apply_op(state, UiPatchOp(op="remove", path="/blocks/claims/rows", key="r1"))
    assert state["blocks"]["claims"]["rows"] == []


def test_block_spec_orders_a_composite_panel() -> None:
    panel = PanelLayout(
        blocks=[
            BlockSpec(id="form", type="form", order=1),
            BlockSpec(id="status", type="status", order=0),
        ]
    )
    assert [b.id for b in sorted(panel.blocks, key=lambda b: b.order)] == ["status", "form"]


def test_form_block_state_exposes_schema_under_its_wire_name() -> None:
    state = FormBlockState.model_validate({"schema": {"type": "object"}, "status": "requested"})
    assert state.schema_ == {"type": "object"}
    assert "schema" in state.model_dump(by_alias=True)


def test_table_block_state_holds_columns_and_rows() -> None:
    state = TableBlockState(columns=[TableColumn(key="policy", label="Policy")], rows=[{"policy": "P-1"}])
    assert state.columns[0].type == "string"
    assert state.rows == [{"policy": "P-1"}]


def test_kb_citations_block_state_carries_hits() -> None:
    state = KbCitationsBlockState(
        items=[KbCitation(chunk_id="c1", filename="policy.md", score=0.9, text="...")]
    )
    assert state.items[0].filename == "policy.md"


def test_open_dialog_payload_uses_the_dialog_key() -> None:
    """R-V2-3b: the fixed key is ``dialog``; ``id`` was the agent-side mistake."""
    from lkap_contracts.ui_protocol import UiRequest

    request = UiRequest(method="open_dialog", payload={"dialog": "packet", "params": {"page": 2}})
    assert request.payload["dialog"] == "packet"


@pytest.mark.parametrize("method", ["form", "show_block", "navigate"])
def test_ui_request_accepts_the_new_v2_methods(method: str) -> None:
    from lkap_contracts.ui_protocol import UiRequest

    assert UiRequest(method=method, payload={}).method == method  # type: ignore[arg-type]


@pytest.mark.parametrize("action", ["form_submit", "block_action", "rewind", "inject_user_text"])
def test_agent_action_accepts_the_new_v2_actions(action: str) -> None:
    from lkap_contracts.ui_protocol import AgentAction

    assert AgentAction(action=action).action == action  # type: ignore[arg-type]


# ---------------------------------------------------------------------------- pricing


def test_price_table_filled_by_v2_05_with_sourced_entries() -> None:
    """V2-05 fills `PRICES`; every entry must carry a verifiable source and date."""
    assert pricing.PRICES, "V2-05 should have added at least one sourced price"
    assert pricing.PRICE_VERSION
    for price in pricing.PRICES:
        assert price.source_url.startswith("https://")
        assert price.as_of


def test_every_price_provider_id_is_a_known_registry_entry() -> None:
    for price in pricing.PRICES:
        assert price.provider_id in {spec.id for spec in REGISTRY}


def test_lookup_returns_none_for_an_unknown_price() -> None:
    assert pricing.lookup("openai-llm", "gpt-x", "tokens_in") is None


def test_lookup_prefers_a_model_specific_price(monkeypatch: pytest.MonkeyPatch) -> None:
    generic = Price(
        provider_id="openai-llm", unit="tokens_in", usd_per_unit="0.001", source_url="u", as_of="2026-01-01"
    )
    specific = Price(
        provider_id="openai-llm",
        model="gpt-5",
        unit="tokens_in",
        usd_per_unit="0.002",
        source_url="u",
        as_of="2026-01-01",
    )
    monkeypatch.setattr(pricing, "PRICES", [generic, specific])
    assert pricing.lookup("openai-llm", "gpt-5", "tokens_in") is specific
    assert pricing.lookup("openai-llm", "other", "tokens_in") is generic


def test_lookup_respects_the_unit(monkeypatch: pytest.MonkeyPatch) -> None:
    price = Price(
        provider_id="elevenlabs-tts",
        unit="chars",
        usd_per_unit="0.0001",
        source_url="u",
        as_of="2026-01-01",
    )
    monkeypatch.setattr(pricing, "PRICES", [price])
    assert pricing.lookup("elevenlabs-tts", None, "chars") is price
    assert pricing.lookup("elevenlabs-tts", None, "minutes") is None


# ------------------------------------------------------------------------------ fleet


def test_fleet_desired_only_describes_supervised_pools() -> None:
    desired = FleetDesired(connection_id="c1", desired_hash="h")
    assert desired.deployment_mode == "supervised"
    assert desired.desired_replicas == 0


def test_worker_env_defaults_are_empty_so_no_secret_is_implied() -> None:
    assert WorkerEnv().env == {}
