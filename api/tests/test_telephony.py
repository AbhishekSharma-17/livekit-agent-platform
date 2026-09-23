"""Telephony: trunks, dispatch rules, numbers, calls, webhooks (LiveKit faked at the boundary).

V2-17's suite plus V2-19's rulings: the dialing policy (R-V2-23), save-time
transfer-destination checks (R-V2-21), call variables in the resolved config
(R-V2-22) and the stuck-call sweep (R-V2-24). Every test that dials runs under
:data:`TEST_POLICY` (the ``world`` fixture), because a workspace without a
policy may not dial at all.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from auth_helpers import key_client, make_api_key, set_agent_columns
from conftest import inference_config
from connection_fakes import KEY_B, SECRET_B, add_agent, connection_row
from fastapi import FastAPI
from google.protobuf.json_format import MessageToJson
from livekit.api import AccessToken, ServerError, SipCallError
from livekit.protocol.models import DisconnectReason, ParticipantInfo, Room
from livekit.protocol.webhook import WebhookEvent
from lkap_contracts.dispatch import DispatchMetadata
from lkap_contracts.telephony import TelephonyConfig as TelephonyConfigModel
from sqlalchemy import select, update
from telephony_fakes import FakeClientFactory, FakeLiveKitApi

from lkap_api.config_service import ValidationContext
from lkap_api.connections.clients import get_client_factory
from lkap_api.db.constants import DEFAULT_WORKSPACE_ID
from lkap_api.db.models import AuditLog, Call, SipDispatchRule, SipTrunk, Workspace, new_id, utcnow
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.session import Database
from lkap_api.settings import Settings
from lkap_api.telephony.calls import (
    STUCK_DIAL_AFTER_S,
    SWEPT_DIAL_REASON,
    advance,
    status_for_sip_code,
    sweep_stuck_calls,
)
from lkap_api.telephony.common import DTMF_TOPIC
from lkap_api.telephony.policy import (
    BLOCKED_PREFIXES,
    NOT_ALLOWED_TO_MODEL,
    TelephonyPolicy,
    destination_problem,
    policy_of,
)
from lkap_api.telephony.validation import telephony_issues
from lkap_api.vault import Vault

INBOUND_NUMBER = "+15551230000"
OUTBOUND_NUMBER = "+15551239999"
CALLEE = "+15557654321"

#: The dialing policy every dialing test runs under (R-V2-23).
TEST_POLICY: dict[str, Any] = {"allowed_prefixes": ["+1555"], "allowed_sip_hosts": ["pbx.example.com"]}


async def _set_policy(database: Database, policy: dict[str, Any] | None) -> None:
    """Store (or, with ``None``, remove) the default workspace's dialing policy."""
    async with database.session() as session:
        await session.execute(
            update(Workspace)
            .where(Workspace.id == DEFAULT_WORKSPACE_ID)
            .values(settings={} if policy is None else {"telephony": policy})
        )


@dataclass
class World:
    """Seeded connection + agent, and the fake LiveKit behind the factory."""

    connection_id: str
    agent_id: str
    agent_name: str
    lk: FakeLiveKitApi
    factory: FakeClientFactory


@pytest.fixture
def fake_lk() -> FakeLiveKitApi:
    return FakeLiveKitApi()


@pytest.fixture
def fake_factory(app: FastAPI, settings: Settings, fake_lk: FakeLiveKitApi) -> Iterator[FakeClientFactory]:
    factory = FakeClientFactory(Vault(settings.master_key), fake_lk)
    app.dependency_overrides[get_client_factory] = lambda: factory
    yield factory
    app.dependency_overrides.pop(get_client_factory, None)


async def _seed_connection(
    database: Database, settings: Settings, *, sip: bool = True, **overrides: object
) -> tuple[str, str, str]:
    vault = Vault(settings.master_key)
    async with database.session() as session:
        conn = connection_row(
            vault,
            slug=f"sip-{new_id()[:6]}",
            capabilities={"sip_enabled": sip},
            **overrides,  # type: ignore[arg-type]
        )
        session.add(conn)
        await session.flush()
        agent = await add_agent(session, inference_config(), connection_id=conn.id)
    return conn.id, agent.id, conn.agent_name


@pytest.fixture
async def dial_policy(database: Database) -> dict[str, Any]:
    await _set_policy(database, TEST_POLICY)
    return TEST_POLICY


@pytest.fixture
async def world(
    database: Database,
    settings: Settings,
    fake_lk: FakeLiveKitApi,
    fake_factory: FakeClientFactory,
    dial_policy: dict[str, Any],
) -> AsyncIterator[World]:
    connection_id, agent_id, agent_name = await _seed_connection(database, settings)
    yield World(connection_id, agent_id, agent_name, fake_lk, fake_factory)


async def _trunk(
    client: httpx.AsyncClient, world: World, direction: str = "inbound", **extra: object
) -> dict[str, object]:
    body: dict[str, object] = {
        "connection_id": world.connection_id,
        "direction": direction,
        "name": f"{direction} trunk",
        "numbers": [INBOUND_NUMBER if direction == "inbound" else OUTBOUND_NUMBER],
    }
    if direction == "outbound":
        body["address"] = "example.pstn.twilio.com"
    body.update(extra)
    response = await client.post("/v1/telephony/trunks", json=body)
    assert response.status_code == 201, response.text
    trunk: dict[str, object] = response.json()
    return trunk


# ------------------------------------------------------------------------------ trunks
async def test_create_trunk_inbound_mirrors_to_livekit_and_stores_lk_trunk_id(
    admin_client: httpx.AsyncClient, world: World, database: Database, settings: Settings
) -> None:
    trunk = await _trunk(admin_client, world, auth_username="lkap", auth_password="pw-secret-123")

    assert trunk["lk_trunk_id"] == "ST_in_1"
    assert trunk["has_password"] is True
    assert "pw-secret-123" not in json.dumps(trunk)
    (sent,) = world.lk.named("create_inbound_trunk")
    assert list(sent.trunk.numbers) == [INBOUND_NUMBER]
    assert sent.trunk.auth_username == "lkap"
    assert sent.trunk.auth_password == "pw-secret-123"
    assert json.loads(sent.trunk.metadata)["lkap_trunk_id"] == trunk["id"]
    async with database.session() as session:
        row = await session.get(SipTrunk, str(trunk["id"]))
    assert row is not None and row.lk_trunk_id == "ST_in_1"
    assert row.auth_password_ct is not None
    assert Vault(settings.master_key).decrypt(row.auth_password_ct) == {"auth_password": "pw-secret-123"}


