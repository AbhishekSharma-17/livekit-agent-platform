"""V4-17: the `cost_reconcile` job against a `respx` OpenRouter `/generation` fake (COSTS.md §7).

Offline: every OpenRouter call is mocked; no real key is used.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx
from conftest import captured_text, inference_config
from fastapi import FastAPI
from lkap_contracts.common import ProviderRef
from sqlalchemy import select
from test_internal import _credential, _session_for

from lkap_api.costs.vendors import validate_reconcile
from lkap_api.costs.vendors.openrouter import (
    MAX_LOOKUPS_PER_S,
    OpenRouterAuthError,
    fetch_generations,
    openrouter_api_key,
)
from lkap_api.db.models import AuditLog, Job, ProviderCatalogCache, utcnow
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.models import SessionCost as SessionCostRow
from lkap_api.db.session import Database
from lkap_api.jobs.handlers import load_all_handlers
from lkap_api.jobs.kinds import COST_RECONCILE
from lkap_api.jobs.reconcile import RETRY_DELAY_S
from lkap_api.jobs.service import JobsService
from lkap_api.net_guard import NetPolicy
from lkap_api.settings import Settings
from lkap_api.vault import Vault

SECRET = "sk-or-v1-RECONCILE-SECRET-123"
MODEL = "google/gemma-4-31b-it"
GEN_URL = "https://openrouter.ai/api/v1/generation"
IDS = ["gen-aaa", "gen-bbb", "gen-ccc"]
COSTS = {"gen-aaa": 0.0012, "gen-bbb": 0.00034, "gen-ccc": 0.0005}


def _gen(gen_id: str) -> httpx.Response:
    return httpx.Response(200, json={"data": {"id": gen_id, "model": MODEL, "total_cost": COSTS[gen_id]}})


def _by_id(request: httpx.Request) -> httpx.Response:
    return _gen(request.url.params["id"])


async def _no_sleep(_s: float) -> None:
    return None


@pytest.fixture(autouse=True)
def _fast_pacing(monkeypatch: pytest.MonkeyPatch) -> None:
    """The job paces lookups at 10/s and backs off on transient errors; tests don't wait."""
    import lkap_api.jobs.reconcile as reconcile

    monkeypatch.setattr(reconcile, "SLEEP", _no_sleep)


async def _opt_in(admin_client: httpx.AsyncClient, vendors: list[str]) -> httpx.Response:
    return await admin_client.put(
        "/v1/workspaces/default", json={"settings": {"cost": {"reconcile": vendors}}}
    )


async def _openrouter_session(admin_client: httpx.AsyncClient, database: Database) -> str:
    """An agent on `openrouter-llm` with a cached live price, and a session for it."""
    credential_id = await _credential(admin_client, "openrouter-llm", {"api_key": SECRET})
    base = inference_config()
    config = base.model_copy(
        update={
            "pipeline": base.pipeline.model_copy(
                update={
                    "llm": ProviderRef(provider_id="openrouter-llm", credential_id=credential_id, model=MODEL)
                }
            )
        }
    )
    async with database.session() as db:
        db.add(
            ProviderCatalogCache(
                provider_id="openrouter-llm",
                credential_id=None,
                kind="models",
                items=[
                    {
                        "id": MODEL,
                        "label": "Gemma",
                        "meta": {"pricing": {"prompt": "0.0000004", "completion": "0.0000016"}},
                    }
                ],
                ttl_s=21600,
            )
        )
    session_id, _ = await _session_for(admin_client, json.loads(config.model_dump_json()))
    return session_id


def _provider_requests_event(ids: list[str]) -> dict[str, Any]:
    return {
        "ts": time.time(),
        "type": "metrics",
        "payload": {
            "kind": "provider_requests",
            "data": {
                "llm": [{"request_id": i, "provider": "openrouter.ai", "model": MODEL} for i in ids],
                "stt": [],
                "tts": [],
                "dropped": 0,
            },
        },
    }


async def _finish(service_client: httpx.AsyncClient, session_id: str, ids: list[str] | None = IDS) -> None:
    resolved = await service_client.get(f"/internal/v1/sessions/{session_id}/resolved")
    assert resolved.status_code == 200, resolved.text
    if ids is not None:
        posted = await service_client.post(
            f"/internal/v1/sessions/{session_id}/events", json={"events": [_provider_requests_event(ids)]}
        )
        assert posted.status_code == 202, posted.text
    summary = await service_client.put(
        f"/internal/v1/sessions/{session_id}/summary",
        json={
            "status": "ended",
            "usage": {
                "model_usage": [
                    {"type": "llm_usage", "model": MODEL, "input_tokens": 3000, "output_tokens": 600}
                ]
            },
            "transcript": [],
        },
    )
    assert summary.status_code == 204, summary.text


