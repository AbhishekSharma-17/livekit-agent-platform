"""V6-35: every connected app account says who it is signed in as.

Offline only: the routes talk to ``FakeComposio`` (``tests/fakes/composio.py``) and
the unit tests drive :mod:`lkap_api.tool_providers.identity` with the same fake or a
tiny stub. No real Composio key, no network, example.com addresses only.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fakes.composio import VALID_KEY, ComposioWorld
from fastapi import FastAPI

from lkap_api.db.models import Credential
from lkap_api.db.session import Database
from lkap_api.settings import Settings
from lkap_api.tool_providers import identity
from lkap_api.tool_providers.adapter import ToolProviderUnavailableError
from lkap_api.tool_providers.composio import ComposioAdapter
from lkap_api.tool_providers.router import get_adapter_factory
from lkap_api.vault import Vault

BASE = "/v1/tool-providers/composio"
CONSOLE_OK = "http://localhost:3000/console/tools?tab=apps&connect=ok"
CALENDAR_WHO = "GOOGLECALENDAR_GET_CURRENT_USER"


@pytest.fixture
def world(app: FastAPI) -> ComposioWorld:
    fake = ComposioWorld(valid_keys={VALID_KEY})
    app.dependency_overrides[get_adapter_factory] = lambda: fake.factory
    return fake


@pytest.fixture
async def key_id(admin_client: httpx.AsyncClient, world: ComposioWorld) -> str:
    response = await admin_client.post(
        "/v1/credentials",
        json={"provider_id": "composio", "label": "Composio", "secrets": {"api_key": VALID_KEY}},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _flow_query(world: ComposioWorld) -> dict[str, str]:
    callback_url = world.calls_of("start_link")[-1].kwargs["callback_url"]
    return {key: values[0] for key, values in parse_qs(urlsplit(callback_url).query).items()}


async def _finish_sign_in(
    client: httpx.AsyncClient, world: ComposioWorld, *, display_name: str | None = None
) -> str:
    """Complete the last sign-in LKAP started; returns the connected account id."""
    account = list(world.accounts)[-1]
    world.complete(account, display_name=display_name)
    response = await client.get(
        f"{BASE}/callback",
        params={**_flow_query(world), "status": "success", "connected_account_id": account},
        follow_redirects=False,
    )
    assert response.headers["location"] == CONSOLE_OK
    return account


async def _calendar_account(
    admin_client: httpx.AsyncClient,
    client: httpx.AsyncClient,
    world: ComposioWorld,
    *,
    label: str | None = None,
    display_name: str | None = None,
) -> tuple[str, str]:
    """Connect one more Google Calendar account; returns (connection id, connected account id)."""
    response = await admin_client.post(
        f"{BASE}/connections", json={"toolkit": "googlecalendar", **({"label": label} if label else {})}
    )
    assert response.status_code == 201, response.text
    account = await _finish_sign_in(client, world, display_name=display_name)
    return str(response.json()["connection_id"]), account


async def _items(admin_client: httpx.AsyncClient) -> dict[str, dict[str, Any]]:
    body = (await admin_client.get(f"{BASE}/connections")).json()
    return {item["id"]: item for item in body["items"]}


async def _bag(database: Database, settings: Settings, connection_id: str) -> dict[str, str]:
    async with database.session() as session:
        row = await session.get(Credential, connection_id)
        assert row is not None
        return Vault(settings.master_key).decrypt(row.ciphertext)


async def _age_identity_check(
    database: Database, settings: Settings, connection_id: str, *, by: dt.timedelta
) -> None:
    vault = Vault(settings.master_key)
    async with database.session() as session:
        row = await session.get(Credential, connection_id)
        assert row is not None
        bag = vault.decrypt(row.ciphertext)
        checked = dt.datetime.fromisoformat(bag["identity_checked_at"])
        bag["identity_checked_at"] = (checked - by).isoformat()
        row.ciphertext = vault.encrypt(bag)


def _who(account_id: str) -> dict[str, Any]:
    """The calendar's "who am I" answer, different per account (so a wrong pin shows)."""
    return {"data": {"email": f"{account_id}@example.com"}, "error": None, "successful": True}


# ============================================================================ through the routes
async def test_connect_display_name_is_the_identity_and_label_without_an_action_call(
    admin_client: httpx.AsyncClient, client: httpx.AsyncClient, world: ComposioWorld, key_id: str
) -> None:
    connection_id, _ = await _calendar_account(admin_client, client, world, display_name="pat@example.com")

    item = (await _items(admin_client))[connection_id]
    assert (item["identity"], item["identity_kind"]) == ("pat@example.com", "email")
    assert item["identity_checked_at"] is not None
    assert item["label"] == "pat@example.com", "an unnamed new account is named after who it is"
    assert world.calls_of("execute") == [], "Composio's display name needs no metered call"


