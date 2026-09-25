"""V5-02: the generic blocking request (`request` / `block_submit`) in `ui_protocol`."""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from lkap_contracts.ui_protocol import (
    AgentAction,
    BlockRequestPayload,
    BlockSubmitPayload,
    FormBlockState,
    RequestableState,
    UiRequest,
    UiRequestResult,
)

#: A `form` block's state as a v2 session stored it (before `cancelled` existed).
V2_FORM_STATE_JSON = """
{
  "schema": {
    "type": "object",
    "properties": {
      "full_name": {"title": "Full name", "type": "string"},
      "date_of_loss": {"title": "Date of loss", "type": "string", "format": "date"}
    },
    "required": ["full_name"]
  },
  "values": {"full_name": "Jordan Example"},
  "status": "submitted",
  "submitted_at": 1758800000.5
}
"""


def test_ui_request_accepts_the_generic_request_method() -> None:
    request = UiRequest(method="request", payload={"block_id": "pick", "timeout_s": 60})
    assert UiRequest.model_validate_json(request.model_dump_json()).method == "request"


def test_ui_request_keeps_the_form_alias() -> None:
    assert UiRequest(method="form", payload={}).method == "form"


def test_agent_action_accepts_block_submit_and_keeps_form_submit() -> None:
    action = AgentAction(action="block_submit", payload={"block_id": "b", "values": {}})
    assert action.action == "block_submit"
    assert AgentAction(action="form_submit").action == "form_submit"


@pytest.mark.parametrize("status", ["idle", "requested", "submitted", "cancelled"])
def test_requestable_state_accepts_every_status(status: str) -> None:
    assert RequestableState.model_validate({"status": status}).status == status


def test_requestable_state_rejects_an_unknown_status() -> None:
    with pytest.raises(ValidationError):
        RequestableState.model_validate({"status": "pending"})


def test_requestable_state_defaults_to_idle_and_unsubmitted() -> None:
    state = RequestableState()
    assert (state.status, state.submitted_at) == ("idle", None)


def test_form_block_state_is_requestable_and_accepts_cancelled() -> None:
    assert issubclass(FormBlockState, RequestableState)
    state = FormBlockState.model_validate({"schema": {"type": "object"}, "status": "cancelled"})
    assert state.status == "cancelled"
    assert state.model_dump(by_alias=True)["schema"] == {"type": "object"}


def test_form_block_state_from_an_existing_session_still_validates() -> None:
    """Compatibility: JSON a v2 session stored round-trips unchanged."""
    raw: dict[str, Any] = json.loads(V2_FORM_STATE_JSON)
    state = FormBlockState.model_validate(raw)
    assert state.status == "submitted"
    assert state.model_dump(mode="json", by_alias=True) == raw


def test_form_block_state_json_schema_lists_the_cancelled_status() -> None:
    status = FormBlockState.model_json_schema(by_alias=True)["properties"]["status"]
    assert status["enum"] == ["idle", "requested", "submitted", "cancelled"]


def test_block_request_payload_carries_block_timeout_and_optional_schema() -> None:
    payload = BlockRequestPayload.model_validate(
        {"block_id": "f", "timeout_s": 30, "schema": {"type": "object"}}
    )
    assert payload.model_dump(by_alias=True, exclude_none=True) == {
        "block_id": "f",
        "timeout_s": 30.0,
        "schema": {"type": "object"},
    }
    assert "schema" not in BlockRequestPayload(block_id="f", timeout_s=1).model_dump(
        by_alias=True, exclude_none=True
    )


@pytest.mark.parametrize("bad", [{"block_id": "", "timeout_s": 1}, {"block_id": "f", "timeout_s": 0}])
def test_block_request_payload_rejects_bad_values(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        BlockRequestPayload.model_validate(bad)


def test_block_submit_payload_accepts_values_or_cancelled() -> None:
    assert BlockSubmitPayload.model_validate({"block_id": "b", "values": {"a": 1}}).values == {"a": 1}
    cancelled = BlockSubmitPayload.model_validate({"block_id": "b", "cancelled": True})
    assert cancelled.cancelled is True and cancelled.values is None


@pytest.mark.parametrize(
    "bad",
    [
        {"block_id": "b"},
        {"block_id": "b", "values": {}, "cancelled": True},
        {"block_id": "b", "values": "x"},
        {"block_id": "", "values": {}},
    ],
)
def test_block_submit_payload_needs_exactly_one_answer(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        BlockSubmitPayload.model_validate(bad)


def test_ui_request_result_is_unchanged() -> None:
    assert set(UiRequestResult.model_fields) == {"ok", "payload"}
