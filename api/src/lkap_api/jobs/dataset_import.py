"""The ``dataset_import`` job (V6-16): a stored upload's rows into ``dataset_rows``/``dataset_keys``.

Enqueued by ``POST /v1/datasets`` once the ``pending`` dataset row is committed and the bytes
are in storage; the body is :func:`lkap_api.datasets.service.import_rows`. A failure is
recorded on the dataset (``failed``) and its job row (``payload.error``), never retried.
"""

from __future__ import annotations

from typing import Any

from lkap_api.datasets.service import import_rows
from lkap_api.jobs.context import JobContext
from lkap_api.jobs.kinds import DATASET_IMPORT
from lkap_api.jobs.registry import job
from lkap_api.storage.resolve import default_storage

__all__ = ["run_dataset_import"]


@job(DATASET_IMPORT)
async def run_dataset_import(ctx: JobContext, payload: dict[str, Any]) -> None:
    """Import one dataset's rows (``payload["dataset_id"]``)."""
    await import_rows(ctx.database, default_storage(ctx.settings), str(payload["dataset_id"]))
