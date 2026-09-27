"""V5-32: answering-machine results, warm-transfer routes and records, validators, ``v5_007``.

LiveKit is faked at the boundary (``telephony_fakes``); the migration runs on a scratch
copy of the v1 seed under ``tmp_path``, never the live database.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import test_telephony as telephony_suite
from alembic.config import Config
from conftest import inference_config
from lkap_contracts.agent_config import AgentConfig
from lkap_contracts.connections import ConnectionCapabilities
from lkap_contracts.telephony import AmdConfig, TelephonyConfig, TransferTarget, WarmTransferRoute
from sqlalchemy import select
from test_telephony import (
    OUTBOUND_NUMBER,
    TEST_POLICY,
    World,
    _call,
    _inbound_session,
    _put_config,
    _set_policy,
    _trunk,
)

from alembic import command
from lkap_api.config_service import (
    AMD_DEFAULT_MESSAGE_TIP,
    AMD_NEEDS_CASCADED_MESSAGE,
    AMD_NEEDS_OUTBOUND_MESSAGE,
    WARM_NEEDS_CLOUD_MESSAGE,
    WARM_NEEDS_ONE_LINE_MESSAGE,
    ConnectionContext,
    ValidationContext,
    amd_and_transfer_issues,
    validation_context_for,
)
from lkap_api.db.constants import DEFAULT_WORKSPACE_ID
from lkap_api.db.models import Call, Job, LiveKitConnection
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.session import Database
from lkap_api.telephony.calls import apply_amd_result, call_out, warm_transfer_route
from lkap_api.webhooks.events import CALL_VOICEMAIL, KNOWN_EVENTS

#: The telephony suite's world (a seeded SIP connection and agent, LiveKit faked, the test
#: dialing policy), reused by name.
fake_lk = telephony_suite.fake_lk
fake_factory = telephony_suite.fake_factory
dial_policy = telephony_suite.dial_policy
world = telephony_suite.world

API_ROOT = Path(__file__).resolve().parents[1]
REPORT = "/internal/v1/telephony/calls/report"
WARM_TO = "+15550009999"


# ------------------------------------------------------------------ helpers
async def _call_row(database: Database, call_id: str) -> Call:
    async with database.session() as session:
        row = await session.get(Call, call_id)
    assert row is not None
    return row


async def _voicemail_jobs(database: Database) -> list[dict[str, Any]]:
    async with database.session() as session:
        rows = (await session.scalars(select(Job).where(Job.kind == "call_event"))).all()
    return [dict(row.payload) for row in rows if row.payload.get("event_type") == CALL_VOICEMAIL]


def _warm_config(*targets: tuple[str, str, str]) -> AgentConfig:
    return inference_config().model_copy(
        update={
            "telephony": TelephonyConfig(
                transfer_targets=[
                    TransferTarget.model_validate({"label": label, "to": to, "mode": mode})
                    for label, to, mode in targets
                ]
            )
        }
    )


# ------------------------------------------------------------------ AMD results
async def test_a_voicemail_verdict_is_stored_once_and_fires_call_voicemail(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    call = await _call(admin_client, world)
    session_id = str(call["session_id"])

    first = await service_client.post(
        REPORT, json={"session_id": session_id, "status": "answered", "amd_result": "machine-vm"}
    )
    again = await service_client.post(
        REPORT, json={"session_id": session_id, "status": "answered", "amd_result": "human"}
    )

    assert first.status_code == 200, first.text
    assert first.json()["amd_result"] == "machine-vm"
    assert again.json()["amd_result"] == "machine-vm"  # the first verdict stands
    (job,) = await _voicemail_jobs(database)
    assert job["workspace_id"] == DEFAULT_WORKSPACE_ID
    assert job["data"]["call_id"] == call["id"] and job["data"]["amd_result"] == "machine-vm"
    assert (await _call_row(database, str(call["id"]))).amd_result == "machine-vm"


async def test_a_person_answering_is_recorded_without_a_webhook(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    call = await _call(admin_client, world)

    response = await service_client.post(
        REPORT, json={"session_id": call["session_id"], "status": "answered", "amd_result": "human"}
    )

    assert response.json()["amd_result"] == "human"
    assert await _voicemail_jobs(database) == []


async def test_an_unknown_verdict_is_a_422(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient, world: World
) -> None:
    call = await _call(admin_client, world)
    response = await service_client.post(
        REPORT, json={"session_id": call["session_id"], "status": "answered", "amd_result": "fax"}
    )
    assert response.status_code == 422


def test_call_voicemail_is_a_known_webhook_event() -> None:
    assert CALL_VOICEMAIL == "call.voicemail"
    assert CALL_VOICEMAIL in KNOWN_EVENTS


def test_apply_amd_result_ignores_nonsense_and_keeps_the_first() -> None:
    row = Call(id="c1", workspace_id="w", direction="outbound", status="answered")
    assert apply_amd_result(row, None) is False
    assert apply_amd_result(row, "fax") is False
    assert apply_amd_result(row, "uncertain") is True
    assert apply_amd_result(row, "machine-vm") is False
    assert row.amd_result == "uncertain"


def test_call_out_reads_an_unknown_stored_value_as_none() -> None:
    row = Call(
        id="c1",
        workspace_id="w",
        direction="outbound",
        status="answered",
        from_e164="",
        to_e164="",
        amd_result="odd",
        transfer_mode="hot",
    )
    out = call_out(row)
    assert (out.amd_result, out.transfer_mode) == (None, None)


# ------------------------------------------------------------------ transfer records
async def test_a_warm_transfer_report_moves_the_call_and_keeps_mode_target_and_summary(
    service_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    session_id = await _inbound_session(database, world)
    await service_client.post(REPORT, json={"session_id": session_id, "status": "answered"})

    response = await service_client.post(
        REPORT,
        json={
            "session_id": session_id,
            "status": "transferred",
            "transfer_mode": "warm",
            "transfer_to": WARM_TO,
            "transfer_summary": "Wants to amend claim C-1.",
        },
    )

    body = response.json()
    assert response.status_code == 200, response.text
    assert (body["status"], body["transfer_mode"], body["transfer_to"], body["transfer_summary"]) == (
        "transferred",
        "warm",
        WARM_TO,
        "Wants to amend claim C-1.",
    )
    detail = await service_client.post(REPORT, json={"session_id": session_id, "status": "completed"})
    assert detail.json()["status"] == "transferred"  # terminal: the teardown's report changes nothing


async def test_a_cold_fallback_keeps_the_summary_on_the_row_the_refer_already_moved(
    service_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    session_id = await _inbound_session(database, world)
    refer = await service_client.post(
        f"/internal/v1/telephony/sessions/{session_id}/transfer",
        json={"to": "+15550002222", "participant_identity": "sip_caller"},
    )
    assert refer.json()["ok"] is True

    response = await service_client.post(
        REPORT,
        json={
            "session_id": session_id,
            "status": "transferred",
            "transfer_mode": "cold",
            "transfer_to": "+15550002222",
            "transfer_summary": "Wanted the claims desk; warm was not available.",
        },
    )

    body = response.json()
    assert (body["status"], body["transfer_mode"], body["transfer_to"]) == (
        "transferred",
        "cold",
        "+15550002222",
    )
    assert body["transfer_summary"].startswith("Wanted the claims desk")


# ------------------------------------------------------------------ the warm route
async def test_the_warm_route_names_the_trunk_and_only_the_policy_allowed_warm_targets(
    admin_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    await _trunk(admin_client, world, "outbound")
    session_id = await _inbound_session(database, world)
    config = _warm_config(
        ("Claims", WARM_TO, "warm"), ("Abroad", "+14155550100", "warm"), ("Sales", "+15550001111", "cold")
    )

    async with database.session() as db:
        row = await db.get(SessionRow, session_id)
        conn = await db.get(LiveKitConnection, world.connection_id)
        assert row is not None and conn is not None
        route = await warm_transfer_route(db, row, conn, config)

    assert route == WarmTransferRoute(trunk_id="ST_out_1", caller_id=OUTBOUND_NUMBER, targets=[WARM_TO])


async def test_no_warm_route_without_cloud_a_single_trunk_or_a_warm_target(
    admin_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    session_id = await _inbound_session(database, world)
    warm = _warm_config(("Claims", WARM_TO, "warm"))

    async with database.session() as db:
        row = await db.get(SessionRow, session_id)
        conn = await db.get(LiveKitConnection, world.connection_id)
        assert row is not None and conn is not None
        assert await warm_transfer_route(db, row, conn, warm) is None  # no outbound trunk yet
    await _trunk(admin_client, world, "outbound")
    async with database.session() as db:
        row = await db.get(SessionRow, session_id)
        conn = await db.get(LiveKitConnection, world.connection_id)
        assert row is not None and conn is not None
        assert await warm_transfer_route(db, row, conn, _warm_config(("Claims", WARM_TO, "cold"))) is None
        assert await warm_transfer_route(db, row, conn, warm) is not None
        conn.deployment_type = "self_hosted"
        assert await warm_transfer_route(db, row, conn, warm) is None
        conn.deployment_type = "cloud"
        row.channel = "web"
        assert await warm_transfer_route(db, row, conn, warm) is None
        await db.rollback()
    await _trunk(admin_client, world, "outbound", name="second line")
    async with database.session() as db:
        row = await db.get(SessionRow, session_id)
        conn = await db.get(LiveKitConnection, world.connection_id)
        assert row is not None and conn is not None
        assert await warm_transfer_route(db, row, conn, warm) is None  # two lines: which one?


async def test_the_policy_is_read_when_the_session_resolves(
    admin_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    await _trunk(admin_client, world, "outbound")
    session_id = await _inbound_session(database, world)
    await _set_policy(database, {"allowed_prefixes": ["+1444"]})

    async with database.session() as db:
        row = await db.get(SessionRow, session_id)
        conn = await db.get(LiveKitConnection, world.connection_id)
        assert row is not None and conn is not None
        assert await warm_transfer_route(db, row, conn, _warm_config(("Claims", WARM_TO, "warm"))) is None
    await _set_policy(database, TEST_POLICY)


async def test_the_resolved_document_carries_the_warm_route(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient, world: World
) -> None:
    saved = await _put_config(
        admin_client,
        world.agent_id,
        telephony={"transfer_targets": [{"label": "Claims", "to": WARM_TO, "mode": "warm"}]},
    )
    assert saved.status_code == 200, saved.text
    call = await _call(admin_client, world)

    resolved = await service_client.get(f"/internal/v1/sessions/{call['session_id']}/resolved")

    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["warm_transfer"] == {
        "trunk_id": "ST_out_1",
        "caller_id": OUTBOUND_NUMBER,
        "targets": [WARM_TO],
    }


async def test_a_session_without_warm_targets_resolves_with_no_route(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient, world: World
) -> None:
    call = await _call(admin_client, world)
    resolved = await service_client.get(f"/internal/v1/sessions/{call['session_id']}/resolved")
    assert resolved.json()["warm_transfer"] is None


# ------------------------------------------------------------------ validators
def _connection(deployment: str = "cloud", *, sip: bool = True) -> ConnectionContext:
    return ConnectionContext(
        connection_id="conn-1",
        deployment_type=deployment,  # type: ignore[arg-type]
        capabilities=ConnectionCapabilities(sip_enabled=sip),
    )


def test_a_warm_target_on_self_hosted_warns_it_falls_back() -> None:
    config = _warm_config(("Sales", "+15550001111", "cold"), ("Claims", WARM_TO, "warm"))
    issues = amd_and_transfer_issues(ValidationContext(config=config, connection=_connection("self_hosted")))
    assert [(i.path, i.message, i.severity) for i in issues] == [
        ("telephony.transfer_targets[1].mode", WARM_NEEDS_CLOUD_MESSAGE, "warning")
    ]
    assert "standard transfer" in WARM_NEEDS_CLOUD_MESSAGE


@pytest.mark.parametrize(("trunks", "warned"), [(0, True), (1, False), (2, True), (None, False)])
def test_a_warm_target_needs_exactly_one_outbound_line(trunks: int | None, warned: bool) -> None:
    config = _warm_config(("Claims", WARM_TO, "warm"))
    issues = amd_and_transfer_issues(
        ValidationContext(config=config, connection=_connection(), outbound_trunks=trunks)
    )
    assert [i.message for i in issues] == ([WARM_NEEDS_ONE_LINE_MESSAGE] if warned else [])


def test_amd_without_an_outbound_line_or_on_a_realtime_model_warns() -> None:
    amd = TelephonyConfig(amd=AmdConfig(enabled=True, on_machine="leave_message"))
    config = inference_config().model_copy(update={"telephony": amd})
    no_line = amd_and_transfer_issues(
        ValidationContext(config=config, connection=_connection(), outbound_trunks=0)
    )
    assert [(i.path, i.message) for i in no_line] == [
        ("telephony.amd.enabled", AMD_NEEDS_OUTBOUND_MESSAGE),
        ("telephony.amd.message", AMD_DEFAULT_MESSAGE_TIP),
    ]
    assert all(i.severity == "warning" for i in no_line)
    realtime = config.model_copy(update={"pipeline": config.pipeline.model_copy(update={"mode": "realtime"})})
    issues = amd_and_transfer_issues(
        ValidationContext(config=realtime, connection=_connection(sip=False), outbound_trunks=1)
    )
    assert AMD_NEEDS_CASCADED_MESSAGE in [i.message for i in issues]
    assert AMD_NEEDS_OUTBOUND_MESSAGE in [i.message for i in issues]


def test_defaults_add_no_finding() -> None:
    """Compatibility: an agent saved before V5-32 (amd off, targets cold) validates as before."""
    config = _warm_config(("Sales", "+15550001111", "cold"))
    assert (
        amd_and_transfer_issues(ValidationContext(config=config, connection=_connection("self_hosted"))) == []
    )
    assert amd_and_transfer_issues(ValidationContext(config=inference_config())) == []


async def test_the_validation_context_counts_the_synced_outbound_lines(
    admin_client: httpx.AsyncClient, world: World, database: Database
) -> None:
    async with database.session() as db:
        before = await validation_context_for(
            db, inference_config(), workspace_id=DEFAULT_WORKSPACE_ID, connection_id=world.connection_id
        )
    await _trunk(admin_client, world, "outbound")
    await _trunk(admin_client, world, "inbound")
    async with database.session() as db:
        after = await validation_context_for(
            db, inference_config(), workspace_id=DEFAULT_WORKSPACE_ID, connection_id=world.connection_id
        )
    assert (before.outbound_trunks, after.outbound_trunks) == (0, 1)


async def test_saving_a_warm_target_on_a_cloud_connection_without_a_line_is_accepted_with_a_warning(
    admin_client: httpx.AsyncClient, world: World
) -> None:
    response = await _put_config(
        admin_client,
        world.agent_id,
        telephony={
            "transfer_targets": [{"label": "Claims", "to": WARM_TO, "mode": "warm"}],
            "amd": {"enabled": True},
        },
    )
    assert response.status_code == 200, response.text
    validation = await admin_client.post(f"/v1/agents/{world.agent_id}/validate")
    paths = {issue["path"] for issue in validation.json().get("issues", [])}
    assert {"telephony.transfer_targets[0].mode", "telephony.amd.enabled"} <= paths


# ------------------------------------------------------------------ migration
REVISION = "v5_007_telephony_amd"
PREVIOUS = "v5_005_knowledge_connections"


@pytest.fixture
def scratch_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A scratch copy of the v1 seed, with the developer's LiveKit env kept out."""
    for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "LKAP_MASTER_KEY"):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "lkap.db"
    shutil.copy(API_ROOT / "tests" / "fixtures" / "v1_seed.sqlite", path)
    yield path


