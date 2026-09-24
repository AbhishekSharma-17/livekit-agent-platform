"""LiveKit-hosted phone numbers (V4-05, docs/v4/PHONE-NUMBERS.md §8).

Two fakes stand in for LiveKit: the in-process Twirp server of
``connection_fakes`` answers ``PhoneNumberService`` in JSON over a real socket
(so the shim's codec, signing and error mapping run for real), and
``telephony_fakes.FakeLiveKitApi`` records the SIP service calls (dispatch
rules). Both append to one ``timeline`` so ordering can be asserted. Numbers
are ``+1555…`` examples only.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import livekit.api
import pytest
from alembic.config import Config
from conftest import inference_config
from connection_fakes import KEY_B, SECRET_B, add_agent, connection_row, fake_livekit, hosted_number
from connection_fakes import FakeLiveKit as TwirpFake
from fastapi import FastAPI
from google.protobuf.json_format import MessageToJson
from livekit.api import (
    AccessToken,
    CreateSIPDispatchRuleRequest,
    ListSIPDispatchRuleRequest,
    ListSIPDispatchRuleResponse,
    ServerError,
    SIPDispatchRuleInfo,
)
from livekit.protocol.models import ParticipantInfo, Room
from livekit.protocol.webhook import WebhookEvent
from sqlalchemy import select
from telephony_fakes import FakeClientFactory, FakeLiveKitApi

from alembic import command
from lkap_api.connections.clients import ConnectionClientFactory, get_client_factory
from lkap_api.db.constants import DEFAULT_WORKSPACE_ID
from lkap_api.db.models import AuditLog, Call, PhoneNumber, SipDispatchRule, Workspace, new_id
from lkap_api.db.session import Database
from lkap_api.settings import Settings
from lkap_api.telephony import phone_numbers as shim
from lkap_api.telephony.common import LiveKitUpstreamError
from lkap_api.telephony.phone_numbers import LkPhoneNumber, PhoneNumberClient, PhoneNumbersUnavailableError
from lkap_api.vault import Vault

HOSTED = "+15550100001"
HOSTED_2 = "+15550100002"
HOSTED_3 = "+15550100003"


# ----------------------------------------------------------------------------- fakes
@dataclass
class HostedFakeApi(FakeLiveKitApi):
    """The SIP fake plus ``list_dispatch_rule`` (the conflict pre-check) and a shared timeline."""

    existing_rules: list[SIPDispatchRuleInfo] = field(default_factory=list)
    timeline: list[str] = field(default_factory=list)

    def _record(self, method: str, request: Any) -> None:
        self.timeline.append(f"sip.{method}")
        super()._record(method, request)

    async def list_dispatch_rule(self, request: ListSIPDispatchRuleRequest) -> ListSIPDispatchRuleResponse:
        self._record("list_dispatch_rule", request)
        return ListSIPDispatchRuleResponse(items=self.existing_rules)


@dataclass
class Hosted:
    """A SIP-capable connection whose url is the Twirp fake, an agent on it, and both fakes."""

    twirp: TwirpFake
    lk: HostedFakeApi
    connection_id: str
    agent_id: str
    agent_name: str

    def phone_methods(self) -> list[str]:
        return [method for method, *_ in self.twirp.phone_requests]


async def _seed_connection(
    database: Database,
    settings: Settings,
    url: str,
    *,
    sip: bool = True,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> tuple[str, str, str]:
    vault = Vault(settings.master_key)
    async with database.session() as session:
        conn = connection_row(
            vault,
            slug=f"lk-{new_id()[:6]}",
            url=url,
            capabilities={"sip_enabled": sip},
            workspace_id=workspace_id,
        )
        session.add(conn)
        await session.flush()
        agent = await add_agent(session, inference_config(), connection_id=conn.id)
        agent.workspace_id = workspace_id
    return conn.id, agent.id, conn.agent_name


@pytest.fixture
async def hosted(app: FastAPI, settings: Settings, database: Database) -> AsyncIterator[Hosted]:
    async with fake_livekit() as (twirp, url):
        lk = HostedFakeApi(timeline=twirp.timeline)
        factory = FakeClientFactory(Vault(settings.master_key), lk)
        app.dependency_overrides[get_client_factory] = lambda: factory
        connection_id, agent_id, agent_name = await _seed_connection(database, settings, url)
        try:
            yield Hosted(twirp, lk, connection_id, agent_id, agent_name)
        finally:
            app.dependency_overrides.pop(get_client_factory, None)


async def _refresh(client: httpx.AsyncClient, connection_id: str) -> httpx.Response:
    """Refresh one connection (the workspace's bootstrap connection is never contacted)."""
    return await client.post("/v1/telephony/numbers/refresh", json={"connection_id": connection_id})


async def _numbers(client: httpx.AsyncClient) -> dict[str, dict[str, Any]]:
    items = (await client.get("/v1/telephony/numbers")).json()["items"]
    return {item["e164"]: item for item in items}


async def _mirrored(client: httpx.AsyncClient, hosted: Hosted, *numbers: dict[str, Any]) -> dict[str, Any]:
    """Put ``numbers`` in the project, refresh, and return the first one's row."""
    hosted.twirp.phone_numbers.extend(numbers)
    response = await _refresh(client, hosted.connection_id)
    assert response.status_code == 200, response.text
    return (await _numbers(client))[numbers[0]["e164_format"]]


# ------------------------------------------------------------------------------ shim
async def _client_for(settings: Settings, url: str) -> tuple[ConnectionClientFactory, Any]:
    vault = Vault(settings.master_key)
    return ConnectionClientFactory(vault), connection_row(vault, url=url)


async def test_shim_list_follows_pages_signs_with_sip_admin_and_posts_json(settings: Settings) -> None:
    async with fake_livekit(phone_page_size=2) as (twirp, url):
        twirp.phone_numbers.extend(
            [hosted_number(HOSTED, "PN_1"), hosted_number(HOSTED_2, "PN_2"), hosted_number(HOSTED_3, "PN_3")]
        )
        factory, row = await _client_for(settings, url)
        async with factory.phone_numbers(row) as client:
            numbers = await client.list()

    assert [n.id for n in numbers] == ["PN_1", "PN_2", "PN_3"]
    assert [method for method, *_ in twirp.phone_requests] == ["ListPhoneNumbers", "ListPhoneNumbers"]
    first, second = twirp.phone_requests
    assert first[1] == {
        "statuses": [
            "PHONE_NUMBER_STATUS_ACTIVE",
            "PHONE_NUMBER_STATUS_PENDING",
            "PHONE_NUMBER_STATUS_OFFLINE",
        ],
        "limit": shim.PAGE_LIMIT,
    }
    assert second[1]["pageToken"] == {"token": "2"}
    assert {content_type for _, _, content_type, _ in twirp.phone_requests} == {"application/json"}
    assert first[3]["sip"]["admin"] is True  # R-V4-12: SIPGrants(admin=True)


@pytest.mark.parametrize("camel", [False, True])
async def test_shim_parses_both_key_styles_and_lowers_enums(settings: Settings, camel: bool) -> None:
    async with fake_livekit(phone_camel=camel) as (twirp, url):
        twirp.phone_numbers.append(
            hosted_number(
                HOSTED,
                "PN_1",
                status="PHONE_NUMBER_STATUS_OFFLINE",
                inbound_status="PHONE_NUMBER_IN_STATUS_UNSPECIFIED",
                rule_ids=["SDR_a", "SDR_b"],
                name="Front desk",
            )
        )
        factory, row = await _client_for(settings, url)
        async with factory.phone_numbers(row) as client:
            (number,) = await client.list()

    assert number.e164_format == HOSTED
    assert (number.status, number.inbound_status, number.outbound_status) == ("offline", "unknown", "unknown")
    assert number.number_type == "local"
    assert number.rule_ids == ["SDR_a", "SDR_b"]
    assert number.display_region == "San Francisco, CA"
    assert number.name == "Front desk"
    assert number.created_at is None and number.assigned_at is not None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("PHONE_NUMBER_STATUS_ACTIVE", "active"),
        ("PHONE_NUMBER_STATUS_PENDING", "pending"),
        ("PHONE_NUMBER_STATUS_RELEASED", "released"),
        ("PHONE_NUMBER_STATUS_UNSPECIFIED", "unknown"),
        ("offline", "offline"),
        (3, "unknown"),
    ],
)
def test_lk_phone_number_status_lowering(raw: object, expected: str) -> None:
    assert LkPhoneNumber.model_validate({"id": "PN", "status": raw}).status == expected


