import * as React from "react";

import { describe, expect, it } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { ClockIcon, ListTreeIcon } from "lucide-react";

import { BUILTIN_EVENT_KINDS, formatMs, humanize } from "@/components/console/sessions/detail/builtin-event-kinds";
import {
  CORE_EVENT_TYPES,
  pickTab,
  resolveEventKinds,
  resolveSessionDetailSlots,
  resolveSessionTabs,
  visibleTabs,
} from "@/components/console/sessions/detail/registry";
import { DEFAULT_SESSION_TAB_ID, sessionTabs } from "@/components/console/sessions/detail/resolved";
import type { SessionDetailExtension, SessionTabDef } from "@/components/console/sessions/detail/types";
import { TimelineView } from "@/components/console/sessions/session-timeline";
import {
  ALL_FILTERS,
  applyFilters,
  buildTimeline,
  collectEntries,
  collectTurns,
  countByFilter,
  formatOffset,
  pairToolCalls,
  prettyJson,
  summarizeStates,
  toolStatus,
  withMinuteMarkers,
  type EventEntry,
  type StateEntry,
  type TurnEntry,
} from "@/components/console/sessions/timeline-model";
import type { SessionDetailOut, SessionEventOut } from "@/contracts/lkap-contracts";

/**
 * The unified session timeline (docs/UI_UX_SPEC.md §7.8 item 2, item 4):
 * merge order across ISO and epoch-seconds inputs, tool pairing, filters,
 * the state track, minute markers — plus the WP-7 tab registry V2-14 plugs
 * into (docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md §3).
 */

const START_ISO = "2026-09-18T20:17:06.000Z";
const START = Date.parse(START_ISO);
const iso = (offsetMs: number) => new Date(START + offsetMs).toISOString();
const epochSeconds = (offsetMs: number) => (START + offsetMs) / 1000;

let nextId = 1;
function ev(type: string, offsetMs: number, payload: Record<string, unknown> = {}): SessionEventOut {
  return { id: nextId++, type, ts: iso(offsetMs), payload };
}

function detail(overrides: Partial<SessionDetailOut> = {}): SessionDetailOut {
  return {
    id: "s-1",
    agent_id: "a-1",
    agent_name: "Claims desk",
    config_version: 1,
    room_name: "lkap-s1",
    status: "ended",
    pipeline_mode: "cascaded",
    created_at: iso(-2000),
    started_at: START_ISO,
    ended_at: iso(90_000),
    usage: null,
    error: null,
    transcript: null,
    final_ui_state: null,
    ...overrides,
  };
}

const KINDS = resolveEventKinds(BUILTIN_EVENT_KINDS);

describe("collectTurns — transcript vs turn events", () => {
  it("uses the stored transcript (epoch seconds) and ignores duplicate turn events", () => {
    const events = [ev("user_turn", 1500, { text: "Hi" }), ev("agent_turn", 30_000, { text: "Hello" })];
    const turns = collectTurns(
      [
        { role: "user", text: "Hi", ts: epochSeconds(1000) },
        { role: "assistant", text: "Hello", ts: epochSeconds(2000), interrupted: true },
      ],
      events,
    );
    expect(turns.map((t) => [t.role, t.text, t.at])).toEqual([
      ["user", "Hi", START + 1000],
      ["assistant", "Hello", START + 2000],
    ]);
    expect(turns[1].interrupted).toBe(true);
  });

  it("falls back to user_turn/agent_turn events when no transcript was posted", () => {
    const turns = collectTurns(null, [
      ev("agent_turn", 3000, { text: "Hello", interrupted: true }),
      ev("user_turn", 1000, { text: "Hi" }),
      ev("user_turn", 4000, { text: "   " }),
    ]);
    expect(turns.map((t) => t.text)).toEqual(["Hello", "Hi"]);
    expect(turns[0].interrupted).toBe(true);
    expect(collectTurns([], [ev("user_turn", 1, { text: "x" })])).toHaveLength(1);
  });
});