async def _lines(database: Database, session_id: str) -> dict[str, SessionCostRow]:
    async with database.session() as db:
        rows = (
            (await db.execute(select(SessionCostRow).where(SessionCostRow.session_id == session_id)))
            .scalars()
            .all()
        )
    return {row.unit: row for row in rows if row.provider_id == "openrouter-llm"}


async def _session(database: Database, session_id: str) -> SessionRow:
    async with database.session() as db:
        row = await db.get(SessionRow, session_id)
    assert row is not None
    return row


async def _audits(database: Database, session_id: str) -> list[AuditLog]:
    async with database.session() as db:
        return list(
            (
                await db.execute(
                    select(AuditLog).where(
                        AuditLog.action == "cost_reconciled", AuditLog.target_id == session_id
                    )
                )
            )
            .scalars()
            .all()
        )


# ------------------------------------------------------------------------ registration
def test_the_cost_reconcile_handler_is_registered() -> None:
    assert COST_RECONCILE in load_all_handlers()


# --------------------------------------------------------------------------- the job
async def test_the_job_sums_total_cost_onto_the_llm_line_and_the_session(
    app: FastAPI,
    admin_client: httpx.AsyncClient,
    service_client: httpx.AsyncClient,
    database: Database,
    log_capture: pytest.LogCaptureFixture,
) -> None:
    assert (await _opt_in(admin_client, ["openrouter"])).status_code == 200
    session_id = await _openrouter_session(admin_client, database)

    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(GEN_URL).mock(side_effect=_by_id)
        await _finish(service_client, session_id)

    assert route.call_count == 3
    assert {call.request.url.params["id"] for call in route.calls} == set(IDS)
    assert all(call.request.headers["authorization"] == f"Bearer {SECRET}" for call in route.calls)
    lines = await _lines(database, session_id)
    expected = Decimal("0.00204")
    assert lines["tokens_in"].price_source == "live"
    assert Decimal(str(lines["tokens_in"].vendor_usd)) == expected
    assert lines["tokens_in"].vendor_ref == "3 generations"
    assert lines["tokens_out"].vendor_usd is None
    row = await _session(database, session_id)
    assert Decimal(str(row.reconciled_usd)) == expected
    audits = await _audits(database, session_id)
    assert len(audits) == 1
    assert audits[0].actor_type == "system"
    assert audits[0].payload["vendor"] == "openrouter"
    assert audits[0].payload["generations"] == 3
    logs = captured_text(log_capture, scope=None)
    assert "cost_reconciled" in logs
    assert SECRET not in logs


async def test_the_session_detail_shows_the_vendor_figures(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient, database: Database
) -> None:
    await _opt_in(admin_client, ["openrouter"])
    session_id = await _openrouter_session(admin_client, database)
    with respx.mock:
        respx.get(GEN_URL).mock(side_effect=_by_id)
        await _finish(service_client, session_id)

    cost = (await admin_client.get(f"/v1/sessions/{session_id}")).json()["cost"]

    assert Decimal(str(cost["reconciled_usd"])) == Decimal("0.00204")
    vendor = [line for line in cost["lines"] if line.get("vendor_usd") is not None]
    assert [line["unit"] for line in vendor] == ["tokens_in"]


async def test_a_404_is_retried_once_thirty_seconds_later(
    app: FastAPI, admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient, database: Database
) -> None:
    await _opt_in(admin_client, ["openrouter"])
    session_id = await _openrouter_session(admin_client, database)
    first_try: set[str] = set()

    def late_ccc(request: httpx.Request) -> httpx.Response:
        gen_id = request.url.params["id"]
        if gen_id == "gen-ccc" and gen_id not in first_try:
            first_try.add(gen_id)
            return httpx.Response(404, json={"error": {"message": "Generation not found"}})
        return _gen(gen_id)

    with respx.mock:
        route = respx.get(GEN_URL).mock(side_effect=late_ccc)
        await _finish(service_client, session_id)

        # Nothing written yet: the job re-enqueued itself for 30 s later.
        assert (await _session(database, session_id)).reconciled_usd is None
        async with database.session() as db:
            pending = (
                (await db.execute(select(Job).where(Job.kind == COST_RECONCILE, Job.status == "pending")))
                .scalars()
                .all()
            )
        assert len(pending) == 1
        assert pending[0].payload == {"session_id": session_id, "attempt": 2}
        jobs: JobsService = app.state.jobs
        assert await jobs.run_due(now=utcnow() + dt.timedelta(seconds=RETRY_DELAY_S - 5)) == 0
        assert await jobs.run_due(now=utcnow() + dt.timedelta(seconds=RETRY_DELAY_S + 1)) == 1

    assert route.call_count == 6
    row = await _session(database, session_id)
    assert Decimal(str(row.reconciled_usd)) == Decimal("0.00204")
    assert (await _lines(database, session_id))["tokens_in"].vendor_ref == "3 generations"


