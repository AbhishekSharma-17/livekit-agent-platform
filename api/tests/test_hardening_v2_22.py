"""V2-22 final hardening: the acceptance lines of rulings R-V2-26 … R-V2-36 (PLAN-V2 §8).

One section per ruling, in the card's order of risk:

* R-V2-26 / R2-38: the guarded aiohttp session never follows a redirect and
  its connector checks IP literals itself.
* R-V2-26 / R2-39: numeric-looking hosts are refused at save time.
* R-V2-34 / R2-15: the concurrency caps hold under a burst.
* R-V2-30 / R2-19: ``add_member`` only re-adds a former member.
* R-V2-36 / R2-20: one ``recording.ready`` per session; R2-26; R2-34; #75; #76.
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator
from typing import Any

import aiohttp
import httpx
import pytest
import test_telephony as tel
from aiohttp import web
from auth_helpers import WEB_ORIGIN, client_for, login, make_user, make_workspace, set_agent_columns
from conftest import create_agent, inference_config, postgres_url
from connection_fakes import add_agent, connection_row
from fastapi import FastAPI
from livekit.protocol.egress import EgressInfo, EgressStatus, FileInfo
from livekit.protocol.webhook import WebhookEvent
from lkap_contracts.agent_config import AgentLimits
from sqlalchemy import func, select
from test_telephony import CALLEE, TEST_POLICY, World, _set_policy, _trunk

from lkap_api import limits as limits_module
from lkap_api import net_guard
from lkap_api.auth.ratelimit import AgentBusyError
from lkap_api.connections import bundle
from lkap_api.connections.clients import ConnectionClientFactory
from lkap_api.connections.probe import probe_connection
from lkap_api.connections.service import UnsavedConnection
from lkap_api.connections.webhooks import WebhookContext, dispatch_webhook
from lkap_api.db.constants import DEFAULT_WORKSPACE_ID
from lkap_api.db.models import Agent, Call, Job, LiveKitConnection, User, WebhookDelivery, new_id
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.session import Database
from lkap_api.errors import UnprocessableEntityError
from lkap_api.jobs.context import JobContext
from lkap_api.jobs.kinds import RECORDING_FINALIZE
from lkap_api.kb.ingest import upload_storage_key
from lkap_api.limits import LIVE_STATUSES, reserve_session_slot
from lkap_api.recordings.job import handle_recording_finalize
from lkap_api.routers.knowledge import upload_basename
from lkap_api.settings import Settings
from lkap_api.storage.endpoint import validate_endpoint_url
from lkap_api.telephony.calls import CALL_EVENT_JOB, STUCK_DIAL_AFTER_S, _emit_call_event, advance
from lkap_api.vault import Vault
from lkap_api.webhooks.events import CALL_ENDED, CALL_STARTED, KNOWN_EVENTS, RECORDING_READY

# The telephony world's fixtures, shared with ``test_telephony`` (pytest finds them by name).
fake_lk = tel.fake_lk
fake_factory = tel.fake_factory
dial_policy = tel.dial_policy
world = tel.world

PROD = net_guard.NetPolicy.from_entries(())
ALICE_PASSWORD = "alice's own correct horse battery"


class _FakeResolver(aiohttp.abc.AbstractResolver):
    """Answers from a table instead of DNS; counts the lookups."""

    def __init__(self, answers: dict[str, list[str]]) -> None:
        self._answers = answers
        self.lookups: list[str] = []

    async def resolve(
        self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_INET
    ) -> list[aiohttp.abc.ResolveResult]:
        self.lookups.append(host)
        return [
            {"hostname": host, "host": a, "port": port, "family": family, "proto": 0, "flags": 0}
            for a in self._answers[host]
        ]

    async def close(self) -> None:
        return None


# ===================================================== R2-38: redirect to a literal
class _Servers:
    """A redirecting stand-in for an admin-controlled LiveKit url, and the inward target."""

    def __init__(self) -> None:
        self.target_hits: list[str] = []
        self.redirect_hits: list[str] = []
        self.redirect_port = 0
        self.target_port = 0


async def _start(app: web.Application) -> tuple[web.AppRunner, int]:
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    server = site._server
    assert server is not None
    port = int(server.sockets[0].getsockname()[1])  # type: ignore[union-attr]
    return runner, port


@pytest.fixture
async def servers() -> AsyncIterator[_Servers]:
    state = _Servers()

    async def target(request: web.Request) -> web.Response:
        state.target_hits.append(request.path)
        return web.Response(text="ami-id\ninstance-id\niam/")

    async def redirect(request: web.Request) -> web.Response:
        state.redirect_hits.append(request.path)
        raise web.HTTPTemporaryRedirect(f"http://127.0.0.1:{state.target_port}/latest/meta-data/")

    target_app = web.Application()
    target_app.router.add_route("*", "/{tail:.*}", target)
    redirect_app = web.Application()
    redirect_app.router.add_route("*", "/{tail:.*}", redirect)
    target_runner, state.target_port = await _start(target_app)
    redirect_runner, state.redirect_port = await _start(redirect_app)
    try:
        yield state
    finally:
        await redirect_runner.cleanup()
        await target_runner.cleanup()


#: The redirecting host is allowlisted *by name* so the fake can stand on loopback;
#: the literal in its `Location` is not covered by that exemption.
_REDIRECTOR_POLICY = net_guard.NetPolicy.from_entries(["lk.example"])


async def test_guarded_session_never_follows_a_redirect_to_a_loopback_literal(servers: _Servers) -> None:
    session = net_guard.guarded_aiohttp_session(
        _REDIRECTOR_POLICY,
        timeout=aiohttp.ClientTimeout(total=5),
        inner_resolver=_FakeResolver({"lk.example": ["127.0.0.1"]}),
    )
    async with session:
        with pytest.raises(net_guard.BlockedDestinationError, match="blocked destination.*redirect"):
            # `allow_redirects=True` is what LiveKit's Twirp client passes (the default).
            await session.post(
                f"http://lk.example:{servers.redirect_port}/twirp/livekit.RoomService/ListRooms",
                data=b"{}",
                allow_redirects=True,
            )

    assert servers.redirect_hits == ["/twirp/livekit.RoomService/ListRooms"]
    assert servers.target_hits == []


async def test_guarded_connector_refuses_a_redirect_it_is_asked_to_follow(servers: _Servers) -> None:
    """Belt and braces: even a plain session that follows redirects cannot reach the literal."""
    resolver = net_guard.GuardedResolver(
        _REDIRECTOR_POLICY, inner=_FakeResolver({"lk.example": ["127.0.0.1"]})
    )
    connector = net_guard.GuardedTCPConnector(_REDIRECTOR_POLICY, resolver=resolver)
    async with aiohttp.ClientSession(connector=connector) as session:
        with pytest.raises(aiohttp.ClientConnectorError) as raised:
            await session.post(f"http://lk.example:{servers.redirect_port}/x", data=b"{}")

    blocked = net_guard.blocked_cause(raised.value)
    assert blocked is not None and "loopback" in str(blocked)
    assert servers.target_hits == []


async def test_probe_through_a_redirecting_url_says_blocked_and_never_hits_the_target(
    servers: _Servers, settings: Settings
) -> None:
    """The R2-38 runtime path: factory → guarded session → Twirp POST → 307 → refused."""
    vault = Vault(settings.master_key)
    row = UnsavedConnection(
        id="c-redirect",
        url=f"http://lk.example:{servers.redirect_port}",
        agent_name="x",
        credentials_version=1,
        api_key_ct=vault.encrypt({"api_key": "k"}),
        api_secret_ct=vault.encrypt({"api_secret": "s" * 32}),
    )
    factory = ConnectionClientFactory(
        vault,
        net_policy=_REDIRECTOR_POLICY,
        net_resolver=lambda: _FakeResolver({"lk.example": ["127.0.0.1"]}),
    )

    result = await probe_connection(factory, row, deployment_type="self_hosted", use_inference=False)

    assert result.ok is False
    assert "blocked destination" in result.message
    assert servers.redirect_hits  # the admin's server was asked…
    assert servers.target_hits == []  # …and the inward address never was


@pytest.mark.parametrize("literal", ["127.0.0.1", "::ffff:169.254.169.254", "169.254.169.254", "10.0.0.7"])
async def test_guarded_connector_refuses_inward_literals_under_the_prod_policy(literal: str) -> None:
    connector = net_guard.GuardedTCPConnector(PROD, resolver=net_guard.GuardedResolver(PROD))
    try:
        with pytest.raises(net_guard.BlockedDestinationError, match="blocked destination"):
            await connector._resolve_host(literal, 80)
    finally:
        await connector.close()


async def test_guarded_connector_allows_a_literal_the_policy_exempts_but_never_metadata() -> None:
    policy = net_guard.NetPolicy.from_entries(["127.0.0.1", "169.254.0.0/16"])
    connector = net_guard.GuardedTCPConnector(policy, resolver=net_guard.GuardedResolver(policy))
    try:
        answers = await connector._resolve_host("127.0.0.1", 80)
        assert [a["host"] for a in answers] == ["127.0.0.1"]
        # R-V2-26: metadata addresses are refused even when an allowlisted network covers them.
        with pytest.raises(net_guard.BlockedDestinationError, match="metadata"):
            await connector._resolve_host("::ffff:169.254.169.254", 80)
    finally:
        await connector.close()


async def test_the_livekit_session_is_the_guarded_one() -> None:
    """Tripwire: `guarded_aiohttp_session` wires both halves of the R2-38 fix."""
    async with net_guard.guarded_aiohttp_session(PROD, timeout=aiohttp.ClientTimeout(total=1)) as session:
        assert isinstance(session, net_guard.NoRedirectClientSession)
        assert isinstance(session.connector, net_guard.GuardedTCPConnector)


# ====================================================== R2-39: numeric-looking hosts
NUMERIC_HOSTS = ["2130706433", "0x7f000001", "127.1", "0"]


@pytest.mark.parametrize("host", NUMERIC_HOSTS)
def test_numeric_hosts_are_refused_offline(host: str) -> None:
    dev = net_guard.NetPolicy.from_entries(net_guard.DEV_DEFAULT_ALLOW)
    for policy in (PROD, dev):
        problem = net_guard.check_url(f"http://{host}/", policy)
        assert problem is not None and "non-canonical" in problem


@pytest.mark.parametrize("host", ["0xabc.example.com", "api.example.com", "93.184.216.34", "10.example.com"])
def test_real_names_and_canonical_public_literals_are_not_numeric(host: str) -> None:
    assert net_guard.check_url(f"https://{host}/", PROD) is None


@pytest.mark.parametrize("host", NUMERIC_HOSTS)
async def test_numeric_webhook_url_is_422_blocked_destination_at_save(
    admin_client: httpx.AsyncClient, host: str
) -> None:
    created = await admin_client.post("/v1/webhooks", json={"url": f"http://{host}/hook", "events": []})

    assert created.status_code == 422, created.text
    assert created.json()["error"]["details"]["reason"] == "blocked_destination"


@pytest.mark.parametrize("host", NUMERIC_HOSTS)
async def test_numeric_connection_url_is_422_blocked_destination_at_save(
    admin_client: httpx.AsyncClient, host: str
) -> None:
    body: dict[str, Any] = {
        "slug": "numeric",
        "name": "numeric",
        "deployment_type": "self_hosted",
        "url": f"ws://{host}:7880",
        "api_key": "k",
        "api_secret": "s" * 32,
        "agent_name": "lkap-numeric",
    }

    created = await admin_client.post("/v1/connections", json=body)
    probed = await admin_client.post("/v1/connections/test", json=body)

    for response in (created, probed):
        assert response.status_code == 422, response.text
        assert response.json()["error"]["details"]["reason"] == "blocked_destination"


# ================================================= R-V2-34 / R2-15: cap reservation
async def _burst(client: httpx.AsyncClient, path: str, body: dict[str, Any], n: int) -> list[httpx.Response]:
    return list(await asyncio.gather(*(client.post(path, json=body) for _ in range(n))))


async def _live_sessions(database: Database, agent_id: str) -> int:
    async with database.session() as session:
        total = await session.scalar(
            select(func.count())
            .select_from(SessionRow)
            .where(SessionRow.agent_id == agent_id, SessionRow.status.in_(LIVE_STATUSES))
        )
    return int(total or 0)


@pytest.mark.parametrize("route", ["connect", "text-sessions"])
async def test_a_burst_of_50_starts_at_a_cap_of_5_admits_exactly_5(
    admin_client: httpx.AsyncClient, database: Database, route: str
) -> None:
    """The V2-21 load test (`race.py`) as an api test: before R-V2-34 it admitted 14."""
    agent = await create_agent(admin_client)
    await set_agent_columns(
        database, str(agent["id"]), limits={"max_concurrent_sessions": 5, "rate_per_agent_per_min": 1000}
    )

    responses = await _burst(admin_client, f"/v1/agents/{agent['id']}/{route}", {}, 50)

    statuses = sorted(r.status_code for r in responses)
    assert statuses.count(200) == 5, statuses
    assert statuses.count(429) == 45
    assert {r.json()["error"]["code"] for r in responses if r.status_code == 429} == {"agent_busy"}
    assert await _live_sessions(database, str(agent["id"])) == 5


async def test_a_burst_of_20_dials_at_an_outbound_cap_of_3_admits_exactly_3(
    admin_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    await _trunk(admin_client, world, "outbound")
    await _set_policy(database, {**TEST_POLICY, "max_concurrent_outbound": 3, "max_calls_per_min": 1000})
    await set_agent_columns(database, world.agent_id, limits={"max_concurrent_sessions": 50})

    responses = await _burst(admin_client, "/v1/calls", {"agent_id": world.agent_id, "to_e164": CALLEE}, 20)

    statuses = sorted(r.status_code for r in responses)
    assert statuses.count(201) == 3, statuses
    assert statuses.count(429) == 17
    assert {r.json()["error"]["code"] for r in responses if r.status_code == 429} == {"calls_busy"}
    async with database.session() as session:
        calls = await session.scalar(
            select(func.count()).select_from(Call).where(Call.direction == "outbound")
        )
    assert calls == 3


async def test_a_burst_of_dials_at_the_agent_session_cap_admits_exactly_the_cap(
    admin_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    await _trunk(admin_client, world, "outbound")
    await _set_policy(database, {**TEST_POLICY, "max_concurrent_outbound": 100, "max_calls_per_min": 1000})
    await set_agent_columns(database, world.agent_id, limits={"max_concurrent_sessions": 2})

    responses = await _burst(admin_client, "/v1/calls", {"agent_id": world.agent_id, "to_e164": CALLEE}, 12)

    assert sorted(r.status_code for r in responses).count(201) == 2
    assert await _live_sessions(database, world.agent_id) == 2


async def _start_one(database: Database, agent_id: str, hold_s: float) -> str:
    """Reserve a slot on a session of its own and insert a live session row."""
    async with database.session() as db:
        agent = await db.scalar(select(Agent).where(Agent.id == agent_id))
        assert agent is not None
        limits = AgentLimits.model_validate(agent.limits or {})
        try:
            async with reserve_session_slot(db, agent, limits):
                session_id = new_id()
                db.add(
                    SessionRow(
                        id=session_id,
                        workspace_id=agent.workspace_id,
                        agent_id=agent.id,
                        config_version=agent.config_version,
                        room_name=f"room-{session_id}",
                        participant_identity="p",
                        participant_name="p",
                        status="created",
                        pipeline_mode="cascaded",
                    )
                )
                await db.flush()
                await asyncio.sleep(hold_s)  # the window the race used to fall through
                await db.commit()
        except AgentBusyError:
            return "busy"
    return "admitted"


async def test_two_sessions_at_the_cap_admit_one_even_across_processes(
    admin_client: httpx.AsyncClient, database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-V2-34's Postgres case (run by CI's `test-postgres` job with `LKAP_TEST_DATABASE_URL`).

    On Postgres the per-process lock is replaced by a fresh lock per call, which
    is what two api processes look like, so only the agent row's
    ``FOR UPDATE`` stands between them. SQLite has no row locks (and runs one
    api process, RUNBOOK), so there the real process lock is kept.
    """
    if postgres_url() is not None:
        monkeypatch.setattr(limits_module, "_process_lock", lambda _scope, _key: asyncio.Lock())
    agent = await create_agent(admin_client)
    await set_agent_columns(database, str(agent["id"]), limits={"max_concurrent_sessions": 1})

    outcomes = await asyncio.gather(
        _start_one(database, str(agent["id"]), 0.2), _start_one(database, str(agent["id"]), 0.2)
    )

    assert sorted(outcomes) == ["admitted", "busy"]
    assert await _live_sessions(database, str(agent["id"])) == 1