describe("collectEntries — merge order", () => {
  it("sorts transcript epoch-seconds turns and ISO events on one clock", () => {
    const events = [
      ev("agent_state", 500, { state: "listening" }),
      ev("tool_call_started", 2500, { call_id: "c1", tool: "lookup_policy", args_redacted: { n: 1 } }),
      ev("tool_call_ended", 2510, { call_id: "c1", tool: "lookup_policy", status: "done", duration_ms: 10 }),
      ev("session_ended", 9000, { reason: "participant_disconnected" }),
    ];
    const entries = collectEntries(
      { transcript: [{ role: "user", text: "Hi", ts: epochSeconds(1000) }, { role: "assistant", text: "Sure", ts: epochSeconds(3000) }] },
      events,
      KINDS,
    );
    expect(entries.map((e) => e.kind)).toEqual(["state", "turn", "tool", "turn", "event"]);
    expect(entries.map((e) => e.at)).toEqual([START + 500, START + 1000, START + 2500, START + 3000, START + 9000]);
  });

  it("breaks same-millisecond ties by event id", () => {
    const a = ev("agent_state", 100, { state: "listening" });
    const b = ev("session_started", 100, { pipeline_mode: "cascaded" });
    const entries = collectEntries({ transcript: null }, [b, a].sort((x, y) => y.id - x.id), KINDS);
    expect(entries.map((e) => e.key)).toEqual([`state-${a.id}`, `event-${b.id}`]);
  });

  it("drops kinds marked hidden and events with unparseable timestamps", () => {
    const hidden = resolveEventKinds([{ type: "noise", filter: "other", hidden: true, title: () => "Noise" }]);
    const entries = collectEntries(
      { transcript: null },
      [ev("noise", 10), { id: 999, type: "custom", ts: "not a date", payload: {} }],
      hidden,
    );
    expect(entries).toEqual([]);
  });
});

describe("pairToolCalls", () => {
  it("pairs start and end by call_id into one row with duration, status and result", () => {
    const rows = pairToolCalls([
      ev("tool_call_started", 1000, { call_id: "a", tool: "lookup_policy", args_redacted: { policy: "H0-1" } }),
      ev("tool_call_started", 1100, { call_id: "b", tool: "describe_frame" }),
      ev("tool_call_ended", 1200, { call_id: "b", status: "error", result_preview: "boom" }),
      ev("tool_call_ended", 4100, { call_id: "a", tool: "lookup_policy", status: "done", duration_ms: 3097 }),
    ]);
    expect(rows).toHaveLength(2);
    const [a, b] = rows;
    expect([a.tool, a.status, a.durationMs, a.args]).toEqual(["lookup_policy", "done", 3097, { policy: "H0-1" }]);
    // No duration_ms in the end payload → measured from the event timestamps.
    expect([b.tool, b.status, b.durationMs, b.result]).toEqual(["describe_frame", "error", 100, "boom"]);
  });

  it("keeps an unanswered start (no result) and an orphan end as their own rows", () => {
    const rows = pairToolCalls([
      ev("tool_call_started", 1000, { call_id: "x", tool: "slow_tool" }),
      ev("tool_call_ended", 2000, { call_id: "y", tool: "late_tool", status: "done", duration_ms: 5 }),
    ]);
    expect(rows.map((r) => [r.tool, r.status, r.started !== null, r.ended !== null])).toEqual([
      ["slow_tool", null, true, false],
      ["late_tool", "done", false, true],
    ]);
  });

  it("maps statuses to chip tones", () => {
    expect(toolStatus("done")).toEqual({ tone: "success", label: "Done" });
    expect(toolStatus("error").tone).toBe("danger");
    expect(toolStatus("cancelled")).toEqual({ tone: "warning", label: "Cancelled" });
    expect(toolStatus(null)).toEqual({ tone: "neutral", label: "No result" });
    expect(toolStatus("queued").label).toBe("Queued");
  });
});

