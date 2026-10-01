"""`notebook_write` built-in tool (V6-08, D-V6-15): write in one section of a notebook block.

A notebook's sections are listed by its config (`NotebookBlockConfig.sections`); the model
names one by id and writes what that kind of section holds:

* a `text` section: `text`, appended as a new note, or updating the note that has the same
  `key` in place, or replacing the whole section with `mode="replace"`;
* a `checklist` section: `items` (`{id, label, done, blocking, hint}`), upserted by id (a
  ticked item keeps its tick; `notebook_check` unticks), or the whole list with `replace`;
* a `details` section: `fields` (`{key, value, label}`), upserted by key as `set_details`
  does, or the whole card with `replace`.

An `ink` section is a drawing board (V6-12) and is never written here. A write: blocking,
instant, never in the background; it answers nothing on a realtime model (a half cascade
too), so the model does not stop to speak about it (ruling on ask #23). Only this tool and
`notebook_check` write a notebook: `update_block` and a page's `state_delta` never do.
"""

from __future__ import annotations

import re
import time
from typing import Annotated, Any, Final, Literal, get_args

from livekit.agents import FunctionTool, RunContext, ToolError, function_tool
from lkap_contracts.blocks import NotebookSectionConfig
from lkap_contracts.ui_protocol import (
    MAX_NOTEBOOK_ENTRIES,
    MAX_NOTEBOOK_ENTRY_CHARS,
    MAX_NOTEBOOK_ITEMS,
    BlockSpec,
    Tone,
    UiPatchOp,
)
from packs.base import PackSessionContext
from pydantic import BaseModel, Field, ValidationError

from lkap_agent.tools.json_args import json_list
from lkap_agent.ui.blocks import (
    new_notebook_entry_id,
    notebook_section_ops,
    notebook_section_state,
    notebook_sections,
    pick_block,
    session_block_specs,
)

from .set_checklist import CHECKLIST_ID_PATTERN, MAX_HINT_CHARS, MAX_LABEL_CHARS, QUIET_MODES
from .set_details import DETAIL_EXAMPLE, DetailIn, detail_item

__all__ = [
    "NOTE_KEY_PATTERN",
    "NotebookItemIn",
    "build_notebook_write_tool",
    "describe_notebooks",
    "resolve_notebook_section",
]

#: A note's key: letters, digits and `_.-` (never `:`, which the platform's entry ids carry,
#: so a key can never match another note's id).
NOTE_KEY_PATTERN: Final[str] = r"^[A-Za-z0-9_.-]{1,64}$"
_NOTE_KEY_RE: Final[re.Pattern[str]] = re.compile(NOTE_KEY_PATTERN)
_TONES: Final[frozenset[str]] = frozenset(get_args(Tone))
_KIND_WORDS: Final[dict[str, str]] = {
    "text": "notes",
    "checklist": "a checklist",
    "details": "a summary card",
    "ink": "a drawing board",
}


class NotebookItemIn(BaseModel):
    """One item of a notebook checklist section."""

    id: str = Field(pattern=CHECKLIST_ID_PATTERN, description="A short id, e.g. repair_quote.")
    label: str = Field(description="What the caller sees, e.g. 'A repair quote'.")
    done: bool = Field(default=False, description="True once it is already provided.")
    blocking: bool = Field(default=False, description="True when the call cannot finish without it.")
    hint: str = Field(default="", description="An optional short hint shown under the item.")


#: One checklist item as the model writes it (V6-30).
NOTEBOOK_ITEM_EXAMPLE: Final[str] = '{"id": "photos", "label": "Photos of the damage", "done": false}'
#: The two list parameters; JSON text of either is read too (V6-30, F-2).
NotebookItemList = Annotated[list[NotebookItemIn] | None, json_list(NOTEBOOK_ITEM_EXAMPLE)]
NotebookFieldList = Annotated[list[DetailIn] | None, json_list(DETAIL_EXAMPLE)]


def _clean(text: str, limit: int) -> str:
    return " ".join(text.split())[:limit]


def describe_notebooks(specs: list[BlockSpec]) -> str:
    """ "notebook: notes (notes), still_needed (a checklist)…" for a tool description."""
    parts: list[str] = []
    for spec in specs:
        if spec.type != "notebook":
            continue
        sections = ", ".join(f"{s.id} ({_KIND_WORDS[s.kind]})" for s in notebook_sections(spec))
        parts.append(f"{spec.id}: {sections or 'no sections'}")
    return "; ".join(parts) if parts else "none"


