import * as React from "react";

import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { RoomContext } from "@livekit/components-react";
import type { Room } from "livekit-client";

import type { AgentPublicOut, BlockSpec, CartBlockState, ChartBlockState, PanelLayout, TimerBlockState } from "@/contracts/lkap-contracts";
import { Block } from "@/panels/blocks";
import { BLOCK_FIXTURE_STATES, FIXTURE_LAYOUT, fixtureAssetUrls, fixtureUiState } from "@/panels/blocks/__fixtures__";
import { handleCompositeRequest } from "@/panels/composite/requests";
import { TOPIC_UI_UPLOAD } from "@/panels/composite/upload";
import { TOPIC_UI_INK } from "@/lib/livekit";
import type { PanelProps } from "@/panels/registry";

/**
 * V6-24: the five next-block renderers (V6-23's contract, ask #215) —
 * `signature`, `chart`, `timer`, `code`, `cart`. `panel-blocks.test.tsx`
 * covers the original twelve, `panel-quartet.test.tsx` the V5-12 four,
 * `panel-canvas.test.tsx` the drawing board; this file covers the next five.
 */

/**
 * `rasteriseBoard` is mocked exactly as `panel-canvas.test.tsx` mocks it:
 * jsdom's `<canvas>` has no real 2D context, so a genuine rasterisation
 * always resolves `null` here — the mock stands in for "the pad renders to
 * some PNG", letting this file assert the request/ack/upload wiring instead.
 */
vi.mock("@/panels/blocks/canvas/rasterise", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/panels/blocks/canvas/rasterise")>();
  return { ...actual, rasteriseBoard: vi.fn(async () => new Blob(["fake-png"], { type: "image/png" })) };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.useRealTimers();
});

function agent(panel?: PanelLayout): AgentPublicOut {
  return {
    id: "a-1",
    slug: "claims",
    name: "Maya",
    description: "",
    pipeline_mode: "cascaded",
    ui_panel_id: "composite",
    capabilities: {},
    panel: (panel ?? FIXTURE_LAYOUT) as AgentPublicOut["panel"],
  };
}

function panelProps(overrides: Partial<PanelProps> = {}): PanelProps {
  return {
    state: fixtureUiState(),
    assets: fixtureAssetUrls(),
    agent: agent(),
    sessionId: "s-1",
    perform: vi.fn(async () => ({ ok: true, payload: {} })),
    transcript: [],
    connectionState: "connected",
    ...overrides,
  };
}

function specOf(type: BlockSpec["type"]): BlockSpec {
  const spec = FIXTURE_LAYOUT.blocks.find((b) => b.type === type);
  if (!spec) throw new Error(type);
  return spec;
}

function fakeRoom() {
  const writer = { write: vi.fn(async (_chunk: Uint8Array) => {}), close: vi.fn(async () => {}) };
  const streamBytes = vi.fn(async (_options: Record<string, unknown>) => writer);
  const sendText = vi.fn<(text: string, options: Record<string, unknown>) => Promise<object>>(async () => ({}));
  const room = { localParticipant: { streamBytes, sendText } };
  return { room, streamBytes, sendText, writer };
}

/** One stroke on the signature pad (pointer down at 20,20, up at 60,60). */
async function drawStroke(el: HTMLElement) {
  const svg = within(el).getByRole("img", { name: /Sign here/ });
  Object.defineProperty(svg, "getBoundingClientRect", {
    configurable: true,
    value: () => ({ left: 0, top: 0, width: 640, height: 220, right: 640, bottom: 220, x: 0, y: 0, toJSON: () => ({}) }),
  });
  await act(async () => {
    const down = new MouseEvent("pointerdown", { bubbles: true, cancelable: true });
    Object.defineProperty(down, "clientX", { value: 20 });
    Object.defineProperty(down, "clientY", { value: 20 });
    svg.dispatchEvent(down);
    const up = new MouseEvent("pointerup", { bubbles: true, cancelable: true });
    Object.defineProperty(up, "clientX", { value: 60 });
    Object.defineProperty(up, "clientY", { value: 60 });
    svg.dispatchEvent(up);
  });
}

async function findBlock(type: string) {
  await waitFor(() => expect(screen.getByTestId(`block-${type}`).getAttribute("data-loading")).toBeNull());
  return screen.getByTestId(`block-${type}`);
}

