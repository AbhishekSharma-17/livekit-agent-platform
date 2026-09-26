"""`POST /v1/agents/{id}/text-sessions` and `GET /v1/agents/{id}/embed-policy` (V2-18).

`start_text_session` reuses `routers.connect`'s protection helpers directly, so
this file proves the reuse (one origin-403 test, one channel/row-shape test)
rather than re-running every case `test_connect_limits.py` already covers.
"""

from __future__ import annotations

from typing import Any

import httpx
import jwt
from auth_helpers import set_agent_columns
from conftest import create_agent
from sqlalchemy import select

from lkap_api.db.models import Session as SessionRow
from lkap_api.db.session import Database
from lkap_api.settings import Settings

WEB = {"Origin": "http://localhost:3000"}


async def _start(client: httpx.AsyncClient, agent: dict[str, Any], **kwargs: Any) -> httpx.Response:
    return await client.post(f"/v1/agents/{agent['id']}/text-sessions", json=kwargs.pop("json", {}), **kwargs)


# ------------------------------------------------------------------- text-sessions


async def test_start_text_session_mints_a_channel_text_session_row(
    client: httpx.AsyncClient, admin_client: httpx.AsyncClient, database: Database
) -> None:
    agent = await create_agent(admin_client)

    response = await _start(client, agent, headers=WEB)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["protocolVersion"] == 1
    assert body["agent"]["slug"] == agent["slug"]
    async with database.session() as session:
        row = (await session.execute(select(SessionRow))).scalar_one()
    assert row.id == body["sessionId"]
    assert row.channel == "text"
    assert row.status == "created"


async def test_start_text_session_from_a_disallowed_origin_is_403(
    client: httpx.AsyncClient, admin_client: httpx.AsyncClient
) -> None:
    agent = await create_agent(admin_client)

    response = await _start(client, agent, headers={"Origin": "https://evil.example"})

    assert response.status_code == 403
    assert response.json()["error"]["details"]["origin"] == "https://evil.example"


async def test_start_text_session_privileged_reaches_an_unpublished_agent(
    admin_client: httpx.AsyncClient,
) -> None:
    agent = await create_agent(admin_client, published=False)

    response = await _start(admin_client, agent)

    assert response.status_code == 200, response.text
    assert response.json()["sessionId"]


async def test_start_text_session_unpublished_is_403_for_an_unprivileged_caller(
    client: httpx.AsyncClient, admin_client: httpx.AsyncClient
) -> None:
    agent = await create_agent(admin_client, published=False)

    response = await _start(client, agent, headers=WEB)

    assert response.status_code == 403


# --------------------------------------------------------------------- embed-policy


async def test_embed_policy_returns_the_published_agents_allowed_origins(
    client: httpx.AsyncClient, admin_client: httpx.AsyncClient, database: Database
) -> None:
    agent = await create_agent(admin_client)
    await set_agent_columns(database, str(agent["id"]), allowed_origins=["https://shop.example"])

    response = await client.get(f"/v1/agents/{agent['id']}/embed-policy")

    assert response.status_code == 200, response.text
    assert response.json() == {"allowed_origins": ["https://shop.example"]}


async def test_embed_policy_default_allowed_origins_is_empty(
    client: httpx.AsyncClient, admin_client: httpx.AsyncClient
) -> None:
    agent = await create_agent(admin_client)

    response = await client.get(f"/v1/agents/{agent['id']}/embed-policy")

    assert response.status_code == 200, response.text
    assert response.json() == {"allowed_origins": []}


async def test_embed_policy_unpublished_agent_is_403_for_an_anonymous_caller(
    client: httpx.AsyncClient, admin_client: httpx.AsyncClient
) -> None:
    agent = await create_agent(admin_client, published=False)

    response = await client.get(f"/v1/agents/{agent['id']}/embed-policy")

    assert response.status_code == 403


async def test_embed_policy_unknown_agent_is_404(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/agents/does-not-exist/embed-policy")

    assert response.status_code == 404


# ------------------------------------------------------------ caller timezone (R-V5-10)
def _attributes(response: httpx.Response, settings: Settings) -> dict[str, str] | None:
    claims: dict[str, Any] = jwt.decode(
        response.json()["participantToken"],
        settings.livekit_api_secret,
        algorithms=["HS256"],
        issuer=settings.livekit_api_key,
    )
    attributes: dict[str, str] | None = claims.get("attributes")
    return attributes


async def test_start_text_session_stamps_the_timezone_field_as_lkap_tz(
    client: httpx.AsyncClient, admin_client: httpx.AsyncClient, settings: Settings
) -> None:
    agent = await create_agent(admin_client)

    response = await _start(client, agent, json={"timezone": "Asia/Kolkata"}, headers=WEB)

    assert response.status_code == 200, response.text
    assert _attributes(response, settings) == {"lkap.tz": "Asia/Kolkata"}


async def test_start_text_session_reads_the_metadata_timezone_too(
    client: httpx.AsyncClient, admin_client: httpx.AsyncClient, settings: Settings
) -> None:
    agent = await create_agent(admin_client)

    response = await _start(
        client, agent, json={"participant_metadata": {"timezone": "Europe/London"}}, headers=WEB
    )

    assert _attributes(response, settings) == {"lkap.tz": "Europe/London"}


async def test_start_text_session_drops_an_invalid_timezone_without_an_error(
    client: httpx.AsyncClient, admin_client: httpx.AsyncClient, settings: Settings
) -> None:
    agent = await create_agent(admin_client)

    response = await _start(client, agent, json={"timezone": "Nowhere/Land"}, headers=WEB)

    assert response.status_code == 200, response.text
    assert _attributes(response, settings) is None