def test_lk_phone_number_toll_free_type_is_not_split() -> None:
    assert LkPhoneNumber.model_validate(
        {"id": "PN", "numberType": "PHONE_NUMBER_TYPE_TOLL_FREE"}
    ).number_type == ("toll_free")


async def test_shim_unauthenticated_is_409_phone_numbers_unavailable(settings: Settings) -> None:
    async with fake_livekit(
        phone_fail={"ListPhoneNumbers": (401, {"code": "unauthenticated", "msg": "no"})}
    ) as (
        _twirp,
        url,
    ):
        factory, row = await _client_for(settings, url)
        async with factory.phone_numbers(row) as client:
            with pytest.raises(PhoneNumbersUnavailableError) as caught:
                await client.list()

    assert caught.value.status_code == 409
    assert caught.value.code == "phone_numbers_unavailable"
    assert "SIPGrants(admin=True)" in caught.value.message


async def test_shim_server_error_is_502(settings: Settings) -> None:
    async with fake_livekit(phone_fail={"GetPhoneNumber": (500, {"code": "internal", "msg": "boom"})}) as (
        _t,
        url,
    ):
        factory, row = await _client_for(settings, url)
        async with factory.phone_numbers(row) as client:
            with pytest.raises(LiveKitUpstreamError) as caught:
                await client.get("PN_1")

    assert caught.value.status_code == 502