async def test_create_trunk_outbound_sends_address_and_numbers(
    admin_client: httpx.AsyncClient, world: World
) -> None:
    trunk = await _trunk(admin_client, world, "outbound")

    assert trunk["lk_trunk_id"] == "ST_out_1"
    (sent,) = world.lk.named("create_outbound_trunk")
    assert sent.trunk.address == "example.pstn.twilio.com"
    assert list(sent.trunk.numbers) == [OUTBOUND_NUMBER]


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ({"direction": "outbound", "numbers": [OUTBOUND_NUMBER]}, "SIP address"),
        ({"direction": "outbound", "address": "sip.example.com"}, "at least one number"),
    ],
)
async def test_create_trunk_outbound_incomplete_is_422_and_sends_nothing(
    admin_client: httpx.AsyncClient, world: World, body: dict[str, object], fragment: str
) -> None:
    response = await admin_client.post(
        "/v1/telephony/trunks", json={"connection_id": world.connection_id, "name": "t", **body}
    )

    assert response.status_code == 422
    assert fragment in response.json()["error"]["message"]
    assert world.lk.requests == []


async def test_create_trunk_invalid_number_is_422(admin_client: httpx.AsyncClient, world: World) -> None:
    response = await admin_client.post(
        "/v1/telephony/trunks",
        json={
            "connection_id": world.connection_id,
            "direction": "inbound",
            "name": "t",
            "numbers": ["555-1234"],
        },
    )

    assert response.status_code == 422


