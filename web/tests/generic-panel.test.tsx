import * as React from "react";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AgentPublicOut, UiState } from "@/contracts/lkap-contracts";
import { GenericPanel } from "@/panels/generic";
import type { PanelProps } from "@/panels/registry";

import genericFixture from "./fixtures/generic_ui_state.json";

afterEach(cleanup);

const AGENT: AgentPublicOut = {
  id: "agent-1",
  slug: "insurance-claim",
  name: "Claims assistant",
  description: "Takes first notice of loss.",
  ui_panel_id: "generic",
  pipeline_mode: "cascaded",
  capabilities: {
    camera: true,
    screen_share: false,
    chat_input: true,
    vision_inject_per_turn: true,
  },
};

const FIXTURE: UiState = {
  v: 1,
  status: { label: "In review", tone: "warning", key: "review" },
  progress: 60,
  notes: [
    {
      id: "n1",
      key: "policy",
      kind: "policy",
      text: "Policy H0-44721 is active.",
      tone: "success",
      ts: 1_700_000_000,
    },
    {
      id: "n2",
      kind: "note",
      text: "Basement flooded yesterday in Denver.",
      tone: "neutral",
      ts: 1_700_000_060,
    },
  ],
  checklist: [
    { id: "c1", label: "Date of loss", done: true, blocking: false },
    {
      id: "c2",
      label: "Photos of the damage",
      done: false,
      blocking: true,
      hint: "Point your camera at the basement",
    },
  ],
  assets: [
    {
      asset_id: "asset-1",
      kind: "photo",
      mime: "image/jpeg",
      caption: "Water line on the wall",
      meta: {},
      ts: 1_700_000_120,
    },
    {
      asset_id: "asset-2",
      kind: "sketch",
      mime: "image/png",
      caption: "Incident sketch",
      meta: {},
      ts: 1_700_000_180,
    },
  ],
  activity: [
    {
      v: 1,
      id: "call-1",
      ts: 1_700_000_030,
      source: "lookup_policy",
      label: "Policy desk",
      phase: "done",
      headline: "Looked up policy H0-44721",
      urgent: false,
      duration_ms: 412,
    },
    {
      v: 1,
      id: "call-2",
      ts: 1_700_000_090,
      source: "sync_claim_packet",
      label: "Claim team",
      phase: "running",
      headline: "Building the adjuster packet",
      urgent: true,
    },
  ],
  custom: { routing: "standard_claim", policy: { number: "H0-44721" } },
};

function props(overrides: Partial<PanelProps> = {}): PanelProps {
  return {
    state: FIXTURE,
    // asset-1 has bytes; asset-2 has not arrived yet.
    assets: new Map([["asset-1", "blob:mock/asset-1"]]),
    agent: AGENT,
    sessionId: "sess-1",
    perform: vi.fn(async () => ({ ok: true })),
    transcript: [],
    connectionState: "connected",
    ...overrides,
  };
}

/** The golden envelope fixture the preview route and WP-10 also render. */
const GOLDEN = genericFixture as unknown as UiState;