def test_shim_has_only_list_get_update() -> None:
    public = {name for name in vars(PhoneNumberClient) if not name.startswith("_")}
    assert public == {"list", "get", "update"}
    source = Path(shim.__file__).read_text()
    assert "application/protobuf" not in source


def test_sentinel_livekit_api_has_no_phone_number_service_yet() -> None:
    """D-V4-16 removal condition: when this fails, replace the Twirp-JSON shim with SDK calls."""
    assert not hasattr(livekit.api.LiveKitAPI, "phone_number"), (
        "livekit-api now ships PhoneNumberService: replace lkap_api/telephony/phone_numbers.py with SDK calls"
    )
    assert not hasattr(livekit.api, "PhoneNumberService")


# --------------------------------------------------------------------------- refresh
async def test_refresh_inserts_livekit_rows(admin_client: httpx.AsyncClient, hosted: Hosted) -> None:
    hosted.twirp.phone_numbers.extend(
        [
            hosted_number(HOSTED, "PN_1", name="Support line"),
            hosted_number(HOSTED_2, "PN_2", status="PHONE_NUMBER_STATUS_OFFLINE"),
        ]
    )

    response = await _refresh(admin_client, hosted.connection_id)

    assert response.status_code == 200, response.text
    (result,) = response.json()
    assert result | {"warnings": []} == {
        "connection_id": hosted.connection_id,
        "seen": 2,
        "added": 2,
        "updated": 0,
        "released": 0,
        "conflicts": [],
        "warnings": [],
    }
    assert result["warnings"] == ["1 number(s) offline in LiveKit"]
    rows = await _numbers(admin_client)
    first = rows[HOSTED]
    assert first["source"] == "livekit"
    assert first["connection_id"] == hosted.connection_id
    assert first["lk_number_id"] == "PN_1"
    assert first["trunk_id"] is None
    assert first["region"] == "San Francisco, CA"
    assert first["label"] == "Support line"
    assert (first["lk_status"], first["lk_inbound_status"], first["attach_state"]) == (
        "active",
        "detached",
        "not_routed",
    )
    assert first["lk_synced_at"] is not None
    assert rows[HOSTED_2]["attach_state"] == "offline"


async def test_refresh_is_idempotent(admin_client: httpx.AsyncClient, hosted: Hosted) -> None:
    await _mirrored(admin_client, hosted, hosted_number(HOSTED, "PN_1"))

    (again,) = (await _refresh(admin_client, hosted.connection_id)).json()

    assert (again["seen"], again["added"], again["updated"], again["released"]) == (1, 0, 0, 0)
    assert len(await _numbers(admin_client)) == 1


