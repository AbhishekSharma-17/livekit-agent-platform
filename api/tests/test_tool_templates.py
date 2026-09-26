"""Tool templates (V5-25, D-V5-36): the Cal.com set, `GET /v1/tool-templates`, instantiation."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
from conftest import create_agent
from lkap_contracts.tools import HttpToolDefinition, ToolTemplate

from lkap_api.templates.tools import (
    ToolTemplateError,
    group_templates,
    instantiate_definition,
    load_tool_templates,
    tool_template,
)

CAL_NAMES = [
    "booking_check_availability",
    "booking_create",
    "booking_list",
    "booking_get",
    "booking_reschedule",
    "booking_cancel",
]


async def _cal_key(admin_client: httpx.AsyncClient, secrets: dict[str, str] | None = None) -> str:
    response = await admin_client.post(
        "/v1/credentials",
        json={
            "provider_id": "http-tool-secret",
            "label": "Cal.com",
            "secrets": secrets if secrets is not None else {"CAL_API_KEY": "cal_test_not_real"},
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


# ------------------------------------------------------------------ the catalogue
def test_the_cal_com_set_has_the_six_booking_tools_in_order() -> None:
    assert [t.definition.name for t in group_templates("cal_com")] == CAL_NAMES
    assert [t.id for t in load_tool_templates()][:6] == [f"cal_com.{n}" for n in CAL_NAMES]


@pytest.mark.parametrize("template", group_templates("cal_com"), ids=lambda t: t.id)
def test_each_cal_com_template_is_a_valid_https_tool_on_its_allowed_host(template: ToolTemplate) -> None:
    definition = template.definition
    assert urlsplit(definition.url).scheme == "https"
    assert urlsplit(definition.url).hostname in definition.allowed_hosts == ["api.cal.com"]
    assert definition.headers["Authorization"] == "Bearer {{ secret.CAL_API_KEY }}"
    assert definition.headers["cal-api-version"]
    assert template.secret_names == ["CAL_API_KEY"]
    assert definition.credential_id is None
    assert definition.max_result_chars <= 1500


@pytest.mark.parametrize(
    "template",
    [t for t in group_templates("cal_com") if "time_zone" in t.definition.parameters["properties"]],
    ids=lambda t: t.id,
)
def test_the_time_zone_argument_is_the_callers_zone(template: ToolTemplate) -> None:
    """R-V5-10: Cal.com gets the caller's zone; the model reads it from the time note."""
    schema = template.definition.parameters["properties"]["time_zone"]
    assert "caller's IANA time zone" in schema["description"]
    assert "time_zone" in template.definition.parameters["required"]
    assert "timeZone" in (template.definition.url + (template.definition.body_template or ""))


def test_writes_block_and_reads_run_auto_where_set() -> None:
    by_name = {t.definition.name: t for t in group_templates("cal_com")}
    for name in ("booking_create", "booking_reschedule", "booking_cancel"):
        assert by_name[name].risk == "write"
        assert by_name[name].definition.method == "POST"
        assert by_name[name].definition.execution.mode is None  # POST: blocking unless set
    assert by_name["booking_check_availability"].definition.execution.mode == "auto"


def test_the_event_type_is_an_argument_default_the_admin_must_give() -> None:
    create = tool_template("cal_com.booking_create")
    assert create is not None
    [default] = create.defaults
    assert default.name == "event_type_id" and default.required


def test_instantiate_definition_turns_defaults_into_schema_defaults() -> None:
    template = tool_template("cal_com.booking_check_availability")
    assert template is not None

    definition = instantiate_definition(template, {"event_type_id": "123456"}, credential_id="cred")

    properties = definition.parameters["properties"]
    assert properties["event_type_id"]["default"] == 123456
    assert "event_type_id" not in definition.parameters["required"]
    assert definition.credential_id == "cred"
    # The catalogue's own copy is untouched.
    assert "default" not in template.definition.parameters["properties"]["event_type_id"]


@pytest.mark.parametrize(
    ("value", "ok"), [(7, True), ("42", True), ("abc", False), (True, False), (1.5, False)]
)
def test_an_integer_default_is_coerced_or_refused(value: Any, ok: bool) -> None:
    template = tool_template("cal_com.booking_create")
    assert template is not None
    if ok:
        definition = instantiate_definition(template, {"event_type_id": value}, credential_id="c")
        assert isinstance(definition.parameters["properties"]["event_type_id"]["default"], int)
    else:
        with pytest.raises(ToolTemplateError, match="must be of type integer"):
            instantiate_definition(template, {"event_type_id": value}, credential_id="c")