async def test_create_trunk_self_hosted_without_sip_is_409(
    admin_client: httpx.AsyncClient,
    database: Database,
    settings: Settings,
    fake_lk: FakeLiveKitApi,
    fake_factory: FakeClientFactory,
) -> None:
    connection_id, _agent, _name = await _seed_connection(
        database, settings, sip=False, deployment_type="self_hosted", url="ws://localhost:7880"
    )

    response = await admin_client.post(
        "/v1/telephony/trunks",
        json={
            "connection_id": connection_id,
            "direction": "inbound",
            "name": "t",
            "numbers": [INBOUND_NUMBER],
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["details"]["reason"] == "sip_disabled"
    assert fake_lk.requests == []


async def test_create_trunk_livekit_rejects_is_422_and_stores_nothing(
    admin_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    world.lk.fail["create_inbound_trunk"] = ServerError("invalid_argument", "number in use", status=400)

    response = await admin_client.post(
        "/v1/telephony/trunks",
        json={
            "connection_id": world.connection_id,
            "direction": "inbound",
            "name": "t",
            "numbers": [INBOUND_NUMBER],
        },
    )

    assert response.status_code == 422
    assert "number in use" in response.json()["error"]["message"]
    async with database.session() as session:
        assert (await session.scalars(select(SipTrunk))).all() == []


async def test_update_trunk_sends_field_update_and_keeps_password(
    admin_client: httpx.AsyncClient, world: World
) -> None:
    trunk = await _trunk(admin_client, world, auth_password="keep-me-please")

    response = await admin_client.put(
        f"/v1/telephony/trunks/{trunk['id']}", json={"name": "Renamed", "numbers": [INBOUND_NUMBER, CALLEE]}
    )

    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Renamed"
    assert response.json()["has_password"] is True
    (update,) = world.lk.named("update_inbound_trunk_fields")
    assert update == {"trunk_id": "ST_in_1", "name": "Renamed", "numbers": [INBOUND_NUMBER, CALLEE]}


async def test_sync_trunk_recreates_trunk_and_repoints_rules(
    admin_client: httpx.AsyncClient, world: World
) -> None:
    trunk = await _trunk(admin_client, world, auth_password="pw-for-sync")
    rule = await admin_client.post(
        "/v1/telephony/dispatch-rules", json={"trunk_id": trunk["id"], "agent_id": world.agent_id}
    )
    assert rule.status_code == 201, rule.text

    response = await admin_client.post(f"/v1/telephony/trunks/{trunk['id']}/sync")

    assert response.status_code == 200, response.text
    new_id_ = response.json()["lk_trunk_id"]
    assert new_id_ != trunk["lk_trunk_id"]
    assert [d.sip_trunk_id for d in world.lk.named("delete_trunk")] == ["ST_in_1"]
    recreated = world.lk.named("create_inbound_trunk")[-1]
    assert recreated.trunk.auth_password == "pw-for-sync"  # decrypted from the vault
    assert [d.sip_dispatch_rule_id for d in world.lk.named("delete_dispatch_rule")] == [
        rule.json()["lk_rule_id"]
    ]
    assert list(world.lk.named("create_dispatch_rule")[-1].dispatch_rule.trunk_ids) == [new_id_]


async def test_delete_trunk_deletes_rules_and_unroutes_numbers(
    admin_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    trunk = await _trunk(admin_client, world)
    number = await admin_client.post(
        "/v1/telephony/numbers",
        json={"e164": INBOUND_NUMBER, "trunk_id": trunk["id"], "inbound_agent_id": world.agent_id},
    )
    assert number.status_code == 201, number.text

    response = await admin_client.delete(f"/v1/telephony/trunks/{trunk['id']}")

    assert response.status_code == 204
    assert len(world.lk.named("delete_dispatch_rule")) == 1
    assert [d.sip_trunk_id for d in world.lk.named("delete_trunk")] == ["ST_in_1"]
    listed = (await admin_client.get("/v1/telephony/numbers")).json()["items"]
    assert listed == [
        {
            "id": number.json()["id"],
            "e164": INBOUND_NUMBER,
            "trunk_id": None,
            "inbound_agent_id": None,
            "label": "",
            "dispatch_rule_id": None,
        }
    ]
    async with database.session() as session:
        assert (await session.scalars(select(SipDispatchRule))).all() == []


# ---------------------------------------------------------------------- dispatch rules
async def test_create_dispatch_rule_metadata_carries_agent_id_and_sip_in_channel(
    admin_client: httpx.AsyncClient, world: World
) -> None:
    trunk = await _trunk(admin_client, world)

    response = await admin_client.post(
        "/v1/telephony/dispatch-rules",
        json={
            "trunk_id": trunk["id"],
            "agent_id": world.agent_id,
            "numbers": [INBOUND_NUMBER],
            "pin": "1234",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["lk_rule_id"] == "SDR_2"
    assert body["has_pin"] is True and "1234" not in json.dumps(body)
    (sent,) = world.lk.named("create_dispatch_rule")
    info = sent.dispatch_rule
    assert list(info.trunk_ids) == ["ST_in_1"]
    assert list(info.numbers) == [INBOUND_NUMBER]
    assert info.rule.dispatch_rule_individual.room_prefix == "call-"
    assert info.rule.dispatch_rule_individual.pin == "1234"
    (dispatch,) = info.room_config.agents
    assert dispatch.agent_name == world.agent_name
    meta = DispatchMetadata.model_validate_json(dispatch.metadata)
    assert meta.agent_id == world.agent_id
    assert meta.channel == "sip_in"
    assert meta.session_id is None
    assert meta.config_version == 0
    assert meta.participant_identity == ""
    assert meta.connection_id == world.connection_id


async def test_create_dispatch_rule_on_outbound_trunk_is_422(
    admin_client: httpx.AsyncClient, world: World
) -> None:
    trunk = await _trunk(admin_client, world, "outbound")

    response = await admin_client.post(
        "/v1/telephony/dispatch-rules", json={"trunk_id": trunk["id"], "agent_id": world.agent_id}
    )

    assert response.status_code == 422
    assert world.lk.named("create_dispatch_rule") == []


async def test_create_dispatch_rule_agent_on_other_connection_is_422(
    admin_client: httpx.AsyncClient, world: World, database: Database, settings: Settings
) -> None:
    trunk = await _trunk(admin_client, world)
    _other_conn, other_agent, _name = await _seed_connection(database, settings)

    response = await admin_client.post(
        "/v1/telephony/dispatch-rules", json={"trunk_id": trunk["id"], "agent_id": other_agent}
    )

    assert response.status_code == 422
    assert "different connection" in response.json()["error"]["message"]


# ----------------------------------------------------------------------------- numbers
async def test_create_number_routes_to_agent_with_managed_rule(
    admin_client: httpx.AsyncClient, world: World
) -> None:
    trunk = await _trunk(admin_client, world, numbers=[])

    response = await admin_client.post(
        "/v1/telephony/numbers",
        json={
            "e164": CALLEE,
            "trunk_id": trunk["id"],
            "inbound_agent_id": world.agent_id,
            "label": "Support",
        },
    )

    assert response.status_code == 201, response.text
    number = response.json()
    assert number["inbound_agent_id"] == world.agent_id
    assert number["dispatch_rule_id"]
    (added,) = world.lk.named("update_inbound_trunk_fields")
    assert list(added["numbers"].add) == [CALLEE]
    (rule,) = world.lk.named("create_dispatch_rule")
    assert list(rule.dispatch_rule.numbers) == [CALLEE]
    rules = (await admin_client.get("/v1/telephony/dispatch-rules")).json()["items"]
    assert [(r["id"], r["managed_by_number"]) for r in rules] == [(number["dispatch_rule_id"], CALLEE)]
    trunks = (await admin_client.get("/v1/telephony/trunks")).json()["items"]
    assert trunks[0]["numbers"] == [CALLEE]


async def test_create_number_duplicate_is_409(admin_client: httpx.AsyncClient, world: World) -> None:
    first = await admin_client.post("/v1/telephony/numbers", json={"e164": CALLEE})
    assert first.status_code == 201

    response = await admin_client.post("/v1/telephony/numbers", json={"e164": CALLEE})

    assert response.status_code == 409


async def test_update_number_changes_agent_replaces_rule_and_clearing_unroutes(
    admin_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    trunk = await _trunk(admin_client, world)
    async with database.session() as session:
        second = await add_agent(session, inference_config(), connection_id=world.connection_id)
    created = (
        await admin_client.post(
            "/v1/telephony/numbers",
            json={"e164": INBOUND_NUMBER, "trunk_id": trunk["id"], "inbound_agent_id": world.agent_id},
        )
    ).json()

    moved = await admin_client.put(
        f"/v1/telephony/numbers/{created['id']}", json={"inbound_agent_id": second.id}
    )
    cleared = await admin_client.put(
        f"/v1/telephony/numbers/{created['id']}", json={"inbound_agent_id": None}
    )

    assert moved.status_code == 200, moved.text
    assert moved.json()["dispatch_rule_id"] not in (None, created["dispatch_rule_id"])
    metas = [
        DispatchMetadata.model_validate_json(r.dispatch_rule.room_config.agents[0].metadata).agent_id
        for r in world.lk.named("create_dispatch_rule")
    ]
    assert metas == [world.agent_id, second.id]
    assert cleared.status_code == 200
    assert cleared.json()["dispatch_rule_id"] is None
    assert len(world.lk.named("delete_dispatch_rule")) == 2
    assert (await admin_client.get("/v1/telephony/dispatch-rules")).json()["items"] == []


async def test_number_inbound_agent_without_trunk_is_422(
    admin_client: httpx.AsyncClient, world: World
) -> None:
    response = await admin_client.post(
        "/v1/telephony/numbers", json={"e164": CALLEE, "inbound_agent_id": world.agent_id}
    )

    assert response.status_code == 422


# ---------------------------------------------------------------------- outbound calls
async def _call(client: httpx.AsyncClient, world: World, **extra: object) -> dict[str, object]:
    await _trunk(client, world, "outbound")
    response = await client.post(
        "/v1/calls", json={"agent_id": world.agent_id, "to_e164": CALLEE, "timeout_s": 25, **extra}
    )
    assert response.status_code == 201, response.text
    call: dict[str, object] = response.json()
    return call


async def test_place_call_dispatches_agent_then_dials_and_marks_answered(
    admin_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    call = await _call(admin_client, world, variables={"customer": "Ada"})

    assert call["status"] == "dialing"
    assert call["direction"] == "outbound"
    assert (call["from_e164"], call["to_e164"]) == (OUTBOUND_NUMBER, CALLEE)
    methods = [name for name, _ in world.lk.requests if name in ("create_dispatch", "create_sip_participant")]
    assert methods == ["create_dispatch", "create_sip_participant"]
    (dispatch,) = world.lk.named("create_dispatch")
    (dial,) = world.lk.named("create_sip_participant")
    meta = DispatchMetadata.model_validate_json(dispatch.metadata)
    assert dispatch.agent_name == world.agent_name
    assert meta.channel == "sip_out" and meta.session_id == call["session_id"]
    assert meta.participant_identity == call["lk_participant_identity"] == dial.participant_identity
    assert dial.room_name == dispatch.room
    assert (dial.sip_trunk_id, dial.sip_call_to, dial.sip_number) == ("ST_out_1", CALLEE, OUTBOUND_NUMBER)
    assert dial.wait_until_answered is True
    assert dial.ringing_timeout.seconds == 25
    assert world.factory.opened[-1][2] is False  # no region failover: a replayed dial rings twice
    got = (await admin_client.get(f"/v1/calls/{call['id']}")).json()
    assert got["status"] == "answered"
    assert got["sip_call_id"] == "SCL_outbound_1"
    assert got["started_at"] and got["answered_at"]
    async with database.session() as session:
        row = await session.get(SessionRow, str(call["session_id"]))
    assert row is not None
    assert (row.channel, row.status, row.participant_identity) == (
        "sip_out",
        "created",
        dial.participant_identity,
    )
    assert row.caller is not None and row.caller["to"] == CALLEE
    assert (row.caller["call_id"], row.caller["lkap_call_id"]) == ("SCL_outbound_1", call["id"])
    assert row.variables == {"customer": "Ada"}


@pytest.mark.parametrize(("sip_code", "expected"), [("486", "busy"), ("480", "no_answer"), ("503", "failed")])
async def test_place_call_sip_failure_maps_status_and_dismisses_agent(
    admin_client: httpx.AsyncClient, world: World, database: Database, sip_code: str, expected: str
) -> None:
    world.lk.fail["create_sip_participant"] = SipCallError(
        "unavailable", "call failed", status=503, metadata={"sip_status_code": sip_code, "sip_status": "Nope"}
    )

    call = await _call(admin_client, world)

    got = (await admin_client.get(f"/v1/calls/{call['id']}")).json()
    assert got["status"] == expected
    assert got["hangup_reason"] == "Nope"
    assert [d.room for d in world.lk.named("delete_room")] == [world.lk.named("create_dispatch")[0].room]
    async with database.session() as session:
        row = await session.get(SessionRow, str(call["session_id"]))
    assert row is not None and row.status == "failed"


async def test_place_call_sip_disabled_is_409(
    admin_client: httpx.AsyncClient,
    database: Database,
    settings: Settings,
    fake_factory: FakeClientFactory,
    dial_policy: dict[str, Any],
) -> None:
    _conn, agent_id, _name = await _seed_connection(database, settings, sip=False)

    response = await admin_client.post("/v1/calls", json={"agent_id": agent_id, "to_e164": CALLEE})

    assert response.status_code == 409
    assert response.json()["error"]["details"]["reason"] == "sip_disabled"


async def test_place_call_without_outbound_trunk_is_422(
    admin_client: httpx.AsyncClient, world: World
) -> None:
    response = await admin_client.post("/v1/calls", json={"agent_id": world.agent_id, "to_e164": CALLEE})

    assert response.status_code == 422
    assert "no outbound trunk" in response.json()["error"]["message"]


async def test_place_call_bad_number_is_422(admin_client: httpx.AsyncClient, world: World) -> None:
    await _trunk(admin_client, world, "outbound")

    response = await admin_client.post("/v1/calls", json={"agent_id": world.agent_id, "to_e164": "12345"})

    assert response.status_code == 422


async def test_transfer_answered_call_sends_refer_and_marks_transferred(
    admin_client: httpx.AsyncClient, world: World
) -> None:
    call = await _call(admin_client, world)

    response = await admin_client.post(f"/v1/calls/{call['id']}/transfer", json={"to": "+15550001111"})

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "transferred"
    assert response.json()["transfer_to"] == "+15550001111"
    (refer,) = world.lk.named("transfer_sip_participant")
    assert refer.transfer_to == "tel:+15550001111"
    assert refer.participant_identity == call["lk_participant_identity"]
    assert refer.room_name == world.lk.named("create_dispatch")[0].room


async def test_transfer_refused_by_far_end_is_502_and_call_stays_answered(
    admin_client: httpx.AsyncClient, world: World
) -> None:
    call = await _call(admin_client, world)
    world.lk.transfer_status = 1  # STS_TRANSFER_FAILED

    response = await admin_client.post(
        f"/v1/calls/{call['id']}/transfer", json={"to": "sip:desk@pbx.example.com"}
    )

    assert response.status_code == 502
    assert (await admin_client.get(f"/v1/calls/{call['id']}")).json()["status"] == "answered"


async def test_transfer_invalid_target_is_422(admin_client: httpx.AsyncClient, world: World) -> None:
    call = await _call(admin_client, world)

    response = await admin_client.post(f"/v1/calls/{call['id']}/transfer", json={"to": "reception"})

    assert response.status_code == 422


async def test_dtmf_sends_server_data_packet_on_telephony_topic(
    admin_client: httpx.AsyncClient, world: World
) -> None:
    call = await _call(admin_client, world)

    response = await admin_client.post(f"/v1/calls/{call['id']}/dtmf", json={"digits": "12#"})

    assert response.status_code == 200, response.text
    (packet,) = world.lk.named("send_data")
    assert packet.topic == DTMF_TOPIC
    assert json.loads(packet.data) == {"v": 1, "op": "dtmf", "digits": "12#", "call_id": call["id"]}
    assert packet.room == world.lk.named("create_dispatch")[0].room


async def test_dtmf_invalid_digits_is_422(admin_client: httpx.AsyncClient, world: World) -> None:
    call = await _call(admin_client, world)

    response = await admin_client.post(f"/v1/calls/{call['id']}/dtmf", json={"digits": "12x"})

    assert response.status_code == 422


async def test_hangup_deletes_room_and_completes_then_second_hangup_is_409(
    admin_client: httpx.AsyncClient, world: World
) -> None:
    call = await _call(admin_client, world)

    first = await admin_client.post(f"/v1/calls/{call['id']}/hangup")
    second = await admin_client.post(f"/v1/calls/{call['id']}/hangup")

    assert first.status_code == 200
    assert first.json()["status"] == "completed"
    assert first.json()["hangup_reason"] == "hangup"
    assert len(world.lk.named("delete_room")) == 1
    assert second.status_code == 409


async def test_list_calls_filters_by_direction(admin_client: httpx.AsyncClient, world: World) -> None:
    call = await _call(admin_client, world)

    outbound = (await admin_client.get("/v1/calls", params={"direction": "outbound"})).json()
    inbound = (await admin_client.get("/v1/calls", params={"direction": "inbound"})).json()

    assert [c["id"] for c in outbound["items"]] == [call["id"]]
    assert inbound == {"items": [], "total": 0}


# ------------------------------------------------------------------------- webhooks
def _signed(event: WebhookEvent) -> tuple[str, dict[str, str]]:
    body = MessageToJson(event)
    digest = base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()
    token = AccessToken(KEY_B, SECRET_B).with_sha256(digest).to_jwt()
    return body, {"Authorization": token, "Content-Type": "application/webhook+json"}


def _sip_event(
    name: str,
    room: str,
    identity: str,
    *,
    attributes: dict[str, str] | None = None,
    reason: int = 0,
    ts: int = 1,
) -> WebhookEvent:
    participant = ParticipantInfo(
        identity=identity,
        kind=ParticipantInfo.Kind.SIP,
        attributes=attributes or {},
        disconnect_reason=reason,
    )
    return WebhookEvent(
        event=name,
        id=f"EV_{name}_{ts}",
        created_at=1_760_000_000 + ts,
        room=Room(name=room),
        participant=participant,
    )


async def _seed_dialing_call(database: Database, world: World) -> tuple[str, str, str]:
    """An outbound call row still ``dialing`` (as if the dial task had not finished)."""
    async with database.session() as session:
        sess = SessionRow(
            id=new_id(),
            workspace_id=DEFAULT_WORKSPACE_ID,
            agent_id=world.agent_id,
            connection_id=world.connection_id,
            config_version=1,
            room_name=f"lkap-call-{new_id()[:12]}",
            participant_identity="sip-leg-1",
            participant_name=CALLEE,
            status="created",
            pipeline_mode="cascaded",
            channel="sip_out",
        )
        session.add(sess)
        await session.flush()
        call = Call(
            id=new_id(),
            session_id=sess.id,
            workspace_id=DEFAULT_WORKSPACE_ID,
            connection_id=world.connection_id,
            direction="outbound",
            to_e164=CALLEE,
            status="dialing",
            lk_participant_identity="sip-leg-1",
        )
        session.add(call)
    return call.id, sess.room_name, sess.id


async def _call_row(database: Database, call_id: str) -> Call:
    async with database.session() as session:
        row = await session.get(Call, call_id)
    assert row is not None
    return row


async def test_webhooks_move_outbound_call_dialing_ringing_answered_completed(
    client: httpx.AsyncClient, world: World, database: Database
) -> None:
    call_id, room, _session_id = await _seed_dialing_call(database, world)
    url = f"/hooks/livekit/{world.connection_id}"

    for event, expected in [
        (
            _sip_event("participant_joined", room, "sip-leg-1", attributes={"sip.callStatus": "dialing"}),
            "ringing",
        ),
        (
            _sip_event(
                "participant_joined", room, "sip-leg-1", attributes={"sip.callStatus": "active"}, ts=2
            ),
            "answered",
        ),
        (
            _sip_event("participant_left", room, "sip-leg-1", reason=DisconnectReason.CLIENT_INITIATED, ts=3),
            "completed",
        ),
    ]:
        body, headers = _signed(event)
        response = await client.post(url, content=body, headers=headers)
        assert response.status_code == 204
        assert (await _call_row(database, call_id)).status == expected

    row = await _call_row(database, call_id)
    assert row.answered_at is not None and row.ended_at is not None
    assert row.hangup_reason == "client_initiated"
    # A late duplicate never moves a finished call backwards.
    body, headers = _signed(
        _sip_event("participant_joined", room, "sip-leg-1", attributes={"sip.callStatus": "active"}, ts=4)
    )
    await client.post(url, content=body, headers=headers)
    assert (await _call_row(database, call_id)).status == "completed"


async def test_webhook_unanswered_leg_left_maps_disconnect_reason(
    client: httpx.AsyncClient, world: World, database: Database
) -> None:
    call_id, room, _session_id = await _seed_dialing_call(database, world)
    body, headers = _signed(
        _sip_event("participant_left", room, "sip-leg-1", reason=DisconnectReason.USER_REJECTED)
    )

    await client.post(f"/hooks/livekit/{world.connection_id}", content=body, headers=headers)

    assert (await _call_row(database, call_id)).status == "busy"


async def test_webhook_inbound_sip_join_creates_call_and_fills_caller(
    client: httpx.AsyncClient, world: World, database: Database
) -> None:
    async with database.session() as session:
        sess = SessionRow(
            id=new_id(),
            workspace_id=DEFAULT_WORKSPACE_ID,
            agent_id=world.agent_id,
            connection_id=world.connection_id,
            config_version=1,
            room_name="call-_+15557654321_abc",
            participant_identity="sip_+15557654321",
            participant_name="caller",
            status="active",
            pipeline_mode="cascaded",
            channel="sip_in",
        )
        session.add(sess)
    attributes = {
        "sip.phoneNumber": CALLEE,
        "sip.trunkPhoneNumber": INBOUND_NUMBER,
        "sip.callID": "SCL_in_1",
        "sip.trunkID": "ST_in_1",
    }
    body, headers = _signed(
        _sip_event("participant_joined", sess.room_name, "sip_+15557654321", attributes=attributes)
    )

    response = await client.post(f"/hooks/livekit/{world.connection_id}", content=body, headers=headers)

    assert response.status_code == 204
    async with database.session() as session:
        calls = (await session.scalars(select(Call))).all()
        stored = await session.get(SessionRow, sess.id)
    assert len(calls) == 1
    call = calls[0]
    assert (call.direction, call.status, call.from_e164, call.to_e164) == (
        "inbound",
        "answered",
        CALLEE,
        INBOUND_NUMBER,
    )
    assert call.sip_call_id == "SCL_in_1" and call.session_id == sess.id
    assert stored is not None and stored.caller == {
        "direction": "inbound",
        "from": CALLEE,
        "to": INBOUND_NUMBER,
        "trunk_id": "ST_in_1",
        "call_id": "SCL_in_1",
    }


async def test_webhook_room_finished_closes_open_call(
    client: httpx.AsyncClient, world: World, database: Database
) -> None:
    call_id, room, _session_id = await _seed_dialing_call(database, world)
    body, headers = _signed(WebhookEvent(event="room_finished", id="EV_rf", room=Room(name=room)))

    await client.post(f"/hooks/livekit/{world.connection_id}", content=body, headers=headers)

    row = await _call_row(database, call_id)
    assert (row.status, row.hangup_reason) == ("failed", "room_finished")


# ------------------------------------------------------------------ internal (worker)
async def _inbound_session(database: Database, world: World) -> str:
    async with database.session() as session:
        sess = SessionRow(
            id=new_id(),
            workspace_id=DEFAULT_WORKSPACE_ID,
            agent_id=world.agent_id,
            connection_id=world.connection_id,
            config_version=1,
            room_name=f"call-{new_id()[:8]}",
            participant_identity="sip_caller",
            participant_name="caller",
            status="active",
            pipeline_mode="cascaded",
            channel="sip_in",
            caller={"from": CALLEE, "to": INBOUND_NUMBER, "call_id": "SCL_9", "trunk_id": "ST_in_1"},
        )
        session.add(sess)
    return sess.id


async def test_internal_report_creates_inbound_call_then_completes_it(
    service_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    session_id = await _inbound_session(database, world)

    answered = await service_client.post(
        "/internal/v1/telephony/calls/report",
        json={"session_id": session_id, "status": "answered", "participant_identity": "sip_caller"},
    )
    completed = await service_client.post(
        "/internal/v1/telephony/calls/report",
        json={"session_id": session_id, "status": "completed", "reason": "caller hung up"},
    )

    assert answered.status_code == 200, answered.text
    assert answered.json()["direction"] == "inbound"
    assert answered.json()["from_e164"] == CALLEE
    assert answered.json()["sip_call_id"] == "SCL_9"
    assert completed.json()["id"] == answered.json()["id"]
    assert completed.json()["status"] == "completed"
    assert completed.json()["hangup_reason"] == "caller hung up"


async def test_internal_report_requires_service_token(
    client: httpx.AsyncClient, world: World, database: Database
) -> None:
    session_id = await _inbound_session(database, world)

    response = await client.post(
        "/internal/v1/telephony/calls/report", json={"session_id": session_id, "status": "answered"}
    )

    assert response.status_code == 401


async def test_internal_transfer_from_tool_refers_the_caller(
    service_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    session_id = await _inbound_session(database, world)

    response = await service_client.post(
        f"/internal/v1/telephony/sessions/{session_id}/transfer",
        json={"to": "+15550002222", "participant_identity": "sip_caller"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True and response.json()["status"] == "transferred"
    (refer,) = world.lk.named("transfer_sip_participant")
    assert (refer.participant_identity, refer.transfer_to) == ("sip_caller", "tel:+15550002222")


async def test_internal_transfer_failure_is_reported_not_raised(
    service_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    session_id = await _inbound_session(database, world)
    world.lk.fail["transfer_sip_participant"] = SipCallError(
        "unavailable",
        "refer failed",
        status=503,
        metadata={"sip_status_code": "403", "sip_status": "Forbidden"},
    )

    response = await service_client.post(
        f"/internal/v1/telephony/sessions/{session_id}/transfer", json={"to": "+15550002222"}
    )

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert "Forbidden" in response.json()["reason"]


# ------------------------------------------------------------------------- units
@pytest.mark.parametrize(
    ("code", "expected"),
    [(486, "busy"), (600, "busy"), (480, "no_answer"), (487, "no_answer"), (None, "failed")],
)
def test_status_for_sip_code_maps_final_responses(code: int | None, expected: str) -> None:
    assert status_for_sip_code(code) == expected


def test_advance_never_moves_backwards_or_out_of_a_terminal_state() -> None:
    call = Call(id="c", workspace_id="w", direction="outbound", status="dialing")

    assert advance(call, "answered") is True
    assert advance(call, "ringing") is False
    assert advance(call, "completed", reason="bye") is True
    assert advance(call, "failed") is False
    assert (call.status, call.hangup_reason) == ("completed", "bye")


async def test_create_trunk_through_the_real_sdk_hits_the_sip_twirp_route(
    admin_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    """No fake factory: the real ``LiveKitAPI`` talks Twirp to an in-process server."""
    from connection_fakes import fake_livekit

    async with fake_livekit() as (fake, url):
        connection_id, _agent, _name = await _seed_connection(database, settings, url=url)
        response = await admin_client.post(
            "/v1/telephony/trunks",
            json={
                "connection_id": connection_id,
                "direction": "inbound",
                "name": "t",
                "numbers": [INBOUND_NUMBER],
            },
        )

    assert response.status_code == 201, response.text
    assert fake.calls == ["/twirp/livekit.SIP/CreateSIPInboundTrunk"]


async def test_inbound_join_webhook_before_session_is_linked_by_the_worker_report(
    client: httpx.AsyncClient,
    admin_client: httpx.AsyncClient,
    service_client: httpx.AsyncClient,
    world: World,
    database: Database,
) -> None:
    trunk = await _trunk(admin_client, world)
    attributes = {
        "sip.phoneNumber": CALLEE,
        "sip.trunkPhoneNumber": INBOUND_NUMBER,
        "sip.callID": "SCL_race",
        "sip.trunkID": str(trunk["lk_trunk_id"]),
    }
    room = "call-_+15557654321_race"
    body, headers = _signed(_sip_event("participant_joined", room, "sip_+15557654321", attributes=attributes))
    await client.post(f"/hooks/livekit/{world.connection_id}", content=body, headers=headers)
    async with database.session() as session:
        sess = SessionRow(
            id=new_id(),
            workspace_id=DEFAULT_WORKSPACE_ID,
            agent_id=world.agent_id,
            connection_id=world.connection_id,
            config_version=1,
            room_name=room,
            participant_identity="sip_in-caller",  # the placeholder sessions/start stores
            participant_name="caller",
            status="active",
            pipeline_mode="cascaded",
            channel="sip_in",
        )
        session.add(sess)

    report = await service_client.post(
        "/internal/v1/telephony/calls/report",
        json={
            "session_id": sess.id,
            "status": "answered",
            "participant_identity": "sip_+15557654321",
            "sip_call_id": "SCL_race",
        },
    )

    assert report.status_code == 200, report.text
    async with database.session() as session:
        calls = (await session.scalars(select(Call))).all()
        stored = await session.get(SessionRow, sess.id)
    assert len(calls) == 1
    assert (calls[0].session_id, calls[0].status, calls[0].from_e164) == (sess.id, "answered", CALLEE)
    assert calls[0].lk_participant_identity == "sip_+15557654321"
    assert stored is not None and stored.caller is not None and stored.caller["from"] == CALLEE


async def test_sip_join_on_a_foreign_trunk_records_nothing(
    client: httpx.AsyncClient, world: World, database: Database
) -> None:
    attributes = {"sip.callID": "SCL_other_app", "sip.trunkID": "ST_not_ours"}
    body, headers = _signed(
        _sip_event("participant_joined", "other-app-room", "sip_x", attributes=attributes)
    )

    await client.post(f"/hooks/livekit/{world.connection_id}", content=body, headers=headers)

    async with database.session() as session:
        assert (await session.scalars(select(Call))).all() == []


# ================================================================ V2-19 rulings
async def _audit_actions(database: Database) -> list[str]:
    async with database.session() as session:
        rows = (await session.execute(select(AuditLog).where(AuditLog.action.like("call.%")))).scalars()
        return [row.action for row in rows]


# ------------------------------------------------------- R-V2-23 dialing policy
@pytest.mark.parametrize(
    ("target", "allowed"),
    [
        ("+15557654321", True),
        ("tel:+15557654321", True),
        ("sip:+15551230000@carrier.example.net", True),  # a numeric user passes by prefix
        ("sips:+15551230000;user=phone@carrier.example.net", True),
        ("sip:desk@pbx.example.com", True),
        ("sip:desk@PBX.example.com:5060", True),
        ("+15550000000", True),
        ("+14155550000", False),  # off-list prefix
        ("sip:100@evil.example", False),  # non-numeric user on an unlisted host
        ("tel:5551234", False),  # no country code
        ("reception", False),
    ],
)
def test_destination_problem_prefix_and_sip_host_rules(target: str, allowed: bool) -> None:
    policy = policy_of({"telephony": TEST_POLICY})

    assert (destination_problem(policy, target) is None) is allowed


@pytest.mark.parametrize("number", ["+19005550100", "+19765550100", "+449098790000", "+881631234567"])
def test_blocked_prefixes_win_over_a_broad_allow_list(number: str) -> None:
    policy = TelephonyPolicy(allowed_prefixes=["+1", "+44", "+8"])

    problem = destination_problem(policy, number)

    assert problem is not None and "always blocked" in problem


def test_blocked_prefixes_cover_premium_and_satellite_ranges() -> None:
    assert {"+1900", "+1976", "+449", "+881", "+882", "+870", "+979"} <= set(BLOCKED_PREFIXES)


def test_policy_of_defaults_to_deny_and_drops_malformed_entries() -> None:
    assert policy_of(None).dialing_enabled is False
    assert policy_of({"telephony": "on"}).allowed_prefixes == []
    policy = policy_of(
        {
            "telephony": {
                "allowed_prefixes": ["+1", "44", "+", "+1 555"],
                "allowed_sip_hosts": ["PBX.Example.com."],
            }
        }
    )
    assert policy.allowed_prefixes == ["+1", "+1555"]
    assert policy.allowed_sip_hosts == ["pbx.example.com"]
    assert (policy.max_calls_per_min, policy.max_concurrent_outbound) == (10, 5)
    broken = policy_of({"telephony": {"allowed_prefixes": ["+1"], "max_calls_per_min": "lots"}})
    assert broken.allowed_prefixes == ["+1"] and broken.max_calls_per_min == 10


async def test_place_call_policy_less_workspace_is_422_with_an_actionable_message(
    admin_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    await _trunk(admin_client, world, "outbound")
    await _set_policy(database, None)

    response = await admin_client.post("/v1/calls", json={"agent_id": world.agent_id, "to_e164": CALLEE})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "destination_not_allowed"
    assert "allowed number prefixes" in error["message"]
    assert error["details"]["allowed_prefixes"] == []
    assert world.lk.named("create_sip_participant") == []


async def test_place_call_off_list_and_premium_numbers_are_422_and_never_dialed(
    admin_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    await _trunk(admin_client, world, "outbound")
    off_list = await admin_client.post(
        "/v1/calls", json={"agent_id": world.agent_id, "to_e164": "+14155550100"}
    )
    await _set_policy(database, {"allowed_prefixes": ["+1"]})
    premium = await admin_client.post(
        "/v1/calls", json={"agent_id": world.agent_id, "to_e164": "+19005550100"}
    )

    assert off_list.status_code == 422
    assert off_list.json()["error"]["code"] == "destination_not_allowed"
    assert off_list.json()["error"]["details"]["allowed_prefixes"] == ["+1555"]
    assert premium.status_code == 422
    assert "always blocked" in premium.json()["error"]["message"]
    assert world.lk.named("create_sip_participant") == []
    async with database.session() as session:
        assert (await session.execute(select(Call))).scalars().all() == []


async def test_policy_set_through_the_workspace_route_opens_dialing(
    admin_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    await _set_policy(database, None)
    await _trunk(admin_client, world, "outbound")
    response = await admin_client.put(
        f"/v1/workspaces/{DEFAULT_WORKSPACE_ID}",
        json={"settings": {"telephony": {"allowed_prefixes": ["+1555"], "max_calls_per_min": 3}}},
    )
    assert response.status_code == 200, response.text

    call = await admin_client.post("/v1/calls", json={"agent_id": world.agent_id, "to_e164": CALLEE})

    assert call.status_code == 201, call.text


async def test_eleventh_call_in_a_minute_is_429_rate_limited(
    admin_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    await _trunk(admin_client, world, "outbound")
    await _set_policy(database, {**TEST_POLICY, "max_concurrent_outbound": 100})
    await set_agent_columns(database, world.agent_id, limits={"max_concurrent_sessions": 100})

    statuses = [
        (
            await admin_client.post("/v1/calls", json={"agent_id": world.agent_id, "to_e164": CALLEE})
        ).status_code
        for _ in range(11)
    ]

    assert statuses == [201] * 10 + [429]
    last = await admin_client.post("/v1/calls", json={"agent_id": world.agent_id, "to_e164": CALLEE})
    assert last.json()["error"]["code"] == "rate_limited"


async def test_api_key_calls_share_the_workspace_bucket(
    app: FastAPI, admin_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    await _trunk(admin_client, world, "outbound")
    await _set_policy(database, {**TEST_POLICY, "max_calls_per_min": 2, "max_concurrent_outbound": 100})
    await set_agent_columns(database, world.agent_id, limits={"max_concurrent_sessions": 100})
    _key_id, raw = await make_api_key(database, ["calls:write", "sessions:read"])
    body = {"agent_id": world.agent_id, "to_e164": CALLEE}

    first = await admin_client.post("/v1/calls", json=body)
    async with key_client(app, raw) as keyed:
        second = await keyed.post("/v1/calls", json=body)
        third = await keyed.post("/v1/calls", json=body)
        off_list = await keyed.post("/v1/calls", json={**body, "to_e164": "+14155550100"})

    assert (first.status_code, second.status_code, third.status_code) == (201, 201, 429), third.text
    assert third.json()["error"]["code"] == "rate_limited"
    assert off_list.status_code == 422


async def test_open_outbound_cap_and_agent_session_limit_are_429_calls_busy(
    admin_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    await _trunk(admin_client, world, "outbound")
    await _set_policy(database, {**TEST_POLICY, "max_concurrent_outbound": 1})
    body = {"agent_id": world.agent_id, "to_e164": CALLEE}

    first = await admin_client.post("/v1/calls", json=body)  # answered by the fake: still open
    workspace_cap = await admin_client.post("/v1/calls", json=body)
    await _set_policy(database, {**TEST_POLICY, "max_concurrent_outbound": 50})
    await set_agent_columns(database, world.agent_id, limits={"max_concurrent_sessions": 1})
    agent_cap = await admin_client.post("/v1/calls", json=body)

    assert first.status_code == 201
    assert workspace_cap.status_code == 429
    assert workspace_cap.json()["error"]["code"] == "calls_busy"
    assert workspace_cap.json()["error"]["details"]["max_concurrent_outbound"] == 1
    assert agent_cap.status_code == 429
    assert agent_cap.json()["error"]["details"]["max_concurrent_sessions"] == 1


async def test_console_transfer_policy_sip_host_and_numeric_user(
    admin_client: httpx.AsyncClient, world: World
) -> None:
    call = await _call(admin_client, world)

    evil = await admin_client.post(f"/v1/calls/{call['id']}/transfer", json={"to": "sip:100@evil.example"})
    off_list = await admin_client.post(f"/v1/calls/{call['id']}/transfer", json={"to": "+14155550100"})
    by_prefix = await admin_client.post(
        f"/v1/calls/{call['id']}/transfer", json={"to": "sip:+15551230000@carrier.example.net"}
    )

    assert evil.status_code == 422 and evil.json()["error"]["code"] == "destination_not_allowed"
    assert off_list.status_code == 422
    assert by_prefix.status_code == 200, by_prefix.text
    (refer,) = world.lk.named("transfer_sip_participant")
    assert refer.transfer_to == "sip:+15551230000@carrier.example.net"


async def test_dials_and_transfers_write_audit_rows(
    admin_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    call = await _call(admin_client, world)
    await admin_client.post(f"/v1/calls/{call['id']}/transfer", json={"to": "+15550001111"})

    async with database.session() as session:
        rows = (
            (
                await session.execute(
                    select(AuditLog)
                    .where(AuditLog.target_id == call["id"], AuditLog.action.like("call.%"))
                    .order_by(AuditLog.id)
                )
            )
            .scalars()
            .all()
        )

    assert [row.action for row in rows] == ["call.placed", "call.transferred"]
    assert rows[0].target_type == "calls"
    assert rows[0].payload is not None and rows[0].payload["to"] == CALLEE
    assert rows[1].payload is not None and rows[1].payload["to"] == "+15550001111"


async def test_internal_transfer_off_list_is_refused_for_the_model_and_audited(
    service_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    session_id = await _inbound_session(database, world)

    response = await service_client.post(
        f"/internal/v1/telephony/sessions/{session_id}/transfer",
        json={"to": "+19005550100", "participant_identity": "sip_caller"},
    )

    assert response.status_code == 200
    assert response.json() | {"call_id": None} == {
        "ok": False,
        "status": "refused",
        "call_id": None,
        "reason": NOT_ALLOWED_TO_MODEL,
    }
    assert world.lk.named("transfer_sip_participant") == []
    assert await _audit_actions(database) == ["call.transfer_refused"]


# ------------------------------------------- R-V2-21 save-time destination checks
def _targets(*pairs: tuple[str, str]) -> dict[str, Any]:
    return {"transfer_targets": [{"label": label, "to": to} for label, to in pairs]}


async def _put_config(client: httpx.AsyncClient, agent_id: str, **config: Any) -> httpx.Response:
    body: dict[str, Any] = json.loads(inference_config().model_dump_json())
    body.update(config)
    return await client.put(f"/v1/agents/{agent_id}", json={"config": body})


def _issue_paths(response: httpx.Response) -> list[str]:
    return [issue["path"] for issue in response.json()["error"]["details"]["issues"]]


@pytest.mark.parametrize(
    "to", ["+19005550100", "sip:x@unknown-host.example", "+14155550100"], ids=["premium", "sip", "off-list"]
)
async def test_saving_a_transfer_target_outside_the_policy_is_422_at_its_path(
    admin_client: httpx.AsyncClient, world: World, database: Database, to: str
) -> None:
    await _set_policy(database, {"allowed_prefixes": ["+1555", "+1900"]})  # a listed prefix still loses

    response = await _put_config(
        admin_client, world.agent_id, telephony=_targets(("Desk", "+15550001111"), ("Other", to))
    )

    assert response.status_code == 422, response.text
    assert "telephony.transfer_targets[1].to" in _issue_paths(response)
    assert "telephony.transfer_targets[0].to" not in _issue_paths(response)


async def test_saving_allowed_targets_round_trips_and_duplicate_labels_are_422(
    admin_client: httpx.AsyncClient, world: World
) -> None:
    targets = _targets(("Sales", "+15550001111"), ("Desk", "sip:desk@pbx.example.com"))
    saved = await _put_config(admin_client, world.agent_id, telephony=targets)
    duplicate = await _put_config(
        admin_client, world.agent_id, telephony=_targets(("Sales", "+15550001111"), ("sales", "+15550002222"))
    )

    assert saved.status_code == 200, saved.text
    assert saved.json()["config"]["telephony"] == targets
    assert duplicate.status_code == 422
    assert _issue_paths(duplicate) == ["telephony.transfer_targets[1].label"]


async def test_policy_less_workspace_refuses_every_saved_target_and_flow_transfer_node(
    admin_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    await _set_policy(database, None)
    flow = {
        "nodes": [
            {"id": "start", "kind": "start", "greeting": "Hello."},
            {"id": "ask", "kind": "agent", "instructions": "Ask what they need."},
            {"id": "xfer", "kind": "transfer", "to": "+15550001111"},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "ask", "condition": "always"},
            {"id": "e2", "source": "ask", "target": "xfer", "condition": "They want a person."},
        ],
    }

    response = await _put_config(
        admin_client, world.agent_id, telephony=_targets(("Sales", "+15550001111")), flow=flow
    )

    assert response.status_code == 422
    assert {"telephony.transfer_targets[0].to", "flow.nodes[2].to"} <= set(_issue_paths(response))


def test_telephony_validator_without_a_policy_checks_labels_only() -> None:
    config = inference_config().model_copy(
        update={
            "telephony": TelephonyConfigModel.model_validate(
                _targets(("A", "+19005550100"), ("a", "+15550001111"))
            )
        }
    )

    issues = telephony_issues(ValidationContext(config=config))

    assert [i.path for i in issues] == ["telephony.transfer_targets[1].label"]


# ------------------------------------------------ R-V2-22 variables to the worker
async def test_call_variables_reach_the_resolved_config_and_survive_the_summary(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient, world: World
) -> None:
    call = await _call(admin_client, world, variables={"claim_id": "C-1"})
    session_id = call["session_id"]

    resolved = await service_client.get(f"/internal/v1/sessions/{session_id}/resolved")
    summary = await service_client.put(
        f"/internal/v1/sessions/{session_id}/summary",
        json={"status": "ended", "usage": {}, "transcript": [], "variables": {}},
    )
    detail = await admin_client.get(f"/v1/sessions/{session_id}")

    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["variables"] == {"claim_id": "C-1"}
    assert summary.status_code == 204
    assert detail.json()["variables"] == {"claim_id": "C-1"}


# ---------------------------------------------------------- R-V2-24 stuck calls
async def _sweep_row(
    database: Database,
    world: World,
    *,
    direction: str,
    call_status: str,
    session_status: str,
    age_s: float,
    started: bool = False,
) -> tuple[str, str]:
    born = utcnow() - dt.timedelta(seconds=age_s)
    async with database.session() as session:
        sess = SessionRow(
            id=new_id(),
            workspace_id=DEFAULT_WORKSPACE_ID,
            agent_id=world.agent_id,
            connection_id=world.connection_id,
            config_version=1,
            room_name=f"lkap-call-{new_id()[:8]}",
            participant_identity="sip-x",
            participant_name="",
            status=session_status,
            pipeline_mode="cascaded",
            channel="sip_out" if direction == "outbound" else "sip_in",
            created_at=born,
        )
        session.add(sess)
        await session.flush()
        call = Call(
            id=new_id(),
            session_id=sess.id,
            workspace_id=DEFAULT_WORKSPACE_ID,
            connection_id=world.connection_id,
            direction=direction,
            to_e164=CALLEE,
            status=call_status,
            started_at=born if started else None,
        )
        session.add(call)
    return call.id, sess.id


async def test_sweep_fails_an_outbound_dial_that_never_ran(world: World, database: Database) -> None:
    call_id, session_id = await _sweep_row(
        database, world, direction="outbound", call_status="dialing", session_status="created", age_s=240
    )

    assert await sweep_stuck_calls(database) == 1

    row = await _call_row(database, call_id)
    assert (row.status, row.hangup_reason) == ("failed", SWEPT_DIAL_REASON)
    async with database.session() as session:
        sess = await session.get(SessionRow, session_id)
    assert sess is not None and sess.status == "failed"


async def test_sweep_leaves_young_dials_and_inbound_calls_in_progress(
    world: World, database: Database
) -> None:
    young, _ = await _sweep_row(
        database,
        world,
        direction="outbound",
        call_status="ringing",
        session_status="created",
        age_s=STUCK_DIAL_AFTER_S - 30,
        started=True,
    )
    inbound, _ = await _sweep_row(
        database, world, direction="inbound", call_status="dialing", session_status="active", age_s=240
    )

    assert await sweep_stuck_calls(database) == 0

    assert (await _call_row(database, young)).status == "ringing"
    assert (await _call_row(database, inbound)).status == "dialing"


async def test_sweep_closes_open_calls_whose_session_ended(world: World, database: Database) -> None:
    answered, _ = await _sweep_row(
        database, world, direction="inbound", call_status="answered", session_status="ended", age_s=10
    )
    dialing, _ = await _sweep_row(
        database, world, direction="outbound", call_status="dialing", session_status="failed", age_s=10
    )

    assert await sweep_stuck_calls(database) == 2

    assert (await _call_row(database, answered)).status == "completed"
    assert (await _call_row(database, dialing)).status == "failed"


async def test_a_late_worker_report_on_a_swept_call_is_a_no_op(
    service_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    call_id, session_id = await _sweep_row(
        database, world, direction="outbound", call_status="dialing", session_status="active", age_s=240
    )
    await sweep_stuck_calls(database)

    response = await service_client.post(
        "/internal/v1/telephony/calls/report", json={"session_id": session_id, "status": "answered"}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    row = await _call_row(database, call_id)
    assert (row.status, row.answered_at) == ("failed", None)
