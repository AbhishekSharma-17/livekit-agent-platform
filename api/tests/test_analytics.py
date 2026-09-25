"""`GET /v1/analytics/summary`: the estimate side (COSTS.md §4.4, D-V4-46)."""

from __future__ import annotations

import datetime as dt

import httpx
import pytest
from conftest import create_agent

from lkap_api.db.constants import DEFAULT_WORKSPACE_ID
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.models import SessionCost as SessionCostRow
from lkap_api.db.models import utcnow
from lkap_api.db.session import Database


async def _session(
    database: Database,
    agent_id: str,
    index: int,
    *,
    cost: float | None,
    estimated: float | None,
    lines: list[tuple[str, str, str, float]] | None = None,
) -> None:
    now = utcnow()
    session_id = f"{index:032d}"
    async with database.session() as session:
        session.add(
            SessionRow(
                id=session_id,
                workspace_id=DEFAULT_WORKSPACE_ID,
                agent_id=agent_id,
                config_version=1,
                room_name=f"lkap-analytics-{index}",
                participant_identity="u",
                participant_name="U",
                status="ended",
                pipeline_mode="cascaded",
                created_at=now - dt.timedelta(hours=1),
                started_at=now - dt.timedelta(hours=1),
                ended_at=now - dt.timedelta(hours=1) + dt.timedelta(minutes=2),
                cost_usd=cost,
                estimated_usd=estimated,
            )
        )
        await session.flush()
        for provider_id, model, unit, line_cost in lines or []:
            session.add(
                SessionCostRow(
                    session_id=session_id,
                    provider_id=provider_id,
                    model=model,
                    unit=unit,
                    quantity=1.0,
                    unit_price_usd=line_cost,
                    cost_usd=line_cost,
                    price_version="2026-09-25",
                )
            )


async def test_accuracy_is_computed_only_over_sessions_with_both_figures(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    agent = await create_agent(admin_client, name="Accuracy")
    agent_id = str(agent["id"])
    await _session(database, agent_id, 1, cost=0.12, estimated=0.10)
    await _session(database, agent_id, 2, cost=0.08, estimated=0.10)
    await _session(database, agent_id, 3, cost=0.50, estimated=None)  # no snapshot: not in accuracy
    await _session(database, agent_id, 4, cost=None, estimated=0.40)  # nothing priced: not in accuracy

    body = (await admin_client.get("/v1/analytics/summary?range=7d")).json()

    assert body["sessions_estimated"] == 3
    assert float(body["estimated_usd"]) == pytest.approx(0.60)
    assert body["accuracy_pct"] == pytest.approx(100.0)  # (0.12 + 0.08) / (0.10 + 0.10)
    bucket = body["by_agent"][0]
    assert bucket["sessions_estimated"] == 3
    assert bucket["accuracy_pct"] == pytest.approx(100.0)
    day = body["by_day"][0]
    assert float(day["estimated_usd"]) == pytest.approx(0.60)


async def test_no_estimates_means_no_accuracy(admin_client: httpx.AsyncClient, database: Database) -> None:
    agent = await create_agent(admin_client, name="No estimates")
    await _session(database, str(agent["id"]), 5, cost=0.2, estimated=None)

    body = (await admin_client.get("/v1/analytics/summary")).json()

    assert body["estimated_usd"] is None
    assert body["accuracy_pct"] is None
    assert body["sessions_estimated"] == 0


async def test_top_drivers_are_at_most_eight_and_their_shares_sum_to_100(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    agent = await create_agent(admin_client, name="Drivers")
    lines = [(f"provider-{n}", f"model-{n}", "tokens_in", 0.01 * (n + 1)) for n in range(10)]
    await _session(database, str(agent["id"]), 6, cost=0.55, estimated=0.5, lines=lines)

    body = (await admin_client.get("/v1/analytics/summary")).json()
    drivers = body["top_drivers"]

    assert len(drivers) == 8
    assert drivers[0]["provider_id"] == "provider-9"  # largest first
    assert sum(d["share_pct"] for d in drivers) == pytest.approx(100.0, abs=0.1)
    costs = [float(d["cost_usd"]) for d in drivers]
    assert costs == sorted(costs, reverse=True)
