"""Unit tests for `lkap_agent.tools.background.BackgroundToolRunner`.

A real `AgentSession` needs a running pipeline to call `generate_reply`/
`current_agent`, so these tests use small fakes: `FakeSession.generate_reply`
mirrors the real (synchronous, non-awaited) signature verified in
livekit-agents==1.8.2 source, and `FakeAgent` uses the real `ChatContext` so
`chat_ctx.copy()` / `add_message()` behave exactly as in production.
"""

from __future__ import annotations

import asyncio
from typing import Any

from livekit.agents import ChatContext
from lkap_contracts.ui_protocol import ActivityEvent

from lkap_agent.tools.background import BackgroundToolRunner


class FakeUi:
    def __init__(self) -> None:
        self.events: list[ActivityEvent] = []

    async def activity(self, event: ActivityEvent) -> None:
        self.events.append(event)


class FakeAgent:
    def __init__(self) -> None:
        self.chat_ctx = ChatContext.empty()

    async def update_chat_ctx(self, chat_ctx: ChatContext) -> None:
        self.chat_ctx = chat_ctx


class FakeSession:
    def __init__(self) -> None:
        self.current_agent = FakeAgent()
        self.generate_reply_calls: list[dict[str, Any]] = []

    def generate_reply(
        self, *, instructions: str | None = None, allow_interruptions: bool | None = None, **_: Any
    ) -> object:
        self.generate_reply_calls.append(
            {"instructions": instructions, "allow_interruptions": allow_interruptions}
        )
        return object()


def _phases(ui: FakeUi, job_id: str) -> list[str]:
    return [e.phase for e in ui.events if e.id == job_id]


async def test_submit_emits_running_then_done_activity() -> None:
    ui, session = FakeUi(), FakeSession()
    runner = BackgroundToolRunner(ui, session)  # type: ignore[arg-type]

    async def work() -> str:
        return "ok"

    job_id = runner.submit(name="lookup_policy", coro=work())
    await asyncio.sleep(0.01)

    assert _phases(ui, job_id) == ["running", "done"]
    assert ui.events[0].source == "lookup_policy"
    assert ui.events[0].label == "Lookup policy"


async def test_routine_result_updates_chat_ctx_with_note_not_generate_reply() -> None:
    ui, session = FakeUi(), FakeSession()
    runner = BackgroundToolRunner(ui, session)  # type: ignore[arg-type]

    async def work() -> str:
        return "no injuries"

    runner.submit(name="sync_claim_packet", coro=work(), routine_note=lambda r: f"Background result: {r}")
    await asyncio.sleep(0.01)

    assert session.generate_reply_calls == []
    messages = session.current_agent.chat_ctx.messages()
    assert messages[-1].role == "assistant"
    assert messages[-1].text_content == "Background result: no injuries"


async def test_urgent_result_calls_generate_reply_and_skips_chat_ctx_update() -> None:
    ui, session = FakeUi(), FakeSession()
    runner = BackgroundToolRunner(ui, session)  # type: ignore[arg-type]

    async def work() -> str:
        return "emergency_escalation"

    runner.submit(
        name="sync_claim_packet",
        coro=work(),
        urgent=lambda r: r == "emergency_escalation",
        urgent_instructions=lambda r: f"Tell the caller: {r} routing applies.",
        routine_note=lambda r: "should not be used",
    )
    await asyncio.sleep(0.01)

    assert session.current_agent.chat_ctx.messages() == []
    assert len(session.generate_reply_calls) == 1
    call = session.generate_reply_calls[0]
    assert call["instructions"] == "Tell the caller: emergency_escalation routing applies."
    assert call["allow_interruptions"] is False
    done_events = [e for e in ui.events if e.phase == "done"]
    assert done_events[0].urgent is True