async def test_connect_asks_the_app_on_the_signed_in_account_and_each_account_gets_its_own_identity(
    admin_client: httpx.AsyncClient, client: httpx.AsyncClient, world: ComposioWorld, key_id: str
) -> None:
    world.action_results[CALENDAR_WHO] = _who
    first, first_account = await _calendar_account(admin_client, client, world)
    second, second_account = await _calendar_account(admin_client, client, world, label="Personal")

    items = await _items(admin_client)
    assert items[first]["identity"] == f"{first_account}@example.com"
    assert items[second]["identity"] == f"{second_account}@example.com"
    assert items[first]["label"] == f"{first_account}@example.com"
    assert items[second]["label"] == "Personal", "a name the user gave is kept"
    pins = [call.kwargs["connected_account_id"] for call in world.calls_of("execute")]
    assert pins == [first_account, second_account], "every ask names its own account"
    assert world.accounts[first_account]["alias"] == f"{first_account}_example_com"


async def test_a_failed_identity_ask_leaves_it_empty_and_the_connect_still_succeeds(
    admin_client: httpx.AsyncClient,
    client: httpx.AsyncClient,
    world: ComposioWorld,
    key_id: str,
    database: Database,
    settings: Settings,
) -> None:
    world.failures["execute"] = ToolProviderUnavailableError("Composio did not answer in time")
    connection_id, _ = await _calendar_account(admin_client, client, world)

    item = (await _items(admin_client))[connection_id]
    assert item["status"] == "active"
    assert item["identity"] is None and item["identity_kind"] is None
    assert item["identity_checked_at"] is not None
    assert item["label"] == "Google Calendar"
    bag = await _bag(database, settings, connection_id)
    assert bag["identity"] == "" and set(bag) >= {"identity", "identity_kind", "identity_checked_at"}


async def test_a_check_does_not_ask_again_while_fresh_but_check_now_does(
    admin_client: httpx.AsyncClient, client: httpx.AsyncClient, world: ComposioWorld, key_id: str
) -> None:
    world.action_results[CALENDAR_WHO] = _who
    connection_id, account = await _calendar_account(admin_client, client, world)
    asks = len(world.calls_of("execute"))

    checked = await admin_client.get(f"{BASE}/connections/{connection_id}")
    assert checked.status_code == 200
    assert checked.json()["identity"] == f"{account}@example.com"
    assert len(world.calls_of("execute")) == asks, "a fresh identity is not asked for again"

    forced = await admin_client.get(f"{BASE}/connections/{connection_id}", params={"identify": "true"})
    assert forced.status_code == 200
    assert len(world.calls_of("execute")) == asks + 1
    assert world.calls_of("execute")[-1].kwargs["connected_account_id"] == account


async def test_a_check_backfills_an_unidentified_account_without_renaming_it(
    admin_client: httpx.AsyncClient,
    client: httpx.AsyncClient,
    world: ComposioWorld,
    key_id: str,
    database: Database,
    settings: Settings,
) -> None:
    world.failures["execute"] = ToolProviderUnavailableError("Composio did not answer in time")
    connection_id, account = await _calendar_account(admin_client, client, world)
    world.action_results[CALENDAR_WHO] = _who

    soon = await admin_client.get(f"{BASE}/connections/{connection_id}")
    assert soon.json()["identity"] is None, "asked a moment ago: not again yet"
    await _age_identity_check(database, settings, connection_id, by=identity.IDENTITY_RETRY)
    later = (await admin_client.get(f"{BASE}/connections/{connection_id}")).json()

    assert later["identity"] == f"{account}@example.com"
    assert later["label"] == "Google Calendar", "an existing account keeps its label"


async def test_a_reconnect_with_another_account_updates_the_identity_but_keeps_the_label(
    admin_client: httpx.AsyncClient, client: httpx.AsyncClient, world: ComposioWorld, key_id: str
) -> None:
    connection_id, _ = await _calendar_account(admin_client, client, world, display_name="pat@example.com")

    reconnect = await admin_client.post(f"{BASE}/connections/{connection_id}/reconnect")
    assert reconnect.status_code == 200, reconnect.text
    await _finish_sign_in(client, world, display_name="lee@example.com")

    item = (await _items(admin_client))[connection_id]
    assert item["identity"] == "lee@example.com"
    assert item["label"] == "pat@example.com", "no silent rename on reconnect"


async def test_a_broken_account_keeps_its_identity_so_the_reconnect_row_says_who(
    admin_client: httpx.AsyncClient, client: httpx.AsyncClient, world: ComposioWorld, key_id: str
) -> None:
    connection_id, account = await _calendar_account(
        admin_client, client, world, display_name="pat@example.com"
    )
    world.accounts[account]["status"] = "EXPIRED"

    checked = (await admin_client.get(f"{BASE}/connections/{connection_id}")).json()

    assert checked["needs_reconnect"] is True
    assert checked["identity"] == "pat@example.com"


