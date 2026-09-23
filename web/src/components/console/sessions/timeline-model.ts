import type { SessionDetailOut, SessionEventOut } from "@/contracts/lkap-contracts";
import { toMillis } from "@/lib/format";

import type { EventPayload, TimelineEventKind, TimelineFilter } from "./detail/types";
import { sessionOriginMs } from "./session-model";

/**
 * The unified timeline (docs/UI_UX_SPEC.md §4.10, §7.8): transcript turns and
 * worker events merged into one list sorted by `toMillis`, tool calls paired
 * by `call_id`, agent state changes folded into a thin track, a minute marker
 * whenever the minute changes. Pure — the component only renders the result.
 *
 * Turn source: the worker records every turn twice — once live as a
 * `user_turn`/`agent_turn` event and once in the final `transcript` it posts
 * at the end. Rendering both would show every line twice, so the stored
 * transcript wins when it has turns (its `ts` is when the turn began, the
 * right anchor for reading the call in order) and the turn events are the
 * fallback when no transcript was posted (e.g. the worker died).
 */

export interface TurnEntry {
  kind: "turn";
  key: string;
  at: number;
  seq: number;
  role: "user" | "assistant";
  text: string;
  interrupted: boolean;
}

export interface ToolEntry {
  kind: "tool";
  key: string;
  at: number;
  seq: number;
  callId: string | null;
  tool: string;
  /** `tool_call_ended.status`; `null` when no end event arrived. */
  status: string | null;
  durationMs: number | null;
  args: unknown;
  result: string | null;
  started: SessionEventOut | null;
  ended: SessionEventOut | null;
}

export interface StateTransition {
  at: number;
  state: string;
  id: number;
}

export interface StateEntry {
  kind: "state";
  key: string;
  at: number;
  seq: number;
  transitions: StateTransition[];
}

export interface EventEntry {
  kind: "event";
  key: string;
  at: number;
  seq: number;
  type: string;
  filter: TimelineFilter;
  eventKind: TimelineEventKind | null;
  /** Every event folded into this row (more than one for `collapse` kinds). */
  events: SessionEventOut[];
}

export type TimelineEntry = TurnEntry | ToolEntry | StateEntry | EventEntry;

export interface MinuteMarker {
  kind: "minute";
  key: string;
  minute: number;
  at: number;
}

export type TimelineRow = TimelineEntry | MinuteMarker;

export const TIMELINE_FILTERS: { id: TimelineFilter; label: string }[] = [
  { id: "turns", label: "Turns" },
  { id: "tools", label: "Tools" },
  { id: "state", label: "State" },
  { id: "errors", label: "Errors" },
  { id: "other", label: "Other" },
];

export const ALL_FILTERS: ReadonlySet<TimelineFilter> = new Set(TIMELINE_FILTERS.map((filter) => filter.id));

