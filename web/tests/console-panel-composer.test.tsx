import * as React from "react";

import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { FormProvider, useForm, useFormContext } from "react-hook-form";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { buildAgentUpdate, toFormValues } from "@/components/console/agents/editor/form-values";
import {
  addBlock,
  blockToolStatus,
  moveBlock,
  nextBlockId,
  removeBlock,
  setBlockConfig,
  switchPanel,
} from "@/components/console/agents/panel-section/composer-model";
import { panelComposerExtension } from "@/components/console/agents/panel-section/extension";
import { PanelComposer, panelChoices } from "@/components/console/agents/panel-section/panel-composer";
import { PanelTab } from "@/components/console/agents/tabs/panel-tab";
import { EDITOR_EXTENSIONS } from "@/components/console/agents/editor/extensions";
import { agentEditorFormSchema, type AgentEditorForm, type PanelLayoutForm } from "@/components/console/lib/schemas";
import { zodResolver } from "@/components/console/lib/zod-resolver";
import type { AgentOut, ProvidersResponse } from "@/contracts/lkap-contracts";
import { BLOCK_CATALOG } from "@/panels/blocks/catalog";

/**
 * V2-11 — the panel composer (the agent editor's "Panel & capabilities"
 * section): pure layout edits, the form contract (`config.panel` +
 * `ui_panel_id`), block tools (#69), and the session capabilities it took
 * over from WP-5's read-only placeholder.
 *
 * jsdom: Radix selects need the `:popover-open` / `:modal` override from
 * `console-editor-shell.test.tsx`, plus `ResizeObserver` / `scrollIntoView`.
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

const TEXT_ONLY_PROVIDERS: ProvidersResponse = {
  providers: [
    {
      id: "openai-llm",
      kind: "llm",
      label: "OpenAI",
      package: "x",
      python_class: "x.OpenAiLlm",
      vendor: "openai",
      default_model: "gpt-text",
      models: [{ id: "gpt-text", label: "GPT text-only" }],
    },
  ],
};

function stubFetch(providers: ProvidersResponse = { providers: [] }) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/providers")) return jsonResponse(providers);
      if (url.includes("/packs")) {
        return jsonResponse({
          items: [
            {
              manifest: {
                id: "insurance_claim",
                ui_panel_id: "insurance_notebook",
                blocks: [{ id: "claim_docs", type: "document", title: "Claim documents", config: {}, order: 0 }],
              },
            },
          ],
        });
      }
      return jsonResponse({});
    }),
  );
}

const FOUR = [
  { id: "status", type: "status", title: null, config: {}, order: 0 },
  { id: "notes", type: "notes", title: "Notes", config: {}, order: 1 },
  { id: "checklist", type: "checklist", title: "Still needed", config: {}, order: 2 },
  { id: "activity", type: "activity", title: "Activity", config: {}, order: 3 },
] as const;

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
      panel: { panel_id: "composite", layout: "side", blocks: FOUR.map((b) => ({ ...b })) },
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

function Harness({ agent: theAgent, Section = PanelComposer }: { agent: AgentOut; Section?: typeof PanelComposer }) {
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
          <Section agent={theAgent} />
          <Spy />
        </form>
      </FormProvider>
    </QueryClientProvider>
  );
}

function panel(blocks: PanelLayoutForm["blocks"] = FOUR.map((b) => ({ ...b }))): PanelLayoutForm {
  return { panel_id: "composite", layout: "side", blocks };
}

describe("composer model", () => {
  it("adds a block with the next free id and keeps order = position", () => {
    let p = addBlock(panel(), "table");
    expect(p.blocks.at(-1)).toEqual({ id: "table", type: "table", title: null, config: {}, order: 4 });
    p = addBlock(p, "table");
    expect(p.blocks.at(-1)?.id).toBe("table_2");
    expect(nextBlockId(p.blocks, "table")).toBe("table_3");
    expect(addBlock(panel([]), "video").blocks[0].config).toEqual({ source: "agent_avatar" });
  });

  it("moves, clamps and removes, renumbering orders", () => {
    const moved = moveBlock(panel(), 3, 0);
    expect(moved.blocks.map((b) => b.id)).toEqual(["activity", "status", "notes", "checklist"]);
    expect(moved.blocks.map((b) => b.order)).toEqual([0, 1, 2, 3]);
    expect(moveBlock(panel(), 0, -5)).toEqual(panel());
    expect(moveBlock(panel(), 1, 99).blocks.map((b) => b.id)).toEqual(["status", "checklist", "activity", "notes"]);
    const removed = removeBlock(panel(), 1);
    expect(removed.blocks.map((b) => [b.id, b.order])).toEqual([["status", 0], ["checklist", 1], ["activity", 2]]);
  });

  it("writes only chosen config values", () => {
    const p = addBlock(panel([]), "document");
    const url = BLOCK_CATALOG.document.configFields.find((f) => f.key === "url")!;
    const page = BLOCK_CATALOG.document.configFields.find((f) => f.key === "page")!;
    let next = setBlockConfig(p, 0, url, "https://example.com/a.pdf");
    next = setBlockConfig(next, 0, page, 3);
    expect(next.blocks[0].config).toEqual({ url: "https://example.com/a.pdf", page: 3 });
    next = setBlockConfig(next, 0, page, 1);
    next = setBlockConfig(next, 0, url, "");
    expect(next.blocks[0].config).toEqual({});
  });

  it("switches panels: custom panels carry no blocks; back to blocks seeds the default four", () => {
    const custom = switchPanel(panel(), "insurance_notebook");
    expect(custom).toEqual({ panel_id: "insurance_notebook", layout: "wide", blocks: [] });
    const back = switchPanel(custom, "composite");
    expect(back.panel_id).toBe("composite");
    expect(back.blocks.map((b) => b.id)).toEqual(["status", "notes", "checklist", "activity"]);
  });

  it("knows when the worker registers each block tool (#69)", () => {
    const none = blockToolStatus(panel(), []);
    expect(none.every((s) => !s.available)).toBe(true);
    const withForm = blockToolStatus(addBlock(panel(), "form"), ["request_form"]);
    const byName = Object.fromEntries(withForm.map((s) => [s.name, s]));
    expect(byName.request_form).toMatchObject({ available: true, enabled: false });
    expect(byName.update_block.available).toBe(false); // form is not an update_block target
    const withTable = Object.fromEntries(blockToolStatus(addBlock(panel(), "table"), []).map((s) => [s.name, s]));
    expect(withTable.update_block.available).toBe(true);
    expect(withTable.table_append.available).toBe(true);
    expect(withTable.show_document.available).toBe(false);
  });

  it("offers every registered panel, blocks first, hiding the classic panel unless it is in use", () => {
    expect(panelChoices("composite")).toEqual(["composite", "insurance_notebook"]);
    expect(panelChoices("generic")).toContain("generic");
    expect(panelChoices("some_pack_panel")).toContain("some_pack_panel");
  });
});

describe("form contract", () => {
  it("round-trips config.panel through the form and sends ui_panel_id as its mirror", () => {
    const a = agent();
    const values = toFormValues(a);
    expect(values.config.panel.blocks.map((b) => b.id)).toEqual(["status", "notes", "checklist", "activity"]);
    values.config.panel = moveBlock(addBlock(values.config.panel, "form"), 4, 0);
    const body = buildAgentUpdate(a, values);
    expect(body.ui_panel_id).toBe("composite");
    expect(body.config?.panel?.blocks?.map((b) => [b.id, b.order])).toEqual([
      ["form", 0],
      ["status", 1],
      ["notes", 2],
      ["checklist", 3],
      ["activity", 4],
    ]);
  });

  it("sorts stored blocks by order when loading", () => {
    const a = agent({
      config: {
        instructions: "Hi",
        pipeline: { mode: "cascaded" },
        panel: { panel_id: "composite", layout: "wide", blocks: [{ id: "b2", type: "notes", order: 5 }, { id: "b1", type: "status", order: 1 }] },
      },
    });
    const value = toFormValues(a).config.panel;
    expect(value).toEqual({
      panel_id: "composite",
      layout: "wide",
      blocks: [
        { id: "b1", type: "status", title: null, config: {}, order: 0 },
        { id: "b2", type: "notes", title: null, config: {}, order: 1 },
      ],
    });
  });

  it("rejects duplicate and malformed block ids", async () => {
    const values = toFormValues(agent());
    values.config.panel.blocks[1] = { ...values.config.panel.blocks[1], id: "status" };
    values.config.panel.blocks[2] = { ...values.config.panel.blocks[2], id: "has/slash" };
    const result = agentEditorFormSchema.safeParse(values);
    expect(result.success).toBe(false);
    const paths = result.success ? [] : result.error.issues.map((i) => i.path.join("."));
    expect(paths).toContain("config.panel.blocks.1.id");
    expect(paths).toContain("config.panel.blocks.2.id");
  });
});

describe("PanelComposer", () => {
  it("replaces the built-in panel section through an EditorExtension", () => {
    expect(EDITOR_EXTENSIONS).toContain(panelComposerExtension);
    const section = panelComposerExtension.sections?.[0];
    expect(section).toMatchObject({ id: "panel", order: 40, issuePaths: ["panel", "capabilities", "ui_panel_id"] });
    // The WP-5 placeholder is a re-export, so the built-in list renders the composer too.
    expect(PanelTab).toBe(PanelComposer);
  });

  it("lists the blocks, adds one from the palette and shows it in the live preview", async () => {
    stubFetch();
    render(<Harness agent={agent()} />);
    const list = screen.getByRole("list", { name: "Blocks" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(4);

    fireEvent.click(screen.getByRole("button", { name: "Add block" }));
    fireEvent.click(within(screen.getByRole("list", { name: "Block types" })).getByText("Table"));
    expect(within(screen.getByRole("list", { name: "Blocks" })).getAllByRole("listitem")).toHaveLength(5);
    expect(latest?.config.panel.blocks.at(-1)).toMatchObject({ id: "table", type: "table", order: 4 });
    expect(latest?.ui_panel_id).toBe("composite");

    // The live preview is the composite panel over the fixtures: the new table is filled.
    const preview = await screen.findByTestId("composer-preview", {}, { timeout: 5000 });
    expect(await within(preview).findByText("Induction hob")).toBeTruthy();
  });

  it("reorders with the keyboard on the drag handle and announces it", async () => {
    stubFetch();
    render(<Harness agent={agent()} />);
    const handle = screen.getByRole("button", { name: /Reorder Activity, position 4 of 4/ });
    fireEvent.keyDown(handle, { key: "ArrowUp" });
    expect(latest?.config.panel.blocks.map((b) => b.id)).toEqual(["status", "notes", "activity", "checklist"]);
    expect(screen.getByText("Activity moved to position 3 of 4.")).toBeTruthy();
  });

  it("reorders by drag and drop", () => {
    stubFetch();
    render(<Harness agent={agent()} />);
    const handle = screen.getByRole("button", { name: /Reorder Status/ });
    const rows = within(screen.getByRole("list", { name: "Blocks" })).getAllByRole("listitem");
    const dataTransfer = { setData: vi.fn(), effectAllowed: "" };
    fireEvent.dragStart(handle, { dataTransfer });
    fireEvent.dragOver(rows[2], { dataTransfer });
    fireEvent.drop(rows[2], { dataTransfer });
    expect(latest?.config.panel.blocks.map((b) => b.id)).toEqual(["notes", "checklist", "status", "activity"]);
  });

  it("removes a block", () => {
    stubFetch();
    render(<Harness agent={agent()} />);
    fireEvent.click(screen.getByRole("button", { name: "Remove Still needed" }));
    expect(latest?.config.panel.blocks.map((b) => b.id)).toEqual(["status", "notes", "activity"]);
  });

  it("edits a block's title, id and config from the generated form", async () => {
    stubFetch();
    const a = agent({
      config: {
        instructions: "Hi",
        pipeline: { mode: "cascaded" },
        panel: { panel_id: "composite", layout: "side", blocks: [{ id: "cam", type: "video", config: {}, order: 0 }] },
      },
    });
    render(<Harness agent={a} />);
    fireEvent.click(screen.getByRole("button", { name: /^Video/ }));
    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Your camera" } });
    fireEvent.change(screen.getByLabelText("Block id"), { target: { value: "my camera" } });
    fireEvent.click(screen.getByRole("switch", { name: "Start muted" }));
    expect(latest?.config.panel.blocks[0]).toMatchObject({ id: "my_camera", title: "Your camera", config: { muted: true } });
    // Editing the id keeps the row (and the field) mounted.
    expect(screen.getByLabelText("Block id")).toBeTruthy();

    // A generated select (Radix) writes the config key.
    fireEvent.click(screen.getByRole("combobox", { name: "Source" }));
    fireEvent.click(await screen.findByRole("option", { name: "The caller's camera" }));
    expect(latest?.config.panel.blocks[0].config).toEqual({ muted: true, source: "user_camera" });
  });

  it("edits a table's starting columns", () => {
    stubFetch();
    const a = agent({
      config: {
        instructions: "Hi",
        pipeline: { mode: "cascaded" },
        panel: { panel_id: "composite", layout: "side", blocks: [{ id: "items", type: "table", config: {}, order: 0 }] },
      },
    });
    render(<Harness agent={a} />);
    fireEvent.click(screen.getByRole("button", { name: /^Table/ }));
    fireEvent.click(screen.getByRole("button", { name: "Add column" }));
    fireEvent.change(screen.getByLabelText("Column 1 label"), { target: { value: "Item" } });
    expect(latest?.config.panel.blocks[0].config).toEqual({ columns: [{ key: "col_1", label: "Item", type: "string" }] });
  });

  it("switches the layout and the panel, keeping ui_panel_id in step", () => {
    stubFetch();
    render(<Harness agent={agent()} />);
    fireEvent.click(screen.getByRole("radio", { name: /Main column/ }));
    expect(latest?.config.panel.layout).toBe("wide");
    fireEvent.click(screen.getByRole("radio", { name: /Claim notebook/ }));
    expect(latest?.config.panel).toEqual({ panel_id: "insurance_notebook", layout: "wide", blocks: [] });
    expect(latest?.ui_panel_id).toBe("insurance_notebook");
    // A custom panel has no block list, preview or block tools.
    expect(screen.queryByRole("list", { name: "Blocks" })).toBeNull();
    expect(screen.queryByTestId("composer-preview")).toBeNull();
  });

  it("names the blocks a custom pack panel exposes", async () => {
    stubFetch();
    const a = agent({
      pack_id: "insurance_claim",
      ui_panel_id: "insurance_notebook",
      config: { instructions: "Hi", pipeline: { mode: "cascaded" }, panel: { panel_id: "insurance_notebook", layout: "wide", blocks: [] } },
    });
    render(<Harness agent={a} />);
    expect(await screen.findByText(/blocks the panel exposes: Claim documents/)).toBeTruthy();
  });

  it("gates the block tool switches on matching blocks and writes builtin_disabled", () => {
    stubFetch();
    render(<Harness agent={agent()} />);
    const requestForm = screen.getByRole("switch", { name: "Ask with a form" });
    expect(requestForm.hasAttribute("disabled")).toBe(true);
    expect(screen.getAllByText("Add a form block first").length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: "Add block" }));
    fireEvent.click(within(screen.getByRole("list", { name: "Block types" })).getByText("Form"));
    const enabled = screen.getByRole("switch", { name: "Ask with a form" });
    expect(enabled.hasAttribute("disabled")).toBe(false);
    expect(enabled.getAttribute("aria-checked")).toBe("true");
    fireEvent.click(enabled);
    expect(latest?.config.tools.builtin_disabled).toContain("request_form");
  });

  it("offers the switch to blocks for an agent on the classic panel", () => {
    stubFetch();
    const a = agent({ ui_panel_id: "generic", config: { instructions: "Hi", pipeline: { mode: "cascaded" } } });
    render(<Harness agent={a} />);
    expect(screen.getByText(/still uses the classic session panel/)).toBeTruthy();
    fireEvent.click(screen.getByRole("radio", { name: /Block panel/ }));
    expect(latest?.config.panel.blocks.map((b) => b.id)).toEqual(["status", "notes", "checklist", "activity"]);
    expect(latest?.ui_panel_id).toBe("composite");
  });

  it("keeps the session capabilities (moved from the WP-5 tab)", async () => {
    stubFetch(TEXT_ONLY_PROVIDERS);
    const withCameraAndTextOnlyLlm = agent({
      config: {
        instructions: "Hi",
        pipeline: { mode: "cascaded", llm: { provider_id: "openai-llm", model: "gpt-text" } },
        capabilities: { camera: true, screen_share: false, chat_input: true, vision_inject_per_turn: true },
      },
    });
    render(<Harness agent={withCameraAndTextOnlyLlm} />);
    expect(screen.getByRole("switch", { name: "Camera" })).toBeTruthy();
    expect(screen.getByRole("switch", { name: "Typing" })).toBeTruthy();
    expect(await screen.findByText(/can't see images/)).toBeTruthy();
  });

  it("hides the vision hint without a known text-only model", async () => {
    stubFetch();
    render(<Harness agent={agent()} />);
    await waitFor(() => expect(screen.queryByText(/can't see images/)).toBeNull());
    await act(async () => {});
  });
});
