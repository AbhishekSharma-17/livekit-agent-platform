import {
  ActivityIcon,
  CircleAlertIcon,
  FlagIcon,
  PhoneIcon,
  PhoneOffIcon,
  WorkflowIcon,
} from "lucide-react";

import type { EventPayload, TimelineEventKind } from "./types";

/**
 * Timeline rows for the event types the v1 worker emits besides turns, tools
 * and agent state (`agent/src/lkap_agent/observability.py`, `main.py`). The
 * v2 types (`handoff`, `block_update`, `form_submitted`, `dtmf`, `transfer`,
 * `recording`) are V2-14's to add through an extension.
 */

function text(payload: EventPayload, key: string): string | null {
  const value = payload[key];
  return typeof value === "string" && value.trim().length > 0 ? value.trim() : null;
}

function number(payload: EventPayload, key: string): number | null {
  const value = payload[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** "10 ms", "4.2 s". */
export function formatMs(ms: number | null | undefined): string | null {
  if (ms === null || ms === undefined || !Number.isFinite(ms) || ms < 0) return null;
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const seconds = ms / 1000;
  return seconds < 60 ? `${seconds.toFixed(1)} s` : `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

/** "session closed: participant_disconnected" → "Session closed: participant disconnected". */
export function humanize(value: string): string {
  const spaced = value.replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim();
  return spaced.length === 0 ? spaced : spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

const PIPELINE_MODES: Record<string, string> = {
  cascaded: "Cascaded pipeline",
  realtime: "Realtime model",
  half_cascade: "Half-cascade pipeline",
};

export const BUILTIN_EVENT_KINDS: TimelineEventKind[] = [
  {
    type: "session_started",
    filter: "other",
    icon: PhoneIcon,
    title: () => "Call started",
    summary: (payload) => {
      const mode = text(payload, "pipeline_mode");
      return mode ? (PIPELINE_MODES[mode] ?? humanize(mode)) : null;
    },
  },
  {
    type: "session_ended",
    filter: "other",
    icon: PhoneOffIcon,
    title: () => "Call ended",
    summary: (payload) => {
      const reason = text(payload, "reason");
      return reason ? humanize(reason) : null;
    },
  },
  {
    type: "workflow_run",
    filter: "tools",
    icon: WorkflowIcon,
    title: (payload) => `Workflow ${text(payload, "name") ?? "run"}`,
    summary: (payload) => {
      const status = text(payload, "status");
      const parts = [status ? humanize(status) : null, formatMs(number(payload, "duration_ms"))].filter(
        (part): part is string => Boolean(part),
      );
      return parts.length > 0 ? parts.join(" · ") : null;
    },
  },
  {
    type: "metrics",
    filter: "other",
    icon: ActivityIcon,
    collapse: true,
    title: (payload) => (text(payload, "kind") === "session_usage" ? "Usage updated" : "Metrics"),
  },
  {
    type: "error",
    filter: "errors",
    tone: "danger",
    icon: CircleAlertIcon,
    title: () => "Error",
    summary: (payload) => text(payload, "message") ?? text(payload, "error"),
  },
  {
    type: "escalation",
    filter: "errors",
    tone: "warning",
    icon: FlagIcon,
    title: () => "Escalated",
    summary: (payload) => text(payload, "reason") ?? text(payload, "message"),
  },
];
