import * as React from "react";

import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import type { AgentPublicOut, BlockSpec, NotebookBlockState } from "@/contracts/lkap-contracts";
import { Block } from "@/panels/blocks";
import { initialBlockState } from "@/panels/blocks/catalog";
import { CompositePanel } from "@/panels/composite";
import { handleCompositeRequest } from "@/panels/composite/requests";
import {
  FIXTURE_LAYOUT,
  fixtureAssetUrls,
  fixtureUiState,
} from "@/panels/blocks/__fixtures__";
import notebookFixture from "@/panels/blocks/__fixtures__/notebook.json";
import { SafeMarkdown } from "@/lib/safe-markdown";
import type { PanelProps } from "@/panels/registry";

/**
 * V6-10 (D-V6-15, D-V6-18): the `notebook` and `layout` renderers, caller
 * edits on `notebook` / `checklist` / `details`, per-block margin notes, and
 * the shared `safe-markdown` helper (ask #333). `panel-blocks.test.tsx`
 * (V2-11) and `panel-wave10.test.tsx` cover everything else these two
 * packages did not touch.
 */

beforeAll(() => {
  Element.prototype.scrollIntoView = function scrollIntoView() {};
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function agent(panel?: AgentPublicOut["panel"]): AgentPublicOut {
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

function specWith(type: BlockSpec["type"], config: Record<string, unknown>): BlockSpec {
  const spec = specOf(type);
  return { ...spec, config: { ...(spec.config as Record<string, unknown>), ...config } };
}

/* -------------------------------------------------------------------------- */
/* notebook renderer                                                          */
/* -------------------------------------------------------------------------- */

describe("notebook block", () => {
  const SPEC = specOf("notebook");

  it("renders every section kind in config order, from the fixture state", async () => {
    render(<Block spec={SPEC} {...panelProps()} />);
    await waitFor(() => expect(screen.getByTestId("block-notebook").getAttribute("data-loading")).toBeNull());
    const el = screen.getByTestId("block-notebook");
    // text
    expect(within(el).getByText(/Small kitchen fire this morning/)).toBeTruthy();
    // checklist
    expect(within(el).getByText("A photo of the stove")).toBeTruthy();
    expect(within(el).getByText("A repair quote")).toBeTruthy();
    // details
    expect(within(el).getByText("CLM-20931")).toBeTruthy();
    // ink placeholder (V6-12)
    expect(within(el).getByText("Drawing board coming soon.")).toBeTruthy();
  });

  it("a caller-authored entry shows 'written by you'; a caller-edited one shows 'changed by you'", async () => {
    const state = fixtureUiState({
      notebook: {
        ...notebookFixture,
        sections: {
          ...notebookFixture.sections,
          notes: {
            kind: "text",
            entries: [
              { id: "n1", text: "Agent wrote this.", author: "agent", ts: 1, edited_by: "caller" },
              { id: "n2", text: "Caller wrote this.", author: "caller", ts: 2 },
            ],
          },
        },
      },
    });
    render(<Block spec={SPEC} {...panelProps({ state })} />);
    expect(await screen.findByText(/changed by you/)).toBeTruthy();
    expect(screen.getByText(/written by you/)).toBeTruthy();
  });

  it("a section missing from state (or with the wrong kind) renders its own empty content, not a crash", () => {
    const state = fixtureUiState({ notebook: { sections: { notes: { kind: "checklist", items: [] } } } });
    expect(() => render(<Block spec={SPEC} {...panelProps({ state })} />)).not.toThrow();
    expect(screen.getByText("Nothing written here yet.")).toBeTruthy();
  });

  it("caller_can_write: adding a note sends block_action edit {section_id, text}", async () => {
    const perform = vi.fn(async () => ({ ok: true, payload: {} }));
    render(<Block spec={SPEC} {...panelProps({ perform })} />);
    const input = await screen.findByLabelText("Add a note");
    fireEvent.change(input, { target: { value: "Caller's own note" } });
    fireEvent.click(screen.getByRole("button", { name: /Add/ }));
    await waitFor(() =>
      expect(perform).toHaveBeenCalledWith({
        action: "block_action",
        payload: { block_id: "notebook", name: "edit", data: { section_id: "notes", text: "Caller's own note" } },
      }),
    );
  });

  it("editing an existing entry sends {section_id, entry_id, text}", async () => {
    const perform = vi.fn(async () => ({ ok: true, payload: {} }));
    render(<Block spec={SPEC} {...panelProps({ perform })} />);
    const editButtons = await screen.findAllByRole("button", { name: "Edit this note" });
    fireEvent.click(editButtons[0]);
    const field = screen.getByLabelText("Edit note text");
    fireEvent.change(field, { target: { value: "Updated text" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(perform).toHaveBeenCalledWith({
        action: "block_action",
        payload: { block_id: "notebook", name: "edit", data: { section_id: "notes", entry_id: "n1", text: "Updated text" } },
      }),
    );
  });

  it("removing an entry sends {section_id, entry_id, text: ''}", async () => {
    const perform = vi.fn(async () => ({ ok: true, payload: {} }));
    render(<Block spec={SPEC} {...panelProps({ perform })} />);
    const removeButtons = await screen.findAllByRole("button", { name: "Remove this note" });
    fireEvent.click(removeButtons[0]);
    await waitFor(() =>
      expect(perform).toHaveBeenCalledWith({
        action: "block_action",
        payload: { block_id: "notebook", name: "edit", data: { section_id: "notes", entry_id: "n1", text: "" } },
      }),
    );
  });

  it("ticking a checklist item sends {section_id, item_id, done}", async () => {
    const perform = vi.fn(async () => ({ ok: true, payload: {} }));
    render(<Block spec={SPEC} {...panelProps({ perform })} />);
    const checkbox = await screen.findByRole("checkbox", { name: "A repair quote" });
    fireEvent.click(checkbox);
    await waitFor(() =>
      expect(perform).toHaveBeenCalledWith({
        action: "block_action",
        payload: { block_id: "notebook", name: "edit", data: { section_id: "still_needed", item_id: "repair_quote", done: true } },
      }),
    );
  });

  it("changing a details row sends {section_id, key, value}", async () => {
    const perform = vi.fn(async () => ({ ok: true, payload: {} }));
    render(<Block spec={SPEC} {...panelProps({ perform })} />);
    const editButtons = await screen.findAllByRole("button", { name: /Edit Claim number/ });
    fireEvent.click(editButtons[0]);
    const field = screen.getByLabelText("Claim number value");
    fireEvent.change(field, { target: { value: "CLM-99999" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(perform).toHaveBeenCalledWith({
        action: "block_action",
        payload: { block_id: "notebook", name: "edit", data: { section_id: "summary", key: "claim_no", value: "CLM-99999" } },
      }),
    );
  });

  it("ok:false keeps the old value and shows the refusal in plain words", async () => {
    const perform = vi.fn(async () => ({ ok: false, error: "that note is too old to change" }));
    render(<Block spec={SPEC} {...panelProps({ perform })} />);
    const input = await screen.findByLabelText("Add a note");
    fireEvent.change(input, { target: { value: "Trying to add" } });
    fireEvent.click(screen.getByRole("button", { name: /Add/ }));
    expect((await screen.findByRole("alert")).textContent).toContain("that note is too old to change");
    // The failed draft is not cleared (the caller can retry or edit it).
    expect((screen.getByLabelText("Add a note") as HTMLInputElement).value).toBe("Trying to add");
  });

  it("a no-op edit (payload.changed: false) is treated as success with nothing to show", async () => {
    const perform = vi.fn(async () => ({ ok: true, payload: { changed: false } }));
    render(<Block spec={SPEC} {...panelProps({ perform })} />);
    const input = await screen.findByLabelText("Add a note");
    fireEvent.change(input, { target: { value: "Same as before" } });
    fireEvent.click(screen.getByRole("button", { name: /Add/ }));
    await waitFor(() => expect(perform).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("refuses a note longer than MAX_CALLER_EDIT_CHARS client-side (maxLength on the input)", async () => {
    render(<Block spec={SPEC} {...panelProps()} />);
    const input = (await screen.findByLabelText("Add a note")) as HTMLInputElement;
    expect(input.maxLength).toBe(500);
  });

  it("caller_can_write: false renders no add-note form and read-only checklist items", async () => {
    const spec = specWith("notebook", { caller_can_write: false });
    render(<Block spec={spec} {...panelProps()} />);
    await screen.findByText(/Small kitchen fire/);
    expect(screen.queryByLabelText("Add a note")).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it("caller_can_draw greys the drawing board note", () => {
    const spec = specWith("notebook", { caller_can_draw: true });
    render(<Block spec={spec} {...panelProps()} />);
    expect(screen.getByText("Drawing is not available yet.")).toBeTruthy();
  });
});

/* -------------------------------------------------------------------------- */
/* catalog: notebook's pre-call state (ask #60)                               */
/* -------------------------------------------------------------------------- */

describe("notebook initialBlockState (ask #60)", () => {
  it("mirrors the worker's empty_notebook_sections: one empty section per config entry, by kind", () => {
    const state = initialBlockState({
      id: "nb",
      type: "notebook",
      config: {
        sections: [
          { id: "a", kind: "text" },
          { id: "b", kind: "checklist" },
          { id: "c", kind: "details" },
          { id: "d", kind: "ink" },
        ],
      },
    }) as NotebookBlockState;
    expect(state).toEqual({
      sections: {
        a: { kind: "text", entries: [] },
        b: { kind: "checklist", items: [] },
        c: { kind: "details", items: [] },
        d: { kind: "ink", canvas_block_id: null },
      },
      updated_at: null,
    });
  });

  it("defaults one 'notes' text section when the config carries none", () => {
    const state = initialBlockState({ id: "nb", type: "notebook", config: {} }) as NotebookBlockState;
    expect(state).toEqual({ sections: { notes: { kind: "text", entries: [] } }, updated_at: null });
  });
});

/* -------------------------------------------------------------------------- */
/* layout renderer                                                            */
/* -------------------------------------------------------------------------- */

describe("layout block", () => {
  it("tabs: renders the active tab's child inside it, and switches on click", async () => {
    render(<Block spec={specOf("layout")} {...panelProps()} />);
    // "claim" (the details block) is the first, active tab.
    expect(await screen.findByText("H0-44721")).toBeTruthy();
    // Radix's Tabs.Trigger selects on mousedown (and on focus, in automatic mode).
    fireEvent.mouseDown(screen.getByRole("tab", { name: "Recap" }));
    await waitFor(() => expect(screen.getByRole("tab", { name: "Recap" }).getAttribute("data-state")).toBe("active"));
  });

  it("skips a child block_id that is not on the panel, and never renders a nested layout", async () => {
    const spec: BlockSpec = {
      id: "tabs2",
      type: "layout",
      title: null,
      config: {
        kind: "tabs",
        children: [
          { block_id: "does-not-exist" },
          { block_id: "tabs" }, // another layout: never allowed inside a layout
          { block_id: "claim", label: "Claim" },
        ],
      },
      order: 0,
    };
    render(<Block spec={spec} {...panelProps()} />);
    // Only "claim" resolves: one tab, not three.
    expect(await screen.findAllByRole("tab")).toHaveLength(1);
    expect(screen.getByRole("tab", { name: "Claim" })).toBeTruthy();
  });

  it("holds no blocks yet: the empty state", async () => {
    const spec: BlockSpec = { id: "empty-layout", type: "layout", title: null, config: { children: [] }, order: 0 };
    render(<Block spec={spec} {...panelProps()} />);
    expect(await screen.findByText("This holds no blocks yet.")).toBeTruthy();
  });

  it("columns: renders every child at once (no tabs), stacked at narrow widths (grid-cols-1)", async () => {
    const spec: BlockSpec = {
      id: "cols",
      type: "layout",
      title: null,
      config: { kind: "columns", columns: 2, children: [{ block_id: "claim" }, { block_id: "recap" }] },
      order: 0,
    };
    render(<Block spec={spec} {...panelProps()} />);
    expect(await screen.findByText("H0-44721")).toBeTruthy();
    const columns = document.querySelector('[data-slot="layout-columns"]');
    expect(columns?.className).toContain("grid-cols-1");
  });
});

describe("composite panel + layout (D-V6-18)", () => {
  it("a layout's claimed children are never shown at the top level too (no duplicate blocks)", async () => {
    render(<CompositePanel {...panelProps()} />);
    await screen.findByText("Induction hob", {}, { timeout: 5000 });
    // "claim" is claimed by the "tabs" layout (itself lazy, like "notebook"): wait
    // for its content before asserting it renders exactly once (inside the layout).
    await screen.findByText("H0-44721", {}, { timeout: 5000 });
    expect(document.querySelectorAll('[data-block-id="claim"]')).toHaveLength(1);
    expect(document.querySelector('[data-block-id="claim"]')?.closest('[data-block-id="tabs"]')).toBeTruthy();
  });

  it("show_block highlighting reaches a claimed child through the layout (not just top-level blocks)", async () => {
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    render(<CompositePanel {...panelProps()} />);
    await screen.findByText("Induction hob", {}, { timeout: 5000 });
    // "claim" is the tabs layout's active (first) tab: wait for the lazy layout
    // to mount it before show_block looks for its DOM node.
    await screen.findByText("H0-44721", {}, { timeout: 5000 });
    expect(handleCompositeRequest({ v: 1, method: "show_block", payload: { block_id: "claim" } })).toEqual({
      ok: true,
      payload: {},
    });
    await waitFor(() => expect(screen.getByTestId("block-details").getAttribute("data-highlighted")).toBe("true"));
  });
});

/* -------------------------------------------------------------------------- */
/* checklist / details caller edits (V6-06, ask #24)                          */
/* -------------------------------------------------------------------------- */

describe("checklist caller edits", () => {
  it("caller_can_edit renders real checkboxes and sends block_action edit {item_id, done}", async () => {
    const perform = vi.fn(async () => ({ ok: true, payload: {} }));
    const spec = specWith("checklist", { caller_can_edit: true });
    render(<Block spec={spec} {...panelProps({ perform })} />);
    const box = screen.getByRole("checkbox", { name: "Service address" });
    fireEvent.click(box);
    await waitFor(() =>
      expect(perform).toHaveBeenCalledWith({
        action: "block_action",
        payload: { block_id: "checklist", name: "edit", data: { item_id: "c:address", done: false } },
      }),
    );
  });

  it("caller_can_edit: false stays read-only (no checkbox role)", () => {
    render(<Block spec={specOf("checklist")} {...panelProps()} />);
    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it("a refused edit shows the plain-words error under the item", async () => {
    const perform = vi.fn(async () => ({ ok: false, error: "this item can no longer be changed" }));
    const spec = specWith("checklist", { caller_can_edit: true });
    render(<Block spec={spec} {...panelProps({ perform })} />);
    fireEvent.click(screen.getByRole("checkbox", { name: "Service address" }));
    expect((await screen.findByRole("alert")).textContent).toContain("this item can no longer be changed");
  });
});

describe("details caller edits", () => {
  it("caller_can_edit shows an Edit control that sends block_action edit {key, value}", async () => {
    const perform = vi.fn(async () => ({ ok: true, payload: {} }));
    const spec = specWith("details", { caller_can_edit: true });
    render(<Block spec={spec} {...panelProps({ perform })} />);
    fireEvent.click(screen.getByRole("button", { name: /Edit Claim number/ }));
    fireEvent.change(screen.getByLabelText("Claim number value"), { target: { value: "CLM-11111" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(perform).toHaveBeenCalledWith({
        action: "block_action",
        payload: { block_id: "claim", name: "edit", data: { key: "claim_no", value: "CLM-11111" } },
      }),
    );
  });

  it("caller_can_edit: false (the default) shows no Edit control", () => {
    render(<Block spec={specOf("details")} {...panelProps()} />);
    expect(screen.queryByRole("button", { name: /Edit Claim number/ })).toBeNull();
  });
});

/* -------------------------------------------------------------------------- */
/* margin notes (Note.block_id, ask #24)                                      */
/* -------------------------------------------------------------------------- */

describe("margin notes (Note.block_id)", () => {
  it("a note pinned to a block shows in that block's margin and is left out of the notes list", () => {
    const base = fixtureUiState();
    const state = {
      ...base,
      notes: [...base.notes, { id: "note:pinned", text: "Pinned to checklist", ts: 1, block_id: "checklist" }],
    };
    const checklist = render(<Block spec={specOf("checklist")} {...panelProps({ state })} />);
    expect(within(checklist.container).getByText("Pinned to checklist")).toBeTruthy();
    checklist.unmount();
    const notes = render(<Block spec={specOf("notes")} {...panelProps({ state })} />);
    expect(within(notes.container).queryByText("Pinned to checklist")).toBeNull();
  });

  it("a note whose block_id names no block on the panel still shows in the notes list (nothing vanishes)", () => {
    const base = fixtureUiState();
    const state = {
      ...base,
      notes: [...base.notes, { id: "note:stale", text: "Stale pin", ts: 1, block_id: "no-such-block" }],
    };
    render(<Block spec={specOf("notes")} {...panelProps({ state })} />);
    expect(screen.getByText("Stale pin")).toBeTruthy();
  });
});

/* -------------------------------------------------------------------------- */
/* safe-markdown (ask #333)                                                    */
/* -------------------------------------------------------------------------- */

describe("SafeMarkdown", () => {
  it("never renders a javascript: link, even with allowLinks", () => {
    render(<SafeMarkdown text="[click me](javascript:alert(1))" allowLinks />);
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByText("click me")).toBeTruthy();
  });

  it("renders an https: link only when allowLinks is set", () => {
    const off = render(<SafeMarkdown text="[go](https://example.com)" />);
    expect(within(off.container).queryByRole("link")).toBeNull();
    off.unmount();
    // A fresh mount (not `rerender`): Streamdown memoizes a parsed block by its
    // text alone, so re-rendering identical text with new `components` would
    // not prove anything about this component's own logic either way.
    const on = render(<SafeMarkdown text="[go](https://example.com)" allowLinks />);
    const link = within(on.container).getByRole("link", { name: "go" });
    expect(link.getAttribute("href")).toBe("https://example.com");
    expect(link.getAttribute("target")).toBe("_blank");
  });

  it("never renders an image whose src is a third-party URL, only a resolved asset id", () => {
    const assets = new Map([["asset-1", "blob:resolved"]]);
    render(<SafeMarkdown text={`![evil](https://evil.com/x.png)\n\n![ok](asset-1)`} assets={assets} />);
    expect(screen.queryByAltText("evil")).toBeNull();
    expect((screen.getByAltText("ok") as HTMLImageElement).src).toContain("blob:resolved");
  });

  it("strips raw HTML nodes rather than rendering or interpreting them", () => {
    render(<SafeMarkdown text={'before <script>alert(1)</script> after'} />);
    expect(document.querySelector("script")).toBeNull();
  });
});
