"""`calculate` (V5-25): the fixture set, and refusal of everything outside the grammar."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import cast

import pytest
from fakes.fake_ctx import FakePackSessionContext
from livekit.agents import RunContext, ToolError
from lkap_contracts.tools import NEVER_BACKGROUND_TOOLS

from lkap_agent.tools.builtin.calculate import CalculationError, build_calculate_tool, evaluate


@dataclass
class _Call:
    call_id: str = "call-1"


@dataclass
class _Run:
    function_call: _Call = field(default_factory=_Call)


def _run() -> RunContext:
    return cast(RunContext, _Run())


@pytest.mark.parametrize(
    ("expression", "result"),
    [
        ("2 + 2", "4"),
        ("1200 * 15%", "180"),
        ("15% of 200", "30"),
        ("(450 + 120) / 12", "47.5"),
        ("0.1 + 0.2", "0.3"),
        ("17 % 5", "2"),
        ("1,250.50 * 3", "3751.5"),
        ("3 x 4", "12"),
        ("10 ÷ 4", "2.5"),
        ("7 × 6", "42"),
        ("10 − 3", "7"),
        ("round(1/3, 4)", "0.3333"),
        ("round(2.5)", "3"),
        ("round(99.995, 2)", "100"),
        ("-(3 - 5) * 2", "4"),
        ("1000 / 12", "83.3333333333"),
        ("12.5% * 80", "10"),
    ],
)
def test_evaluate_the_fixture_set(expression: str, result: str) -> None:
    assert evaluate(expression)[1] == result


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('ls')",
        "os",
        "x + 1",
        "(1).real",
        "a.b",
        "abs(-1)",
        "print(1)",
        "round(1, 2, 3)",
        "round(x=1)",
        "2 ** 10",
        "2 // 3",
        "1 < 2",
        "[1, 2]",
        "'text'",
        "True + 1",
        "lambda: 1",
        "1 if 1 else 2",
        "{1: 2}",
        "1; 2",
    ],
)
def test_names_attributes_calls_and_other_syntax_are_refused(expression: str) -> None:
    with pytest.raises(CalculationError):
        evaluate(expression)


@pytest.mark.parametrize(
    ("expression", "message"),
    [
        ("1 / 0", "divide by zero"),
        ("5 % 0", "divide by zero"),
        ("9" * 20, "too large"),
        ("999999999 * 999999999", "too large"),
        ("round(2, 1.5)", "whole number"),
        ("round(2, 11)", "decimal places"),
        ("", "nothing"),
        ("1 +" + " 1 +" * 80 + " 1", "longer than"),
        ("(1 + ", "could not be read"),
    ],
)
def test_bad_input_gets_a_sayable_reason(expression: str, message: str) -> None:
    with pytest.raises(CalculationError, match=message):
        evaluate(expression)


async def test_the_tool_returns_the_result_as_json() -> None:
    tool = build_calculate_tool(FakePackSessionContext())

    result = json.loads(await tool(context=_run(), expression="1200 * 15%"))

    assert result == {"expression": "1200 * (15/100)", "result": "180"}


async def test_the_tool_turns_a_refusal_into_a_tool_error() -> None:
    tool = build_calculate_tool(FakePackSessionContext())

    with pytest.raises(ToolError, match="Could not calculate that"):
        await tool(context=_run(), expression="__import__('os')")


def test_calculate_never_runs_in_the_background() -> None:
    assert "calculate" in NEVER_BACKGROUND_TOOLS


def test_the_module_never_calls_eval() -> None:
    import inspect

    from lkap_agent.tools.builtin import calculate as module

    source = inspect.getsource(module)
    assert "eval(" not in source.replace("_eval(", "").replace("evaluate(", "")
    assert "exec(" not in source