# ================================================== R-V2-30 / R2-19: add_member re-adds
async def _members(client: httpx.AsyncClient, workspace_id: str) -> set[str]:
    listed = await client.get(f"/v1/workspaces/{workspace_id}/members")
    assert listed.status_code == 200, listed.text
    return {m["email"] for m in listed.json()["items"]}


async def test_add_member_only_re_adds_and_another_workspace_must_invite(
    app: FastAPI, database: Database, admin_client: httpx.AsyncClient
) -> None:
    beta_id = await make_workspace(database, "beta")
    await make_user(database, "bob@b.example", role="admin", workspace_id=beta_id)
    # The admin of A pre-creates alice@b.example: invite, then accept with a password A chose.
    invite_a = await admin_client.post(
        f"/v1/workspaces/{DEFAULT_WORKSPACE_ID}/invites", json={"email": "alice@b.example", "role": "viewer"}
    )
    assert invite_a.status_code == 201, invite_a.text
    async with client_for(app) as anon:
        joined_a = await anon.post(
            "/v1/auth/accept-invite", json={"token": invite_a.json()["token"], "password": ALICE_PASSWORD}
        )
    assert joined_a.status_code == 204, joined_a.text

    async with await login(app, "bob@b.example") as bob:
        bob.headers["Origin"] = WEB_ORIGIN
        added = await bob.post(
            f"/v1/workspaces/{beta_id}/members", json={"email": "alice@b.example", "role": "viewer"}
        )
        invite_b = await bob.post(
            f"/v1/workspaces/{beta_id}/invites", json={"email": "alice@b.example", "role": "viewer"}
        )
        async with client_for(app) as anon:
            joined_b = await anon.post(
                "/v1/auth/accept-invite", json={"token": invite_b.json()["token"], "password": ALICE_PASSWORD}
            )
        beta_members = await _members(bob, beta_id)

    assert added.status_code == 409
    assert added.json()["error"]["details"]["reason"] == "use_invite"
    assert invite_b.status_code == 201, invite_b.text
    assert joined_b.status_code == 204, joined_b.text
    assert "alice@b.example" in beta_members

    # A removes alice, then re-adds her: a former member of A comes back in one step.
    alice_id = await _user_id(database, "alice@b.example")
    removed = await admin_client.delete(f"/v1/workspaces/{DEFAULT_WORKSPACE_ID}/members/{alice_id}")
    re_added = await admin_client.post(
        f"/v1/workspaces/{DEFAULT_WORKSPACE_ID}/members", json={"email": "alice@b.example", "role": "viewer"}
    )
    assert removed.status_code == 204, removed.text
    assert re_added.status_code == 201, re_added.text


