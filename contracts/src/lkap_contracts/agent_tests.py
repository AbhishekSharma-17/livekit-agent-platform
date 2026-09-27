"""Text simulations, judges and the pre-publish gate (V5-29, D-V5-29).

An agent's test cases live in its configuration (``AgentConfig.tests``), so they
version with it: a run is pinned to the ``config_version`` it was started on. The
api plays each case's persona against the agent over a scratch text session
(the same transport as the console's Test chat and the lkap-mcp ``chat_*``
tools), then five LLM judges score the transcript: task completion, tool use,
safety, relevancy and accuracy against the case's ``expectations``.

``AgentConfig.publish_gate`` is **opt-in** (``require_tests=False`` by default):
when on, publishing refuses with ``422 tests_failing`` unless the latest run on
the version being published passed at least ``min_pass_ratio`` of its cases. A
run that could not run (no worker, no persona model) blocks publishing too, and
the refusal says so (``reason="error"``, never "failing").

Tool mocks (``AgentTest.mocks``) reach the worker as
``ResolvedAgentConfig.tool_mocks`` for that case's session only: a mocked HTTP
tool or connected-app action returns the fixture instead of calling out.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Final, Literal

from pydantic import BaseModel, Field, field_validator

from lkap_contracts.tools import TOOL_NAME_PATTERN

__all__ = [
    "AGENT_TEST_ID_PATTERN",
    "AGENT_TEST_JUDGES",
    "MAX_AGENT_TESTS",
    "MAX_AGENT_TEST_TURNS",
    "AgentTest",
    "AgentTestCaseStatus",
    "AgentTestJudgeName",
    "AgentTestJudgeScore",
    "AgentTestRun",
    "AgentTestRunIn",
    "AgentTestRunPage",
    "AgentTestRunStatus",
    "AgentTestStopReason",
    "AgentTestToolCall",
    "AgentTestTurn",
    "AgentTestVerdict",
    "JudgeVerdict",
    "PublishGate",
    "PublishGateReason",
    "PublishGateRefusal",
]

#: A case id: short, stable, URL-safe (the console generates one; MCP callers may name it).
AGENT_TEST_ID_PATTERN: Final = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"

#: At most this many cases per agent.
MAX_AGENT_TESTS: Final = 50

#: The ceiling of ``AgentTest.max_turns``.
MAX_AGENT_TEST_TURNS: Final = 40

AgentTestJudgeName = Literal["task_completion", "tool_use", "safety", "relevancy", "accuracy"]

#: The five judges, in the order the verdicts list them.
AGENT_TEST_JUDGES: Final[tuple[AgentTestJudgeName, ...]] = (
    "task_completion",
    "tool_use",
    "safety",
    "relevancy",
    "accuracy",
)

#: One judge's answer. ``inconclusive``: the judge did not answer with valid JSON twice
#: (the first answer gets one repair retry), or its call failed.
JudgeVerdict = Literal["pass", "fail", "inconclusive"]

#: A case's outcome. ``passed``: every judge passed. ``failed``: at least one judge failed.
#: ``inconclusive``: none failed but at least one could not decide. ``error``: the case did not
#: run (the agent never joined, the persona model failed); it is not a test failure.
AgentTestCaseStatus = Literal["passed", "failed", "inconclusive", "error"]

#: A run's state. ``queued``/``running`` while it works; then ``passed`` (every case passed),
#: ``failed`` (at least one case failed), ``inconclusive`` (none failed, some undecided) or
#: ``error`` (the run could not run: no worker, no persona or judge model, the agent changed).
AgentTestRunStatus = Literal["queued", "running", "passed", "failed", "inconclusive", "error"]

#: Why a simulated conversation stopped. ``persona_done``: the persona said it was finished;
#: ``max_turns``: the case's turn budget ran out; ``agent_timeout``: the agent did not answer
#: in time; ``disconnected``: the room closed; ``error``: something else went wrong.
AgentTestStopReason = Literal["persona_done", "max_turns", "agent_timeout", "disconnected", "error"]

#: ``details.reason`` of a ``422 tests_failing`` publish refusal: ``missing`` (no run on this
#: version), ``running`` (the latest run has not finished), ``failing`` (it passed fewer cases
#: than ``min_pass_ratio``) or ``error`` (it could not run: the tests did not fail, they did
#: not run — ``details.error`` says why).
PublishGateReason = Literal["missing", "running", "failing", "error"]


class AgentTest(BaseModel):
    """One simulated conversation: who the caller is, what they want, what must happen."""

    id: str = Field(pattern=AGENT_TEST_ID_PATTERN, description="Stable case id, unique in the agent.")
    name: str = Field(min_length=1, max_length=120)
    persona_instructions: str = Field(
        min_length=1,
        max_length=4000,
        description="Who the simulated caller is and how they talk (played by an LLM).",
    )
    scenario: str = Field(
        default="",
        max_length=4000,
        description="What the caller wants in this conversation; the persona pursues it.",
    )
    expectations: list[str] = Field(
        default=[],
        max_length=20,
        description="What must be true of the agent's side, one statement each "
        "(the accuracy and task-completion judges check them).",
    )
    mocks: dict[str, Any] = Field(
        default={},
        description="Tool name → the result the tool returns in this case instead of calling out "
        "(HTTP tools and connected-app actions). A string is returned as-is; anything else as JSON.",
    )
    max_turns: int = Field(
        default=12,
        ge=1,
        le=MAX_AGENT_TEST_TURNS,
        description="Caller turns before the conversation is stopped.",
    )

    @field_validator("expectations")
    @classmethod
    def _expectations_bounded(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("an expectation cannot be empty")
        if any(len(item) > 500 for item in cleaned):
            raise ValueError("an expectation is at most 500 characters")
        return cleaned

    @field_validator("mocks")
    @classmethod
    def _mock_names(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > 50:
            raise ValueError("at most 50 mocked tools per case")
        pattern = re.compile(TOOL_NAME_PATTERN)
        bad = sorted(name for name in value if not pattern.match(name))
        if bad:
            raise ValueError(f"not a tool name: {', '.join(bad)}")
        return value


class PublishGate(BaseModel):
    """Whether publishing needs a passing test run on the version being published (opt-in)."""

    require_tests: bool = Field(
        default=False,
        description="Refuse to publish unless the latest test run on this version passed.",
    )
    min_pass_ratio: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="The share of cases that must pass (1.0 = every case).",
    )


class AgentTestJudgeScore(BaseModel):
    """One judge's verdict on one case."""

    judge: AgentTestJudgeName
    verdict: JudgeVerdict
    score: float | None = Field(default=None, ge=0.0, le=1.0, description="0 (worst) to 1 (best).")
    reason: str = ""


