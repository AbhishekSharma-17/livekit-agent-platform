import * as React from "react";

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  ActivityEvent,
  AgentPublicOut,
  AssetRef,
  UiState,
} from "@/contracts/lkap-contracts";
import type { PanelProps } from "@/panels/registry";
import { PANELS, resolvePanel } from "@/panels/registry";
import { NOTEBOOK_CSS } from "@/panels/insurance_notebook/notebook-styles";
import {
  INSURANCE_NOTEBOOK_PANEL,
  InsuranceNotebookPanel,
  handleNotebookRequest,
} from "@/panels/insurance_notebook";

import autoFixture from "./fixtures/insurance_ui_state_lkap_auto.json";
import blankFixture from "./fixtures/insurance_ui_state_lkap_blank.json";
import floodFixture from "./fixtures/insurance_ui_state_lkap_flood.json";

afterEach(cleanup);

/** The golden `UiState` fixtures shipped by W1-PACK-INSURANCE-CORE. */
const BLANK = blankFixture as unknown as UiState;
const FLOOD = floodFixture as unknown as UiState;
const AUTO = autoFixture as unknown as UiState;

const AGENT: AgentPublicOut = {
  id: "agent-1",
  slug: "insurance-claim",
  name: "Claims assistant",
  description: "Takes first notice of loss.",
  ui_panel_id: "insurance_notebook",
  panel: { panel_id: "insurance_notebook", layout: "wide", blocks: [] },
  pipeline_mode: "cascaded",
  capabilities: {
    camera: true,
    screen_share: false,
    chat_input: true,
    vision_inject_per_turn: true,
  },
};

function props(overrides: Partial<PanelProps> = {}): PanelProps {
  return {
    state: FLOOD,
    assets: new Map<string, string>(),
    agent: AGENT,
    sessionId: "sess-1",
    perform: vi.fn(async () => ({ ok: true })),
    transcript: [],
    connectionState: "connected",
    ...overrides,
  };
}

function evidence(overrides: Partial<AssetRef> = {}): AssetRef {
  return {
    asset_id: "asset-evidence",
    kind: "evidence",
    mime: "image/jpeg",
    caption: "Water line halfway up the drywall",
    ts: 1_777_000_100,
    meta: {
      confirmed: "true",
      claimant_description: "the sump pump corner",
      evidence_type: "damage",
      captured_at: "14:02",
    },
    ...overrides,
  };
}

function sketch(overrides: Partial<AssetRef> = {}): AssetRef {
  return {
    asset_id: "asset-sketch",
    kind: "sketch",
    mime: "image/png",
    caption: "Does this look right?",
    ts: 1_777_000_200,
    meta: { version: "2", brief: "flooded basement, sump pump corner" },
    ...overrides,
  };
}

function running(source: string): ActivityEvent {
  return {
    v: 1,
    id: `call-${source}`,
    ts: 1_777_000_050,
    source,
    label: source === "lookup_policy" ? "Policy desk" : "Claim writer",
    phase: "running",
    headline: "Working on it",
    urgent: false,
  };
}

function withCustom(state: UiState, patch: Record<string, unknown>): UiState {
  return { ...state, custom: { ...(state.custom ?? {}), ...patch } };
}

describe("insurance notebook registration", () => {
  it("is registered under its pack ui_panel_id as a wide panel", () => {
    expect(PANELS["insurance_notebook"]).toBe(INSURANCE_NOTEBOOK_PANEL);
    expect(INSURANCE_NOTEBOOK_PANEL.layout).toBe("wide");
    expect(resolvePanel("insurance_notebook").id).toBe("insurance_notebook");
    expect(INSURANCE_NOTEBOOK_PANEL.Component).toBe(InsuranceNotebookPanel);
  });
});

