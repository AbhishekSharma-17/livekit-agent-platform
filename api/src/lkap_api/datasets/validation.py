"""Save-time checks of an agent's ``dataset`` tools (V6-16), registered into ``VALIDATORS``.

Per attached tool row (``tools[i]`` is the i-th ``tools.tool_ids`` entry), read from the stored
JSON:

* the dataset is not one of the workspace's → error (deleted, or a row written around the api);
* a ``key_columns`` entry that is not one of the dataset's declared key columns → error;
* a ``return_columns`` entry that is not one of its columns → error;
* the dataset's import failed → error; it is still importing → warning.

The tool route refuses the first three at save; this catches rows stored another way and a
dataset that changed since.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from lkap_contracts.api_models import Issue

from lkap_api.config_service import ValidationContext, register_validator

__all__ = ["dataset_tool_issues"]


def _column_issues(
    base: str, name: str, definition: Mapping[str, Any], dataset: Mapping[str, Any]
) -> list[Issue]:
    issues: list[Issue] = []
    keys = dataset.get("keys") or {}
    columns = set(dataset.get("columns") or [])
    title = dataset.get("name")
    for position, column in enumerate(definition.get("key_columns") or []):
        if column not in keys:
            issues.append(
                Issue(
                    path=f"{base}.key_columns[{position}]",
                    message=f"'{name}' looks rows up by '{column}', which is not a key column of "
                    f"'{title}' (its keys: {', '.join(keys) or 'none'})",
                )
            )
    for position, column in enumerate(definition.get("return_columns") or []):
        if column not in columns:
            issues.append(
                Issue(
                    path=f"{base}.return_columns[{position}]",
                    message=f"'{name}' returns '{column}', which is not a column of '{title}'",
                )
            )
    return issues


def dataset_tool_issues(ctx: ValidationContext) -> list[Issue]:
    """The dataset tool checks; nothing without dataset tools or without the workspace's datasets."""
    definitions = ctx.tool_definitions_by_id
    datasets = ctx.datasets_by_id
    if definitions is None or datasets is None:
        return []
    issues: list[Issue] = []
    for index, tool_id in enumerate(ctx.config.tools.tool_ids):
        definition = definitions.get(tool_id)
        if not isinstance(definition, Mapping) or definition.get("kind") != "dataset":
            continue
        base = f"tools[{index}].definition"
        name = str(definition.get("name") or tool_id)
        dataset = datasets.get(str(definition.get("dataset_id") or ""))
        if dataset is None:
            issues.append(
                Issue(
                    path=f"{base}.dataset_id",
                    message=f"'{name}' reads a lookup table that is not in this workspace; pick another",
                )
            )
            continue
        issues.extend(_column_issues(base, name, definition, dataset))
        status = dataset.get("status")
        if status == "failed":
            issues.append(
                Issue(
                    path=f"{base}.dataset_id",
                    message=f"the lookup table of '{name}' failed to import; upload it again",
                )
            )
        elif status == "pending":
            issues.append(
                Issue(
                    path=f"{base}.dataset_id",
                    message=f"the lookup table of '{name}' is still being imported; "
                    "lookups wait until it is ready",
                    severity="warning",
                )
            )
    return issues


register_validator(dataset_tool_issues)