class AgentTestTurn(BaseModel):
    """One line of a simulated conversation (``user`` is the persona)."""

    role: Literal["user", "assistant"]
    text: str


class AgentTestToolCall(BaseModel):
    """A tool the agent called during the case (from the session's events)."""

    tool: str
    status: str | None = None
    arguments: str | None = Field(default=None, description="The call's arguments as the worker logged them.")
    result_preview: str | None = None
    mocked: bool = False


class AgentTestVerdict(BaseModel):
    """One case's outcome within a run."""

    case_id: str
    case_name: str
    status: AgentTestCaseStatus
    scores: list[AgentTestJudgeScore] = []
    turns: int = 0
    stopped_by: AgentTestStopReason | None = None
    session_id: str | None = None
    transcript: list[AgentTestTurn] = []
    tool_calls: list[AgentTestToolCall] = []
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class AgentTestRun(BaseModel):
    """``POST /v1/agents/{id}/tests/run`` and ``GET …/tests/runs[/{run_id}]``."""

    id: str
    agent_id: str
    config_version: int
    status: AgentTestRunStatus
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    case_ids: list[str] = Field(default=[], description="The cases this run plays, in order.")
    passed: int = 0
    failed: int = 0
    inconclusive: int = 0
    errored: int = 0
    pass_ratio: float | None = Field(
        default=None, description="passed / cases, once finished; null while running or on error."
    )
    error: str | None = Field(default=None, description="Why the run could not run (status `error`).")
    persona_model: str | None = None
    judge_model: str | None = None
    verdicts: list[AgentTestVerdict] = Field(
        default=[], description="Per-case outcomes; empty in the runs list, filled on the run itself."
    )


class AgentTestRunIn(BaseModel):
    """``POST /v1/agents/{id}/tests/run``: which cases to play (all of them by default)."""

    case_ids: list[str] | None = Field(default=None, max_length=MAX_AGENT_TESTS)


class AgentTestRunPage(BaseModel):
    """``GET /v1/agents/{id}/tests/runs``: newest first."""

    items: list[AgentTestRun]


class PublishGateRefusal(BaseModel):
    """``details`` of the ``422 tests_failing`` publish refusal."""

    reason: PublishGateReason
    config_version: int
    min_pass_ratio: float
    run_id: str | None = None
    run_status: AgentTestRunStatus | None = None
    pass_ratio: float | None = None
    error: str | None = None
