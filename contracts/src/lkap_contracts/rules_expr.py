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
  A pattern that can backtrack for very long (``(a+)+``, ``(a|a)+``, ``a*a*``;
  :func:`nested_repeat`) is refused at parse time: Python's ``re`` has no timeout. A stored
  rule is read with ``safe_patterns=False`` (grammar only, V6-28), so such a pattern is an
  error on that rule (``rules.rule_issues``) rather than an unloadable config; the worker
  parses with the default and skips the rule.
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
    "referenced_patterns",
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
#
# V6-21 (S6-4): a small, engine-independent scanner. It reads the pattern into items
# (a character atom, a zero-width assertion or a group, each with its repeat bounds) and
# refuses three shapes that backtrack for very long on a long text:
#
# * a repeated group that holds an unbounded repeat: ``(a+)+``, ``(\w*\s)*``;
# * a repeated group that holds alternatives: ``(a|a)+``, ``(a|aa)+``;
# * two unbounded repeats that can match the same character and can meet, looking through
#   anything that may match nothing: ``a*a*``, ``\w*\s*\w*``, ``\d+\.?\d*``.
#
# Characters are modelled as ASCII code points plus four stand-ins for everything else
# (a digit, a space, a letter, any other), so ``[a-z]+\d*`` and ``\w+\s\w+`` pass. The
# console's copy (``web/src/components/console/agents/rules/condition.ts``,
# ``hasNestedRepeat``) makes the same decisions; keep the two in step.

_OTHER_DIGIT: Final[int] = 128
_OTHER_SPACE: Final[int] = 129
_OTHER_WORD: Final[int] = 130
_OTHER_PUNCT: Final[int] = 131
_ASCII: Final[frozenset[int]] = frozenset(range(128))
_OTHERS: Final[frozenset[int]] = frozenset({_OTHER_DIGIT, _OTHER_SPACE, _OTHER_WORD, _OTHER_PUNCT})
_UNIVERSE: Final[frozenset[int]] = _ASCII | _OTHERS
_DIGITS: Final[frozenset[int]] = frozenset(range(ord("0"), ord("9") + 1))
_LETTERS: Final[frozenset[int]] = frozenset(range(ord("a"), ord("z") + 1)) | frozenset(
    range(ord("A"), ord("Z") + 1)
)
_WORDS: Final[frozenset[int]] = _DIGITS | _LETTERS | {ord("_")}
_SPACES: Final[frozenset[int]] = frozenset(ord(c) for c in " \t\n\r\f\v\x1c\x1d\x1e\x1f")
#: ``\d \D \w \W \s \S``; their non-ASCII part is exact (a negated class may remove it).
_CATEGORIES: Final[dict[str, frozenset[int]]] = {
    "d": _DIGITS | {_OTHER_DIGIT},
    "D": (_ASCII - _DIGITS) | {_OTHER_SPACE, _OTHER_WORD, _OTHER_PUNCT},
    "w": _WORDS | {_OTHER_DIGIT, _OTHER_WORD},
    "W": (_ASCII - _WORDS) | {_OTHER_SPACE, _OTHER_PUNCT},
    "s": _SPACES | {_OTHER_SPACE},
    "S": (_ASCII - _SPACES) | {_OTHER_DIGIT, _OTHER_WORD, _OTHER_PUNCT},
}
_CONTROL_ESCAPES: Final[dict[str, int]] = {"n": 10, "t": 9, "r": 13, "f": 12, "v": 11, "a": 7}
_HEX_LENGTH: Final[dict[str, int]] = {"x": 2, "u": 4, "U": 8}
_HEX_DIGITS: Final[str] = "0123456789abcdefABCDEF"
_BRACE_RE: Final[re.Pattern[str]] = re.compile(r"\{([0-9]*)(,?)([0-9]*)\}")


def _code_chars(code: int) -> frozenset[int]:
    """One character; both cases of an ASCII letter (a pattern may ignore case)."""
    if code >= 128:
        return _OTHERS
    char = chr(code)
    return frozenset({ord(char.lower()), ord(char.upper())})


@dataclass(frozen=True, slots=True)
class _Atom:
    """One character out of ``chars``."""

    chars: frozenset[int]


@dataclass(frozen=True, slots=True)
class _Zero:
    """An assertion that matches no character (``^``, ``\\b``, inline flags, a comment)."""