def resolve_notebook_section(
    ctx: PackSessionContext, block_id: str, section_id: str
) -> tuple[str, NotebookSectionConfig, dict[str, Any]]:
    """The notebook block, the section and its current content a call names.

    Raises:
        ToolError: With a model-readable message when the block or the section is unknown.
    """
    specs = session_block_specs(ctx.ui, ctx.config.panel)
    try:
        target = pick_block(specs, "notebook", block_id or None)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    spec = next(s for s in specs if s.id == target)
    sections = notebook_sections(spec)
    section = next((s for s in sections if s.id == section_id), None)
    if section is None:
        known = ", ".join(f"{s.id} ({_KIND_WORDS[s.kind]})" for s in sections) or "none"
        raise ToolError(f"The notebook {target} has no section {section_id!r}. Its sections are: {known}.")
    state = ctx.ui.state.blocks.get(target)
    state = state if isinstance(state, dict) else {}
    return target, section, state


def _text_ops(
    section: NotebookSectionConfig,
    content: dict[str, Any],
    text: str,
    mode: str,
    key: str,
    tone: str,
    now: float,
) -> tuple[list[UiPatchOp], str]:
    cleaned = " ".join(text.split())
    if not cleaned:
        raise ToolError(f"Section {section.id} holds notes: pass the note as text.")
    if len(cleaned) > MAX_NOTEBOOK_ENTRY_CHARS:
        raise ToolError(f"Keep a note under {MAX_NOTEBOOK_ENTRY_CHARS} characters.")
    if key and not _NOTE_KEY_RE.match(key):
        raise ToolError("A note's key uses letters, digits, '_', '.' and '-' only.")
    if tone and tone not in _TONES:
        raise ToolError(f"tone is one of: {', '.join(sorted(_TONES))}.")
    base = f"/sections/{section.id}/entries"
    entries = [e for e in content.get("entries") or [] if isinstance(e, dict)]
    entry: dict[str, Any] = {"id": new_notebook_entry_id(), "text": cleaned, "author": "agent", "ts": now}
    if key:
        entry["key"] = key
    if tone:
        entry["tone"] = tone
    if mode == "replace":
        return [UiPatchOp(op="set", path=base, value=[entry])], f"Replaced the notes in {section.id}."
    existing = next((e for e in entries if key and e.get("key") == key), None)
    if existing is not None:
        # The agent writing its keyed note again replaces it, and clears a caller-edit marker.
        entry["id"] = existing["id"]
        return [
            UiPatchOp(op="upsert", path=base, value=entry, key=key)
        ], f"Updated the note {key} in {section.id}."
    if len(entries) >= MAX_NOTEBOOK_ENTRIES:
        raise ToolError(
            f"Section {section.id} is full ({MAX_NOTEBOOK_ENTRIES} notes). Write it again with "
            'mode="replace" and a short summary.'
        )
    return [UiPatchOp(op="append", path=base, value=entry)], f"Added a note to {section.id}."


def _checklist_ops(
    section: NotebookSectionConfig, content: dict[str, Any], items: list[NotebookItemIn], mode: str
) -> tuple[list[UiPatchOp], str]:
    if not items:
        raise ToolError(f"Section {section.id} is a checklist: pass items.")
    ids = [item.id for item in items]
    if len(set(ids)) != len(ids):
        raise ToolError("Item ids must be unique.")
    current = {i.get("id"): i for i in content.get("items") or [] if isinstance(i, dict)}
    rows: list[dict[str, Any]] = []
    for item in items:
        label = _clean(item.label, MAX_LABEL_CHARS)
        if not label:
            raise ToolError(f"Give item {item.id} a label.")
        existing = current.get(item.id) or {}
        done = item.done or bool(existing.get("done"))
        row: dict[str, Any] = {"id": item.id, "label": label, "done": done, "blocking": item.blocking}
        hint = _clean(item.hint, MAX_HINT_CHARS)
        if hint:
            row["hint"] = hint
        if existing.get("edited_by") == "caller" and done == bool(existing.get("done")):
            row["edited_by"] = "caller"
        rows.append(row)
    base = f"/sections/{section.id}/items"
    if mode == "replace":
        if len(rows) > MAX_NOTEBOOK_ITEMS:
            raise ToolError(f"Keep the list to {MAX_NOTEBOOK_ITEMS} items or fewer.")
        return [
            UiPatchOp(op="set", path=base, value=rows)
        ], f"The list in {section.id} now has {len(rows)} items."
    if len(set(current) | set(ids)) > MAX_NOTEBOOK_ITEMS:
        raise ToolError(f"Keep the list to {MAX_NOTEBOOK_ITEMS} items or fewer.")
    ops = [UiPatchOp(op="upsert", path=base, value=row, key=row["id"]) for row in rows]
    return ops, f"Updated {len(rows)} item{'s' if len(rows) != 1 else ''} in {section.id}."


