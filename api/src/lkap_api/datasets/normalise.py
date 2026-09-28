"""How a key cell becomes a lookup value (V6-16, ``lkap_contracts.datasets``).

The same function normalises a cell at import and a value at lookup, so a lookup matches
whatever the file and the caller wrote the value as:

* ``string`` — Unicode NFKC, case-folded, runs of white space collapsed to one space;
* ``phone`` — ASCII digits only (after NFKC, so full-width digits count), leading zeros
  dropped (a trunk ``0`` or an international ``00``), then the last
  :data:`~lkap_contracts.datasets.PHONE_MATCH_DIGITS` digits: ``+91 98765 43210``,
  ``0091 98765-43210`` and ``098765 43210`` are all ``9876543210``;
* ``email`` — white space removed, case-folded;
* ``number`` — grouping (``,``, ``_``, spaces) removed, read as a decimal and written without
  exponent or trailing zeros: ``1,200.50`` → ``1200.5``; text that is not a number has no value.

A value that normalises to nothing (a blank cell, a phone without digits) is not indexed and
never matches.
"""

from __future__ import annotations

import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Final

from lkap_contracts.datasets import MAX_DATASET_KEY_CHARS, PHONE_MATCH_DIGITS, DatasetKeyType

__all__ = ["normalise_key", "normalise_number"]

_ASCII_DIGITS: Final = frozenset("0123456789")


def normalise_number(text: str) -> str | None:
    """``text`` as a canonical decimal string, or ``None`` when it is not a finite number."""
    cleaned = "".join(ch for ch in text if ch not in ", _  ")
    if not cleaned:
        return None
    try:
        number = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    formatted = format(number.normalize(), "f")
    if "." in formatted:
        formatted = formatted.rstrip("0").rstrip(".")
    return "0" if formatted in ("-0", "") else formatted


def normalise_key(value: str | None, kind: DatasetKeyType) -> str | None:
    """The lookup value of one key cell (or a lookup's input); ``None`` when it has none.

    Args:
        value: The raw text.
        kind: The key column's declared type.

    Returns:
        The normalised value, at most :data:`MAX_DATASET_KEY_CHARS` characters.
    """
    if value is None:
        return None
    text = unicodedata.normalize("NFKC", value).strip()
    if not text:
        return None
    result: str | None
    match kind:
        case "phone":
            digits = "".join(ch for ch in text if ch in _ASCII_DIGITS).lstrip("0")
            result = digits[-PHONE_MATCH_DIGITS:] if digits else None
        case "email":
            result = "".join(text.split()).casefold() or None
        case "number":
            result = normalise_number(text)
        case _:
            result = " ".join(text.casefold().split()) or None
    return result[:MAX_DATASET_KEY_CHARS] if result else None