@dataclass(frozen=True, slots=True)
class _Group:
    branches: tuple[tuple[_Item, ...], ...]
    lookaround: bool


@dataclass(frozen=True, slots=True)
class _Item:
    node: _Atom | _Zero | _Group
    low: int
    #: ``None``: unbounded.
    high: int | None


_ZERO: Final = _Zero()


@dataclass(frozen=True, slots=True)
class _Escape:
    #: ``None``: a zero-width assertion.
    chars: frozenset[int] | None
    #: The one character it stands for (a range end), else ``None``.
    code: int | None
    end: int
    #: A category (``\d``): its non-ASCII part is exact, not a stand-in.
    exact: bool = False


def _escape(pattern: str, index: int, *, in_class: bool) -> _Escape:
    if index + 1 >= len(pattern):
        return _Escape(_code_chars(92), 92, len(pattern))
    char = pattern[index + 1]
    end = index + 2
    if char in _CATEGORIES:
        return _Escape(_CATEGORIES[char], None, end, exact=True)
    if not in_class and char in "AZbBz":
        return _Escape(None, None, end)
    if in_class and char == "b":
        return _Escape(_code_chars(8), 8, end)
    if char in _CONTROL_ESCAPES:
        code = _CONTROL_ESCAPES[char]
        return _Escape(_code_chars(code), code, end)
    if char in _HEX_LENGTH:
        digits = pattern[end : end + _HEX_LENGTH[char]]
        if len(digits) == _HEX_LENGTH[char] and all(d in _HEX_DIGITS for d in digits):
            code = int(digits, 16)
            return _Escape(_code_chars(code), code, end + len(digits))
        return _Escape(_UNIVERSE, None, end)
    if char == "N":
        close = pattern.find("}", end)
        return _Escape(_UNIVERSE, None, len(pattern) if close < 0 else close + 1)
    if "0" <= char <= "9":
        while end < len(pattern) and end < index + 4 and "0" <= pattern[end] <= "9":
            end += 1
        return _Escape(_UNIVERSE, None, end)
    return _Escape(_code_chars(ord(char)), ord(char), end)