async def test_on_result_callback_invoked_before_note_delivery() -> None:
    ui, session = FakeUi(), FakeSession()
    runner = BackgroundToolRunner(ui, session)  # type: ignore[arg-type]
    seen: list[str] = []

    async def on_result(result: str) -> None:
        seen.append(result)

    async def work() -> str:
        return "value"

    runner.submit(name="job", coro=work(), on_result=on_result)
    await asyncio.sleep(0.01)

    assert seen == ["value"]


async def test_job_error_emits_error_activity_and_does_not_propagate() -> None:
    ui, session = FakeUi(), FakeSession()
    runner = BackgroundToolRunner(ui, session)  # type: ignore[arg-type]

    async def work() -> str:
        raise ValueError("boom")

    job_id = runner.submit(name="job", coro=work())
    await asyncio.sleep(0.01)

    assert _phases(ui, job_id) == ["running", "error"]
    assert "boom" in ui.events[-1].headline
    assert session.generate_reply_calls == []


async def test_delivery_failure_after_success_emits_error_activity_not_a_crash() -> None:
    """`generate_reply`/`update_chat_ctx` can fail if the session already tore down
    mid-job; that must surface as an `error` activity, not an unhandled task
    exception (the coroutine itself succeeded).
    """
    ui, session = FakeUi(), FakeSession()

    def _boom(**_: Any) -> object:
        raise RuntimeError("AgentSession isn't running")

    session.generate_reply = _boom  # type: ignore[method-assign]
    runner = BackgroundToolRunner(ui, session)  # type: ignore[arg-type]

    async def work() -> str:
        return "emergency_escalation"

    job_id = runner.submit(name="job", coro=work(), urgent=lambda r: True, urgent_instructions=lambda r: "go")
    await asyncio.sleep(0.01)

    assert _phases(ui, job_id) == ["running", "done", "error"]
    assert "AgentSession isn't running" in ui.events[-1].headline


async def test_cancel_marks_job_cancelled_and_stops_result_delivery() -> None:
    ui, session = FakeUi(), FakeSession()
    runner = BackgroundToolRunner(ui, session)  # type: ignore[arg-type]

    async def work() -> str:
        await asyncio.sleep(10)
        return "unreachable"

    job_id = runner.submit(name="job", coro=work(), routine_note=lambda r: "should never run")
    await asyncio.sleep(0)  # let the task actually start awaiting `coro`
    runner.cancel(job_id)
    await asyncio.sleep(0.01)

    assert _phases(ui, job_id) == ["running", "cancelled"]
    assert session.current_agent.chat_ctx.messages() == []


async def test_cancel_of_finished_job_is_a_no_op() -> None:
    ui, session = FakeUi(), FakeSession()
    runner = BackgroundToolRunner(ui, session)  # type: ignore[arg-type]

    async def work() -> str:
        return "done"

    job_id = runner.submit(name="job", coro=work())
    await asyncio.sleep(0.01)
    runner.cancel(job_id)  # must not raise even though the job already finished


async def test_call_id_is_used_as_the_job_id() -> None:
    ui, session = FakeUi(), FakeSession()
    runner = BackgroundToolRunner(ui, session)  # type: ignore[arg-type]

    async def work() -> str:
        return "ok"

    job_id = runner.submit(name="job", coro=work(), call_id="explicit-id")
    assert job_id == "explicit-id"
    await asyncio.sleep(0.01)


async def test_job_completion_is_independent_of_tool_reply_cancellation() -> None:
    """`BackgroundToolRunner` has no coupling to `FunctionToolsExecutedEvent.cancel_tool_reply()`
    (that lives in `PlatformAgent`, W1-AGENT-CORE): a submitted job always runs to
    completion and delivers its result regardless of what happens to the tool call's
    conversational reply.
    """
    ui, session = FakeUi(), FakeSession()
    runner = BackgroundToolRunner(ui, session)  # type: ignore[arg-type]

    async def work() -> str:
        return "value"

    job_id = runner.submit(name="job", coro=work(), routine_note=lambda r: f"got {r}")
    await asyncio.sleep(0.01)

    assert _phases(ui, job_id) == ["running", "done"]
    assert session.current_agent.chat_ctx.messages()[-1].text_content == "got value"