async def test_an_identity_longer_than_a_label_is_shown_but_not_used_as_the_label(
    admin_client: httpx.AsyncClient, client: httpx.AsyncClient, world: ComposioWorld, key_id: str
) -> None:
    long_address = "a.very.long.mailbox.name.for.testing@example.com"
    assert len(long_address) > 40
    connection_id, _ = await _calendar_account(admin_client, client, world, display_name=long_address)

    item = (await _items(admin_client))[connection_id]
    assert item["identity"] == long_address
    assert item["label"] == "Google Calendar", "an address is never truncated into a label"


async def test_a_keyless_app_is_never_asked_who_it_is(
    admin_client: httpx.AsyncClient, world: ComposioWorld, key_id: str
) -> None:
    response = await admin_client.post(
        f"{BASE}/connections", json={"toolkit": "publicholidays", "method": "none"}
    )
    assert response.status_code == 201, response.text

    item = (await _items(admin_client))[response.json()["connection_id"]]
    assert item["identity"] is None and item["identity_checked_at"] is None
    assert world.calls_of("execute") == [] and world.calls_of("proxy") == []


# ============================================================================ the identity module
async def _identify(world: ComposioWorld, toolkit: str, **kwargs: Any) -> identity.Identity | None:
    return await identity.identify(
        world.factory(VALID_KEY), toolkit=toolkit, subject="ws:test", connected_account_id="ca_1", **kwargs
    )


@pytest.mark.parametrize(
    ("toolkit", "slug", "data", "expected"),
    [
        (
            "gmail",
            "GMAIL_GET_PROFILE",
            {"emailAddress": "sam@example.com", "messagesTotal": 3},
            ("sam@example.com", "email"),
        ),
        (
            "github",
            "GITHUB_GET_THE_AUTHENTICATED_USER",
            {"login": "octo-sam", "id": 42},
            ("@octo-sam", "username"),
        ),
        (
            "slack",
            "SLACK_TEST_AUTH",
            {"ok": True, "user": "sam", "team": "Acme"},
            ("@sam · Acme", "username"),
        ),
        ("slack", "SLACK_TEST_AUTH", {"ok": True, "team": "Acme"}, ("Acme", "workspace")),
        (
            "googledrive",
            "GOOGLEDRIVE_GET_ABOUT",
            {"user": {"emailAddress": "sam@example.com"}},
            ("sam@example.com", "email"),
        ),
        (
            "notion",
            "NOTION_GET_ABOUT_ME",
            {"bot": {"owner": {"type": "workspace"}, "workspace_name": "Acme"}},
            ("Acme", "workspace"),
        ),
        (
            "linear",
            "LINEAR_GET_CURRENT_USER",
            {"viewer": {"email": "sam@example.com", "name": "Sam"}},
            ("sam@example.com", "email"),
        ),
        (
            "jira",
            "JIRA_GET_CURRENT_USER",
            {"accountId": "5b10", "displayName": "Sam Lee"},
            ("Sam Lee", "other"),
        ),
        (
            "googlecalendar",
            CALENDAR_WHO,
            {"id": "sam@example.com", "timezone": "UTC"},
            ("sam@example.com", "email"),
        ),
        ("gmail", "GMAIL_GET_PROFILE", '{"emailAddress": "sam@example.com"}', ("sam@example.com", "email")),
        (
            "gmail",
            "GMAIL_GET_PROFILE",
            {"response_data": {"emailAddress": "sam@example.com"}},
            ("sam@example.com", "email"),
        ),
    ],
)
async def test_identify_reads_each_apps_own_answer(
    toolkit: str, slug: str, data: Any, expected: tuple[str, str]
) -> None:
    world = ComposioWorld()
    world.action_results[slug] = {"data": data, "error": None, "successful": True}

    found = await _identify(world, toolkit)

    assert found is not None and (found.value, found.kind) == expected
    (call,) = world.calls_of("execute")
    assert call.kwargs["tool_slug"] == slug and call.kwargs["connected_account_id"] == "ca_1"


