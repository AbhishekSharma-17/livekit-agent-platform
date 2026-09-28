"""Datasets (lookup tables): upload, list, get, rows, export, delete, lookup (V6-16, D-V6-27).

Admin routes under ``/v1/datasets`` follow the tools rule (``auth.roles``): a viewer or an
``agents:read`` key reads, a builder with ``agents:write`` uploads, deletes and runs a test
lookup. Every one is scoped to the caller's workspace; another workspace's dataset is a 404.

``POST /internal/v1/datasets/{id}/lookup`` is the worker's (service token): the session decides
the workspace, so a session of one workspace never reads another workspace's rows.
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import PurePosixPath
from typing import Annotated, Final

from fastapi import APIRouter, BackgroundTasks, File, Form, Query, Response, UploadFile, status
from lkap_contracts.datasets import (
    MAX_DATASET_BYTES,
    DatasetKeyType,
    DatasetLookupIn,
    DatasetLookupOut,
    DatasetOut,
    DatasetPage,
    DatasetPreviewOut,
    InternalDatasetLookupIn,
)
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import func, select

from lkap_api.datasets.parse import dataset_format, parse_dataset
from lkap_api.datasets.service import (
    create_dataset,
    dataset_out,
    delete_dataset,
    export_csv,
    load_dataset,
    lookup,
    preview,
)
from lkap_api.db.models import Dataset
from lkap_api.deps import AdminCtxDep, DbDep, ServiceDep
from lkap_api.errors import NotFoundError, UnprocessableEntityError
from lkap_api.jobs.deps import JobsDep
from lkap_api.logging import get_logger
from lkap_api.session_assets.service import load_session_any
from lkap_api.storage.base import UploadTooLargeError
from lkap_api.storage.deps import StorageDep

log = get_logger(__name__)

admin_router = APIRouter(prefix="/v1/datasets", tags=["datasets"])
internal_router = APIRouter(prefix="/internal/v1/datasets", tags=["internal"])

router = APIRouter()
router.include_router(admin_router)
router.include_router(internal_router)

_READ_CHUNK: Final = 256 * 1024
_KEY_SPEC: TypeAdapter[dict[str, DatasetKeyType]] = TypeAdapter(dict[str, DatasetKeyType])
_UNSAFE_CATEGORIES: Final = frozenset({"Cc", "Cf"})
#: The most rows ``GET …/rows`` returns at once.
MAX_PREVIEW_ROWS: Final = 200


def _basename(filename: str | None) -> str:
    """The upload's name without any directory part or control characters."""
    cleaned = "".join(ch for ch in (filename or "") if unicodedata.category(ch) not in _UNSAFE_CATEGORIES)
    base = PurePosixPath(cleaned.replace("\\", "/")).name.strip()
    return base if base.strip(".") else "dataset.csv"


async def _read_capped(file: UploadFile) -> bytes:
    """Read the upload, stopping as soon as it passes :data:`MAX_DATASET_BYTES`."""
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(_READ_CHUNK):
        total += len(chunk)
        if total > MAX_DATASET_BYTES:
            raise _too_large()
        chunks.append(chunk)
    return b"".join(chunks)


def _too_large() -> UploadTooLargeError:
    return UploadTooLargeError(
        "a lookup table is made from a file of at most 5 MB", details={"max_bytes": MAX_DATASET_BYTES}
    )


def _key_spec(raw: str) -> dict[str, DatasetKeyType]:
    try:
        return _KEY_SPEC.validate_python(json.loads(raw))
    except (json.JSONDecodeError, ValidationError):
        raise UnprocessableEntityError(
            'key_columns is a JSON object of column → type, e.g. {"phone": "phone"}; the types are '
            "string, phone, email and number",
            details={"field": "key_columns", "reason": "invalid_key_columns"},
        ) from None


@admin_router.post(
    "",
    response_model=DatasetOut,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a dataset",
    description=(
        "Makes a read-only lookup table from a `.csv`, `.tsv` or `.json` file (at most 5 MiB, 50,000 "
        "rows and 64 columns). `key_columns` is a JSON object naming the columns lookups match on "
        "(by header or column name) and how each is compared: `string` (case and spacing ignored), "
        "`phone` (digits only, the last ten), `email` (case ignored) or `number`. The file is checked "
        "here (422 with a plain reason, 415 for another file type, 413 past 5 MiB); the rows are then "
        "imported as a background job — poll `GET /v1/datasets/{id}` for `status` and `progress`. "
        "Nothing in a cell is ever evaluated."
    ),
)
async def upload_dataset(
    db: DbDep,
    storage: StorageDep,
    jobs: JobsDep,
    background_tasks: BackgroundTasks,
    ctx: AdminCtxDep,
    file: Annotated[UploadFile, File(description="A .csv, .tsv or .json file, at most 5 MiB")],
    name: Annotated[str, Form(min_length=1, max_length=200, description="The table's name")],
    key_columns: Annotated[
        str,
        Form(
            description='JSON object of key column → type, e.g. {"phone": "phone", "policy_number": "string"}'
        ),
    ],
) -> DatasetOut:
    """Check the upload, store it and enqueue the import."""
    if file.size is not None and file.size > MAX_DATASET_BYTES:
        raise _too_large()
    filename = _basename(file.filename)
    fmt = dataset_format(filename)
    spec = _key_spec(key_columns)
    data = await _read_capped(file)
    parsed = parse_dataset(data, fmt, spec, tab=PurePosixPath(filename.lower()).suffix == ".tsv")
    row = await create_dataset(
        db,
        storage,
        jobs,
        background_tasks,
        workspace_id=ctx.workspace_id,
        name=" ".join(name.split()),
        filename=filename,
        parsed=parsed,
        data=data,
    )
    return await dataset_out(db, row)


