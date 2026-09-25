"""R-V5-10: the caller's timezone on the api side.

The token attribute (`lkap.tz`), the workspace default timezone, its use when an
agent is seeded from a pack, the effective business timezone in the resolved
config, and `caller_timezone` copied from the `locale` event into the summary.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from conftest import create_agent, inference_config
from lkap_contracts.agent_config import ResolvedAgentConfig
from sqlalchemy import select

from lkap_api.db.models import Session as SessionRow
from lkap_api.db.session import Database
from lkap_api.livekit_tokens import participant_attributes
from lkap_api.routers.workspaces import default_timezone_of

# ------------------------------------------------------------------ token attributes


@pytest.mark.parametrize(
    ("metadata", "timezone", "expected"),
    [
        ({"timezone": "Asia/Kolkata"}, None, {"lkap.tz": "Asia/Kolkata"}),
        ({"timezone": "Not/AZone"}, None, None),
        ({"timezone": "Not/AZone", "tier": "gold"}, None, {"tier": "gold"}),
        ({"lkap.tz": "Asia/Kolkata", "lkap.other": "x"}, None, None),
        ({"lkap.tz": "Asia/Kolkata", "timezone": "Europe/London"}, None, {"lkap.tz": "Europe/London"}),
        ({}, "America/New_York", {"lkap.tz": "America/New_York"}),
        ({"timezone": "Europe/London"}, "America/New_York", {"lkap.tz": "America/New_York"}),
        ({"timezone": "Europe/London"}, "Nowhere/Land", {"lkap.tz": "Europe/London"}),
        ({}, None, None),
    ],
    ids=[
        "valid",
        "invalid-dropped",
        "invalid-dropped-others-kept",
        "client-lkap-keys-dropped",
        "client-lkap-tz-replaced",
        "explicit",
        "explicit-wins",
        "invalid-explicit-falls-back",
        "empty",
    ],
)
def test_participant_attributes(
    metadata: dict[str, str], timezone: str | None, expected: dict[str, str] | None
) -> None:
    assert participant_attributes(metadata, timezone=timezone) == expected


@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        ({"locale": {"timezone": "Asia/Kolkata"}}, "Asia/Kolkata"),
        ({"locale": {"timezone": "nope"}}, None),
        ({"locale": "Asia/Kolkata"}, None),
        ({"timezone": "Asia/Kolkata"}, None),
        (None, None),
    ],
)
def test_default_timezone_of(settings: dict[str, Any] | None, expected: str | None) -> None:
    assert default_timezone_of(settings) == expected


# ------------------------------------------------------------------ workspace default


async def test_workspace_settings_accept_and_merge_a_locale_timezone(admin_client: httpx.AsyncClient) -> None:
    first = await admin_client.put(
        "/v1/workspaces/default", json={"settings": {"locale": {"timezone": "Asia/Kolkata"}}}
    )
    second = await admin_client.put("/v1/workspaces/default", json={"settings": {"locale": {"other": 1}}})

    assert first.status_code == 200, first.text
    assert second.json()["settings"]["locale"] == {"timezone": "Asia/Kolkata", "other": 1}


async def test_workspace_settings_clear_the_timezone_with_null(admin_client: httpx.AsyncClient) -> None:
    await admin_client.put(
        "/v1/workspaces/default", json={"settings": {"locale": {"timezone": "Asia/Kolkata"}}}
    )

    response = await admin_client.put(
        "/v1/workspaces/default", json={"settings": {"locale": {"timezone": None}}}
    )

    assert response.status_code == 200, response.text
    assert response.json()["settings"]["locale"] == {}


@pytest.mark.parametrize("locale", [{"timezone": "Mars/Base"}, "Asia/Kolkata"])
async def test_workspace_settings_refuse_an_unknown_timezone(
    admin_client: httpx.AsyncClient, locale: Any
) -> None:
    response = await admin_client.put("/v1/workspaces/default", json={"settings": {"locale": locale}})

    assert response.status_code == 422, response.text


async def test_the_workspace_default_seeds_an_agent_created_from_a_pack(
    admin_client: httpx.AsyncClient,
) -> None:
    await admin_client.put(
        "/v1/workspaces/default", json={"settings": {"locale": {"timezone": "Asia/Kolkata"}}}
    )

    response = await admin_client.post("/v1/agents", json={"name": "Seeded"})

    assert response.status_code == 201, response.text
    assert response.json()["config"]["timezone"] == "Asia/Kolkata"


async def test_an_explicit_config_keeps_its_own_timezone(admin_client: httpx.AsyncClient) -> None:
    await admin_client.put(
        "/v1/workspaces/default", json={"settings": {"locale": {"timezone": "Asia/Kolkata"}}}
    )

    agent = await create_agent(admin_client)

    assert agent["config"]["timezone"] == "UTC"  # type: ignore[index]


async def test_without_a_workspace_default_a_seeded_agent_stays_on_utc(
    admin_client: httpx.AsyncClient,
) -> None:
    response = await admin_client.post("/v1/agents", json={"name": "Seeded"})

    assert response.json()["config"]["timezone"] == "UTC"


# ------------------------------------------------------------------ resolved config


async def _resolved(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient, **config: Any
) -> ResolvedAgentConfig:
    agent = await create_agent(admin_client, config=json.loads(inference_config(**config).model_dump_json()))
    session_id = (await admin_client.post(f"/v1/agents/{agent['id']}/connect", json={})).json()["sessionId"]
    response = await service_client.get(f"/internal/v1/sessions/{session_id}/resolved")
    assert response.status_code == 200, response.text
    return ResolvedAgentConfig.model_validate(response.json())


async def test_resolved_business_timezone_is_the_agents_own(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient
) -> None:
    await admin_client.put(
        "/v1/workspaces/default", json={"settings": {"locale": {"timezone": "Asia/Kolkata"}}}
    )

    resolved = await _resolved(admin_client, service_client, timezone="Europe/London")

    assert resolved.business_timezone == "Europe/London"
    assert resolved.config.timezone == "Europe/London"
    assert resolved.locale.caller_timezone == "detect"


async def test_resolved_business_timezone_falls_back_to_the_workspace_default(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient
) -> None:
    await admin_client.put(
        "/v1/workspaces/default", json={"settings": {"locale": {"timezone": "Asia/Kolkata"}}}
    )

    resolved = await _resolved(admin_client, service_client, timezone="Not a zone")

    assert resolved.business_timezone == "Asia/Kolkata"
    assert resolved.config.timezone == "Asia/Kolkata"


async def test_resolved_business_timezone_falls_back_to_utc(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient
) -> None:
    resolved = await _resolved(admin_client, service_client, timezone="")

    assert resolved.business_timezone == "UTC"
    assert resolved.config.timezone == "UTC"


async def test_resolved_carries_the_locale_setting(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient
) -> None:
    from lkap_contracts.agent_config import LocaleConfig  # noqa: PLC0415

    resolved = await _resolved(admin_client, service_client, locale=LocaleConfig(caller_timezone="business"))

    assert resolved.locale.caller_timezone == "business"
    assert resolved.config.locale.caller_timezone == "business"


# ------------------------------------------------------------------ summary


async def _finish(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient, events: list[dict[str, Any]]
) -> tuple[str, dict[str, Any]]:
    agent = await create_agent(admin_client)
    session_id = (await admin_client.post(f"/v1/agents/{agent['id']}/connect", json={})).json()["sessionId"]
    if events:
        posted = await service_client.post(
            f"/internal/v1/sessions/{session_id}/events", json={"events": events}
        )
        assert posted.status_code == 202
    response = await service_client.put(
        f"/internal/v1/sessions/{session_id}/summary",
        json={"status": "ended", "usage": {"llm_prompt_tokens": 1}, "transcript": []},
    )
    assert response.status_code == 204, response.text
    detail = (await admin_client.get(f"/v1/sessions/{session_id}")).json()
    return session_id, detail


def _locale_event(zone: str, ts: float = 1758000000.0, source: str = "browser") -> dict[str, Any]:
    return {
        "ts": ts,
        "type": "locale",
        "payload": {"caller_timezone": zone, "source": source, "business_timezone": "Europe/London"},
    }


async def test_the_summary_copies_the_caller_timezone_from_the_locale_event(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient, database: Database
) -> None:
    session_id, detail = await _finish(admin_client, service_client, [_locale_event("Asia/Kolkata")])

    async with database.session() as session:
        row = (await session.execute(select(SessionRow).where(SessionRow.id == session_id))).scalar_one()
    assert row.usage == {"llm_prompt_tokens": 1, "caller_timezone": "Asia/Kolkata"}
    assert detail["caller_timezone"] == "Asia/Kolkata"


async def test_the_latest_locale_event_wins(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient
) -> None:
    events = [_locale_event("Asia/Kolkata"), _locale_event("America/New_York", ts=1758000001.0)]

    _, detail = await _finish(admin_client, service_client, events)

    assert detail["caller_timezone"] == "America/New_York"


@pytest.mark.parametrize("events", [[], [_locale_event("Not/AZone")]], ids=["no-event", "invalid-zone"])
async def test_no_caller_timezone_without_a_valid_locale_event(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient, events: list[dict[str, Any]]
) -> None:
    _, detail = await _finish(admin_client, service_client, events)

    assert detail["caller_timezone"] is None
    assert "caller_timezone" not in detail["usage"]
