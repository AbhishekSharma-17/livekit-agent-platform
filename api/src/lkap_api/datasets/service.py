"""Datasets: create, import, look up, preview, export and delete (V6-16, D-V6-27).

Tenant rule: every read of ``datasets`` names the workspace (the admin routes use the
caller's, the worker's route the session's), so a lookup never reads another workspace's
rows; ``dataset_rows`` and ``dataset_keys`` are reached only through a dataset already loaded
that way. The import job carries only the dataset id (a deliberately cross-workspace read,
like the knowledge ingest).

Privacy: key values (a caller's phone number) are never logged; only column names and counts.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import unicodedata
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any, Final

from fastapi import BackgroundTasks
from lkap_contracts.datasets import (
    MIN_DATASET_PREFIX_CHARS,
    DatasetColumn,
    DatasetFormat,
    DatasetKeyColumn,
    DatasetKeyType,
    DatasetLookupIn,
    DatasetLookupOut,
    DatasetOut,
    DatasetPreviewOut,
)
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.auth.audit import record
from lkap_api.datasets.normalise import normalise_key
from lkap_api.datasets.parse import DatasetFileError, ParsedDataset, neutralise_cell, parse_dataset
from lkap_api.db.guard import CROSS_WORKSPACE_OPTION
from lkap_api.db.models import Dataset, DatasetKey, DatasetRow, Job, Tool, new_id, utcnow
from lkap_api.db.session import Database
from lkap_api.errors import ApiError, ConflictError, NotFoundError, UnprocessableEntityError
from lkap_api.jobs.kinds import DATASET_IMPORT
from lkap_api.jobs.service import JobsService
from lkap_api.limits import MAX_DATASET_ROWS_PER_WORKSPACE, MAX_DATASETS_PER_WORKSPACE
from lkap_api.logging import get_logger
from lkap_api.storage.base import StorageBackend

__all__ = [
    "DatasetQuotaExceededError",
    "check_quota",
    "create_dataset",
    "dataset_out",
    "delete_dataset",
    "export_csv",
    "import_rows",
    "load_dataset",
    "lookup",
    "preview",
    "storage_key_for",
    "tools_using",
]

log = get_logger(__name__)

#: Rows inserted (and committed, with a progress update) per step of the import.
IMPORT_BATCH: Final = 1000
_SLUG_UNSAFE_RE: Final = re.compile(r"[^a-z0-9]+")
_MAX_SLUG_CHARS: Final = 60


class DatasetQuotaExceededError(ApiError):
    """409 — the workspace already holds as many datasets or rows as it may."""

    status_code = 409
    code = "quota_exceeded"


#: The upload suffixes a storage key keeps (the import reads ``.tsv`` off the key).
_STORED_SUFFIXES: Final = frozenset({".csv", ".tsv", ".json"})


def storage_key_for(workspace_id: str, dataset_id: str, filename: str) -> str:
    """Where a dataset's uploaded bytes live in the storage backend.

    V6-21 (S6-18): ``datasets/<workspace>/<dataset>/source<suffix>``, bounded whatever the
    upload was called (the name is never stored, echoed or used as a path).
    """
    suffix = PurePosixPath(filename.lower()).suffix
    return f"datasets/{workspace_id}/{dataset_id}/source{suffix if suffix in _STORED_SUFFIXES else ''}"


def _slug_base(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").lower()
    return _SLUG_UNSAFE_RE.sub("-", text).strip("-")[:_MAX_SLUG_CHARS].strip("-") or "dataset"


async def _unique_slug(db: AsyncSession, workspace_id: str, name: str) -> str:
    base = _slug_base(name)
    taken = set(
        (
            await db.execute(
                select(Dataset.slug).where(
                    Dataset.workspace_id == workspace_id, Dataset.slug.startswith(base)
                )
            )
        ).scalars()
    )
    slug, counter = base, 2
    while slug in taken:
        slug = f"{base}-{counter}"
        counter += 1
    return slug


async def load_dataset(db: AsyncSession, workspace_id: str, dataset_id: str) -> Dataset:
    """A dataset of ``workspace_id`` (404 for an unknown id or another workspace's)."""
    row = await db.scalar(
        select(Dataset).where(Dataset.id == dataset_id, Dataset.workspace_id == workspace_id)
    )
    if row is None:
        raise NotFoundError(f"unknown lookup table '{dataset_id}'")
    return row


async def _latest_job_payload(db: AsyncSession, dataset_id: str) -> Mapping[str, Any]:
    payload = await db.scalar(
        select(Job.payload)
        .where(Job.kind == DATASET_IMPORT, Job.payload["dataset_id"].as_string() == dataset_id)
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    return payload if isinstance(payload, Mapping) else {}


async def dataset_out(db: AsyncSession, row: Dataset) -> DatasetOut:
    """The api shape of a dataset; a pending or failed one reads its job for progress and error."""
    progress: float | None = 1.0 if row.status == "ready" else None
    error: str | None = None
    if row.status != "ready":
        payload = await _latest_job_payload(db, row.id)
        done, total = payload.get("done"), payload.get("total")
        if isinstance(done, int) and isinstance(total, int) and total > 0:
            progress = min(done / total, 1.0)
        elif row.status == "pending":
            progress = 0.0
        if row.status == "failed":
            error = str(payload.get("error") or "the import failed")
    return DatasetOut(
        id=row.id,
        name=row.name,
        slug=row.slug,
        format=row.format,
        columns=[DatasetColumn.model_validate(column) for column in row.columns or []],
        key_columns=[DatasetKeyColumn.model_validate(column) for column in row.key_columns or []],
        row_count=row.row_count,
        sha256=row.sha256,
        status=row.status,
        progress=progress,
        error=error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def check_quota(db: AsyncSession, workspace_id: str, rows: int) -> None:
    """Refuse a dataset past the workspace's dataset or row quota.

    Raises:
        DatasetQuotaExceededError: 409 with the limit in ``details``.
    """
    count, used = (
        await db.execute(
            select(func.count(Dataset.id), func.coalesce(func.sum(Dataset.row_count), 0)).where(
                Dataset.workspace_id == workspace_id
            )
        )
    ).one()
    if int(count) >= MAX_DATASETS_PER_WORKSPACE:
        raise DatasetQuotaExceededError(
            f"this workspace already has {MAX_DATASETS_PER_WORKSPACE} lookup tables. Delete one first",
            details={"limit": "datasets_per_workspace", "max": MAX_DATASETS_PER_WORKSPACE},
        )
    if int(used) + rows > MAX_DATASET_ROWS_PER_WORKSPACE:
        raise DatasetQuotaExceededError(
            f"this workspace's lookup tables would hold more than {MAX_DATASET_ROWS_PER_WORKSPACE:,} rows. "
            "Delete a table first",
            details={"limit": "rows_per_workspace", "max": MAX_DATASET_ROWS_PER_WORKSPACE},
        )


async def create_dataset(
    db: AsyncSession,
    storage: StorageBackend,
    jobs: JobsService,
    background_tasks: BackgroundTasks | None,
    *,
    workspace_id: str,
    name: str,
    filename: str,
    parsed: ParsedDataset,
    data: bytes,
) -> Dataset:
    """Store the upload, create the ``pending`` dataset row and enqueue its import.

    The row is committed before the job is enqueued: the job opens its own connection.
    """
    await check_quota(db, workspace_id, len(parsed.rows))
    dataset_id = new_id()
    storage_key = storage_key_for(workspace_id, dataset_id, filename)
    await storage.put(
        storage_key, data, content_type="application/json" if parsed.format == "json" else "text/csv"
    )
    row = Dataset(
        id=dataset_id,
        workspace_id=workspace_id,
        name=name,
        slug=await _unique_slug(db, workspace_id, name),
        format=parsed.format,
        columns=[column.model_dump(mode="json") for column in parsed.columns],
        key_columns=[
            DatasetKeyColumn(name=column.name, type=column.type).model_dump(mode="json")
            for column in parsed.key_columns
        ],
        row_count=len(parsed.rows),
        storage_key=storage_key,
        sha256=hashlib.sha256(data).hexdigest(),
        status="pending",
    )
    db.add(row)
    await db.commit()  # the object keeps its values (no expiry on commit)
    await jobs.enqueue(
        DATASET_IMPORT,
        {"dataset_id": dataset_id, "done": 0, "total": len(parsed.rows)},
        background_tasks=background_tasks,
    )
    log.info(
        "dataset_created",
        dataset_id=dataset_id,
        rows=len(parsed.rows),
        columns=len(parsed.columns),
        key_columns=[column.name for column in parsed.key_columns],
    )
    return row


# ------------------------------------------------------------------ the import (job body)


async def _update_job(session: AsyncSession, dataset_id: str, **fields: Any) -> None:
    job = (
        (
            await session.execute(
                select(Job)
                .where(Job.kind == DATASET_IMPORT, Job.payload["dataset_id"].as_string() == dataset_id)
                .order_by(Job.created_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    if job is not None:
        job.payload = {**dict(job.payload or {}), **fields}


async def _clear_rows(session: AsyncSession, dataset_id: str) -> None:
    await session.execute(delete(DatasetKey).where(DatasetKey.dataset_id == dataset_id))
    await session.execute(delete(DatasetRow).where(DatasetRow.dataset_id == dataset_id))


async def _fail(session: AsyncSession, dataset_id: str, workspace_id: str, message: str) -> None:
    """Undo a partial import and record why it failed (the dataset id, not the expired row).

    V6-21 (S6-19): one ``dataset.import_failed`` audit row with the reason, never a cell value.
    """
    await session.rollback()
    await _clear_rows(session, dataset_id)
    await session.execute(
        update(Dataset)
        .where(Dataset.id == dataset_id)
        .values(status="failed", updated_at=utcnow())
        .execution_options(**{CROSS_WORKSPACE_OPTION: True})
    )
    await _update_job(session, dataset_id, error=message)
    record(
        session,
        workspace_id=workspace_id,
        actor_type="system",
        actor_id=None,
        action="dataset.import_failed",
        target_type="dataset",
        target_id=dataset_id,
        payload={"dataset_id": dataset_id, "reason": message[:200]},
    )
    await session.commit()
    log.warning("dataset_import_failed", dataset_id=dataset_id, reason=message[:200])


async def import_rows(database: Database, storage: StorageBackend, dataset_id: str) -> None:
    """Import a stored dataset's rows and keys in batches, then mark it ``ready`` (or ``failed``).

    Re-running replaces whatever an earlier attempt inserted. Each batch commits with a
    progress update on the job row (``done``/``total``), so a reader sees it move.
    """
    async with database.session() as session:
        dataset = (
            await session.execute(
                select(Dataset)
                .where(Dataset.id == dataset_id)
                .execution_options(**{CROSS_WORKSPACE_OPTION: True})
            )
        ).scalar_one_or_none()
        if dataset is None:
            log.info("dataset_import_gone", dataset_id=dataset_id)
            return
        workspace_id = dataset.workspace_id  # read before a rollback expires the row
        try:
            data = await storage.get(dataset.storage_key)
        except FileNotFoundError:
            await _fail(
                session,
                dataset_id,
                workspace_id,
                "the uploaded file is missing from storage. Upload it again",
            )
            return
        key_spec: dict[str, DatasetKeyType] = {
            str(column["name"]): column["type"] for column in dataset.key_columns or []
        }
        try:
            fmt: DatasetFormat = "json" if dataset.format == "json" else "csv"
            tab = PurePosixPath(dataset.storage_key).suffix.lower() == ".tsv"
            parsed = parse_dataset(data, fmt, key_spec, tab=tab)
        except (DatasetFileError, UnprocessableEntityError) as exc:
            await _fail(session, dataset_id, workspace_id, exc.message)
            return
        keys = [(column.name, column.type) for column in parsed.key_columns]
        total = len(parsed.rows)
        try:
            await _clear_rows(session, dataset_id)
            for start in range(0, total, IMPORT_BATCH):
                row_values: list[dict[str, Any]] = []
                key_values: list[dict[str, Any]] = []
                for ordinal, cells in enumerate(parsed.rows[start : start + IMPORT_BATCH], start=start):
                    row_id = new_id()
                    normalised = {name: normalise_key(cells.get(name), kind) for name, kind in keys}
                    row_values.append(
                        {
                            "id": row_id,
                            "dataset_id": dataset_id,
                            "ordinal": ordinal,
                            "keys": normalised,
                            "row": cells,
                        }
                    )
                    key_values.extend(
                        {"row_id": row_id, "column_name": name, "dataset_id": dataset_id, "value": value}
                        for name, value in normalised.items()
                        if value is not None
                    )
                await session.execute(insert(DatasetRow), row_values)
                if key_values:
                    await session.execute(insert(DatasetKey), key_values)
                await _update_job(session, dataset_id, done=min(start + IMPORT_BATCH, total), total=total)
                await session.commit()
            dataset.status = "ready"
            dataset.row_count = total
            dataset.updated_at = utcnow()
            await session.commit()
        except Exception as exc:  # noqa: BLE001 - recorded on the dataset, never raised to the runner
            await _fail(
                session,
                dataset_id,
                workspace_id,
                f"the import failed ({type(exc).__name__}). Upload the file again",
            )
            return
    log.info("dataset_imported", dataset_id=dataset_id, rows=total, key_columns=[name for name, _ in keys])


# ------------------------------------------------------------------ reads


def _columns(dataset: Dataset) -> list[DatasetColumn]:
    return [DatasetColumn.model_validate(column) for column in dataset.columns or []]


async def lookup(db: AsyncSession, dataset: Dataset, request: DatasetLookupIn) -> DatasetLookupOut:
    """Rows of ``dataset`` matching every key of ``request``, in file order, bounded.

    Raises:
        ConflictError: 409 while the dataset is importing (or after a failed import).
        UnprocessableEntityError: 422 for a column that is not a key or not a column, or a
            ``prefix`` value shorter than :data:`MIN_DATASET_PREFIX_CHARS` once normalised.
    """
    if dataset.status != "ready":
        message = (
            "this lookup table is still being imported. Try again in a moment"
            if dataset.status == "pending"
            else "this lookup table's import failed. Upload the file again"
        )
        raise ConflictError(message, details={"dataset_id": dataset.id, "status": dataset.status})
    key_types = {str(column["name"]): column["type"] for column in dataset.key_columns or []}
    column_names = [column.name for column in _columns(dataset)]
    unknown_keys = sorted(set(request.keys) - set(key_types))
    if unknown_keys:
        raise UnprocessableEntityError(
            f"'{unknown_keys[0]}' is not a key column of this lookup table. Its keys are: "
            f"{', '.join(key_types)}",
            details={"reason": "not_a_key_column", "column": unknown_keys[0]},
        )
    unknown_columns = sorted(set(request.return_columns) - set(column_names))
    if unknown_columns:
        raise UnprocessableEntityError(
            f"'{unknown_columns[0]}' is not a column of this lookup table",
            details={"reason": "unknown_column", "column": unknown_columns[0]},
        )
    statement = select(DatasetRow.row).where(DatasetRow.dataset_id == dataset.id)
    found_nothing = False
    for column, raw in request.keys.items():
        value = normalise_key(raw, key_types[column])
        if value is None:
            found_nothing = True
            break
        if request.match == "prefix" and len(value) < MIN_DATASET_PREFIX_CHARS:
            raise UnprocessableEntityError(
                f"give at least {MIN_DATASET_PREFIX_CHARS} characters of '{column}' "
                "to look it up by its start",
                details={"reason": "prefix_too_short", "column": column},
            )
        condition = (
            DatasetKey.value.startswith(value, autoescape=True)
            if request.match == "prefix"
            else DatasetKey.value == value
        )
        statement = statement.where(
            DatasetRow.id.in_(
                select(DatasetKey.row_id).where(
                    DatasetKey.dataset_id == dataset.id, DatasetKey.column_name == column, condition
                )
            )
        )
    rows: list[dict[str, Any]] = []
    if not found_nothing:
        rows = list(
            (await db.execute(statement.order_by(DatasetRow.ordinal).limit(request.max_rows + 1))).scalars()
        )
    wanted = request.return_columns or column_names
    shaped = [{name: (row or {}).get(name) for name in wanted} for row in rows[: request.max_rows]]
    return DatasetLookupOut(
        dataset_id=dataset.id,
        dataset_name=dataset.name,
        match=request.match,
        rows=shaped,
        truncated=len(rows) > request.max_rows,
    )


async def preview(db: AsyncSession, dataset: Dataset, *, offset: int, limit: int) -> DatasetPreviewOut:
    """A page of ``dataset``'s rows in file order (none until the import finishes)."""
    rows = list(
        (
            await db.execute(
                select(DatasetRow.row)
                .where(DatasetRow.dataset_id == dataset.id)
                .order_by(DatasetRow.ordinal)
                .offset(offset)
                .limit(limit)
            )
        ).scalars()
    )
    total = int(
        await db.scalar(select(func.count(DatasetRow.id)).where(DatasetRow.dataset_id == dataset.id)) or 0
    )
    columns = _columns(dataset)
    return DatasetPreviewOut(
        dataset_id=dataset.id,
        columns=columns,
        rows=[{column.name: (row or {}).get(column.name) for column in columns} for row in rows],
        total=total,
        offset=offset,
    )


async def export_csv(db: AsyncSession, dataset: Dataset) -> str:
    """The dataset as CSV (the original headers), every cell neutralised for spreadsheet programs."""
    columns = _columns(dataset)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow([neutralise_cell(column.label) for column in columns])
    result = await db.execute(
        select(DatasetRow.row).where(DatasetRow.dataset_id == dataset.id).order_by(DatasetRow.ordinal)
    )
    for row in result.scalars():
        writer.writerow([neutralise_cell((row or {}).get(column.name)) for column in columns])
    return buffer.getvalue()


async def tools_using(db: AsyncSession, workspace_id: str, dataset_id: str) -> list[str]:
    """Names of the workspace's ``dataset`` tools that read ``dataset_id``."""
    rows = (
        await db.execute(
            select(Tool.name, Tool.definition).where(
                Tool.workspace_id == workspace_id, Tool.kind == "dataset"
            )
        )
    ).tuples()
    return sorted(
        name
        for name, definition in rows
        if isinstance(definition, dict) and definition.get("dataset_id") == dataset_id
    )


async def delete_dataset(db: AsyncSession, storage: StorageBackend, dataset: Dataset) -> None:
    """Delete a dataset no tool uses: its keys, rows, row and stored file.

    Raises:
        ConflictError: 409 naming the tools that still read it.
    """
    users = await tools_using(db, dataset.workspace_id, dataset.id)
    if users:
        raise ConflictError(
            f"these tools still use this lookup table: {', '.join(users)}. Delete or change them first",
            details={"dataset_id": dataset.id, "tools": users},
        )
    await _clear_rows(db, dataset.id)
    await db.delete(dataset)
    await db.flush()
    try:
        await storage.delete(dataset.storage_key)
    except Exception:  # noqa: BLE001 - the rows are gone; a stray file is only storage
        log.warning("dataset_storage_delete_failed", dataset_id=dataset.id)
