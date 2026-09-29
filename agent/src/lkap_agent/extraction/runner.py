"""The live extraction runner (V6-13, D-V6-24): the generic form of the insurance ``ClaimWorkflow``.

One :class:`ExtractionRunner` per session. :meth:`ExtractionRunner.run` reads the
conversation so far, and unless the transcript is unchanged since the last successful run
(a SHA-256 of the transcript and the field list: unchanged text costs nothing) makes **one**
prompt-for-JSON call on the session's ``workflow_llm`` (the agent's own LLM when no separate one
is set) within the session's budget (:func:`extraction_budget_s`: ``LKAP_EXTRACTION_TIMEOUT_S``,
default :data:`EXTRACTION_BUDGET_S`). It never raises and is always started in the background by
:class:`~lkap_agent.extraction.session.LiveStructure`, so the reply is never delayed; when the
model answers after the reply went out, the values still land and the rules still run (V6-30).
A timeout is a logged warning naming the budget.

Writes, in order:

1. the values into the session's variable store (``tools.context.session_variables``: a
   flow's ``FlowState.variables``, else ``userdata["lkap.variables"]``; ask #32), a ``null``
   never overwriting a value, and the names into ``userdata["lkap.extracted_variables"]``
   (so a flow fences them in its instructions, ask #31). Names a flow step's own ``extract``
   lists are left to the step;
2. each field's ``show_in``: a ``details`` block's row, or a notebook section's row (a
   ``details`` section) or keyed note (a ``text`` section); a ``sensitive`` field shows
   :data:`SENSITIVE_MASK`, never its value (V6-21, S6-11);
3. the "still needed" checklist items (``need_<field>``) when ``still_needed`` is
   ``checklist``, keeping every other item;
4. one ``extraction`` session event (names and whether each is set; values only on the
   ``full`` storage tier and never for a ``sensitive`` field).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from livekit.agents import llm as lk_llm
from lkap_contracts.extraction import (
    EXTRACTION_BUDGET_S,
    EXTRACTION_EVENT,
    ExtractionConfig,
    ExtractionEvent,
    ExtractionField,
    parse_show_in,
    still_needed_item_id,
)
from lkap_contracts.flow import AgentNode
from lkap_contracts.rules_expr import is_set
from lkap_contracts.ui_protocol import ChecklistItem, UiPatchOp
from pydantic import BaseModel

from lkap_agent.flow.variables import (
    EXTRACTED_VARIABLES_USERDATA_KEY,
    coerce_variable,
    transcript_text,
    variables_model,
)
from lkap_agent.logging import get_logger
from lkap_agent.tools.context import session_variables

__all__ = [
    "EXTRACTION_TIMEOUT_ATTR",
    "SENSITIVE_MASK",
    "ExtractionRun",
    "ExtractionRunner",
    "Trigger",
    "extraction_budget_s",
    "field_label",
    "summary_variables",
]

_log = get_logger(__name__)

Trigger = Literal["turn", "tool", "node_exit", "manual"]

#: Longest value an ``extraction`` event carries (``full`` tier only).
_MAX_EVENT_VALUE_CHARS: Final[int] = 200
#: A little slack over the budget for a model client that ignores ``timeout_s``.
_GUARD_S: Final[float] = 0.25
#: V6-21 (S6-11): what the panel shows for a captured ``sensitive`` field.
SENSITIVE_MASK: Final[str] = "••••"
#: V6-30: the ``SessionContext`` attribute carrying the worker's ``LKAP_EXTRACTION_TIMEOUT_S``.
EXTRACTION_TIMEOUT_ATTR: Final[str] = "extraction_timeout_s"
#: What the timeout warning suggests.
_TIMEOUT_HINT: Final[str] = (
    "the extraction model did not answer within the budget; raise LKAP_EXTRACTION_TIMEOUT_S on the "
    "worker or set a faster workflow_llm on the agent"
)

_INSTRUCTIONS: Final[str] = (
    "Extract the following facts from the conversation between a voice agent (assistant) and a "
    "caller (user). Only use what the caller actually said or confirmed; never guess. When the "
    "caller corrected something, use the corrected value. Use null for anything not stated."
)


def extraction_budget_s(ctx: Any) -> float:
    """The session's extraction budget: the worker's setting, else :data:`EXTRACTION_BUDGET_S`."""
    value = getattr(ctx, EXTRACTION_TIMEOUT_ATTR, None)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return EXTRACTION_BUDGET_S
    budget = float(value)
    return budget if math.isfinite(budget) and budget > 0 else EXTRACTION_BUDGET_S


def field_label(spec: ExtractionField) -> str:
    """What the panel and the checklist call a field."""
    return spec.label.strip() or spec.name.replace("_", " ").strip().capitalize()


def summary_variables(config: Any, variables: Mapping[str, Any]) -> dict[str, Any]:
    """The captured variables the session summary may carry (V6-21, S6-11).

    Every one on the ``full`` storage tier; on any other tier the ``sensitive`` extraction
    fields are left out (``sessions.variables`` is not scrubbed by tier).
    """
    tier = getattr(getattr(config, "privacy", None), "storage_tier", "full")
    if tier == "full":
        return dict(variables)
    fields = getattr(getattr(config, "extraction", None), "fields", None) or []
    sensitive = {spec.name for spec in fields if getattr(spec, "sensitive", False)}
    return {name: value for name, value in variables.items() if name not in sensitive}


@dataclass(slots=True)
class ExtractionRun:
    """What one :meth:`ExtractionRunner.run` did (``None`` from ``run`` means: unchanged, skipped)."""

    status: Literal["ok", "failed", "timeout"]
    changed: list[str] = field(default_factory=list)
    still_needed: list[str] = field(default_factory=list)
    duration_ms: int = 0


def _flow_owned(config: Any) -> set[str]:
    flow = getattr(config, "flow", None)
    names: set[str] = set()
    for node in getattr(flow, "nodes", None) or []:
        if isinstance(node, AgentNode):
            names.update(node.extract)
    return names


class ExtractionRunner:
    """Extracts ``AgentConfig.extraction.fields`` for one session (see the module docstring)."""

    def __init__(self, ctx: Any, config: ExtractionConfig, *, budget_s: float | None = None) -> None:
        """Bind the runner to a session.

        Args:
            ctx: The session's ``SessionContext`` (``workflow_llm``, ``session``, ``ui``,
                ``userdata``, ``config``, ``record_event``, ``extraction_timeout_s``).
            config: ``AgentConfig.extraction`` (on, with fields).
            budget_s: The wall-clock budget of one extraction call; ``None`` reads the
                session's (:func:`extraction_budget_s`).
        """
        self.ctx = ctx
        self.config = config
        self.budget_s = budget_s if budget_s is not None else extraction_budget_s(ctx)
        owned = _flow_owned(getattr(ctx, "config", None))
        self.fields: list[ExtractionField] = [spec for spec in config.fields if spec.name not in owned]
        self._schema: type[BaseModel] | None = (
            variables_model([self._with_hint(spec) for spec in self.fields]) if self.fields else None
        )
        self._signature = json.dumps([spec.model_dump(mode="json") for spec in self.fields], sort_keys=True)
        self._last_key: str | None = None

    @staticmethod
    def _with_hint(spec: ExtractionField) -> ExtractionField:
        if not spec.hint:
            return spec
        description = f"{spec.description or field_label(spec)}. {spec.hint}".strip()
        return spec.model_copy(update={"description": description})

    # ------------------------------------------------------------------ reading

    def variables(self) -> MutableMapping[str, Any]:
        """The session's variable store (shared with tools, bindings, flows and rules)."""
        return session_variables(self.ctx)

    def still_needed(self) -> list[str]:
        """The required fields not captured yet."""
        store = self.variables()
        return [
            spec.name for spec in self.config.fields if spec.required and not is_set(store.get(spec.name))
        ]

    def _transcript(self) -> str:
        try:
            history = self.ctx.session.history
        except Exception:
            return ""
        return transcript_text(history) if isinstance(history, lk_llm.ChatContext) else ""

    def _instructions(self) -> str:
        lines = [
            f"- {spec.name} ({spec.type}): {spec.description or field_label(spec)}" for spec in self.fields
        ]
        for spec in self.fields:
            if spec.hint:
                lines.append(f"  {spec.name}: {spec.hint}")
            if spec.type == "enum" and spec.options:
                lines.append(f"  {spec.name} must be one of: {', '.join(spec.options)}")
        return f"{_INSTRUCTIONS}\nFacts:\n" + "\n".join(lines)

    # ------------------------------------------------------------------ running

    async def run(self, trigger: Trigger) -> ExtractionRun | None:
        """Extract now, unless nothing changed since the last successful run.

        Returns:
            What happened; ``None`` when there is nothing to do (no fields, an empty
            transcript, or the same transcript as last time). Never raises.
        """
        if self._schema is None:
            return None
        transcript = self._transcript()
        if not transcript.strip():
            return None
        key = hashlib.sha256(f"{self._signature}\n{transcript}".encode()).hexdigest()
        if key == self._last_key:
            _log.debug("extraction.unchanged", trigger=trigger)
            return None
        started = time.monotonic()
        try:
            result = await asyncio.wait_for(
                self.ctx.workflow_llm.extract(
                    instructions=self._instructions(),
                    input_text=transcript,
                    schema=self._schema,
                    timeout_s=self.budget_s,
                ),
                timeout=self.budget_s + _GUARD_S,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            timed_out = isinstance(exc, TimeoutError) or "timed out" in str(exc)
            run = ExtractionRun(
                status="timeout" if timed_out else "failed",
                still_needed=self.still_needed(),
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            if timed_out:
                # V6-30 (F-1): never silent; the rules reading these values wait for the next run.
                _log.warning(
                    "extraction.timeout",
                    trigger=trigger,
                    budget_s=self.budget_s,
                    duration_ms=run.duration_ms,
                    hint=_TIMEOUT_HINT,
                )
            else:
                _log.warning("extraction.failed", trigger=trigger, error_type=type(exc).__name__)
            self._record(trigger, run)
            return run
        raw = result.model_dump() if isinstance(result, BaseModel) else dict(result or {})
        changed = self._write(raw)
        self._last_key = key
        run = ExtractionRun(
            status="ok",
            changed=changed,
            still_needed=self.still_needed(),
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        await self._show(changed)
        await self._still_needed_checklist()
        self._record(trigger, run)
        _log.debug("extraction.ok", trigger=trigger, changed=changed, duration_ms=run.duration_ms)
        return run

    def _write(self, raw: dict[str, Any]) -> list[str]:
        store = self.variables()
        userdata = getattr(self.ctx, "userdata", None)
        marked: set[str] | None = None
        if isinstance(userdata, dict):
            current = userdata.setdefault(EXTRACTED_VARIABLES_USERDATA_KEY, set())
            marked = current if isinstance(current, set) else None
        changed: list[str] = []
        for spec in self.fields:
            value = coerce_variable(spec, raw.get(spec.name))
            if value is None:
                continue  # a null never overwrites a captured value
            if store.get(spec.name) != value:
                store[spec.name] = value
                changed.append(spec.name)
            if marked is not None:
                marked.add(spec.name)
        return changed

    # ------------------------------------------------------------------ the panel

    def _rows(self, block_id: str, section_id: str | None) -> list[dict[str, Any]]:
        state = self.ctx.ui.state.blocks.get(block_id) or {}
        if not isinstance(state, dict):
            return []
        if section_id is not None:
            section = (state.get("sections") or {}).get(section_id)
            state = section if isinstance(section, dict) else {}
        rows = state.get("items") or state.get("entries")
        return [row for row in rows or [] if isinstance(row, dict)]

    async def _show(self, changed: list[str]) -> None:
        """Write each changed field where it shows: a details block's row, or a notebook section's
        row (``details`` section) or keyed note (``text`` section)."""
        wanted = [spec for spec in self.fields if spec.name in changed and spec.show_in]
        if not wanted:
            return
        from lkap_agent.tools.builtin.set_details import DetailIn, detail_item  # noqa: PLC0415
        from lkap_agent.ui.blocks import (  # noqa: PLC0415 - avoids an import cycle
            new_notebook_entry_id,
            notebook_sections,
            session_block_specs,
        )

        panel = getattr(getattr(self.ctx, "config", None), "panel", None)
        specs = {
            spec.id: spec for spec in (session_block_specs(self.ctx.ui, panel) if panel is not None else [])
        }
        store = self.variables()
        now = time.time()
        ops: dict[str, list[UiPatchOp]] = {}
        for wanted_field in wanted:
            try:
                target = parse_show_in(wanted_field.show_in or "")
            except ValueError:
                continue
            block = specs.get(target.block_id)
            if block is None or str(block.type) != target.kind:
                _log.debug("extraction.show_in_skipped", field=wanted_field.name, kind=target.kind)
                continue
            value = store.get(wanted_field.name)
            text = (("yes" if value else "no") if isinstance(value, bool) else str(value))[:500]
            if wanted_field.sensitive:
                text = SENSITIVE_MASK  # V6-21 (S6-11): captured, never shown or read back
            if target.kind == "details":
                key = target.key or wanted_field.name
                existing = next((r for r in self._rows(block.id, None) if r.get("key") == key), None)
                row = detail_item(
                    DetailIn(key=key, value=text, label=field_label(wanted_field)), existing, now=now
                )
                ops.setdefault(block.id, []).append(UiPatchOp(op="upsert", path="/items", value=row, key=key))
                continue
            section = next((s for s in notebook_sections(block) if s.id == target.key), None)
            if section is None or section.kind not in ("details", "text"):
                _log.debug("extraction.show_in_skipped", field=wanted_field.name, kind="notebook_section")
                continue
            rows = self._rows(block.id, section.id)
            key = wanted_field.name
            if section.kind == "details":
                existing = next((r for r in rows if r.get("key") == key), None)
                row = detail_item(
                    DetailIn(key=key, value=text, label=field_label(wanted_field)), existing, now=now
                )
                path = f"/sections/{section.id}/items"
            else:
                existing = next((r for r in rows if r.get("key") == key), None)
                row = {
                    "id": existing.get("id") if existing else new_notebook_entry_id(),
                    "text": f"{field_label(wanted_field)}: {text}",
                    "author": "agent",
                    "key": key,
                    "ts": now,
                }
                path = f"/sections/{section.id}/entries"
            ops.setdefault(block.id, []).append(UiPatchOp(op="upsert", path=path, value=row, key=key))
        for block_id, block_ops in ops.items():
            try:
                await self.ctx.ui.patch_block(block_id, block_ops)
            except Exception:
                _log.debug("extraction.show_failed", block_id=block_id, exc_info=True)

    async def _still_needed_checklist(self) -> None:
        """Merge the ``need_<field>`` items into the checklist, keeping every other item."""
        if self.config.still_needed != "checklist":
            return
        store = self.variables()
        current: list[ChecklistItem] = list(self.ctx.ui.state.checklist)
        by_id = {item.id: index for index, item in enumerate(current)}
        items = list(current)
        for spec in self.config.fields:
            if not spec.required:
                continue
            item_id = still_needed_item_id(spec.name)
            done = is_set(store.get(spec.name))
            index = by_id.get(item_id)
            if index is None:
                items.append(
                    ChecklistItem(id=item_id, label=field_label(spec), done=done, hint=spec.hint or None)
                )
            elif items[index].done != done:
                items[index] = items[index].model_copy(update={"done": done})
        if items != current:
            try:
                await self.ctx.ui.set_checklist(items)
            except Exception:
                _log.debug("extraction.checklist_failed", exc_info=True)

    async def refresh_panel(self) -> None:
        """Show the still-needed list before the first extraction (the first caller turn)."""
        await self._still_needed_checklist()

    # ------------------------------------------------------------------ the event

    def _record(self, trigger: Trigger, run: ExtractionRun) -> None:
        store = self.variables()
        privacy = getattr(getattr(self.ctx, "config", None), "privacy", None)
        full = getattr(privacy, "storage_tier", "full") == "full"
        values: dict[str, Any] | None = None
        if full and run.changed:
            sensitive = {spec.name for spec in self.config.fields if spec.sensitive}
            values = {}
            for name in run.changed:
                if name in sensitive:
                    continue
                value = store.get(name)
                values[name] = value[:_MAX_EVENT_VALUE_CHARS] if isinstance(value, str) else value
        event = ExtractionEvent(
            trigger=trigger,
            status=run.status,
            duration_ms=run.duration_ms,
            fields={spec.name: is_set(store.get(spec.name)) for spec in self.config.fields},
            changed=list(run.changed),
            still_needed=list(run.still_needed),
            values=values or None,
        )
        try:
            self.ctx.record_event(EXTRACTION_EVENT, event.model_dump(mode="json", exclude_none=True))
        except Exception:
            _log.debug("extraction.event_failed", exc_info=True)