@pytest.mark.parametrize(
    ("toolkit", "slug", "data"),
    [
        ("gmail", "GMAIL_GET_PROFILE", {"emailAddress": "not-an-address"}),
        ("gmail", "GMAIL_GET_PROFILE", {"something_else": "sam@example.com"}),
        ("github", "GITHUB_GET_THE_AUTHENTICATED_USER", {"login": "12345"}),
        ("github", "GITHUB_GET_THE_AUTHENTICATED_USER", {"login": "two words"}),
        ("jira", "JIRA_GET_CURRENT_USER", {"displayName": "see https://example.com/x"}),
        ("googlecalendar", CALENDAR_WHO, {"id": "c_1234567890"}),
        ("gmail", "GMAIL_GET_PROFILE", "not json"),
    ],
)
async def test_identify_never_guesses_from_an_answer_of_another_shape(
    toolkit: str, slug: str, data: Any
) -> None:
    world = ComposioWorld()
    world.action_results[slug] = {"data": data, "error": None, "successful": True}

    assert await _identify(world, toolkit) is None


async def test_identify_takes_nothing_from_an_unsuccessful_action() -> None:
    world = ComposioWorld()
    world.action_results["GMAIL_GET_PROFILE"] = {
        "data": {"emailAddress": "sam@example.com"},
        "error": "insufficient scope",
        "successful": False,
    }

    assert await _identify(world, "gmail") is None


async def test_identify_outlook_goes_through_the_proxy_to_graph_me() -> None:
    world = ComposioWorld()
    world.proxy_results["https://graph.microsoft.com/v1.0/me"] = {
        "data": {"mail": None, "userPrincipalName": "sam@example.com"},
        "status": 200,
        "headers": {},
    }

    found = await _identify(world, "outlook")

    assert found == identity.Identity("sam@example.com", "email")
    (call,) = world.calls_of("proxy")
    assert call.kwargs == {
        "endpoint": "https://graph.microsoft.com/v1.0/me",
        "http_method": "GET",
        "connected_account_id": "ca_1",
    }


async def test_identify_ignores_a_proxy_answer_that_is_not_a_success() -> None:
    world = ComposioWorld()
    world.proxy_results["https://graph.microsoft.com/v1.0/me"] = {
        "data": {"mail": "sam@example.com"},
        "status": 401,
        "headers": {},
    }

    assert await _identify(world, "outlook") is None


async def test_identify_prefers_composios_display_name_and_makes_no_call() -> None:
    world = ComposioWorld()
    account = {"state": {"val": {"status": "ACTIVE", "displayName": "sam@example.com"}}}

    found = await _identify(world, "gmail", account=account)

    assert found == identity.Identity("sam@example.com", "email")
    assert world.calls == []


async def test_identify_an_app_without_a_known_question_makes_no_call() -> None:
    world = ComposioWorld()

    assert await _identify(world, "hubspot") is None
    assert world.calls == []


async def test_identify_a_vendor_error_is_none_not_an_exception() -> None:
    world = ComposioWorld()
    world.failures["execute"] = ToolProviderUnavailableError("Composio answered HTTP 500")

    assert await _identify(world, "gmail") is None


class _SlowAdapter:
    """Answers far later than the identity bound allows."""

    async def execute(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        await asyncio.sleep(5)
        return {"data": {"emailAddress": "sam@example.com"}, "successful": True}


async def test_identify_is_bounded_by_a_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(identity, "IDENTITY_TIMEOUT_S", 0.01)

    found = await identity.identify(
        _SlowAdapter(),  # type: ignore[arg-type]
        toolkit="gmail",
        subject="ws:test",
        connected_account_id="ca_1",
    )

    assert found is None


def test_due_retries_an_unknown_identity_sooner_than_it_refreshes_a_known_one() -> None:
    now = dt.datetime(2026, 10, 1, 12, 0, tzinfo=dt.UTC)
    recent = now - dt.timedelta(minutes=1)

    assert identity.due(identity=None, checked_at=None, now=now)
    assert not identity.due(identity=None, checked_at=recent, now=now)
    assert identity.due(identity=None, checked_at=now - identity.IDENTITY_RETRY, now=now)
    assert not identity.due(identity="sam@example.com", checked_at=now - identity.IDENTITY_RETRY, now=now)
    assert identity.due(identity="sam@example.com", checked_at=now - identity.IDENTITY_TTL, now=now)
    assert identity.due(identity="sam@example.com", checked_at=recent, now=now, force=True)


async def test_the_composio_adapter_proxy_posts_the_documented_body() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": {"mail": "sam@example.com"}, "status": 200, "headers": {}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        answer = await ComposioAdapter(http, VALID_KEY).proxy(
            endpoint="https://graph.microsoft.com/v1.0/me", method="GET", connected_account_id="ca_1"
        )

    (request,) = seen
    assert (request.method, request.url.path) == ("POST", "/api/v3.1/tools/execute/proxy")
    assert json.loads(request.content) == {
        "endpoint": "https://graph.microsoft.com/v1.0/me",
        "method": "GET",
        "connected_account_id": "ca_1",
    }
    assert request.headers["x-api-key"] == VALID_KEY
    assert answer["data"] == {"mail": "sam@example.com"}