def _migrate(database: Path, revision: str, *, downgrade: bool = False) -> None:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    config.cmd_opts = None  # type: ignore[assignment]
    url = f"sqlite+aiosqlite:///{database}"
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["configure_logger"] = False
    os.environ["LKAP_DATABASE_URL"] = url
    try:
        (command.downgrade if downgrade else command.upgrade)(config, revision)
    finally:
        os.environ.pop("LKAP_DATABASE_URL", None)


def _calls_columns(database: Path) -> set[str]:
    connection = sqlite3.connect(database)
    try:
        return {row[1] for row in connection.execute("PRAGMA table_info(calls)")}
    finally:
        connection.close()


def _calls_ddl(database: Path) -> str:
    connection = sqlite3.connect(database)
    try:
        return str(connection.execute("SELECT sql FROM sqlite_master WHERE name='calls'").fetchone()[0])
    finally:
        connection.close()


NEW_COLUMNS = {"amd_result", "transfer_mode", "transfer_summary"}


def test_v5_007_up_down_up_adds_and_drops_three_nullable_columns(scratch_db: Path) -> None:
    _migrate(scratch_db, PREVIOUS)
    assert not NEW_COLUMNS & _calls_columns(scratch_db)

    _migrate(scratch_db, REVISION)
    assert NEW_COLUMNS <= _calls_columns(scratch_db)
    connection = sqlite3.connect(scratch_db)
    try:
        info = {row[1]: row for row in connection.execute("PRAGMA table_info(calls)")}
    finally:
        connection.close()
    assert all(info[name][3] == 0 for name in NEW_COLUMNS)  # nullable

    _migrate(scratch_db, PREVIOUS, downgrade=True)
    assert not NEW_COLUMNS & _calls_columns(scratch_db)
    ddl = _calls_ddl(scratch_db)
    assert "status_valid" in ddl and "direction_valid" in ddl  # no rebuild lost the checks

    _migrate(scratch_db, REVISION)
    assert NEW_COLUMNS <= _calls_columns(scratch_db)


def test_v5_007_is_the_single_head() -> None:
    from alembic.script import ScriptDirectory  # noqa: PLC0415

    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    # V5-40: a later migration now chains after this one; there is still exactly one head and
    # v5_007 is on its path.
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    assert len(heads) == 1
    assert REVISION in {rev.revision for rev in script.walk_revisions("base", heads[0])}


async def test_rows_saved_before_v5_007_read_as_none(world: World, database: Database) -> None:
    session_id = await _inbound_session(database, world)
    async with database.session() as db:
        db.add(
            Call(
                id="legacy-call",
                session_id=session_id,
                workspace_id=DEFAULT_WORKSPACE_ID,
                connection_id=world.connection_id,
                direction="inbound",
                status="completed",
            )
        )
    row = await _call_row(database, "legacy-call")
    out = call_out(row)
    assert (out.amd_result, out.transfer_mode, out.transfer_summary) == (None, None, None)