async def test_add_member_of_an_account_with_no_history_here_is_409_use_invite(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    beta_id = await make_workspace(database, "beta")
    await make_user(database, "carol@b.example", role="builder", workspace_id=beta_id)

    added = await admin_client.post(
        f"/v1/workspaces/{DEFAULT_WORKSPACE_ID}/members", json={"email": "carol@b.example", "role": "viewer"}
    )

    assert added.status_code == 409
    assert added.json()["error"]["details"]["reason"] == "use_invite"


async def _user_id(database: Database, email: str) -> str:
    async with database.session() as session:
        user_id = await session.scalar(select(User.id).where(User.email == email))
    assert user_id is not None
    return str(user_id)


# ================================================ R2-20: one `recording.ready` per session
class _RecordingJobs:
    """Stands in for `JobsService`: records the webhook deliveries `emit` enqueues."""

    def __init__(self) -> None:
        self.enqueued: list[tuple[str, dict[str, Any]]] = []

    async def enqueue(self, kind: str, payload: dict[str, Any], **_: Any) -> None:
        self.enqueued.append((kind, payload))


async def _egress_session(database: Database, settings: Settings) -> tuple[LiveKitConnection, str]:
    vault = Vault(settings.master_key)
    async with database.session() as session:
        conn = connection_row(vault)
        session.add(conn)
        await session.flush()
        agent = await add_agent(session, inference_config(), connection_id=conn.id)
        row = SessionRow(
            id="e" * 32,
            agent_id=agent.id,
            connection_id=conn.id,
            config_version=1,
            room_name="lkap-egress-once",
            participant_identity="u",
            participant_name="U",
            status="ended",
            pipeline_mode="cascaded",
            recording_egress_id="EG_ONCE",
            recording_status="active",
        )
        session.add(row)
    return conn, row.id


async def _egress_ended(database: Database, conn: LiveKitConnection) -> None:
    info = EgressInfo(
        egress_id="EG_ONCE",
        status=EgressStatus.EGRESS_COMPLETE,
        file_results=[FileInfo(duration=30_000_000_000)],
    )
    async with database.session() as session:
        event = WebhookEvent(event="egress_ended", egress_info=info)
        await dispatch_webhook(WebhookContext(db=session, connection=conn, event=event))


@pytest.mark.parametrize("order", ["webhook_first", "worker_first", "worker_twice"])
async def test_egress_ended_and_the_worker_fallback_deliver_recording_ready_once(
    admin_client: httpx.AsyncClient,
    service_client: httpx.AsyncClient,
    database: Database,
    settings: Settings,
    order: str,
) -> None:
    hook = await admin_client.post(
        "/v1/webhooks", json={"url": "https://hooks.example/lkap", "events": [RECORDING_READY]}
    )
    assert hook.status_code == 201, hook.text
    conn, session_id = await _egress_session(database, settings)

    async def worker_report() -> None:
        response = await service_client.post(
            f"/internal/v1/sessions/{session_id}/recording",
            json={"egress_id": "EG_ONCE", "status": "ready", "duration_s": 30.0},
        )
        assert response.status_code == 204, response.text

    if order == "webhook_first":
        await _egress_ended(database, conn)
        await worker_report()
    elif order == "worker_first":
        await worker_report()
        await _egress_ended(database, conn)
    else:
        await worker_report()
        await worker_report()

    async with database.session() as session:
        jobs = list((await session.execute(select(Job).where(Job.kind == RECORDING_FINALIZE))).scalars())
    assert len(jobs) == 1
    recording_jobs = _RecordingJobs()
    ctx = JobContext(
        database=database,
        settings=settings,
        vault=Vault(settings.master_key),
        http=httpx.AsyncClient(),
        jobs=recording_jobs,  # type: ignore[arg-type]
    )
    for job_row in jobs:
        await handle_recording_finalize(ctx, dict(job_row.payload))
    await ctx.http.aclose()
    async with database.session() as session:
        deliveries = list(
            (
                await session.execute(
                    select(WebhookDelivery).where(WebhookDelivery.event_type == RECORDING_READY)
                )
            )
            .scalars()
            .all()
        )
    assert len(deliveries) == 1
    assert deliveries[0].payload["data"]["session_id"] == session_id


async def test_a_not_yet_ready_report_does_not_use_up_the_one_finalise_job(
    service_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    conn, session_id = await _egress_session(database, settings)

    early = await service_client.post(
        f"/internal/v1/sessions/{session_id}/recording", json={"egress_id": "EG_ONCE", "status": "active"}
    )
    await _egress_ended(database, conn)

    assert early.status_code == 204
    async with database.session() as session:
        count = await session.scalar(
            select(func.count()).select_from(Job).where(Job.kind == RECORDING_FINALIZE)
        )
    assert count == 1


# =========================================== #75: one worker callback url; #76: call events
@pytest.mark.parametrize(
    ("api_base", "public_base", "expected"),
    [
        ("http://api.internal:8096/", "https://lkap.example", "http://api.internal:8096"),
        (None, "https://lkap.example/", "https://lkap.example"),
        (None, None, "http://127.0.0.1:8096"),
    ],
)
def test_bundle_api_base_url_is_the_worker_callback_url(
    settings: Settings, api_base: str | None, public_base: str | None, expected: str
) -> None:
    settings.api_base_url = api_base
    settings.public_base_url = public_base
    settings.port = 8096

    assert bundle.api_base_url(settings) == expected == settings.worker_callback_base_url


async def _call_jobs(database: Database) -> list[Job]:
    async with database.session() as session:
        rows = await session.execute(select(Job).where(Job.kind == CALL_EVENT_JOB).order_by(Job.created_at))
        return list(rows.scalars())


async def test_answering_and_hanging_up_a_call_emit_call_started_and_call_ended_once(
    admin_client: httpx.AsyncClient, world: World, database: Database, settings: Settings
) -> None:
    hook = await admin_client.post(
        "/v1/webhooks", json={"url": "https://hooks.example/calls", "events": [CALL_STARTED, CALL_ENDED]}
    )
    assert hook.status_code == 201, hook.text
    await _trunk(admin_client, world, "outbound")
    placed = await admin_client.post("/v1/calls", json={"agent_id": world.agent_id, "to_e164": CALLEE})
    assert placed.status_code == 201, placed.text
    call_id = placed.json()["id"]
    answered = await _call_jobs(database)
    hung_up = await admin_client.post(f"/v1/calls/{call_id}/hangup")
    again = await admin_client.post(f"/v1/calls/{call_id}/hangup")  # a no-op transition emits nothing

    assert hung_up.status_code == 200, hung_up.text
    assert again.status_code == 409
    jobs = await _call_jobs(database)
    assert [j.payload["event_type"] for j in answered] == [CALL_STARTED]
    assert [j.payload["event_type"] for j in jobs] == [CALL_STARTED, CALL_ENDED]
    assert jobs[1].payload["data"]["status"] == "completed"
    assert jobs[1].payload["data"]["call_id"] == call_id

    recording_jobs = _RecordingJobs()
    async with httpx.AsyncClient() as http:
        ctx = JobContext(
            database=database,
            settings=settings,
            vault=Vault(settings.master_key),
            http=http,
            jobs=recording_jobs,  # type: ignore[arg-type]
        )
        for row in jobs:
            await _emit_call_event(ctx, dict(row.payload))
    async with database.session() as session:
        deliveries = list(
            (await session.execute(select(WebhookDelivery).order_by(WebhookDelivery.id))).scalars()
        )
    assert sorted(d.event_type for d in deliveries) == sorted([CALL_STARTED, CALL_ENDED])
    assert len(recording_jobs.enqueued) == 2


def test_call_events_are_offered_in_the_webhook_picker() -> None:
    assert {CALL_STARTED, CALL_ENDED} <= set(KNOWN_EVENTS)


def test_a_call_outside_any_session_advances_without_queueing_an_event() -> None:
    call = Call(id="x" * 32, workspace_id=DEFAULT_WORKSPACE_ID, direction="outbound", status="dialing")

    assert advance(call, "answered") is True
    assert advance(call, "completed") is True


# ================================================ LOW closures: R2-26, R2-34; R-V2-31
@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\a\\claim.pdf", "claim.pdf"),
        ("/abs/notes.md", "notes.md"),
        ("..", "document"),
        ("", "document"),
        (None, "document"),
        ("policy.txt", "policy.txt"),
    ],
)
def test_kb_upload_keys_use_the_basename(filename: str | None, expected: str) -> None:
    name = upload_basename(filename)

    assert name == expected
    assert upload_storage_key("kb1", "doc1", name) == f"kb/kb1/doc1_{expected}"


@pytest.mark.parametrize(
    "url",
    ["http://169.254.169.254", "http://10.0.0.5:9000", "http://minio.localhost:9000", "http://2130706433"],
)
def test_an_admin_set_storage_endpoint_on_a_private_address_is_422(settings: Settings, url: str) -> None:
    settings.env = "prod"
    settings.net_allow_private_hosts = None

    with pytest.raises(UnprocessableEntityError) as raised:
        validate_endpoint_url(url, settings)

    assert raised.value.details == {"field": "endpoint_url", "reason": "blocked_destination"}


@pytest.mark.parametrize(
    "url", [None, "", "https://s3.eu-west-1.amazonaws.com", "https://abc.r2.cloudflarestorage.com"]
)
def test_public_or_absent_storage_endpoints_pass(settings: Settings, url: str | None) -> None:
    validate_endpoint_url(url, settings)


def test_the_stuck_dial_sweep_waits_195_seconds() -> None:
    """R-V2-31: 120 s ring + 15 s margin + 60 s = 195 s (R-V2-24's "210" was an arithmetic slip)."""
    assert STUCK_DIAL_AFTER_S == 195
