"""V5-43: the signed link hook (`POST /v1/hooks/link/{session_id}`) and the new panel blocks' checks.

LiveKit is faked at the boundary (`ConnectionClientFactory.api`), as in the whisper tests.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field

import httpx
import pytest
from conftest import inference_config
from connection_fakes import add_agent, connection_row
from fastapi import FastAPI
from livekit.api import ListParticipantsRequest
from livekit.protocol.models import DataPacket, ParticipantInfo
from livekit.protocol.room import ListParticipantsResponse
from lkap_contracts.agent_config import PanelLayout, ToolsConfig
from lkap_contracts.common import ProviderRef
from lkap_contracts.telephony import TelephonyConfig, TransferTarget
from lkap_contracts.ui_protocol import TOPIC_UI_LINK, BlockSpec, LinkCompletedPacket
from telephony_fakes import FakeClientFactory, FakeLiveKitApi

from lkap_api.config_service import ValidationContext, validate
from lkap_api.connections.clients import get_client_factory
from lkap_api.db.constants import DEFAULT_WORKSPACE_ID
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.models import WebhookEndpoint, Workspace, new_id
from lkap_api.db.session import Database
from lkap_api.panels import LINK_ON_PHONE_MESSAGE
from lkap_api.settings import Settings
from lkap_api.vault import Vault
from lkap_api.webhooks.signing import SIGNATURE_HEADER, sign

SECRET = "whsec-link-secret"


@dataclass
class RoomFake(FakeLiveKitApi):
    """The telephony fake plus `list_participants`."""

    participants: list[ParticipantInfo] = field(default_factory=list)

    async def list_participants(self, req: ListParticipantsRequest) -> ListParticipantsResponse:
        self._record("list_participants", req)
        return ListParticipantsResponse(participants=self.participants)


@dataclass
class Live:
    session_id: str
    room_name: str
    lk: RoomFake


@pytest.fixture
def room_fake(app: FastAPI, settings: Settings) -> Iterator[RoomFake]:
    lk = RoomFake(
        participants=[
            ParticipantInfo(identity="user-abc", kind=ParticipantInfo.Kind.STANDARD),
            ParticipantInfo(identity="agent-AJ_1", kind=ParticipantInfo.Kind.AGENT),
        ]
    )
    app.dependency_overrides[get_client_factory] = lambda: FakeClientFactory(Vault(settings.master_key), lk)
    yield lk
    app.dependency_overrides.pop(get_client_factory, None)


async def _endpoint(
    database: Database, settings: Settings, secret: str, *, workspace_id: str, enabled: bool
) -> None:
    vault = Vault(settings.master_key)
    async with database.session() as db:
        db.add(
            WebhookEndpoint(
                workspace_id=workspace_id,
                url="https://hooks.example.com/lkap",
                secret_ct=vault.encrypt({"secret": secret}),
                events=[],
                enabled=enabled,
            )
        )


async def _seed(database: Database, settings: Settings, *, status: str = "active") -> tuple[str, str]:
    vault = Vault(settings.master_key)
    session_id = new_id()
    room_name = f"lkap-{session_id[:8]}"
    async with database.session() as db:
        conn = connection_row(vault, slug=f"conn-{new_id()[:6]}", workspace_id=DEFAULT_WORKSPACE_ID)
        db.add(conn)
        await db.flush()
        agent = await add_agent(db, inference_config(), connection_id=conn.id)
        db.add(
            SessionRow(
                id=session_id,
                workspace_id=DEFAULT_WORKSPACE_ID,
                agent_id=agent.id,
                connection_id=conn.id,
                config_version=1,
                room_name=room_name,
                participant_identity="user-abc",
                participant_name="Caller",
                status=status,
                pipeline_mode="cascaded",
                channel="web",
            )
        )
    return session_id, room_name


@pytest.fixture
async def live(database: Database, settings: Settings, room_fake: RoomFake) -> AsyncIterator[Live]:
    await _endpoint(database, settings, SECRET, workspace_id=DEFAULT_WORKSPACE_ID, enabled=True)
    session_id, room_name = await _seed(database, settings)
    yield Live(session_id, room_name, room_fake)


def _signed(
    body: dict[str, object], secret: str = SECRET, *, t: int | None = None
) -> tuple[bytes, dict[str, str]]:
    raw = json.dumps(body).encode()
    return raw, {SIGNATURE_HEADER: sign(secret, raw, t=t), "Content-Type": "application/json"}


async def test_a_signed_outcome_reaches_only_the_agents_as_a_link_packet(
    client: httpx.AsyncClient, live: Live
) -> None:
    raw, headers = _signed({"reference": "CLM-20931", "status": "completed"})

    response = await client.post(f"/v1/hooks/link/{live.session_id}", content=raw, headers=headers)

    assert response.status_code == 202, response.text
    body = response.json()
    assert (
        body["session_id"] == live.session_id and body["status"] == "completed" and body["delivered_to"] == 1
    )
    (packet,) = live.lk.named("send_data")
    assert packet.room == live.room_name
    assert packet.topic == TOPIC_UI_LINK
    assert packet.kind == DataPacket.Kind.RELIABLE
    assert list(packet.destination_identities) == ["agent-AJ_1"]  # never the caller
    sent = LinkCompletedPacket.model_validate(json.loads(packet.data))
    assert sent.session_id == live.session_id and sent.reference == "CLM-20931" and sent.id == body["id"]


@pytest.mark.parametrize(
    "case",
    ["no_header", "wrong_secret", "tampered", "stale", "unknown_session"],
)
async def test_a_bad_signature_or_an_unknown_session_is_401(
    client: httpx.AsyncClient, live: Live, case: str
) -> None:
    raw, headers = _signed({"block_id": "pay", "status": "completed"})
    session_id = live.session_id
    if case == "no_header":
        headers.pop(SIGNATURE_HEADER)
    elif case == "wrong_secret":
        raw, headers = _signed({"block_id": "pay"}, "whsec-someone-else")
    elif case == "tampered":
        raw = raw.replace(b"completed", b"failed")
    elif case == "stale":
        raw, headers = _signed({"block_id": "pay"}, t=int(time.time()) - 3600)
    else:
        session_id = new_id()

    response = await client.post(f"/v1/hooks/link/{session_id}", content=raw, headers=headers)

    assert response.status_code == 401, response.text
    assert live.lk.named("send_data") == []


async def test_a_disabled_or_another_workspaces_endpoint_does_not_verify(
    client: httpx.AsyncClient, live: Live, database: Database, settings: Settings
) -> None:
    await _endpoint(database, settings, "whsec-disabled", workspace_id=DEFAULT_WORKSPACE_ID, enabled=False)
    async with database.session() as db:
        other = Workspace(slug=f"other-{new_id()[:6]}", name="Other")
        db.add(other)
        await db.flush()
        other_id = other.id
    await _endpoint(database, settings, "whsec-other", workspace_id=other_id, enabled=True)
    for secret in ("whsec-disabled", "whsec-other"):
        raw, headers = _signed({"block_id": "pay"}, secret)
        response = await client.post(f"/v1/hooks/link/{live.session_id}", content=raw, headers=headers)
        assert response.status_code == 401, secret


@pytest.mark.parametrize(
    "body",
    [{}, {"block_id": "pay", "status": "paid"}, {"block_id": "pay", "extra": 1}, {"reference": "has spaces"}],
)
async def test_a_signed_but_invalid_body_is_422(
    client: httpx.AsyncClient, live: Live, body: dict[str, object]
) -> None:
    raw, headers = _signed(body)
    response = await client.post(f"/v1/hooks/link/{live.session_id}", content=raw, headers=headers)
    assert response.status_code == 422, response.text
    assert live.lk.named("send_data") == []


async def test_a_session_that_is_not_live_is_409(
    client: httpx.AsyncClient, live: Live, database: Database, settings: Settings
) -> None:
    ended, _room = await _seed(database, settings, status="ended")
    raw, headers = _signed({"block_id": "pay"})
    response = await client.post(f"/v1/hooks/link/{ended}", content=raw, headers=headers)
    assert response.status_code == 409 and response.json()["error"]["code"] == "not_live"


async def test_no_agent_in_the_room_is_409(client: httpx.AsyncClient, live: Live) -> None:
    live.lk.participants = [ParticipantInfo(identity="user-abc", kind=ParticipantInfo.Kind.STANDARD)]
    raw, headers = _signed({"block_id": "pay"})
    response = await client.post(f"/v1/hooks/link/{live.session_id}", content=raw, headers=headers)
    assert response.status_code == 409 and response.json()["error"]["code"] == "no_agent"
    assert live.lk.named("send_data") == []


async def test_the_livekit_hook_keeps_its_path(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/hooks/livekit/does-not-exist", content=b"{}", headers={"Authorization": "x"}
    )
    assert response.status_code == 401


# ----------------------------------------------------------------------- panel checks


def _panel_issues(blocks: list[BlockSpec], **kwargs: object) -> list[tuple[str, str, str]]:
    config = inference_config(panel=PanelLayout(blocks=blocks), **kwargs)
    result = validate(ValidationContext(config=config))
    return [(i.path, i.severity, i.message) for i in result.issues if i.path.startswith("panel.")]


def test_a_link_block_needs_its_sites() -> None:
    issues = _panel_issues([BlockSpec(id="pay", type="link")])
    assert [(p, s) for p, s, _ in issues] == [("panel.blocks[0].config.allowed_hosts", "error")]
    bad = _panel_issues([BlockSpec(id="pay", type="link", config={"allowed_hosts": ["https://example.com"]})])
    assert bad and "not a site name" in bad[0][2]
    assert _panel_issues([BlockSpec(id="pay", type="link", config={"allowed_hosts": ["example.com"]})]) == []


def test_slots_and_cards_configs_are_strict() -> None:
    assert _panel_issues([BlockSpec(id="t", type="slots"), BlockSpec(id="c", type="cards")]) == []
    issues = _panel_issues([BlockSpec(id="c", type="cards", config={"max_cards": 99, "shiny": True})])
    assert {p for p, _, _ in issues} == {"panel.blocks[0].config.max_cards", "panel.blocks[0].config.shiny"}


def test_a_link_on_a_phone_agent_without_text_messages_gets_a_tip() -> None:
    link = BlockSpec(id="pay", type="link", config={"allowed_hosts": ["example.com"]})
    phone = TelephonyConfig(transfer_targets=[TransferTarget(label="Desk", to="+15550009999")])
    assert _panel_issues([link], telephony=phone) == [("panel.blocks[0]", "warning", LINK_ON_PHONE_MESSAGE)]
    texting = ToolsConfig(sms=ProviderRef(provider_id="twilio-sms"))
    assert _panel_issues([link], telephony=phone, tools=texting) == []
    assert LINK_ON_PHONE_MESSAGE.startswith("Tip:")
