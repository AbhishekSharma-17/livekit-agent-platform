"""V5-02: the generic blocking request (`request` / `block_submit`) in `ui_protocol`."""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from lkap_contracts.ui_protocol import (
    AgentAction,
    BlockRequestPayload,
    BlockSpec,
    BlockSubmitPayload,
    ChoiceOption,
    ChoicesBlockState,
    DetailsBlockState,
    DetailsItem,
    FormBlockState,
    KbCitation,
    MarkdownBlockState,
    RequestableState,
    StepItem,
    StepsBlockState,
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


# ------------------------------------------------------------- V5-08: the block quartet


def test_choices_block_state_is_requestable_and_holds_selected_ids() -> None:
    state = ChoicesBlockState.model_validate(
        {
            "prompt": "Was anyone injured?",
            "options": [
                {"id": "no", "label": "No"},
                {"id": "minor", "label": "Yes, minor", "tone": "warning"},
            ],
            "selected": ["no"],
            "status": "submitted",
            "submitted_at": 1.0,
        }
    )
    assert isinstance(state, RequestableState)
    assert state.selected == ["no"] and state.options[1].tone == "warning"
    assert ChoicesBlockState().model_dump() == {
        "status": "idle",
        "submitted_at": None,
        "prompt": "",
        "options": [],
        "multi": False,
        "selected": [],
        "reveal": None,
    }


def test_choice_option_ids_are_non_empty() -> None:
    with pytest.raises(ValidationError):
        ChoiceOption(id="", label="x")


def test_details_items_carry_text_numbers_or_nothing() -> None:
    state = DetailsBlockState.model_validate(
        {
            "items": [
                {"key": "claim_no", "label": "Claim no.", "value": "CLM-1"},
                {"key": "amount", "label": "Amount", "value": 1250.5, "type": "money"},
                {"key": "date", "label": "Date", "value": None, "type": "date"},
            ]
        }
    )
    assert [i.value for i in state.items] == ["CLM-1", 1250.5, None]
    with pytest.raises(ValidationError):
        DetailsItem(key="a", label="A", type="colour")  # type: ignore[arg-type]


def test_markdown_block_state_defaults_to_empty_text() -> None:
    assert MarkdownBlockState().model_dump() == {"markdown": "", "title": None, "updated_at": None}


@pytest.mark.parametrize("status", ["pending", "active", "done", "skipped", "failed"])
def test_step_item_accepts_every_status(status: str) -> None:
    assert StepItem.model_validate({"id": "a", "label": "A", "status": status}).status == status


def test_step_item_rejects_an_unknown_status() -> None:
    with pytest.raises(ValidationError):
        StepItem.model_validate({"id": "a", "label": "A", "status": "later"})


def test_steps_block_state_defaults() -> None:
    assert StepsBlockState().model_dump() == {"steps": [], "current": None}


def test_kb_citation_locators_are_optional() -> None:
    old = KbCitation.model_validate({"chunk_id": "c", "filename": "f.pdf", "score": 0.5, "text": "t"})
    assert old.document_id is None and old.page is None and old.heading_path is None
    new = KbCitation(
        chunk_id="c",
        filename="f.pdf",
        score=0.5,
        text="t",
        document_id="d1",
        page=3,
        heading_path=["Policy", "Exclusions"],
        char_start=10,
        char_end=40,
    )
    assert KbCitation.model_validate_json(new.model_dump_json()) == new


def test_the_four_new_block_types_are_block_types() -> None:
    for block_type in ("choices", "details", "markdown", "steps"):
        assert BlockSpec(id="b", type=block_type).type == block_type  # type: ignore[arg-type]
