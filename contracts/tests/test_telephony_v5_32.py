"""V5-32 contracts: answering-machine detection, transfer modes, the handoff block, call fields."""

from __future__ import annotations

from typing import get_args

import pytest
from pydantic import ValidationError

from lkap_contracts.agent_config import AgentConfig, ResolvedAgentConfig
from lkap_contracts.api_models import CallOut, CallReportIn, TransferEvent, VoicemailEvent
from lkap_contracts.blocks import BLOCK_CONFIG_MODELS, HandoffBlockConfig, validate_block_config
from lkap_contracts.telephony import (
    AMD_MACHINE_RESULTS,
    AMD_RESULTS,
    MAX_AMD_MESSAGE_CHARS,
    MAX_TRANSFER_SUMMARY_CHARS,
    AmdConfig,
    AmdResult,
    TelephonyConfig,
    TransferTarget,
    WarmTransferRoute,
)
from lkap_contracts.ui_protocol import BlockSpec, BlockType, HandoffBlockState, HandoffStatus


def test_amd_is_off_by_default_and_hangs_up_on_machines() -> None:
    amd = TelephonyConfig().amd
    assert amd == AmdConfig()
    assert amd.enabled is False
    assert amd.on_machine == "hangup"
    assert amd.message is None
    assert amd.ivr_detection is False


def test_a_telephony_config_saved_before_v5_32_validates_unchanged() -> None:
    stored = {"transfer_targets": [{"label": "Claims desk", "to": "+15551230000"}], "sms_targets": []}
    config = TelephonyConfig.model_validate(stored)
    assert config.transfer_targets[0].mode == "cold"
    assert config.amd.enabled is False
    # The dump gains only the new defaults.
    dumped = config.model_dump()
    assert dumped["transfer_targets"][0] == {"label": "Claims desk", "to": "+15551230000", "mode": "cold"}


def test_an_agent_config_without_the_new_keys_resolves_to_the_old_behaviour() -> None:
    config = AgentConfig.model_validate({"instructions": "Hi", "pipeline": {}})
    assert config.telephony.amd.enabled is False
    assert all(target.mode == "cold" for target in config.telephony.transfer_targets)


@pytest.mark.parametrize("mode", ["cold", "warm"])
def test_transfer_target_accepts_both_modes(mode: str) -> None:
    assert TransferTarget(label="Desk", to="+15551230000", mode=mode).mode == mode  # type: ignore[arg-type]


def test_transfer_target_refuses_an_unknown_mode() -> None:
    with pytest.raises(ValidationError):
        TransferTarget.model_validate({"label": "Desk", "to": "+15551230000", "mode": "hot"})


def test_amd_message_is_bounded() -> None:
    AmdConfig(enabled=True, on_machine="leave_message", message="x" * MAX_AMD_MESSAGE_CHARS)
    with pytest.raises(ValidationError):
        AmdConfig(message="x" * (MAX_AMD_MESSAGE_CHARS + 1))
    with pytest.raises(ValidationError):
        AmdConfig.model_validate({"on_machine": "record"})


def test_amd_results_mirror_the_sdk_categories() -> None:
    # livekit-agents 1.8.3 `AMDCategory` values (the worker's tripwire pins the SDK side).
    assert tuple(get_args(AmdResult)) == AMD_RESULTS
    assert AMD_MACHINE_RESULTS == {"machine-ivr", "machine-vm", "machine-unavailable"}
    assert AMD_MACHINE_RESULTS < set(AMD_RESULTS)


def test_handoff_is_a_block_type_with_a_strict_config() -> None:
    assert "handoff" in get_args(BlockType)
    assert BLOCK_CONFIG_MODELS["handoff"] is HandoffBlockConfig
    assert HandoffBlockConfig() == HandoffBlockConfig(show_queue=True, show_agent_name=True)
    ok = BlockSpec(id="handoff", type="handoff", config={"show_queue": False})
    assert validate_block_config(ok) == []
    bad = BlockSpec(id="handoff", type="handoff", config={"queue_size": 3})
    assert [issue.path for issue in validate_block_config(bad)] == ["config.queue_size"]


def test_handoff_state_walks_the_documented_states() -> None:
    assert get_args(HandoffStatus) == ("idle", "requested", "connecting", "connected", "timeout", "ended")
    state = HandoffBlockState()
    assert state.status == "idle" and state.mode is None and state.target is None
    connected = HandoffBlockState(status="connected", mode="warm", target="Claims desk", agent_name="Sam")
    assert connected.model_dump()["agent_name"] == "Sam"
    with pytest.raises(ValidationError):
        HandoffBlockState(queue_position=-1)


def test_call_out_carries_the_amd_and_transfer_fields_with_none_defaults() -> None:
    out = CallOut(id="c1", direction="outbound")
    assert (out.amd_result, out.transfer_mode, out.transfer_summary) == (None, None, None)
    filled = CallOut(
        id="c1", direction="outbound", amd_result="machine-vm", transfer_mode="warm", transfer_summary="s"
    )
    assert filled.amd_result == "machine-vm"


def test_call_report_carries_the_verdict_and_the_transfer() -> None:
    report = CallReportIn(session_id="s1", status="answered", amd_result="human")
    assert report.amd_result == "human"
    moved = CallReportIn(
        session_id="s1",
        status="transferred",
        transfer_mode="warm",
        transfer_to="+15551230000",
        transfer_summary="Caller wants to change a claim.",
    )
    assert moved.status == "transferred"
    with pytest.raises(ValidationError):
        CallReportIn(session_id="s1", status="answered", amd_result="fax")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        too_long = "x" * (MAX_TRANSFER_SUMMARY_CHARS + 1)
        CallReportIn(session_id="s1", status="transferred", transfer_summary=too_long)


def test_the_session_event_payloads() -> None:
    event = VoicemailEvent(result="machine-vm", action="leave_message", message_left=True)
    assert event.model_dump() == {"result": "machine-vm", "action": "leave_message", "message_left": True}
    # A V2-17 transfer event (no mode keys) still reads.
    old = TransferEvent.model_validate({"to": "+15551230000", "ok": True, "status": "transferred"})
    assert (old.mode, old.requested_mode, old.outcome) == ("cold", "cold", None)
    fell_back = TransferEvent(
        to="+15551230000",
        ok=True,
        status="transferred",
        mode="cold",
        requested_mode="warm",
        target="Claims desk",
        outcome="transferred",
        summary="Wants a claim update.",
    )
    assert fell_back.requested_mode == "warm"


def test_the_resolved_document_has_no_warm_route_by_default() -> None:
    fields = ResolvedAgentConfig.model_fields
    assert fields["warm_transfer"].default is None
    route = WarmTransferRoute(trunk_id="ST_out_1", caller_id="+15551230000", targets=["+15551239999"])
    assert route.targets == ["+15551239999"]
    with pytest.raises(ValidationError):
        WarmTransferRoute(trunk_id="")
