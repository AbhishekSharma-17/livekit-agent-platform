import * as React from "react";

import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { AgentsTable } from "@/components/console/agents/agents-table";
import type { AgentOut, AgentPage, PacksResponse, ProvidersResponse } from "@/contracts/lkap-contracts";

// jsdom has no ResizeObserver; the shadcn `Select`/`DropdownMenu` (Radix
// popper positioning) need one to mount (docs pattern already used by
// tests/console-http-tool-editor.test.tsx).
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const routerReplace = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: routerReplace, push: vi.fn() }),
  usePathname: () => "/console/agents",
  useSearchParams: () => new URLSearchParams(),
}));

function agent(overrides: Partial<AgentOut> & { id: string; name: string; slug: string }): AgentOut {
  return {
    description: "",
    pack_id: "insurance_claim",
    ui_panel_id: "insurance_notebook",
    published: false,
    config_version: 1,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-19T00:00:00Z",
    config: {
      instructions: "Be helpful.",
      pipeline: {
        mode: "cascaded",
        stt: { provider_id: "deepgram-stt" },
        llm: { provider_id: "openai-llm" },
        tts: { provider_id: "elevenlabs-tts" },
      },
    },
    ...overrides,
  };
}

const PROVIDERS: ProvidersResponse = {
  providers: [
    { id: "deepgram-stt", kind: "stt", label: "Deepgram Nova", vendor: "Deepgram", package: "", python_class: "" },
    { id: "openai-llm", kind: "llm", label: "GPT-4o", vendor: "OpenAI", package: "", python_class: "" },
    { id: "elevenlabs-tts", kind: "tts", label: "Eleven Labs", vendor: "ElevenLabs", package: "", python_class: "" },
  ],
};

const PACKS: PacksResponse = {
  items: [
    {
      manifest: {
        id: "insurance_claim",
        name: "Insurance claim intake",
        description: "Adjuster claim intake",
        capabilities: {},
        default_greeting: "Hi",
        default_instructions: "Help.",
        recommended_pipeline: { mode: "cascaded" },
        state_schema: {},
        tool_names: [],
        ui_panel_id: "insurance_notebook",
        version: "1",
      },
    },
  ],
};