describe("golden UiState fixtures", () => {
  it("renders the blank intake page", () => {
    const { container } = render(
      <InsuranceNotebookPanel {...props({ state: BLANK })} />,
    );

    expect(
      screen.getByText(
        "Waiting for the claimant. Tap Talk, show the camera, or type below.",
      ),
    ).toBeTruthy();
    expect(screen.getByTestId("notebook-stamp").textContent).toBe("Needs docs");
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe(
      "12",
    );
    // Nothing pinned, nothing running.
    expect(container.querySelector('[data-testid="notebook-board"]')).toBeNull();
    expect(container.querySelector('[data-testid="notebook-pen"]')).toBeNull();
    expect(
      container.querySelector('[data-testid="notebook-notes"]')?.innerHTML,
    ).toMatchSnapshot();
  });

  it("renders the flood claim page", () => {
    const { container } = render(
      <InsuranceNotebookPanel {...props({ state: FLOOD })} />,
    );

    expect(screen.getByText("Maya Singh · H0-44721")).toBeTruthy();
    expect(
      screen.getByText("Homeowners (HO-3), active. Dwelling $620,000"),
    ).toBeTruthy();
    expect(screen.getByText("Best contact?")).toBeTruthy();
    expect(screen.getByTestId("notebook-stamp").textContent).toBe("Needs docs");
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe(
      "63",
    );
    expect(
      container.querySelector('[data-testid="notebook-notes"]')?.innerHTML,
    ).toMatchSnapshot();
  });

  it("renders the injured auto claim page with its escalation stamp", () => {
    const { container } = render(
      <InsuranceNotebookPanel {...props({ state: AUTO })} />,
    );

    const stamp = screen.getByTestId("notebook-stamp");
    expect(stamp.textContent).toBe("Escalate to human");
    expect(stamp.getAttribute("data-tone")).toBe("danger");
    expect(stamp.getAttribute("data-route")).toBe("emergency_escalation");
    expect(
      container.querySelector('[data-testid="notebook-notes"]')?.innerHTML,
    ).toMatchSnapshot();
  });
});

describe("the handwriting", () => {
  it("draws each note kind, marking danger notes urgent", () => {
    const { container } = render(
      <InsuranceNotebookPanel {...props({ state: AUTO })} />,
    );

    const lapsed = container.querySelector(
      '[data-key="policy:AUTO-11111:lapsed"]',
    );
    expect(lapsed?.className).toContain("flag");
    expect(lapsed?.className).toContain("urgent");

    const title = container.querySelector(
      '[data-key="title:Chris Park|AUTO-11111"]',
    );
    expect(title?.className).toContain("title");

    // A `blank` note gets the red rule the claimant's answer goes on.
    const blank = container.querySelector('[data-key="blank:date_of_loss"]');
    expect(blank?.className).toContain("blank");
    expect(blank?.querySelector(".blank-line")).toBeTruthy();
  });

  it("falls back to a plain line for an unknown note kind", () => {
    const state: UiState = {
      ...BLANK,
      notes: [
        {
          id: "n1",
          key: "k1",
          kind: "sparkle",
          text: "Something new",
          tone: "neutral",
          ts: 1,
        },
      ],
    };
    const { container } = render(
      <InsuranceNotebookPanel {...props({ state })} />,
    );
    const note = container.querySelector('[data-key="k1"]');
    expect(note?.className).toContain("note");
    expect(note?.className).not.toContain("sparkle");
  });

  it("inks in only the note keys it has not drawn before", () => {
    const first: UiState = {
      ...BLANK,
      notes: [
        { id: "n1", key: "a", kind: "note", text: "First line", ts: 1 },
      ],
    };
    const second: UiState = {
      ...BLANK,
      notes: [
        { id: "n1", key: "a", kind: "note", text: "First line", ts: 1 },
        { id: "n2", key: "b", kind: "note", text: "Second line", ts: 2 },
      ],
    };

    const { container, rerender } = render(
      <InsuranceNotebookPanel {...props({ state: first })} />,
    );
    expect(container.querySelector('[data-key="a"]')?.className).toContain(
      "ink-in",
    );

    rerender(<InsuranceNotebookPanel {...props({ state: second })} />);
    expect(container.querySelector('[data-key="a"]')?.className).not.toContain(
      "ink-in",
    );
    expect(container.querySelector('[data-key="b"]')?.className).toContain(
      "ink-in",
    );
  });
});

