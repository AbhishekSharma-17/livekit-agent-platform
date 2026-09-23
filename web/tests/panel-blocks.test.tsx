import * as React from "react";

import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import type { AgentPublicOut, BlockSpec, PanelLayout, UiRequest } from "@/contracts/lkap-contracts";
import { emptyUiState } from "@/lib/ui-state";
import { BLOCK_COMPONENTS, Block, LAZY_BLOCK_TYPES } from "@/panels/blocks";
import { BLOCK_CATALOG, BLOCK_TYPES, blockStateOf, blockTitle, initialBlockState } from "@/panels/blocks/catalog";
import { coerceForm, formFields } from "@/panels/blocks/form";
import { formatCell } from "@/panels/blocks/table";
import { resolveDocument } from "@/panels/blocks/document";
import {
  BLOCK_FIXTURE_STATES,
  FIXTURE_LAYOUT,
  FIXTURE_TRANSCRIPT,
  FORM_SUBMITTED_STATE,
  fixtureAssetUrls,
  fixtureUiState,
} from "@/panels/blocks/__fixtures__";
import { COMPOSITE_PANEL, CompositePanel } from "@/panels/composite";
import {
  DEFAULT_COMPOSITE_BLOCKS,
  normalizeBlocks,
  panelLayoutOf,
  storedPanelLayout,
} from "@/panels/composite/layout";
import { handleCompositeRequest, safeNavigateUrl } from "@/panels/composite/requests";
import { PANELS, agentActionFor, panelLayoutFor, resolvePanel, type PanelProps } from "@/panels/registry";

/**
 * V2-11: the twelve block components, `<Block>`, the composite panel and its
 * `lkap.ui.request` handling. Every block renders its fixture state
 * (`src/panels/blocks/__fixtures__`) with no LiveKit room.
 *
 * pdf.js is replaced by a stub — jsdom has no canvas — so the document test
 * covers everything around the page (source resolution, paging, highlights,
 * notes); the real PDF rendering is covered by the preview screenshots.
 */
vi.mock("@/panels/blocks/pdf-page", () => {
  function PdfPageStub({
    url,
    page,
    children,
    onPageCount,
  }: {
    url: string;
    page: number;
    children?: React.ReactNode;
    onPageCount?: (n: number) => void;
  }) {
    React.useEffect(() => onPageCount?.(2), [onPageCount]);
    return (
      <div data-testid="pdf-stub" data-url={url.slice(0, 30)} data-page={page}>
        {children}
      </div>
    );
  }
  return { default: PdfPageStub };
});

beforeAll(() => {
  Element.prototype.scrollIntoView = function scrollIntoView() {};
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
afterAll(() => vi.restoreAllMocks());

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
    transcript: FIXTURE_TRANSCRIPT,
    connectionState: "connected",
    ...overrides,
  };
}

function specOf(type: BlockSpec["type"]): BlockSpec {
  const spec = FIXTURE_LAYOUT.blocks.find((b) => b.type === type);
  if (!spec) throw new Error(type);
  return spec;
}

/* -------------------------------------------------------------------------- */

describe("block catalog", () => {
  it("has a component and a catalog entry for every BlockType", () => {
    expect([...BLOCK_TYPES].sort()).toEqual(Object.keys(BLOCK_COMPONENTS).sort());
    expect([...BLOCK_TYPES].sort()).toEqual(Object.keys(BLOCK_CATALOG).sort());
    expect([...LAZY_BLOCK_TYPES].sort()).toEqual(["document", "table", "video"]);
  });

  it("seeds initial state from config keys that name a state field, like the worker", () => {
    expect(initialBlockState({ id: "t", type: "table", config: { columns: [{ key: "a", label: "A" }], nope: 1 } })).toEqual({
      columns: [{ key: "a", label: "A" }],
      rows: [],
      selected_row: null,
    });
    expect(initialBlockState({ id: "v", type: "video", config: { source: "user_screen" } })).toEqual({ source: "user_screen", muted: false });
    expect(initialBlockState({ id: "n", type: "notes", config: {} })).toEqual({});
    expect(blockStateOf({ id: "g", type: "gallery" }, { g: { asset_ids: ["x"] } })).toEqual({ asset_ids: ["x"], selected: null });
  });

  it("uses the title, else the type's default heading (status has none)", () => {
    expect(blockTitle({ id: "s", type: "status", title: null })).toBeNull();
    expect(blockTitle({ id: "n", type: "notes", title: "  " })).toBe("Notes");
    expect(blockTitle({ id: "t", type: "table", title: "Damaged items" })).toBe("Damaged items");
  });
});