/* -------------------------------------------------------------------------- */
/* signature                                                                   */
/* -------------------------------------------------------------------------- */

describe("signature block", () => {
  const spec = specOf("signature");

  it("shows the disclosure text, a local pad and Sign / Not now while requested", async () => {
    render(<Block spec={spec} {...panelProps()} />);
    const el = await findBlock("signature");
    expect(within(el).getByText(/repair estimate of 1,240.00 USD/)).toBeTruthy();
    expect(within(el).getByRole("img", { name: /Sign here/ })).toBeTruthy();
    expect(within(el).getByRole("button", { name: "Sign" })).toBeTruthy();
    expect(within(el).getByRole("button", { name: "Not now" })).toBeTruthy();
  });

  it("hides Not now when allow_decline is off", async () => {
    const declineOffSpec: BlockSpec = { ...spec, config: { allow_decline: false } };
    render(<Block spec={declineOffSpec} {...panelProps()} />);
    const el = await findBlock("signature");
    expect(within(el).queryByRole("button", { name: "Not now" })).toBeNull();
  });

  it("Sign stays disabled until the caller has drawn", async () => {
    const perform = vi.fn(async () => ({ ok: true, payload: {} }));
    render(<Block spec={spec} {...panelProps({ perform })} />);
    const el = await findBlock("signature");
    const sign = within(el).getByRole("button", { name: "Sign" }) as HTMLButtonElement;
    expect(sign.disabled).toBe(true);
    await act(async () => {
      sign.click();
    });
    expect(perform).not.toHaveBeenCalled();

    await drawStroke(el);
    expect((within(el).getByRole("button", { name: "Sign" }) as HTMLButtonElement).disabled).toBe(false);

    await act(async () => {
      within(el).getByRole("button", { name: "Clear" }).click();
    });
    expect((within(el).getByRole("button", { name: "Sign" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("tapping Sign sends block_submit {values: {signed: true}} — never any stroke data", async () => {
    const perform = vi.fn(async () => ({ ok: true, payload: {} }));
    render(<Block spec={spec} {...panelProps({ perform })} />);
    const el = await findBlock("signature");
    await drawStroke(el);
    await act(async () => {
      within(el).getByRole("button", { name: "Sign" }).click();
    });
    expect(perform).toHaveBeenCalledWith({ action: "block_submit", payload: { block_id: "sign", values: { signed: true } } });
  });

  it("tapping Not now sends {signed: false}", async () => {
    const perform = vi.fn(async () => ({ ok: true, payload: {} }));
    render(<Block spec={spec} {...panelProps({ perform })} />);
    const el = await findBlock("signature");
    await act(async () => {
      within(el).getByRole("button", { name: "Not now" }).click();
    });
    expect(perform).toHaveBeenCalledWith({ action: "block_submit", payload: { block_id: "sign", values: { signed: false } } });
  });

  it("answers the worker's snapshot request with a PNG of the locally-drawn strokes — never lkap.ui.ink", async () => {
    const { room, streamBytes, sendText } = fakeRoom();
    render(
      <RoomContext.Provider value={room as unknown as Room}>
        <Block spec={spec} {...panelProps()} />
      </RoomContext.Provider>,
    );
    const el = await findBlock("signature");
    await drawStroke(el);

    let result: ReturnType<typeof handleCompositeRequest> | undefined;
    await act(async () => {
      result = handleCompositeRequest({ method: "snapshot", payload: { block_id: "sign" } });
    });
    expect(result).toEqual({ ok: true, payload: {} });

    await waitFor(() => expect(streamBytes).toHaveBeenCalled());
    expect(streamBytes).toHaveBeenCalledWith(
      expect.objectContaining({ topic: TOPIC_UI_UPLOAD, name: "sign.png", mimeType: "image/png", attributes: { block_id: "sign", name: "sign.png" } }),
    );
    // Ask #277: the strokes never travel on the ink topic (nor on any other text stream).
    expect(sendText).not.toHaveBeenCalledWith(expect.anything(), expect.objectContaining({ topic: TOPIC_UI_INK }));
    expect(sendText).not.toHaveBeenCalled();
    for (const [options] of streamBytes.mock.calls) {
      expect((options as { topic?: string }).topic).not.toBe(TOPIC_UI_INK);
    }
  });

  it("shows Signed once settled, with the timestamp", async () => {
    const state = fixtureUiState({ sign: { status: "submitted", signed: true, asset_id: null, text_hash: "x", at: 1777000100, disclosure_text: "x", submitted_at: 1777000100 } });
    render(<Block spec={spec} {...panelProps({ state })} />);
    const el = await findBlock("signature");
    expect(within(el).getByText("Signed")).toBeTruthy();
  });

  it("shows a decline as 'Not signed', not an error", async () => {
    const state = fixtureUiState({ sign: { status: "submitted", signed: false, asset_id: null, text_hash: "x", at: 1777000100, disclosure_text: "x", submitted_at: 1777000100 } });
    render(<Block spec={spec} {...panelProps({ state })} />);
    const el = await findBlock("signature");
    expect(within(el).getByText("Not signed")).toBeTruthy();
  });

  it("a cancelled request offers nothing to press", async () => {
    const state = fixtureUiState({ sign: { status: "cancelled", signed: null, asset_id: null, text_hash: null, at: null, disclosure_text: "x", submitted_at: null } });
    render(<Block spec={spec} {...panelProps({ state })} />);
    const el = await findBlock("signature");
    expect(within(el).queryByRole("button")).toBeNull();
  });

  it("has an idle empty state before anything is asked", async () => {
    const state = fixtureUiState({ sign: {} });
    render(<Block spec={spec} {...panelProps({ state })} />);
    const el = await findBlock("signature");
    expect(within(el).getByText(/asked to sign here/)).toBeTruthy();
  });
});

/* -------------------------------------------------------------------------- */
/* chart                                                                       */
/* -------------------------------------------------------------------------- */

describe("chart block", () => {
  const spec = specOf("chart");

  it("renders the bar fixture: title, an aria-hidden svg and an always-present accessible table", async () => {
    render(<Block spec={spec} {...panelProps()} />);
    const el = await findBlock("chart");
    expect(within(el).getByText("Claims by month")).toBeTruthy();
    const svg = el.querySelector("svg");
    expect(svg?.getAttribute("aria-hidden")).toBe("true");
    const table = el.querySelector('[data-slot="chart-table"]');
    expect(table).toBeTruthy();
    expect(table?.className).toContain("sr-only"); // show_table is off in the fixture
    expect(within(el).getByText("Most claims came in March.")).toBeTruthy();
    const fixture = BLOCK_FIXTURE_STATES.chart as ChartBlockState;
    for (const point of fixture.points ?? []) {
      expect(within(table as HTMLElement).getByText(point.label)).toBeTruthy();
    }
  });

  it("show_table puts the table on screen too", async () => {
    const visibleSpec: BlockSpec = { ...spec, config: { ...spec.config, show_table: true } };
    render(<Block spec={visibleSpec} {...panelProps()} />);
    const el = await findBlock("chart");
    const table = el.querySelector('[data-slot="chart-table"]');
    expect(table?.className).not.toContain("sr-only");
  });

  it("renders a number chart as one big value", async () => {
    const numberSpec: BlockSpec = { id: "n", type: "chart", title: null, config: { kind: "number" } };
    const state = fixtureUiState({ n: { kind: "number", points: [{ label: "Total", value: 42, series: null }], unit: "claims" } });
    render(<Block spec={numberSpec} {...panelProps({ state })} />);
    const el = await findBlock("chart");
    const numberEl = el.querySelector('[data-slot="chart-number"]') as HTMLElement;
    expect(within(numberEl).getByText("42 claims")).toBeTruthy();
  });

  it("renders a gauge with min/max/value labels", async () => {
    const gaugeSpec: BlockSpec = { id: "g", type: "chart", title: null, config: { kind: "gauge" } };
    const state = fixtureUiState({ g: { kind: "gauge", points: [{ label: "Load", value: 75, series: null }], gauge_min: 0, gauge_max: 100 } });
    render(<Block spec={gaugeSpec} {...panelProps({ state })} />);
    const el = await findBlock("chart");
    const gauge = el.querySelector('[data-slot="chart-gauge"]') as HTMLElement;
    expect(within(gauge).getByText("75")).toBeTruthy();
    expect(within(gauge).getByText("0")).toBeTruthy();
    expect(within(gauge).getByText("100")).toBeTruthy();
  });

  it("renders one pie slice per point", async () => {
    const pieSpec: BlockSpec = { id: "p", type: "chart", title: null, config: { kind: "pie" } };
    const state = fixtureUiState({
      p: {
        kind: "pie",
        points: [
          { label: "A", value: 1, series: null },
          { label: "B", value: 2, series: null },
          { label: "C", value: 3, series: null },
        ],
      },
    });
    render(<Block spec={pieSpec} {...panelProps({ state })} />);
    const el = await findBlock("chart");
    const svg = el.querySelector("svg") as SVGSVGElement;
    expect(svg.querySelectorAll("path").length).toBe(3);
  });

  it("has an empty state with no points", async () => {
    const state = fixtureUiState({ claims_chart: { kind: "bar", points: [] } });
    render(<Block spec={spec} {...panelProps({ state })} />);
    const el = await findBlock("chart");
    expect(within(el).getByText("No numbers yet.")).toBeTruthy();
  });
});

/* -------------------------------------------------------------------------- */
/* timer                                                                       */
/* -------------------------------------------------------------------------- */

describe("timer block", () => {
  const spec = specOf("timer");

  /**
   * `timer` is lazy (`React.lazy`); `findBlock`'s `waitFor` polls on real
   * timers, so this uses `vi.setSystemTime` alone (which fakes only `Date`,
   * not the timer functions — Vitest refuses a later `vi.useFakeTimers()`
   * layered on top of it) and a short real delay to let the component's own
   * real `setInterval` tick once against the newly-set system clock.
   */
  it("counts down from duration_s on the page's own clock, ignoring ends_at", async () => {
    const state = fixtureUiState({
      timer: { mode: "countdown", status: "running", duration_s: 120, started_at: 1_700_000_000, ends_at: 999, ended_at: null, label: "Find your policy number" },
    });
    vi.setSystemTime(1_700_000_000_000);
    render(<Block spec={spec} {...panelProps({ state })} />);
    const el = await findBlock("timer");
    expect(within(el).getByText("2:00")).toBeTruthy();
    expect(within(el).getByText("Find your policy number")).toBeTruthy();

    vi.setSystemTime(1_700_000_030_000); // 30s later
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 300));
    });
    expect(within(el).getByText("1:30")).toBeTruthy();
  });

  it("counts up for elapsed mode, capped at duration_s", async () => {
    const state: Record<string, TimerBlockState> = {
      t: { mode: "elapsed", status: "running", duration_s: 60, started_at: 1_700_000_000, ends_at: 1, ended_at: null, label: null },
    };
    const elapsedSpec: BlockSpec = { id: "t", type: "timer", title: "Timer", config: {} };
    vi.setSystemTime(1_700_000_000_000);
    render(<Block spec={elapsedSpec} {...panelProps({ state: fixtureUiState(state) })} />);
    const el = await findBlock("timer");
    expect(within(el).getByText("0:00")).toBeTruthy();

    vi.setSystemTime(1_700_000_090_000); // 90s later, past the 60s cap
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 300));
    });
    expect(within(el).getByText("1:00")).toBeTruthy();
  });

  it("shows Time's up once ended", async () => {
    const state = fixtureUiState({ timer: { mode: "countdown", status: "ended", duration_s: 60, started_at: 1, ends_at: 61, ended_at: 61, label: null } });
    render(<Block spec={spec} {...panelProps({ state })} />);
    const el = await findBlock("timer");
    expect(within(el).getByText(/Time.s up/)).toBeTruthy();
  });

  it("has an idle empty state before any timer starts", async () => {
    const state = fixtureUiState({ timer: {} });
    render(<Block spec={spec} {...panelProps({ state })} />);
    const el = await findBlock("timer");
    expect(within(el).getByText("No timer running.")).toBeTruthy();
  });
});