describe("the pinboard", () => {
  it("pins evidence and the sketch, and shows a placeholder until bytes arrive", () => {
    const state: UiState = { ...FLOOD, assets: [evidence(), sketch()] };
    const { container } = render(
      <InsuranceNotebookPanel
        {...props({
          state,
          // Only the photo's bytes have been delivered so far.
          assets: new Map([["asset-evidence", "blob:mock/evidence"]]),
        })}
      />,
    );

    const images = container.querySelectorAll('[data-testid="notebook-board"] img');
    expect(images).toHaveLength(1);
    expect(images[0]?.getAttribute("src")).toBe("blob:mock/evidence");
    expect(screen.getByText("Water line halfway up the drywall")).toBeTruthy();
    expect(screen.getByText(/the sump pump corner/)).toBeTruthy();
    expect(screen.getByText(/Seen on camera 14:02 · damage/)).toBeTruthy();

    // The sketch is referenced but its bytes have not arrived.
    expect(screen.getByText("sketching…")).toBeTruthy();
    expect(screen.getByText("Does this look right?")).toBeTruthy();
    expect(screen.getByText(/Sketch v2/)).toBeTruthy();
  });

  it("marks an unconfirmed camera note in red and keeps assets it does not know off the board", () => {
    const state: UiState = {
      ...FLOOD,
      assets: [
        evidence({ meta: { ...evidence().meta, confirmed: "false" } }),
        {
          asset_id: "asset-audio",
          kind: "recording",
          mime: "audio/webm",
          caption: "ignored",
          meta: {},
          ts: 1,
        },
      ],
    };
    const { container } = render(
      <InsuranceNotebookPanel {...props({ state })} />,
    );

    const tag = screen.getByText(/not confirmed on camera/);
    expect(tag.className).toContain("unconfirmed");
    expect(
      container.querySelectorAll('[data-testid="notebook-board"] figure'),
    ).toHaveLength(1);
  });
});

describe("the stamp and the pen", () => {
  it("re-stamps when the route id changes", () => {
    const { container, rerender } = render(
      <InsuranceNotebookPanel {...props({ state: FLOOD })} />,
    );
    const before = container.querySelector('[data-testid="notebook-stamp"]');
    expect(before?.getAttribute("data-route")).toBe("needs_docs");

    rerender(<InsuranceNotebookPanel {...props({ state: AUTO })} />);
    const after = container.querySelector('[data-testid="notebook-stamp"]');
    expect(after?.getAttribute("data-route")).toBe("emergency_escalation");
    // A fresh node replays the stamp animation, like the demo's reflow reset.
    expect(after).not.toBe(before);
  });

  it("hides the stamp when the pack has not set a status", () => {
    const { container } = render(
      <InsuranceNotebookPanel {...props({ state: { ...FLOOD, status: null } })} />,
    );
    expect(container.querySelector('[data-testid="notebook-stamp"]')).toBeNull();
  });

  it("scribbles while a background tool runs, but not for the policy desk", () => {
    const writing: UiState = { ...FLOOD, activity: [running("sync_claim_packet")] };
    const { container, rerender } = render(
      <InsuranceNotebookPanel {...props({ state: writing })} />,
    );
    expect(container.querySelector('[data-testid="notebook-pen"]')).toBeTruthy();

    rerender(
      <InsuranceNotebookPanel
        {...props({ state: { ...FLOOD, activity: [running("lookup_policy")] } })}
      />,
    );
    expect(container.querySelector('[data-testid="notebook-pen"]')).toBeNull();
  });
});

