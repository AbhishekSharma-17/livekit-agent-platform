"""V6-30 (F-1): live extraction gets a realistic budget and lands after the reply.

In the V6 demos run every extraction on Gemini Flash through OpenRouter timed out at the
fixed 2 s budget, so the rules that read extracted values never fired. The call already ran
off the reply path; these tests pin that a slow model still lands its values (the variables,
the panel, the rules and the next turn's instruction) after the reply went out, that the
budget comes from the worker setting, and that a timeout is logged as a warning.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, cast

import pytest
import structlog
from fakes.fake_ctx import FakePackSessionContext, default_agent_config
from livekit.agents import llm as lk_llm
from livekit.agents.voice.events import ConversationItemAddedEvent
from lkap_contracts.agent_config import PanelLayout
from lkap_contracts.extraction import EXTRACTION_BUDGET_S, ExtractionConfig
from lkap_contracts.rules import Rule
from lkap_contracts.ui_protocol import BlockSpec
from pydantic import BaseModel

from lkap_agent.extraction.runner import ExtractionRunner, extraction_budget_s
from lkap_agent.extraction.session import live_structure
from lkap_agent.settings import Settings


class GatedLLM:
    """A `StructuredLLM` that answers once `release` is set (a model slower than the reply)."""

    def __init__(self, answer: dict[str, Any]) -> None:
        self.answer = answer
        self.release = asyncio.Event()
        self.calls = 0

    async def extract(
        self, *, instructions: str, input_text: str, schema: type[BaseModel], timeout_s: float = 45
    ) -> BaseModel:
        self.calls += 1
        await self.release.wait()
        return schema.model_validate(self.answer)


class SleepyLLM:
    """A `StructuredLLM` that takes `delay_s` and ignores `timeout_s`."""

    def __init__(self, answer: dict[str, Any], delay_s: float) -> None:
        self.answer = answer
        self.delay_s = delay_s

    async def extract(
        self, *, instructions: str, input_text: str, schema: type[BaseModel], timeout_s: float = 45
    ) -> BaseModel:
        await asyncio.sleep(self.delay_s)
        return schema.model_validate(self.answer)


class FakeSession:
    """An `AgentSession` stand-in with a history and `on()`."""

    def __init__(self) -> None:
        self.history = lk_llm.ChatContext.empty()
        self.handlers: dict[str, list[Any]] = {}

    def on(self, event: str, handler: Any) -> None:
        self.handlers.setdefault(event, []).append(handler)

    def say(self, role: str, text: str) -> None:
        message = self.history.add_message(role=role, content=text)  # type: ignore[arg-type]
        for handler in self.handlers.get("conversation_item_added", []):
            handler(ConversationItemAddedEvent(item=message))


_PANEL = PanelLayout(blocks=[BlockSpec(id="site", type="details"), BlockSpec(id="todo", type="checklist")])

_EXTRACTION = ExtractionConfig.model_validate(
    {
        "enabled": True,
        "fields": [
            {
                "name": "hazard",
                "type": "enum",
                "options": ["gas", "water", "none"],
                "show_in": "details:site",
            },
        ],
        "min_turn_chars": 5,
    }
)

_GAS_RULE = {
    "id": "gas",
    "when": 'var.hazard == "gas"',
    "then": [
        {"do": "checklist.set_item", "id": "evacuate", "label": "Everyone out of the building"},
        {"do": "instruct", "text": "Tell the caller to leave the building now."},
    ],
}


def _ctx(llm: Any, *, timeout_s: float | None = None) -> tuple[FakePackSessionContext, FakeSession]:
    session = FakeSession()
    rules = [Rule.model_validate(_GAS_RULE)]
    config = default_agent_config(panel=_PANEL, extraction=_EXTRACTION, rules=rules)
    ctx = FakePackSessionContext(config=config, session=cast(Any, session), workflow_llm=cast(Any, llm))
    if timeout_s is not None:
        ctx.extraction_timeout_s = timeout_s  # type: ignore[attr-defined]
    return ctx, session


def _events(ctx: FakePackSessionContext, kind: str) -> list[dict[str, Any]]:
    return [payload for event, payload in ctx.events if event == kind]


def test_extraction_default_budget_fits_a_hosted_model() -> None:
    """A hosted model's first token alone took 1.2-2.4 s in the demos run; 2 s never fit."""
    assert EXTRACTION_BUDGET_S >= 8.0
    ctx, _ = _ctx(SleepyLLM({}, 0))
    assert ExtractionRunner(ctx, _EXTRACTION).budget_s == EXTRACTION_BUDGET_S


def test_extraction_budget_s_reads_the_session_setting() -> None:
    ctx, _ = _ctx(SleepyLLM({}, 0), timeout_s=4.5)
    assert extraction_budget_s(ctx) == 4.5
    assert ExtractionRunner(ctx, _EXTRACTION).budget_s == 4.5
    structure = live_structure(ctx)
    assert structure is not None and structure.runner is not None
    assert structure.runner.budget_s == 4.5