describe("each block renders its fixture state", () => {
  const cases: [BlockSpec["type"], (el: HTMLElement) => Promise<void> | void][] = [
    ["status", (el) => void expect(within(el).getByText("In progress")).toBeTruthy()],
    ["notes", (el) => void expect(within(el).getByText("Booking a service visit for a leaking dishwasher.")).toBeTruthy()],
    ["checklist", (el) => void expect(within(el).getByText("Service address")).toBeTruthy()],
    ["activity", (el) => void expect(within(el).getByText("Holding Thursday morning")).toBeTruthy()],
    [
      "form",
      (el) => {
        expect((within(el).getByLabelText("Full name") as HTMLInputElement).value).toBe("Priya Raman");
        expect((within(el).getByLabelText("Date of loss") as HTMLInputElement).type).toBe("date");
        expect((within(el).getByLabelText("Email") as HTMLInputElement).type).toBe("email");
        expect((within(el).getByLabelText("Cause") as HTMLSelectElement).value).toBe("Fire");
        expect((within(el).getByLabelText("I rent this home") as HTMLInputElement).type).toBe("checkbox");
      },
    ],
    [
      "table",
      async (el) => {
        expect(await within(el).findByText("Induction hob")).toBeTruthy();
        expect(within(el).getByText("1,450.5")).toBeTruthy();
        expect(el.querySelector("[data-row-id=r2]")?.getAttribute("data-selected")).toBe("true");
      },
    ],
    [
      "document",
      async (el) => {
        const pdf = await within(el).findByTestId("pdf-stub");
        expect(pdf.getAttribute("data-page")).toBe("1");
        expect(pdf.getAttribute("data-url")).toMatch(/^data:application\/pdf/);
        expect(pdf.querySelectorAll("[data-slot=block-document-highlight]")).toHaveLength(2);
        expect(within(el).getByText("Needs a signature before we can pay out.")).toBeTruthy();
      },
    ],
    [
      "gallery",
      (el) => {
        expect(within(el).getByAltText("Scorched stove top")).toBeTruthy();
        expect(el.querySelectorAll("[data-selected=true]")).toHaveLength(1);
      },
    ],
    [
      "kb_citations",
      (el) => {
        expect(within(el).getByText("home-policy-2026.pdf")).toBeTruthy();
        expect(within(el).getByText("91% match")).toBeTruthy();
      },
    ],
    [
      "transcript",
      (el) => {
        expect(within(el).getByText("There was a small kitchen fire this morning.")).toBeTruthy();
        // show_tools: the activity rows are interleaved.
        expect(el.querySelectorAll("[data-slot=block-transcript-tool]").length).toBeGreaterThan(0);
      },
    ],
    ["video", (el) => void expect(within(el).getByText("Turn on your camera to show it here.")).toBeTruthy()],
    ["custom", (el) => void expect(within(el).getByText("Show raw state")).toBeTruthy()],
  ];

  it.each(cases)("%s", async (type, check) => {
    const spec = specOf(type);
    render(<Block spec={spec} {...panelProps()} />);
    // Lazy blocks render a loading frame first; wait for the real one.
    await waitFor(() => expect(screen.getByTestId(`block-${type}`).getAttribute("data-loading")).toBeNull());
    const el = screen.getByTestId(`block-${type}`);
    expect(el.getAttribute("data-block-id")).toBe(spec.id);
    await check(el);
  });
});

describe("each block has an empty state", () => {
  const empties: Partial<Record<BlockSpec["type"], RegExp>> = {
    notes: /Nothing noted yet/,
    checklist: /No open items/,
    activity: /has not run any tools/,
    form: /will ask for details here/,
    table: /No rows yet/,
    document: /hasn.t opened a document/,
    gallery: /No photos yet/,
    kb_citations: /Sources appear here/,
    transcript: /conversation appears here/,
    video: /avatar.s video appears here/,
    custom: /Nothing from the pack yet/,
  };
  it.each(Object.entries(empties))("%s", async (type, text) => {
    render(
      <Block
        spec={{ id: type, type: type as BlockSpec["type"], config: {} }}
        {...panelProps({ state: emptyUiState(), assets: new Map(), transcript: [] })}
      />,
    );
    expect(await screen.findByText(text as RegExp)).toBeTruthy();
  });

  it("status shows 'Not started'", () => {
    render(<Block spec={{ id: "s", type: "status" }} {...panelProps({ state: emptyUiState() })} />);
    expect(screen.getByText("Not started")).toBeTruthy();
  });
});

