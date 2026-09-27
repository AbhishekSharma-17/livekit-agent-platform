"""Deterministic masking of emails, card numbers and long numbers (V5-30, P §4.2 C10).

The first pass of the post-call scrub (:mod:`lkap_api.privacy.scrub`): pure
functions, no I/O, the same input always gives the same output. What is masked:

* an email address → ``[email]``;
* a run of 13-19 digits (spaces or dashes allowed between them) that passes
  the Luhn check → ``[card number]``;
* any other run of 6 or more digits (phone, account, policy, social-security
  and card-like numbers; ``+``, spaces, dots, dashes and brackets allowed
  between them) → ``[number]``.

Short numbers (amounts like ``1,250``, times, ages, a four-digit year) stay:
the transcript keeps its meaning. Names and addresses are the optional LLM
pass's job (:mod:`lkap_api.privacy.llm`), not this module's.

:func:`scrub_value` walks a JSON value (an event payload, a UI state) and
masks every string except identifiers: a key named ``id``, ``ts`` or ``type``,
or ending in ``_id`` / ``_ids``, is kept as it is.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Final

__all__ = [
    "CARD_TOKEN",
    "EMAIL_TOKEN",
    "NUMBER_TOKEN",
    "luhn_valid",
    "redact_text",
    "scrub_value",
]

EMAIL_TOKEN: Final[str] = "[email]"
CARD_TOKEN: Final[str] = "[card number]"
NUMBER_TOKEN: Final[str] = "[number]"

_EMAIL_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}(?![\w-])"
)
#: 13-19 digits, one optional space or dash between any two.
_CARD_RE: Final[re.Pattern[str]] = re.compile(r"(?<![\w+])\d(?:[ -]?\d){12,18}(?![\w])")
#: 6 or more digits, up to two separators between any two, an optional leading `+` or `(`.
_NUMBER_RE: Final[re.Pattern[str]] = re.compile(r"(?<![\w])[+(]?\d(?:[\s().-]{0,2}\d){5,}(?![\w])")

_KEPT_KEYS: Final[frozenset[str]] = frozenset({"id", "ts", "type"})


def luhn_valid(digits: str) -> bool:
    """Whether ``digits`` (digits only) passes the Luhn checksum card numbers carry."""
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def redact_text(text: str, counts: Counter[str] | None = None) -> str:
    """``text`` with emails, card numbers and long numbers masked.

    Args:
        text: Any text (a transcript turn, an event string).
        counts: Incremented per kind (``email``, ``card``, ``number``) when given.

    Returns:
        The masked text; ``text`` itself when nothing matched.
    """
    tally: Counter[str] = counts if counts is not None else Counter()

    def _email(_: re.Match[str]) -> str:
        tally["email"] += 1
        return EMAIL_TOKEN

    def _card(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        if not luhn_valid(digits):
            return match.group(0)  # left for the number pass
        tally["card"] += 1
        return CARD_TOKEN

    def _number(_: re.Match[str]) -> str:
        tally["number"] += 1
        return NUMBER_TOKEN

    masked = _EMAIL_RE.sub(_email, text)
    masked = _CARD_RE.sub(_card, masked)
    return _NUMBER_RE.sub(_number, masked)


def _kept(key: str | None) -> bool:
    return key is not None and (key in _KEPT_KEYS or key.endswith(("_id", "_ids")))


def scrub_value(value: Any, counts: Counter[str] | None = None, *, key: str | None = None) -> Any:
    """A copy of a JSON value with every string masked by :func:`redact_text`, identifiers kept.

    Args:
        value: A JSON value (dict, list, str, number, bool, None).
        counts: Incremented per masked kind when given.
        key: The dict key ``value`` sits under (list items inherit it).

    Returns:
        The masked copy.
    """
    if isinstance(value, str):
        return value if _kept(key) else redact_text(value, counts)
    if isinstance(value, dict):
        return {k: scrub_value(v, counts, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub_value(item, counts, key=key) for item in value]
    return value