async def test_a_second_404_writes_what_was_found_and_says_so(
    app: FastAPI, admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient, database: Database
) -> None:
    await _opt_in(admin_client, ["openrouter"])
    session_id = await _openrouter_session(admin_client, database)

    def never_ccc(request: httpx.Request) -> httpx.Response:
        gen_id = request.url.params["id"]
        return httpx.Response(404) if gen_id == "gen-ccc" else _gen(gen_id)

    with respx.mock:
        route = respx.get(GEN_URL).mock(side_effect=never_ccc)
        await _finish(service_client, session_id)
        await app.state.jobs.run_due(now=utcnow() + dt.timedelta(seconds=RETRY_DELAY_S + 1))

    assert route.call_count == 6
    assert Decimal(str((await _session(database, session_id)).reconciled_usd)) == Decimal("0.00154")
    assert (await _lines(database, session_id))["tokens_in"].vendor_ref == "2 generations, 1 not found"
    async with database.session() as db:
        pending = (
            await db.execute(select(Job).where(Job.kind == COST_RECONCILE, Job.status == "pending"))
        ).all()
    assert pending == []


async def test_the_job_is_skipped_when_the_workspace_did_not_opt_in(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient, database: Database
) -> None:
    session_id = await _openrouter_session(admin_client, database)

    with respx.mock:
        route = respx.get(GEN_URL).mock(side_effect=_by_id)
        await _finish(service_client, session_id)

    assert route.call_count == 0
    assert (await _session(database, session_id)).reconciled_usd is None
    async with database.session() as db:
        assert (await db.execute(select(Job).where(Job.kind == COST_RECONCILE))).all() == []


async def test_no_job_without_a_provider_requests_event(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient, database: Database
) -> None:
    await _opt_in(admin_client, ["openrouter"])
    session_id = await _openrouter_session(admin_client, database)

    with respx.mock:
        route = respx.get(GEN_URL).mock(side_effect=_by_id)
        await _finish(service_client, session_id, ids=None)

    assert route.call_count == 0
    async with database.session() as db:
        assert (await db.execute(select(Job).where(Job.kind == COST_RECONCILE))).all() == []


async def test_a_second_run_is_idempotent(
    app: FastAPI, admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient, database: Database
) -> None:
    await _opt_in(admin_client, ["openrouter"])
    session_id = await _openrouter_session(admin_client, database)

    with respx.mock:
        respx.get(GEN_URL).mock(side_effect=_by_id)
        await _finish(service_client, session_id)
        before = await _lines(database, session_id)
        await app.state.jobs.enqueue(COST_RECONCILE, {"session_id": session_id, "attempt": 1})

    after = await _lines(database, session_id)
    assert {u: (r.vendor_usd, r.vendor_ref) for u, r in after.items()} == {
        u: (r.vendor_usd, r.vendor_ref) for u, r in before.items()
    }
    assert Decimal(str((await _session(database, session_id)).reconciled_usd)) == Decimal("0.00204")
    assert len(await _audits(database, session_id)) == 1


async def test_a_refused_key_writes_nothing_and_does_not_retry(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient, database: Database
) -> None:
    await _opt_in(admin_client, ["openrouter"])
    session_id = await _openrouter_session(admin_client, database)

    with respx.mock:
        route = respx.get(GEN_URL).mock(return_value=httpx.Response(401, json={"error": {"code": 401}}))
        await _finish(service_client, session_id)

    assert route.call_count == 1
    assert (await _session(database, session_id)).reconciled_usd is None
    async with database.session() as db:
        jobs = (await db.execute(select(Job).where(Job.kind == COST_RECONCILE))).scalars().all()
    assert [j.status for j in jobs] == ["done"]