class _RegexScan:
    """Reads a pattern into :class:`_Item` sequences; never raises (a bad pattern is ``re``'s to refuse)."""

    def __init__(self, pattern: str) -> None:
        self.pattern = pattern
        self.index = 0
        self.depth = 0

    def branches(self) -> tuple[tuple[_Item, ...], ...]:
        pattern = self.pattern
        found: list[tuple[_Item, ...]] = []
        sequence: list[_Item] = []
        while self.index < len(pattern):
            char = pattern[self.index]
            if char == "|":
                found.append(tuple(sequence))
                sequence = []
                self.index += 1
                continue
            if char == ")" and self.depth > 0:
                break
            sequence.append(self._quantified(self._atom()))
        found.append(tuple(sequence))
        return tuple(found)

    def _atom(self) -> _Atom | _Zero | _Group:
        pattern = self.pattern
        char = pattern[self.index]
        if char == "(":
            return self._group()
        if char == "[":
            return _Atom(self._class())
        if char == "\\":
            escape = _escape(pattern, self.index, in_class=False)
            self.index = escape.end
            return _ZERO if escape.chars is None else _Atom(escape.chars)
        self.index += 1
        if char == ".":
            return _Atom(_UNIVERSE)
        if char in "^$":
            return _ZERO
        return _Atom(_code_chars(ord(char)))

    def _skip_past(self, stop: str, start: int) -> None:
        close = self.pattern.find(stop, start)
        self.index = len(self.pattern) if close < 0 else close + 1

    def _group(self) -> _Atom | _Zero | _Group:
        pattern = self.pattern
        self.index += 1
        lookaround = False
        if pattern.startswith("?", self.index):
            rest = pattern[self.index + 1 : self.index + 3]
            if rest.startswith("#"):
                self._skip_past(")", self.index)
                return _ZERO
            if rest == "P=":
                self._skip_past(")", self.index)
                return _Atom(_UNIVERSE)
            if rest == "P<":
                self._skip_past(">", self.index)
            elif rest[:1] in ("=", "!"):
                lookaround = True
                self.index += 2
            elif rest in ("<=", "<!"):
                lookaround = True
                self.index += 3
            elif rest[:1] in (":", ">"):
                self.index += 2
            elif rest[:1] == "(":
                self._skip_past(")", self.index + 2)
            else:
                end = self.index + 1
                while end < len(pattern) and (pattern[end] == "-" or ord(pattern[end]) in _LETTERS):
                    end += 1
                if end < len(pattern) and pattern[end] == ")":
                    self.index = end + 1
                    return _ZERO
                self.index = end + 1 if end < len(pattern) and pattern[end] == ":" else end
        self.depth += 1
        branches = self.branches()
        self.depth -= 1
        if self.index < len(pattern) and pattern[self.index] == ")":
            self.index += 1
        return _Group(branches, lookaround)

    def _class(self) -> frozenset[int]:
        pattern = self.pattern
        index = self.index + 1
        negate = index < len(pattern) and pattern[index] == "^"
        if negate:
            index += 1
        members: set[int] = set()
        exact_others: set[int] = set()
        first = True
        while index < len(pattern):
            char = pattern[index]
            if char == "]" and not first:
                index += 1
                break
            first = False
            if char == "\\":
                escape = _escape(pattern, index, in_class=True)
                chars = escape.chars if escape.chars is not None else _UNIVERSE
                code, index, exact = escape.code, escape.end, escape.exact
            else:
                code, index, exact = ord(char), index + 1, False
                chars = _code_chars(code)
            if (
                code is not None
                and index + 1 < len(pattern)
                and pattern[index] == "-"
                and pattern[index + 1] != "]"
            ):
                if pattern[index + 1] == "\\":
                    high_escape = _escape(pattern, index + 1, in_class=True)
                    high, after = high_escape.code, high_escape.end
                else:
                    high, after = ord(pattern[index + 1]), index + 2
                if high is not None:
                    index = after
                    for point in range(code, min(high, 127) + 1):
                        members |= _code_chars(point)
                    if high >= 128:
                        members |= _OTHERS
                    continue
            members |= chars
            if exact:
                exact_others |= chars & _OTHERS
        self.index = index
        if negate:
            return (_ASCII - members) | (_OTHERS - exact_others)
        return frozenset(members)

    def _quantified(self, node: _Atom | _Zero | _Group) -> _Item:
        pattern = self.pattern
        index = self.index
        char = pattern[index] if index < len(pattern) else ""
        brace = _BRACE_RE.match(pattern, index) if char == "{" else None
        low: int
        high: int | None
        if char == "*":
            low, high = 0, None
        elif char == "+":
            low, high = 1, None
        elif char == "?":
            low, high = 0, 1
        elif brace is not None and (brace.group(1) or brace.group(2)):
            low = int(brace.group(1) or "0")
            high = (int(brace.group(3)) if brace.group(3) else None) if brace.group(2) else low
            index = brace.end() - 1
        else:
            return _Item(node, 1, 1)
        index += 1
        if index < len(pattern) and pattern[index] in "?+":
            index += 1
        self.index = index
        return _Item(node, low, high)


def _repeats(item: _Item) -> bool:
    return item.high is None or item.high > 1


def _has_unbounded(group: _Group) -> bool:
    return any(
        item.high is None or (isinstance(item.node, _Group) and _has_unbounded(item.node))
        for branch in group.branches
        for item in branch
    )


def _has_alternatives(group: _Group) -> bool:
    return len(group.branches) > 1 or any(
        isinstance(item.node, _Group) and _has_alternatives(item.node)
        for branch in group.branches
        for item in branch
    )


def _chars_of(node: _Atom | _Zero | _Group) -> frozenset[int]:
    if isinstance(node, _Atom):
        return node.chars
    if isinstance(node, _Zero):
        return frozenset()
    found: frozenset[int] = frozenset()
    for branch in node.branches:
        for item in branch:
            found |= _chars_of(item.node)
    return found


def _may_be_empty(item: _Item) -> bool:
    node = item.node
    if item.low == 0 or isinstance(node, _Zero):
        return True
    if isinstance(node, _Atom):
        return False
    return node.lookaround or any(all(_may_be_empty(i) for i in branch) for branch in node.branches)


def _step(
    window: list[frozenset[int]], chars: frozenset[int], *, unbounded: bool, optional: bool
) -> list[frozenset[int]] | None:
    """The unbounded repeats the next item can still meet; ``None`` when two of them overlap."""
    if unbounded and any(chars & earlier for earlier in window):
        return None
    if optional:
        return [*window, chars] if unbounded else window
    return [chars] if unbounded else []


