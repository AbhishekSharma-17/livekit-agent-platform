"""Datasets: read-only lookup tables an agent's ``dataset`` tool reads (V6-16, D-V6-27).

A workspace uploads a CSV or JSON file (at most :data:`MAX_DATASET_BYTES`,
:data:`MAX_DATASET_ROWS` rows and :data:`MAX_DATASET_COLUMNS` columns) and declares which
columns are **keys**, each typed :data:`DatasetKeyType`. The api stores the bytes in its
storage backend, imports the rows as a background job (``dataset_import``) and indexes every
key cell **normalised** for matching:

* ``string`` — Unicode NFKC, case-folded, runs of white space collapsed;
* ``phone`` — digits only, leading zeros dropped, then the last
  :data:`PHONE_MATCH_DIGITS` digits, so ``+91 98765 43210`` and ``098765 43210`` match;
* ``email`` — trimmed and case-folded;
* ``number`` — a decimal without grouping (``1,200.50`` → ``1200.5``).

A lookup (``POST /v1/datasets/{id}/lookup``, and the worker's
``POST /internal/v1/datasets/{id}/lookup``) matches every given key (``exact`` or ``prefix``
on the normalised value), returns at most :data:`MAX_DATASET_LOOKUP_ROWS` rows in file order,
and only the columns asked for. Datasets are never written from a call.

Column names: every header becomes a ``name`` (lower case letters, digits and ``_``, unique
in the dataset; ``Policy Number`` → ``policy_number``) and keeps its ``label``. Rows, keys and
lookups use the name.
"""

from datetime import datetime
from typing import Final, Literal, Self

from pydantic import BaseModel, Field, model_validator

__all__ = [
    "DATASET_COLUMN_NAME_PATTERN",
    "DATASET_MODELS",
    "MAX_DATASET_BYTES",
    "MAX_DATASET_COLUMNS",
    "MAX_DATASET_KEY_CHARS",
    "MAX_DATASET_KEY_COLUMNS",
    "MAX_DATASET_LOOKUP_ROWS",
    "MAX_DATASET_ROWS",
    "MIN_DATASET_PREFIX_CHARS",
    "PHONE_MATCH_DIGITS",
    "DatasetColumn",
    "DatasetFormat",
    "DatasetKeyColumn",
    "DatasetKeyType",
    "DatasetLookupIn",
    "DatasetLookupOut",
    "DatasetMatch",
    "DatasetOut",
    "DatasetPage",
    "DatasetPreviewOut",
    "DatasetStatus",
    "InternalDatasetLookupIn",
]

#: The largest file a dataset may be made from (5 MiB).
MAX_DATASET_BYTES: Final[int] = 5 * 1024 * 1024
#: The most data rows one dataset holds (the header row not counted).
MAX_DATASET_ROWS: Final[int] = 50_000
#: The most columns one dataset has.
MAX_DATASET_COLUMNS: Final[int] = 64
#: The most key columns one dataset declares (and one tool matches on).
MAX_DATASET_KEY_COLUMNS: Final[int] = 8
#: The most rows one lookup returns.
MAX_DATASET_LOOKUP_ROWS: Final[int] = 20
#: The longest key cell (before normalisation); a longer one is refused at upload.
MAX_DATASET_KEY_CHARS: Final[int] = 256
#: A ``prefix`` lookup needs at least this many normalised characters.
MIN_DATASET_PREFIX_CHARS: Final[int] = 2
#: A ``phone`` key matches on its last this-many digits (the national number, no country code).
PHONE_MATCH_DIGITS: Final[int] = 10
#: A column's ``name``.
DATASET_COLUMN_NAME_PATTERN: Final[str] = r"^[a-z_][a-z0-9_]{0,63}$"

#: How a key column's cells are normalised for matching.
DatasetKeyType = Literal["string", "phone", "email", "number"]
#: The file a dataset was made from.
DatasetFormat = Literal["csv", "json"]
#: ``pending`` while the rows are imported, then ``ready`` (or ``failed``).
DatasetStatus = Literal["pending", "ready", "failed"]
#: How a lookup compares a key: the whole normalised value, or its beginning.
DatasetMatch = Literal["exact", "prefix"]

