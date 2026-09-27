"""The ``agent_tests_run`` job: play every case's persona, judge the transcripts, store verdicts (V5-29).

One run, cases in order. For each case the runner mints a scratch ``channel="text"``
session for the agent in-process (the same token, dispatch and session row as
``POST /v1/agents/{id}/text-sessions``, under the agent's concurrency slot),
records the session on the case's result row **before** joining — so the
session's resolve delivers the case's tool mocks (``tool_mocks_for_session``) —
joins the room as the caller, captures the greeting, then alternates persona
turns and agent replies until the persona is done, ``max_turns`` is spent, the
agent stops answering or the room closes. The worker's tool-call events are read
back from ``session_events`` and the five judges score the case.

Nothing here raises out of the job: a run that cannot run (the agent changed
since it was queued, no persona or judge model the api can call, no ready worker,
no rtc SDK) finishes ``error`` with the reason, and a case that cannot run
finishes ``error`` — "did not run", never "failed".
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
import secrets
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any, Final

import httpx
from lkap_contracts.agent_config import AgentConfig, AgentLimits
from lkap_contracts.agent_tests import (
    AgentTest,
    AgentTestCaseStatus,
    AgentTestJudgeScore,
    AgentTestRunStatus,
    AgentTestStopReason,
    AgentTestToolCall,
    AgentTestTurn,
    AgentTestVerdict,
)
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api import net_guard
from lkap_api.agent_tests import judges, persona
from lkap_api.agent_tests.transport import (
    LiveKitRoomTransport,
    RoomTransport,
    TransportFactory,
    TransportUnavailableError,
)
from lkap_api.auth.ratelimit import AgentBusyError
from lkap_api.connections.clients import get_client_factory
from lkap_api.connections.service import mint_session_token, resolve_agent_connection
from lkap_api.db.models import Agent, AgentTestResult, SessionEvent, WorkerInstance, new_id, utcnow
from lkap_api.db.models import AgentTestRun as RunRow
from lkap_api.db.models import Session as SessionRow
from lkap_api.errors import ApiError
from lkap_api.jobs.context import JobContext
from lkap_api.jobs.kinds import AGENT_TESTS_RUN
from lkap_api.jobs.registry import job
from lkap_api.limits import reserve_session_slot
from lkap_api.livekit_tokens import room_name_for
from lkap_api.logging import get_logger
from lkap_api.qa.llm_client import JudgeLLM
from lkap_api.qa.resolve import resolve_judge

__all__ = ["LIMITS", "RunnerLimits", "run_agent_tests", "run_agent_tests_job", "transport_factory"]

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RunnerLimits:
    """Timings of one case (tests shorten them)."""

    agent_join_timeout_s: float = 30.0
    greeting_timeout_s: float = 20.0
    reply_timeout_s: float = 60.0
    #: After a final reply, further replies arriving within this window belong to the same turn
    #: (a tool-calling turn often speaks twice).
    settle_s: float = 1.5
    #: How long to wait for the worker's events (tool calls) after leaving the room.
    events_wait_s: float = 8.0
    events_poll_s: float = 0.5


LIMITS = RunnerLimits()

#: Builds one room connection per case; tests replace it with a fake.
transport_factory: TransportFactory = LiveKitRoomTransport

#: Each LLM call of a run (the persona's turn, one judge) may read for this long.
LLM_HTTP_TIMEOUT_S: Final = 60.0

#: Cut a case's error text to this length.
_MAX_ERROR: Final = 500

_DISCONNECTED: Any = object()


class _CaseError(Exception):
    """A case that cannot run: its verdict is ``error`` with this message."""


class _RunError(Exception):
    """The whole run cannot run: it finishes ``error`` with this message."""


@job(AGENT_TESTS_RUN)
async def run_agent_tests_job(ctx: JobContext, payload: dict[str, Any]) -> None:
    """Job handler: play and judge the run named in ``payload`` (``run_id``, ``workspace_id``)."""
    await run_agent_tests(ctx, str(payload["run_id"]), str(payload["workspace_id"]))


async def run_agent_tests(ctx: JobContext, run_id: str, workspace_id: str) -> None:
    """Play and judge one run. Never raises; every outcome is written to the run's rows."""
    try:
        async with _llm_http(ctx) as http:
            await _run(ctx, http, run_id, workspace_id)
    except _RunError as exc:
        await _finish_run(ctx, run_id, workspace_id, status="error", error=str(exc)[:_MAX_ERROR])
    except Exception as exc:  # noqa: BLE001 - the job must finish its row, never retry forever
        log.warning("agent_tests_unexpected_error", run_id=run_id, error_type=type(exc).__name__)
        await _finish_run(
            ctx,
            run_id,
            workspace_id,
            status="error",
            error=f"the run stopped on an unexpected error ({type(exc).__name__})",
        )