describe("the desk", () => {
  it("lists blockers before documents and answered items last", () => {
    const { container } = render(
      <InsuranceNotebookPanel {...props({ state: FLOOD })} />,
    );
    const rows = Array.from(
      container.querySelectorAll('[data-testid="notebook-still-needed"] li'),
    );
    const labels = rows.map((row) => row.textContent ?? "");

    expect(labels[0]).toContain("Best contact");
    const firstDoc = labels.findIndex((label) =>
      label.includes("Mitigation or drying invoice"),
    );
    const lastBlocker = labels.findIndex((label) =>
      label.includes("Whether a plumber or mitigation company"),
    );
    expect(lastBlocker).toBeLessThan(firstDoc);
    // The already-provided document sinks to the bottom.
    expect(
      labels[labels.length - 1].includes(
        "Photos or video of damaged areas before cleanup",
      ),
    ).toBe(true);
    expect(rows[rows.length - 1]?.getAttribute("data-done")).toBe("true");
  });

  it("recovers the pack's document priority for the checklist badge", () => {
    // The envelope's ChecklistItem carries no priority; `custom.documents` does.
    const state = withCustom(
      {
        ...FLOOD,
        checklist: [
          { id: "doc:witness_statement", label: "Witness statement", done: false },
          { id: "doc:repair_estimate", label: "Repair estimate", done: false },
        ],
      },
      {
        documents: [
          {
            item: "Witness statement",
            reason: "Helps liability review.",
            priority: "recommended",
            already_provided: false,
          },
          {
            item: "Repair estimate",
            reason: "Supports the claimed amount.",
            priority: "required",
            already_provided: false,
          },
        ],
      },
    );
    render(<InsuranceNotebookPanel {...props({ state })} />);
    expect(screen.getByText("recommended")).toBeTruthy();
    expect(screen.getByText("required")).toBeTruthy();
  });

  it("shows the claim team feed newest first with the urgent marker", () => {
    const activity: ActivityEvent[] = [
      {
        v: 1,
        id: "call-1",
        ts: 1_777_000_010,
        source: "lookup_policy",
        label: "Policy desk",
        phase: "done",
        headline: "Checked AUTO-11111",
        urgent: false,
        duration_ms: 8,
      },
      {
        v: 1,
        id: "call-2",
        ts: 1_777_000_090,
        source: "sync_claim_packet",
        label: "Claim writer",
        phase: "done",
        headline: "Packet updated",
        urgent: true,
      },
    ];
    const { container } = render(
      <InsuranceNotebookPanel {...props({ state: { ...FLOOD, activity } })} />,
    );

    const rows = Array.from(
      container.querySelectorAll('[data-testid="notebook-team-feed"] li'),
    ).map((row) => row.textContent ?? "");
    expect(rows[0]).toContain("Packet updated");
    expect(rows[0]).toContain("interrupted the agent");
    expect(rows[1]).toContain("8 ms");
  });
});

describe("the adjuster packet", () => {
  it("stays disabled until the workflow has written one", () => {
    const state = withCustom(FLOOD, { packet_markdown: "" });
    render(<InsuranceNotebookPanel {...props({ state })} />);
    const trigger = screen.getByTestId("notebook-packet-trigger");
    expect(trigger.hasAttribute("disabled")).toBe(true);
    expect(trigger.textContent).toContain("not written yet");
  });

  it("opens a labelled dialog with the handoff summary and the markdown", async () => {
    render(<InsuranceNotebookPanel {...props({ state: AUTO })} />);
    fireEvent.click(screen.getByTestId("notebook-packet-trigger"));

    const dialog = await screen.findByRole("dialog");
    expect(dialog.textContent).toContain("Adjuster packet");
    // The accessible name comes from the DialogTitle, not a bare div.
    expect(screen.getByRole("heading", { name: "Adjuster packet" })).toBeTruthy();
    expect(screen.getByText("Priority")).toBeTruthy();
    expect(screen.getByText(/Passenger injury reported/)).toBeTruthy();
    expect(screen.getByTestId("notebook-packet-body").textContent).toContain(
      "Insurance Claim Intake Packet",
    );
  });
});