def test_a_missing_required_default_is_refused() -> None:
    template = tool_template("cal_com.booking_create")
    assert template is not None
    with pytest.raises(ToolTemplateError, match="needs a value for 'event_type_id'"):
        instantiate_definition(template, {}, credential_id="c")


def test_every_instantiated_template_is_a_valid_http_tool() -> None:
    for template in load_tool_templates():
        definition = instantiate_definition(template, {"event_type_id": 1}, credential_id="c")
        HttpToolDefinition.model_validate(definition.model_dump(mode="json"))


# ------------------------------------------------------------------ the routes
async def test_the_list_route_returns_the_templates(admin_client: httpx.AsyncClient) -> None:
    response = await admin_client.get("/v1/tool-templates")

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [item["id"] for item in items][:6] == [f"cal_com.{n}" for n in CAL_NAMES]
    assert items[0]["group_label"] == "Cal.com bookings"


async def test_the_starter_template_routes_still_answer(admin_client: httpx.AsyncClient) -> None:
    response = await admin_client.get("/v1/templates")

    assert response.status_code == 200, response.text
    assert response.json()["items"]


async def test_instantiating_the_group_creates_six_http_tools(admin_client: httpx.AsyncClient) -> None:
    key = await _cal_key(admin_client)
    agent = await create_agent(admin_client, name="Booker")

    response = await admin_client.post(
        "/v1/tool-templates/cal_com/instantiate",
        json={"credential_id": key, "defaults": {"event_type_id": 98765}, "agent_id": agent["id"]},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["names"] == CAL_NAMES
    assert body["template_ids"] == [f"cal_com.{n}" for n in CAL_NAMES]
    listed = (await admin_client.get("/v1/tools", params={"agent_id": agent["id"]})).json()["items"]
    by_name = {row["name"]: row for row in listed}
    assert set(by_name) == set(CAL_NAMES)
    create = by_name["booking_create"]["definition"]
    assert create["credential_id"] == key
    assert create["parameters"]["properties"]["event_type_id"]["default"] == 98765
    assert create["allowed_hosts"] == ["api.cal.com"]
    assert "{{ secret.CAL_API_KEY }}" in create["headers"]["Authorization"]


async def test_instantiating_one_template_or_a_subset(admin_client: httpx.AsyncClient) -> None:
    key = await _cal_key(admin_client)

    one = await admin_client.post(
        "/v1/tool-templates/cal_com.booking_get/instantiate", json={"credential_id": key}
    )
    subset = await admin_client.post(
        "/v1/tool-templates/cal_com/instantiate",
        json={"credential_id": key, "names": ["booking_list", "booking_get"]},
    )

    assert one.status_code == 201, one.text
    assert one.json()["names"] == ["booking_get"]
    assert subset.status_code == 201, subset.text
    assert subset.json()["names"] == ["booking_list", "booking_get"]


@pytest.mark.parametrize(
    ("path", "body", "status", "fragment"),
    [
        ("/v1/tool-templates/nope/instantiate", {}, 404, "unknown tool template"),
        ("/v1/tool-templates/cal_com/instantiate", {"names": ["booking_fly"]}, 422, "unknown template name"),
        (
            "/v1/tool-templates/cal_com.booking_get/instantiate",
            {"names": ["booking_get"]},
            422,
            "names applies",
        ),
        ("/v1/tool-templates/cal_com/instantiate", {"defaults": {"colour": "red"}}, 422, "not arguments"),
        ("/v1/tool-templates/cal_com/instantiate", {}, 422, "needs a value for 'event_type_id'"),
    ],
)
async def test_instantiation_refusals(
    admin_client: httpx.AsyncClient, path: str, body: dict[str, Any], status: int, fragment: str
) -> None:
    key = await _cal_key(admin_client)

    response = await admin_client.post(path, json={"credential_id": key, **body})

    assert response.status_code == status, response.text
    assert fragment in response.text


async def test_a_key_without_cal_api_key_is_refused_by_the_tool_checks(
    admin_client: httpx.AsyncClient,
) -> None:
    key = await _cal_key(admin_client, {"OTHER": "x"})

    response = await admin_client.post(
        "/v1/tool-templates/cal_com.booking_get/instantiate", json={"credential_id": key}
    )

    assert response.status_code == 422
    assert "CAL_API_KEY" in response.text


async def test_a_provider_key_cannot_back_a_template(admin_client: httpx.AsyncClient) -> None:
    response = await admin_client.post(
        "/v1/credentials", json={"provider_id": "openai-llm", "label": "o", "secrets": {"api_key": "sk-x"}}
    )
    key = response.json()["id"]

    refused = await admin_client.post(
        "/v1/tool-templates/cal_com.booking_get/instantiate", json={"credential_id": key}
    )

    assert refused.status_code == 422
    assert "http-tool-secret" in refused.text
