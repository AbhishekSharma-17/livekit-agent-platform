"""The save-time check of a ``dataset`` tool (V6-16), called by ``POST/PUT /v1/tools``."""

from __future__ import annotations

from lkap_contracts.tools import DatasetToolDefinition
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.datasets.service import load_dataset
from lkap_api.errors import NotFoundError, UnprocessableEntityError

__all__ = ["check_dataset_tool"]


async def check_dataset_tool(db: AsyncSession, workspace_id: str, definition: DatasetToolDefinition) -> None:
    """Refuse a dataset tool whose dataset or columns this workspace does not have.

    Raises:
        UnprocessableEntityError: 422 for another workspace's (or an unknown) dataset, a key
            column the dataset does not declare, or a return column it does not have.
    """
    try:
        dataset = await load_dataset(db, workspace_id, definition.dataset_id)
    except NotFoundError:
        raise UnprocessableEntityError(
            f"unknown lookup table '{definition.dataset_id}'",
            details={"field": "definition.dataset_id", "reason": "unknown_dataset"},
        ) from None
    keys = [str(column.get("name")) for column in dataset.key_columns or []]
    columns = {str(column.get("name")) for column in dataset.columns or []}
    for column in definition.key_columns:
        if column not in keys:
            raise UnprocessableEntityError(
                f"'{column}' is not a key column of '{dataset.name}'. Its keys are: {', '.join(keys)}",
                details={"field": "definition.key_columns", "reason": "not_a_key_column", "column": column},
            )
    for column in definition.return_columns:
        if column not in columns:
            raise UnprocessableEntityError(
                f"'{column}' is not a column of '{dataset.name}'",
                details={"field": "definition.return_columns", "reason": "unknown_column", "column": column},
            )
