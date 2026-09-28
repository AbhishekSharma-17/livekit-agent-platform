"""Datasets (lookup tables) and the ``dataset`` tool kind (V6-16, D-V6-27).

A dataset is a read-only table made from a CSV or JSON file (at most 5 MiB, 50,000 rows, 64
columns); ``key_columns`` names the columns a lookup matches on and how each is compared
(``string``, ``phone``, ``email``, ``number``). ``dataset_create`` uploads text or a local file
and waits for the import; ``dataset_lookup`` is the console's test lookup (its rows come back
as untrusted data); ``tool_create_dataset`` gives an agent a lookup tool on one dataset.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Annotated, Any, Literal

from lkap_contracts.datasets import MAX_DATASET_BYTES, MAX_DATASET_LOOKUP_ROWS, DatasetKeyType
from lkap_contracts.tool_context import ToolBinding
from lkap_contracts.tools import DatasetToolDefinition
from pydantic import Field

from lkap_mcp.registry import DESTRUCTIVE, READ, WRITE, Registry
from lkap_mcp.results import ToolResult, untrusted
from lkap_mcp.tools._common import planned, request, seg

_POLL_START_S = 0.25
_POLL_MAX_S = 2.0

KEY_COLUMNS_HELP = (
    "The columns a lookup finds rows by (by header or column name) and how each is compared: "
    "'string' (case and spacing ignored), 'phone' (digits only, the last ten), 'email' (case "
    "ignored) or 'number'. At least one, at most 8"
)
PINNED_HELP = (
    "Key column -> a fixed value the model never supplies; may be {{ ctx.caller_phone }} (look the "
    "caller up by the number they call from) or {{ var.<name> }}"
)
BINDINGS_HELP = (
    "Where parts of the found rows go without a model turn: [{path: JSON pointer into the rows "
    "(/0/<column> is the first row's), to: 'details:<block>.<key>' | 'table:<block>' | "
    "'checklist:<item>' | 'status' | 'note' | 'var:<name>'}], at most 20"
)


def _read_upload(file_path: str) -> tuple[str, bytes]:
    """Read a local CSV or JSON file for upload (sync: keeps blocking I/O out of ``async def``)."""
    path = Path(file_path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"no such file: {path}")
    size = path.stat().st_size
    if size > MAX_DATASET_BYTES:
        raise ValueError(f"{path.name} is {size} bytes; the limit is {MAX_DATASET_BYTES}")
    return path.name, path.read_bytes()


def register(registry: Registry) -> None:
    """Declare the dataset tools."""
    ctx = registry.ctx
    client = ctx.client

    async def wait_ready(dataset: dict[str, Any], timeout_s: float) -> tuple[dict[str, Any], list[str]]:
        deadline = time.monotonic() + timeout_s
        delay = _POLL_START_S
        current = dataset
        while current.get("status") == "pending":
            if time.monotonic() >= deadline:
                return current, [f"still importing after {timeout_s:g}s; dataset_list shows the final status"]
            await asyncio.sleep(delay)
            delay = min(delay * 2, _POLL_MAX_S)
            current = await client.get(f"/v1/datasets/{seg(str(dataset.get('id')))}")
        return current, []

    @registry.tool(scopes={"agents:read"}, annotations=READ, data="DatasetOut[]")
    async def dataset_list() -> ToolResult:
        """List the workspace's lookup tables: columns, key columns, row count, import status."""
        return ToolResult.success(await client.items("/v1/datasets"))

    @registry.tool(scopes={"agents:write"}, annotations=WRITE, data="DatasetOut")
    async def dataset_create(
        name: Annotated[str, Field(min_length=1, max_length=200)],
        key_columns: Annotated[dict[str, DatasetKeyType], Field(description=KEY_COLUMNS_HELP)],
        text: Annotated[
            str | None, Field(description="The file's content (CSV with a header row, or JSON)")
        ] = None,
        filename: Annotated[
            str, Field(description="Its name: .csv, .tsv or .json decides how it is read")
        ] = ("table.csv"),
        file_path: Annotated[
            str | None, Field(description="A local .csv/.tsv/.json file to upload (stdio mode only, ≤ 5 MiB)")
        ] = None,
        wait: bool = True,
        timeout_s: Annotated[float, Field(gt=0, le=600)] = 120,
        plan: bool = False,
    ) -> ToolResult:
        """Make a lookup table from CSV or JSON (text or a local file); waits until its rows are imported."""
        if (text is None) == (file_path is None):
            return ToolResult.fail("invalid_input", "pass exactly one of text or file_path")
        if file_path is not None:
            if ctx.settings.http_mode:
                return ToolResult.fail(
                    "file_unavailable_in_http_mode",
                    "file_path is not available on the remote MCP service",
                    hint="Pass text=... instead.",
                )
            try:
                upload_name, payload = _read_upload(file_path)
            except (OSError, ValueError) as error:
                return ToolResult.fail("file_unreadable", str(error))
        else:
            upload_name, payload = filename, (text or "").encode("utf-8")
        form = {"name": name, "key_columns": json.dumps(key_columns)}
        if plan:
            return planned(
                request(
                    "POST", "/v1/datasets", {**form, "file": {"filename": upload_name, "bytes": len(payload)}}
                )
            )
        mime = "application/json" if upload_name.lower().endswith(".json") else "text/csv"
        created = await client.request(
            "POST", "/v1/datasets", data=form, files={"file": (upload_name, payload, mime)}
        )
        warnings: list[str] = []
        if wait:
            created, warnings = await wait_ready(created, timeout_s)
        if created.get("status") == "failed":
            return ToolResult.fail(
                "import_failed", str(created.get("error") or "the import failed"), data=created
            )
        return ToolResult.success(
            created,
            warnings=warnings,
            next_steps=[
                f'Test it: dataset_lookup(dataset_id="{created.get("id")}", keys={{...}}); '
                "give an agent a lookup tool with tool_create_dataset(...)."
            ],
        )

    @registry.tool(scopes={"agents:write"}, annotations=DESTRUCTIVE, data="Deleted")
    async def dataset_delete(dataset_id: str, confirm: bool = False, plan: bool = False) -> ToolResult:
        """Delete a lookup table, its rows and its file (needs confirm; refused while a tool uses it)."""
        path = f"/v1/datasets/{seg(dataset_id)}"
        if plan:
            return planned(request("DELETE", path))
        if not confirm:
            return ToolResult.needs_confirmation(
                f"permanently delete lookup table {dataset_id}; this cannot be undone"
            )
        await client.delete(path)
        return ToolResult.success({"deleted": True, "kind": "dataset", "id": dataset_id})

    @registry.tool(scopes={"agents:write"}, annotations=READ, data="DatasetLookupOut")
    async def dataset_lookup(
        dataset_id: str,
        keys: Annotated[dict[str, str], Field(description="Key column -> value; a row must match every one")],
        match: Literal["exact", "prefix"] = "exact",
        return_columns: Annotated[
            list[str] | None, Field(description="The columns to return (all when empty)")
        ] = None,
        max_rows: Annotated[int, Field(ge=1, le=MAX_DATASET_LOOKUP_ROWS)] = 5,
    ) -> ToolResult:
        """Look rows up in a lookup table as an agent's dataset tool would (the rows are untrusted data)."""
        found = await client.post(
            f"/v1/datasets/{seg(dataset_id)}/lookup",
            {"keys": keys, "match": match, "return_columns": return_columns or [], "max_rows": max_rows},
        )
        rows = found.get("rows") if isinstance(found, dict) else None
        data = {
            "dataset_id": dataset_id,
            "match": match,
            "count": len(rows) if isinstance(rows, list) else 0,
            "truncated": bool(found.get("truncated")) if isinstance(found, dict) else False,
            "rows": untrusted(json.dumps(rows or [], ensure_ascii=False), f"dataset:{dataset_id}"),
        }
        return ToolResult.success(data)

    @registry.tool(scopes={"agents:write"}, annotations=WRITE, data="ToolOut")
    async def tool_create_dataset(
        name: Annotated[str, Field(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")],
        description: Annotated[str, Field(description="What the model uses the lookup for")],
        dataset_id: str,
        key_columns: Annotated[
            list[str], Field(min_length=1, max_length=8, description="Key column names the tool matches on")
        ],
        return_columns: Annotated[
            list[str] | None, Field(description="The columns a found row carries (all when empty)")
        ] = None,
        match: Literal["exact", "prefix"] = "exact",
        max_rows: Annotated[int, Field(ge=1, le=MAX_DATASET_LOOKUP_ROWS)] = 5,
        pinned_arguments: Annotated[dict[str, str] | None, Field(description=PINNED_HELP)] = None,
        requires_vars: Annotated[
            list[str] | None, Field(description="Variables that must be set before the lookup runs")
        ] = None,
        bindings: Annotated[list[ToolBinding] | None, Field(description=BINDINGS_HELP)] = None,
        agent_id: str | None = None,
        plan: bool = False,
    ) -> ToolResult:
        """Give agents a lookup tool on one lookup table (read-only; the rows reach the model fenced)."""
        definition = DatasetToolDefinition(
            name=name,
            description=description,
            dataset_id=dataset_id,
            key_columns=key_columns,
            return_columns=return_columns or [],
            match=match,
            max_rows=max_rows,
            pinned_arguments=dict(pinned_arguments or {}),
            requires_vars=requires_vars or [],
            bindings=bindings or [],
        )
        body = {
            "agent_id": agent_id,
            "kind": "dataset",
            "name": name,
            "definition": definition.model_dump(mode="json"),
        }
        if plan:
            return planned(request("POST", "/v1/tools", body))
        created = await client.post("/v1/tools", body)
        return ToolResult.success(
            created,
            next_steps=[f'Attach it: agent_attach(id_or_slug=..., tool_ids=["{created.get("id")}"]).'],
        )