/* -------------------------------------------------------------------------- */

describe("form block", () => {
  const spec = specOf("form");

  it("maps the worker's field schema (fields_to_schema) to controls in property order", () => {
    const fields = formFields(BLOCK_FIXTURE_STATES.form?.schema);
    expect(fields.map((f) => [f.name, f.kind, f.required])).toEqual([
      ["full_name", "text", true],
      ["email", "email", true],
      ["date_of_loss", "date", true],
      ["rooms", "integer", false],
      ["cause", "select", false],
      ["tenant", "boolean", false],
    ]);
  });

  it("coerces values and reports errors", () => {
    const fields = formFields(BLOCK_FIXTURE_STATES.form?.schema);
    const bad = coerceForm(fields, { full_name: " ", email: "nope", date_of_loss: "", rooms: "2.5", cause: "Hail", tenant: false });
    expect(Object.keys(bad.errors).sort()).toEqual(["cause", "date_of_loss", "email", "full_name", "rooms"]);
    const good = coerceForm(fields, { full_name: "Priya", email: "p@x.io", date_of_loss: "2026-09-21", rooms: "2", cause: "Fire", tenant: true });
    expect(good).toEqual({
      values: { full_name: "Priya", email: "p@x.io", date_of_loss: "2026-09-21", rooms: 2, cause: "Fire", tenant: true },
      errors: {},
    });
  });

  it("submits the values through perform as form_submit", async () => {
    const perform = vi.fn(async () => ({ ok: true, payload: {} }));
    render(<Block spec={spec} {...panelProps({ perform })} />);
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "priya@example.com" } });
    fireEvent.change(screen.getByLabelText("Date of loss"), { target: { value: "2026-09-21" } });
    fireEvent.change(screen.getByLabelText("Rooms affected"), { target: { value: "2" } });
    fireEvent.click(screen.getByLabelText("I rent this home"));
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(perform).toHaveBeenCalledTimes(1));
    expect(perform).toHaveBeenCalledWith({
      action: "form_submit",
      payload: {
        block_id: "intake",
        values: {
          full_name: "Priya Raman",
          email: "priya@example.com",
          date_of_loss: "2026-09-21",
          rooms: 2,
          cause: "Fire",
          tenant: true,
        },
      },
    });
    expect(screen.getByRole("button", { name: "Sending…" })).toBeTruthy();
  });

  it("does not submit an invalid form and shows field errors", () => {
    const perform = vi.fn();
    render(<Block spec={spec} {...panelProps({ perform })} />);
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(perform).not.toHaveBeenCalled();
    expect(screen.getByText("Enter email.")).toBeTruthy();
    expect(screen.getByLabelText("Email").getAttribute("aria-invalid")).toBe("true");
  });

  it("dismisses with {block_id, cancelled: true}", async () => {
    const perform = vi.fn(async () => ({ ok: true, payload: {} }));
    render(<Block spec={spec} {...panelProps({ perform })} />);
    fireEvent.click(screen.getByRole("button", { name: "Not now" }));
    await waitFor(() =>
      expect(perform).toHaveBeenCalledWith({ action: "form_submit", payload: { block_id: "intake", cancelled: true } }),
    );
  });

  it("shows the agent's refusal and lets the caller retry", async () => {
    const perform = vi.fn(async () => ({ ok: false, payload: {}, error: "values must be an object" }));
    render(<Block spec={spec} {...panelProps({ perform })} />);
    fireEvent.click(screen.getByRole("button", { name: "Not now" }));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "values must be an object");
    expect(screen.getByRole("button", { name: "Send" }).hasAttribute("disabled")).toBe(false);
  });

  it("shows what was sent once submitted", () => {
    const state = fixtureUiState({ intake: FORM_SUBMITTED_STATE });
    render(<Block spec={spec} {...panelProps({ state })} />);
    expect(screen.getByText("Sent")).toBeTruthy();
    expect(screen.getByText("priya@example.com")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Send" })).toBeNull();
  });

  it("starts a fresh draft for a new request", () => {
    const { rerender } = render(<Block spec={spec} {...panelProps()} />);
    fireEvent.change(screen.getByLabelText("Full name"), { target: { value: "Typed" } });
    const next = { ...BLOCK_FIXTURE_STATES.form, values: { full_name: "New prefill" } };
    rerender(<Block spec={spec} {...panelProps({ state: fixtureUiState({ intake: next }) })} />);
    expect((screen.getByLabelText("Full name") as HTMLInputElement).value).toBe("New prefill");
  });
});