_ColumnName = Field(pattern=DATASET_COLUMN_NAME_PATTERN)


class DatasetColumn(BaseModel):
    """One column of a dataset."""

    name: str = _ColumnName
    """The column's name in rows, keys and lookups (``policy_number``)."""
    label: str = Field(max_length=200)
    """The header as the file had it (``Policy Number``)."""
    type: DatasetKeyType = "string"
    """A key column's declared type; any other column is ``string`` or ``number`` (inferred)."""
    key: bool = False
    """Whether lookups may match on this column."""


class DatasetKeyColumn(BaseModel):
    """A key column and how its cells are normalised."""

    name: str = _ColumnName
    type: DatasetKeyType = "string"


class DatasetOut(BaseModel):
    """``GET /v1/datasets/{id}``: a dataset without its rows."""

    id: str
    name: str
    slug: str
    format: DatasetFormat
    columns: list[DatasetColumn]
    key_columns: list[DatasetKeyColumn]
    row_count: int
    """Data rows in the file (lookups find them once ``status`` is ``ready``)."""
    sha256: str
    """The uploaded file's digest (the same file uploaded twice has the same one)."""
    status: DatasetStatus
    progress: float | None = None
    """The import's share done, from ``0`` to ``1`` (``None`` when unknown)."""
    error: str | None = None
    """Why the import failed, in plain words (``status == "failed"`` only)."""
    created_at: datetime
    updated_at: datetime


class DatasetPage(BaseModel):
    """``GET /v1/datasets``."""

    items: list[DatasetOut]
    total: int


class DatasetPreviewOut(BaseModel):
    """``GET /v1/datasets/{id}/rows``: a page of rows in file order."""

    dataset_id: str
    columns: list[DatasetColumn]
    rows: list[dict[str, str | None]]
    total: int
    offset: int


class DatasetLookupIn(BaseModel):
    """``POST /v1/datasets/{id}/lookup``: find rows by their key columns."""

    keys: dict[str, str] = Field(min_length=1, max_length=MAX_DATASET_KEY_COLUMNS)
    """Key column name → the value to match; a row must match every one."""
    match: DatasetMatch = "exact"
    return_columns: list[str] = Field(default=[], max_length=MAX_DATASET_COLUMNS)
    """The columns each row carries (empty: all of them)."""
    max_rows: int = Field(default=5, ge=1, le=MAX_DATASET_LOOKUP_ROWS)

    @model_validator(mode="after")
    def _bounded_values(self) -> Self:
        for column, value in self.keys.items():
            if len(value) > MAX_DATASET_KEY_CHARS:
                raise ValueError(
                    f"the value for '{column}' is longer than {MAX_DATASET_KEY_CHARS} characters"
                )
        return self


class InternalDatasetLookupIn(DatasetLookupIn):
    """``POST /internal/v1/datasets/{id}/lookup`` (the worker): the session decides the workspace."""

    session_id: str = Field(min_length=1, max_length=64)


class DatasetLookupOut(BaseModel):
    """What a lookup found (rows in file order, only the asked-for columns)."""

    dataset_id: str
    dataset_name: str
    match: DatasetMatch
    rows: list[dict[str, str | None]]
    truncated: bool = False
    """More rows matched than ``max_rows``."""


#: The dataset models, registered in ``export.py`` with one line (``**DATASET_MODELS``).
DATASET_MODELS: dict[str, type[BaseModel]] = {
    "DatasetColumn": DatasetColumn,
    "DatasetKeyColumn": DatasetKeyColumn,
    "DatasetOut": DatasetOut,
    "DatasetPage": DatasetPage,
    "DatasetPreviewOut": DatasetPreviewOut,
    "DatasetLookupIn": DatasetLookupIn,
    "InternalDatasetLookupIn": InternalDatasetLookupIn,
    "DatasetLookupOut": DatasetLookupOut,
}
