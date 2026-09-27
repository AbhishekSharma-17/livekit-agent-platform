"""Post-call fields as the api hands them out (V5-30, P §4.2 C23).

The worker's judge stores the filled fields in ``session_qa.raw["fields"]``
(``lkap_agent.qa.extract_fields``). :func:`qa_fields` reads them back for
``QaOut.fields``, the ``session.qa_completed`` webhook and the sessions CSV;
:func:`csv_cell` renders one value for a spreadsheet without letting it run as
a formula.
"""

from __future__ import annotations

from typing import Any, Final

__all__ = ["CSV_FORMULA_PREFIXES", "csv_cell", "qa_fields"]

#: A cell starting with one of these is a formula to a spreadsheet; it gets a leading `'`.
CSV_FORMULA_PREFIXES: Final[tuple[str, ...]] = ("=", "+", "-", "@", "\t", "\r")


def qa_fields(raw: dict[str, Any] | None) -> dict[str, Any]:
    """``raw["fields"]`` when it is a ``{name: value}`` object, else ``{}``."""
    if not isinstance(raw, dict):
        return {}
    fields = raw.get("fields")
    if not isinstance(fields, dict):
        return {}
    return {str(name): value for name, value in fields.items()}


def csv_cell(value: Any) -> str:
    """One CSV cell: ``""`` for null, ``true``/``false``, numbers as written, text formula-safe."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    text = str(value)
    return f"'{text}" if text.startswith(CSV_FORMULA_PREFIXES) else text