@contextlib.asynccontextmanager
async def _llm_http(ctx: JobContext) -> AsyncIterator[httpx.AsyncClient]:
    """The client for the run's LLM calls: the jobs client, or a guarded one with a longer budget.

    The shared jobs client reads for 15 s at most (webhooks, QA re-scores); a judge reading
    a long transcript can take longer, so a run gets its own guarded client then.
    """
    read = ctx.http.timeout.read
    if read is None or read >= LLM_HTTP_TIMEOUT_S:
        yield ctx.http
        return
    policy = net_guard.policy_from_settings(ctx.settings)
    timeout = httpx.Timeout(LLM_HTTP_TIMEOUT_S, connect=10.0)
    async with net_guard.guarded_http_client(policy, timeout=timeout) as client:
        yield client


async def _run(ctx: JobContext, http: httpx.AsyncClient, run_id: str, workspace_id: str) -> None:
    async with ctx.database.session() as db:
        run = await db.scalar(select(RunRow).where(RunRow.id == run_id, RunRow.workspace_id == workspace_id))
        if run is None or run.status != "queued":
            return  # deleted, or already picked up (a re-delivered job)
        run.status = "running"
        run.started_at = utcnow()
        agent = await db.scalar(
            select(Agent).where(Agent.id == run.agent_id, Agent.workspace_id == workspace_id)
        )
        if agent is None:
            raise _RunError("the agent was deleted")
        if agent.config_version != run.config_version:
            raise _RunError(
                f"the agent changed after the run was queued (version {run.config_version} → "
                f"{agent.config_version}); run the tests again"
            )
        try:
            config = AgentConfig.model_validate(agent.config)
        except ValidationError as exc:
            raise _RunError(f"the agent's configuration is invalid: {exc.errors()[0]['msg']}") from exc
        persona_config = config.model_copy(update={"qa": config.qa.model_copy(update={"model": None})})
        persona_res, reason = await resolve_judge(
            db, ctx.vault, http, persona_config, workspace_id=workspace_id
        )
        if persona_res is None:
            raise _RunError(
                f"no model can play the caller: {reason}. Set the agent's workflow model "
                "(pipeline.workflow_llm) to an OpenAI-compatible provider with a key, such as OpenRouter"
            )
        judge_res, reason = await resolve_judge(db, ctx.vault, http, config, workspace_id=workspace_id)
        if judge_res is None:
            raise _RunError(
                f"no model can judge the conversations: {reason}. Set the QA model (qa.model) or the "
                "workflow model to an OpenAI-compatible provider with a key, such as OpenRouter"
            )
        await _preflight_worker(db, agent)
        run.summary = {
            **dict(run.summary or {}),
            "persona_model": persona_res.model_label,
            "judge_model": judge_res.model_label,
        }
        results = list(
            (
                await db.execute(
                    select(AgentTestResult)
                    .where(AgentTestResult.run_id == run.id, AgentTestResult.workspace_id == workspace_id)
                    .order_by(AgentTestResult.ordinal)
                )
            )
            .scalars()
            .all()
        )
        plan = [(result.id, result.case_id) for result in results]
    log.info("agent_tests_started", run_id=run_id, agent_id=agent.id, cases=len(plan))

    cases = {case.id: case for case in config.tests}
    statuses: list[AgentTestCaseStatus] = []
    for result_id, case_id in plan:
        case = cases.get(case_id)
        started = utcnow()
        if case is None:
            verdict = AgentTestVerdict(
                case_id=case_id,
                case_name=case_id,
                status="error",
                error="the case is no longer in the agent's configuration",
                started_at=started,
                finished_at=started,
            )
        else:
            verdict = await _play_and_judge(
                ctx, agent.id, workspace_id, result_id, case, persona_res.llm, judge_res.llm
            )
        await _store_verdict(ctx, result_id, workspace_id, verdict)
        statuses.append(verdict.status)
    counts = {name: statuses.count(name) for name in ("passed", "failed", "inconclusive", "error")}
    status: AgentTestRunStatus
    if counts["failed"]:
        status = "failed"
    elif counts["error"]:
        status = "error"
    elif counts["inconclusive"]:
        status = "inconclusive"
    else:
        status = "passed"
    error = f"{counts['error']} of {len(statuses)} cases could not run" if counts["error"] else None
    await _finish_run(
        ctx,
        run_id,
        workspace_id,
        status=status,
        error=error,
        totals={
            "passed": counts["passed"],
            "failed": counts["failed"],
            "inconclusive": counts["inconclusive"],
            "errored": counts["error"],
            "pass_ratio": counts["passed"] / len(statuses) if statuses else 0.0,
        },
    )
    log.info("agent_tests_finished", run_id=run_id, status=status, **counts)


