import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { BUILTIN_EVENT_KINDS } from "@/components/console/sessions/detail/builtin-event-kinds";
import { resolveEventKinds } from "@/components/console/sessions/detail/registry";
import { SESSION_DETAIL_EXTENSIONS } from "@/components/console/sessions/detail/extensions";
import { EXTRACTION_RULES_EVENT_KINDS } from "@/components/console/sessions/timeline/extraction-rules-extension";
import { TimelineView } from "@/components/console/sessions/session-timeline";
import type { SessionDetailOut, SessionEventOut } from "@/contracts/lkap-contracts";

/**
 * V6-15 (`docs/v6/PLAN-V6.md` V6-15): the `extraction` and `rule_fired` timeline rows
 * V6-13's contracts added (`ExtractionEvent`, `RuleFiredEvent`). Both render through the
 * generic `EventRow` (title/summary, `session-timeline.test.tsx`'s existing "never shows
 * raw JSON outside a Details disclosure" rule already covers them) — this file pins the
 * plain-words summary and, above all, that a captured *value* never appears in the open
 * row, whatever the payload carries.
 */

const START_ISO = "2026-09-28T10:00:00.000Z";
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
    usage: null,
    error: null,
    transcript: null,
    final_ui_state: null,
    ...overrides,
  };
}

const KINDS = resolveEventKinds(BUILTIN_EVENT_KINDS, SESSION_DETAIL_EXTENSIONS);

describe("SESSION_DETAIL_EXTENSIONS registers extraction/rule_fired", () => {
  it("resolves both kinds through the shared, append-only extensions list", () => {
    expect(KINDS.has("extraction")).toBe(true);
    expect(KINDS.has("rule_fired")).toBe(true);
  });

  it("the module exports exactly the two kinds the plan names", () => {
    expect(EXTRACTION_RULES_EVENT_KINDS.map((k) => k.type).sort()).toEqual(["extraction", "rule_fired"]);
  });
});

describe("extraction row", () => {
  it("reads counts and field names, never the field's captured value", () => {
    const event = ev("extraction", 1000, {
      trigger: "turn",
      status: "ok",
      duration_ms: 340,
      fields: { policy_number: true, claim_type: false },
      changed: ["policy_number"],
      still_needed: ["claim_type"],
      values: { policy_number: "PX-99999" },
    });
    const { container } = render(<TimelineView session={detail()} events={[event]} eventKinds={KINDS} />);
    expect(screen.getByText(/Captured details/)).toBeTruthy();
    expect(screen.getByText(/1 of 2 set/)).toBeTruthy();
    expect(screen.getByText(/updated policy number/)).toBeTruthy();
    expect(screen.getByText(/still needs claim type/)).toBeTruthy();
    // The value is never in the open row — only inside the closed Details disclosure.
    const openText = container.querySelector("summary")?.textContent ?? "";
    expect(openText).not.toContain("PX-99999");
    expect(container.textContent).toContain("PX-99999"); // present, but only inside <details>
  });

  it("reads a failed or timed-out run as a plain notice, not a field count", () => {
    render(
      <TimelineView
        session={detail()}
        events={[ev("extraction", 1000, { trigger: "manual", status: "timeout", fields: {} })]}
        eventKinds={KINDS}
      />,
    );
    expect(screen.getByText("Took too long to finish.")).toBeTruthy();
  });

  it("carries no values on a run that recorded none (the redacted/basic tiers)", () => {
    const { container } = render(
      <TimelineView
        session={detail()}
        events={[ev("extraction", 1000, { trigger: "turn", status: "ok", fields: { policy_number: true } })]}
        eventKinds={KINDS}
      />,
    );
    expect(container.textContent).not.toMatch(/"values"/);
  });
});

describe("rule_fired row", () => {
  it("names the rule and its actions in plain words, never a value", () => {
    const event = ev("rule_fired", 2000, {
      rule_id: "urgent_hazard",
      label: "Flag an urgent hazard",
      actions: ["status.set", "escalate"],
      skipped: ["checklist.set_item"],
      trigger: "extraction",
    });
    render(<TimelineView session={detail()} events={[event]} eventKinds={KINDS} />);
    expect(screen.getByText("Flag an urgent hazard")).toBeTruthy();
    expect(screen.getByText(/changed the status, handed off to a person/)).toBeTruthy();
    expect(screen.getByText(/couldn't: updated the checklist/)).toBeTruthy();
  });

  it("falls back to 'A rule fired' when the event carries no label", () => {
    render(
      <TimelineView
        session={detail()}
        events={[ev("rule_fired", 2000, { rule_id: "r1", actions: ["instruct"] })]}
        eventKinds={KINDS}
      />,
    );
    expect(screen.getByText("A rule fired")).toBeTruthy();
  });
});
