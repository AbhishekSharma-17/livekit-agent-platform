import * as React from "react";

import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { CreateAgentDialog } from "@/components/console/agents/create/create-agent-dialog";
import type { AgentConfig, AgentOut, CredentialPage, PacksResponse, PanelPreset, ProvidersResponse } from "@/contracts/lkap-contracts";

import { TEMPLATES, templateById } from "./fixtures/templates";

/**
 * V6-10 (ask #56): the New agent dialog's "Starting panel" choice — the
 * Notebook preset applied with a second `PUT /v1/agents/{id}` right after
 * creation. `console-create-agent.test.tsx` (V4-02) covers everything else
 * about this dialog.
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

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/console/agents",
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

const PROVIDERS: ProvidersResponse = { providers: [] };
const NO_KEYS: CredentialPage = { items: [], total: 0 };
const PACKS: PacksResponse = { items: [templateById("blank").pack].map((manifest) => ({ manifest })) };

const NOTEBOOK_PRESET: PanelPreset = {
  id: "notebook",
  name: "Notebook",
  description: "A wide notebook the agent writes in as the call goes.",
  panel: {
    panel_id: "composite",
    layout: "wide",
    blocks: [
      { id: "status", type: "status", title: null, config: {}, order: 0 },
      { id: "notebook", type: "notebook", title: "Notebook", config: { caller_can_write: true }, order: 1 },
      { id: "gallery", type: "gallery", title: "Pictures", config: {}, order: 2 },
    ],
  },
};

function createdAgent(config?: Partial<AgentConfig>): AgentOut {
  const template = templateById("blank");
  return {
    id: "agent-new",
    name: template.template.name,
    slug: "agent-new",
    description: "",
    pack_id: template.pack.id,
    ui_panel_id: template.pack.ui_panel_id,
    published: false,
    config_version: 1,
    created_at: "2026-09-24T00:00:00Z",
    updated_at: "2026-09-24T00:00:00Z",
    config: { instructions: "Help.", pipeline: { mode: "cascaded" }, panel: { panel_id: "composite", layout: "side", blocks: [] }, ...config },
  };
}

function stubFetch(presets: PanelPreset[] = [NOTEBOOK_PRESET]) {
  const puts: unknown[] = [];
  const respond = (body: unknown, status = 200) => ({ ok: status < 400, status, statusText: "", json: async () => body }) as Response;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    if (url.startsWith("/api/console/agents/") && method === "PUT") {
      puts.push({ url, body: JSON.parse(init?.body as string) });
      return respond(createdAgent());
    }
    if (url.startsWith("/api/console/agents") && method === "POST") return respond(createdAgent(), 201);
    if (url.startsWith("/api/console/templates")) return respond(TEMPLATES);
    if (url.startsWith("/api/console/packs")) return respond(PACKS);
    if (url.startsWith("/api/console/providers")) return respond(PROVIDERS);
    if (url.startsWith("/api/console/credentials")) return respond(NO_KEYS);
    if (url.startsWith("/api/console/connections")) return respond({ items: [], total: 0 });
    if (url.startsWith("/api/console/panels/presets")) return respond({ items: presets });
    throw new Error(`Unhandled fetch: ${method} ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { puts, fetchMock };
}

function renderDialog(presets: PanelPreset[] = [NOTEBOOK_PRESET]) {
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const stub = stubFetch(presets);
  const view = render(
    <QueryClientProvider client={client}>
      <CreateAgentDialog open onOpenChange={vi.fn()} />
    </QueryClientProvider>,
  );
  return { ...view, ...stub };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

async function goToNameStep() {
  await waitFor(() => expect(document.querySelectorAll('[data-slot="template-tile"]').length).toBeGreaterThan(0));
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  await screen.findByRole("textbox", { name: /^Name/ });
}

describe("CreateAgentDialog — Starting panel (V6-10, ask #56)", () => {
  it("offers the Notebook preset and applies it with a second PUT after creation", async () => {
    const { puts } = renderDialog();
    await goToNameStep();
    fireEvent.change(screen.getByRole("textbox", { name: /^Name/ }), { target: { value: "Claims line" } });

    fireEvent.click(screen.getByRole("combobox", { name: "Starting panel" }));
    fireEvent.click(await screen.findByRole("option", { name: "Notebook" }));

    fireEvent.click(screen.getByRole("button", { name: "Create agent" }));
    await waitFor(() => expect(puts.length).toBe(1));
    const put = puts[0] as { url: string; body: { config: { panel: unknown }; ui_panel_id: string } };
    expect(put.url).toBe("/api/console/agents/agent-new");
    expect(put.body.config.panel).toEqual(NOTEBOOK_PRESET.panel);
    expect(put.body.ui_panel_id).toBe("composite");
  });

  it("leaves the starter's own panel alone when no preset is chosen (no second PUT)", async () => {
    const { puts } = renderDialog();
    await goToNameStep();
    fireEvent.change(screen.getByRole("textbox", { name: /^Name/ }), { target: { value: "Claims line" } });
    fireEvent.click(screen.getByRole("button", { name: "Create agent" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Create agent" })).toBeNull());
    expect(puts).toHaveLength(0);
  });

  it("hides the Starting panel field when the api has no presets (404 -> empty list)", async () => {
    renderDialog([]);
    await goToNameStep();
    expect(screen.queryByRole("combobox", { name: "Starting panel" })).toBeNull();
  });
});