function isRecord(value: unknown): value is EventPayload {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function payloadOf(event: SessionEventOut): EventPayload {
  return isRecord(event.payload) ? event.payload : {};
}

function str(payload: EventPayload, key: string): string | null {
  const value = payload[key];
  return typeof value === "string" ? value : null;
}

/** Turns from the stored transcript, or from turn events when there is none. */
export function collectTurns(
  transcript: SessionDetailOut["transcript"],
  events: readonly SessionEventOut[],
): TurnEntry[] {
  if (transcript && transcript.length > 0) {
    return transcript
      .filter((turn) => typeof turn.text === "string" && turn.text.trim().length > 0)
      .map((turn, index) => ({
        kind: "turn" as const,
        key: `turn-t${index}`,
        at: toMillis(turn.ts),
        seq: index,
        role: turn.role === "assistant" ? ("assistant" as const) : ("user" as const),
        text: turn.text,
        interrupted: Boolean(turn.interrupted),
      }));
  }
  return events
    .filter((event) => event.type === "user_turn" || event.type === "agent_turn")
    .map((event) => {
      const payload = payloadOf(event);
      return {
        kind: "turn" as const,
        key: `turn-e${event.id}`,
        at: toMillis(event.ts),
        seq: event.id,
        role: event.type === "agent_turn" ? ("assistant" as const) : ("user" as const),
        text: str(payload, "text") ?? "",
        interrupted: payload.interrupted === true,
      };
    })
    .filter((turn) => turn.text.trim().length > 0);
}

/** Pair `tool_call_started`/`tool_call_ended` by `call_id` into one row each. */
export function pairToolCalls(events: readonly SessionEventOut[]): ToolEntry[] {
  const rows: ToolEntry[] = [];
  const open = new Map<string, ToolEntry>();

  for (const event of events) {
    if (event.type !== "tool_call_started" && event.type !== "tool_call_ended") continue;
    const payload = payloadOf(event);
    const callId = str(payload, "call_id");
    const at = toMillis(event.ts);

    if (event.type === "tool_call_started") {
      const row: ToolEntry = {
        kind: "tool",
        key: `tool-${event.id}`,
        at,
        seq: event.id,
        callId,
        tool: str(payload, "tool") ?? "tool",
        status: null,
        durationMs: null,
        args: payload.args_redacted ?? payload.args ?? null,
        result: null,
        started: event,
        ended: null,
      };
      rows.push(row);
      if (callId) open.set(callId, row);
      continue;
    }

    const durationPayload = payload.duration_ms;
    const status = str(payload, "status");
    const result = str(payload, "result_preview");
    const match = callId ? open.get(callId) : undefined;
    if (match) {
      open.delete(callId as string);
      match.ended = event;
      match.status = status;
      match.result = result;
      match.durationMs = typeof durationPayload === "number" ? durationPayload : Math.max(0, at - match.at);
      if (match.tool === "tool" && str(payload, "tool")) match.tool = str(payload, "tool") as string;
    } else {
      // An end without a start (the start fell outside the fetched events).
      rows.push({
        kind: "tool",
        key: `tool-${event.id}`,
        at,
        seq: event.id,
        callId,
        tool: str(payload, "tool") || "tool",
        status,
        durationMs: typeof durationPayload === "number" ? durationPayload : null,
        args: null,
        result,
        started: null,
        ended: event,
      });
    }
  }
  return rows;
}

/** Every timeline entry, unfiltered and ungrouped, in time order. */
export function collectEntries(
  session: Pick<SessionDetailOut, "transcript">,
  events: readonly SessionEventOut[],
  kinds: ReadonlyMap<string, TimelineEventKind>,
): TimelineEntry[] {
  const entries: TimelineEntry[] = [...collectTurns(session.transcript, events), ...pairToolCalls(events)];

  for (const event of events) {
    switch (event.type) {
      case "user_turn":
      case "agent_turn":
      case "tool_call_started":
      case "tool_call_ended":
        continue;
      case "agent_state": {
        const state = str(payloadOf(event), "state");
        if (!state) continue;
        const at = toMillis(event.ts);
        entries.push({ kind: "state", key: `state-${event.id}`, at, seq: event.id, transitions: [{ at, state, id: event.id }] });
        continue;
      }
      default: {
        const eventKind = kinds.get(event.type) ?? null;
        if (eventKind?.hidden) continue;
        entries.push({
          kind: "event",
          key: `event-${event.id}`,
          at: toMillis(event.ts),
          seq: event.id,
          type: event.type,
          filter: eventKind?.filter ?? "other",
          eventKind,
          events: [event],
        });
      }
    }
  }

  return entries
    .filter((entry) => !Number.isNaN(entry.at))
    .sort((a, b) => a.at - b.at || a.seq - b.seq || a.key.localeCompare(b.key));
}

export function entryFilter(entry: TimelineEntry): TimelineFilter {
  switch (entry.kind) {
    case "turn":
      return "turns";
    case "tool":
      return "tools";
    case "state":
      return "state";
    case "event":
      return entry.filter;
  }
}

export function countByFilter(entries: readonly TimelineEntry[]): Record<TimelineFilter, number> {
  const counts: Record<TimelineFilter, number> = { turns: 0, tools: 0, state: 0, errors: 0, other: 0 };
  for (const entry of entries) counts[entryFilter(entry)] += 1;
  return counts;
}

/**
 * Keep the active filters, then fold runs: consecutive state entries become
 * one track; consecutive events of a `collapse` kind become one row.
 */
export function applyFilters(entries: readonly TimelineEntry[], active: ReadonlySet<TimelineFilter>): TimelineEntry[] {
  const out: TimelineEntry[] = [];
  for (const entry of entries) {
    if (!active.has(entryFilter(entry))) continue;
    const previous = out[out.length - 1];
    if (entry.kind === "state" && previous?.kind === "state") {
      out[out.length - 1] = { ...previous, transitions: [...previous.transitions, ...entry.transitions] };
      continue;
    }
    if (
      entry.kind === "event" &&
      entry.eventKind?.collapse &&
      previous?.kind === "event" &&
      previous.type === entry.type
    ) {
      out[out.length - 1] = { ...previous, events: [...previous.events, ...entry.events] };
      continue;
    }
    out.push(entry);
  }
  return out;
}

/** Whole minutes since the origin (negative offsets count as minute 0). */
export function minuteOf(at: number, origin: number): number {
  return Math.max(0, Math.floor((at - origin) / 60_000));
}

/** Insert a marker before the first entry of every minute. */
export function withMinuteMarkers(entries: readonly TimelineEntry[], origin: number): TimelineRow[] {
  const rows: TimelineRow[] = [];
  let current: number | null = null;
  for (const entry of entries) {
    const minute = minuteOf(entry.at, origin);
    if (minute !== current) {
      rows.push({ kind: "minute", key: `minute-${minute}`, minute, at: origin + minute * 60_000 });
      current = minute;
    }
    rows.push(entry);
  }
  return rows;
}

export function buildTimeline(
  session: Pick<SessionDetailOut, "transcript" | "started_at" | "created_at">,
  events: readonly SessionEventOut[],
  kinds: ReadonlyMap<string, TimelineEventKind>,
  active: ReadonlySet<TimelineFilter> = ALL_FILTERS,
): { entries: TimelineEntry[]; rows: TimelineRow[]; counts: Record<TimelineFilter, number>; origin: number } {
  const origin = sessionOriginMs(session);
  const entries = collectEntries(session, events, kinds);
  const visible = applyFilters(entries, active);
  return { entries, rows: withMinuteMarkers(visible, origin), counts: countByFilter(entries), origin };
}

/** "03:07" from the session start ("−00:02" before it, "1:02:03" past an hour). */
export function formatOffset(at: number, origin: number): string {
  if (!Number.isFinite(at) || !Number.isFinite(origin)) return "—";
  const diff = at - origin;
  const sign = diff < 0 ? "−" : "";
  const total = Math.floor(Math.abs(diff) / 1000);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return hours > 0 ? `${sign}${hours}:${pad(minutes)}:${pad(seconds)}` : `${sign}${pad(minutes)}:${pad(seconds)}`;
}

export const STATE_WORDS: Record<string, string> = {
  initializing: "Starting",
  idle: "Idle",
  listening: "Listening",
  thinking: "Thinking",
  speaking: "Speaking",
  connecting: "Connecting",
  reconnecting: "Reconnecting",
  failed: "Failed",
  ended: "Ended",
};

export function stateWord(state: string): string {
  return STATE_WORDS[state] ?? state.charAt(0).toUpperCase() + state.slice(1);
}

/**
 * "Listening → Thinking → Speaking ×12": consecutive duplicates dropped, a
 * repeating cycle shown once with its count, long irregular runs shortened.
 */
export function summarizeStates(states: readonly string[]): string {
  const deduped: string[] = [];
  for (const state of states) if (deduped[deduped.length - 1] !== state) deduped.push(state);
  if (deduped.length === 0) return "";

  for (let period = 2; period <= Math.floor(deduped.length / 2); period += 1) {
    const cycle = deduped.slice(0, period);
    let repeats = 0;
    let index = 0;
    while (index + period <= deduped.length && deduped.slice(index, index + period).every((s, i) => s === cycle[i])) {
      repeats += 1;
      index += period;
    }
    const tail = deduped.slice(index);
    const tailFits = tail.every((s, i) => s === cycle[i]);
    if (repeats >= 2 && tailFits) {
      return `${cycle.map(stateWord).join(" → ")} ×${repeats}${tail.length > 0 ? ` → ${tail.map(stateWord).join(" → ")}` : ""}`;
    }
  }

  const words = deduped.map(stateWord);
  if (words.length <= 5) return words.join(" → ");
  return `${words.slice(0, 3).join(" → ")} → … → ${words[words.length - 1]} (${words.length} changes)`;
}

/** Tool status → chip tone and label. */
export function toolStatus(status: string | null): { tone: "success" | "danger" | "warning" | "neutral"; label: string } {
  if (status === null) return { tone: "neutral", label: "No result" };
  const normalized = status.toLowerCase();
  if (["done", "success", "succeeded", "ok", "completed", "complete"].includes(normalized)) {
    return { tone: "success", label: "Done" };
  }
  if (["error", "failed", "failure", "exception"].includes(normalized)) return { tone: "danger", label: "Failed" };
  if (["cancelled", "canceled", "interrupted", "timeout", "timed_out"].includes(normalized)) {
    return { tone: "warning", label: normalized.startsWith("time") ? "Timed out" : "Cancelled" };
  }
  return { tone: "neutral", label: status.charAt(0).toUpperCase() + status.slice(1) };
}

/** Pretty JSON for a Details disclosure; strings that hold JSON are parsed first. */
export function prettyJson(value: unknown): string {
  if (typeof value === "string") {
    const trimmed = value.trim();
    if ((trimmed.startsWith("{") && trimmed.endsWith("}")) || (trimmed.startsWith("[") && trimmed.endsWith("]"))) {
      try {
        return JSON.stringify(JSON.parse(trimmed), null, 2);
      } catch {
        return value;
      }
    }
    return value;
  }
  try {
    return JSON.stringify(value, null, 2) ?? String(value);
  } catch {
    return String(value);
  }
}