/* -------------------------------------------------------------------------- */
/* code                                                                        */
/* -------------------------------------------------------------------------- */

describe("code block", () => {
  const spec = specOf("code");

  it("renders the fixture's code as plain text inside <pre><code>, with its language and title", async () => {
    render(<Block spec={spec} {...panelProps()} />);
    const el = await findBlock("code");
    const pre = el.querySelector('[data-slot="block-code-pre"]');
    expect(pre?.tagName).toBe("PRE");
    expect(pre?.querySelector("code")?.textContent).toBe('{\n  "policy": "PX-20931",\n  "excess": 250\n}');
    expect(within(el).getByText("json")).toBeTruthy();
    expect(within(el).getByText("Your policy record")).toBeTruthy();
  });

  it("never interprets Markdown or HTML in the code — a fence-breaking payload stays inert text", async () => {
    const payload = "```js\n<script>window.x = 1</script>\n```\n[link](https://evil.test)";
    const state = fixtureUiState({ record: { code: payload, language: null, title: null } });
    const { container } = render(<Block spec={spec} {...panelProps({ state })} />);
    const el = await findBlock("code");
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("a")).toBeNull();
    const pre = el.querySelector('[data-slot="block-code-pre"]');
    expect(pre?.querySelector("code")?.textContent).toBe(payload);
  });

  it("wrap toggles whitespace handling", async () => {
    const wrapSpec: BlockSpec = { ...spec, config: { wrap: true } };
    render(<Block spec={wrapSpec} {...panelProps()} />);
    const el = await findBlock("code");
    const pre = el.querySelector('[data-slot="block-code-pre"]');
    expect(pre?.className).toContain("whitespace-pre-wrap");
  });

  it("copies the code to the clipboard", async () => {
    const writeText = vi.fn(async () => {});
    Object.assign(navigator, { clipboard: { writeText } });
    render(<Block spec={spec} {...panelProps()} />);
    const el = await findBlock("code");
    await act(async () => {
      within(el).getByRole("button", { name: "Copy" }).click();
    });
    expect(writeText).toHaveBeenCalledWith('{\n  "policy": "PX-20931",\n  "excess": 250\n}');
  });

  it("has an empty state with no code", async () => {
    const state = fixtureUiState({ record: { code: "", language: null, title: null } });
    render(<Block spec={spec} {...panelProps({ state })} />);
    const el = await findBlock("code");
    expect(within(el).getByText("Nothing to show yet.")).toBeTruthy();
  });
});