async def _preflight_worker(db: AsyncSession, agent: Agent) -> None:
    try:
        connection = await resolve_agent_connection(db, agent)
    except ApiError as exc:
        raise _RunError(exc.message) from exc
    ready = await db.scalar(
        select(WorkerInstance.id)
        .where(WorkerInstance.connection_id == connection.id, WorkerInstance.status == "ready")
        .limit(1)
    )
    if ready is None:
        raise _RunError(
            f"no ready worker is registered for the agent's LiveKit connection '{connection.name}', so no "
            "agent would answer; start one (Connections → Workers) and run the tests again"
        )


async def _play_and_judge(
    ctx: JobContext,
    agent_id: str,
    workspace_id: str,
    result_id: str,
    case: AgentTest,
    persona_llm: JudgeLLM,
    judge_llm: JudgeLLM,
) -> AgentTestVerdict:
    started = utcnow()
    transcript: list[AgentTestTurn] = []
    session_id: str | None = None
    try:
        session_id, server_url, token = await _mint_session(ctx, agent_id, workspace_id, result_id, case)
        stopped_by, turns = await _converse(case, server_url, token, persona_llm, transcript)
    except _RunError:
        raise
    except _CaseError as exc:
        return AgentTestVerdict(
            case_id=case.id,
            case_name=case.name,
            status="error",
            session_id=session_id,
            transcript=transcript,
            error=str(exc)[:_MAX_ERROR],
            started_at=started,
            finished_at=utcnow(),
        )
    tool_calls = await _tool_calls(ctx, session_id, case)
    scores = await judges.judge_case(judge_llm, case, transcript, tool_calls)
    return AgentTestVerdict(
        case_id=case.id,
        case_name=case.name,
        status=_case_status(scores),
        scores=scores,
        turns=turns,
        stopped_by=stopped_by,
        session_id=session_id,
        transcript=transcript,
        tool_calls=tool_calls,
        started_at=started,
        finished_at=utcnow(),
    )


def _case_status(scores: Sequence[AgentTestJudgeScore]) -> AgentTestCaseStatus:
    verdicts = {score.verdict for score in scores}
    if "fail" in verdicts:
        return "failed"
    if "inconclusive" in verdicts:
        return "inconclusive"
    return "passed"


