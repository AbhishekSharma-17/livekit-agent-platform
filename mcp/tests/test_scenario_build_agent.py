"""The "agent builds an agent" scenario, end to end and offline (V3-05, AGENT-ACCESS §2.1, §6).

One coding-agent session, through the real ``mcp.ClientSession`` against the
in-process scratch api, doing what the live acceptance (V3-07) asks Claude Code
to do: connect a LiveKit project, store a vendor key, build an insurance intake
agent from the ``insurance_claim`` pack, give it a knowledge base and an HTTP
tool, validate it, test it in a chat and publish it. Then ``activity`` must show
every write attributed to this client and tool (R-V3-11, R-V3-22), and
``session_list(channel="text")`` must show the chat.

Only external boundaries are faked:

* LiveKit's server API, with the api tests' in-process Twirp server (``fake_livekit``);
* the vendor (``api.openai.com``) and the HTTP tool's host, with ``respx``;
* the room, with ``FakeRoomTransport`` (``chat_tools.TRANSPORT_FACTORY``);
* the worker, by registering a ready instance on the new connection through
  the api's own ``/internal/v1`` route, and by posting the tool-call event a
  worker would post.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import respx
from conftest import API_BASE, CLIENT_NAME, OPERATOR_SCOPES, all_log_text, dumped
from connection_fakes import KEY_B, SECRET_B, fake_livekit
from fastapi import FastAPI
from lkap_api.settings import Settings
from lkap_testing.fake_room import FakeRoomTransport

from lkap_mcp.chat import tools as chat_tools

VENDOR_KEY = "sk-openai-scenario-0123456789abcdefghij"
POLICY = (
    "# Home policy HO-4\n\n"
    "Flood damage to a finished basement is covered up to 10,000 USD.\n"
    "Claims must be reported within 30 days of the loss.\n"
)
WEATHER_URL = "https://tools.example.com/anything"
GREETING = "Hi, I'm the claims intake assistant. What happened?"
REPLIES = [
    "I'm sorry to hear about your basement. Flood damage is covered up to 10,000 USD. What is your city?",
    "Thanks. It rained heavily in Oslo yesterday, which matches your report. Your claim is started.",
]

#: The write tools of the run whose api calls leave an audit row (R-V3-11).
AUDITED_WRITES = {
    "connection_create",
    "provider_key_create",
    "agent_create",
    "kb_create",
    "kb_add_document",
    "tool_create_http",
    "agent_attach",
    "agent_update",
    "agent_publish",
}


class Rooms:
    """``chat_tools.TRANSPORT_FACTORY``: hands out the scripted rooms in order."""

    def __init__(self, *rooms: FakeRoomTransport) -> None:
        self.queue = list(rooms)
        self.made: list[FakeRoomTransport] = []

    def __call__(self) -> FakeRoomTransport:
        room = self.queue.pop(0)
        self.made.append(room)
        return room


@pytest.fixture
def rooms(monkeypatch: pytest.MonkeyPatch) -> Rooms:
    """One scripted room: the greeting and a reply per turn (patched before the server is built)."""
    factory = Rooms(FakeRoomTransport(greeting=GREETING, turns=[[REPLIES[0]], [REPLIES[1]]]))
    monkeypatch.setattr(chat_tools, "TRANSPORT_FACTORY", factory)
    monkeypatch.setattr(chat_tools, "SETTLE_S", 0.05)
    return factory


@pytest.fixture
async def service(app: FastAPI, api_settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    """The worker's side of the scratch api (``X-Service-Token``), to stand in for a running worker."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=API_BASE,
        headers={"X-Service-Token": api_settings.service_token},
    ) as client:
        yield client


def client_header(request: httpx.Request) -> dict[str, str]:
    """``X-LKAP-Client: lkap-mcp/<v>; client=…; tool=…; call=…`` as a dict (``product`` = the first part)."""
    product, *pairs = (part.strip() for part in request.headers.get("x-lkap-client", "").split(";"))
    fields = dict(pair.split("=", 1) for pair in pairs if "=" in pair)
    return {"product": product, "client": "", "tool": "", "call": "", **fields}


def ok(result: dict[str, Any]) -> Any:
    """Assert a tool result succeeded and return its ``data``."""
    assert result.get("ok") is True, result
    return result["data"]


