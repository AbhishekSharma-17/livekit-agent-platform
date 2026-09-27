"""Test runs: creation, read models, the publish gate and the per-session tool mocks (V5-29)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from typing import Any, Final, NoReturn, cast

from lkap_contracts.agent_config import AgentConfig
from lkap_contracts.agent_tests import (
    AgentTestRun,
    AgentTestRunStatus,
    AgentTestVerdict,
    PublishGateRefusal,
)
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.db.models import Agent, AgentTestResult, utcnow
from lkap_api.db.models import AgentTestRun as RunRow
from lkap_api.db.models import Session as SessionRow
from lkap_api.errors import ConflictError, UnprocessableEntityError

__all__ = [
    "ACTIVE_STATUSES",
    "RUN_STALE_AFTER",
    "PublishGateError",
    "check_publish_gate",
    "create_run",
    "expire_stale_runs",
    "latest_run",
    "load_results",
    "run_out",
    "tool_mocks_for_session",
]

#: A run still ``queued``/``running`` this long after it was created stopped without
#: finishing (the process died); it is closed as ``error`` the next time anyone looks.
RUN_STALE_AFTER: Final = dt.timedelta(hours=2)

ACTIVE_STATUSES: Final[tuple[AgentTestRunStatus, ...]] = ("queued", "running")

STALE_ERROR: Final = (
    "the run stopped without finishing (the api or jobs process restarted); run the tests again"
)


class PublishGateError(UnprocessableEntityError):
    """422 ``tests_failing``: the publish gate refused (``details`` is a ``PublishGateRefusal``)."""

    code = "tests_failing"


async def expire_stale_runs(db: AsyncSession, *, workspace_id: str, agent_id: str) -> None:
    """Close the agent's runs that stayed ``queued``/``running`` past :data:`RUN_STALE_AFTER`."""
    cutoff = utcnow() - RUN_STALE_AFTER
    await db.execute(
        update(RunRow)
        .where(
            RunRow.workspace_id == workspace_id,
            RunRow.agent_id == agent_id,
            RunRow.status.in_(ACTIVE_STATUSES),
            RunRow.created_at < cutoff,
        )
        .values(status="error", error=STALE_ERROR, finished_at=utcnow())
    )


async def create_run(
    db: AsyncSession,
    agent: Agent,
    config: AgentConfig,
    *,
    case_ids: Sequence[str] | None,
    created_by: str | None,
) -> RunRow:
    """Insert a ``queued`` run pinned to the agent's current version, with one result row per case.

    Raises:
        UnprocessableEntityError: The agent has no test cases, or ``case_ids`` names an unknown one.
        ConflictError: A run of this agent is still queued or running.
    """
    if not config.tests:
        raise UnprocessableEntityError(
            "this agent has no test cases; add some to `tests` in its configuration first",
            details={"path": "tests"},
        )
    known = {case.id: case for case in config.tests}
    if case_ids is not None:
        unknown = sorted(set(case_ids) - set(known))
        if unknown:
            raise UnprocessableEntityError(
                f"unknown test case id: {', '.join(unknown)}", details={"unknown": unknown}
            )
        chosen = [known[case_id] for case_id in dict.fromkeys(case_ids)]
    else:
        chosen = list(config.tests)
    if not chosen:
        raise UnprocessableEntityError("choose at least one test case", details={"path": "case_ids"})

    await expire_stale_runs(db, workspace_id=agent.workspace_id, agent_id=agent.id)
    active = await db.scalar(
        select(RunRow.id).where(
            RunRow.workspace_id == agent.workspace_id,
            RunRow.agent_id == agent.id,
            RunRow.status.in_(ACTIVE_STATUSES),
        )
    )
    if active is not None:
        raise ConflictError(
            "a test run of this agent is still in progress; wait for it to finish",
            details={"run_id": active},
        )

    run = RunRow(
        workspace_id=agent.workspace_id,
        agent_id=agent.id,
        config_version=agent.config_version,
        status="queued",
        created_by=created_by,
        summary={"case_ids": [case.id for case in chosen]},
    )
    db.add(run)
    await db.flush()
    for ordinal, case in enumerate(chosen):
        db.add(
            AgentTestResult(
                run_id=run.id,
                workspace_id=agent.workspace_id,
                case_id=case.id,
                ordinal=ordinal,
                status="pending",
                mocks=dict(case.mocks) or None,
            )
        )
    await db.flush()
    return run


async def load_results(db: AsyncSession, run: RunRow) -> list[AgentTestResult]:
    """A run's result rows in case order."""
    return list(
        (
            await db.execute(
                select(AgentTestResult)
                .where(AgentTestResult.run_id == run.id, AgentTestResult.workspace_id == run.workspace_id)
                .order_by(AgentTestResult.ordinal)
            )
        )
        .scalars()
        .all()
    )


