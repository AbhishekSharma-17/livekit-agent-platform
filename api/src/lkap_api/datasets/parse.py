"""Reading an uploaded CSV or JSON file into dataset columns and rows (V6-16).

Pure functions: the upload route calls :func:`parse_dataset` to refuse a bad file before
anything is stored (a 422 with a plain sentence), and the ``dataset_import`` job calls it
again on the stored bytes with the columns the route recorded, so both read the file the same
way.

* **CSV** — UTF-8 (a byte-order mark is dropped); a file that is not UTF-8 is read as
  Windows-1252, what spreadsheet programs write. The separator is detected among ``,``,
  ``;``, tab and ``|`` (``.tsv`` is always tab). The first row is the header. Blank rows are
  skipped; a row with more non-empty cells than the header is refused.
* **JSON** — a list of objects, or an object whose ``rows``, ``items`` or ``data`` is one.
  The columns are the objects' keys in first-seen order; numbers and booleans become text,
  nested values compact JSON.

Cells are text (a blank cell is ``None``); nothing in a cell is ever evaluated, so a
spreadsheet formula stays the text it is (:func:`neutralise_cell` guards the export).
"""

from __future__ import annotations

import csv
import io
import json
import re
import unicodedata
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Final

from lkap_contracts.datasets import (
    MAX_DATASET_COLUMNS,
    MAX_DATASET_KEY_CHARS,
    MAX_DATASET_KEY_COLUMNS,
    MAX_DATASET_ROWS,
    DatasetColumn,
    DatasetFormat,
    DatasetKeyType,
)

from lkap_api.datasets.normalise import normalise_number
from lkap_api.errors import ApiError, UnprocessableEntityError

__all__ = [
    "DATASET_SUFFIXES",
    "DatasetFileError",
    "ParsedDataset",
    "UnsupportedDatasetFileError",
    "column_name",
    "dataset_format",
    "neutralise_cell",
    "parse_dataset",
]

#: The file extensions a dataset may be uploaded as, and how each is read.
DATASET_SUFFIXES: Final[dict[str, DatasetFormat]] = {
    ".csv": "csv",
    ".tsv": "csv",
    ".txt": "csv",
    ".json": "json",
}

_SEPARATORS: Final = ",;\t|"
_MAX_LABEL_CHARS: Final = 200
_MAX_NAME_CHARS: Final = 64
_NAME_UNSAFE_RE: Final = re.compile(r"[^a-z0-9]+")
_UNSAFE_CATEGORIES: Final = frozenset({"Cc", "Cf"})
#: The characters a spreadsheet program reads as the start of a formula (OWASP CSV injection).
_FORMULA_STARTS: Final = ("=", "+", "-", "@", "\t", "\r")
#: A cell that is only a signed number or a phone number is data, not a formula.
_PLAIN_NUMBER_RE: Final = re.compile(r"^[+-]?[0-9][0-9 ().-]*$")


class DatasetFileError(UnprocessableEntityError):
    """422 — the file cannot be made into a dataset; the message says why in plain words."""

    code = "invalid_dataset"


class UnsupportedDatasetFileError(ApiError):
    """415 — the file is not a ``.csv``, ``.tsv``, ``.txt`` or ``.json``."""

    status_code = 415
    code = "unsupported_media_type"


@dataclass(frozen=True, slots=True)
class ParsedDataset:
    """A file read as columns and rows (cells by column ``name``)."""

    format: DatasetFormat
    columns: list[DatasetColumn]
    rows: list[dict[str, str | None]]

    @property
    def key_columns(self) -> list[DatasetColumn]:
        """The columns lookups may match on, in file order."""
        return [column for column in self.columns if column.key]


def dataset_format(filename: str) -> DatasetFormat:
    """The format of an upload by its extension.

    Raises:
        UnsupportedDatasetFileError: Any other extension.
    """
    suffix = PurePosixPath(filename.lower()).suffix
    found = DATASET_SUFFIXES.get(suffix)
    if found is None:
        raise UnsupportedDatasetFileError(
            "a lookup table is made from a .csv, .tsv or .json file",
            details={"allowed": sorted(DATASET_SUFFIXES)},
        )
    return found


