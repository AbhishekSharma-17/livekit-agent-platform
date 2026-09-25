import * as React from "react";

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { ActivityBlock, ActivityRow } from "@/panels/generic/blocks";
import type { ActivityEvent } from "@/contracts/lkap-contracts";

/**
 * V4-13 (BACKGROUND-TOOLS.md §6, E2 in panels-and-capabilities.md): a
 * non-blocking tool's progress update carries `detail.message`, which
 * `ActivityRow` renders in a collapsible "Details" disclosure — hidden until
 * a viewer opens it, so the feed stays a one-line-per-event list even while a
 * background tool is chattering with fillers/announcements.
 */

function event(overrides: Partial<ActivityEvent> = {}): ActivityEvent {
  return {
    id: "evt-1",
    v: 1,
    source: "tool",
    phase: "running",
    headline: "Looking up policy H0-44721",
    label: "lookup_policy",
    ts: 1_000,
    ...overrides,
  };
}

describe("ActivityRow — detail.message (E2)", () => {
  it("renders detail.message for a running event, inside a collapsible disclosure", () => {
    render(<ActivityRow event={event({ detail: { message: "Still checking the underwriting system." } })} />);

    expect(screen.getByText("Looking up policy H0-44721")).toBeTruthy();
    const details = document.querySelector('[data-slot="panel-activity-detail"]');
    expect(details).toBeTruthy();
    expect(details?.tagName.toLowerCase()).toBe("details");
    expect(screen.getByText("Still checking the underwriting system.")).toBeTruthy();
  });

  it("shows nothing extra when there is no detail, or the message is blank", () => {
    const { rerender } = render(<ActivityRow event={event()} />);
    expect(document.querySelector('[data-slot="panel-activity-detail"]')).toBeNull();

    rerender(<ActivityRow event={event({ detail: { message: "   " } })} />);
    expect(document.querySelector('[data-slot="panel-activity-detail"]')).toBeNull();

    rerender(<ActivityRow event={event({ detail: { call_id: "c1" } })} />);
    expect(document.querySelector('[data-slot="panel-activity-detail"]')).toBeNull();
  });

  it("also shows the detail on a non-running (done) row", () => {
    render(
      <ActivityRow
        event={event({ phase: "done", duration_ms: 420, detail: { message: "Found policy H0-44721." } })}
      />,
    );
    expect(screen.getByText("Found policy H0-44721.")).toBeTruthy();
  });
});

describe("ActivityBlock", () => {
  it("renders one row per event, newest first, each carrying its own detail", () => {
    render(
      <ActivityBlock
        events={[
          event({ id: "evt-1", ts: 1_000, headline: "Started", detail: { message: "First update." } }),
          event({ id: "evt-2", ts: 2_000, headline: "Still going", detail: { message: "Second update." } }),
        ]}
      />,
    );
    const rows = screen.getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    // Newest (ts 2000) first.
    expect(rows[0].textContent).toContain("Still going");
    expect(rows[1].textContent).toContain("Started");
  });
});
