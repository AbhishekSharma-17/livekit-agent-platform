"""V6-26b (docs/v6/_asks.md #151): `AgentPublicOut.avatar_framing` on the public agent view.

The public `GET /v1/agents/{slug}` (and `connect`, which returns the same projection) carries the
avatar's display hints — `AvatarOptions.framing`/`.fit` as stored plus the avatar provider's
registry `avatar_aspect` — so a real `/s/[slug]`, embed or test-mode session frames the avatar the
way the builder chose. Display hints only: never the provider id or any other config.
"""

from __future__ import annotations

import json

import httpx
from conftest import create_agent, inference_config
from lkap_contracts.agent_config import AgentConfig, AvatarOptions, ProviderRef

from lkap_api.db.models import Agent
from lkap_api.db.session import Database
from lkap_api.routers.agents import _public_avatar_framing

#: The platform's own web origin — the public session page calls connect from it.
WEB = {"Origin": "http://localhost:3000"}

_PUBLIC_KEYS = {
    "id",
    "slug",
    "name",
    "description",
    "ui_panel_id",
    "panel",
    "capabilities",
    "pipeline_mode",
    "avatar_framing",
}


def _avatar_config(provider_id: str, options: AvatarOptions | None = None) -> AgentConfig:
    base = inference_config()
    pipeline = base.pipeline.model_copy(
        update={
            "avatar": ProviderRef(provider_id=provider_id),
            "avatar_options": options or AvatarOptions(),
        }
    )
    return base.model_copy(update={"pipeline": pipeline})


async def _create(
    admin_client: httpx.AsyncClient, database: Database, config: AgentConfig
) -> dict[str, object]:
    """A published agent whose *stored* config carries `config`'s avatar.

    Written straight to the row: saving an avatar through the api needs the vendor's credential
    and a `full`-image connection (validation), neither of which this projection depends on.
    """
    agent = await create_agent(admin_client, name="Avatar desk")
    async with database.session() as session:
        row = await session.get(Agent, agent["id"])
        assert row is not None
        row.config = json.loads(config.model_dump_json())
    return agent


async def test_public_get_carries_portrait_cover_avatar_framing(
    client: httpx.AsyncClient, admin_client: httpx.AsyncClient, database: Database
) -> None:
    config = _avatar_config("lemonslice-avatar", AvatarOptions(framing="portrait", fit="cover"))
    agent = await _create(admin_client, database, config)

    response = await client.get(f"/v1/agents/{agent['slug']}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == _PUBLIC_KEYS
    assert body["avatar_framing"] == {"framing": "portrait", "fit": "cover", "declared_aspect": "portrait"}
    # Nothing else from the pipeline leaks: no provider id, no avatar options beyond the hints.
    assert "lemonslice" not in response.text
    assert "participant_name" not in response.text


async def test_public_get_without_an_avatar_has_null_avatar_framing(
    client: httpx.AsyncClient, admin_client: httpx.AsyncClient
) -> None:
    agent = await create_agent(admin_client, name="Voice only")

    body = (await client.get(f"/v1/agents/{agent['slug']}")).json()

    assert set(body) == _PUBLIC_KEYS
    assert body["avatar_framing"] is None


async def test_public_get_keeps_unset_avatar_options_unset(
    client: httpx.AsyncClient, admin_client: httpx.AsyncClient, database: Database
) -> None:
    """A pre-V6-26 agent (no framing/fit saved) with an undocumented-aspect vendor: all `None`,
    which the stage renders as its crop-free `auto` + `contain` default."""
    agent = await _create(admin_client, database, _avatar_config("bey-avatar"))

    body = (await client.get(f"/v1/agents/{agent['slug']}")).json()

    assert body["avatar_framing"] == {"framing": None, "fit": None, "declared_aspect": None}


async def test_connect_response_agent_carries_avatar_framing(
    client: httpx.AsyncClient, admin_client: httpx.AsyncClient, database: Database
) -> None:
    """`connect` returns the same projection — the session room reads it from here once connected
    (this is also how the console's `?mode=test` preview gets the provider's declared aspect)."""
    config = _avatar_config("anam-avatar", AvatarOptions(framing="auto", fit="contain"))
    agent = await _create(admin_client, database, config)

    response = await client.post(
        f"/v1/agents/{agent['slug']}/connect", json={"participant_name": "Ada"}, headers=WEB
    )

    assert response.status_code == 200, response.text
    assert response.json()["agent"]["avatar_framing"] == {
        "framing": "auto",
        "fit": "contain",
        "declared_aspect": "landscape",
    }


def test_public_avatar_framing_of_an_unknown_provider_drops_only_the_declared_aspect() -> None:
    """A provider id the registry no longer knows must never 500 the public route."""
    config = _avatar_config("retired-avatar", AvatarOptions(framing="square", fit="cover"))

    framing = _public_avatar_framing(config)

    assert framing is not None
    assert framing.model_dump() == {"framing": "square", "fit": "cover", "declared_aspect": None}
