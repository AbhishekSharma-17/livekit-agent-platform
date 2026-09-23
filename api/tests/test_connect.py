"""The browser `connect` endpoint: authorisation, session rows and dispatch."""

from __future__ import annotations

import json
from typing import Any

import httpx
import jwt
import pytest
from conftest import captured_text, create_agent, inference_config
from lkap_contracts.dispatch import DispatchMetadata
from sqlalchemy import select

from lkap_api.db.models import Session as SessionRow
from lkap_api.db.session import Database
from lkap_api.settings import Settings

#: The platform's own web origin (`LKAP_CORS_ORIGINS` default): the public session
#: page `/s/[slug]` calls connect from it, so it is allowed for every agent.
WEB = {"Origin": "http://localhost:3000"}


def _claims(token: str, settings: Settings) -> dict[str, Any]:
    decoded: dict[str, Any] = jwt.decode(
        token, settings.livekit_api_secret, algorithms=["HS256"], issuer=settings.livekit_api_key
    )
    return decoded


async def test_connect_returns_token_source_compatible_fields(
    client: httpx.AsyncClient, admin_client: httpx.AsyncClient, settings: Settings
) -> None:
    agent = await create_agent(admin_client, name="Public agent")

    response = await client.post(
        f"/v1/agents/{agent['slug']}/connect", json={"participant_name": "Ada"}, headers=WEB
    )

    body = response.json()
    assert response.status_code == 200, response.text
    assert body["serverUrl"] == settings.livekit_url
    assert body["participantName"] == "Ada"
    assert body["roomName"] == f"lkap-{body['sessionId'][:8]}"
    # `uiPanelId` mirrors `agent.panel.panel_id` for one release (R-V2-7), not
    # the stored `agents.ui_panel_id` column ("generic") returned by create.
    assert body["uiPanelId"] == body["agent"]["panel"]["panel_id"] == "composite"
    assert body["protocolVersion"] == 1
    assert body["agent"]["slug"] == agent["slug"]


async def test_connect_creates_a_session_row(
    client: httpx.AsyncClient, admin_client: httpx.AsyncClient, database: Database
) -> None:
    agent = await create_agent(admin_client)

    body = (await client.post(f"/v1/agents/{agent['id']}/connect", json={}, headers=WEB)).json()

    async with database.session() as session:
        row = (await session.execute(select(SessionRow))).scalar_one()
    assert row.id == body["sessionId"]
    assert row.status == "created"
    assert row.pipeline_mode == "cascaded"
    assert row.config_version == agent["config_version"]
    assert row.participant_identity.startswith("user-")


async def test_minted_token_dispatches_the_platform_agent_with_id_only_metadata(
    client: httpx.AsyncClient, admin_client: httpx.AsyncClient, settings: Settings
) -> None:
    agent = await create_agent(admin_client)

    body = (await client.post(f"/v1/agents/{agent['id']}/connect", json={}, headers=WEB)).json()

    claims = _claims(body["participantToken"], settings)
    agents = claims["roomConfig"]["agents"]
    assert len(agents) == 1
    assert agents[0]["agentName"] == settings.agent_name == "lkap-agent"

    metadata = json.loads(agents[0]["metadata"])
    assert set(metadata) == set(DispatchMetadata.model_fields)
    parsed = DispatchMetadata.model_validate(metadata)
    assert parsed.session_id == body["sessionId"]
    assert parsed.agent_id == agent["id"]
    assert parsed.config_version == agent["config_version"]
    assert claims["video"]["room"] == body["roomName"]


async def test_client_supplied_room_config_is_ignored(
    client: httpx.AsyncClient, admin_client: httpx.AsyncClient, settings: Settings
) -> None:
    agent = await create_agent(admin_client)

    body = (
        await client.post(
            f"/v1/agents/{agent['id']}/connect",
            json={
                "participant_name": "Mallory",
                "roomConfig": {"agents": [{"agentName": "other-project-agent"}]},
                "agentName": "other-project-agent",
            },
            headers=WEB,
        )
    ).json()

    claims = _claims(body["participantToken"], settings)
    assert [a["agentName"] for a in claims["roomConfig"]["agents"]] == ["lkap-agent"]


async def test_connect_to_an_unpublished_agent_is_forbidden_without_admin(
    client: httpx.AsyncClient, admin_client: httpx.AsyncClient
) -> None:
    agent = await create_agent(admin_client, published=False)

    response = await client.post(f"/v1/agents/{agent['id']}/connect", json={})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


async def test_an_admin_may_connect_to_an_unpublished_agent(
    admin_client: httpx.AsyncClient,
) -> None:
    agent = await create_agent(admin_client, published=False)

    response = await admin_client.post(f"/v1/agents/{agent['id']}/connect", json={})

    assert response.status_code == 200


async def test_connect_to_an_unknown_agent_is_404(client: httpx.AsyncClient) -> None:
    response = await client.post("/v1/agents/ghost/connect", json={})

    assert response.status_code == 404


async def test_connect_public_caller_identity_is_ignored_but_attributes_kept(
    client: httpx.AsyncClient, admin_client: httpx.AsyncClient, settings: Settings
) -> None:
    # F-13: a public caller may not pick its participant identity.
    agent = await create_agent(admin_client)

    body = (
        await client.post(
            f"/v1/agents/{agent['id']}/connect",
            json={"participant_identity": "customer-7", "participant_metadata": {"tier": "gold"}},
            headers=WEB,
        )
    ).json()

    claims = _claims(body["participantToken"], settings)
    assert claims["sub"] != "customer-7"
    assert claims["sub"].startswith("user-")
    assert claims["attributes"] == {"tier": "gold"}


async def test_connect_privileged_caller_identity_is_honoured(
    admin_client: httpx.AsyncClient, settings: Settings
) -> None:
    agent = await create_agent(admin_client)

    body = (
        await admin_client.post(
            f"/v1/agents/{agent['id']}/connect", json={"participant_identity": "customer-7"}
        )
    ).json()

    assert _claims(body["participantToken"], settings)["sub"] == "customer-7"


async def test_connect_never_logs_or_returns_configuration(
    client: httpx.AsyncClient,
    admin_client: httpx.AsyncClient,
    log_capture: pytest.LogCaptureFixture,
) -> None:
    marker = "NEVER-LEAK-THIS-PROMPT"
    config = json.loads(inference_config(instructions=marker).model_dump_json())
    agent = await create_agent(admin_client, name="Confidential agent", config=config)

    response = await client.post(f"/v1/agents/{agent['id']}/connect", json={}, headers=WEB)

    assert response.status_code == 200
    assert marker not in response.text
    assert "config" not in response.json()["agent"]
    assert marker not in captured_text(log_capture)
    assert response.json()["participantToken"] not in captured_text(log_capture)