async def test_activity_label_comes_from_the_pack_while_source_stays_the_tool_name() -> None:
    """The notebook keys on `source`; the team feed shows `ToolMeta.activity_label`."""
    ui, session = FakeUi(), FakeSession()
    runner = BackgroundToolRunner(ui, session, labels={"sync_claim_packet": "Claim writer"})  # type: ignore[arg-type]

    async def work() -> str:
        return "ok"

    job_id = runner.submit(name="sync_claim_packet", coro=work())
    other = runner.submit(name="draw_incident_sketch", coro=work())
    await asyncio.sleep(0.01)

    events = [e for e in ui.events if e.id == job_id]
    assert {e.source for e in events} == {"sync_claim_packet"}
    assert {e.label for e in events} == {"Claim writer"}
    assert events[0].headline == "Claim writer started"
    assert {e.label for e in ui.events if e.id == other} == {"Draw incident sketch"}


async def test_every_finished_job_is_recorded_as_a_workflow_run_event() -> None:
    """CONTRACTS §7 `workflow_run` `{name, duration_ms, status}`: done, error and cancelled."""
    ui, session = FakeUi(), FakeSession()
    events: list[tuple[str, dict[str, Any]]] = []
    runner = BackgroundToolRunner(ui, session, record_event=lambda t, p: events.append((t, p)))  # type: ignore[arg-type]

    async def ok() -> str:
        return "ok"

    async def boom() -> str:
        raise RuntimeError("extraction failed")

    async def slow() -> str:
        await asyncio.sleep(10)
        return "never"

    runner.submit(name="sync_claim_packet", coro=ok())
    runner.submit(name="draw_incident_sketch", coro=boom())
    slow_id = runner.submit(name="lookup_policy", coro=slow())
    await asyncio.sleep(0.01)
    runner.cancel(slow_id)
    await asyncio.sleep(0.01)

    runs = {p["name"]: p for t, p in events if t == "workflow_run"}
    assert {name: p["status"] for name, p in runs.items()} == {
        "sync_claim_packet": "done",
        "draw_incident_sketch": "error",
        "lookup_policy": "cancelled",
    }
    assert all(isinstance(p["duration_ms"], int) for p in runs.values())


async def test_cancel_all_cancels_every_running_job_and_skips_finished_ones() -> None:
    """REVIEW-FINAL F-11: a hangup must not leave workflows running into a closed session."""
    ui, session = FakeUi(), FakeSession()
    events: list[tuple[str, dict[str, Any]]] = []
    runner = BackgroundToolRunner(ui, session, record_event=lambda t, p: events.append((t, p)))  # type: ignore[arg-type]

    async def quick() -> str:
        return "ok"

    async def slow() -> str:
        await asyncio.sleep(10)
        return "never"

    done_id = runner.submit(name="lookup_policy", coro=quick())
    await asyncio.sleep(0.01)
    slow_ids = [
        runner.submit(name="sync_claim_packet", coro=slow(), routine_note=lambda r: "never delivered"),
        runner.submit(name="draw_incident_sketch", coro=slow()),
    ]
    await asyncio.sleep(0)

    assert runner.cancel_all() == 2
    await asyncio.sleep(0.01)

    assert _phases(ui, done_id) == ["running", "done"]
    for job_id in slow_ids:
        assert _phases(ui, job_id) == ["running", "cancelled"]
    assert session.current_agent.chat_ctx.messages() == []
    assert sorted(p["status"] for t, p in events if t == "workflow_run") == ["cancelled", "cancelled", "done"]
    assert runner._jobs == {}


async def test_cancel_all_with_no_jobs_is_a_no_op() -> None:
    runner = BackgroundToolRunner(FakeUi(), FakeSession())  # type: ignore[arg-type]

    assert runner.cancel_all() == 0
