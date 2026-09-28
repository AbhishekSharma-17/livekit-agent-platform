"""The condition grammar of declarative rules (V6-13, D-V6-25): parse and evaluate, never ``eval``.

A rule's ``when`` is a short boolean expression over the session's variables and the
outcome of its tools. It is tokenised and parsed here into a small tree of frozen
dataclasses, and evaluated against a plain mapping. There is no attribute access, no
call, no arithmetic and no name the grammar does not spell out, so a condition can only
ever read a variable or a tool outcome and compare it with a literal.

Grammar (keywords are case-insensitive)::

    condition  := or
    or         := and ("or" and)*
    and        := unary ("and" unary)*
    unary      := "not" unary | "(" or ")" | predicate
    predicate  := VAR "is" ["not"] ("set" | "empty")
                | VAR ("==" | "!=") literal
                | VAR (">=" | "<=" | ">" | "<") NUMBER
                | VAR "matches" REGEX
                | TOOL
    VAR        := "var." name              (name: ``[a-z][a-z0-9_]{0,63}``)
    TOOL       := "tool." toolname (".ok" | ".failed")
    literal    := STRING | NUMBER | "true" | "false"
    STRING     := "…" or '…' (``\\"``, ``\\'`` and ``\\\\`` escapes), at most 100 characters
    REGEX      := /…/ with an optional ``i`` flag

Examples: ``var.policy_number is set``, ``var.claim_type == "auto" and var.estimate >= 5000``,
``var.hazard matches /fire|smoke|gas/i``, ``tool.lookup_policy.failed``,
``not (var.injured == true or var.hazard is set)``.

Semantics (:func:`evaluate`):

* ``is set``: the value is not ``None``, not blank text and not an empty list or object;
  ``is empty`` is its opposite.
* A comparison with an unset variable is false (``!=`` included), so a rule never fires on
  a value nobody has captured yet.
* ``==``/``!=`` with text compares trimmed and case-insensitively; with a number, the value is
  read as a number (``"5,000"`` reads 5000); with ``true``/``false``, ``yes``/``no`` and
  ``1``/``0`` text count too.
* ``>=``, ``<=``, ``>``, ``<`` need a number on the right; a value that is not a number is false.
* ``matches`` searches the value's text (the first :data:`MAX_SUBJECT_CHARS` characters).
  A pattern that repeats a group which already repeats (``(a+)+``) is refused at parse
  time: Python's ``re`` has no timeout.
* ``tool.<name>.ok`` is true when the tool's latest call in this session succeeded;
  ``tool.<name>.failed`` when it failed. Neither is true before the first call.

Bounds: :data:`MAX_CONDITION_CHARS` characters, :data:`MAX_DEPTH` levels of nesting,
:data:`MAX_PREDICATES` predicates and :data:`MAX_LITERAL_CHARS` characters per literal.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal

__all__ = [
    "MAX_CONDITION_CHARS",
    "MAX_DEPTH",
    "MAX_LITERAL_CHARS",
    "MAX_PREDICATES",
    "MAX_SUBJECT_CHARS",
    "And",
    "Compare",
    "ConditionError",
    "Expr",
    "IsSet",
    "Matches",
    "Not",
    "Or",
    "ToolOutcome",
    "evaluate",
    "is_set",
    "nested_repeat",
    "parse_condition",
    "referenced_tools",
    "referenced_variables",
]

#: Longest condition.
MAX_CONDITION_CHARS: Final[int] = 200
#: Deepest nesting of parentheses and ``not``.
MAX_DEPTH: Final[int] = 8
#: Most predicates in one condition.
MAX_PREDICATES: Final[int] = 12
#: Longest string literal or regular expression.
MAX_LITERAL_CHARS: Final[int] = 100
#: How much of a value ``matches`` searches.
MAX_SUBJECT_CHARS: Final[int] = 1000

_NAME = r"[a-z][a-z0-9_]{0,63}"
_TOOL_NAME = r"[A-Za-z_][A-Za-z0-9_]{0,63}"

CompareOp = Literal["==", "!=", ">=", "<=", ">", "<"]
Literal_ = str | float | bool


class ConditionError(ValueError):
    """A condition that does not parse, or breaks a bound. ``position`` is a 0-based offset."""

    def __init__(self, message: str, position: int | None = None) -> None:
        super().__init__(message if position is None else f"{message} (at character {position + 1})")
        self.reason = message
        self.position = position


# --------------------------------------------------------------------------- the tree


@dataclass(frozen=True, slots=True)
class IsSet:
    """``var.<name> is set`` (``is empty`` parses as ``Not(IsSet)``)."""

    name: str


@dataclass(frozen=True, slots=True)
class Compare:
    """``var.<name> <op> <literal>``."""

    name: str
    op: CompareOp
    value: Literal_


@dataclass(frozen=True, slots=True)
class Matches:
    """``var.<name> matches /<pattern>/<flags>``."""

    name: str
    pattern: str
    ignore_case: bool


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    """``tool.<name>.ok`` or ``tool.<name>.failed``."""

    name: str
    outcome: Literal["ok", "failed"]


@dataclass(frozen=True, slots=True)
class Not:
    """``not <expr>``."""

    item: Expr


@dataclass(frozen=True, slots=True)
class And:
    """``<expr> and <expr> …``."""

    items: tuple[Expr, ...]


@dataclass(frozen=True, slots=True)
class Or:
    """``<expr> or <expr> …``."""

    items: tuple[Expr, ...]


Expr = IsSet | Compare | Matches | ToolOutcome | Not | And | Or


# --------------------------------------------------------------------------- regex safety

_UNBOUNDED_RE: Final[re.Pattern[str]] = re.compile(r"[+*]|\{\d*,\}")


def nested_repeat(pattern: str) -> bool:
    """Whether ``pattern`` repeats a group that holds an unbounded repeat (``(a+)+``, ``(\\w*\\s)*``).

    The same scanner as the guardrails check in the api (``config_service.nested_repeat``):
    it tracks groups, skips escapes and character classes, and flags a group containing
    ``+``, ``*`` or ``{n,}`` that is itself followed by ``+``, ``*`` or ``{``.
    """
    stack: list[bool] = []
    index = 0
    in_class = False
    while index < len(pattern):
        char = pattern[index]
        if char == "\\":
            index += 2
            continue
        if in_class:
            in_class = char != "]"
        elif char == "[":
            in_class = True
        elif char == "(":
            stack.append(False)
        elif char == ")" and stack:
            inner = stack.pop()
            if inner and pattern[index + 1 : index + 2] in ("+", "*", "{"):
                return True
            if stack:
                stack[-1] = stack[-1] or inner
        elif stack and _UNBOUNDED_RE.match(pattern, index):
            stack[-1] = True
        index += 1
    return False


# --------------------------------------------------------------------------- tokens

TokenKind = Literal["lparen", "rparen", "op", "var", "tool", "string", "number", "regex", "word", "end"]


@dataclass(frozen=True, slots=True)
class _Token:
    kind: TokenKind
    text: str
    position: int
    value: Any = None


_VAR_RE: Final = re.compile(rf"var\.({_NAME})(?![A-Za-z0-9_.])")
_TOOL_RE: Final = re.compile(rf"tool\.({_TOOL_NAME})\.(ok|failed)(?![A-Za-z0-9_.])")
_NUMBER_RE: Final = re.compile(r"-?\d{1,15}(?:\.\d{1,15})?(?![A-Za-z0-9_.])")
_WORD_RE: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_OPS: Final[tuple[str, ...]] = ("==", "!=", ">=", "<=", ">", "<")
_KEYWORDS: Final[frozenset[str]] = frozenset(
    {"and", "or", "not", "is", "set", "empty", "matches", "true", "false"}
)


def _string(text: str, start: int) -> tuple[str, int]:
    quote = text[start]
    index = start + 1
    out: list[str] = []
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text) and text[index + 1] in (quote, "\\"):
            out.append(text[index + 1])
            index += 2
            continue
        if char == quote:
            value = "".join(out)
            if len(value) > MAX_LITERAL_CHARS:
                raise ConditionError(f"text in quotes is longer than {MAX_LITERAL_CHARS} characters", start)
            return value, index + 1
        out.append(char)
        index += 1
    raise ConditionError("text in quotes is not closed", start)


def _regex(text: str, start: int) -> tuple[tuple[str, bool], int]:
    index = start + 1
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == "/":
            pattern = text[start + 1 : index]
            index += 1
            flags = _WORD_RE.match(text, index)
            ignore_case = False
            if flags is not None:
                if flags.group(0) != "i":
                    raise ConditionError("the only pattern flag is 'i' (ignore case)", index)
                ignore_case = True
                index = flags.end()
            return (pattern, ignore_case), index
        index += 1
    raise ConditionError("a pattern must end with '/'", start)


def _tokens(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char.isspace():
            index += 1
            continue
        if char == "(":
            tokens.append(_Token("lparen", char, index))
            index += 1
            continue
        if char == ")":
            tokens.append(_Token("rparen", char, index))
            index += 1
            continue
        if char in "\"'":
            value, end = _string(text, index)
            tokens.append(_Token("string", text[index:end], index, value))
            index = end
            continue
        if char == "/":
            if not tokens or tokens[-1].kind != "word" or tokens[-1].text.lower() != "matches":
                raise ConditionError("a /pattern/ may only follow 'matches'", index)
            (pattern, ignore_case), end = _regex(text, index)
            tokens.append(_Token("regex", text[index:end], index, (pattern, ignore_case)))
            index = end
            continue
        op = next((candidate for candidate in _OPS if text.startswith(candidate, index)), None)
        if op is not None:
            tokens.append(_Token("op", op, index))
            index += len(op)
            continue
        if text.startswith("var.", index):
            match = _VAR_RE.match(text, index)
            if match is None:
                raise ConditionError(
                    "a variable is written var.<name>: lower-case letters, digits and '_', nothing after it",
                    index,
                )
            tokens.append(_Token("var", match.group(0), index, match.group(1)))
            index = match.end()
            continue
        if text.startswith("tool.", index):
            match = _TOOL_RE.match(text, index)
            if match is None:
                raise ConditionError("a tool outcome is written tool.<name>.ok or tool.<name>.failed", index)
            tokens.append(_Token("tool", match.group(0), index, (match.group(1), match.group(2))))
            index = match.end()
            continue
        number = _NUMBER_RE.match(text, index)
        if number is not None and (char.isdigit() or char == "-"):
            tokens.append(_Token("number", number.group(0), index, float(number.group(0))))
            index = number.end()
            continue
        word = _WORD_RE.match(text, index)
        if word is not None:
            if word.group(0).lower() not in _KEYWORDS:
                raise ConditionError(
                    f"'{word.group(0)}' is not something a condition understands; name a variable as "
                    "var.<name> and put text in quotes",
                    index,
                )
            if text.startswith(".", word.end()):
                raise ConditionError("conditions cannot read attributes or call anything", word.end())
            tokens.append(_Token("word", word.group(0), index))
            index = word.end()
            continue
        raise ConditionError(f"unexpected character '{char}'", index)
    tokens.append(_Token("end", "", len(text)))
    return tokens


# --------------------------------------------------------------------------- parser


class _Parser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.tokens = _tokens(text)
        self.index = 0
        self.predicates = 0

    @property
    def current(self) -> _Token:
        return self.tokens[self.index]

    def _word(self, *words: str) -> bool:
        token = self.current
        return token.kind == "word" and token.text.lower() in words

    def _advance(self) -> _Token:
        token = self.current
        self.index += 1
        return token

    def parse(self) -> Expr:
        expr = self._or(0)
        if self.current.kind != "end":
            raise ConditionError(f"unexpected '{self.current.text}'", self.current.position)
        return expr

    def _or(self, depth: int) -> Expr:
        items = [self._and(depth)]
        while self._word("or"):
            self._advance()
            items.append(self._and(depth))
        return items[0] if len(items) == 1 else Or(tuple(items))

    def _and(self, depth: int) -> Expr:
        items = [self._unary(depth)]
        while self._word("and"):
            self._advance()
            items.append(self._unary(depth))
        return items[0] if len(items) == 1 else And(tuple(items))

    def _unary(self, depth: int) -> Expr:
        if depth >= MAX_DEPTH:
            raise ConditionError(f"conditions nest at most {MAX_DEPTH} levels deep", self.current.position)
        if self._word("not"):
            self._advance()
            return Not(self._unary(depth + 1))
        if self.current.kind == "lparen":
            opening = self._advance()
            inner = self._or(depth + 1)
            if self.current.kind != "rparen":
                raise ConditionError("a '(' is not closed", opening.position)
            self._advance()
            return inner
        return self._predicate()

    def _count(self, position: int) -> None:
        self.predicates += 1
        if self.predicates > MAX_PREDICATES:
            raise ConditionError(f"a condition has at most {MAX_PREDICATES} checks", position)

    def _predicate(self) -> Expr:
        token = self.current
        if token.kind == "tool":
            self._advance()
            self._count(token.position)
            name, outcome = token.value
            return ToolOutcome(name, outcome)
        if token.kind != "var":
            if token.kind == "end":
                raise ConditionError("the condition ends too early", token.position)
            raise ConditionError(
                f"expected var.<name> or tool.<name>.ok, found '{token.text}'", token.position
            )
        self._advance()
        self._count(token.position)
        name: str = token.value
        follow = self.current
        if self._word("is"):
            self._advance()
            negate = False
            if self._word("not"):
                self._advance()
                negate = True
            if self._word("set"):
                self._advance()
                expr: Expr = IsSet(name)
                return Not(expr) if negate else expr
            if self._word("empty"):
                self._advance()
                return IsSet(name) if negate else Not(IsSet(name))
            raise ConditionError("after 'is' write 'set', 'empty', 'not set' or 'not empty'", follow.position)
        if self._word("matches"):
            self._advance()
            regex = self.current
            if regex.kind != "regex":
                raise ConditionError("after 'matches' write a /pattern/", regex.position)
            self._advance()
            pattern, ignore_case = regex.value
            _check_pattern(pattern, regex.position)
            return Matches(name, pattern, ignore_case)
        if follow.kind == "op":
            op: CompareOp = follow.text  # type: ignore[assignment]
            self._advance()
            literal = self.current
            if op in ("==", "!="):
                if literal.kind in ("string", "number"):
                    self._advance()
                    return Compare(name, op, literal.value)
                if self._word("true", "false"):
                    self._advance()
                    return Compare(name, op, literal.text.lower() == "true")
                raise ConditionError(
                    f"after '{op}' write text in quotes, a number, true or false", literal.position
                )
            if literal.kind != "number":
                raise ConditionError(f"after '{op}' write a number", literal.position)
            self._advance()
            return Compare(name, op, literal.value)
        raise ConditionError(
            "after a variable write 'is set', 'is empty', '==', '!=', '>=', '<=', '>', '<' or 'matches'",
            follow.position,
        )


def _check_pattern(pattern: str, position: int) -> None:
    if not pattern:
        raise ConditionError("the pattern is empty", position)
    if len(pattern) > MAX_LITERAL_CHARS:
        raise ConditionError(f"a pattern is at most {MAX_LITERAL_CHARS} characters", position)
    if nested_repeat(pattern):
        raise ConditionError(
            "this pattern repeats a group that already repeats, which can take very long; simplify it",
            position,
        )
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ConditionError(f"the pattern is not valid ({exc.msg})", position) from exc


def parse_condition(text: str) -> Expr:
    """Parse one condition.

    Raises:
        ConditionError: It does not parse or breaks a bound; the message says where and why
            in words an admin can act on.
    """
    if not text or not text.strip():
        raise ConditionError("the condition is empty")
    if len(text) > MAX_CONDITION_CHARS:
        raise ConditionError(f"a condition is at most {MAX_CONDITION_CHARS} characters")
    return _Parser(text).parse()


# --------------------------------------------------------------------------- analysis


def referenced_variables(expr: Expr) -> set[str]:
    """Every ``var.<name>`` the condition reads."""
    match expr:
        case IsSet(name=name) | Compare(name=name) | Matches(name=name):
            return {name}
        case ToolOutcome():
            return set()
        case Not(item=item):
            return referenced_variables(item)
        case And(items=items) | Or(items=items):
            return set().union(*(referenced_variables(item) for item in items))
    return set()  # pragma: no cover - exhaustive


def referenced_tools(expr: Expr) -> set[str]:
    """Every ``tool.<name>`` the condition reads."""
    match expr:
        case ToolOutcome(name=name):
            return {name}
        case Not(item=item):
            return referenced_tools(item)
        case And(items=items) | Or(items=items):
            return set().union(*(referenced_tools(item) for item in items))
    return set()


# --------------------------------------------------------------------------- evaluation

_TRUE_WORDS: Final[frozenset[str]] = frozenset({"true", "yes", "1", "y", "on"})
_FALSE_WORDS: Final[frozenset[str]] = frozenset({"false", "no", "0", "n", "off"})
_NUMBER_TEXT_RE: Final = re.compile(r"^[+-]?[\d,]*\.?\d+$")


def is_set(value: Any) -> bool:
    """Whether a variable counts as set: not ``None``, blank text, or an empty list or object."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list | dict | tuple | set):
        return bool(value)
    return True


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace(" ", "")
        if _NUMBER_TEXT_RE.match(text):
            try:
                return float(text.replace(",", ""))
            except ValueError:
                return None
    return None


def _boolean(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE_WORDS:
            return True
        if text in _FALSE_WORDS:
            return False
    return None


def _compare(value: Any, op: CompareOp, literal: Literal_) -> bool:
    if not is_set(value):
        return False
    if isinstance(literal, bool):
        flag = _boolean(value)
        if flag is None:
            return False
        return (flag == literal) if op == "==" else (flag != literal)
    if isinstance(literal, float):
        number = _number(value)
        if number is None:
            return False
        match op:
            case "==":
                return number == literal
            case "!=":
                return number != literal
            case ">=":
                return number >= literal
            case "<=":
                return number <= literal
            case ">":
                return number > literal
            case "<":
                return number < literal
    text = value if isinstance(value, str) else str(value)
    equal = text.strip().casefold() == literal.strip().casefold()
    return equal if op == "==" else not equal


def evaluate(
    expr: Expr,
    variables: Mapping[str, Any],
    tools: Mapping[str, bool] | None = None,
) -> bool:
    """Evaluate a parsed condition.

    Args:
        expr: From :func:`parse_condition`.
        variables: The session's variables (``{name: value}``).
        tools: Each tool's latest outcome in the session (``True`` = succeeded).

    Returns:
        Whether the condition holds. Never raises for a value of an unexpected type.
    """
    outcomes = tools or {}
    match expr:
        case IsSet(name=name):
            return is_set(variables.get(name))
        case Compare(name=name, op=op, value=literal):
            return _compare(variables.get(name), op, literal)
        case Matches(name=name, pattern=pattern, ignore_case=ignore_case):
            value = variables.get(name)
            if not is_set(value):
                return False
            subject = (value if isinstance(value, str) else str(value))[:MAX_SUBJECT_CHARS]
            return re.search(pattern, subject, re.IGNORECASE if ignore_case else 0) is not None
        case ToolOutcome(name=name, outcome=outcome):
            result = outcomes.get(name)
            if result is None:
                return False
            return result if outcome == "ok" else not result
        case Not(item=item):
            return not evaluate(item, variables, outcomes)
        case And(items=items):
            return all(evaluate(item, variables, outcomes) for item in items)
        case Or(items=items):
            return any(evaluate(item, variables, outcomes) for item in items)
    return False  # pragma: no cover - exhaustive