describe("the sketch confirmation", () => {
  const SKETCH = { asset_id: "asset-sketch", version: 2, brief: "flooded basement", confirmed: false };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("is absent until the pack has drawn a sketch", () => {
    render(<InsuranceNotebookPanel {...props({ state: FLOOD })} />);
    expect(screen.queryByTestId("notebook-confirm-sketch")).toBeNull();
  });

  it("sends confirm_sketch through perform and never touches anything else", async () => {
    const perform = vi.fn(async () => ({ ok: true }));
    const state = withCustom(FLOOD, { sketch: SKETCH });
    render(<InsuranceNotebookPanel {...props({ state, perform })} />);

    fireEvent.click(screen.getByTestId("notebook-confirm-sketch"));

    await waitFor(() => expect(perform).toHaveBeenCalledTimes(1));
    expect(perform).toHaveBeenCalledWith({
      action: "ui_action",
      payload: {
        name: "confirm_sketch",
        data: { asset_id: "asset-sketch", version: 2 },
      },
    });
  });

  it("reports a failed confirmation instead of throwing", async () => {
    const perform = vi.fn(async () => {
      throw new Error("rpc failed");
    });
    const state = withCustom(FLOOD, { sketch: SKETCH });
    render(<InsuranceNotebookPanel {...props({ state, perform })} />);

    fireEvent.click(screen.getByTestId("notebook-confirm-sketch"));
    const status = await screen.findByRole("status");
    expect(status.textContent).toContain("Could not send that");
  });

  it("acknowledges a confirmed sketch instead of asking again", () => {
    const state = withCustom(FLOOD, {
      sketch: { ...SKETCH, confirmed: true },
    });
    render(<InsuranceNotebookPanel {...props({ state })} />);
    expect(screen.queryByTestId("notebook-confirm-sketch")).toBeNull();
    expect(screen.getByTestId("notebook-sketch-confirmed").textContent).toContain(
      "confirmed",
    );
  });
});

describe("degraded state", () => {
  it("renders an empty envelope without throwing", () => {
    const { container } = render(
      <InsuranceNotebookPanel {...props({ state: { v: 1 } })} />,
    );
    expect(container.querySelector('[data-testid="insurance-notebook"]')).toBeTruthy();
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("0");
    expect(screen.getByText("Nothing outstanding yet.")).toBeTruthy();
    expect(screen.getByText("The team is waiting for the first detail.")).toBeTruthy();
  });

  it("keeps the page up when custom state is malformed", () => {
    const state: UiState = {
      ...FLOOD,
      custom: { fields: "not-an-object", documents: 42, sketch: "nope" },
    };
    render(<InsuranceNotebookPanel {...props({ state })} />);
    expect(screen.getByText("Maya Singh · H0-44721")).toBeTruthy();
    expect(screen.queryByTestId("notebook-confirm-sketch")).toBeNull();
  });
});

describe("the paper's stylesheet", () => {
  it("loads no fonts at runtime and reads the session's font variables", () => {
    expect(NOTEBOOK_CSS).not.toContain("@import");
    expect(NOTEBOOK_CSS).not.toContain("fonts.googleapis.com");
    expect(NOTEBOOK_CSS).toContain('--hand: var(--font-hand, "Caveat")');
    expect(NOTEBOOK_CSS).toContain('--hand-label: var(--font-hand-label, "Patrick Hand")');
  });

  it("marks a field's status with a dot, never a side stripe", () => {
    expect(NOTEBOOK_CSS).not.toContain("border-left");
    expect(NOTEBOOK_CSS).toContain(".lkap-notebook .field .f-label::before");
    expect(NOTEBOOK_CSS).toContain(
      '.lkap-notebook .field[data-status="urgent"] .f-label::before',
    );
  });

  it("keeps the paper's character: tape, stamp, pen and the ink-in animation", () => {
    expect(NOTEBOOK_CSS).toContain("mix-blend-mode: multiply");
    expect(NOTEBOOK_CSS).toContain("@keyframes lkapInkIn");
    expect(NOTEBOOK_CSS).toContain("@keyframes lkapPinIn");
    expect(NOTEBOOK_CSS).toContain("@keyframes lkapStampIn");
    expect(NOTEBOOK_CSS).toContain("@keyframes lkapScribble");
    expect(NOTEBOOK_CSS).toContain("--tape:");
    expect(NOTEBOOK_CSS).toContain("prefers-reduced-motion");
  });

  it("paints the % ready ring's inner disc with the card token", () => {
    expect(NOTEBOOK_CSS).toContain("background: var(--card, var(--color-card));");
    expect(NOTEBOOK_CSS).not.toContain("#1e2126");
  });
});