async def test_refresh_marks_a_vanished_number_released(
    admin_client: httpx.AsyncClient, hosted: Hosted
) -> None:
    await _mirrored(admin_client, hosted, hosted_number(HOSTED, "PN_1"))
    hosted.twirp.phone_numbers.clear()

    (result,) = (await _refresh(admin_client, hosted.connection_id)).json()

    assert result["released"] == 1
    row = (await _numbers(admin_client))[HOSTED]
    assert (row["lk_status"], row["attach_state"]) == ("released", "released")


async def test_refresh_reports_a_trunk_number_as_conflict(
    admin_client: httpx.AsyncClient, hosted: Hosted
) -> None:
    created = await admin_client.post("/v1/telephony/numbers", json={"e164": HOSTED})
    assert created.status_code == 201, created.text
    hosted.twirp.phone_numbers.append(hosted_number(HOSTED, "PN_1"))

    (result,) = (await _refresh(admin_client, hosted.connection_id)).json()

    assert result["conflicts"] == [HOSTED]
    assert result["added"] == 0
    assert (await _numbers(admin_client))[HOSTED]["source"] == "trunk"


async def test_refresh_without_sip_is_409_sip_disabled(
    admin_client: httpx.AsyncClient, app: FastAPI, settings: Settings, database: Database
) -> None:
    async with fake_livekit() as (twirp, url):
        factory = FakeClientFactory(Vault(settings.master_key), HostedFakeApi())
        app.dependency_overrides[get_client_factory] = lambda: factory
        connection_id, _agent, _name = await _seed_connection(database, settings, url, sip=False)
        try:
            response = await _refresh(admin_client, connection_id)
        finally:
            app.dependency_overrides.pop(get_client_factory, None)

    assert response.status_code == 409
    assert response.json()["error"]["details"]["reason"] == "sip_disabled"
    assert twirp.phone_requests == []


async def test_refresh_relays_the_grant_refusal_as_409(
    admin_client: httpx.AsyncClient, hosted: Hosted
) -> None:
    hosted.twirp.phone_fail["ListPhoneNumbers"] = (403, {"code": "permission_denied", "msg": "no sip grant"})

    response = await _refresh(admin_client, hosted.connection_id)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "phone_numbers_unavailable"


async def test_refresh_is_audited(
    admin_client: httpx.AsyncClient, hosted: Hosted, database: Database
) -> None:
    await _mirrored(admin_client, hosted, hosted_number(HOSTED, "PN_1"))

    async with database.session() as session:
        rows = list(
            await session.scalars(select(AuditLog).where(AuditLog.action == "telephony.numbers.refresh"))
        )

    assert [(row.target_id, row.payload["added"]) for row in rows] == [(hosted.connection_id, 1)]


# ------------------------------------------------------------------------ assignment
def _dispatch_metadata(create: CreateSIPDispatchRuleRequest) -> dict[str, Any]:
    (agent,) = create.dispatch_rule.room_config.agents
    return {"agent_name": agent.agent_name, **json.loads(agent.metadata)}