function stubFetch(agents: AgentOut[]) {
  const page: AgentPage = { items: agents, total: agents.length };
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    if (url.startsWith("/api/console/agents/") && method === "DELETE") {
      return { ok: true, status: 204, json: async () => undefined } as Response;
    }
    if (url.startsWith("/api/console/agents/") && method === "PUT") {
      const id = url.split("?")[0].split("/").pop() as string;
      const body = JSON.parse(init?.body as string) as Partial<AgentOut>;
      const found = agents.find((a) => a.id === id) as AgentOut;
      return { ok: true, status: 200, json: async () => ({ ...found, ...body }) } as Response;
    }
    if (url.startsWith("/api/console/agents")) {
      return { ok: true, status: 200, json: async () => page } as Response;
    }
    if (url.startsWith("/api/console/providers")) {
      return { ok: true, status: 200, json: async () => PROVIDERS } as Response;
    }
    if (url.startsWith("/api/console/packs")) {
      return { ok: true, status: 200, json: async () => PACKS } as Response;
    }
    throw new Error(`Unhandled fetch: ${method} ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderTable(agents: AgentOut[]) {
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const fetchMock = stubFetch(agents);
  const view = render(
    <QueryClientProvider client={client}>
      <AgentsTable />
    </QueryClientProvider>,
  );
  return { ...view, fetchMock };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

/**
 * `ResponsiveTable` renders the table (≥ 768 px) and the mobile card list at
 * the same time — CSS, not conditional rendering, picks which shows
 * (docs/UI_UX_SPEC.md §2.7). Row-content assertions are scoped to the table
 * half so "Claims intake" (etc.) isn't ambiguous with its card twin; a
 * dedicated test below checks the card half renders the same row.
 */
function tableScope() {
  const el = document.querySelector('[data-slot="responsive-table-table"]');
  if (!el) throw new Error("responsive table not rendered");
  return within(el as HTMLElement);
}

describe("AgentsTable", () => {
  it("renders a row with name, slug, pack, pipeline vendors and status", async () => {
    renderTable([agent({ id: "a-1", name: "Claims intake", slug: "claims-intake", published: true })]);

    await waitFor(() => expect(tableScope().getByText("Claims intake")).toBeTruthy());
    const table = tableScope();
    expect(table.getByText("Claims intake")).toBeTruthy();
    expect(table.getByText("/claims-intake")).toBeTruthy();
    expect(table.getByText("Insurance claim intake")).toBeTruthy();
    expect(table.getByText("Live")).toBeTruthy();
    expect(table.getAllByRole("img", { name: "Deepgram" }).length).toBeGreaterThan(0);
  });

  it("has no publish switch in the row (row menu only, per §7.3 acceptance)", async () => {
    renderTable([agent({ id: "a-1", name: "Claims intake", slug: "claims-intake" })]);
    await waitFor(() => expect(tableScope().getByText("Claims intake")).toBeTruthy());
    expect(screen.queryAllByRole("switch")).toHaveLength(0);
  });

  it("filters by search text across name and slug", async () => {
    renderTable([
      agent({ id: "a-1", name: "Claims intake", slug: "claims-intake" }),
      agent({ id: "a-2", name: "Support desk", slug: "support-desk" }),
    ]);

    await waitFor(() => expect(tableScope().getByText("Claims intake")).toBeTruthy());
    expect(tableScope().getByText("Support desk")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Search agents"), { target: { value: "support" } });

    await waitFor(() => expect(tableScope().queryByText("Claims intake")).toBeNull());
    expect(tableScope().getByText("Support desk")).toBeTruthy();
  });

  it("filters by status (Live / Draft)", async () => {
    renderTable([
      agent({ id: "a-1", name: "Claims intake", slug: "claims-intake", published: true }),
      agent({ id: "a-2", name: "Support desk", slug: "support-desk", published: false }),
    ]);

    await waitFor(() => expect(tableScope().getByText("Claims intake")).toBeTruthy());

    fireEvent.click(screen.getByRole("button", { name: "Live" }));
    await waitFor(() => expect(tableScope().queryByText("Support desk")).toBeNull());
    expect(tableScope().getByText("Claims intake")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Draft" }));
    await waitFor(() => expect(tableScope().queryByText("Claims intake")).toBeNull());
    expect(tableScope().getByText("Support desk")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "All" }));
    await waitFor(() => expect(tableScope().getByText("Claims intake")).toBeTruthy());
    expect(tableScope().getByText("Support desk")).toBeTruthy();
  });

  it("also renders the row in the mobile card list", async () => {
    renderTable([agent({ id: "a-1", name: "Claims intake", slug: "claims-intake" })]);
    await waitFor(() => expect(tableScope().getByText("Claims intake")).toBeTruthy());

    const cardList = document.querySelector('[data-slot="responsive-table-cards"]');
    expect(cardList).toBeTruthy();
    expect(within(cardList as HTMLElement).getByText("Claims intake")).toBeTruthy();
  });

  /**
   * The row menu's open → select → confirm flow (a `DropdownMenu` opening a
   * sibling `Dialog`, per §7.3's "Publish/Unpublish with confirm, Delete with
   * confirm") is exercised in the running dev server / the WP-2 screenshot
   * pass instead of here. Simulating the Radix `DropdownMenuTrigger`'s open
   * gesture in jsdom (it opens on `pointerdown`, which needs a `PointerEvent`
   * polyfill since jsdom has none) reliably hangs the test process well after
   * the assertions themselves pass — reproduced with a minimal, project-
   * independent `DropdownMenu` + `Dialog` composition, so it is an
   * environment limitation (Radix's focus/dismiss-layer handling vs. jsdom),
   * not a defect in `AgentRowMenu`. What's covered here instead: the trigger
   * renders with the right accessible name for every row, and the actions
   * that don't require opening a nested dialog (there are none reachable
   * without opening the menu) — see the manual verification note above.
   */
  it("renders a row actions trigger with an accessible name per agent", async () => {
    renderTable([
      agent({ id: "a-1", name: "Claims intake", slug: "claims-intake" }),
      agent({ id: "a-2", name: "Support desk", slug: "support-desk" }),
    ]);
    await waitFor(() => expect(tableScope().getByText("Claims intake")).toBeTruthy());

    expect(tableScope().getByRole("button", { name: "Actions for Claims intake" })).toBeTruthy();
    expect(tableScope().getByRole("button", { name: "Actions for Support desk" })).toBeTruthy();
  });

  it("shows the empty state with a link to the new-agent flow when there are no agents", async () => {
    renderTable([]);
    expect(await screen.findByText("No agents yet")).toBeTruthy();
    const link = screen.getByRole("link", { name: "New agent" });
    expect(link.getAttribute("href")).toBe("/console/agents/new");
  });
});