describe("document block", () => {
  const spec = specOf("document");

  it("pages locally, and the agent's new page wins", async () => {
    const { rerender } = render(<Block spec={spec} {...panelProps()} />);
    await screen.findByTestId("pdf-stub");
    fireEvent.click(await screen.findByRole("button", { name: "Next page" }));
    expect(screen.getByTestId("pdf-stub").getAttribute("data-page")).toBe("2");
    expect(screen.getByText("2 / 2")).toBeTruthy();
    // Page 2 has no highlights.
    expect(screen.queryByText("Needs a signature before we can pay out.")).toBeNull();
    const state = fixtureUiState({ claim_form: { ...BLOCK_FIXTURE_STATES.document, page: 1, highlights: [] } });
    rerender(<Block spec={spec} {...panelProps({ state })} />);
    expect(screen.getByTestId("pdf-stub").getAttribute("data-page")).toBe("2");
  });

  it("resolves sources: assets by mime, https URLs by extension, never other schemes", () => {
    const assets = [{ asset_id: "a", mime: "image/png", caption: "Photo" }];
    const urls = new Map([["a", "blob:x"]]);
    expect(resolveDocument({ asset_id: "a" }, assets, urls)).toEqual({ kind: "image", src: "blob:x", name: "Photo", external: null });
    expect(resolveDocument({ asset_id: "a" }, assets, new Map())?.src).toBeNull();
    expect(resolveDocument({ url: "https://x.test/docs/Policy%20Guide.pdf" }, [], urls)).toMatchObject({ kind: "pdf", name: "Policy Guide.pdf" });
    expect(resolveDocument({ url: "https://x.test/readme.md" }, [], urls)?.kind).toBe("markdown");
    expect(resolveDocument({ url: "https://x.test/a.png" }, [], urls)?.kind).toBe("image");
    expect(resolveDocument({ url: "http://x.test/a.pdf" }, [], urls)?.src).toBeNull();
    expect(resolveDocument({ url: "javascript:alert(1)" }, [], urls)?.src).toBeNull();
    expect(resolveDocument({}, [], urls)).toBeNull();
  });

  it("renders an image with its highlights", async () => {
    const state = fixtureUiState({
      claim_form: { url: "https://cdn.test/receipt.png", page: 1, highlights: [{ page: 1, bbox: [0.1, 0.2, 0.5, 0.4], note: "Total" }] },
    });
    render(<Block spec={spec} {...panelProps({ state })} />);
    expect(await screen.findByAltText("receipt.png")).toBeTruthy();
    const box = document.querySelector<HTMLElement>("[data-slot=block-document-highlight]");
    expect(box?.style.left).toBe("10%");
    expect(box?.style.height).toBe("20%");
    expect(screen.getByRole("link", { name: /Open/ }).getAttribute("rel")).toContain("noopener");
  });

  it("renders Markdown fetched from the document URL", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, text: async () => "# Claims FAQ\n\nKeep your receipts." }) as Response));
    const state = fixtureUiState({ claim_form: { url: "https://cdn.test/faq.md", page: 1, highlights: [] } });
    render(<Block spec={spec} {...panelProps({ state })} />);
    expect(await screen.findByText("Keep your receipts.")).toBeTruthy();
  });

  it("says when a link can't be opened", async () => {
    const state = fixtureUiState({ claim_form: { url: "http://insecure.test/a.pdf", page: 1, highlights: [] } });
    render(<Block spec={spec} {...panelProps({ state })} />);
    expect(await screen.findByText(/can’t be opened/)).toBeTruthy();
  });
});

