import * as React from "react";

import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { AgentsTable } from "@/components/console/agents/agents-table";
import { CreateAgentDeepLink } from "@/components/console/agents/create/create-agent-deep-link";
import type { AgentOut, AgentPage, PacksResponse, ProvidersResponse } from "@/contracts/lkap-contracts";

import { TEMPLATES } from "./fixtures/templates";

// jsdom has no ResizeObserver; the shadcn `Select`/`DropdownMenu` (Radix
// popper positioning) need one to mount (docs pattern already used by
// tests/console-http-tool-editor.test.tsx).
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const routerReplace = vi.fn();
const routerPush = vi.fn();
let searchParams = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: routerReplace, push: routerPush }),
  usePathname: () => "/console/agents",
  useSearchParams: () => searchParams,
}));

// Radix Dialog positioning in jsdom (see console-editor-shell.test.tsx): answer
// the top-layer pseudo-class probes with `false` instead of seconds in nwsapi.
const nativeMatches = Element.prototype.matches;
beforeAll(() => {
  Element.prototype.matches = function matches(this: Element, selector: string) {
    if (selector === ":popover-open" || selector === ":modal") return false;
    return nativeMatches.call(this, selector);
  };
});
afterAll(() => {
  Element.prototype.matches = nativeMatches;
});

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

const CONNECTIONS = {
  items: [{ id: "conn-1", name: "Cloud A", deployment_type: "cloud", is_default: true }],
  total: 1,
};

function stubFetch(agents: AgentOut[], role: "owner" | "admin" | "builder" | "viewer" = "admin") {
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
    if (url.startsWith("/api/console/connections")) {
      return { ok: true, status: 200, json: async () => CONNECTIONS } as Response;
    }
    if (url.startsWith("/api/console/packs")) {
      return { ok: true, status: 200, json: async () => PACKS } as Response;
    }
    if (url.startsWith("/api/console/templates")) {
      return { ok: true, status: 200, json: async () => TEMPLATES } as Response;
    }
    if (url.startsWith("/api/console/credentials")) {
      return { ok: true, status: 200, json: async () => ({ items: [], total: 0 }) } as Response;
    }
    if (url.startsWith("/api/console/auth/me")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          user: { id: "u1", email: "admin@example.test" },
          workspaces: [{ id: "ws1", name: "Test workspace", slug: "test", role }],
        }),
      } as Response;
    }
    throw new Error(`Unhandled fetch: ${method} ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderTable(agents: AgentOut[], role: "owner" | "admin" | "builder" | "viewer" = "admin") {
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const fetchMock = stubFetch(agents, role);
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
  searchParams = new URLSearchParams();
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

  it.each([
    [undefined, "Cloud A (default)"],
    ["conn-1", "Cloud A · Cloud"],
    ["conn-gone", "Unknown connection"],
  ])("labels the connection by name, never its id (connection_id=%s)", async (connectionId, label) => {
    renderTable([agent({ id: "a-1", name: "Claims intake", slug: "claims-intake", connection_id: connectionId })]);
    await waitFor(() => expect(tableScope().getByText(label)).toBeTruthy());
    if (connectionId) expect(tableScope().queryByText(connectionId)).toBeNull();
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

  it("opens the New agent dialog from the empty state's button, without navigating", async () => {
    renderTable([]);
    expect(await screen.findByText("No agents yet")).toBeTruthy();
    expect(screen.queryByRole("link", { name: "New agent" })).toBeNull();

    // The button is gated until the role is known, then enabled for an admin.
    await waitFor(() => expect((screen.getByRole("button", { name: "New agent" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "New agent" }));

    expect(await screen.findByRole("dialog", { name: "New agent" })).toBeTruthy();
    await waitFor(() => expect(screen.getByRole("radio", { name: "Blank agent" }).getAttribute("aria-checked")).toBe("true"));
    expect(routerPush).not.toHaveBeenCalled();
    expect(routerReplace).not.toHaveBeenCalled();
  });

  // ------------------------------------------------------- V2-20-5: viewer gating
  it("disables the New agent button for a viewer (no dialog)", async () => {
    renderTable([], "viewer");
    await screen.findByText("No agents yet");
    // `builder`+ is required (auth/roles.py::ROUTE_POLICY "/v1/agents" write);
    // a viewer never even sees the create form's `Link`, just a disabled
    // button (docs/v2/_asks.md V2-20-5 — a `<Link>` can't be `disabled`).
    const button = await screen.findByRole("button", { name: "New agent" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByRole("link", { name: "New agent" })).toBeNull();
    fireEvent.click(button);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  // The row menu's Publish/Delete items are also gated (`disabled={!canWrite}`
  // in `AgentRowMenu`), but opening this specific Radix `DropdownMenu` in
  // jsdom hangs the test process (see the documented limitation above on
  // "renders a row actions trigger…") — not exercised here for the same
  // environment reason.
});

describe("/console/agents/new deep link", () => {
  function renderDeepLink(role: "admin" | "viewer" = "admin") {
    vi.stubGlobal("ResizeObserver", ResizeObserverStub);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    stubFetch([], role);
    return render(
      <QueryClientProvider client={client}>
        <CreateAgentDeepLink />
      </QueryClientProvider>,
    );
  }

  it("opens the dialog with ?template=receptionist preselected", async () => {
    searchParams = new URLSearchParams("template=receptionist");
    renderDeepLink();
    expect(await screen.findByRole("dialog", { name: "New agent" })).toBeTruthy();
    await waitFor(() => expect(screen.getByRole("radio", { name: "Receptionist" }).getAttribute("aria-checked")).toBe("true"));
    expect(screen.getByRole("radio", { name: "Blank agent" }).getAttribute("aria-checked")).toBe("false");
  });

  it("goes back to the agents list when closed without creating", async () => {
    renderDeepLink();
    await screen.findByRole("dialog", { name: "New agent" });
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(routerReplace).toHaveBeenCalledWith("/console/agents"));
  });

  it("never opens for a viewer", async () => {
    renderDeepLink("viewer");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    // Give the role query a chance to resolve, then check again.
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