async def test_assign_creates_a_trunkless_rule_for_the_number_then_attaches_it(
    admin_client: httpx.AsyncClient, hosted: Hosted
) -> None:
    number = await _mirrored(admin_client, hosted, hosted_number(HOSTED, "PN_1"))

    response = await admin_client.put(
        f"/v1/telephony/numbers/{number['id']}", json={"inbound_agent_id": hosted.agent_id}
    )

    assert response.status_code == 200, response.text
    (create,) = hosted.lk.named("create_dispatch_rule")
    info = create.dispatch_rule
    assert list(info.trunk_ids) == []
    assert list(info.numbers) == [HOSTED]
    assert list(info.inbound_numbers) == []
    metadata = _dispatch_metadata(create)
    assert metadata["agent_name"] == hosted.agent_name
    assert (metadata["agent_id"], metadata["connection_id"], metadata["channel"]) == (
        hosted.agent_id,
        hosted.connection_id,
        "sip_in",
    )
    assert info.rule.dispatch_rule_individual.room_prefix == "call-"
    method, body, _ctype, _claims = hosted.twirp.phone_requests[-1]
    assert (method, body) == ("UpdatePhoneNumber", {"id": "PN_1", "sipDispatchRuleId": "SDR_1"})
    assert hosted.twirp.timeline.index("sip.create_dispatch_rule") < len(hosted.twirp.timeline) - 1
    saved = response.json()
    assert saved["dispatch_rule_id"] is not None
    assert saved["inbound_agent_id"] == hosted.agent_id
    assert saved["lk_rule_ids"] == ["SDR_1"]
    assert saved["attach_state"] == "routed"
    assert saved["warnings"] == []
    (rule,) = (await admin_client.get("/v1/telephony/dispatch-rules")).json()["items"]
    assert (rule["trunk_id"], rule["phone_number_id"], rule["managed_by_number"]) == (
        None,
        number["id"],
        HOSTED,
    )


async def test_assign_an_agent_of_another_connection_is_422(
    admin_client: httpx.AsyncClient, hosted: Hosted, database: Database, settings: Settings
) -> None:
    number = await _mirrored(admin_client, hosted, hosted_number(HOSTED, "PN_1"))
    _other_conn, other_agent, _name = await _seed_connection(database, settings, "wss://other.livekit.cloud")

    response = await admin_client.put(
        f"/v1/telephony/numbers/{number['id']}", json={"inbound_agent_id": other_agent}
    )

    assert response.status_code == 422
    assert "number's LiveKit project" in response.json()["error"]["message"]
    assert hosted.lk.named("create_dispatch_rule") == []
    assert "UpdatePhoneNumber" not in hosted.phone_methods()


async def test_assign_warns_about_a_project_catch_all_rule(
    admin_client: httpx.AsyncClient, hosted: Hosted
) -> None:
    hosted.lk.existing_rules = [
        SIPDispatchRuleInfo(sip_dispatch_rule_id="SDR_other", name="another app"),
        SIPDispatchRuleInfo(sip_dispatch_rule_id="SDR_num", name="old", numbers=[HOSTED], trunk_ids=["ST_x"]),
    ]
    number = await _mirrored(admin_client, hosted, hosted_number(HOSTED, "PN_1"))

    response = await admin_client.put(
        f"/v1/telephony/numbers/{number['id']}", json={"inbound_agent_id": hosted.agent_id}
    )

    assert response.status_code == 200, response.text
    warnings = response.json()["warnings"]
    assert any("another app" in w and "every call" in w for w in warnings)
    assert any("'old'" in w and HOSTED in w for w in warnings)


async def test_livekit_conflict_is_relayed_as_422_and_leaves_no_rule(
    admin_client: httpx.AsyncClient, hosted: Hosted, database: Database
) -> None:
    number = await _mirrored(admin_client, hosted, hosted_number(HOSTED, "PN_1"))
    hosted.lk.fail["create_dispatch_rule"] = ServerError(
        "already_exists", "Conflicting SIP Dispatch Rules: same Trunk+Number+PIN combination", status=409
    )

    response = await admin_client.put(
        f"/v1/telephony/numbers/{number['id']}", json={"inbound_agent_id": hosted.agent_id}
    )

    assert response.status_code == 422
    assert "Conflicting SIP Dispatch Rules" in response.json()["error"]["message"]
    async with database.session() as session:
        assert list(await session.scalars(select(SipDispatchRule))) == []
    assert "UpdatePhoneNumber" not in hosted.phone_methods()
    assert (await _numbers(admin_client))[HOSTED]["inbound_agent_id"] is None


async def test_attach_refusal_deletes_the_new_rule_again(
    admin_client: httpx.AsyncClient, hosted: Hosted, database: Database
) -> None:
    number = await _mirrored(admin_client, hosted, hosted_number(HOSTED, "PN_1"))
    hosted.twirp.phone_fail["UpdatePhoneNumber"] = (400, {"code": "invalid_argument", "msg": "bad rule"})

    response = await admin_client.put(
        f"/v1/telephony/numbers/{number['id']}", json={"inbound_agent_id": hosted.agent_id}
    )

    assert response.status_code == 422
    assert [d.sip_dispatch_rule_id for d in hosted.lk.named("delete_dispatch_rule")] == ["SDR_1"]
    async with database.session() as session:
        assert list(await session.scalars(select(SipDispatchRule))) == []


