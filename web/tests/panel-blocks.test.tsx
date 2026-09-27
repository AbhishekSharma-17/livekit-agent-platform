import * as React from "react";

import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import { RoomContext } from "@livekit/components-react";
import type { Room, TextStreamReader } from "livekit-client";

import type { AgentPublicOut, BlockSpec, CaptionSegment, PanelLayout, UiRequest } from "@/contracts/lkap-contracts";
import { emptyUiState } from "@/lib/ui-state";
import { BLOCK_COMPONENTS, Block, LAZY_BLOCK_TYPES } from "@/panels/blocks";
import { BLOCK_CATALOG, BLOCK_TYPES, blockStateOf, blockTitle, initialBlockState } from "@/panels/blocks/catalog";
import { latestSegmentFor, TOPIC_UI_CAPTIONS, useCaptionsStream } from "@/panels/composite/captions-stream";
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
    // V5-12: `markdown` joins the lazy split (it pulls in `streamdown`).
    // V5-23: `upload` joins it too (it needs `@livekit/components-react`, like `video`).
    // V5-35: `captions` joins it too, same reason (`useCaptionsStream` needs a room);
    // `transcript` follows for the same reason once its language chip reads the same hook.
    // V5-44: `activity` joins it too — its "working on" line reads the room's live
    // `agent_state` participant attribute the same way.
    expect([...LAZY_BLOCK_TYPES].sort()).toEqual([
      "activity",
      "captions",
      "document",
      "markdown",
      "table",
      "transcript",
      "upload",
      "video",
    ]);
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
      // Lazy since V5-35 (the language chip needs `useCaptionsStream`).
      async (el) => {
        expect(await within(el).findByText("There was a small kitchen fire this morning.")).toBeTruthy();
        // show_tools: the activity rows are interleaved.
        expect(el.querySelectorAll("[data-slot=block-transcript-tool]").length).toBeGreaterThan(0);
      },
    ],
    ["video", (el) => void expect(within(el).getByText("Turn on your camera to show it here.")).toBeTruthy()],
    ["custom", (el) => void expect(within(el).getByText("Show raw state")).toBeTruthy()],
    [
      "consent",
      (el) => {
        expect(within(el).getByText(/Is it okay if we record it/)).toBeTruthy();
        expect(within(el).getByRole("button", { name: "Accept" })).toBeTruthy();
        expect(within(el).getByRole("button", { name: "Decline" })).toBeTruthy();
      },
    ],
    [
      "upload",
      (el) => {
        // No `RoomContext` here (like the `video` case above): the picker
        // shows its files (worker-verified and worker-refused) and disables
        // sending, exactly as it does in the console's read-only snapshot.
        expect(within(el).getByText("Please send a photo of the damage.")).toBeTruthy();
        expect(within(el).getByText("stove.jpg")).toBeTruthy();
        expect(within(el).getByText("That type of file can't be sent here. Send a photo or a PDF.")).toBeTruthy();
        expect(within(el).getByText("Files can be sent during a call.")).toBeTruthy();
        expect(within(el).getByRole("button", { name: /Choose a file/ }).hasAttribute("disabled")).toBe(true);
      },
    ],
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
    consent: /will ask for your agreement here/,
    upload: /will ask for a file here/,
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

  it("submits the values through perform as block_submit (V5-03: the form path moved onto the generic requestable-block machinery)", async () => {
    const perform = vi.fn(async () => ({ ok: true, payload: {} }));
    render(<Block spec={spec} {...panelProps({ perform })} />);
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "priya@example.com" } });
    fireEvent.change(screen.getByLabelText("Date of loss"), { target: { value: "2026-09-21" } });
    fireEvent.change(screen.getByLabelText("Rooms affected"), { target: { value: "2" } });
    fireEvent.click(screen.getByLabelText("I rent this home"));
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(perform).toHaveBeenCalledTimes(1));
    expect(perform).toHaveBeenCalledWith({
      action: "block_submit",
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

  it("dismisses with block_submit {block_id, cancelled: true}", async () => {
    const perform = vi.fn(async () => ({ ok: true, payload: {} }));
    render(<Block spec={spec} {...panelProps({ perform })} />);
    fireEvent.click(screen.getByRole("button", { name: "Not now" }));
    await waitFor(() =>
      expect(perform).toHaveBeenCalledWith({ action: "block_submit", payload: { block_id: "intake", cancelled: true } }),
    );
  });

  it("shows a plain dismissal message once cancelled, with no Send/Not now controls", () => {
    const state = fixtureUiState({ intake: { ...BLOCK_FIXTURE_STATES.form, status: "cancelled" } });
    render(<Block spec={spec} {...panelProps({ state })} />);
    expect(screen.getByText("You dismissed this without answering.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Send" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Not now" })).toBeNull();
  });

  it("re-arms from a reconnect snapshot alone: a fresh block instance already at status=requested renders pending without any request/form RPC", () => {
    // No `handleCompositeRequest` call anywhere in this test — the snapshot
    // (`BLOCK_FIXTURE_STATES.form`, `status: "requested"`) is the only thing
    // driving the render, exactly as it is after a browser reconnect.
    const perform = vi.fn(async () => ({ ok: true, payload: {} }));
    render(<Block spec={spec} {...panelProps({ perform })} />);
    expect(screen.getByRole("form", { name: "Your details" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Send" })).toBeTruthy();
    expect(perform).not.toHaveBeenCalled();
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

describe("consent block (V5-17)", () => {
  const spec = specOf("consent");

  it("accepts through perform as block_submit {accepted: true}", async () => {
    const perform = vi.fn(async () => ({ ok: true, payload: {} }));
    render(<Block spec={spec} {...panelProps({ perform })} />);
    fireEvent.click(screen.getByRole("button", { name: "Accept" }));
    await waitFor(() =>
      expect(perform).toHaveBeenCalledWith({
        action: "block_submit",
        payload: { block_id: "recording_consent", values: { accepted: true } },
      }),
    );
  });

  it("declines through perform as block_submit {accepted: false} — decline is the answer, not a cancel", async () => {
    const perform = vi.fn(async () => ({ ok: true, payload: {} }));
    render(<Block spec={spec} {...panelProps({ perform })} />);
    fireEvent.click(screen.getByRole("button", { name: "Decline" }));
    await waitFor(() =>
      expect(perform).toHaveBeenCalledWith({
        action: "block_submit",
        payload: { block_id: "recording_consent", values: { accepted: false } },
      }),
    );
  });

  it("shows the agent's refusal and lets the caller retry", async () => {
    const perform = vi.fn(async () => ({ ok: false, payload: {}, error: "already answered" }));
    render(<Block spec={spec} {...panelProps({ perform })} />);
    fireEvent.click(screen.getByRole("button", { name: "Accept" }));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "already answered");
  });

  it("shows who agreed, how and when once submitted", () => {
    const submitted = { ...(BLOCK_FIXTURE_STATES.consent ?? {}), status: "submitted", accepted: true, method: "tap", at: 1777000000 };
    const state = fixtureUiState({ recording_consent: submitted });
    render(<Block spec={spec} {...panelProps({ state })} />);
    expect(screen.getByText("Agreed")).toBeTruthy();
    expect(screen.getByText(/by tap/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Accept" })).toBeNull();
  });

  it("shows what was declined too, not just what was agreed to", () => {
    const submitted = { ...(BLOCK_FIXTURE_STATES.consent ?? {}), status: "submitted", accepted: false, method: "voice" };
    const state = fixtureUiState({ recording_consent: submitted });
    render(<Block spec={spec} {...panelProps({ state })} />);
    expect(screen.getByText("Declined")).toBeTruthy();
    expect(screen.getByText(/by voice/)).toBeTruthy();
  });

  it("shows a plain dismissal message once cancelled, with no Accept/Decline controls", () => {
    const state = fixtureUiState({ recording_consent: { ...(BLOCK_FIXTURE_STATES.consent ?? {}), status: "cancelled" } });
    render(<Block spec={spec} {...panelProps({ state })} />);
    expect(screen.getByText("You dismissed this without answering.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Accept" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Decline" })).toBeNull();
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
  it("hides tool rows unless show_tools", async () => {
    const state = fixtureUiState({ transcript: { show_tools: false } });
    render(<Block spec={specOf("transcript")} {...panelProps({ state })} />);
    // `transcript` is lazy-loaded since V5-35 (its language chip needs `useCaptionsStream`).
    await screen.findByText(/kitchen fire/);
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

  describe("the consent banner (V5-15/V5-17)", () => {
    it("shows the fixed disclosure line, panel-level, not the fixture's recording question", async () => {
      // The fixture's consent block is `kind: "recording"` (its own text asks
      // about recording, not disclosure) with `show_banner` at its default
      // `true` — the banner is the AI disclosure, never a repeat of whatever
      // else that block is asking (`ConsentBlockConfig`'s own docstring).
      render(<CompositePanel {...panelProps()} />);
      const banner = await screen.findByRole("note");
      expect(banner.textContent).toBe("You're talking to an AI assistant.");
    });

    it("shows an ai_disclosure block's own wording instead of the fixed line", () => {
      const layout: PanelLayout = {
        panel_id: "composite",
        layout: "side",
        blocks: [{ id: "disclosure", type: "consent", title: null, config: { kind: "ai_disclosure" }, order: 0 }],
      };
      const state = {
        ...emptyUiState(),
        blocks: { disclosure: { kind: "ai_disclosure", text: "Heads up — you're speaking with an AI today." } },
      };
      render(<CompositePanel {...panelProps({ agent: agent(layout), state: state as never })} />);
      expect(screen.getByRole("note").textContent).toBe("Heads up — you're speaking with an AI today.");
    });

    it("prefers an ai_disclosure block over a recording one when a layout has both", () => {
      const layout: PanelLayout = {
        panel_id: "composite",
        layout: "side",
        blocks: [
          { id: "recording_consent", type: "consent", title: null, config: { kind: "recording" }, order: 0 },
          { id: "disclosure", type: "consent", title: null, config: { kind: "ai_disclosure" }, order: 1 },
        ],
      };
      const state = {
        ...emptyUiState(),
        blocks: {
          recording_consent: { kind: "recording", text: "Can we record this call?" },
          disclosure: { kind: "ai_disclosure", text: "Just so you know, I'm an AI." },
        },
      };
      render(<CompositePanel {...panelProps({ agent: agent(layout), state: state as never })} />);
      expect(screen.getByRole("note").textContent).toBe("Just so you know, I'm an AI.");
    });

    it("shows nothing when the only consent block opts out with show_banner: false", () => {
      const layout: PanelLayout = {
        panel_id: "composite",
        layout: "side",
        blocks: [{ id: "c1", type: "consent", title: null, config: { show_banner: false }, order: 0 }],
      };
      render(<CompositePanel {...panelProps({ agent: agent(layout), state: emptyUiState() })} />);
      expect(screen.queryByRole("note")).toBeNull();
    });

    it("shows no other source — a layout without any consent block has no banner", () => {
      const layout: PanelLayout = { panel_id: "composite", layout: "side", blocks: DEFAULT_COMPOSITE_BLOCKS as BlockSpec[] };
      render(<CompositePanel {...panelProps({ agent: agent(layout), state: emptyUiState() })} />);
      expect(screen.queryByRole("note")).toBeNull();
    });
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

/** A fake `Room` that captures the one `lkap.captions` handler `captions-stream.ts` registers (the same shape `use-byte-stream.test.tsx` uses for `registerByteStreamHandler`). */
function fakeCaptionsRoom() {
  let handler: ((reader: TextStreamReader) => void) | null = null;
  const registerTextStreamHandler = vi.fn((topic: string, cb: (reader: TextStreamReader) => void) => {
    if (topic === TOPIC_UI_CAPTIONS) handler = cb;
  });
  const unregisterTextStreamHandler = vi.fn((topic: string) => {
    if (topic === TOPIC_UI_CAPTIONS) handler = null;
  });
  const room = { registerTextStreamHandler, unregisterTextStreamHandler };
  function emit(segment: CaptionSegment) {
    const reader = { readAll: async () => JSON.stringify(segment) } as unknown as TextStreamReader;
    handler?.(reader);
  }
  return { room: room as unknown as Room, emit, registerTextStreamHandler, unregisterTextStreamHandler };
}

function segment(overrides: Partial<CaptionSegment> = {}): CaptionSegment {
  return { v: 1, id: "u1", speaker: "user", text: "Hello", final: false, language: null, ts: 1, ...overrides };
}

describe("useCaptionsStream (V5-35)", () => {
  it("registers lkap.captions once on mount and unregisters on unmount", () => {
    const { room, registerTextStreamHandler, unregisterTextStreamHandler } = fakeCaptionsRoom();
    const { unmount } = renderHook(() => useCaptionsStream({ room }));
    expect(registerTextStreamHandler).toHaveBeenCalledTimes(1);
    expect(registerTextStreamHandler.mock.calls[0]?.[0]).toBe(TOPIC_UI_CAPTIONS);
    unmount();
    expect(unregisterTextStreamHandler).toHaveBeenCalledWith(TOPIC_UI_CAPTIONS);
  });

  it("registers only once for two mounted readers of the same room (only one handler per topic per room)", async () => {
    const { room, emit, registerTextStreamHandler } = fakeCaptionsRoom();
    const first = renderHook(() => useCaptionsStream({ room }));
    renderHook(() => useCaptionsStream({ room }));
    expect(registerTextStreamHandler).toHaveBeenCalledTimes(1);

    act(() => emit(segment({ text: "Hi" })));
    await waitFor(() => expect(first.result.current).toHaveLength(1));
  });

  it("replaces an interim segment with its final one in place, by id", async () => {
    const { room, emit } = fakeCaptionsRoom();
    const { result } = renderHook(() => useCaptionsStream({ room }));

    act(() => emit(segment({ id: "u1", text: "Hel", final: false })));
    await waitFor(() => expect(result.current).toHaveLength(1));
    act(() => emit(segment({ id: "u1", text: "Hello there", final: true })));
    await waitFor(() => expect(result.current[0]?.text).toBe("Hello there"));
    expect(result.current).toHaveLength(1);
    expect(result.current[0]?.final).toBe(true);
  });

  it("keeps both sides as separate segments and latestSegmentFor reads the right one", async () => {
    const { room, emit } = fakeCaptionsRoom();
    const { result } = renderHook(() => useCaptionsStream({ room }));

    act(() => emit(segment({ id: "u1", speaker: "user", text: "Hi" })));
    act(() => emit(segment({ id: "a1", speaker: "agent", text: "Hello!" })));
    await waitFor(() => expect(result.current).toHaveLength(2));

    expect(latestSegmentFor(result.current, "user")?.text).toBe("Hi");
    expect(latestSegmentFor(result.current, "agent")?.text).toBe("Hello!");
    expect(latestSegmentFor(result.current, "user")).not.toBe(null);
    expect(latestSegmentFor([], "user")).toBeNull();
  });

  it("does nothing without a room", () => {
    expect(() => renderHook(() => useCaptionsStream())).not.toThrow();
  });
});

describe("captions block (V5-35)", () => {
  function captionsSpec(config: Record<string, unknown> = {}): BlockSpec {
    return { id: "live_captions", type: "captions", title: null, config, order: 0 };
  }

  it("shows an empty state with no captions yet (and no room, in the console preview)", async () => {
    render(<Block spec={captionsSpec()} {...panelProps()} />);
    expect(await screen.findByText("Captions appear here once someone speaks.")).toBeTruthy();
  });

  it("shows the current utterance per side, and a language chip when it differs from the conversation's language", async () => {
    const { room, emit } = fakeCaptionsRoom();
    const state = fixtureUiState({ live_captions: { language: "en", target_language: null } });
    render(
      <RoomContext.Provider value={room}>
        <Block spec={captionsSpec()} {...panelProps({ state })} />
      </RoomContext.Provider>,
    );
    await screen.findByText("Captions appear here once someone speaks.");

    act(() => emit(segment({ id: "u1", speaker: "user", text: "¿Qué tal?", language: "es", final: true })));
    expect(await screen.findByText("¿Qué tal?")).toBeTruthy();
    expect(screen.getByText("Spanish")).toBeTruthy();

    act(() => emit(segment({ id: "a1", speaker: "agent", text: "All good", language: "en", final: true })));
    await screen.findByText("All good");
    // The agent's line is in English, the conversation's own language — no chip on it.
    expect(screen.queryByText("English")).toBeNull();
  });

  it("hides a side whose show_user/show_agent is off", async () => {
    const { room, emit } = fakeCaptionsRoom();
    render(
      <RoomContext.Provider value={room}>
        <Block spec={captionsSpec({ show_user: false })} {...panelProps()} />
      </RoomContext.Provider>,
    );
    act(() => emit(segment({ id: "u1", speaker: "user", text: "Hidden" })));
    await waitFor(() => expect(screen.queryByText("Captions appear here once someone speaks.")).toBeTruthy());
    expect(screen.queryByText("Hidden")).toBeNull();
  });
});