describe("table block", () => {
  it("formats cells by column type", () => {
    expect(formatCell(1450.5, "number")).toBe("1,450.5");
    expect(formatCell(true, "boolean")).toBe("Yes");
    expect(formatCell("2026-09-21", "date")).toBe("2026-09-21");
    expect(formatCell(null, "string")).toBe("—");
    expect(formatCell({ a: 1 }, "string")).toBe('{"a":1}');
  });

  it("derives columns from the rows when none are declared", async () => {
    const state = fixtureUiState({ items: { columns: [], rows: [{ id: "r1", item: "Hob", qty: 1 }] } });
    render(<Block spec={specOf("table")} {...panelProps({ state })} />);
    expect(await screen.findByRole("columnheader", { name: "item" })).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: "qty" })).toBeTruthy();
  });
});

describe("transcript block", () => {
  it("hides tool rows unless show_tools", () => {
    const state = fixtureUiState({ transcript: { show_tools: false } });
    render(<Block spec={specOf("transcript")} {...panelProps({ state })} />);
    expect(document.querySelectorAll("[data-slot=block-transcript-tool]")).toHaveLength(0);
    expect(document.querySelector("[data-who=user]")?.textContent).toContain("kitchen fire");
  });
});

/* -------------------------------------------------------------------------- */

describe("layout (R-V2-7)", () => {
  it("reads agent.panel, sorted by order with duplicate or invalid ids dropped", () => {
    const layout = panelLayoutOf(
      agent({
        panel_id: "composite",
        layout: "wide",
        blocks: [
          { id: "b", type: "notes", order: 2 },
          { id: "a", type: "status", order: 1 },
          { id: "b", type: "table", order: 3 },
          { id: "x/y", type: "table", order: 0 },
        ],
      }),
    );
    expect(layout.layout).toBe("wide");
    expect(layout.blocks.map((b) => b.id)).toEqual(["a", "b"]);
  });

  it("carries BlockSpec.title straight from the generated contract type (R-V2-15)", () => {
    const titled: BlockSpec = { id: "items", type: "table", title: "Damaged items", config: {}, order: 0 };
    const layout = panelLayoutOf(agent({ panel_id: "composite", blocks: [titled] }));
    expect(layout.blocks[0]?.title).toBe("Damaged items");
    expect(blockTitle(layout.blocks[0]!)).toBe("Damaged items");
  });

  it("falls back to the api's effective layout when an older api omits panel", () => {
    const legacy = { ...agent(), panel: undefined } as unknown as AgentPublicOut;
    expect(panelLayoutOf({ ...legacy, ui_panel_id: "generic" }).blocks).toEqual(normalizeBlocks(DEFAULT_COMPOSITE_BLOCKS));
    expect(panelLayoutOf({ ...legacy, ui_panel_id: "insurance_notebook" })).toEqual({
      panel_id: "insurance_notebook",
      layout: "side",
      blocks: [],
    });
  });

  it("storedPanelLayout shows the default four for a block panel saved without blocks", () => {
    expect(storedPanelLayout(undefined, "composite").blocks).toHaveLength(4);
    expect(storedPanelLayout({ panel_id: "insurance_notebook", blocks: [] }, "insurance_notebook")).toEqual({
      panel_id: "insurance_notebook",
      blocks: [],
    });
  });

  it("registers the composite panel with a per-agent layout", () => {
    expect(resolvePanel("composite")).toBe(COMPOSITE_PANEL);
    expect(PANELS.composite.blocksAware).toBe(true);
    expect(panelLayoutFor(COMPOSITE_PANEL, agent({ ...FIXTURE_LAYOUT, layout: "wide" }))).toBe("wide");
    expect(panelLayoutFor(COMPOSITE_PANEL, agent())).toBe("side");
    // A custom panel keeps its own layout whatever PanelLayout.layout says.
    expect(panelLayoutFor(PANELS.insurance_notebook, agent({ panel_id: "insurance_notebook", layout: "side" }))).toBe("wide");
  });
});

