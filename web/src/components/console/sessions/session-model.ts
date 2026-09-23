import type { StatusTone } from "@/components/shared/status-chip";
import type { SessionOut } from "@/contracts/lkap-contracts";
import { toMillis } from "@/lib/format";

/**
 * Pure helpers shared by the sessions list and the session detail
 * (docs/UI_UX_SPEC.md §4.10, §7.8). No React here so the tests can hit them
 * directly.
 */

export type SessionStatus = SessionOut["status"];
export type SessionChannel = NonNullable<SessionOut["channel"]>;

export const SESSION_STATUS_LABEL: Record<SessionStatus, string> = {
  created: "Created",
  active: "Active",
  ended: "Ended",
  failed: "Failed",
};

export const SESSION_STATUS_TONE: Record<SessionStatus, StatusTone> = {
  created: "neutral",
  active: "live",
  ended: "neutral",
  failed: "danger",
};

/** CONTRACTS-V2 `SessionOut.channel`, in the order the filter lists them. */
export const CHANNEL_LABEL: Record<SessionChannel, string> = {
  web: "Web",
  test: "Test call",
  text: "Text",
  sip_in: "Phone (inbound)",
  sip_out: "Phone (outbound)",
  widget: "Widget",
  api: "API",
};

export const CHANNELS = Object.keys(CHANNEL_LABEL) as SessionChannel[];

export const PIPELINE_MODE_LABEL: Record<SessionOut["pipeline_mode"], string> = {
  cascaded: "Cascaded",
  realtime: "Realtime",
  half_cascade: "Half-cascade",
};

export function channelLabel(channel: string | null | undefined): string | null {
  if (!channel) return null;
  return CHANNEL_LABEL[channel as SessionChannel] ?? channel;
}

export function pipelineModeLabel(mode: string | null | undefined): string {
  if (!mode) return "—";
  return PIPELINE_MODE_LABEL[mode as SessionOut["pipeline_mode"]] ?? mode;
}

/**
 * `failed` rows the stale-session sweep produced (DECISIONS-W2 D-W2-2b) get
 * a muted chip instead of the destructive "Failed" look — they never ran a
 * real call, so they must not read as an error the way a mid-call crash does.
 */
export const SWEPT_ERRORS = new Set(["never started", "summary never received"]);

export function sweptReason(session: Pick<SessionOut, "status" | "error">): string | null {
  return session.status === "failed" && session.error && SWEPT_ERRORS.has(session.error) ? session.error : null;
}

/** "never started" → "Never started". */
export function sentenceCase(text: string): string {
  return text.length === 0 ? text : text.charAt(0).toUpperCase() + text.slice(1);
}

/** Status chip tone, with swept failures muted. */
export function sessionStatusTone(session: Pick<SessionOut, "status" | "error">): StatusTone {
  return sweptReason(session) ? "neutral" : SESSION_STATUS_TONE[session.status];
}

/**
 * `ended_at − started_at` in ms; `null` when the session never started (the
 * list shows "—"). An active session measures to `now`.
 */
export function sessionDurationMs(
  session: Pick<SessionOut, "started_at" | "ended_at" | "status">,
  now: number = Date.now(),
): number | null {
  if (!session.started_at) return null;
  const start = toMillis(session.started_at);
  if (Number.isNaN(start)) return null;
  const end = session.ended_at ? toMillis(session.ended_at) : session.status === "active" ? now : Number.NaN;
  if (Number.isNaN(end) || end < start) return null;
  return end - start;
}

/** The timeline's zero: `started_at`, falling back to `created_at` (swept sessions). */
export function sessionOriginMs(session: Pick<SessionOut, "started_at" | "created_at">): number {
  const started = session.started_at ? toMillis(session.started_at) : Number.NaN;
  return Number.isNaN(started) ? toMillis(session.created_at) : started;
}

/**
 * Turn count for the list: `usage` is an untyped dict (CONTRACTS §7) whose
 * keys depend on the SDK's `AgentSessionUsage`; use a turn counter only when
 * one is actually there.
 */
export function usageTurns(usage: SessionOut["usage"]): number | null {
  if (!usage) return null;
  for (const key of ["turns", "turn_count", "num_turns", "user_turns"]) {
    const value = usage[key];
    if (typeof value === "number" && Number.isFinite(value)) return value;
  }
  return null;
}

/** "$0.0123" for small amounts, "$1.23" otherwise; `null` when unknown. */
export function formatUsd(value: number | string | null | undefined): string | null {
  if (value === null || value === undefined || value === "") return null;
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return null;
  const digits = n !== 0 && Math.abs(n) < 1 ? 4 : 2;
  return `$${n.toFixed(digits)}`;
}

// ---- list filters ----

export type SessionRange = "" | "today" | "7d" | "30d";

export const RANGE_OPTIONS: { value: SessionRange; label: string }[] = [
  { value: "", label: "All time" },
  { value: "today", label: "Today" },
  { value: "7d", label: "Last 7 days" },
  { value: "30d", label: "Last 30 days" },
];

/** Epoch ms lower bound of a range, or `null` for "all time". "Today" is the viewer's local midnight. */
export function rangeStart(range: SessionRange, now: number = Date.now()): number | null {
  switch (range) {
    case "today": {
      const midnight = new Date(now);
      midnight.setHours(0, 0, 0, 0);
      return midnight.getTime();
    }
    case "7d":
      return now - 7 * 24 * 60 * 60 * 1000;
    case "30d":
      return now - 30 * 24 * 60 * 60 * 1000;
    default:
      return null;
  }
}

export interface SessionFilters {
  agentId: string;
  status: string;
  channel: string;
  connectionId: string;
  range: SessionRange;
}

export const EMPTY_FILTERS: SessionFilters = { agentId: "", status: "", channel: "", connectionId: "", range: "" };

/**
 * Client-side filtering. The api filters by agent and status, but not yet by
 * channel, connection or date (CONTRACTS-V2 §3 lists them; the router does
 * not implement them), so the list fetches one page and filters here.
 */
export function filterSessions(sessions: SessionOut[], filters: SessionFilters, now: number = Date.now()): SessionOut[] {
  const since = rangeStart(filters.range, now);
  return sessions.filter((session) => {
    if (filters.agentId && session.agent_id !== filters.agentId) return false;
    if (filters.status && session.status !== filters.status) return false;
    if (filters.channel && (session.channel ?? "web") !== filters.channel) return false;
    if (filters.connectionId && session.connection_id !== filters.connectionId) return false;
    if (since !== null && toMillis(session.created_at) < since) return false;
    return true;
  });
}

export const PAGE_SIZE = 25;

export function pageCount(total: number, pageSize: number = PAGE_SIZE): number {
  return Math.max(1, Math.ceil(total / pageSize));
}

/** 1-based page, clamped. */
export function paginate<T>(rows: T[], page: number, pageSize: number = PAGE_SIZE): T[] {
  const last = pageCount(rows.length, pageSize);
  const current = Math.min(Math.max(1, page), last);
  return rows.slice((current - 1) * pageSize, current * pageSize);
}