async def test_a_hosted_number_refuses_a_trunk(admin_client: httpx.AsyncClient, hosted: Hosted) -> None:
    number = await _mirrored(admin_client, hosted, hosted_number(HOSTED, "PN_1"))

    response = await admin_client.put(f"/v1/telephony/numbers/{number['id']}", json={"trunk_id": "whatever"})

    assert response.status_code == 422
    assert "has no trunk" in response.json()["error"]["message"]


async def test_reattach_replaces_the_managed_rule(admin_client: httpx.AsyncClient, hosted: Hosted) -> None:
    number = await _mirrored(admin_client, hosted, hosted_number(HOSTED, "PN_1"))
    url = f"/v1/telephony/numbers/{number['id']}"
    await admin_client.put(url, json={"inbound_agent_id": hosted.agent_id})
    # Someone re-points the number in the LiveKit dashboard; Refresh shows it detached.
    hosted.twirp.phone_numbers[0]["sip_dispatch_rule_ids"] = ["SDR_elsewhere"]
    await _refresh(admin_client, hosted.connection_id)
    assert (await _numbers(admin_client))[HOSTED]["attach_state"] == "detached"

    response = await admin_client.put(url, json={"inbound_agent_id": hosted.agent_id})

    assert response.status_code == 200, response.text
    assert response.json()["attach_state"] == "routed"
    assert [d.sip_dispatch_rule_id for d in hosted.lk.named("delete_dispatch_rule")] == ["SDR_1"]
    assert response.json()["lk_rule_ids"] == ["SDR_2"]


# ------------------------------------------------------------------ unassign, delete
async def test_unassign_detaches_before_deleting_the_rule(
    admin_client: httpx.AsyncClient, hosted: Hosted
) -> None:
    number = await _mirrored(admin_client, hosted, hosted_number(HOSTED, "PN_1"))
    url = f"/v1/telephony/numbers/{number['id']}"
    await admin_client.put(url, json={"inbound_agent_id": hosted.agent_id})
    hosted.twirp.timeline.clear()

    response = await admin_client.put(url, json={"inbound_agent_id": None})

    assert response.status_code == 200, response.text
    timeline = hosted.twirp.timeline
    assert timeline.index("phone.UpdatePhoneNumber") < timeline.index("sip.delete_dispatch_rule")
    detach = [body for method, body, *_ in hosted.twirp.phone_requests if method == "UpdatePhoneNumber"][-1]
    assert detach == {"id": "PN_1", "sipDispatchRuleId": ""}
    saved = response.json()
    assert (saved["inbound_agent_id"], saved["dispatch_rule_id"], saved["attach_state"]) == (
        None,
        None,
        "not_routed",
    )
    assert saved["lk_inbound_status"] == "detached"


async def test_unassign_tolerates_livekit_refusing_the_empty_rule_id(
    admin_client: httpx.AsyncClient, hosted: Hosted, database: Database
) -> None:
    number = await _mirrored(admin_client, hosted, hosted_number(HOSTED, "PN_1"))
    url = f"/v1/telephony/numbers/{number['id']}"
    await admin_client.put(url, json={"inbound_agent_id": hosted.agent_id})
    hosted.twirp.phone_reject_empty_rule = True

    response = await admin_client.put(url, json={"inbound_agent_id": None})

    assert response.status_code == 200, response.text
    assert [d.sip_dispatch_rule_id for d in hosted.lk.named("delete_dispatch_rule")] == ["SDR_1"]
    async with database.session() as session:
        assert list(await session.scalars(select(SipDispatchRule))) == []