@pytest.mark.parametrize("bad", [None, 0, -1, "ten", float("nan")])
def test_extraction_budget_s_ignores_an_unusable_setting(bad: Any) -> None:
    ctx, _ = _ctx(SleepyLLM({}, 0), timeout_s=bad)
    assert extraction_budget_s(ctx) == EXTRACTION_BUDGET_S


async def test_runner_session_budget_bounds_a_slow_model() -> None:
    ctx, session = _ctx(SleepyLLM({"hazard": "gas"}, delay_s=5), timeout_s=0.05)
    session.history.add_message(role="user", content="I can smell gas in the hall")
    started = time.monotonic()
    run = await ExtractionRunner(ctx, _EXTRACTION).run("turn")
    assert time.monotonic() - started < 1.0
    assert run is not None and run.status == "timeout"


async def test_runner_model_slower_than_old_budget_still_lands() -> None:
    """A model taking longer than the old 2 s would now finish (scaled down: 0.3 s in a 1 s budget)."""
    ctx, session = _ctx(SleepyLLM({"hazard": "gas"}, delay_s=0.3), timeout_s=1.0)
    session.history.add_message(role="user", content="I can smell gas in the hall")
    run = await ExtractionRunner(ctx, _EXTRACTION).run("turn")
    assert run is not None and run.status == "ok" and run.changed == ["hazard"]


async def test_extraction_finishing_after_the_reply_updates_panel_and_fires_rule() -> None:
    llm = GatedLLM({"hazard": "gas"})
    ctx, session = _ctx(llm)
    structure = live_structure(ctx)
    assert structure is not None
    started = time.monotonic()
    session.say("user", "I can smell gas in the hallway")
    # The caller's turn hands off at once: the extraction is still waiting on the model.
    assert time.monotonic() - started < 0.05
    await asyncio.sleep(0)
    assert llm.calls == 1
    assert "hazard" not in ctx.userdata.get("lkap.variables", {})
    # The agent's reply goes out before the extraction has an answer.
    this_turn = lk_llm.ChatContext.empty()
    assert structure.add_instructions(this_turn) == 0
    session.say("assistant", "Thanks, let me note that down.")
    # The model answers late: values, panel, rule and the next turn's note all land.
    llm.release.set()
    await asyncio.wait_for(structure.wait_idle(), timeout=2)
    assert ctx.userdata["lkap.variables"]["hazard"] == "gas"
    assert [(row["key"], row["value"]) for row in ctx.ui.state.blocks["site"]["items"]] == [("hazard", "gas")]
    assert [item.id for item in ctx.ui.state.checklist] == ["evacuate"]
    assert [event["rule_id"] for event in _events(ctx, "rule_fired")] == ["gas"]
    assert _events(ctx, "extraction")[-1]["status"] == "ok"
    next_turn = lk_llm.ChatContext.empty()
    assert structure.add_instructions(next_turn) == 1
    assert "leave the building" in (next_turn.items[0].text_content or "")  # type: ignore[union-attr]


async def test_runner_timeout_logs_a_warning_naming_the_budget() -> None:
    ctx, session = _ctx(SleepyLLM({}, delay_s=5), timeout_s=0.05)
    session.history.add_message(role="user", content="hello there, a long turn")
    with structlog.testing.capture_logs() as logs:
        run = await ExtractionRunner(ctx, _EXTRACTION).run("turn")
    assert run is not None and run.status == "timeout"
    warnings = [entry for entry in logs if entry.get("log_level") == "warning"]
    assert [entry["event"] for entry in warnings] == ["extraction.timeout"]
    assert warnings[0]["budget_s"] == 0.05
    assert "LKAP_EXTRACTION_TIMEOUT_S" in warnings[0]["hint"]


async def test_run_now_waits_on_a_running_extraction_within_the_session_budget() -> None:
    llm = GatedLLM({"hazard": "water"})
    ctx, session = _ctx(llm, timeout_s=0.2)
    structure = live_structure(ctx)
    assert structure is not None
    session.say("user", "water is coming through the ceiling")
    await asyncio.sleep(0)
    started = time.monotonic()
    await structure.run_now()
    # Bounded by the session's 0.2 s budget, not the 10 s default.
    assert time.monotonic() - started < 1.5
    structure.on_close()


def _settings(**overrides: Any) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        LIVEKIT_URL="wss://example.livekit.cloud",  # type: ignore[call-arg]
        LIVEKIT_API_KEY="k",  # type: ignore[call-arg]
        LIVEKIT_API_SECRET="s",  # type: ignore[call-arg]
        service_token="svc",
        api_base_url="http://api.test",
        **overrides,
    )


def test_settings_extraction_timeout_defaults_to_the_contract_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LKAP_EXTRACTION_TIMEOUT_S", raising=False)
    assert _settings().extraction_timeout_s == EXTRACTION_BUDGET_S
    monkeypatch.setenv("LKAP_EXTRACTION_TIMEOUT_S", "6")
    assert _settings().extraction_timeout_s == 6.0


@pytest.mark.parametrize("bad", [0.1, 0, 600])
def test_settings_extraction_timeout_out_of_range_is_refused(bad: float) -> None:
    with pytest.raises(ValueError):
        _settings(extraction_timeout_s=bad)
