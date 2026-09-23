"""`POST /hooks/livekit/{connection_id}`: per-connection verification and handlers (V2-03)."""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Iterator

import httpx
import pytest
from conftest import inference_config
from connection_fakes import KEY_B, SECRET_B, add_agent, connection_row
from google.protobuf.json_format import MessageToJson
from livekit.api import AccessToken
from livekit.protocol.egress import EgressInfo, EgressStatus
from livekit.protocol.models import ParticipantInfo, Room
from livekit.protocol.webhook import WebhookEvent
from sqlalchemy import select

from lkap_api.connections.webhooks import WEBHOOK_HANDLERS, WebhookContext, register_webhook_handler
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.models import SessionEvent, new_id
from lkap_api.db.session import Database
from lkap_api.settings import Settings
from lkap_api.vault import Vault


def _signed(event: WebhookEvent, *, key: str = KEY_B, secret: str = SECRET_B) -> tuple[str, dict[str, str]]:
    body = MessageToJson(event)
    digest = base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()
    token = AccessToken(key, secret).with_sha256(digest).to_jwt()
    return body, {"Authorization": token, "Content-Type": "application/webhook+json"}


async def _seed(database: Database, settings: Settings, *, status: str) -> tuple[str, str, str]:
    """Create connection B, an agent on it and one session; return (connection, session, room)."""
    vault = Vault(settings.master_key)
    async with database.session() as session:
        conn = connection_row(vault)
        session.add(conn)
        await session.flush()
        agent = await add_agent(session, inference_config(), connection_id=conn.id)
        row = SessionRow(
            id=new_id(),
            agent_id=agent.id,
            connection_id=conn.id,
            config_version=1,
            room_name=f"lkap-{new_id()[:8]}",
            participant_identity="user-x",
            participant_name="X",
            status=status,
            pipeline_mode="cascaded",
            recording_egress_id="EG_123",
            recording_status="active",
        )
        session.add(row)
    return conn.id, row.id, row.room_name


async def _session(database: Database, session_id: str) -> SessionRow:
    async with database.session() as session:
        row = await session.get(SessionRow, session_id)
    assert row is not None
    return row


async def test_webhook_with_wrong_signature_is_401(
    client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    connection_id, _session_id, room = await _seed(database, settings, status="created")
    body, headers = _signed(
        WebhookEvent(event="room_finished", room=Room(name=room)), secret="some-other-secret-value-000000"
    )

    response = await client.post(f"/hooks/livekit/{connection_id}", content=body, headers=headers)

    assert response.status_code == 401


async def test_webhook_with_tampered_body_is_401(
    client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    connection_id, _session_id, room = await _seed(database, settings, status="created")
    body, headers = _signed(WebhookEvent(event="room_finished", room=Room(name=room)))

    response = await client.post(
        f"/hooks/livekit/{connection_id}", content=body.replace(room, "lkap-other"), headers=headers
    )

    assert response.status_code == 401


async def test_webhook_for_unknown_connection_is_401(client: httpx.AsyncClient) -> None:
    body, headers = _signed(WebhookEvent(event="room_finished", room=Room(name="x")))

    response = await client.post("/hooks/livekit/does-not-exist", content=body, headers=headers)

    assert response.status_code == 401


async def test_room_finished_marks_never_started_session_failed(
    client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    connection_id, session_id, room = await _seed(database, settings, status="created")
    body, headers = _signed(WebhookEvent(event="room_finished", room=Room(name=room), id="EV_1"))

    response = await client.post(f"/hooks/livekit/{connection_id}", content=body, headers=headers)

    row = await _session(database, session_id)
    assert response.status_code == 204, response.text
    assert (row.status, row.error) == ("failed", "never started")
    async with database.session() as session:
        events = (
            await session.execute(select(SessionEvent).where(SessionEvent.session_id == session_id))
        ).scalars()
        types = [(e.type, e.payload["event_id"]) for e in events]
    assert types == [("room_finished", "EV_1")]


async def test_room_finished_leaves_an_active_session_to_the_worker(
    client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    connection_id, session_id, room = await _seed(database, settings, status="active")
    body, headers = _signed(WebhookEvent(event="room_finished", room=Room(name=room)))

    await client.post(f"/hooks/livekit/{connection_id}", content=body, headers=headers)

    assert (await _session(database, session_id)).status == "active"


async def test_participant_left_appends_an_event(
    client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    connection_id, session_id, room = await _seed(database, settings, status="active")
    event = WebhookEvent(
        event="participant_left", room=Room(name=room), participant=ParticipantInfo(identity="u")
    )
    body, headers = _signed(event)

    response = await client.post(f"/hooks/livekit/{connection_id}", content=body, headers=headers)

    assert response.status_code == 204
    async with database.session() as session:
        stored = (
            await session.execute(select(SessionEvent).where(SessionEvent.session_id == session_id))
        ).scalar_one()
    assert stored.payload["identity"] == "u"


async def test_a_replayed_event_id_is_acknowledged_but_not_handled_twice(
    client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    """V2-21: a captured, still-valid delivery posted again appends nothing."""
    connection_id, session_id, room = await _seed(database, settings, status="active")
    event = WebhookEvent(
        event="participant_left",
        id="EV_replay",
        room=Room(name=room),
        participant=ParticipantInfo(identity="u"),
    )
    body, headers = _signed(event)
    url = f"/hooks/livekit/{connection_id}"

    first = await client.post(url, content=body, headers=headers)
    replay = await client.post(url, content=body, headers=headers)

    assert (first.status_code, replay.status_code) == (204, 204)
    async with database.session() as session:
        rows = (
            (await session.execute(select(SessionEvent).where(SessionEvent.session_id == session_id)))
            .scalars()
            .all()
        )
    assert len(rows) == 1


@pytest.mark.parametrize(
    ("egress_status", "expected"),
    [(EgressStatus.EGRESS_COMPLETE, "ready"), (EgressStatus.EGRESS_FAILED, "failed")],
)
async def test_egress_ended_sets_recording_status(
    client: httpx.AsyncClient, database: Database, settings: Settings, egress_status: int, expected: str
) -> None:
    connection_id, session_id, _room = await _seed(database, settings, status="active")
    event = WebhookEvent(
        event="egress_ended", egress_info=EgressInfo(egress_id="EG_123", status=egress_status)
    )
    body, headers = _signed(event)

    response = await client.post(f"/hooks/livekit/{connection_id}", content=body, headers=headers)

    assert response.status_code == 204
    assert (await _session(database, session_id)).recording_status == expected


@pytest.fixture
def seen_events() -> Iterator[list[str]]:
    seen: list[str] = []

    async def handler(ctx: WebhookContext) -> None:
        seen.append(f"{ctx.connection.slug}:{ctx.event.event}")

    register_webhook_handler("track_published")(handler)
    yield seen
    WEBHOOK_HANDLERS["track_published"].remove(handler)


async def test_registered_webhook_handler_receives_the_event(
    client: httpx.AsyncClient, database: Database, settings: Settings, seen_events: list[str]
) -> None:
    connection_id, _session_id, room = await _seed(database, settings, status="active")
    body, headers = _signed(WebhookEvent(event="track_published", room=Room(name=room)))

    response = await client.post(f"/hooks/livekit/{connection_id}", content=body, headers=headers)

    assert response.status_code == 204
    assert seen_events == ["conn-b:track_published"]
