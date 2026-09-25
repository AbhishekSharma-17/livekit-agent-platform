import * as React from "react";

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { BUILTIN_EVENT_KINDS } from "@/components/console/sessions/detail/builtin-event-kinds";
import { resolveEventKinds } from "@/components/console/sessions/detail/registry";
import { TimelineView } from "@/components/console/sessions/session-timeline";
import { collectEntries } from "@/components/console/sessions/timeline-model";
import type { SessionDetailOut, SessionEventOut } from "@/contracts/lkap-contracts";

/**
 * The two rows V4-12's background-tool executor adds to the session
 * timeline (BACKGROUND-TOOLS.md D-V4-38): `tool_call_updated` (an announce or
 * a later progress message) and `tool_reply` (the deferred reply the SDK
 * schedules once the tool is done, `status=skipped` reading "already
 * covered"). Registered as ordinary `builtin-event-kinds.ts` entries, so they
 * render through the existing generic "event" row — no change to
 * `session-timeline.tsx`'s pairing logic.
 */

const START_ISO = "2026-09-25T10:00:00.000Z";
const START = Date.parse(START_ISO);
const iso = (offsetMs: number) => new Date(START + offsetMs).toISOString();

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
    ended_at: iso(20_000),
    usage: null,
    error: null,
    transcript: null,
    final_ui_state: null,
    ...overrides,
  };
}

const KINDS = resolveEventKinds(BUILTIN_EVENT_KINDS);

describe("BUILTIN_EVENT_KINDS — tool_call_updated / tool_reply", () => {
  it("titles a tool_call_updated row with the tool name and its message as the summary", () => {
    const kind = KINDS.get("tool_call_updated");
    expect(kind).toBeDefined();
    expect(kind?.filter).toBe("tools");
    expect(kind?.title({ call_id: "c1", tool: "lookup_policy", message_preview: "Looking that up." })).toBe(
      "Update from lookup_policy",
    );
    expect(kind?.summary?.({ call_id: "c1", tool: "lookup_policy", message_preview: "Looking that up." })).toBe(
      "Looking that up.",
    );
  });

  it('reads a skipped tool_reply as "Already covered"', () => {
    const kind = KINDS.get("tool_reply");
    expect(kind).toBeDefined();
    expect(kind?.title({ call_ids: ["c1"], status: "skipped" })).toBe("Already covered");
  });

  it("otherwise humanizes the reply status", () => {
    const kind = KINDS.get("tool_reply");
    expect(kind?.title({ call_ids: ["c1"], status: "scheduled" })).toBe("Reply Scheduled");
    expect(kind?.title({ call_ids: ["c1", "c2"], status: "completed" })).toBe("Reply Completed");
    expect(kind?.summary?.({ call_ids: ["c1", "c2"], status: "completed" })).toBe("2 tool calls");
  });
});

describe("collectEntries — tool_call_updated / tool_reply ordering (ask #89)", () => {
  it("orders rows by ts even when tool_reply is posted before the matching tool_call_ended", () => {
    // The SDK queues the reply from inside the tool task and reports `ended`
    // from a done-callback, so `tool_reply` can carry an EARLIER ts than the
    // `tool_call_ended` for the same call — the timeline must not assume the
    // opposite order.
    const events: SessionEventOut[] = [
      ev("tool_call_started", 1000, { call_id: "c1", tool: "lookup_policy" }),
      ev("tool_call_updated", 1200, { call_id: "c1", tool: "lookup_policy", message_preview: "Looking that up." }),
      ev("tool_reply", 1400, { call_ids: ["c1"], status: "scheduled" }),
      ev("tool_call_ended", 1500, { call_id: "c1", tool: "lookup_policy", status: "done", duration_ms: 500 }),
    ];
    const entries = collectEntries({ transcript: null }, events, KINDS);
    expect(entries.map((e) => (e.kind === "event" ? e.type : e.kind))).toEqual([
      "tool", // tool_call_started/ended paired
      "tool_call_updated",
      "tool_reply",
    ]);
    expect(entries.map((e) => e.at)).toEqual([START + 1000, START + 1200, START + 1400]);
  });
});

describe("SessionTimeline — rendering the two rows", () => {
  it("shows an Update row and a Reply row, the latter reading 'Already covered' when skipped", () => {
    const events: SessionEventOut[] = [
      ev("tool_call_started", 1000, { call_id: "c1", tool: "lookup_policy" }),
      ev("tool_call_updated", 1200, { call_id: "c1", tool: "lookup_policy", message_preview: "Looking that up." }),
      ev("tool_call_ended", 1500, { call_id: "c1", tool: "lookup_policy", status: "done", duration_ms: 500 }),
      ev("tool_reply", 1600, { call_ids: ["c1"], status: "skipped" }),
    ];
    render(<TimelineView session={detail()} events={events} eventKinds={KINDS} />);

    expect(screen.getByText("Update from lookup_policy")).toBeTruthy();
    expect(screen.getByText("Looking that up.")).toBeTruthy();
    expect(screen.getByText("Already covered")).toBeTruthy();
  });
});