describe("filters and folding", () => {
  const events = [
    ev("agent_state", 100, { state: "listening" }),
    ev("agent_state", 200, { state: "thinking" }),
    ev("metrics", 300, { kind: "session_usage" }),
    ev("metrics", 400, { kind: "session_usage" }),
    ev("metrics", 500, { kind: "session_usage" }),
    ev("agent_state", 600, { state: "speaking" }),
    ev("error", 700, { message: "LLM timeout" }),
    ev("user_turn", 800, { text: "Hi" }),
    ev("tool_call_started", 900, { call_id: "c", tool: "t" }),
    ev("mystery_type", 1000, { a: 1 }),
  ];

  it("counts entries per filter chip before folding", () => {
    const entries = collectEntries({ transcript: null }, events, KINDS);
    expect(countByFilter(entries)).toEqual({ turns: 1, tools: 1, state: 3, errors: 1, other: 4 });
  });

  it("folds consecutive state changes into one track and consecutive usage snapshots into one ×n row", () => {
    const entries = applyFilters(collectEntries({ transcript: null }, events, KINDS), ALL_FILTERS);
    expect(entries.map((e) => e.kind)).toEqual(["state", "event", "state", "event", "turn", "tool", "event"]);
    expect((entries[0] as StateEntry).transitions.map((t) => t.state)).toEqual(["listening", "thinking"]);
    expect((entries[1] as EventEntry).events).toHaveLength(3);
    // Unknown types fall into "Other" without a kind.
    expect((entries[6] as EventEntry).eventKind).toBeNull();
    expect((entries[6] as EventEntry).filter).toBe("other");
  });

  it("keeps only the active filters; state changes become contiguous and fold into one track", () => {
    const stateOnly = applyFilters(collectEntries({ transcript: null }, events, KINDS), new Set(["state"]));
    expect(stateOnly).toHaveLength(1);
    expect((stateOnly[0] as StateEntry).transitions).toHaveLength(3);

    const errorsAndTurns = applyFilters(collectEntries({ transcript: null }, events, KINDS), new Set(["errors", "turns"]));
    expect(errorsAndTurns.map((e) => e.kind)).toEqual(["event", "turn"]);
  });

  it("inserts a minute marker whenever the minute changes", () => {
    const entries = collectEntries(
      { transcript: null },
      [ev("user_turn", 5_000, { text: "a" }), ev("user_turn", 59_000, { text: "b" }), ev("user_turn", 61_000, { text: "c" }), ev("user_turn", 185_000, { text: "d" })],
      KINDS,
    );
    const rows = withMinuteMarkers(entries, START);
    expect(rows.map((r) => (r.kind === "minute" ? `m${r.minute}` : (r as TurnEntry).text))).toEqual([
      "m0", "a", "b", "m1", "c", "m3", "d",
    ]);
  });

  it("buildTimeline uses created_at as the origin when the session never started", () => {
    const { origin } = buildTimeline(detail({ started_at: null }), [], KINDS);
    expect(origin).toBe(START - 2000);
  });
});

describe("formatting helpers", () => {
  it("formats offsets as mm:ss from the session start", () => {
    expect(formatOffset(START + 7_900, START)).toBe("00:07");
    expect(formatOffset(START + 125_000, START)).toBe("02:05");
    expect(formatOffset(START + 3_723_000, START)).toBe("1:02:03");
    expect(formatOffset(START - 2_000, START)).toBe("−00:02");
    expect(formatOffset(Number.NaN, START)).toBe("—");
  });

  it("summarises state runs, compressing a repeating cycle", () => {
    expect(summarizeStates(["listening"])).toBe("Listening");
    expect(summarizeStates(["listening", "thinking", "thinking", "speaking"])).toBe("Listening → Thinking → Speaking");
    const cycle = Array.from({ length: 12 }, () => ["listening", "thinking", "speaking"]).flat();
    expect(summarizeStates(cycle)).toBe("Listening → Thinking → Speaking ×12");
    expect(summarizeStates([...cycle, "listening"])).toBe("Listening → Thinking → Speaking ×12 → Listening");
  });

  it("pretty-prints JSON, including JSON held in a string", () => {
    expect(prettyJson({ a: 1 })).toBe('{\n  "a": 1\n}');
    expect(prettyJson('{"found": true}')).toBe('{\n  "found": true\n}');
    expect(prettyJson("plain text")).toBe("plain text");
    expect(prettyJson('{"truncated": tr')).toBe('{"truncated": tr');
  });

  it("formats millisecond durations and humanises reasons", () => {
    expect(formatMs(10)).toBe("10 ms");
    expect(formatMs(3097)).toBe("3.1 s");
    expect(formatMs(null)).toBeNull();
    expect(humanize("session closed: participant_disconnected")).toBe("Session closed: participant disconnected");
  });
});

