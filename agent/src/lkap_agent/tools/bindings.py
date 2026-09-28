"""Result bindings: a tool's result onto the panel or into variables, no model turn (V6-07, D-V6-23).

:func:`apply_bindings` runs after a **successful** call (an HTTP 2xx, an app action that
succeeded, an MCP result that is not an error) and before the result is handed back to the
model, so the caller sees the lookup on the panel while the model is still composing its
sentence. Each ``ToolBinding`` resolves a JSON pointer against the tool's result (the value
the model sees, after ``result_path``) and writes:

* ``details:<block>.<key>`` — one row of a ``details`` block (upserted by key, the row's
  label and type kept, as ``set_details`` writes it);
* ``table:<block>`` — a list of objects (or one object) replaces a ``table`` block's rows;
  new keys add columns;
* ``checklist:<item>`` — a true value ticks an existing checklist item, a false one unticks it;
* ``status`` — the status stamp's label; ``note`` — one note (updated in place on the next call);
* ``var:<name>`` — a session variable, marked as bound (its value came from a third party).

Bounds: at most :data:`MAX_BINDINGS` bindings, :data:`MAX_BINDING_VALUE_CHARS` characters per
value (control characters removed), :data:`MAX_BINDING_TABLE_ROWS` rows per table. A block
target must exist on the session's panel and be a ``details`` or ``table`` block, so a
requestable, link, consent, upload, captions or handoff block is never written. A binding that
cannot apply is skipped, never an error: the tool's result still reaches the model.

One ``ActivityEvent`` per call records what happened (targets and counts, never values), with
``detail.event == "tool_bindings_applied"``. The model reads bound panel values only through
``describe_panel``, fenced.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from lkap_contracts.tool_context import (
    MAX_BINDING_TABLE_ROWS,
    MAX_BINDING_VALUE_CHARS,
    MAX_BINDINGS,
    BindingTarget,
    ToolBinding,
)
from lkap_contracts.ui_protocol import ActivityEvent, ChecklistItem, UiPatchOp

from lkap_agent.logging import get_logger
from lkap_agent.tools.context import ToolCallContext
from lkap_agent.tools.untrusted import strip_control

__all__ = [
    "BINDINGS_EVENT",
    "BindingReport",
    "apply_bindings",
    "binding_value",
    "parse_result",
    "resolve_pointer",
]

_log = get_logger(__name__)

#: ``ActivityEvent.detail["event"]`` of the one line each call's bindings record.
BINDINGS_EVENT: Final[str] = "tool_bindings_applied"

_FALSE_WORDS: Final[frozenset[str]] = frozenset({"", "false", "no", "0", "none", "null", "off"})


@dataclass(slots=True)
class BindingReport:
    """What :func:`apply_bindings` did: the targets written and the ones skipped (with why)."""

    applied: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)


class _Skip(Exception):
    """One binding that cannot apply (the reason is a short code, never a value)."""


def parse_result(text: str) -> Any:
    """A tool's result text as JSON when it is JSON, else the text itself."""
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return text


def resolve_pointer(data: Any, pointer: str) -> Any:
    """RFC 6901: ``""`` is the whole value; a missing step raises :class:`KeyError`."""
    if not pointer:
        return data
    current = data
    for raw in pointer.lstrip("/").split("/"):
        segment = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            try:
                current = current[int(segment)]
            except (ValueError, IndexError) as exc:
                raise KeyError(segment) from exc
        elif isinstance(current, dict):
            if segment not in current:
                raise KeyError(segment)
            current = current[segment]
        else:
            raise KeyError(segment)
    return current


def _clip(text: str) -> str:
    return strip_control(text)[:MAX_BINDING_VALUE_CHARS]


def binding_value(value: Any) -> str | None:
    """A bound value as bounded display text; ``None`` for a JSON null."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return _clip(value)
    if isinstance(value, int | float):
        return str(value)
    return _clip(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str))


def _cell(value: Any) -> Any:
    """A table cell: numbers and booleans as they are, everything else bounded text."""
    if value is None or isinstance(value, bool | int | float):
        return value
    return binding_value(value)


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in _FALSE_WORDS
    return bool(value)


def _variable(value: Any) -> Any:
    """A variable's value: a scalar kept as it is (text bounded), anything else as bounded JSON text."""
    if value is None or isinstance(value, bool | int | float):
        return value
    return binding_value(value)


