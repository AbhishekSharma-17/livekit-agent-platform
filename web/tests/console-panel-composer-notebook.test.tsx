import * as React from "react";

import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { FormProvider, useForm, useFormContext } from "react-hook-form";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { buildAgentUpdate, toFormValues } from "@/components/console/agents/editor/form-values";
import { PanelComposer } from "@/components/console/agents/panel-section/panel-composer";
import { agentEditorFormSchema, type AgentEditorForm } from "@/components/console/lib/schemas";
import { zodResolver } from "@/components/console/lib/zod-resolver";
import type { AgentOut, PanelPreset } from "@/contracts/lkap-contracts";

/**
 * V6-10 (D-V6-15, D-V6-18, ask #56): the composer's `notebook.sections` and
 * `layout.children` editors (`block-config-form.tsx`), and "Start from the
 * Notebook preset" (`GET /v1/panels/presets`, `panel-composer.tsx`).
 * `console-panel-composer.test.tsx` (V2-11) covers everything else the
 * composer does.
 */
const nativeMatches = Element.prototype.matches;
beforeAll(() => {
  Element.prototype.matches = function matches(this: Element, selector: string) {
    if (selector === ":popover-open" || selector === ":modal") return false;
    return nativeMatches.call(this, selector);
  };
  Element.prototype.scrollIntoView = function scrollIntoView() {};
});
afterAll(() => {
  Element.prototype.matches = nativeMatches;
});

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
beforeEach(() => vi.stubGlobal("ResizeObserver", ResizeObserverStub));
afterEach(() => vi.unstubAllGlobals());

function jsonResponse(body: unknown, status = 200): Response {
  return { ok: status < 400, status, json: async () => body } as Response;
}

const NOTEBOOK_PRESET: PanelPreset = {
  id: "notebook",
  name: "Notebook",
  description: "A wide notebook the agent writes in as the call goes.",
  panel: {
    panel_id: "composite",
    layout: "wide",
    blocks: [
      { id: "status", type: "status", title: null, config: {}, order: 0 },
      {
        id: "notebook",
        type: "notebook",
        title: "Notebook",
        config: { paper: "ruled", font: "handwritten", sections: [{ id: "notes", title: "Notes", kind: "text" }], caller_can_write: true },
        order: 1,
      },
      { id: "gallery", type: "gallery", title: "Pictures", config: {}, order: 2 },
    ],
  },
};

function stubFetch(presets: PanelPreset[] = [NOTEBOOK_PRESET]) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/panels/presets")) return jsonResponse({ items: presets });
      if (url.includes("/providers")) return jsonResponse({ providers: [] });
      if (url.includes("/packs")) return jsonResponse({ items: [] });
      return jsonResponse({});
    }),
  );
}

function agent(overrides: Partial<AgentOut> = {}): AgentOut {
  return {
    id: "a-1",
    slug: "claims",
    name: "Claims",
    description: "",
    pack_id: "generic",
    ui_panel_id: "composite",
    published: false,
    config_version: 1,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    config: {
      instructions: "Hi",
      pipeline: { mode: "cascaded" },
      panel: {
        panel_id: "composite",
        layout: "side",
        blocks: [
          { id: "status", type: "status", title: null, config: {}, order: 0 },
          { id: "notebook", type: "notebook", title: "Notebook", config: {}, order: 1 },
          { id: "recap", type: "markdown", title: "Recap", config: {}, order: 2 },
          { id: "layout1", type: "layout", title: "Tabs", config: { children: [] }, order: 3 },
        ],
      },
    },
    ...overrides,
  } as AgentOut;
}

let latest: AgentEditorForm | null = null;
function Spy() {
  const { watch } = useFormContext<AgentEditorForm>();
  latest = watch();
  return null;
}

function Harness({ agent: theAgent }: { agent: AgentOut }) {
  const client = React.useMemo(() => new QueryClient({ defaultOptions: { queries: { retry: false } } }), []);
  const form = useForm<AgentEditorForm>({
    resolver: zodResolver(agentEditorFormSchema),
    defaultValues: toFormValues(theAgent),
    mode: "onChange",
  });
  return (
    <QueryClientProvider client={client}>
      <FormProvider {...form}>
        <form>
          <PanelComposer agent={theAgent} />
          <Spy />
        </form>
      </FormProvider>
    </QueryClientProvider>
  );
}