describe("<TimelineView />", () => {
  const events = [
    ev("agent_state", 100, { state: "listening" }),
    ev("agent_state", 200, { state: "thinking" }),
    ev("tool_call_started", 2000, { call_id: "c1", tool: "lookup_policy", args_redacted: { policy_number: "H0-44721" } }),
    ev("tool_call_ended", 2010, { call_id: "c1", tool: "lookup_policy", status: "done", duration_ms: 10, result_preview: '{"found": true}' }),
    ev("error", 3000, { message: "LLM timeout" }),
    ev("metrics", 3100, { kind: "session_usage", data: { secret_counter: 42 } }),
  ];
  const session = detail({
    transcript: [
      { role: "user", text: "My basement flooded", ts: epochSeconds(1000) },
      { role: "assistant", text: "Sorry to hear that", ts: epochSeconds(2500), interrupted: true },
    ],
  });

  it("renders turns with speaker chips, the paired tool row and a soft error row", () => {
    const { container } = render(<TimelineView session={session} events={events} eventKinds={KINDS} />);
    expect(screen.getByText("You")).toBeTruthy();
    expect(screen.getByText("Claims desk")).toBeTruthy();
    expect(screen.getByText("Interrupted")).toBeTruthy();
    expect(screen.getByText("lookup_policy")).toBeTruthy();
    expect(screen.getByText("Done")).toBeTruthy();
    expect(screen.getByText("10 ms")).toBeTruthy();
    expect(screen.getByText("LLM timeout")).toBeTruthy();
    expect(container.querySelector(".bg-danger-soft")).toBeTruthy();
    // Wall-clock time lives in the row title; the row shows mm:ss.
    const firstTime = container.querySelector("li[data-row] time");
    expect(firstTime?.textContent).toBe("00:00");
    expect(firstTime?.getAttribute("title")).toBeTruthy();
  });

  it("never shows raw JSON outside a Details disclosure", () => {
    const { container } = render(<TimelineView session={session} events={events} eventKinds={KINDS} />);
    const pres = container.querySelectorAll("pre");
    expect(pres.length).toBeGreaterThan(0);
    for (const pre of pres) expect(pre.closest("details")).not.toBeNull();
    for (const details of container.querySelectorAll("details")) expect(details.open).toBe(false);
    // Payload values only appear inside the closed disclosures.
    const outside = Array.from(container.querySelectorAll("li[data-row]"))
      .map((li) => {
        const clone = li.cloneNode(true) as HTMLElement;
        clone.querySelectorAll("details > :not(summary)").forEach((node) => node.remove());
        return clone.textContent ?? "";
      })
      .join(" ");
    expect(outside).not.toContain("secret_counter");
    expect(outside).not.toContain("H0-44721");
  });

  it("toggles filter chips and offers a way back when nothing matches", () => {
    render(<TimelineView session={session} events={events} eventKinds={KINDS} />);
    const chips = within(screen.getByRole("group", { name: "Show in timeline" }));
    const turns = chips.getByRole("button", { name: /Turns/ });
    expect(turns.getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(turns);
    expect(turns.getAttribute("aria-pressed")).toBe("false");
    expect(screen.queryByText("My basement flooded")).toBeNull();

    for (const name of [/Tools/, /State/, /Errors/, /Other/]) fireEvent.click(chips.getByRole("button", { name }));
    expect(screen.getByText("No rows match these filters")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Show everything" }));
    expect(screen.getByText("My basement flooded")).toBeTruthy();
  });

  it("collapses agent state into a thin track that expands to each change", () => {
    render(<TimelineView session={session} events={events} eventKinds={KINDS} />);
    const track = screen.getByRole("button", { name: /Listening → Thinking/ });
    expect(track.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(track);
    expect(track.getAttribute("aria-expanded")).toBe("true");
    const list = screen.getByRole("list", { name: "State changes" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(2);
  });

  it("shows the minute index only when the call spans more than one minute", () => {
    const long = [...events, ev("user_turn", 75_000, { text: "still there?" })];
    const { rerender } = render(<TimelineView session={session} events={events} eventKinds={KINDS} />);
    expect(screen.queryByRole("navigation", { name: "Jump to minute" })).toBeNull();
    rerender(<TimelineView session={detail({ transcript: null })} events={long} eventKinds={KINDS} />);
    const nav = screen.getByRole("navigation", { name: "Jump to minute" });
    expect(within(nav).getByRole("link", { name: "01:00" }).getAttribute("href")).toBe("#timeline-minute-1");
    expect(document.getElementById("timeline-minute-1")).toBeTruthy();
  });

  it("renders an extension's event kind (V2-14 style) with its title and summary", () => {
    const kinds = resolveEventKinds(BUILTIN_EVENT_KINDS, [
      {
        id: "V2-14",
        eventKinds: [
          { type: "handoff", filter: "other", icon: ClockIcon, title: (p) => `Moved to ${String(p.to)}`, summary: (p) => `from ${String(p.from)}` },
        ],
      },
    ]);
    render(<TimelineView session={detail()} events={[ev("handoff", 100, { from: "intake", to: "billing" })]} eventKinds={kinds} />);
    expect(screen.getByText("Moved to billing")).toBeTruthy();
    expect(screen.getByText("from intake")).toBeTruthy();
  });

  it("shows an empty state when nothing was recorded", () => {
    render(<TimelineView session={detail()} events={[]} eventKinds={KINDS} />);
    expect(screen.getByText("Nothing was recorded for this call")).toBeTruthy();
  });
});

describe("session detail tab registry (WP-7 → V2-14 contract)", () => {
  const Stub = () => null;
  const tab = (id: string, order: number, extra: Partial<SessionTabDef> = {}): SessionTabDef => ({
    id,
    label: id,
    icon: ListTreeIcon,
    order,
    Component: Stub,
    ...extra,
  });

  it("ships timeline · transcript · recording/cost/qa (V2-14) · panel · raw", () => {
    expect(sessionTabs().map((t) => [t.id, t.order])).toEqual([
      ["timeline", 10],
      ["transcript", 20],
      ["recording", 30],
      ["cost", 40],
      ["qa", 50],
      ["panel", 60],
      ["raw", 90],
    ]);
    expect(DEFAULT_SESSION_TAB_ID).toBe("timeline");
  });

  it("adds, replaces and patches tabs from extensions, sorted by order", () => {
    const builtins = [tab("timeline", 10), tab("transcript", 20), tab("panel", 60), tab("raw", 90)];
    const Replacement = () => null;
    const extensions: SessionDetailExtension[] = [
      { id: "V2-14", tabs: [tab("qa", 50), tab("recording", 30), tab("cost", 40)] },
      { id: "V2-11", tabs: [tab("panel", 60, { Component: Replacement })], tabPatches: [{ id: "raw", patch: { label: "Raw" } }] },
      { id: "late", tabPatches: [{ id: "nope", patch: { label: "ignored" } }] },
    ];
    const resolved = resolveSessionTabs(builtins, extensions);
    expect(resolved.map((t) => t.id)).toEqual(["timeline", "transcript", "recording", "cost", "qa", "panel", "raw"]);
    expect(resolved.find((t) => t.id === "panel")?.Component).toBe(Replacement);
    expect(resolved.find((t) => t.id === "raw")?.label).toBe("Raw");
    // Pure: the inputs are untouched.
    expect(builtins.find((t) => t.id === "raw")?.label).toBe("raw");
  });

  it("hides tabs by predicate and falls back to the default for unknown or hidden ids", () => {
    const tabs = [tab("timeline", 10), tab("recording", 30, { visible: ({ session }) => session.recording?.status === "ready" })];
    const noRecording = visibleTabs(tabs, { session: detail() });
    expect(noRecording.map((t) => t.id)).toEqual(["timeline"]);
    expect(pickTab(noRecording, "recording", "timeline")?.id).toBe("timeline");
    expect(pickTab(noRecording, "bogus", "timeline")?.id).toBe("timeline");
    const withRecording = visibleTabs(tabs, { session: detail({ recording: { status: "ready" } }) });
    expect(pickTab(withRecording, "recording", "timeline")?.id).toBe("recording");
    expect(pickTab([], "x", "timeline")).toBeUndefined();
  });

  it("lets extensions add or replace event kinds but never the core types", () => {
    const kinds = resolveEventKinds(BUILTIN_EVENT_KINDS, [
      {
        id: "x",
        eventKinds: [
          { type: "agent_state", filter: "other", title: () => "hijack" },
          { type: "error", filter: "errors", tone: "danger", title: () => "Worker error" },
        ],
      },
    ]);
    for (const core of CORE_EVENT_TYPES) expect(kinds.has(core)).toBe(false);
    expect(kinds.get("error")?.title({})).toBe("Worker error");
    expect(kinds.get("metrics")?.collapse).toBe(true);
  });

  it("concatenates header actions in extension order", () => {
    const A = () => null;
    const B = () => null;
    expect(resolveSessionDetailSlots([{ id: "1", slots: { headerActions: [A] } }, { id: "2" }, { id: "3", slots: { headerActions: [B] } }]).headerActions).toEqual([A, B]);
  });
});