def _details_ops(
    section: NotebookSectionConfig, content: dict[str, Any], fields: list[DetailIn], mode: str, now: float
) -> tuple[list[UiPatchOp], str]:
    if not fields:
        raise ToolError(f"Section {section.id} is a summary card: pass fields.")
    keys = [f.key.strip() for f in fields]
    if not all(keys) or len(set(keys)) != len(keys):
        raise ToolError("Field keys must be unique and non-empty.")
    current = {r.get("key"): r for r in content.get("items") or [] if isinstance(r, dict)}
    rows = [
        detail_item(field.model_copy(update={"key": key}), current.get(key), now=now)
        for field, key in zip(fields, keys, strict=True)
    ]
    base = f"/sections/{section.id}/items"
    total = len(rows) if mode == "replace" else len(set(current) | set(keys))
    if total > MAX_NOTEBOOK_ITEMS:
        raise ToolError(f"Keep the card to {MAX_NOTEBOOK_ITEMS} rows or fewer.")
    if mode == "replace":
        return [
            UiPatchOp(op="set", path=base, value=rows)
        ], f"The card in {section.id} now has {len(rows)} rows."
    ops = [UiPatchOp(op="upsert", path=base, value=row, key=row["key"]) for row in rows]
    return ops, f"Updated {len(rows)} row{'s' if len(rows) != 1 else ''} in {section.id}."


def build_notebook_write_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build the `notebook_write` tool bound to `ctx`."""
    inventory = describe_notebooks(session_block_specs(ctx.ui, ctx.config.panel))

    async def notebook_write(
        context: RunContext[Any],
        section_id: str,
        text: str = "",
        items: NotebookItemList = None,
        fields: NotebookFieldList = None,
        mode: Literal["append", "replace"] = "append",
        key: str = "",
        tone: str = "",
        block_id: str = "",
    ) -> str | None:
        """Write in one section of the notebook in the caller's side panel.

        Args:
            section_id: The section to write in.
            text: For a notes section: the note.
            items: For a checklist section: the items to add or update.
            fields: For a summary section: the facts to add or update, each by key.
            mode: "append" adds or updates; "replace" replaces the whole section.
            key: For a note: a short key, so writing the same key again updates that note.
            tone: For a note: neutral, info, success, warning or danger.
            block_id: The notebook block; leave empty when there is only one.
        """
        target, section, state = resolve_notebook_section(ctx, block_id, section_id)
        content = notebook_section_state(state, section)
        now = time.time()
        match section.kind:
            case "text":
                ops, answer = _text_ops(section, content, text, mode, key.strip(), tone.strip(), now)
            case "checklist":
                ops, answer = _checklist_ops(section, content, items or [], mode)
            case "details":
                ops, answer = _details_ops(section, content, fields or [], mode, now)
            case _:
                raise ToolError(f"Section {section.id} is a drawing board. It cannot be written in.")
        ops = [
            *notebook_section_ops(state, section),
            *ops,
            UiPatchOp(op="set", path="/updated_at", value=now),
        ]
        try:
            await ctx.ui.patch_block(target, ops)
        except ValidationError as exc:
            raise ToolError(f"That does not fit the notebook: {exc.errors()[0]['msg']}.") from exc
        ctx.log.debug(
            "builtin_tool.notebook_write",
            call_id=context.function_call.call_id,
            block_id=target,
            section_id=section.id,
            kind=section.kind,
            mode=mode,
        )
        if ctx.pipeline_mode in QUIET_MODES:
            return None
        return answer

    return function_tool(
        notebook_write,
        description=(
            "Write in the notebook in the caller's side panel as the call goes: a note in a notes "
            "section (text), the items of a checklist section (items), or the facts of a summary "
            "section (fields). Write quietly; do not read the notebook aloud. Use notebook_check "
            "to tick a checklist item. Items and fields are lists of objects, e.g. "
            f"items=[{NOTEBOOK_ITEM_EXAMPLE}] or fields=[{DETAIL_EXAMPLE}]. Notebook sections: {inventory}."
        ),
    )