/* -------------------------------------------------------------------------- */
/* cart                                                                        */
/* -------------------------------------------------------------------------- */

describe("cart block", () => {
  const spec = specOf("cart");

  it("renders lines, adjustments and totals formatted with the state's own currency", async () => {
    render(<Block spec={spec} {...panelProps()} />);
    const el = await findBlock("cart");
    expect(within(el).getByText("Water filter")).toBeTruthy();
    expect(within(el).getByText("2×")).toBeTruthy();
    expect(within(el).getByText("$49.00")).toBeTruthy();
    expect(within(el).getByText("Monday morning")).toBeTruthy();
    expect(within(el).getByText("Discount")).toBeTruthy();
    expect(within(el).getByText("-$10.00")).toBeTruthy();
    expect(within(el).getByText("$138.24")).toBeTruthy(); // total, exactly as given
  });

  it("formats money in the cart's own currency, not a hardcoded USD", async () => {
    const state: Record<string, CartBlockState> = {
      o: {
        currency: "EUR",
        lines: [{ id: "a", name: "Widget", quantity: 1, unit_price: 10, line_total: 10, note: null }],
        adjustments: [],
        subtotal: 10,
        total: 10,
        updated_at: null,
      },
    };
    const eurSpec: BlockSpec = { id: "o", type: "cart", title: "Order", config: { currency: "EUR" } };
    render(<Block spec={eurSpec} {...panelProps({ state: fixtureUiState(state) })} />);
    const el = await findBlock("cart");
    // The single line's total and the cart's grand total are both €10.00 here (one line, no
    // adjustments) — both rows must show the euro sign, not a hardcoded "$".
    expect(within(el).getAllByText("€10.00")).toHaveLength(2);
  });

  it("shows the total exactly as given, never recomputed by the renderer", async () => {
    // Deliberately inconsistent with the lines (the worker's own validator would refuse this;
    // the point here is that the *renderer* never second-guesses the state it is handed).
    const state: Record<string, CartBlockState> = {
      o: {
        currency: "USD",
        lines: [{ id: "a", name: "Widget", quantity: 1, unit_price: 10, line_total: 10, note: null }],
        adjustments: [],
        subtotal: 10,
        total: 999,
        updated_at: null,
      },
    };
    const oddSpec: BlockSpec = { id: "o", type: "cart", title: "Order", config: {} };
    render(<Block spec={oddSpec} {...panelProps({ state: fixtureUiState(state) })} />);
    const el = await findBlock("cart");
    expect(within(el).getByText("$999.00")).toBeTruthy();
  });

  it("has an empty state with no lines", async () => {
    const state = fixtureUiState({ order: { currency: "USD", lines: [], adjustments: [], subtotal: 0, total: 0 } });
    render(<Block spec={spec} {...panelProps({ state })} />);
    const el = await findBlock("cart");
    expect(within(el).getByText("Nothing in the cart yet.")).toBeTruthy();
  });
});