describe("composite panel", () => {
  it("renders the layout's blocks in order", async () => {
    render(<CompositePanel {...panelProps()} />);
    const panel = screen.getByTestId("composite-panel");
    await screen.findByText("Induction hob");
    const order = [...panel.querySelectorAll("[data-block-id]")].map((el) => el.getAttribute("data-block-id"));
    expect(order).toEqual(FIXTURE_LAYOUT.blocks.map((b) => b.id));
  });

  it("says so when the layout has no blocks", () => {
    render(<CompositePanel {...panelProps({ agent: agent({ panel_id: "composite", blocks: [] }) })} />);
    expect(screen.getByText("This panel has no blocks yet.")).toBeTruthy();
  });

  it("renders a custom panel's <Block> with the same props (the custom-panel entry point)", () => {
    function PackPanel(props: PanelProps) {
      return <Block spec={{ id: "sources", type: "kb_citations", title: "Why I said that" }} {...props} />;
    }
    render(<PackPanel {...panelProps()} />);
    expect(screen.getByRole("heading", { name: /Why I said that/ })).toBeTruthy();
    expect(screen.getByText("claims-faq.md")).toBeTruthy();
  });
});

describe("composite lkap.ui.request handling", () => {
  function req(method: UiRequest["method"], payload: Record<string, unknown>): UiRequest {
    return { v: 1, method, payload };
  }

  it("acks form at once and focuses the form's first field", async () => {
    render(<CompositePanel {...panelProps()} />);
    expect(handleCompositeRequest(req("form", { block_id: "intake", schema: {}, prefill: {} }))).toEqual({ ok: true, payload: {} });
    await waitFor(() => expect(document.activeElement).toBe(screen.getByLabelText("Full name")));
    expect(screen.getByTestId("block-form").getAttribute("data-highlighted")).toBe("true");
  });

  it("acks form even before the panel is mounted (the block state drives the form)", () => {
    expect(handleCompositeRequest(req("form", { block_id: "intake" }))).toEqual({ ok: true, payload: {} });
    expect(handleCompositeRequest(req("form", {})).ok).toBe(false);
  });

  it("show_block scrolls a known block into view and refuses an unknown one", async () => {
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    render(<CompositePanel {...panelProps()} />);
    expect(handleCompositeRequest(req("show_block", { block_id: "claim_form" }))).toEqual({ ok: true, payload: {} });
    await waitFor(() => expect(scroll).toHaveBeenCalled());
    expect(handleCompositeRequest(req("show_block", { block_id: "nope" })).ok).toBe(false);
    expect(handleCompositeRequest(req("focus", { target: "sources" })).ok).toBe(true);
  });

  it("navigate asks first and opens http(s) links in a new tab with noopener", async () => {
    const open = vi.fn();
    vi.stubGlobal("open", open);
    render(<CompositePanel {...panelProps()} />);
    await act(async () => {
      expect(handleCompositeRequest(req("navigate", { url: "https://example.com/policy" })).ok).toBe(true);
    });
    const dialog = await screen.findByRole("dialog", { name: "Open this link?" });
    expect(within(dialog).getByText("https://example.com/policy")).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: /Open link/ }));
    expect(open).toHaveBeenCalledWith("https://example.com/policy", "_blank", "noopener,noreferrer");
  });

  it("rejects non-web navigate URLs and unknown methods", () => {
    expect(safeNavigateUrl("javascript:alert(1)")).toBeNull();
    expect(safeNavigateUrl("/relative")).toBeNull();
    expect(handleCompositeRequest(req("navigate", { url: "data:text/html,x" })).ok).toBe(false);
    expect(handleCompositeRequest(req("open_dialog", { dialog: "x" })).ok).toBe(false);
  });
});

describe("panel intents → lkap.agent.action (session-room's perform)", () => {
  it("keeps ui_action's v1 shape and sends block actions as themselves", () => {
    expect(agentActionFor({ action: "ui_action", payload: { name: "open_packet" } })).toEqual({
      v: 1,
      action: "ui_action",
      payload: { name: "open_packet", data: null },
    });
    expect(agentActionFor({ action: "form_submit", payload: { block_id: "intake", values: { a: 1 } } })).toEqual({
      v: 1,
      action: "form_submit",
      payload: { block_id: "intake", values: { a: 1 } },
    });
    expect(agentActionFor({ action: "form_submit", payload: { block_id: "intake", cancelled: true } }).payload).toEqual({
      block_id: "intake",
      cancelled: true,
    });
    expect(agentActionFor({ action: "block_action", payload: { block_id: "t", name: "select", data: { row: "r1" } } })).toEqual({
      v: 1,
      action: "block_action",
      payload: { block_id: "t", name: "select", data: { row: "r1" } },
    });
  });
});