def _clean_label(raw: object) -> str:
    text = "".join(ch for ch in str(raw or "") if unicodedata.category(ch) not in _UNSAFE_CATEGORIES)
    return " ".join(text.split())[:_MAX_LABEL_CHARS]


def column_name(label: str, index: int) -> str:
    """A header as a column name: ``Policy Number`` → ``policy_number`` (``column_3`` if empty)."""
    ascii_text = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode("ascii").lower()
    name = _NAME_UNSAFE_RE.sub("_", ascii_text).strip("_")
    if not name:
        name = f"column_{index + 1}"
    if name[0].isdigit():
        name = f"c_{name}"
    return name[:_MAX_NAME_CHARS].rstrip("_")


def _unique_names(labels: list[str]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for index, label in enumerate(labels):
        base = column_name(label, index)
        name, counter = base, 2
        while name in seen:
            suffix = f"_{counter}"
            name = f"{base[: _MAX_NAME_CHARS - len(suffix)]}{suffix}"
            counter += 1
        seen.add(name)
        names.append(name)
    return names


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _cell(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        text = value.replace("\x00", "")
        return text if text.strip() else None
    if isinstance(value, int | float):
        return json.dumps(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _too_many_rows() -> DatasetFileError:
    return DatasetFileError(
        f"this file has more than {MAX_DATASET_ROWS:,} rows. A lookup table holds at most "
        f"{MAX_DATASET_ROWS:,}, so split the file or remove rows",
        details={"reason": "too_many_rows", "max_rows": MAX_DATASET_ROWS},
    )


def _csv_rows(text: str, *, tab: bool) -> tuple[list[str], Iterator[list[str]]]:
    sample = text[:8192]
    delimiter = "\t"
    if not tab:
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=_SEPARATORS).delimiter
        except csv.Error:
            delimiter = ","
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    try:
        header = next(reader)
    except StopIteration:
        raise DatasetFileError("this file is empty", details={"reason": "empty"}) from None
    return header, reader


def _read_csv(text: str, *, tab: bool) -> tuple[list[str], list[list[object]]]:
    header, reader = _csv_rows(text, tab=tab)
    width = len(header)
    rows: list[list[object]] = []
    try:
        for cells in reader:
            if not any(cell.strip() for cell in cells):
                continue
            if len(cells) > width and any(cell.strip() for cell in cells[width:]):
                raise DatasetFileError(
                    f"row {len(rows) + 1} has more cells than the header has columns",
                    details={"reason": "ragged_row", "row": len(rows) + 1},
                )
            rows.append(list(cells[:width]))
            if len(rows) > MAX_DATASET_ROWS:
                raise _too_many_rows()
    except csv.Error as exc:
        raise DatasetFileError(
            f"this CSV file could not be read ({exc})", details={"reason": "unreadable"}
        ) from exc
    return header, rows


def _read_json(text: str) -> tuple[list[str], list[list[object]]]:
    try:
        payload: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DatasetFileError(
            f"this JSON file could not be read (line {exc.lineno})", details={"reason": "unreadable"}
        ) from exc
    except RecursionError as exc:
        # V6-21 (S6-17): a deeply nested document is a 422, not a 500.
        raise DatasetFileError(
            "this JSON file is nested too deeply to read", details={"reason": "unreadable"}
        ) from exc
    if isinstance(payload, Mapping):
        payload = next(
            (payload[key] for key in ("rows", "items", "data") if isinstance(payload.get(key), list)), None
        )
    if not isinstance(payload, list):
        raise DatasetFileError(
            "a JSON lookup table is a list of objects (or an object with a 'rows' list)",
            details={"reason": "not_a_list"},
        )
    if len(payload) > MAX_DATASET_ROWS:
        raise _too_many_rows()
    header: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(payload):
        if not isinstance(item, Mapping):
            raise DatasetFileError(
                f"item {index + 1} of the list is not an object", details={"reason": "not_an_object"}
            )
        for key in item:
            if str(key) not in seen:
                seen.add(str(key))
                header.append(str(key))
                if len(header) > MAX_DATASET_COLUMNS:
                    break
    rows = [[item.get(key) for key in header] for item in payload]
    return header, rows


def neutralise_cell(value: str | None) -> str:
    """A cell made safe for a spreadsheet program to open (CSV injection, OWASP).

    A cell that starts with ``=``, ``+``, ``-``, ``@``, a tab or a carriage return is prefixed
    with ``'`` so the program shows it as text instead of evaluating it, unless it is only a
    signed number or a phone number (``-12.5``, ``+91 98765 43210``).
    """
    if value is None:
        return ""
    if value.startswith(_FORMULA_STARTS) and _PLAIN_NUMBER_RE.match(value) is None:
        return "'" + value
    return value


def _resolve_keys(
    labels: list[str], names: list[str], key_spec: Mapping[str, DatasetKeyType]
) -> dict[str, DatasetKeyType]:
    """``key_spec`` (by column name or header) as ``{name: type}``."""
    if not key_spec:
        raise DatasetFileError(
            "name at least one key column (the column a lookup finds rows by)",
            details={"reason": "no_key_columns"},
        )
    if len(key_spec) > MAX_DATASET_KEY_COLUMNS:
        raise DatasetFileError(
            f"at most {MAX_DATASET_KEY_COLUMNS} key columns", details={"reason": "too_many_key_columns"}
        )
    by_label = {label.casefold(): name for label, name in zip(labels, names, strict=True)}
    resolved: dict[str, DatasetKeyType] = {}
    for wanted, kind in key_spec.items():
        name = wanted if wanted in names else by_label.get(" ".join(wanted.split()).casefold())
        if name is None:
            raise DatasetFileError(
                f"the file has no column '{wanted}'. Its columns are: {', '.join(labels)}",
                details={"reason": "unknown_key_column", "column": wanted},
            )
        resolved[name] = kind
    return resolved


def parse_dataset(
    data: bytes, format: DatasetFormat, key_spec: Mapping[str, DatasetKeyType], *, tab: bool = False
) -> ParsedDataset:
    """Read an upload into columns and rows, refusing what a lookup table cannot hold.

    Args:
        data: The file's bytes (the caller has checked the size).
        format: ``csv`` or ``json`` (:func:`dataset_format`).
        key_spec: The key columns, by column name or header, and their types.
        tab: A ``.tsv`` file (tab-separated, no detection).

    Returns:
        The columns (keys marked and typed, other columns ``number`` when every cell is one)
        and the rows.

    Raises:
        DatasetFileError: Empty, unreadable, more than :data:`MAX_DATASET_ROWS` rows or
            :data:`MAX_DATASET_COLUMNS` columns, an unknown key column, or a key cell longer
            than :data:`MAX_DATASET_KEY_CHARS` characters.
    """
    text = _decode(data)
    raw_header, raw_rows = _read_json(text) if format == "json" else _read_csv(text, tab=tab)
    if len(raw_header) > MAX_DATASET_COLUMNS:
        raise DatasetFileError(
            f"this file has more than {MAX_DATASET_COLUMNS} columns", details={"reason": "too_many_columns"}
        )
    if not raw_header or not raw_rows:
        raise DatasetFileError("this file has no data rows", details={"reason": "empty"})
    labels = [_clean_label(label) or f"Column {index + 1}" for index, label in enumerate(raw_header)]
    names = _unique_names(labels)
    keys = _resolve_keys(labels, names, key_spec)

    rows: list[dict[str, str | None]] = []
    for cells in raw_rows:
        padded = [*cells, *([None] * (len(names) - len(cells)))]
        rows.append({name: _cell(value) for name, value in zip(names, padded, strict=True)})

    columns: list[DatasetColumn] = []
    for name, label in zip(names, labels, strict=True):
        values = [value for row in rows if (value := row[name]) is not None]
        if name in keys:
            for position, row in enumerate(rows, start=1):
                value = row[name]
                if value is not None and len(value) > MAX_DATASET_KEY_CHARS:
                    raise DatasetFileError(
                        f"row {position}: the '{label}' value is longer than {MAX_DATASET_KEY_CHARS} "
                        "characters, too long for a key column",
                        details={"reason": "key_too_long", "row": position, "column": name},
                    )
            columns.append(DatasetColumn(name=name, label=label, type=keys[name], key=True))
            continue
        numeric = bool(values) and all(normalise_number(value) is not None for value in values)
        columns.append(DatasetColumn(name=name, label=label, type="number" if numeric else "string"))
    return ParsedDataset(format=format, columns=columns, rows=rows)