def _walk(items: tuple[_Item, ...], window: list[frozenset[int]]) -> list[frozenset[int]] | None:
    """Walk one sequence from ``window``; the window it leaves, or ``None`` for a slow shape."""
    for item in items:
        node = item.node
        after: list[frozenset[int]] | None
        if isinstance(node, _Zero):
            continue
        if isinstance(node, _Atom):
            after = _step(window, node.chars, unbounded=item.high is None, optional=item.low == 0)
        elif node.lookaround:
            if any(_walk(branch, []) is None for branch in node.branches):
                return None
            continue
        elif _repeats(item):
            if _has_unbounded(node) or _has_alternatives(node):
                return None
            after = _step(window, _chars_of(node), unbounded=item.high is None, optional=_may_be_empty(item))
        else:
            # At most once: each alternative carries on from where the sequence is.
            after = []
            for branch in node.branches:
                out = _walk(branch, list(window))
                if out is None:
                    return None
                after.extend(out)
            if item.low == 0:
                after.extend(window)
        if after is None:
            return None
        window = after
    return window


def nested_repeat(pattern: str) -> bool:
    """Whether ``pattern`` has a shape that can backtrack for very long on a long text.

    One scanner for rule patterns and the guardrails check in the api (``config_service``
    imports it, ask #75). ``True`` for a repeated group holding an unbounded repeat
    (``(a+)+``, ``(\\w*\\s)*``) or alternatives (``(a|a)+b``), and for two unbounded repeats
    of overlapping characters that can meet (``a*a*b``, ``\\w*\\s*\\w*``); the comment above
    describes the model. ``re`` cannot be interrupted, so this scanner is the bound.
    """
    return any(_walk(branch, []) is None for branch in _RegexScan(pattern).branches())


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
    def __init__(self, text: str, *, safe_patterns: bool = True) -> None:
        self.text = text
        self.tokens = _tokens(text)
        self.index = 0
        self.predicates = 0
        self.safe_patterns = safe_patterns

    @property
    def current(self) -> _Token:
        return self.tokens[self.index]

    def _at(self, kind: TokenKind) -> bool:
        return self.current.kind == kind

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
            if not self._at("rparen"):
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
            tool_name, outcome = token.value
            return ToolOutcome(tool_name, outcome)
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
            _check_pattern(pattern, regex.position, safe=self.safe_patterns)
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


def _check_pattern(pattern: str, position: int, *, safe: bool = True) -> None:
    if not pattern:
        raise ConditionError("the pattern is empty", position)
    if len(pattern) > MAX_LITERAL_CHARS:
        raise ConditionError(f"a pattern is at most {MAX_LITERAL_CHARS} characters", position)
    if safe and nested_repeat(pattern):
        raise ConditionError(
            "this pattern repeats a group that already repeats, which can take very long; simplify it",
            position,
        )
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ConditionError(f"the pattern is not valid ({exc.msg})", position) from exc


def parse_condition(text: str, *, safe_patterns: bool = True) -> Expr:
    """Parse one condition.

    Args:
        text: The condition.
        safe_patterns: Also refuse a ``matches`` pattern :func:`nested_repeat` flags (the
            default, and what the worker uses). ``False`` checks the grammar only: the
            contract reads a stored rule this way so a pattern the scanner refuses is an
            error on that rule (``rules.rule_issues``), not a config that cannot load (V6-28).

    Raises:
        ConditionError: It does not parse or breaks a bound; the message says where and why
            in words an admin can act on.
    """
    if not text or not text.strip():
        raise ConditionError("the condition is empty")
    if len(text) > MAX_CONDITION_CHARS:
        raise ConditionError(f"a condition is at most {MAX_CONDITION_CHARS} characters")
    return _Parser(text, safe_patterns=safe_patterns).parse()


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


def referenced_patterns(expr: Expr) -> list[str]:
    """Every ``matches`` pattern of the condition, in the order written."""
    match expr:
        case Matches(pattern=pattern):
            return [pattern]
        case IsSet() | Compare() | ToolOutcome():
            return []
        case Not(item=item):
            return referenced_patterns(item)
        case And(items=items) | Or(items=items):
            return [pattern for item in items for pattern in referenced_patterns(item)]
    return []  # pragma: no cover - exhaustive


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