async def _mint_session(
    ctx: JobContext, agent_id: str, workspace_id: str, result_id: str, case: AgentTest
) -> tuple[str, str, str]:
    """Mint the case's scratch text session and bind it to the result row (committed before joining)."""
    async with ctx.database.session() as db:
        agent = await db.scalar(select(Agent).where(Agent.id == agent_id, Agent.workspace_id == workspace_id))
        if agent is None:
            raise _RunError("the agent was deleted")
        limits = AgentLimits.model_validate(agent.limits or {})
        session_id = new_id()
        room_name = room_name_for(session_id)
        identity = f"agent-test-{secrets.token_hex(4)}"
        participant_name = f"Agent test: {case.name}"[:200]
        try:
            async with reserve_session_slot(db, agent, limits):
                minted = await mint_session_token(
                    db,
                    get_client_factory(ctx.vault),
                    agent,
                    session_id=session_id,
                    room_name=room_name,
                    identity=identity,
                    participant_name=participant_name,
                    channel="text",
                    ttl=dt.timedelta(seconds=limits.max_session_duration_s),
                )
                config = AgentConfig.model_validate(agent.config)
                db.add(
                    SessionRow(
                        id=session_id,
                        workspace_id=workspace_id,
                        agent_id=agent.id,
                        connection_id=minted.connection_id,
                        config_version=agent.config_version,
                        room_name=room_name,
                        participant_identity=identity,
                        participant_name=participant_name,
                        status="created",
                        pipeline_mode=config.pipeline.mode,
                        channel="text",
                    )
                )
                await db.flush()
                result = await db.scalar(
                    select(AgentTestResult).where(
                        AgentTestResult.id == result_id, AgentTestResult.workspace_id == workspace_id
                    )
                )
                if result is not None:
                    result.session_id = session_id
                    result.status = "running"
                await db.commit()
        except AgentBusyError as exc:
            raise _CaseError(
                "the agent is at its concurrent session limit; run the tests when it is less busy"
            ) from exc
        except ApiError as exc:
            raise _CaseError(exc.message) from exc
    log.info("agent_test_session_created", session_id=session_id, agent_id=agent_id, case_id=case.id)
    return session_id, minted.server_url, minted.participant_token


async def _converse(
    case: AgentTest, server_url: str, token: str, persona_llm: JudgeLLM, transcript: list[AgentTestTurn]
) -> tuple[AgentTestStopReason, int]:
    """Join the room and play the persona; ``transcript`` is filled in place."""
    try:
        transport = transport_factory()
    except TransportUnavailableError as exc:
        raise _RunError(f"the api cannot join LiveKit rooms: {exc}") from exc
    replies: asyncio.Queue[Any] = asyncio.Queue()
    pump: asyncio.Task[None] | None = None
    try:
        try:
            await transport.connect(server_url, token)
        except (ConnectionError, OSError) as exc:
            raise _CaseError(f"could not join the session's room: {exc}") from exc
        try:
            agent_identity = await transport.wait_for_agent(LIMITS.agent_join_timeout_s)
        except TimeoutError as exc:
            raise _CaseError(
                f"the agent did not join the session within {LIMITS.agent_join_timeout_s:g}s "
                "(is a worker running for this agent's connection?)"
            ) from exc
        pump = asyncio.ensure_future(_pump(transport, agent_identity, replies))

        greeting, state = await _collect(replies, LIMITS.greeting_timeout_s)
        transcript.extend(AgentTestTurn(role="assistant", text=text) for text in greeting)
        if state == "disconnected":
            return "disconnected", 0

        turns = 0
        while turns < case.max_turns:
            try:
                turn = await persona.next_persona_turn(persona_llm, case, transcript)
            except persona.PersonaError as exc:
                raise _CaseError(str(exc)) from exc
            if turn.message:
                await transport.send_text(turn.message)
                turns += 1
                transcript.append(AgentTestTurn(role="user", text=turn.message))
                answer, state = await _collect(replies, LIMITS.reply_timeout_s)
                transcript.extend(AgentTestTurn(role="assistant", text=text) for text in answer)
                if state == "disconnected":
                    return "disconnected", turns
                if state == "timeout":
                    return "agent_timeout", turns
            if turn.done:
                return "persona_done", turns
        return "max_turns", turns
    finally:
        if pump is not None:
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await pump
        with contextlib.suppress(Exception):
            await transport.close()


async def _pump(transport: RoomTransport, agent_identity: str, queue: asyncio.Queue[Any]) -> None:
    try:
        async for reply in transport.replies():
            if reply.participant == agent_identity and reply.final and reply.text.strip():
                queue.put_nowait(reply.text)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - a transport failure is a disconnect
        log.debug("agent_test_reply_pump_failed", exc_info=True)
    queue.put_nowait(_DISCONNECTED)