async def test_the_key_is_the_slot_s_else_the_only_one_else_none(
    admin_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    vault = Vault(settings.master_key)
    workspace_id = str((await admin_client.get("/v1/workspaces")).json()["items"][0]["id"])
    first = await _credential(admin_client, "openrouter-llm", {"api_key": "sk-or-first"})
    bare = ProviderRef(provider_id="openrouter-llm", model=MODEL)

    async with database.session() as db:
        only = await openrouter_api_key(db, vault, workspace_id=workspace_id, ref=bare)
        other_workspace = await openrouter_api_key(
            db, vault, workspace_id="0" * 32, ref=bare.model_copy(update={"credential_id": first})
        )
    second = await _credential(admin_client, "openrouter-llm", {"api_key": "sk-or-second"})
    async with database.session() as db:
        ambiguous = await openrouter_api_key(db, vault, workspace_id=workspace_id, ref=bare)
        chosen = await openrouter_api_key(
            db, vault, workspace_id=workspace_id, ref=bare.model_copy(update={"credential_id": second})
        )

    assert only == "sk-or-first"
    assert other_workspace is None
    assert ambiguous is None
    assert chosen == "sk-or-second"


# ----------------------------------------------------------------------- the client
async def test_fetch_generations_retries_a_transient_error_then_gives_up() -> None:
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    with respx.mock:
        route = respx.get(GEN_URL).mock(
            side_effect=[httpx.ReadTimeout("slow"), httpx.Response(503), httpx.Response(502), _gen("gen-bbb")]
        )
        async with httpx.AsyncClient() as http:
            lookup = await fetch_generations(
                http, api_key=SECRET, ids=["gen-aaa", "gen-bbb"], policy=NetPolicy(), sleep=sleep
            )

    assert route.call_count == 4
    assert lookup.failed == ["gen-aaa"]
    assert set(lookup.found) == {"gen-bbb"}
    assert lookup.found["gen-bbb"].total_cost == Decimal("0.00034")
    # Backoff 1 s then 2 s for the first id, then one pacing gap before the second.
    assert sleeps == [1.0, 2.0, 1.0 / MAX_LOOKUPS_PER_S]


async def test_fetch_generations_stops_on_a_refused_key_without_echoing_it() -> None:
    with respx.mock:
        respx.get(GEN_URL).mock(return_value=httpx.Response(403))
        async with httpx.AsyncClient() as http:
            with pytest.raises(OpenRouterAuthError) as info:
                await fetch_generations(http, api_key=SECRET, ids=IDS, policy=NetPolicy(), sleep=_no_sleep)

    assert SECRET not in str(info.value)


async def test_fetch_generations_accepts_a_bare_record_and_rejects_a_missing_cost() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.params["id"] == "gen-aaa":
            return httpx.Response(200, json={"id": "gen-aaa", "total_cost": "0.0012", "model": MODEL})
        return httpx.Response(200, json={"data": {"id": "gen-bbb", "model": MODEL}})

    with respx.mock:
        respx.get(GEN_URL).mock(side_effect=respond)
        async with httpx.AsyncClient() as http:
            lookup = await fetch_generations(
                http, api_key=SECRET, ids=["gen-aaa", "gen-bbb"], policy=NetPolicy(), sleep=_no_sleep
            )

    assert lookup.found["gen-aaa"].total_cost == Decimal("0.0012")
    assert lookup.failed == ["gen-bbb"]


# ---------------------------------------------------------------------- the opt-in
async def test_the_opt_in_keeps_the_workspace_prices_beside_it(admin_client: httpx.AsyncClient) -> None:
    prices = await admin_client.put(
        "/v1/workspace/prices",
        json={
            "prices": [
                {
                    "provider_id": "simli-avatar",
                    "model": None,
                    "unit": "minutes",
                    "usd_per_unit": "0.1",
                    "as_of": "2026-09-25",
                }
            ]
        },
    )
    assert prices.status_code == 200, prices.text

    response = await _opt_in(admin_client, ["openrouter"])

    assert response.status_code == 200, response.text
    cost = response.json()["settings"]["cost"]
    assert cost["reconcile"] == ["openrouter"]
    assert [p["provider_id"] for p in cost["prices"]] == ["simli-avatar"]


@pytest.mark.parametrize("vendors", [["openrouter", "anthropic"], ["deepgram"], "openrouter"])
async def test_the_opt_in_refuses_unknown_or_unbuilt_vendors(
    admin_client: httpx.AsyncClient, vendors: object
) -> None:
    response = await admin_client.put(
        "/v1/workspaces/default", json={"settings": {"cost": {"reconcile": vendors}}}
    )

    assert response.status_code == 422, response.text


def test_validate_reconcile_deduplicates() -> None:
    assert validate_reconcile(["openrouter", "openrouter"]) == ["openrouter"]


async def test_the_resolved_config_carries_the_opt_in(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient
) -> None:
    session_id, _ = await _session_for(admin_client)
    before = (await service_client.get(f"/internal/v1/sessions/{session_id}/resolved")).json()
    await _opt_in(admin_client, ["openrouter"])
    session_id, _ = await _session_for(admin_client)
    after = (await service_client.get(f"/internal/v1/sessions/{session_id}/resolved")).json()

    assert before["cost_reconcile"] == []
    assert after["cost_reconcile"] == ["openrouter"]
