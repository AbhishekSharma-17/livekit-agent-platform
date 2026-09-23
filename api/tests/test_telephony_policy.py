"""Adversarial tests for the outbound dialing policy (V2-21 card, rulings R-V2-23 and R-V2-25).

Each test is one attack from the V2-21 card, run against the real routes with
LiveKit faked at ``ConnectionClientFactory.api`` (``test_telephony``'s world):

* a builder dialing off-list, a premium number under ``allowed_prefixes=["+1"]``,
  a ``sip:`` user on an unlisted host, a policy-less workspace, the 11th call in
  a minute (also with the convenience rate limiter switched off), and the same
  through an API key with ``calls:write``;
* the worker's ``transfer_call`` route refusing an off-list target;
* every refusal leaving an ``audit_log`` row (V2-19T-5a: written in a second
  transaction because the request's own one rolls back);
* the trunk password never reaching a response or a log line;
* the internal telephony routes refusing a missing or wrong service token;
* another workspace never seeing, dialing through or steering these calls.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import test_telephony as tel
from auth_helpers import (
    WEB_ORIGIN,
    key_client,
    login,
    make_api_key,
    make_user,
    make_workspace,
    set_agent_columns,
)
from conftest import captured_text
from fastapi import FastAPI
from sqlalchemy import select, update
from test_telephony import CALLEE, TEST_POLICY, World, _call, _inbound_session, _set_policy, _trunk

from lkap_api.db.models import AuditLog, Workspace
from lkap_api.db.session import Database
from lkap_api.settings import Settings, get_settings
from lkap_api.telephony.policy import BLOCKED_PREFIXES, NANP_NON_US_CA_NPAS, NOT_ALLOWED_TO_MODEL

# The telephony world's fixtures, shared with ``test_telephony`` (pytest finds them by name).
fake_lk = tel.fake_lk
fake_factory = tel.fake_factory
dial_policy = tel.dial_policy
world = tel.world

INTERNAL_ROUTES = (
    ("/internal/v1/telephony/calls/report", {"status": "answered"}),
    ("/internal/v1/telephony/sessions/{session_id}/transfer", {"to": "+15550002222"}),
)


async def _audit(database: Database, prefix: str = "call.") -> list[AuditLog]:
    async with database.session() as session:
        rows = await session.execute(
            select(AuditLog).where(AuditLog.action.like(f"{prefix}%")).order_by(AuditLog.id)
        )
        return list(rows.scalars())


@pytest.fixture
async def builder(app: FastAPI, database: Database) -> Any:
    await make_user(database, "builder@tel.example", role="builder")
    async with await login(app, "builder@tel.example") as client:
        client.headers["Origin"] = WEB_ORIGIN
        yield client


# ------------------------------------------------------------------ destinations
async def test_builder_dialing_off_list_is_422_audited_and_never_dialed(
    admin_client: httpx.AsyncClient, builder: httpx.AsyncClient, world: World, database: Database
) -> None:
    await _trunk(admin_client, world, "outbound")

    response = await builder.post("/v1/calls", json={"agent_id": world.agent_id, "to_e164": "+14155550100"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "destination_not_allowed"
    assert response.json()["error"]["details"]["allowed_prefixes"] == TEST_POLICY["allowed_prefixes"]
    assert world.lk.named("create_sip_participant") == []
    (row,) = await _audit(database)
    assert (row.action, row.actor_type) == ("call.refused", "user")
    assert row.payload == {
        "agent_id": world.agent_id,
        "to": "+14155550100",
        "code": "destination_not_allowed",
        "status": 422,
    }


@pytest.mark.parametrize("number", ["+19005550100", "+19765550100"])
async def test_premium_prefix_is_refused_even_under_a_plus_one_allow_list(
    admin_client: httpx.AsyncClient, world: World, database: Database, number: str
) -> None:
    await _trunk(admin_client, world, "outbound")
    await _set_policy(database, {"allowed_prefixes": ["+1"]})

    response = await admin_client.post("/v1/calls", json={"agent_id": world.agent_id, "to_e164": number})

    assert response.status_code == 422
    assert "always blocked" in response.json()["error"]["message"]
    assert world.lk.named("create_sip_participant") == []


async def test_satellite_range_is_refused_even_when_its_country_code_is_allowed(
    admin_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    await _trunk(admin_client, world, "outbound")
    await _set_policy(database, {"allowed_prefixes": ["+1", "+88"]})

    response = await admin_client.post(
        "/v1/calls", json={"agent_id": world.agent_id, "to_e164": "+881631234567"}
    )

    assert response.status_code == 422
    assert "always blocked" in response.json()["error"]["message"]


def test_blocked_prefixes_cover_premium_and_satellite_ranges() -> None:
    assert {"+1900", "+1976", "+449", "+4487", "+870", "+881", "+882", "+883", "+979"} <= set(
        BLOCKED_PREFIXES
    )


async def test_sip_uri_user_on_an_unlisted_host_is_refused_but_a_listed_host_passes(
    admin_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    call = await _call(admin_client, world)

    evil = await admin_client.post(f"/v1/calls/{call['id']}/transfer", json={"to": "sip:100@evil.example"})
    listed = await admin_client.post(
        f"/v1/calls/{call['id']}/transfer", json={"to": "sip:desk@pbx.example.com"}
    )

    assert evil.status_code == 422
    assert evil.json()["error"]["code"] == "destination_not_allowed"
    assert listed.status_code == 200, listed.text
    (refer,) = world.lk.named("transfer_sip_participant")
    assert refer.transfer_to == "sip:desk@pbx.example.com"
    actions = [row.action for row in await _audit(database)]
    assert actions == ["call.placed", "call.transfer_refused", "call.transferred"]


async def test_policy_less_workspace_is_422_with_an_actionable_message(
    admin_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    await _trunk(admin_client, world, "outbound")
    await _set_policy(database, None)

    response = await admin_client.post("/v1/calls", json={"agent_id": world.agent_id, "to_e164": CALLEE})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["details"]["reason"] == "no_policy"
    assert "Outbound dialing policy" in error["message"]
    assert [row.action for row in await _audit(database)] == ["call.refused"]


# ------------------------------------------------------------------------ bucket
async def test_eleventh_call_in_a_minute_is_429_and_audited(
    admin_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    await _trunk(admin_client, world, "outbound")
    await _set_policy(database, {**TEST_POLICY, "max_concurrent_outbound": 100})
    await set_agent_columns(database, world.agent_id, limits={"max_concurrent_sessions": 100})
    body = {"agent_id": world.agent_id, "to_e164": CALLEE}

    statuses = [(await admin_client.post("/v1/calls", json=body)).status_code for _ in range(11)]

    assert statuses == [201] * 10 + [429]
    refused = [row for row in await _audit(database) if row.action == "call.refused"]
    assert len(refused) == 1 and refused[0].payload is not None
    assert (refused[0].payload["code"], refused[0].payload["status"]) == ("rate_limited", 429)


async def test_the_call_bucket_holds_even_with_the_rate_limiter_switched_off(
    app: FastAPI, settings: Settings, admin_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    """V2-19T-5b: the per-minute call cap is a toll-fraud control, not a convenience limit."""
    app.dependency_overrides[get_settings] = lambda: settings.model_copy(update={"rate_limit_enabled": False})
    try:
        await _trunk(admin_client, world, "outbound")
        await _set_policy(database, {**TEST_POLICY, "max_calls_per_min": 2, "max_concurrent_outbound": 100})
        await set_agent_columns(database, world.agent_id, limits={"max_concurrent_sessions": 100})
        body = {"agent_id": world.agent_id, "to_e164": CALLEE}

        statuses = [(await admin_client.post("/v1/calls", json=body)).status_code for _ in range(3)]
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert statuses == [201, 201, 429]


async def test_calls_busy_is_audited_as_a_refusal(
    admin_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    await _trunk(admin_client, world, "outbound")
    await _set_policy(database, {**TEST_POLICY, "max_concurrent_outbound": 1})
    body = {"agent_id": world.agent_id, "to_e164": CALLEE}

    first = await admin_client.post("/v1/calls", json=body)
    busy = await admin_client.post("/v1/calls", json=body)

    assert (first.status_code, busy.status_code) == (201, 429)
    assert busy.json()["error"]["code"] == "calls_busy"
    refused = [row for row in await _audit(database) if row.action == "call.refused"]
    assert refused[0].payload is not None and refused[0].payload["code"] == "calls_busy"


# ----------------------------------------------------------------------- API key
async def test_api_key_with_calls_write_gets_the_same_policy_and_bucket(
    app: FastAPI, admin_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    await _trunk(admin_client, world, "outbound")
    await _set_policy(database, {**TEST_POLICY, "max_calls_per_min": 1, "max_concurrent_outbound": 100})
    await set_agent_columns(database, world.agent_id, limits={"max_concurrent_sessions": 100})
    _key_id, raw = await make_api_key(database, ["calls:write", "sessions:read"])
    body = {"agent_id": world.agent_id, "to_e164": CALLEE}

    async with key_client(app, raw) as keyed:
        premium = await keyed.post("/v1/calls", json={**body, "to_e164": "+19005550100"})
        first = await keyed.post("/v1/calls", json=body)
        second = await keyed.post("/v1/calls", json=body)

    assert premium.status_code == 422
    assert (first.status_code, second.status_code) == (201, 429)
    refused = [row for row in await _audit(database) if row.action == "call.refused"]
    assert [row.actor_type for row in refused] == ["api_key", "api_key"]


async def test_api_key_without_calls_write_cannot_dial(
    app: FastAPI, admin_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    await _trunk(admin_client, world, "outbound")
    _key_id, raw = await make_api_key(database, ["agents:write", "sessions:read"])

    async with key_client(app, raw) as keyed:
        response = await keyed.post("/v1/calls", json={"agent_id": world.agent_id, "to_e164": CALLEE})

    assert response.status_code == 403
    assert world.lk.named("create_sip_participant") == []


# ------------------------------------------------------------------------ worker
async def test_worker_transfer_to_an_off_list_target_is_refused_for_the_model(
    service_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    session_id = await _inbound_session(database, world)

    response = await service_client.post(
        f"/internal/v1/telephony/sessions/{session_id}/transfer",
        json={"to": "sip:100@evil.example", "participant_identity": "sip_caller"},
    )

    assert response.status_code == 200
    assert (response.json()["ok"], response.json()["status"]) == (False, "refused")
    assert response.json()["reason"] == NOT_ALLOWED_TO_MODEL
    assert world.lk.named("transfer_sip_participant") == []
    assert [row.action for row in await _audit(database)] == ["call.transfer_refused"]


@pytest.mark.parametrize("headers", [{}, {"X-Service-Token": "wrong"}, {"X-Admin-Token": "test-admin"}])
@pytest.mark.parametrize(("path", "body"), INTERNAL_ROUTES)
async def test_internal_telephony_routes_need_the_service_token(
    app: FastAPI,
    world: World,
    database: Database,
    headers: dict[str, str],
    path: str,
    body: dict[str, Any],
) -> None:
    session_id = await _inbound_session(database, world)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api.test", headers=headers
    ) as client:
        response = await client.post(
            path.format(session_id=session_id), json={"session_id": session_id, **body}
        )

    assert response.status_code == 401
    assert world.lk.requests == []


# ------------------------------------------------------------------ trunk secret
async def test_trunk_password_never_reaches_a_response_or_a_log_line(
    admin_client: httpx.AsyncClient, world: World, log_capture: pytest.LogCaptureFixture
) -> None:
    password = "trunk-pw-7f3a9c1e"  # noqa: S105 - test value

    created = await _trunk(admin_client, world, "outbound", auth_username="lkap", auth_password=password)
    listed = await admin_client.get("/v1/telephony/trunks")
    fetched = await admin_client.get(f"/v1/telephony/trunks/{created['id']}")
    updated = await admin_client.put(
        f"/v1/telephony/trunks/{created['id']}", json={"auth_password": password + "2"}
    )

    for response_text in (str(created), listed.text, fetched.text, updated.text):
        assert password not in response_text
        assert "auth_password" not in response_text
    assert created["has_password"] is True
    assert password not in captured_text(log_capture, scope=None)


# --------------------------------------------------------------------- tenancy
async def test_another_workspace_cannot_see_steer_or_dial_through_these_calls(
    app: FastAPI, admin_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    call = await _call(admin_client, world)
    beta_id = await make_workspace(database, "beta-tel")
    async with database.session() as session:  # B may dial too, so only tenancy can stop it
        await session.execute(
            update(Workspace).where(Workspace.id == beta_id).values(settings={"telephony": TEST_POLICY})
        )
    await make_user(database, "oscar@b.example", role="admin", workspace_id=beta_id)

    async with await login(app, "oscar@b.example") as oscar:
        oscar.headers["Origin"] = WEB_ORIGIN
        responses = [
            await oscar.get(f"/v1/calls/{call['id']}"),
            await oscar.post(f"/v1/calls/{call['id']}/transfer", json={"to": "+15550001111"}),
            await oscar.post(f"/v1/calls/{call['id']}/hangup"),
            await oscar.post(f"/v1/calls/{call['id']}/dtmf", json={"digits": "1"}),
            await oscar.post("/v1/calls", json={"agent_id": world.agent_id, "to_e164": CALLEE}),
        ]
        listed = await oscar.get("/v1/calls")

    assert [r.status_code for r in responses] == [404, 404, 404, 404, 404], [r.text for r in responses]
    assert listed.json()["total"] == 0
    assert world.lk.named("transfer_sip_participant") == []
    assert world.lk.named("send_data") == []


# ------------------------------------------------- R-V2-28: numeric sip user needs a host
async def test_numeric_sip_user_on_an_unlisted_host_is_422_naming_allowed_sip_hosts(
    admin_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    await _set_policy(database, {**TEST_POLICY, "allowed_prefixes": ["+1"]})
    call = await _call(admin_client, world)

    response = await admin_client.post(
        f"/v1/calls/{call['id']}/transfer", json={"to": "sip:+15551230000@evil.example"}
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "destination_not_allowed"
    assert "allowed_sip_hosts" in error["message"]
    assert error["details"]["allowed_sip_hosts"] == ["pbx.example.com"]
    assert world.lk.named("transfer_sip_participant") == []


async def test_numeric_sip_user_on_a_listed_host_under_plus_one_passes(
    admin_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    await _set_policy(database, {**TEST_POLICY, "allowed_prefixes": ["+1"]})
    call = await _call(admin_client, world)

    response = await admin_client.post(
        f"/v1/calls/{call['id']}/transfer", json={"to": "sip:+15551230000@pbx.example.com"}
    )

    assert response.status_code == 200, response.text
    (refer,) = world.lk.named("transfer_sip_participant")
    assert refer.transfer_to == "sip:+15551230000@pbx.example.com"


async def test_premium_number_on_a_listed_sip_host_is_still_always_blocked(
    admin_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    await _set_policy(database, {**TEST_POLICY, "allowed_prefixes": ["+1"]})
    call = await _call(admin_client, world)

    response = await admin_client.post(
        f"/v1/calls/{call['id']}/transfer", json={"to": "sip:+19005550100@pbx.example.com"}
    )

    assert response.status_code == 422
    assert "always blocked" in response.json()["error"]["message"]


async def test_transfer_target_with_a_numeric_sip_user_on_an_unlisted_host_is_422_at_save(
    admin_client: httpx.AsyncClient, world: World
) -> None:
    config = (await admin_client.get(f"/v1/agents/{world.agent_id}")).json()["config"]
    config["telephony"] = {"transfer_targets": [{"label": "Desk", "to": "sip:+15551230000@unlisted.example"}]}

    response = await admin_client.put(f"/v1/agents/{world.agent_id}", json={"config": config})

    assert response.status_code == 422, response.text
    issues = response.json()["error"]["details"]["issues"]
    assert any(
        i["path"] == "telephony.transfer_targets[0].to" and "allowed_sip_hosts" in i["message"]
        for i in issues
    )


# ------------------------------------------------- R-V2-29: +1 = US and Canada; 976 exchange
@pytest.mark.parametrize(
    "number",
    [
        "+18095550100",  # Dominican Republic
        "+18295550100",
        "+18495550100",
        "+18765550100",  # Jamaica
        "+12845550100",  # British Virgin Islands
        "+14735550100",  # Grenada
        "+16495550100",  # Turks and Caicos
        "+17875550100",  # Puerto Rico
    ],
)
async def test_plus_one_alone_does_not_reach_the_caribbean_or_the_territories(
    admin_client: httpx.AsyncClient, world: World, database: Database, number: str
) -> None:
    await _trunk(admin_client, world, "outbound")
    await _set_policy(database, {"allowed_prefixes": ["+1"]})

    response = await admin_client.post("/v1/calls", json={"agent_id": world.agent_id, "to_e164": number})

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "destination_not_allowed"
    assert "US and Canada" in response.json()["error"]["message"]
    assert world.lk.named("create_sip_participant") == []


async def test_plus_one_still_reaches_the_united_states(
    admin_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    await _trunk(admin_client, world, "outbound")
    await _set_policy(database, {"allowed_prefixes": ["+1"]})

    response = await admin_client.post(
        "/v1/calls", json={"agent_id": world.agent_id, "to_e164": "+14155550100"}
    )

    assert response.status_code == 201, response.text


async def test_an_explicit_caribbean_prefix_overrides_the_plus_one_exclusion(
    admin_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    await _trunk(admin_client, world, "outbound")
    await _set_policy(database, {"allowed_prefixes": ["+1", "+1876"]})

    jamaica = await admin_client.post(
        "/v1/calls", json={"agent_id": world.agent_id, "to_e164": "+18765550100"}
    )

    assert jamaica.status_code == 201, jamaica.text


@pytest.mark.parametrize("prefixes", [["+1"], ["+1", "+1212"], ["+1212"]])
async def test_the_976_exchange_is_always_blocked(
    admin_client: httpx.AsyncClient, world: World, database: Database, prefixes: list[str]
) -> None:
    await _trunk(admin_client, world, "outbound")
    await _set_policy(database, {"allowed_prefixes": prefixes})

    response = await admin_client.post(
        "/v1/calls", json={"agent_id": world.agent_id, "to_e164": "+12129765555"}
    )

    assert response.status_code == 422
    assert "always blocked" in response.json()["error"]["message"]


def test_the_non_us_ca_npa_set_is_the_nanp_table_and_excludes_the_mainland() -> None:
    assert {
        "809",
        "829",
        "849",
        "876",
        "658",
        "284",
        "473",
        "649",
        "787",
        "939",
        "340",
        "671",
        "670",
        "684",
    } <= (NANP_NON_US_CA_NPAS)
    assert (
        not {"212", "415", "416", "604", "202", "907", "808"} & NANP_NON_US_CA_NPAS
    )  # NY, CA, ON, BC, DC, AK, HI
    assert all(len(npa) == 3 and npa.isdigit() for npa in NANP_NON_US_CA_NPAS)