/* -------------------------------------------------------------------------- */
/* hostile labels (ask #277)                                                   */
/* -------------------------------------------------------------------------- */

const SCRIPT_LABEL = "<script>alert(1)</script>";
const JS_URL_LABEL = "javascript:alert(1)";
const LONG_LABEL = "L".repeat(200);

/** Nothing the agent or the caller wrote became markup, a script or a live link. */
function expectInert(el: HTMLElement) {
  expect(el.querySelector("script")).toBeNull();
  for (const node of Array.from(el.querySelectorAll("[href], [src]"))) {
    expect(node.getAttribute("href") ?? "").not.toMatch(/^\s*javascript:/i);
    expect(node.getAttribute("src") ?? "").not.toMatch(/^\s*javascript:/i);
  }
  for (const node of Array.from(el.querySelectorAll("*"))) {
    for (const attr of Array.from(node.attributes)) {
      expect(attr.name.startsWith("on")).toBe(false);
    }
  }
}

describe("hostile labels stay text", () => {
  it("a chart's title, labels, unit and caption render as text", async () => {
    const hostileSpec: BlockSpec = { id: "h", type: "chart", title: null, config: { kind: "bar", show_table: true } };
    const state = fixtureUiState({
      h: {
        kind: "bar",
        title: SCRIPT_LABEL,
        unit: JS_URL_LABEL,
        points: [
          { label: SCRIPT_LABEL, value: 1, series: null },
          { label: JS_URL_LABEL, value: 2, series: null },
          { label: LONG_LABEL, value: 3, series: null },
        ],
        caption: SCRIPT_LABEL,
      },
    });
    render(<Block spec={hostileSpec} {...panelProps({ state })} />);
    const el = await findBlock("chart");
    expectInert(el);
    expect(el.textContent).toContain(SCRIPT_LABEL);
    expect(el.textContent).toContain(JS_URL_LABEL);
    expect(el.textContent).toContain(LONG_LABEL);
  });

  it("a cart's line names, notes and adjustment labels render as text", async () => {
    const state: Record<string, CartBlockState> = {
      o: {
        currency: "USD",
        lines: [
          { id: "a", name: SCRIPT_LABEL, quantity: 1, unit_price: 10, line_total: 10, note: JS_URL_LABEL },
          { id: "b", name: LONG_LABEL, quantity: 1, unit_price: 5, line_total: 5, note: null },
        ],
        adjustments: [{ label: SCRIPT_LABEL, amount: -1 }],
        subtotal: 15,
        total: 14,
        updated_at: null,
      },
    };
    const hostileSpec: BlockSpec = { id: "o", type: "cart", title: "Order", config: {} };
    render(<Block spec={hostileSpec} {...panelProps({ state: fixtureUiState(state) })} />);
    const el = await findBlock("cart");
    expectInert(el);
    expect(within(el).getAllByText(SCRIPT_LABEL)).toHaveLength(2);
    expect(within(el).getByText(JS_URL_LABEL)).toBeTruthy();
    expect(within(el).getByText(LONG_LABEL)).toBeTruthy();
  });

  it("a signature's disclosure text renders as text", async () => {
    const wording = `${SCRIPT_LABEL} [click](${JS_URL_LABEL}) ${LONG_LABEL}`;
    const state = fixtureUiState({
      sign: { status: "requested", signed: null, asset_id: null, text_hash: null, at: null, disclosure_text: wording, submitted_at: null },
    });
    render(<Block spec={specOf("signature")} {...panelProps({ state })} />);
    const el = await findBlock("signature");
    expectInert(el);
    expect(within(el).getByText(wording)).toBeTruthy();
  });
});
