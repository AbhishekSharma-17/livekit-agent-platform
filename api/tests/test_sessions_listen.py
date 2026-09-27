"""V5-37: supervisor listen-in (`POST /v1/sessions/{id}/listen-token`) and whisper.

LiveKit is faked at the boundary (`ConnectionClientFactory.api`); the listen token
is decoded and verified with the connection's own secret.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt
import pytest
from auth_helpers import key_client, login, make_api_key, make_user
from conftest import inference_config
from connection_fakes import KEY_B, SECRET_B, add_agent, connection_row
from fastapi import FastAPI
from livekit.api import ListParticipantsRequest, ServerError
from livekit.protocol.models import DataPacket, ParticipantInfo
from livekit.protocol.room import ListParticipantsResponse
from lkap_contracts.api_models import (
    LISTEN_TOKEN_TTL_S,
    MAX_WHISPER_CHARS,
    SUPERVISOR_TOPIC,
    SupervisorWhisperPacket,
)
from sqlalchemy import select, update
from telephony_fakes import FakeClientFactory, FakeLiveKitApi

from lkap_api.connections.clients import get_client_factory
from lkap_api.db.constants import DEFAULT_WORKSPACE_ID
from lkap_api.db.models import AuditLog, new_id
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.session import Database
from lkap_api.settings import Settings
from lkap_api.vault import Vault


@dataclass
class RoomFake(FakeLiveKitApi):
    """The telephony fake plus `list_participants` (the room's participants, by kind)."""

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
    factory = FakeClientFactory(Vault(settings.master_key), lk)
    app.dependency_overrides[get_client_factory] = lambda: factory
    yield lk
    app.dependency_overrides.pop(get_client_factory, None)


async def _seed_session(database: Database, settings: Settings, *, status: str = "active") -> tuple[str, str]:
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
    session_id, room_name = await _seed_session(database, settings)
    yield Live(session_id, room_name, room_fake)


async def _audit_rows(database: Database, action: str) -> list[AuditLog]:
    async with database.session() as db:
        return list((await db.execute(select(AuditLog).where(AuditLog.action == action))).scalars())


# --------------------------------------------------------------------------- listen token
async def test_listen_token_is_hidden_subscribe_only_one_room_and_short_lived(
    admin_client: httpx.AsyncClient, live: Live, database: Database
) -> None:
    response = await admin_client.post(f"/v1/sessions/{live.session_id}/listen-token")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["roomName"] == live.room_name
    assert body["sessionId"] == live.session_id
    assert body["serverUrl"] == "wss://project-b.livekit.cloud"
    assert body["identity"].startswith("supervisor:")
    claims = jwt.decode(body["participantToken"], SECRET_B, algorithms=["HS256"])
    assert claims["iss"] == KEY_B
    assert claims["sub"] == body["identity"]
    video = claims["video"]
    assert video["room"] == live.room_name
    assert video["roomJoin"] is True
    assert video["hidden"] is True
    assert video["canSubscribe"] is True
    assert video["canPublish"] is False
    assert video["canPublishData"] is False
    assert video["canPublishSources"] == []
    assert video["roomCreate"] is False
    assert video.get("roomAdmin") in (None, False)
    assert claims["attributes"] == {"lkap.role": "supervisor"}
    assert "roomConfig" not in claims  # never dispatches an agent
    assert claims["exp"] - claims["nbf"] == LISTEN_TOKEN_TTL_S
    (row,) = await _audit_rows(database, "session.listen")
    assert row.target_type == "session"
    assert row.target_id == live.session_id
    assert row.payload is not None and row.payload["identity"] == body["identity"]


async def test_listen_token_for_a_builder_member_names_the_supervisor(
    app: FastAPI, database: Database, live: Live
) -> None:
    user_id = await make_user(database, "lead@example.com", role="builder", name="Dana Lead")

    async with await login(app, "lead@example.com") as client:
        response = await client.post(f"/v1/sessions/{live.session_id}/listen-token")

    assert response.status_code == 200, response.text
    assert response.json()["identity"] == f"supervisor:{user_id}"
    assert response.json()["participantName"] == "Dana Lead"


async def test_listen_token_viewer_is_403(app: FastAPI, database: Database, live: Live) -> None:
    await make_user(database, "viewer@example.com", role="viewer")

    async with await login(app, "viewer@example.com") as client:
        token = await client.post(f"/v1/sessions/{live.session_id}/listen-token")
        whispered = await client.post(f"/v1/sessions/{live.session_id}/whisper", json={"text": "hi"})

    assert token.status_code == 403
    assert whispered.status_code == 403
    assert await _audit_rows(database, "session.listen") == []


@pytest.mark.parametrize(
    ("scopes", "expected"),
    [
        (["sessions:listen"], 200),
        (["sessions:write"], 200),
        (["sessions:read"], 403),
        (["agents:write"], 403),
    ],
)
async def test_listen_token_api_key_scopes(
    app: FastAPI, database: Database, live: Live, scopes: list[str], expected: int
) -> None:
    _, raw = await make_api_key(database, scopes)

    async with key_client(app, raw) as client:
        response = await client.post(f"/v1/sessions/{live.session_id}/listen-token")

    assert response.status_code == expected, response.text


@pytest.mark.parametrize("status", ["created", "ended", "failed"])
async def test_listen_token_on_a_session_that_is_not_live_is_409(
    admin_client: httpx.AsyncClient, database: Database, settings: Settings, room_fake: RoomFake, status: str
) -> None:
    session_id, _ = await _seed_session(database, settings, status=status)

    response = await admin_client.post(f"/v1/sessions/{session_id}/listen-token")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "not_live"


async def test_listen_token_without_a_connection_is_409(
    admin_client: httpx.AsyncClient, database: Database, live: Live
) -> None:
    async with database.session() as db:
        await db.execute(
            update(SessionRow).where(SessionRow.id == live.session_id).values(connection_id=None)
        )

    response = await admin_client.post(f"/v1/sessions/{live.session_id}/listen-token")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "not_live"


async def test_listen_token_unknown_session_is_404(
    admin_client: httpx.AsyncClient, room_fake: RoomFake
) -> None:
    response = await admin_client.post("/v1/sessions/nope/listen-token")

    assert response.status_code == 404


# --------------------------------------------------------------------------------- whisper
async def test_whisper_goes_only_to_the_agent_on_the_supervisor_topic(
    admin_client: httpx.AsyncClient, live: Live, database: Database
) -> None:
    response = await admin_client.post(
        f"/v1/sessions/{live.session_id}/whisper", json={"text": "  Offer the premium plan.  "}
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["delivered_to"] == 1
    (listing,) = live.lk.named("list_participants")
    assert listing.room == live.room_name
    (packet,) = live.lk.named("send_data")
    assert packet.room == live.room_name
    assert packet.topic == SUPERVISOR_TOPIC
    assert packet.kind == DataPacket.Kind.RELIABLE
    assert list(packet.destination_identities) == ["agent-AJ_1"]  # never the caller
    sent = SupervisorWhisperPacket.model_validate(json.loads(packet.data))
    assert sent.text == "Offer the premium plan."
    assert sent.session_id == live.session_id
    assert sent.id == body["id"]
    assert sent.reply_now is False
    (row,) = await _audit_rows(database, "session.whisper")
    assert row.target_id == live.session_id
    assert row.payload is not None
    assert row.payload["chars"] == len("Offer the premium plan.")
    assert row.payload["whisper_id"] == body["id"]
    assert "premium" not in json.dumps(row.payload)  # never the text


async def test_whisper_reply_now_is_carried(admin_client: httpx.AsyncClient, live: Live) -> None:
    response = await admin_client.post(
        f"/v1/sessions/{live.session_id}/whisper", json={"text": "Say goodbye.", "reply_now": True}
    )

    assert response.status_code == 202, response.text
    (packet,) = live.lk.named("send_data")
    assert json.loads(packet.data)["reply_now"] is True


async def test_whisper_without_an_agent_in_the_room_is_409(
    admin_client: httpx.AsyncClient, live: Live, database: Database
) -> None:
    live.lk.participants = [ParticipantInfo(identity="user-abc", kind=ParticipantInfo.Kind.STANDARD)]

    response = await admin_client.post(f"/v1/sessions/{live.session_id}/whisper", json={"text": "hello"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "no_agent"
    assert live.lk.named("send_data") == []
    assert await _audit_rows(database, "session.whisper") == []


@pytest.mark.parametrize("text", ["", "   ", "x" * (MAX_WHISPER_CHARS + 1)])
async def test_whisper_empty_or_too_long_is_422(
    admin_client: httpx.AsyncClient, live: Live, text: str
) -> None:
    response = await admin_client.post(f"/v1/sessions/{live.session_id}/whisper", json={"text": text})

    assert response.status_code == 422
    assert live.lk.named("send_data") == []


async def test_whisper_on_an_ended_session_is_409(
    admin_client: httpx.AsyncClient, database: Database, settings: Settings, room_fake: RoomFake
) -> None:
    session_id, _ = await _seed_session(database, settings, status="ended")

    response = await admin_client.post(f"/v1/sessions/{session_id}/whisper", json={"text": "hello"})

    assert response.status_code == 409
    assert room_fake.named("send_data") == []


async def test_whisper_livekit_failure_is_502(admin_client: httpx.AsyncClient, live: Live) -> None:
    live.lk.fail["send_data"] = ServerError("unavailable", "down", status=503)

    response = await admin_client.post(f"/v1/sessions/{live.session_id}/whisper", json={"text": "hello"})

    assert response.status_code == 502


async def test_whisper_with_a_sessions_listen_key(app: FastAPI, database: Database, live: Live) -> None:
    _, raw = await make_api_key(database, ["sessions:listen"])

    async with key_client(app, raw) as client:
        response = await client.post(f"/v1/sessions/{live.session_id}/whisper", json={"text": "hello"})

    assert response.status_code == 202, response.text
    sent: dict[str, Any] = json.loads(live.lk.named("send_data")[0].data)
    assert sent["by"] == "test key"