class _Applier:
    def __init__(self, context: ToolCallContext, *, tool: str) -> None:
        self.context = context
        self.tool = tool
        self.ui = context.ui
        from lkap_agent.ui.blocks import session_block_specs  # noqa: PLC0415 - avoids an import cycle

        panel = getattr(getattr(context.session, "config", None), "panel", None)
        specs = session_block_specs(self.ui, panel) if panel is not None else []
        self.types = {spec.id: spec.type for spec in specs}
        self.detail_ops: dict[str, list[UiPatchOp]] = {}

    def _block(self, target: BindingTarget, expected: str) -> str:
        block_id = target.block_id or ""
        if self.types.get(block_id) != expected:
            raise _Skip("not_on_panel" if block_id not in self.types else "not_allowed")
        return block_id

    def details(self, target: BindingTarget, value: Any) -> None:
        from lkap_agent.tools.builtin.set_details import DetailIn, detail_item  # noqa: PLC0415

        block_id = self._block(target, "details")
        key = target.key or ""
        state = self.ui.state.blocks.get(block_id) or {}
        rows = state.get("items") if isinstance(state, dict) else None
        existing = next(
            (row for row in rows or [] if isinstance(row, dict) and row.get("key") == key),
            None,
        )
        item = DetailIn(key=key, value=binding_value(value) or "")
        row = detail_item(item, existing, now=time.time())
        self.detail_ops.setdefault(block_id, []).append(
            UiPatchOp(op="upsert", path="/items", value=row, key=key)
        )

    async def table(self, target: BindingTarget, value: Any) -> None:
        from lkap_agent.tools.builtin.table_append import infer_column  # noqa: PLC0415

        block_id = self._block(target, "table")
        raw_rows = value if isinstance(value, list) else [value] if isinstance(value, dict) else None
        if raw_rows is None:
            raise _Skip("not_rows")
        rows: list[dict[str, Any]] = []
        for raw in raw_rows:
            if not isinstance(raw, dict):
                continue
            row = {str(k)[:64]: _cell(v) for k, v in raw.items()}
            row.setdefault("id", uuid.uuid4().hex[:12])
            rows.append(row)
            if len(rows) >= MAX_BINDING_TABLE_ROWS:
                break
        current = self.ui.state.blocks.get(block_id) or {}
        columns = [c for c in (current.get("columns") or []) if isinstance(c, dict)]
        known = {c.get("key") for c in columns}
        added: list[dict[str, Any]] = []
        for row in rows:
            for key, cell in row.items():
                if key != "id" and key not in known:
                    known.add(key)
                    added.append(infer_column(key, cell).model_dump(mode="json"))
        ops: list[UiPatchOp] = []
        if added:
            ops.append(UiPatchOp(op="set", path="/columns", value=[*columns, *added]))
        ops.append(UiPatchOp(op="set", path="/rows", value=rows))
        await self.ui.patch_block(block_id, ops)

    async def checklist(self, target: BindingTarget, value: Any) -> None:
        items: list[ChecklistItem] = list(self.ui.state.checklist)
        index = next((i for i, item in enumerate(items) if item.id == target.key), None)
        if index is None:
            raise _Skip("no_item")
        items[index] = items[index].model_copy(update={"done": _truthy(value)})
        await self.ui.set_checklist(items)

    async def status(self, value: Any) -> None:
        label = binding_value(value)
        if not label:
            raise _Skip("empty")
        await self.ui.set_status(label, "info")

    async def note(self, value: Any, index: int) -> None:
        text = binding_value(value)
        if not text:
            raise _Skip("empty")
        await self.ui.add_note(text, "note", f"binding:{self.tool}:{index}")

    def variable(self, target: BindingTarget, value: Any) -> None:
        self.context.set_variable(target.key or "", _variable(value), bound=True)

    async def flush(self) -> None:
        for block_id, ops in self.detail_ops.items():
            await self.ui.patch_block(block_id, ops)


async def apply_bindings(
    result: Any,
    bindings: Sequence[ToolBinding],
    context: ToolCallContext | None,
    *,
    tool: str,
    call_id: str | None = None,
) -> BindingReport:
    """Apply a tool's bindings to its (successful) result.

    Args:
        result: The parsed result (see :func:`parse_result`), after ``result_path``.
        bindings: The definition's ``bindings``.
        context: The session's tool context; without one (or without a UI) only ``var:``
            targets could apply, and nothing is written at all.
        tool: The tool's name (activity line, note keys, logs).
        call_id: The call id, for the activity line's id.

    Returns:
        What was written and what was skipped. Never raises for a binding that does not
        apply; the caller still returns the result to the model.
    """
    report = BindingReport()
    if not bindings or context is None or context.ui is None:
        return report
    applier = _Applier(context, tool=tool)
    for index, binding in enumerate(bindings[:MAX_BINDINGS]):
        name = binding.to
        try:
            target = binding.target()
            value = resolve_pointer(result, binding.path)
            match target.kind:
                case "details":
                    applier.details(target, value)
                case "table":
                    await applier.table(target, value)
                case "checklist":
                    await applier.checklist(target, value)
                case "status":
                    await applier.status(value)
                case "note":
                    await applier.note(value, index)
                case "var":
                    applier.variable(target, value)
            report.applied.append(name)
        except KeyError:
            report.skipped.append((name, "not_found"))
        except _Skip as skip:
            report.skipped.append((name, str(skip)))
        except Exception:  # a value the block's state model refuses, a closed channel …
            _log.debug("tool_bindings.failed", tool=tool, target=name, exc_info=True)
            report.skipped.append((name, "invalid"))
    try:
        await applier.flush()
    except Exception:
        _log.debug("tool_bindings.flush_failed", tool=tool, exc_info=True)
        details = [name for name in report.applied if name.startswith("details:")]
        report.applied = [name for name in report.applied if name not in details]
        report.skipped.extend((name, "invalid") for name in details)
    _log.debug(
        "tool_bindings.applied",
        tool=tool,
        applied=report.applied,
        skipped=[f"{name} ({why})" for name, why in report.skipped],
    )
    await _record(context, report, tool=tool, call_id=call_id)
    return report


async def _record(context: ToolCallContext, report: BindingReport, *, tool: str, call_id: str | None) -> None:
    count = len(report.applied)
    if not count and not report.skipped:
        return
    headline = (
        f"Filled {count} value{'s' if count != 1 else ''} from {tool}"
        if count
        else f"Nothing from {tool} could be shown"
    )
    event = ActivityEvent(
        id=f"bindings-{call_id or uuid.uuid4().hex[:12]}",
        ts=time.time(),
        source=tool,
        label=tool.replace("_", " "),
        phase="done" if count else "error",
        headline=headline,
        detail={
            "event": BINDINGS_EVENT,
            "applied": list(report.applied),
            "skipped": [{"to": name, "reason": why} for name, why in report.skipped],
        },
        kind="tool",
    )
    try:
        await context.ui.activity(event)
    except Exception:
        _log.debug("tool_bindings.activity_failed", tool=tool, exc_info=True)
