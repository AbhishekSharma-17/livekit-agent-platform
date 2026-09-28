"""V6-16 (D-V6-27): the ``dataset`` tool kind on the worker.

The api is mocked at the transport boundary (``httpx.MockTransport``): the tool posts the
session id, the key values and the definition's bounds to the internal lookup route, fences
the rows as ``dataset:<tool>``, applies V6-07's pinned values, required variables and
bindings, and never logs a key value.
"""

from __future__ import annotations

import json
from typing import Any, cast

import httpx
import pytest
import structlog
from fakes.fake_ctx import FakePackSessionContext, FakeRunContext, default_agent_config
from fakes.fake_room import FakeRemoteParticipant, FakeRoom
from livekit import rtc
from livekit.agents import RunContext, ToolError
from lkap_contracts.agent_config import PanelLayout
from lkap_contracts.tools import DatasetToolDefinition
from lkap_contracts.ui_protocol import BlockSpec

from lkap_agent.tools.context import VARIABLES_USERDATA_KEY, ToolCallContext, uses_tool_context
from lkap_agent.tools.dataset import build_dataset_tool, dataset_parameters
from lkap_agent.tools.declarative import HTTP_STATUS_EXTRA, build_http_tools
from lkap_agent.tools.execution import policy_of

PHONE = "+91 98765 43210"
ROWS = [{"policy_number": "PD-1001", "holder_name": "Demo — Asha Rao"}]


def _definition(**overrides: Any) -> DatasetToolDefinition:
    body: dict[str, Any] = {
        "kind": "dataset",
        "name": "lookup_policy",
        "description": "Find the caller's policy.",
        "dataset_id": "0123456789abcdef0123456789abcdef",
        "key_columns": ["phone", "policy_number"],
        "return_columns": ["policy_number", "holder_name"],
        "max_rows": 3,
    }
    body.update(overrides)
    return DatasetToolDefinition.model_validate(body)


def _session(*, phone: str | None = None, **variables: Any) -> FakePackSessionContext:
    room = FakeRoom()
    room.add_remote_participant(
        FakeRemoteParticipant(
            "sip-caller",
            attributes={"sip.phoneNumber": phone} if phone else {},
            kind=rtc.ParticipantKind.PARTICIPANT_KIND_SIP,
        )
    )
    ctx = FakePackSessionContext(
        session_id="sess_demo",
        agent_id="agent_demo",
        config=default_agent_config(panel=PanelLayout(blocks=[BlockSpec(id="card", type="details")])),
        room=cast(rtc.Room, room),
    )
    cast(Any, ctx).channel = "sip_in"
    ctx.userdata[VARIABLES_USERDATA_KEY] = dict(variables)
    return ctx


class _Api:
    """A fake api answering the internal lookup route; records every request."""

    def __init__(self, status: int = 200, body: Any = None) -> None:
        self.status = status
        self.body = (
            {
                "dataset_id": "0123456789abcdef0123456789abcdef",
                "dataset_name": "Demo",
                "match": "exact",
                "rows": ROWS,
            }
            if body is None
            else body
        )
        self.requests: list[httpx.Request] = []

    def factory(self) -> httpx.AsyncClient:
        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(self.status, json=self.body)

        return httpx.AsyncClient(base_url="http://api.test", transport=httpx.MockTransport(handle))

    @property
    def sent(self) -> dict[str, Any]:
        payload: dict[str, Any] = json.loads(self.requests[-1].content)
        return payload


async def _call(
    definition: DatasetToolDefinition,
    api: _Api,
    session: FakePackSessionContext | None = None,
    **arguments: Any,
) -> str:
    context = ToolCallContext(session or _session(), participant_identity="sip-caller")
    tool = build_dataset_tool(definition, context=context, client_factory=api.factory)
    run = cast(RunContext[Any], FakeRunContext(name=definition.name, call_id="call-1"))
    result: str = await tool(raw_arguments=arguments, context=run)
    return result


def test_schema_offers_one_string_per_key_column_and_hides_pinned_ones() -> None:
    both = dataset_parameters(_definition())
    pinned = dataset_parameters(_definition(pinned_arguments={"phone": "{{ ctx.caller_phone }}"}))

    assert set(both["properties"]) == {"phone", "policy_number"}
    assert "required" not in both
    assert set(pinned["properties"]) == {"policy_number"}
    assert pinned["required"] == ["policy_number"]


def test_a_dataset_tool_always_gets_the_session_context_and_blocks() -> None:
    definition = _definition()

    assert uses_tool_context(definition) is True
    tool = build_dataset_tool(definition)
    assert policy_of(tool) is not None and policy_of(tool).resolved.mode == "blocking"  # type: ignore[union-attr]


