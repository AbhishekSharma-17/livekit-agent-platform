"""``/v1/agents/{id}/tests/*``: run an agent's test cases and read the verdicts (V5-29).

Roles follow ``ROUTE_POLICY``'s ``/v1/agents`` rule: reading runs needs ``viewer``
(``agents:read``), starting one needs ``builder`` (``agents:write``).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Body, Query, status
from lkap_contracts.agent_tests import AgentTestRun, AgentTestRunIn, AgentTestRunPage
from sqlalchemy import select

from lkap_api.agent_tests.service import create_run, expire_stale_runs, load_results, run_out
from lkap_api.db.models import AgentTestRun as RunRow
from lkap_api.deps import AdminCtxDep, DbDep
from lkap_api.errors import ConflictError, NotFoundError
from lkap_api.jobs.deps import JobsDep
from lkap_api.jobs.kinds import AGENT_TESTS_RUN
from lkap_api.logging import get_logger
from lkap_api.routers.agents import agent_config_of, load_scoped_agent

log = get_logger(__name__)

router = APIRouter(prefix="/v1/agents", tags=["agent-tests"])


@router.post(
    "/{agent_id}/tests/run",
    response_model=AgentTestRun,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Run an agent's test cases",
    description=(
        "Queues a run of the agent's `tests` (all of them, or `case_ids`) pinned to its current "
        "`config_version`. Each case plays a simulated caller against the agent over a scratch text "
        "session (a ready worker is needed) and five judges score the transcript. Poll "
        "`GET …/tests/runs/{run_id}`. 422 when the agent has no cases or a case id is unknown; 409 "
        "while another run of the agent is in progress."
    ),
)
async def start_run(
    agent_id: str,
    db: DbDep,
    ctx: AdminCtxDep,
    jobs: JobsDep,
    background_tasks: BackgroundTasks,
    payload: Annotated[AgentTestRunIn | None, Body()] = None,
) -> AgentTestRun:
    """Create a queued run and enqueue the ``agent_tests_run`` job.

    Raises:
        NotFoundError: Unknown agent in this workspace.
        ConflictError: The agent is archived, or a run is already in progress.
        UnprocessableEntityError: No test cases, or an unknown case id.
    """
    agent = await load_scoped_agent(db, ctx, agent_id)
    if agent.archived_at is not None:
        raise ConflictError("the agent is archived; unarchive it to run its tests")
    run = await create_run(
        db,
        agent,
        agent_config_of(agent),
        case_ids=payload.case_ids if payload is not None else None,
        created_by=ctx.actor.id,
    )
    results = await load_results(db, run)
    out = run_out(run, results)
    # The job opens its own session: the run must be durable before it can start.
    await db.commit()
    await jobs.enqueue(
        AGENT_TESTS_RUN,
        {"run_id": run.id, "workspace_id": run.workspace_id},
        background_tasks=background_tasks,
    )
    log.info("agent_tests_queued", run_id=run.id, agent_id=agent.id, cases=len(out.case_ids))
    return out


@router.get(
    "/{agent_id}/tests/runs",
    response_model=AgentTestRunPage,
    summary="List an agent's test runs",
    description="Newest first, without the per-case verdicts (read one run for those).",
)
async def list_runs(
    agent_id: str,
    db: DbDep,
    ctx: AdminCtxDep,
    limit: int = Query(default=20, ge=1, le=100, description="How many runs to return"),
) -> AgentTestRunPage:
    """Return the agent's runs, newest first."""
    agent = await load_scoped_agent(db, ctx, agent_id)
    await expire_stale_runs(db, workspace_id=agent.workspace_id, agent_id=agent.id)
    rows = (
        (
            await db.execute(
                select(RunRow)
                .where(RunRow.workspace_id == agent.workspace_id, RunRow.agent_id == agent.id)
                .order_by(RunRow.created_at.desc(), RunRow.id.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return AgentTestRunPage(items=[run_out(row) for row in rows])


@router.get(
    "/{agent_id}/tests/runs/{run_id}",
    response_model=AgentTestRun,
    summary="Get one test run with its verdicts",
    description=(
        "The run's status and totals plus one verdict per finished case: the five judge scores, the "
        "transcript, the tool calls (mocked ones marked) and why the conversation stopped."
    ),
)
async def get_run(agent_id: str, run_id: str, db: DbDep, ctx: AdminCtxDep) -> AgentTestRun:
    """Return one run of the agent.

    Raises:
        NotFoundError: Unknown agent or run in this workspace.
    """
    agent = await load_scoped_agent(db, ctx, agent_id)
    await expire_stale_runs(db, workspace_id=agent.workspace_id, agent_id=agent.id)
    run = await db.scalar(
        select(RunRow).where(
            RunRow.id == run_id, RunRow.workspace_id == agent.workspace_id, RunRow.agent_id == agent.id
        )
    )
    if run is None:
        raise NotFoundError(f"unknown test run '{run_id}'")
    return run_out(run, await load_results(db, run))