def _verdicts(results: Sequence[AgentTestResult]) -> list[AgentTestVerdict]:
    verdicts: list[AgentTestVerdict] = []
    for result in results:
        if result.verdict is None:
            continue
        try:
            verdicts.append(AgentTestVerdict.model_validate(result.verdict))
        except ValidationError:
            continue
    return verdicts


def run_out(run: RunRow, results: Sequence[AgentTestResult] | None = None) -> AgentTestRun:
    """The ``AgentTestRun`` read model; ``results`` fills ``verdicts`` (omitted in lists)."""
    summary: dict[str, Any] = dict(run.summary or {})
    ratio = summary.get("pass_ratio")
    return AgentTestRun(
        id=run.id,
        agent_id=run.agent_id,
        config_version=run.config_version,
        status=cast(AgentTestRunStatus, run.status),  # the column's check constraint
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        case_ids=[str(case_id) for case_id in summary.get("case_ids", [])],
        passed=int(summary.get("passed", 0)),
        failed=int(summary.get("failed", 0)),
        inconclusive=int(summary.get("inconclusive", 0)),
        errored=int(summary.get("errored", 0)),
        pass_ratio=float(ratio) if isinstance(ratio, int | float) else None,
        error=run.error,
        persona_model=summary.get("persona_model"),
        judge_model=summary.get("judge_model"),
        verdicts=_verdicts(results) if results is not None else [],
    )


async def latest_run(
    db: AsyncSession, *, workspace_id: str, agent_id: str, config_version: int | None = None
) -> RunRow | None:
    """The agent's newest run (on ``config_version`` when given)."""
    query = select(RunRow).where(RunRow.workspace_id == workspace_id, RunRow.agent_id == agent_id)
    if config_version is not None:
        query = query.where(RunRow.config_version == config_version)
    return (await db.execute(query.order_by(RunRow.created_at.desc(), RunRow.id.desc()).limit(1))).scalar()


async def check_publish_gate(db: AsyncSession, agent: Agent, config: AgentConfig) -> None:
    """Refuse publishing when the gate is on and the latest run on this version does not pass.

    Opt-in: nothing is checked when ``publish_gate.require_tests`` is false. A run
    that could not run (``error``) refuses too, and the message says the tests did
    not run rather than that they failed.

    Raises:
        PublishGateError: 422 ``tests_failing`` with a ``PublishGateRefusal`` as ``details``.
    """
    gate = config.publish_gate
    if not gate.require_tests:
        return
    await expire_stale_runs(db, workspace_id=agent.workspace_id, agent_id=agent.id)
    run = await latest_run(
        db, workspace_id=agent.workspace_id, agent_id=agent.id, config_version=agent.config_version
    )
    version = agent.config_version
    way_out = (
        "run the tests on this version, or turn off “Require passing tests” (publish_gate.require_tests)"
    )
    if run is None:
        _refuse(
            f"publishing needs a passing test run on version {version}, and there is none yet: {way_out}",
            PublishGateRefusal(reason="missing", config_version=version, min_pass_ratio=gate.min_pass_ratio),
        )
    out = run_out(run)
    base: dict[str, Any] = {
        "config_version": version,
        "min_pass_ratio": gate.min_pass_ratio,
        "run_id": run.id,
        "run_status": out.status,
        "pass_ratio": out.pass_ratio,
    }
    if out.status in ACTIVE_STATUSES:
        _refuse(
            "the test run on this version has not finished yet; publish once it has",
            PublishGateRefusal(reason="running", **base),
        )
    ratio = out.pass_ratio
    if ratio is not None and ratio + 1e-9 >= gate.min_pass_ratio:
        return
    if out.status == "error" or out.errored:
        why = out.error or f"{out.errored} of {len(out.case_ids)} cases could not run"
        _refuse(
            "the tests on this version did not run, so they neither passed nor failed "
            f"({why}); fix that and {way_out}",
            PublishGateRefusal(reason="error", error=why, **base),
        )
    _refuse(
        f"the latest test run on version {version} passed {out.passed} of {len(out.case_ids)} cases; "
        f"at least {gate.min_pass_ratio:.0%} must pass. Fix the agent and run the tests again, "
        "or turn off “Require passing tests”",
        PublishGateRefusal(reason="failing", **base),
    )


def _refuse(message: str, details: PublishGateRefusal) -> NoReturn:
    raise PublishGateError(message, details=details.model_dump(mode="json"))


async def tool_mocks_for_session(db: AsyncSession, session: SessionRow) -> dict[str, Any]:
    """The tool mocks of a test case's scratch session (``ResolvedAgentConfig.tool_mocks``).

    Only a case that is running right now and owns this session gets its mocks;
    every other session (every real one) gets ``{}``.
    """
    mocks = await db.scalar(
        select(AgentTestResult.mocks).where(
            AgentTestResult.session_id == session.id,
            AgentTestResult.workspace_id == session.workspace_id,
            AgentTestResult.status == "running",
        )
    )
    return dict(mocks) if isinstance(mocks, dict) else {}
