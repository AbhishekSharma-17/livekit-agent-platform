"""V6-23 (D-V6-20): the `signature`, `chart`, `timer`, `code` and `cart` blocks, their strict
configs and states, and their tools."""

from __future__ import annotations

import math
from typing import Any, get_args

import pytest
from pydantic import BaseModel, ValidationError

from lkap_contracts import tools
from lkap_contracts.blocks import (
    BLOCK_CONFIG_MODELS,
    CartBlockConfig,
    ChartBlockConfig,
    CodeBlockConfig,
    SignatureBlockConfig,
    TimerBlockConfig,
    validate_block_config,
)
from lkap_contracts.compliance import consent_text_hash
from lkap_contracts.export import EXPORTED_MODELS
from lkap_contracts.ui_protocol import (
    MAX_CART_LINES,
    MAX_CHART_POINTS,
    MAX_CHART_SERIES,
    MAX_CODE_CHARS,
    MAX_TIMER_SECONDS,
    BlockSpec,
    BlockType,
    CartAdjustment,
    CartBlockState,
    CartLine,
    ChartBlockState,
    CodeBlockState,
    RequestableState,
    SignatureBlockState,
    SignatureEvent,
    TimerBlockState,
    cart_totals,
)

NEXT_BLOCKS = ("signature", "chart", "timer", "code", "cart")
NEXT_TOOLS = ("request_signature", "show_chart", "start_timer", "show_code", "cart_set")


def _spec(block_type: str, config: dict[str, Any] | None = None) -> BlockSpec:
    return BlockSpec(id="b", type=block_type, config=config or {})  # type: ignore[arg-type]


def _points(n: int, **extra: Any) -> list[dict[str, Any]]:
    return [{"label": f"p{i}", "value": i, **extra} for i in range(n)]


# --------------------------------------------------------------------- the blocks and their tools


def test_the_five_blocks_are_appended_with_strict_configs() -> None:
    assert get_args(BlockType)[-5:] == NEXT_BLOCKS
    expected: dict[str, type[BaseModel]] = {
        "signature": SignatureBlockConfig,
        "chart": ChartBlockConfig,
        "timer": TimerBlockConfig,
        "code": CodeBlockConfig,
        "cart": CartBlockConfig,
    }
    for block_type, model in expected.items():
        assert BLOCK_CONFIG_MODELS[block_type] is model  # type: ignore[index]
        issues = validate_block_config(_spec(block_type, {"url": "https://x.example"}))
        assert [(i.path, i.message) for i in issues] == [
            ("config.url", f"unknown config key for a {block_type} block")
        ]


def test_config_defaults_and_bounds() -> None:
    assert SignatureBlockConfig().model_dump() == {"disclosure_text": "", "allow_decline": True}
    assert ChartBlockConfig().model_dump() == {"kind": "bar", "show_table": False}
    assert TimerBlockConfig().model_dump() == {"mode": "countdown", "max_seconds": 3600}
    assert CodeBlockConfig().model_dump() == {"max_chars": 8000, "wrap": False}
    assert CartBlockConfig().model_dump() == {"currency": "USD", "max_lines": 20}
    bad: list[tuple[type[BaseModel], dict[str, Any]]] = [
        (SignatureBlockConfig, {"disclosure_text": "x" * 2001}),
        (ChartBlockConfig, {"kind": "scatter"}),
        (TimerBlockConfig, {"max_seconds": MAX_TIMER_SECONDS + 1}),
        (TimerBlockConfig, {"mode": "alarm"}),
        (CodeBlockConfig, {"max_chars": MAX_CODE_CHARS + 1}),
        (CartBlockConfig, {"currency": "usd"}),
        (CartBlockConfig, {"currency": "DOLLARS"}),
        (CartBlockConfig, {"max_lines": MAX_CART_LINES + 1}),
    ]
    for model, config in bad:
        with pytest.raises(ValidationError):
            model.model_validate(config)


def test_config_keys_that_seed_a_state_field_share_its_name() -> None:
    """`initial_block_state` copies a config key into the state field of the same name."""
    assert "disclosure_text" in SignatureBlockState.model_fields
    assert "kind" in ChartBlockState.model_fields
    assert "mode" in TimerBlockState.model_fields
    assert "currency" in CartBlockState.model_fields


def test_the_five_tools_are_blocking_block_tools() -> None:
    assert tools.BLOCK_TOOL_NAMES[-5:] == NEXT_TOOLS
    for name, block_type in zip(NEXT_TOOLS, NEXT_BLOCKS, strict=True):
        assert name not in tools.BUILTIN_TOOL_NAMES
        assert tools.BLOCK_TOOL_TYPES[name] == {block_type}
        assert tools.never_background(name)
    assert {"show_chart", "start_timer", "show_code", "cart_set"} <= tools.WRITE_BUILTINS
    assert "request_signature" not in tools.WRITE_BUILTINS