async def test_delete_detaches_forgets_and_never_gives_the_number_back(
    admin_client: httpx.AsyncClient, hosted: Hosted, database: Database
) -> None:
    number = await _mirrored(admin_client, hosted, hosted_number(HOSTED, "PN_1"))
    await admin_client.put(
        f"/v1/telephony/numbers/{number['id']}", json={"inbound_agent_id": hosted.agent_id}
    )

    response = await admin_client.delete(f"/v1/telephony/numbers/{number['id']}")

    assert response.status_code == 204
    assert await _numbers(admin_client) == {}
    assert set(hosted.phone_methods()) <= {"ListPhoneNumbers", "GetPhoneNumber", "UpdatePhoneNumber"}
    assert [d.sip_dispatch_rule_id for d in hosted.lk.named("delete_dispatch_rule")] == ["SDR_1"]
    assert len(hosted.twirp.phone_numbers) == 1  # still in the project
    async with database.session() as session:
        assert list(await session.scalars(select(PhoneNumber))) == []


async def test_deleting_the_managed_rule_unroutes_the_hosted_number(
    admin_client: httpx.AsyncClient, hosted: Hosted
) -> None:
    number = await _mirrored(admin_client, hosted, hosted_number(HOSTED, "PN_1"))
    saved = (
        await admin_client.put(
            f"/v1/telephony/numbers/{number['id']}", json={"inbound_agent_id": hosted.agent_id}
        )
    ).json()

    response = await admin_client.delete(f"/v1/telephony/dispatch-rules/{saved['dispatch_rule_id']}")

    assert response.status_code == 204
    row = (await _numbers(admin_client))[HOSTED]
    assert (row["inbound_agent_id"], row["attach_state"]) == (None, "not_routed")
    assert "UpdatePhoneNumber" in hosted.phone_methods()


# -------------------------------------------------------------------------- webhooks
def _signed(event: WebhookEvent) -> tuple[str, dict[str, str]]:
    body = MessageToJson(event)
    digest = base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()
    token = AccessToken(KEY_B, SECRET_B).with_sha256(digest).to_jwt()
    return body, {"Authorization": token, "Content-Type": "application/webhook+json"}


def _joined(room: str, called: str) -> WebhookEvent:
    participant = ParticipantInfo(
        identity="sip_caller",
        kind=ParticipantInfo.Kind.SIP,
        attributes={
            "sip.callID": f"SCL_{room}",
            "sip.phoneNumber": "+15557654321",
            "sip.trunkPhoneNumber": called,
            "sip.trunkID": "ST_livekit_internal",
            "sip.callStatus": "active",
        },
    )
    return WebhookEvent(
        event="participant_joined", id=f"EV_{room}", room=Room(name=room), participant=participant
    )


async def _calls(database: Database) -> list[Call]:
    async with database.session() as session:
        return list(await session.scalars(select(Call)))


async def test_webhook_leg_to_a_hosted_number_creates_the_inbound_call(
    client: httpx.AsyncClient, admin_client: httpx.AsyncClient, hosted: Hosted, database: Database
) -> None:
    await _mirrored(admin_client, hosted, hosted_number(HOSTED, "PN_1"))
    body, headers = _signed(_joined("call-hosted", HOSTED))

    response = await client.post(f"/hooks/livekit/{hosted.connection_id}", content=body, headers=headers)

    assert response.status_code == 204
    (call,) = await _calls(database)
    assert (call.direction, call.to_e164, call.from_e164, call.status) == (
        "inbound",
        HOSTED,
        "+15557654321",
        "answered",
    )


async def test_webhook_leg_to_another_workspaces_number_is_ignored(
    client: httpx.AsyncClient, hosted: Hosted, database: Database, settings: Settings
) -> None:
    other_ws = new_id()
    async with database.session() as session:
        session.add(Workspace(id=other_ws, slug=f"other-{other_ws[:6]}", name="Other", settings={}))
    other_conn, _agent, _name = await _seed_connection(
        database, settings, "wss://other.livekit.cloud", workspace_id=other_ws
    )
    async with database.session() as session:
        session.add(
            PhoneNumber(
                id=new_id(),
                workspace_id=other_ws,
                e164=HOSTED,
                source="livekit",
                connection_id=other_conn,
                lk_number_id="PN_other",
                lk_rule_ids=[],
                region="",
            )
        )
    body, headers = _signed(_joined("call-foreign", HOSTED))

    response = await client.post(f"/hooks/livekit/{hosted.connection_id}", content=body, headers=headers)

    assert response.status_code == 204
    assert await _calls(database) == []