describe("BlockConfigForm — notebook sections editor (V6-10)", () => {
  it("adds a section with a kind restricted to the four real kinds", async () => {
    stubFetch([]);
    render(<Harness agent={agent()} />);
    fireEvent.click(screen.getByRole("button", { name: /^Notebook/ }));
    fireEvent.click(screen.getByRole("button", { name: "Add section" }));
    const rows = within(screen.getByTestId("panel-composer")).getAllByLabelText(/Section \d+ id/);
    expect(rows).toHaveLength(1);
    fireEvent.change(rows[0], { target: { value: "still_needed" } });
    fireEvent.click(screen.getByRole("combobox", { name: /Section 1 kind/ }));
    fireEvent.click(await screen.findByRole("option", { name: "Checklist" }));
    expect(latest?.config.panel.blocks.find((b) => b.type === "notebook")?.config.sections).toEqual([
      { id: "still_needed", title: "", kind: "checklist" },
    ]);
  });

  it("caller_can_draw is a real switch once V6-12's canvas exists (ask #93)", () => {
    stubFetch([]);
    render(<Harness agent={agent()} />);
    fireEvent.click(screen.getByRole("button", { name: /^Notebook/ }));
    const drawSwitch = screen.getByRole("switch", { name: "The caller can draw" });
    expect(drawSwitch.hasAttribute("disabled")).toBe(false);
    fireEvent.click(drawSwitch);
    expect(latest?.config.panel.blocks.find((b) => b.type === "notebook")?.config.caller_can_draw).toBe(true);
  });

  it("an ink section's board picker offers only canvas blocks of this panel", async () => {
    stubFetch([]);
    const withCanvas = agent({
      config: {
        instructions: "Hi",
        pipeline: { mode: "cascaded" },
        panel: {
          panel_id: "composite",
          layout: "side",
          blocks: [
            { id: "notebook", type: "notebook", title: "Notebook", config: { sections: [{ id: "sketch", title: "Sketch", kind: "ink" }] }, order: 0 },
            { id: "board", type: "canvas", title: "Board", config: {}, order: 1 },
          ],
        },
      },
    });
    render(<Harness agent={withCanvas} />);
    fireEvent.click(screen.getByRole("button", { name: /^Notebook/ }));
    fireEvent.click(screen.getByRole("combobox", { name: /Section 1 board/ }));
    fireEvent.click(await screen.findByRole("option", { name: /Board · board/ }));
    expect(latest?.config.panel.blocks.find((b) => b.type === "notebook")?.config.sections).toEqual([
      { id: "sketch", title: "Sketch", kind: "ink", canvas_block_id: "board" },
    ]);
  });
});

describe("BlockConfigForm — layout children editor (V6-10, D-V6-18)", () => {
  it("picks another block of the panel, never itself and never another layout", async () => {
    stubFetch([]);
    render(<Harness agent={agent()} />);
    fireEvent.click(screen.getByRole("button", { name: /^Tabs/ }));
    fireEvent.click(screen.getByRole("button", { name: "Add a block" }));
    // Only "status", "notebook" and "recap" are offered — never "layout1" itself.
    expect(latest?.config.panel.blocks.find((b) => b.id === "layout1")?.config.children).toEqual([
      { block_id: "status", label: null },
    ]);
    fireEvent.click(screen.getByRole("combobox", { name: "Block 1" }));
    const options = await screen.findAllByRole("option");
    expect(options.map((o) => o.textContent)).not.toContain(expect.stringContaining("Tabs"));
  });

  it("gives the child an optional tab label", async () => {
    stubFetch([]);
    render(<Harness agent={agent()} />);
    fireEvent.click(screen.getByRole("button", { name: /^Tabs/ }));
    fireEvent.click(screen.getByRole("button", { name: "Add a block" }));
    fireEvent.change(screen.getByLabelText("Block 1 tab label"), { target: { value: "Overview" } });
    expect(latest?.config.panel.blocks.find((b) => b.id === "layout1")?.config.children).toEqual([
      { block_id: "status", label: "Overview" },
    ]);
  });
});

describe("panel presets (ask #56)", () => {
  it("lists the Notebook preset and replaces the blocks (with confirmation, since blocks already exist)", async () => {
    stubFetch([NOTEBOOK_PRESET]);
    render(<Harness agent={agent()} />);
    const button = await screen.findByRole("button", { name: "Start from the Notebook preset" });
    fireEvent.click(button);
    // The panel already has blocks: a confirm dialog appears first.
    expect(screen.getByRole("dialog", { name: "Replace the current blocks?" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Replace blocks" }));
    expect(latest?.config.panel.blocks.map((b) => b.id)).toEqual(["status", "notebook", "gallery"]);
    expect(latest?.config.panel.layout).toBe("wide");
  });

  it("replaces at once (no confirmation) when the panel has no blocks yet", async () => {
    stubFetch([NOTEBOOK_PRESET]);
    const blank = agent({
      config: { instructions: "Hi", pipeline: { mode: "cascaded" }, panel: { panel_id: "composite", layout: "side", blocks: [] } },
    });
    render(<Harness agent={blank} />);
    const button = await screen.findByRole("button", { name: "Start from the Notebook preset" });
    fireEvent.click(button);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(latest?.config.panel.blocks.map((b) => b.id)).toEqual(["status", "notebook", "gallery"]);
  });

  it("hides the control entirely when the api has no panels/presets route (404 -> empty list)", async () => {
    stubFetch([]);
    render(<Harness agent={agent()} />);
    await waitFor(() => expect(screen.getByTestId("panel-composer")).toBeTruthy());
    expect(screen.queryByRole("button", { name: /Start from the/ })).toBeNull();
  });
});

describe("buildAgentUpdate carries notebook/layout configs unchanged", () => {
  it("round-trips a notebook block's config through the form", () => {
    const a = agent();
    const form = toFormValues(a);
    const update = buildAgentUpdate(a, form);
    const notebook = update.config?.panel?.blocks?.find((b) => b.id === "notebook");
    expect(notebook?.type).toBe("notebook");
  });
});
