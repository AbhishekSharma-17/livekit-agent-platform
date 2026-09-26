"""`spell_back` built-in tool (V5-25): a read-back of what the caller gave, in words a voice can say.

Speech-to-text hears ``b`` and ``d``, ``m`` and ``n`` alike, so the agent
confirms emails, phone numbers, postcodes and reference numbers by spelling
them out: letters as "B as in boy", digits one by one in small groups,
punctuation by name ("dot", "at", "dash"). Amounts are said as money. Pure
text formatting, no network; instant and never backgrounded
(`NEVER_BACKGROUND_TOOLS`).
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Final, Literal

from livekit.agents import FunctionTool, RunContext, ToolError, function_tool
from packs.base import PackSessionContext

__all__ = ["SpellKind", "build_spell_back_tool", "spell"]

SpellKind = Literal["auto", "email", "phone", "code", "amount"]

#: One everyday word per letter, for "B as in boy".
LETTER_WORDS: Final[dict[str, str]] = {
    "a": "apple",
    "b": "boy",
    "c": "cat",
    "d": "dog",
    "e": "echo",
    "f": "fox",
    "g": "golf",
    "h": "hotel",
    "i": "india",
    "j": "juliet",
    "k": "kilo",
    "l": "lima",
    "m": "mike",
    "n": "november",
    "o": "oscar",
    "p": "papa",
    "q": "queen",
    "r": "romeo",
    "s": "sierra",
    "t": "tango",
    "u": "uniform",
    "v": "victor",
    "w": "whiskey",
    "x": "x-ray",
    "y": "yankee",
    "z": "zulu",
}

DIGIT_WORDS: Final[tuple[str, ...]] = (
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
)

SYMBOL_WORDS: Final[dict[str, str]] = {
    ".": "dot",
    "@": "at",
    "-": "dash",
    "_": "underscore",
    "+": "plus",
    "/": "slash",
    "#": "hash",
    "&": "and",
    "'": "apostrophe",
}

#: Mail domains said as words rather than spelt ("gmail dot com").
COMMON_DOMAIN_WORDS: Final[frozenset[str]] = frozenset(
    {"gmail", "googlemail", "outlook", "hotmail", "live", "yahoo", "icloud", "aol", "proton", "protonmail"}
    | {"com", "net", "org", "co", "uk", "us", "ca", "au", "in", "de", "fr", "ie", "nz", "io", "me", "edu"}
)

#: Currency symbols and codes, with their unit and hundredth.
CURRENCIES: Final[dict[str, tuple[str, str, str, str]]] = {
    "$": ("dollar", "dollars", "cent", "cents"),
    "USD": ("dollar", "dollars", "cent", "cents"),
    "£": ("pound", "pounds", "penny", "pence"),
    "GBP": ("pound", "pounds", "penny", "pence"),
    "€": ("euro", "euros", "cent", "cents"),
    "EUR": ("euro", "euros", "cent", "cents"),
    "₹": ("rupee", "rupees", "paisa", "paise"),
    "INR": ("rupee", "rupees", "paisa", "paise"),
}

MAX_INPUT_CHARS: Final[int] = 120

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_AMOUNT_RE = re.compile(
    r"^(?P<cur>[$£€₹]|USD|GBP|EUR|INR)?\s*(?P<num>\d[\d,]*(?:\.\d{1,2})?)\s*(?P<cur2>USD|GBP|EUR|INR)?$"
)
_PHONE_RE = re.compile(r"^\+?[\d\s().-]{6,}$")

_ONES: Final[tuple[str, ...]] = (
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
)
_TENS: Final[tuple[str, ...]] = (
    "",
    "",
    "twenty",
    "thirty",
    "forty",
    "fifty",
    "sixty",
    "seventy",
    "eighty",
    "ninety",
)
_SCALES: Final[tuple[tuple[int, str], ...]] = ((10**9, "billion"), (10**6, "million"), (1000, "thousand"))


def number_words(value: int) -> str:
    """``1234`` → ``"one thousand two hundred and thirty-four"`` (0 ≤ value < 10**12)."""
    if value < 20:
        return _ONES[value]
    if value < 100:
        tens, ones = divmod(value, 10)
        return _TENS[tens] + (f"-{_ONES[ones]}" if ones else "")
    if value < 1000:
        hundreds, rest = divmod(value, 100)
        return f"{_ONES[hundreds]} hundred" + (f" and {number_words(rest)}" if rest else "")
    for scale, name in _SCALES:
        if value >= scale:
            head, rest = divmod(value, scale)
            tail = ""
            if rest:
                tail = f" and {number_words(rest)}" if rest < 100 else f" {number_words(rest)}"
            return f"{number_words(head)} {name}{tail}"
    raise ValueError(value)  # pragma: no cover - the scales cover every value below 10**12


def _char(ch: str) -> str:
    lower = ch.lower()
    if lower in LETTER_WORDS:
        return f"{ch.upper()} as in {LETTER_WORDS[lower]}"
    if ch.isdigit():
        return DIGIT_WORDS[int(ch)]
    if ch.isspace():
        return ""
    return SYMBOL_WORDS.get(ch, ch)


def _spell_chars(text: str) -> str:
    return ", ".join(word for word in (_char(ch) for ch in text) if word)


def _digit_groups(digits: str) -> list[str]:
    """Groups of three, ending in groups of four so no group is shorter than three.

    ``555 010 0123`` (10 digits), ``077 0090 0123`` (11), ``1234 5678`` (8); four digits
    or fewer stay one group, and five split three and two.
    """
    n = len(digits)
    if n <= 4:
        return [digits]
    fours = n % 3 if n >= 8 else (1 if n % 3 == 1 else 0)
    threes_end = n - 4 * fours
    groups = [digits[i : i + 3] for i in range(0, threes_end, 3)]
    groups += [digits[i : i + 4] for i in range(threes_end, n, 4)]
    return groups


def spell_email(text: str) -> str:
    """``jo.smith@gmail.com`` → ``"J as in juliet, O as in oscar, dot, … at gmail dot com"``."""
    local, _, domain = text.strip().partition("@")
    labels = []
    for label in domain.split("."):
        labels.append(label.lower() if label.lower() in COMMON_DOMAIN_WORDS else _spell_chars(label))
    return f"{_spell_chars(local)}, at {' dot '.join(labels)}"


def spell_phone(text: str) -> str:
    """``+44 7700 900123`` → ``"plus, four four; seven seven zero zero; nine zero zero; one two three"``."""
    stripped = text.strip()
    plus = stripped.startswith("+")
    raw_groups = [g for g in re.split(r"[\s().-]+", stripped.lstrip("+")) if g]
    groups: list[str] = []
    for group in raw_groups:
        groups.extend(_digit_groups(group) if len(group) > 4 else [group])
    spoken = ["; ".join(" ".join(DIGIT_WORDS[int(d)] for d in group) for group in groups)]
    return ("plus, " if plus else "") + spoken[0]


def spell_code(text: str) -> str:
    """A reference or postcode, part by part: ``POL-2024`` → ``"P as in papa, …; dash; two zero …"``."""
    parts = [p for p in re.split(r"(\s+|[-/])", text.strip()) if p and not p.isspace()]
    spoken: list[str] = []
    for part in parts:
        if part in ("-", "/"):
            spoken.append(SYMBOL_WORDS[part])
        elif part.isdigit():
            spoken.append("; ".join(" ".join(DIGIT_WORDS[int(d)] for d in g) for g in _digit_groups(part)))
        else:
            spoken.append(_spell_chars(part))
    return "; ".join(spoken)


def spell_amount(text: str) -> str:
    """``$1,234.50`` → ``"one thousand two hundred and thirty-four dollars and fifty cents"``."""
    match = _AMOUNT_RE.match(text.strip())
    if match is None:
        raise ValueError("not an amount")
    code = match.group("cur") or match.group("cur2")
    try:
        value = Decimal(match.group("num").replace(",", ""))
    except InvalidOperation as exc:  # pragma: no cover - the pattern only admits digits
        raise ValueError("not an amount") from exc
    whole = int(value)
    hundredths = int((value - whole) * 100)
    if whole >= 10**12:
        raise ValueError("amount too large")
    if code is None:
        spoken = number_words(whole)
        if hundredths:
            decimals = match.group("num").split(".")[1]
            spoken += " point " + " ".join(DIGIT_WORDS[int(d)] for d in decimals)
        return spoken
    one, many, small_one, small_many = CURRENCIES[code]
    spoken = f"{number_words(whole)} {one if whole == 1 else many}"
    if hundredths:
        spoken += f" and {number_words(hundredths)} {small_one if hundredths == 1 else small_many}"
    return spoken


def detect_kind(text: str) -> SpellKind:
    """Guess what ``text`` is: an email, an amount, a phone number, else a code."""
    stripped = text.strip()
    if _EMAIL_RE.match(stripped):
        return "email"
    if _AMOUNT_RE.match(stripped) and re.search(r"[$£€₹]|USD|GBP|EUR|INR|\.\d{2}$|,", stripped):
        return "amount"
    if _PHONE_RE.match(stripped) and sum(ch.isdigit() for ch in stripped) >= 6:
        return "phone"
    return "code"


def spell(text: str, kind: SpellKind = "auto") -> tuple[SpellKind, str]:
    """Format ``text`` for reading back aloud.

    Args:
        text: What the caller gave (at most :data:`MAX_INPUT_CHARS` characters).
        kind: ``auto`` guesses; ``email``, ``phone``, ``code`` (reference numbers, postcodes)
            and ``amount`` force a format.

    Returns:
        ``(kind used, the read-back)``.

    Raises:
        ValueError: The text is empty, too long, or does not fit the forced ``kind``.
    """
    stripped = text.strip()
    if not stripped:
        raise ValueError("there is nothing to spell")
    if len(stripped) > MAX_INPUT_CHARS:
        raise ValueError(f"it is longer than {MAX_INPUT_CHARS} characters")
    chosen: SpellKind = detect_kind(stripped) if kind == "auto" else kind
    match chosen:
        case "email":
            if "@" not in stripped:
                raise ValueError("that is not an email address")
            return chosen, spell_email(stripped)
        case "phone":
            if not _PHONE_RE.match(stripped):
                raise ValueError("that is not a phone number")
            return chosen, spell_phone(stripped)
        case "amount":
            return chosen, spell_amount(stripped)
        case _:
            return "code", spell_code(stripped)


def build_spell_back_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build the `spell_back` tool bound to `ctx`."""

    @function_tool
    async def spell_back(context: RunContext[Any], text: str, kind: SpellKind = "auto") -> str:
        """Get the words to read back an email, phone number, postcode, reference or amount to the caller.

        Args:
            text: Exactly what the caller gave, e.g. "jo.smith@gmail.com" or "POL-2024-0017".
            kind: What it is; "auto" guesses. Use "code" for postcodes and reference numbers.
        """
        try:
            used, words = spell(text, kind)
        except ValueError as exc:
            raise ToolError(f"Could not spell that: {exc}.") from exc
        ctx.log.debug("builtin_tool.spell_back", call_id=context.function_call.call_id, kind=used)
        return json.dumps({"kind": used, "say": words})

    return spell_back
