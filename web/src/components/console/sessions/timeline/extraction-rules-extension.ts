import { ClipboardListIcon, SplitIcon } from "lucide-react";

import { humanize } from "@/components/console/sessions/detail/builtin-event-kinds";
import type { EventPayload, SessionDetailExtension, TimelineEventKind } from "@/components/console/sessions/detail/types";
import { variableLabel } from "@/components/console/tools/tool-context";

/**
 * V6-15 (`docs/v6/PLAN-V6.md` V6-15, D-V6-24/25): the timeline rows for the `extraction`
 * (`lkap_contracts.extraction.ExtractionEvent`) and `rule_fired`
 * (`lkap_contracts.rules.RuleFiredEvent`) session events V6-13 added. Both render as plain
 * `TimelineEventKind`s (title/summary) rather than their own components — neither carries
 * enough shape-specific chrome (a stage, a tone-bearing action) to need the `ConsentRow`/
 * `GuardrailRow` treatment in `session-timeline.tsx`. Neither `title` nor `summary` ever
 * reads a captured value: only field/variable *names* and counts. A row's raw payload
 * (including `values`, present only on the `full` storage tier and never for a `sensitive`
 * field) stays reachable through the row's own "Details" disclosure, same as every other
 * event kind — so a value only ever shows where the payload actually carries one.
 *
 * This file lives under `sessions/timeline/` (the location V6-15's card names); it plugs
 * into the shared, append-only `sessions/detail/extensions.ts` the same way
 * `sessions-v2/extension.ts` already does.
 */

const ACTION_LABEL: Record<string, string> = {
  "checklist.set_item": "updated the checklist",
  "checklist.check": "checked off an item",
  "status.set": "changed the status",
  "details.set": "updated a details block",
  "note.push": "added a note",
  "var.set": "set a value",
  escalate: "handed off to a person",
  instruct: "gave the agent a note",
  "disposition.set": "set the outcome",
};

function actionWords(actions: unknown): string[] {
  return Array.isArray(actions) ? actions.filter((a): a is string => typeof a === "string").map((a) => ACTION_LABEL[a] ?? humanize(a)) : [];
}

function names(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string").map(variableLabel) : [];
}

const TRIGGER_LABEL: Record<string, string> = {
  turn: "after a caller turn",
  tool: "after a tool call",
  node_exit: "after a step ended",
  manual: "on request",
  extraction: "after new details were captured",
  variables: "after a value changed",
};

function extractionSummary(payload: EventPayload) {
  const status = payload.status;
  if (status === "timeout") return "Took too long to finish.";
  if (status === "failed") return "Couldn't finish this time.";
  const fields = payload.fields;
  const parts: string[] = [];
  if (fields && typeof fields === "object" && !Array.isArray(fields)) {
    const values = Object.values(fields as Record<string, unknown>).filter((v) => v === true).length;
    const total = Object.keys(fields as Record<string, unknown>).length;
    if (total > 0) parts.push(`${values} of ${total} set`);
  }
  const changed = names(payload.changed);
  if (changed.length > 0) parts.push(`updated ${changed.join(", ")}`);
  const stillNeeded = names(payload.still_needed);
  if (stillNeeded.length > 0) parts.push(`still needs ${stillNeeded.join(", ")}`);
  return parts.length > 0 ? parts.join(" · ") : null;
}

export const EXTRACTION_RULES_EVENT_KINDS: TimelineEventKind[] = [
  {
    type: "extraction",
    filter: "other",
    icon: ClipboardListIcon,
    title: (payload) => {
      const trigger = typeof payload.trigger === "string" ? payload.trigger : null;
      return trigger && TRIGGER_LABEL[trigger] ? `Captured details (${TRIGGER_LABEL[trigger]})` : "Captured details";
    },
    summary: extractionSummary,
  },
  {
    type: "rule_fired",
    filter: "other",
    icon: SplitIcon,
    title: (payload) => (typeof payload.label === "string" && payload.label ? payload.label : "A rule fired"),
    summary: (payload) => {
      const parts: string[] = [];
      const actions = actionWords(payload.actions);
      if (actions.length > 0) parts.push(actions.join(", "));
      const skipped = actionWords(payload.skipped);
      if (skipped.length > 0) parts.push(`couldn't: ${skipped.join(", ")}`);
      const trigger = typeof payload.trigger === "string" ? payload.trigger : null;
      if (trigger && TRIGGER_LABEL[trigger]) parts.push(TRIGGER_LABEL[trigger]);
      return parts.length > 0 ? parts.join(" — ") : null;
    },
  },
];

export const extractionRulesExtension: SessionDetailExtension = {
  id: "V6-15",
  eventKinds: EXTRACTION_RULES_EVENT_KINDS,
};