# ------------------------------------------------------------------------- migration
API_ROOT = Path(__file__).resolve().parents[1]


def _alembic(database: Path, target: str, *, downgrade: bool = False) -> None:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    config.cmd_opts = None  # type: ignore[assignment]
    config.attributes["configure_logger"] = False
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database}")
    previous = os.environ.get("LKAP_DATABASE_URL")
    os.environ["LKAP_DATABASE_URL"] = f"sqlite+aiosqlite:///{database}"
    try:
        (command.downgrade if downgrade else command.upgrade)(config, target)
    finally:
        if previous is None:
            os.environ.pop("LKAP_DATABASE_URL", None)
        else:
            os.environ["LKAP_DATABASE_URL"] = previous


def test_migration_backfills_managed_rules_and_downgrade_drops_hosted_rows(tmp_path: Path) -> None:
    """v4_001 on a seeded v3 database: the backfill, then a downgrade that deletes what v3 cannot hold.

    Parent rows (workspace, connection, agent) are left out: ``sqlite3`` runs with
    foreign keys off, and only the three telephony tables matter here.
    """
    database = tmp_path / "lkap.db"
    _alembic(database, "v3_001_agent_keys")
    ws, conn, agent, trunk = "w" * 32, "c" * 32, "a" * 32, "t" * 32
    rule_sql = (
        "INSERT INTO sip_dispatch_rules (id, workspace_id, connection_id, trunk_id, numbers, agent_id, "
        "room_prefix, created_at) VALUES (?, ?, ?, ?, ?, ?, 'call-', '2026-01-01')"
    )
    number_sql = (
        "INSERT INTO phone_numbers (id, workspace_id, e164, trunk_id, inbound_agent_id, label) "
        "VALUES (?, ?, ?, ?, ?, '')"
    )
    with sqlite3.connect(database) as db:
        db.execute(number_sql, ("n1", ws, HOSTED, trunk, agent))
        db.execute(number_sql, ("n2", ws, HOSTED_2, trunk, None))
        db.execute(rule_sql, ("r1", ws, conn, trunk, json.dumps([HOSTED]), agent))
        db.execute(rule_sql, ("r2", ws, conn, trunk, json.dumps([]), agent))

    _alembic(database, "head")

    with sqlite3.connect(database) as db:
        assert dict(db.execute("SELECT id, phone_number_id FROM sip_dispatch_rules")) == {
            "r1": "n1",
            "r2": None,
        }
        assert dict(db.execute("SELECT id, source FROM phone_numbers")) == {"n1": "trunk", "n2": "trunk"}
        db.execute(
            "INSERT INTO phone_numbers (id, workspace_id, e164, source, connection_id, lk_number_id, "
            "lk_rule_ids, region, label) VALUES ('n3', ?, ?, 'livekit', ?, 'PN_1', '[]', '', '')",
            (ws, HOSTED_3, conn),
        )
        db.execute(
            "INSERT INTO sip_dispatch_rules (id, workspace_id, connection_id, trunk_id, numbers, agent_id, "
            "room_prefix, created_at, phone_number_id) "
            "VALUES ('r3', ?, ?, NULL, ?, ?, 'call-', '2026-01-01', 'n3')",
            (ws, conn, json.dumps([HOSTED_3]), agent),
        )

    _alembic(database, "v3_001_agent_keys", downgrade=True)

    with sqlite3.connect(database) as db:
        assert sorted(row[0] for row in db.execute("SELECT id FROM sip_dispatch_rules")) == ["r1", "r2"]
        assert sorted(row[0] for row in db.execute("SELECT id FROM phone_numbers")) == ["n1", "n2"]
        notnull = {row[1]: row[3] for row in db.execute("PRAGMA table_info(sip_dispatch_rules)")}
        assert notnull["trunk_id"] == 1 and "phone_number_id" not in notnull

    _alembic(database, "head")

    with sqlite3.connect(database) as db:
        assert dict(db.execute("SELECT id, phone_number_id FROM sip_dispatch_rules")) == {
            "r1": "n1",
            "r2": None,
        }