async def test_lookup_posts_the_session_and_fences_the_rows() -> None:
    api = _Api(
        body={
            "dataset_id": "0123456789abcdef0123456789abcdef",
            "dataset_name": "Demo",
            "match": "exact",
            "rows": ROWS,
            "truncated": True,
        }
    )

    result = await _call(_definition(), api, policy_number=" PD-1001 ")

    request = api.requests[-1]
    assert request.url.path == "/internal/v1/datasets/0123456789abcdef0123456789abcdef/lookup"
    assert api.sent == {
        "session_id": "sess_demo",
        "keys": {"policy_number": "PD-1001"},
        "match": "exact",
        "return_columns": ["policy_number", "holder_name"],
        "max_rows": 3,
    }
    assert result.startswith('<untrusted source="dataset:lookup_policy">')
    assert "Demo — Asha Rao" in result
    assert "More than 3 records matched" in result.split("</untrusted>", 1)[1]


async def test_pinned_caller_phone_is_looked_up_and_never_logged() -> None:
    api = _Api()
    definition = _definition(pinned_arguments={"phone": "{{ ctx.caller_phone }}"})

    with structlog.testing.capture_logs() as logs:
        await _call(definition, api, _session(phone=PHONE))

    assert api.sent["keys"] == {"phone": PHONE}
    assert "98765" not in json.dumps(logs, default=str)
    assert any(entry.get("key_columns") == ["phone"] for entry in logs)


async def test_missing_values_and_variables_refuse_before_any_lookup() -> None:
    api = _Api()

    with pytest.raises(ToolError, match="phone or the policy number"):
        await _call(_definition(), api)
    with pytest.raises(ToolError, match="I need"):
        await _call(_definition(requires_vars=["claim_id"]), api, policy_number="PD-1001")
    with pytest.raises(ToolError, match="I need"):
        await _call(
            _definition(pinned_arguments={"phone": "{{ ctx.caller_phone }}"}), api, _session(phone=None)
        )

    assert api.requests == []


async def test_bindings_copy_the_found_row_onto_the_panel() -> None:
    session = _session()
    definition = _definition(bindings=[{"path": "/0/holder_name", "to": "details:card.holder"}])

    await _call(definition, _Api(), session, policy_number="PD-1001")

    assert [(row["key"], row["value"]) for row in session.ui.state.blocks["card"]["items"]] == [
        ("holder", "Demo — Asha Rao")
    ]


async def test_no_rows_is_a_plain_answer_and_binds_nothing() -> None:
    session = _session()
    definition = _definition(bindings=[{"path": "/0/holder_name", "to": "details:card.holder"}])

    result = await _call(
        definition,
        _Api(
            body={
                "dataset_id": "0123456789abcdef0123456789abcdef",
                "dataset_name": "Demo",
                "match": "exact",
                "rows": [],
            }
        ),
        session,
        policy_number="x",
    )

    assert result.startswith("No matching record")
    assert "card" not in session.ui.state.blocks


@pytest.mark.parametrize(
    ("status", "body", "fragment"),
    [
        (
            404,
            {
                "error": {
                    "code": "not_found",
                    "message": "unknown lookup table '0123456789abcdef0123456789abcdef'",
                }
            },
            "not available",
        ),
        (
            409,
            {"error": {"code": "conflict", "message": "this lookup table is still being imported"}},
            "still being imported",
        ),
        (422, {"error": {"code": "x", "message": "give at least 2 characters"}}, "at least 2 characters"),
        (500, {}, "the lookup failed"),
    ],
)
async def test_api_errors_become_spoken_safe_tool_errors(status: int, body: Any, fragment: str) -> None:
    with pytest.raises(ToolError, match=fragment):
        await _call(_definition(), _Api(status=status, body=body), policy_number="PD-1001")


async def test_a_misconfigured_row_never_looks_anything_up() -> None:
    definition = _definition().model_copy(update={"pinned_arguments": {"phone": "{{ ctx.nope }}"}})
    api = _Api()

    with pytest.raises(ToolError, match="set up incorrectly"):
        await _call(definition, api, policy_number="PD-1001")

    assert api.requests == []


async def test_the_declarative_builder_builds_a_mocked_lookup_from_its_fixture() -> None:
    (tool,) = build_http_tools(
        [_definition()], mocks={"lookup_policy": ROWS}, context=ToolCallContext(_session())
    )
    run = cast(RunContext[Any], FakeRunContext(name="lookup_policy", call_id="call-2"))

    result = await tool(raw_arguments={"policy_number": "PD-1001"}, context=run)

    assert result.startswith('<untrusted source="dataset:lookup_policy">')
    assert "PD-1001" in result


@pytest.mark.parametrize(("rows", "status"), [(ROWS, 200), ([], 404)])
async def test_the_lookup_records_found_or_not_found_on_the_call(rows: list[Any], status: int) -> None:
    """Ask #116: a flow tool step reads "found nothing" as HTTP 404 (its `empty` outcome)."""
    api = _Api(
        body={
            "dataset_id": "0123456789abcdef0123456789abcdef",
            "dataset_name": "Demo",
            "match": "exact",
            "rows": rows,
        }
    )
    tool = build_dataset_tool(_definition(), context=ToolCallContext(_session()), client_factory=api.factory)
    run = FakeRunContext(name="lookup_policy", call_id="call-3")
    extra: dict[str, Any] = {}
    cast(Any, run.function_call).extra = extra

    await tool(raw_arguments={"policy_number": "PD-1001"}, context=cast(RunContext[Any], run))

    assert extra == {HTTP_STATUS_EXTRA: status}