async def _collect(queue: asyncio.Queue[Any], timeout_s: float) -> tuple[list[str], str]:
    """Wait for a final reply, then keep collecting while more arrive within ``settle_s``."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    texts: list[str] = []
    while True:
        remaining = deadline - loop.time()
        wait = min(remaining, LIMITS.settle_s) if texts else remaining
        if wait <= 0:
            break
        try:
            item = await asyncio.wait_for(queue.get(), wait)
        except TimeoutError:
            break
        if item is _DISCONNECTED:
            queue.put_nowait(_DISCONNECTED)
            return texts, "disconnected" if not texts else "final"
        texts.append(str(item))
    return texts, "final" if texts else "timeout"


async def _tool_calls(ctx: JobContext, session_id: str | None, case: AgentTest) -> list[AgentTestToolCall]:
    """The worker's tool calls of the session, waiting briefly for its last event flush."""
    if session_id is None:
        return []
    loop = asyncio.get_running_loop()
    deadline = loop.time() + LIMITS.events_wait_s
    rows: list[SessionEvent] = []
    while True:
        async with ctx.database.session() as db:
            rows = list(
                (
                    await db.execute(
                        select(SessionEvent)
                        .where(
                            SessionEvent.session_id == session_id,
                            SessionEvent.type.in_(("tool_call_started", "tool_call_ended", "session_ended")),
                        )
                        .order_by(SessionEvent.id)
                    )
                )
                .scalars()
                .all()
            )
        if any(row.type == "session_ended" for row in rows) or loop.time() >= deadline:
            break
        await asyncio.sleep(LIMITS.events_poll_s)
    return _calls_from_events(rows, case)


def _calls_from_events(rows: Sequence[SessionEvent], case: AgentTest) -> list[AgentTestToolCall]:
    calls: dict[str, AgentTestToolCall] = {}
    order: list[str] = []
    for row in rows:
        payload = row.payload if isinstance(row.payload, dict) else {}
        call_id = str(payload.get("call_id") or f"event-{row.id}")
        tool = str(payload.get("tool") or "")
        if row.type == "tool_call_started":
            args = payload.get("args_redacted")
            calls[call_id] = AgentTestToolCall(
                tool=tool,
                status="started",
                arguments=_text(args),
                mocked=tool in case.mocks,
            )
            order.append(call_id)
        elif row.type == "tool_call_ended":
            existing = calls.get(call_id)
            if existing is None:
                existing = AgentTestToolCall(tool=tool, mocked=tool in case.mocks)
                calls[call_id] = existing
                order.append(call_id)
            calls[call_id] = existing.model_copy(
                update={
                    "tool": existing.tool or tool,
                    "status": str(payload.get("status") or "ended"),
                    "result_preview": _text(payload.get("result_preview")),
                }
            )
    return [calls[call_id] for call_id in order]


def _text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value[:2000]
    return json.dumps(value, ensure_ascii=False, default=str)[:2000]


async def _store_verdict(
    ctx: JobContext, result_id: str, workspace_id: str, verdict: AgentTestVerdict
) -> None:
    async with ctx.database.session() as db:
        result = await db.scalar(
            select(AgentTestResult).where(
                AgentTestResult.id == result_id, AgentTestResult.workspace_id == workspace_id
            )
        )
        if result is None:
            return
        result.status = verdict.status
        result.verdict = verdict.model_dump(mode="json")
        result.finished_at = verdict.finished_at or utcnow()
        if verdict.session_id:
            result.session_id = verdict.session_id


async def _finish_run(
    ctx: JobContext,
    run_id: str,
    workspace_id: str,
    *,
    status: AgentTestRunStatus,
    error: str | None,
    totals: dict[str, Any] | None = None,
) -> None:
    async with ctx.database.session() as db:
        run = await db.scalar(select(RunRow).where(RunRow.id == run_id, RunRow.workspace_id == workspace_id))
        if run is None:
            return
        run.status = status
        run.error = error
        run.started_at = run.started_at or utcnow()
        run.finished_at = utcnow()
        run.summary = {**dict(run.summary or {}), **(totals or {})}
        if status == "error" and totals is None:
            # The run did not play: every case still pending is marked as not run.
            pending = (
                await db.execute(
                    select(AgentTestResult).where(
                        AgentTestResult.run_id == run_id,
                        AgentTestResult.workspace_id == workspace_id,
                        AgentTestResult.status.in_(("pending", "running")),
                    )
                )
            ).scalars()
            for result in pending:
                result.status = "error"
                result.finished_at = utcnow()
    if status == "error":
        log.warning("agent_tests_not_run", run_id=run_id, error=error)