@admin_router.get(
    "",
    response_model=DatasetPage,
    summary="List datasets",
    description="The workspace's lookup tables, newest first, with their import status.",
)
async def list_datasets(db: DbDep, ctx: AdminCtxDep) -> DatasetPage:
    """Every dataset of the caller's workspace."""
    in_workspace = Dataset.workspace_id == ctx.workspace_id
    rows = (
        (await db.execute(select(Dataset).where(in_workspace).order_by(Dataset.created_at.desc())))
        .scalars()
        .all()
    )
    total = int(await db.scalar(select(func.count()).select_from(Dataset).where(in_workspace)) or 0)
    return DatasetPage(items=[await dataset_out(db, row) for row in rows], total=total)


@admin_router.get(
    "/{dataset_id}",
    response_model=DatasetOut,
    summary="Get a dataset",
    description="One lookup table: its columns, keys, row count and import status (`progress`, `error`).",
)
async def get_dataset(dataset_id: str, db: DbDep, ctx: AdminCtxDep) -> DatasetOut:
    """One dataset of the caller's workspace."""
    return await dataset_out(db, await load_dataset(db, ctx.workspace_id, dataset_id))


@admin_router.get(
    "/{dataset_id}/rows",
    response_model=DatasetPreviewOut,
    summary="Preview a dataset's rows",
    description=f"A page of rows in file order (at most {MAX_PREVIEW_ROWS}); none until the import finishes.",
)
async def dataset_rows(
    dataset_id: str,
    db: DbDep,
    ctx: AdminCtxDep,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=MAX_PREVIEW_ROWS)] = 50,
) -> DatasetPreviewOut:
    """A page of the dataset's rows."""
    dataset = await load_dataset(db, ctx.workspace_id, dataset_id)
    return await preview(db, dataset, offset=offset, limit=limit)


@admin_router.get(
    "/{dataset_id}/export",
    summary="Download a dataset as CSV",
    description=(
        "The imported rows as CSV with the original headers. A cell a spreadsheet program would read "
        "as a formula (starting with `=`, `+`, `-`, `@`, a tab or a carriage return, and not just a "
        "number) is prefixed with `'` so it opens as text."
    ),
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}}},
)
async def export_dataset(dataset_id: str, db: DbDep, ctx: AdminCtxDep) -> Response:
    """The dataset as a CSV attachment."""
    dataset = await load_dataset(db, ctx.workspace_id, dataset_id)
    return Response(
        content=await export_csv(db, dataset),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{dataset.slug}.csv"'},
    )


@admin_router.delete(
    "/{dataset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a dataset",
    description="Deletes a lookup table, its rows and its file. Refused (409) while a tool still uses it.",
)
async def remove_dataset(dataset_id: str, db: DbDep, storage: StorageDep, ctx: AdminCtxDep) -> Response:
    """Delete a dataset no tool uses."""
    dataset = await load_dataset(db, ctx.workspace_id, dataset_id)
    await delete_dataset(db, storage, dataset)
    log.info("dataset_deleted", dataset_id=dataset_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@admin_router.post(
    "/{dataset_id}/lookup",
    response_model=DatasetLookupOut,
    summary="Look rows up in a dataset",
    description=(
        "Finds rows whose key columns match every given value (`exact`, or `prefix` on the normalised "
        "value), in file order, at most `max_rows` (≤ 20), with `return_columns` only (all when empty). "
        "The console's test; agents look up through a `dataset` tool. 409 while the import runs."
    ),
)
async def lookup_dataset(
    dataset_id: str, payload: DatasetLookupIn, db: DbDep, ctx: AdminCtxDep
) -> DatasetLookupOut:
    """A test lookup in one of the caller's datasets."""
    dataset = await load_dataset(db, ctx.workspace_id, dataset_id)
    return await lookup(db, dataset, payload)


@internal_router.post(
    "/{dataset_id}/lookup",
    response_model=DatasetLookupOut,
    summary="Look rows up for a session (worker)",
    description=(
        "The worker's `dataset` tool. The session names the workspace: a dataset of another "
        "workspace (or an unknown session) is a 404, so a lookup never reads another workspace's rows."
    ),
)
async def internal_lookup_dataset(
    dataset_id: str, payload: InternalDatasetLookupIn, db: DbDep, _service: ServiceDep
) -> DatasetLookupOut:
    """A lookup in the session's workspace (worker-only)."""
    try:
        workspace_id = (await load_session_any(db, payload.session_id)).workspace_id
    except NotFoundError:
        raise NotFoundError(f"unknown lookup table '{dataset_id}'") from None
    dataset = await load_dataset(db, workspace_id, dataset_id)
    result = await lookup(
        db, dataset, DatasetLookupIn.model_validate(payload.model_dump(exclude={"session_id"}))
    )
    log.debug(
        "dataset_lookup",
        dataset_id=dataset_id,
        session_id=payload.session_id,
        key_columns=sorted(payload.keys),
        match=payload.match,
        rows=len(result.rows),
    )
    return result