def test_update_block_may_write_chart_code_and_cart_but_not_signature_or_timer() -> None:
    assert {"chart", "code", "cart"} <= tools.UPDATABLE_BLOCK_TYPES
    assert not {"signature", "timer"} & tools.UPDATABLE_BLOCK_TYPES
    assert tools.builtin_tools_document()["block_tool_types"]["update_block"] == sorted(
        tools.UPDATABLE_BLOCK_TYPES
    )


def test_the_states_and_the_signature_event_are_exported() -> None:
    for name in (
        "SignatureBlockState",
        "ChartBlockState",
        "TimerBlockState",
        "CodeBlockState",
        "CartBlockState",
        "SignatureEvent",
    ):
        assert name in EXPORTED_MODELS


# --------------------------------------------------------------------- signature


def test_a_signature_is_a_requestable_block_whose_answer_is_hashed_wording() -> None:
    assert issubclass(SignatureBlockState, RequestableState)
    text = "I agree to the repair estimate of 1,200 USD."
    state = SignatureBlockState(
        status="submitted",
        disclosure_text=text,
        signed=True,
        asset_id="a1",
        text_hash=consent_text_hash(text),
    )
    assert state.text_hash == consent_text_hash(text)
    with pytest.raises(ValidationError):
        SignatureBlockState(text_hash="not-a-hash")
    with pytest.raises(ValidationError):
        SignatureBlockState(disclosure_text="x" * 2001)
    with pytest.raises(ValidationError):
        SignatureBlockState(asset_id="a" * 129)


def test_the_signature_event_carries_the_hash_and_never_the_picture() -> None:
    event = SignatureEvent(block_id="sign", signed=True, text_hash=consent_text_hash("ok"), asset_id="a1")
    assert event.model_dump() == {
        "block_id": "sign",
        "signed": True,
        "text_hash": consent_text_hash("ok"),
        "asset_id": "a1",
        "method": "drawn",
    }
    with pytest.raises(ValidationError):
        SignatureEvent(block_id="sign", signed=True, text_hash="abc")


# --------------------------------------------------------------------- chart


def test_a_chart_with_200_points_is_accepted() -> None:
    assert len(ChartBlockState(kind="line", points=_points(MAX_CHART_POINTS)).points) == 200  # type: ignore[arg-type]


def test_a_chart_with_201_points_is_refused() -> None:
    with pytest.raises(ValidationError, match="at most 200 items"):
        ChartBlockState(kind="bar", points=_points(MAX_CHART_POINTS + 1))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("state", "message"),
    [
        ({"kind": "number", "points": _points(2)}, "one value"),
        ({"kind": "gauge", "points": _points(2)}, "one value"),
        ({"kind": "gauge", "points": _points(1), "gauge_min": 10, "gauge_max": 10}, "gauge_min"),
        ({"kind": "pie", "points": [{"label": "a", "value": -1}]}, "0 or more"),
        ({"kind": "pie", "points": _points(2, series="s")}, "no series"),
        (
            {"kind": "line", "points": [{"label": "x", "value": 1, "series": f"s{i}"} for i in range(9)]},
            f"at most {MAX_CHART_SERIES} series",
        ),
        ({"kind": "bar", "points": [{"label": "x" * 41, "value": 1}]}, "at most 40"),
        ({"kind": "bar", "points": [{"label": "", "value": 1}]}, "at least 1"),
        ({"kind": "bar", "points": [{"label": "x", "value": 1e13}]}, "less than or equal"),
        ({"kind": "bar", "title": "t" * 121}, "at most 120"),
        ({"kind": "bar", "unit": "u" * 17}, "at most 16"),
        ({"kind": "bar", "caption": "c" * 201}, "at most 200"),
    ],
)
def test_a_chart_that_does_not_fit_its_kind_is_refused(state: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        ChartBlockState.model_validate(state)


@pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan])
def test_a_chart_value_must_be_finite(value: float) -> None:
    with pytest.raises(ValidationError):
        ChartBlockState(points=[{"label": "x", "value": value}])  # type: ignore[list-item]


def test_every_kind_starts_empty_and_a_gauge_has_a_default_scale() -> None:
    for kind in ("number", "bar", "line", "pie", "gauge"):
        assert ChartBlockState(kind=kind).points == []  # type: ignore[arg-type]
    gauge = ChartBlockState(kind="gauge", points=[{"label": "Done", "value": 72}])  # type: ignore[list-item]
    assert (gauge.gauge_min, gauge.gauge_max) == (0, 100)


# --------------------------------------------------------------------- timer