describe("the desk on tokens", () => {
  it("renders the stamp and no palette classes", () => {
    const { container } = render(
      <InsuranceNotebookPanel {...props({ state: FLOOD })} />,
    );
    expect(screen.getByTestId("notebook-stamp")).toBeTruthy();
    expect(container.innerHTML).not.toMatch(
      /\b(?:bg|text|border)-(?:emerald|sky|amber|red|blue)-\d/,
    );
  });

  it("shows the state meter on a running claim-team row", () => {
    const { container } = render(
      <InsuranceNotebookPanel
        {...props({ state: { ...FLOOD, activity: [running("sync_claim_packet")] } })}
      />,
    );
    const row = container.querySelector(
      '[data-testid="notebook-team-feed"] li[data-phase="running"]',
    );
    expect(row?.querySelector('[data-slot="state-meter"]')).toBeTruthy();
  });
});

describe("the agent's open_dialog request (CONTRACTS-V2 §4.4, R-V2-3b)", () => {
  it("is registered on the panel definition", () => {
    expect(INSURANCE_NOTEBOOK_PANEL.handleRequest).toBe(handleNotebookRequest);
  });

  it('opens the adjuster packet for {dialog: "packet"}', async () => {
    render(<InsuranceNotebookPanel {...props({ state: AUTO })} />);
    expect(screen.queryByRole("dialog")).toBeNull();

    let result: ReturnType<typeof handleNotebookRequest> | undefined;
    act(() => {
      result = handleNotebookRequest({
        method: "open_dialog",
        payload: { dialog: "packet" },
      });
    });

    expect(result).toEqual({ ok: true, payload: { dialog: "packet" } });
    const dialog = await screen.findByRole("dialog");
    expect(dialog.textContent).toContain("Adjuster packet");
    expect(screen.getByTestId("notebook-packet-body").textContent).toContain(
      "Insurance Claim Intake Packet",
    );
  });

  it("ignores params it does not need", async () => {
    render(<InsuranceNotebookPanel {...props({ state: AUTO })} />);
    act(() => {
      handleNotebookRequest({
        method: "open_dialog",
        payload: { dialog: "packet", params: { page: 2 } },
      });
    });
    expect(await screen.findByRole("dialog")).toBeTruthy();
  });

  it("declines the old `id` key, an unknown dialog and a method it does not own", () => {
    render(<InsuranceNotebookPanel {...props({ state: AUTO })} />);

    // R-V2-3b: contracts win, so `{id: "packet"}` is not accepted.
    const legacy = handleNotebookRequest({
      method: "open_dialog",
      payload: { id: "packet" },
    });
    expect(legacy.ok).toBe(false);
    expect(String(legacy.payload?.error)).toContain("`dialog`");

    const unknown = handleNotebookRequest({
      method: "open_dialog",
      payload: { dialog: "policy" },
    });
    expect(unknown.ok).toBe(false);
    expect(unknown.payload?.dialogs).toEqual(["packet"]);

    const focus = handleNotebookRequest({
      method: "focus",
      payload: { target: "packet" },
    });
    expect(focus.ok).toBe(false);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("declines while the workflow has not written a packet", () => {
    render(
      <InsuranceNotebookPanel
        {...props({ state: withCustom(FLOOD, { packet_markdown: "" }) })}
      />,
    );
    const result = handleNotebookRequest({
      method: "open_dialog",
      payload: { dialog: "packet" },
    });
    expect(result.ok).toBe(false);
    expect(String(result.payload?.error)).toContain("not been written");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("declines when no notebook is mounted", () => {
    const result = handleNotebookRequest({
      method: "open_dialog",
      payload: { dialog: "packet" },
    });
    expect(result).toEqual({
      ok: false,
      payload: { error: "the claim notebook is not open" },
    });
  });
});