describe("GenericPanel", () => {
  it("renders every envelope slot from a fixture UiState", () => {
    render(<GenericPanel {...props()} />);

    expect(screen.getByText("In review")).toBeTruthy();
    expect(screen.getByText("60%")).toBeTruthy();
    expect(screen.getByText("Policy H0-44721 is active.")).toBeTruthy();
    expect(screen.getByText("Photos of the damage")).toBeTruthy();
    expect(screen.getByText("Point your camera at the basement")).toBeTruthy();
    expect(screen.getByText("Looked up policy H0-44721")).toBeTruthy();
    expect(screen.getByText("Building the adjuster packet")).toBeTruthy();
  });

  it("exposes progress to assistive tech", () => {
    render(<GenericPanel {...props()} />);
    const bar = screen.getByRole("progressbar");
    expect(bar.getAttribute("aria-valuenow")).toBe("60");
  });

  it("renders received assets as images and pending ones as placeholders", () => {
    const { container } = render(<GenericPanel {...props()} />);
    const images = container.querySelectorAll("img");
    expect(images).toHaveLength(1);
    expect(images[0]?.getAttribute("src")).toBe("blob:mock/asset-1");
    expect(images[0]?.getAttribute("alt")).toBe("Water line on the wall");
    // The second asset is referenced by state but its bytes have not arrived.
    expect(screen.getByText("Receiving…")).toBeTruthy();
    expect(screen.getByText("Incident sketch")).toBeTruthy();
  });

  it("shows the newest activity first", () => {
    const { container } = render(<GenericPanel {...props()} />);
    const headlines = Array.from(
      container.querySelectorAll("li p"),
    ).map((node) => node.textContent ?? "");
    const packetIndex = headlines.findIndex((text) =>
      text.includes("Building the adjuster packet"),
    );
    const lookupIndex = headlines.findIndex((text) =>
      text.includes("Looked up policy"),
    );
    expect(packetIndex).toBeGreaterThan(-1);
    expect(packetIndex).toBeLessThan(lookupIndex);
  });

  it("collapses pack-defined custom state into a raw JSON view", () => {
    render(<GenericPanel {...props()} />);
    expect(screen.getByText("Show raw state")).toBeTruthy();
    expect(screen.getByText(/standard_claim/)).toBeTruthy();
  });

  it("renders empty states for a blank envelope", () => {
    render(
      <GenericPanel
        {...props({ state: { v: 1 }, assets: new Map<string, string>() })}
      />,
    );
    expect(screen.getByText("Not started")).toBeTruthy();
    expect(screen.getByText("Nothing noted yet.")).toBeTruthy();
    expect(screen.getByText("No open items.")).toBeTruthy();
    expect(screen.getByText("No images or files yet.")).toBeTruthy();
    expect(screen.getByText("The agent has not run any tools yet.")).toBeTruthy();
    expect(screen.queryByText("Show raw state")).toBeNull();
    expect(screen.queryByRole("progressbar")).toBeNull();
  });
});

describe("the golden generic fixture", () => {
  it("renders every block of tests/fixtures/generic_ui_state.json", () => {
    const { container } = render(
      <GenericPanel
        {...props({
          state: GOLDEN,
          assets: new Map([["asset-leak-photo", "blob:mock/leak"]]),
        })}
      />,
    );

    expect(screen.getByText("In progress")).toBeTruthy();
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("45");
    expect(container.querySelectorAll('[data-slot="panel-note"]')).toHaveLength(3);
    expect(container.querySelectorAll('[data-slot="panel-checklist-item"]')).toHaveLength(4);
    expect(container.querySelectorAll('[data-slot="panel-asset"]')).toHaveLength(2);
    expect(container.querySelectorAll('[data-slot="panel-activity-item"]')).toHaveLength(5);
    // One blocking item, marked with a chip rather than a red asterisk.
    expect(screen.getByText("Required")).toBeTruthy();
    // The pdf has no bytes in this render, so it stays a placeholder.
    expect(container.querySelectorAll("img")).toHaveLength(1);
    expect(screen.getByText("Receiving…")).toBeTruthy();
    expect(screen.getByText(/SRV-40218/)).toBeTruthy();
  });

  it("marks a running activity row with the state meter and the rest with dots", () => {
    const { container } = render(<GenericPanel {...props({ state: GOLDEN })} />);
    const rows = Array.from(
      container.querySelectorAll('[data-slot="panel-activity-item"]'),
    );
    // Newest first: the running "Holding Thursday morning" row leads.
    expect(rows[0]?.getAttribute("data-phase")).toBe("running");
    expect(rows[0]?.querySelector('[data-slot="state-meter"]')).toBeTruthy();
    expect(
      container.querySelectorAll('[data-slot="panel-activity-item"] [data-slot="state-meter"]'),
    ).toHaveLength(1);
    expect(screen.getByText("Urgent")).toBeTruthy();
  });

  it("uses tokens, not palette classes, for every block", () => {
    const { container } = render(<GenericPanel {...props({ state: GOLDEN })} />);
    expect(container.innerHTML).not.toMatch(
      /\b(?:bg|text|border)-(?:emerald|sky|amber|red|blue)-\d/,
    );
  });
});
