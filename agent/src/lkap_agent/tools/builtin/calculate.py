"""`calculate` built-in tool (V5-25): exact arithmetic for quotes, instalments and percentages.

A safe expression evaluator, never ``eval``: the text is parsed with
:mod:`ast` and only numbers, ``+ - * / %`` (and the spoken ``× ÷ −``),
parentheses and ``round(x)`` / ``round(x, digits)`` are accepted. A number
followed by ``%`` that is not a modulo (``15%``, ``15% of 200``) is a
percentage. Names, attributes, any other call, powers, comparisons and
everything else are refused before anything is computed. Arithmetic is
:class:`decimal.Decimal` (``0.1 + 0.2`` is ``0.3``), rounding is half-up.
Instant and never backgrounded (`NEVER_BACKGROUND_TOOLS`).
"""

from __future__ import annotations

import ast
import json
import re
from decimal import ROUND_HALF_UP, Context, Decimal, DivisionByZero, InvalidOperation
from typing import Any, Final

from livekit.agents import FunctionTool, RunContext, ToolError, function_tool
from packs.base import PackSessionContext

__all__ = ["CalculationError", "build_calculate_tool", "evaluate"]

#: Longest expression accepted (characters, after normalisation).
MAX_EXPRESSION_CHARS: Final[int] = 200
#: Largest magnitude of any number or intermediate result.
MAX_MAGNITUDE: Final[Decimal] = Decimal("1e15")
#: Most decimal places `round` may keep.
MAX_ROUND_DIGITS: Final[int] = 10

_CONTEXT: Final[Context] = Context(prec=28, traps=[DivisionByZero, InvalidOperation])

#: A thousands separator between digits (``1,200``), not an argument separator (``round(2.5, 2)``).
_THOUSANDS_RE = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
#: ``15% of 200`` → ``15% * 200``.
_OF_RE = re.compile(r"%\s*of\b", re.IGNORECASE)
#: A percent sign that ends a number and is not followed by an operand (so not a modulo).
_PERCENT_RE = re.compile(r"(\d(?:[\d.]*\d)?|\d)\s*%(?!\s*[\d.(])")

_SYMBOLS: Final[dict[str, str]] = {"×": "*", "÷": "/", "−": "-", "–": "-", "x": "*", "X": "*"}


class CalculationError(ValueError):
    """The expression is not allowed or cannot be computed (the message is safe to say)."""


def _normalise(expression: str) -> str:
    text = expression.strip()
    for symbol, operator in _SYMBOLS.items():
        if symbol in ("x", "X"):
            # Only between two operands: `3 x 4`, `3x4` (never inside a word).
            text = re.sub(rf"(?<=[\d)\s]){symbol}(?=[\s\d(.])", operator, text)
        else:
            text = text.replace(symbol, operator)
    text = _THOUSANDS_RE.sub("", text)
    text = _OF_RE.sub("% *", text)
    return _PERCENT_RE.sub(r"(\1/100)", text)


def _check(value: Decimal) -> Decimal:
    if not value.is_finite() or abs(value) > MAX_MAGNITUDE:
        raise CalculationError("the numbers are too large to work out")
    return value


def _round(value: Decimal, digits: int) -> Decimal:
    if not 0 <= digits <= MAX_ROUND_DIGITS:
        raise CalculationError(f"round keeps 0 to {MAX_ROUND_DIGITS} decimal places")
    return value.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP, context=_CONTEXT)


def _eval(node: ast.AST) -> Decimal:
    match node:
        case ast.Expression(body=body):
            return _eval(body)
        case ast.Constant(value=value) if isinstance(value, (int, float)) and not isinstance(value, bool):
            return _check(Decimal(str(value)))
        case ast.UnaryOp(op=ast.UAdd(), operand=operand):
            return _eval(operand)
        case ast.UnaryOp(op=ast.USub(), operand=operand):
            return -_eval(operand)
        case ast.BinOp(left=left, op=op, right=right) if isinstance(
            op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod)
        ):
            a, b = _eval(left), _eval(right)
            try:
                match op:
                    case ast.Add():
                        result = _CONTEXT.add(a, b)
                    case ast.Sub():
                        result = _CONTEXT.subtract(a, b)
                    case ast.Mult():
                        result = _CONTEXT.multiply(a, b)
                    case ast.Div():
                        result = _CONTEXT.divide(a, b)
                    case _:
                        result = _CONTEXT.remainder(a, b)
            except (DivisionByZero, InvalidOperation) as exc:
                raise CalculationError("cannot divide by zero") from exc
            return _check(result)
        case ast.Call(func=ast.Name(id="round"), args=args, keywords=[]) if 1 <= len(args) <= 2:
            number = _eval(args[0])
            digits = 0
            if len(args) == 2:
                raw = _eval(args[1])
                if raw != raw.to_integral_value():
                    raise CalculationError("round takes a whole number of decimal places")
                digits = int(raw)
            return _check(_round(number, digits))
        case _:
            raise CalculationError("only numbers, + - * / %, parentheses and round() are allowed")


def _format(value: Decimal) -> str:
    if value == value.to_integral_value():
        return f"{value.quantize(Decimal(1), rounding=ROUND_HALF_UP):f}"
    text = f"{_round(value, MAX_ROUND_DIGITS).normalize(_CONTEXT):f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def evaluate(expression: str) -> tuple[str, str]:
    """Evaluate an arithmetic expression safely.

    Args:
        expression: For example ``"1200 * 15%"``, ``"(450 + 120) / 12"``, ``"round(99.999, 2)"``.

    Returns:
        ``(normalised expression, result)``; the result is a plain decimal string.

    Raises:
        CalculationError: The expression is empty, too long, uses anything outside the
            allowed grammar, divides by zero, or leaves the allowed magnitude.
    """
    text = _normalise(expression)
    if not text:
        raise CalculationError("there is nothing to calculate")
    if len(text) > MAX_EXPRESSION_CHARS:
        raise CalculationError(f"the expression is longer than {MAX_EXPRESSION_CHARS} characters")
    if "**" in text or "//" in text:
        raise CalculationError("only numbers, + - * / %, parentheses and round() are allowed")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise CalculationError("the expression could not be read") from exc
    return text, _format(_eval(tree))


def build_calculate_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build the `calculate` tool bound to `ctx`."""

    @function_tool
    async def calculate(context: RunContext[Any], expression: str) -> str:
        """Work out arithmetic exactly (sums, instalments, percentages) instead of doing it in your head.

        Args:
            expression: Numbers with + - * / %, parentheses and round(x, places), e.g. "1200 * 15%".
        """
        try:
            normalised, result = evaluate(expression)
        except CalculationError as exc:
            raise ToolError(f"Could not calculate that: {exc}.") from exc
        ctx.log.debug("builtin_tool.calculate", call_id=context.function_call.call_id, length=len(normalised))
        return json.dumps({"expression": normalised, "result": result})

    return calculate