@pytest.mark.timeout(60)
async def test_scenario_agent_builds_an_agent_end_to_end_offline(
    key: Any,
    mcp_session: Any,
    admin: httpx.AsyncClient,
    service: httpx.AsyncClient,
    rooms: Rooms,
    caplog: pytest.LogCaptureFixture,
) -> None:
    started_at = time.monotonic()
    raw = await key(OPERATOR_SCOPES)

    results: list[dict[str, Any]] = []

    async with fake_livekit() as (livekit, livekit_url), mcp_session(raw) as mcp:

        async def call(tool: str, /, **arguments: Any) -> dict[str, Any]:
            result: dict[str, Any] = await mcp.call(tool, **arguments)
            results.append(result)
            return result

        # 0. Orientation: the guide, then who am I.
        assert ok(await call("lkap_guide"))["markdown"].strip()
        me = ok(await call("me"))
        assert set(OPERATOR_SCOPES) <= set(me["key"]["scopes"]), me
        tools = await mcp.tool_names()
        assert {"connection_create", "chat_start", "agent_publish", "activity"} <= set(tools)
        assert "call_place" not in tools  # no calls:write, no LKAP_MCP_ALLOW_DIAL (R-V3-7)

        # 1. Connect the LiveKit project (tested first against the fake Twirp server).
        created = ok(
            await call(
                "connection_create",
                name="Cloud V3",
                url=livekit_url,
                # A Cloud project (the fake serves on loopback, which alone would read as self-hosted
                # and rule out the pack's LiveKit Inference defaults).
                deployment_type="cloud",
                api_key=KEY_B,
                api_secret=SECRET_B,
                agent_name="lkap-v3-scenario",
                test_first=True,
            )
        )
        connection = created["connection"]
        assert created["test"]["ok"] is True, created
        assert connection["fingerprint"].endswith(KEY_B[-4:])
        assert livekit.calls, "the connection test reached the fake LiveKit"

        # 2. A vendor key, tested against the (mocked) vendor.
        with respx.mock(assert_all_called=True) as vendor:
            models = vendor.get("https://api.openai.com/v1/models").respond(
                200, json={"object": "list", "data": [{"id": "gpt-4o-mini", "object": "model"}]}
            )
            stored = ok(
                await call(
                    "provider_key_create",
                    provider_id="openai-llm",
                    label="OpenAI (scenario)",
                    secrets={"api_key": VENDOR_KEY},
                    test=True,
                )
            )
        assert models.called
        assert stored["key"]["fingerprint"] and "secrets" not in stored["key"]

        # 3. The agent, from the insurance pack, bound to the new connection.
        agent = ok(
            await call(
                "agent_create",
                name="FNOL intake",
                pack_id="insurance_claim",
                connection_id=connection["id"],
            )
        )["agent"]
        assert (agent["pack_id"], agent["connection_id"]) == ("insurance_claim", connection["id"])
        assert agent["published"] is False

        # 4. A knowledge base from text (FakeEmbedder), ready before the call returns.
        kb = ok(await call("kb_create", name="Home policies"))
        document = ok(await call("kb_add_document", kb_id=kb["id"], text=POLICY, filename="ho4.md"))
        assert document["status"] == "ready" and document["chunk_count"] >= 1

        # 5. An HTTP tool, dry-run right after creating it.
        with respx.mock(assert_all_called=True) as tool_host:
            tool_host.get(WEATHER_URL).respond(200, json={"args": {"city": "Oslo"}})
            weather = ok(
                await call(
                    "tool_create_http",
                    name="lookup_weather",
                    description="Recent weather for a city, to corroborate a storm or flood claim",
                    parameters={
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                    url=WEATHER_URL + "?city={{city}}",
                    method="GET",
                    allowed_hosts=["tools.example.com"],
                    result_path="args",
                    dry_run_args={"city": "Oslo"},
                )
            )
        tool = weather["tool"]
        dry_run = weather["dry_run"]
        assert dry_run["result"]["untrusted"] is True and "Oslo" in dry_run["result"]["content"]

        # 6. Attach both, adjust the instructions, validate.
        attached = ok(
            await call("agent_attach", id_or_slug=agent["id"], kb_ids=[kb["id"]], tool_ids=[tool["id"]])
        )
        assert kb["id"] in dumped(attached) and tool["id"] in dumped(attached)
        instructions = (
            agent["config"]["instructions"]
            + "\nWhen the caller reports storm or flood damage, call lookup_weather with their city."
        )
        updated = ok(await call("agent_update", id_or_slug=agent["id"], patch={"instructions": instructions}))
        assert updated["agent"]["config"]["instructions"] == instructions
        validation = ok(await call("agent_validate", id_or_slug=agent["id"]))
        assert not [issue for issue in validation["issues"] if issue["severity"] == "error"], validation

        # 7. Test chat: a ready worker on the connection, then greeting, two turns, end.
        registered = await service.post(
            "/internal/v1/workers/register",
            json={
                "connection_id": connection["id"],
                "instance_key": "scenario-host:1",
                "image": "slim",
                "sdk_version": "1.8.2",
                "installed_provider_ids": [],
                "pack_ids": ["insurance_claim", "generic"],
                "managed_by": "external",
            },
        )
        assert registered.status_code in (200, 201), registered.text

        chat = ok(await call("chat_start", agent_id_or_slug=agent["id"], timeout_s=5))
        assert chat["greeting"]["untrusted"] is True and chat["greeting"]["content"] == GREETING
        room = rooms.made[0]
        assert room.connected_with is not None and room.connected_with[0]

        first = ok(await call("chat_send", chat_id=chat["chat_id"], text="My basement flooded"))
        assert [reply["content"] for reply in first["replies"]] == [REPLIES[0]]

        # What the worker posts when the model calls the tool during the next turn.
        posted = await service.post(
            f"/internal/v1/sessions/{chat['session_id']}/events",
            json={
                "events": [{"ts": 1.0, "type": "tool_call_started", "payload": {"name": "lookup_weather"}}]
            },
        )
        assert posted.status_code in (200, 202, 204), posted.text
        second = ok(await call("chat_send", chat_id=chat["chat_id"], text="Oslo, policy HO-44721"))
        assert [reply["content"] for reply in second["replies"]] == [REPLIES[1]]
        assert [event["type"] for event in second["events"]] == ["tool_call_started"]
        assert "lookup_weather" in second["events"][0]["payload"]["content"]
        assert [text for text, _ in room.sent] == ["My basement flooded", "Oslo, policy HO-44721"]

        ended = ok(await call("chat_end", chat_id=chat["chat_id"]))
        assert ended["session_id"] == chat["session_id"] and ended["turns"] == 2
        assert room.closed

        # 8. Publish.
        published = ok(await call("agent_publish", id_or_slug=agent["id"]))
        assert published["agent"]["published"] is True

        # 9. Look back: the activity of this key, and the chat's session.
        activity = ok(await call("activity", limit=200))
        sessions = ok(await call("session_list", channel="text", agent_id=agent["id"]))
        requests = list(mcp.transport.requests)

    # Every request the MCP made carried the attribution header with the client's name.
    attributed = [(request, client_header(request)) for request in requests]
    assert attributed
    for request, header in attributed:
        assert header["product"].startswith("lkap-mcp/"), request.url
        assert header["client"] == CLIENT_NAME, request.url

    # Every write of the run is in the activity: each non-GET request pairs with the audit row of
    # the same tool call. The one exception is the chat's text-session mint, which the api records
    # as the session row itself (checked below), not as an audit row.
    for row in activity:
        client = row["payload"]["client"]
        assert (client["product"], client["name"]) == ("lkap-mcp", CLIENT_NAME), row
        assert row["actor_type"] == "api_key"
    writes = sorted(
        (header["tool"], header["call"])
        for request, header in attributed
        if request.method != "GET" and not request.url.path.endswith("/text-sessions")
    )
    audited = sorted((row["payload"]["client"]["tool"], row["payload"]["client"]["call"]) for row in activity)
    assert writes == audited
    assert {tool for tool, _ in audited} >= AUDITED_WRITES

    # The chat is a text session of this agent.
    assert [(s["id"], s["channel"], s["agent_id"]) for s in sessions] == [
        (chat["session_id"], "text", agent["id"])
    ]

    # The stored agent is what the scenario built.
    stored_agent = (await admin.get(f"/v1/agents/{agent['id']}")).json()
    assert stored_agent["published"] is True
    assert stored_agent["config"]["instructions"] == instructions
    assert kb["id"] in dumped(stored_agent["config"]) and tool["id"] in dumped(stored_agent["config"])

    # No secret of the run is in any tool result or any log record.
    log_text = all_log_text(caplog)
    for secret in (KEY_B, SECRET_B, VENDOR_KEY):
        assert secret not in dumped(results)
        assert secret not in log_text

    assert time.monotonic() - started_at < 60