def test_a_running_timer_has_its_times() -> None:
    TimerBlockState(status="running", duration_s=60, started_at=1.0, ends_at=61.0)
    with pytest.raises(ValidationError, match="running timer"):
        TimerBlockState(status="running", duration_s=60)
    with pytest.raises(ValidationError):
        TimerBlockState(duration_s=MAX_TIMER_SECONDS + 1)
    with pytest.raises(ValidationError):
        TimerBlockState(duration_s=0)
    with pytest.raises(ValidationError):
        TimerBlockState(label="l" * 81)


# --------------------------------------------------------------------- code


@pytest.mark.parametrize("language", ["python", "c++", "c#", "json", "objective-c", "shell", "f90"])
def test_a_code_language_label_is_plain(language: str) -> None:
    assert CodeBlockState(code="x", language=language).language == language


@pytest.mark.parametrize("language", ["Python", "<script>", "a b", "", "x" * 25, "java\nscript"])
def test_a_code_language_label_that_is_not_plain_is_refused(language: str) -> None:
    with pytest.raises(ValidationError):
        CodeBlockState(code="x", language=language)


def test_code_is_capped() -> None:
    CodeBlockState(code="x" * MAX_CODE_CHARS)
    with pytest.raises(ValidationError):
        CodeBlockState(code="x" * (MAX_CODE_CHARS + 1))


# --------------------------------------------------------------------- cart


def _cart(lines: list[dict[str, Any]], adjustments: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    parsed = [CartLine.model_validate(line) for line in lines]
    extra = [CartAdjustment.model_validate(a) for a in adjustments or []]
    line_totals, subtotal, total = cart_totals(parsed, extra)
    return {
        "lines": [{**line, "line_total": t} for line, t in zip(lines, line_totals, strict=True)],
        "adjustments": adjustments or [],
        "subtotal": subtotal,
        "total": total,
    }


def test_cart_totals_add_up_to_the_cent() -> None:
    lines = [
        CartLine(id="a", name="Filter", quantity=3, unit_price=0.1),
        CartLine(id="b", name="Pad", unit_price=19.99),
    ]
    line_totals, subtotal, total = cart_totals(lines, [CartAdjustment(label="Discount", amount=-5)])
    assert line_totals == [0.3, 19.99]
    assert (subtotal, total) == (20.29, 15.29)


def test_a_consistent_cart_validates_and_an_inconsistent_one_is_refused() -> None:
    state = CartBlockState.model_validate(
        _cart(
            [{"id": "a", "name": "Filter", "quantity": 2, "unit_price": 4.5}],
            [{"label": "Tax", "amount": 0.72}],
        )
    )
    assert (state.subtotal, state.total) == (9.0, 9.72)
    forged = _cart([{"id": "a", "name": "Filter", "quantity": 2, "unit_price": 4.5}])
    with pytest.raises(ValidationError, match="total must be"):
        CartBlockState.model_validate({**forged, "total": 0})
    with pytest.raises(ValidationError, match="subtotal must be"):
        CartBlockState.model_validate({**forged, "subtotal": 1})
    wrong_line = {**forged, "lines": [{**forged["lines"][0], "line_total": 1}]}
    with pytest.raises(ValidationError, match="line_total"):
        CartBlockState.model_validate(wrong_line)


@pytest.mark.parametrize(
    "line",
    [
        {"id": "a", "name": "", "unit_price": 1},
        {"id": "a b", "name": "x", "unit_price": 1},
        {"id": "a", "name": "x", "unit_price": -1},
        {"id": "a", "name": "x", "unit_price": 1, "quantity": 0},
        {"id": "a", "name": "x", "unit_price": 1, "quantity": 10_000},
        {"id": "a", "name": "x" * 121, "unit_price": 1},
        {"id": "a", "name": "x", "unit_price": math.inf},
    ],
)
def test_a_cart_line_outside_its_bounds_is_refused(line: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        CartLine.model_validate(line)


def test_a_cart_holds_at_most_fifty_unique_lines_and_five_adjustments() -> None:
    lines = [{"id": f"l{i}", "name": "x", "unit_price": 1} for i in range(MAX_CART_LINES + 1)]
    with pytest.raises(ValidationError):
        CartBlockState.model_validate(_cart(lines))
    with pytest.raises(ValidationError, match="line ids must be unique"):
        CartBlockState.model_validate(_cart([{"id": "a", "name": "x", "unit_price": 1}] * 2))
    adjustments = [{"label": f"a{i}", "amount": 1} for i in range(6)]
    with pytest.raises(ValidationError):
        CartBlockState.model_validate(_cart([], adjustments))
    with pytest.raises(ValidationError):
        CartBlockState.model_validate({"currency": "euro"})
