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
    if (url.startsWith("/api/console/agents/") && method === "POST" && url.split("?")[0].endsWith("/unarchive")) {
      const id = url.split("?")[0].split("/").slice(-2)[0];
      const found = agents.find((a) => a.id === id) as AgentOut;
      return { ok: true, status: 200, json: async () => ({ ...found, archived_at: null }) } as Response;
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

/**
 * Node ≥ 22 ships an inert global `localStorage` that shadows jsdom's (see
 * tests/theme-provider.test.tsx), so the list's remembered filters go to an
 * in-memory Storage that lives for one test (cleared in `afterEach`).
 */
const storageData = new Map<string, string>();
function stubLocalStorage() {
  const storage: Storage = {
    get length() {
      return storageData.size;
    },
    clear: () => storageData.clear(),
    getItem: (key) => storageData.get(key) ?? null,
    key: (index) => Array.from(storageData.keys())[index] ?? null,
    removeItem: (key) => {
      storageData.delete(key);
    },
    setItem: (key, value) => {
      storageData.set(key, String(value));
    },
  };
  vi.stubGlobal("localStorage", storage);
}

function renderTable(agents: AgentOut[], role: "owner" | "admin" | "builder" | "viewer" = "admin") {
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
  stubLocalStorage();
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
  storageData.clear();
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

  /** Search appears once the list has 6 agents (docs/ui/DESIGN-SYSTEM.md section 9). */
  const SIX_AGENTS = [
    agent({ id: "a-1", name: "Claims intake", slug: "claims-intake" }),
    agent({ id: "a-2", name: "Support desk", slug: "support-desk" }),
    agent({ id: "a-3", name: "Café concierge", slug: "cafe" }),
    agent({ id: "a-4", name: "Billing line", slug: "billing" }),
    agent({ id: "a-5", name: "Renewals", slug: "renewals" }),
    agent({ id: "a-6", name: "Roadside help", slug: "roadside" }),
  ];

  it("hides the search box below 6 agents", async () => {
    renderTable(SIX_AGENTS.slice(0, 2));
    await waitFor(() => expect(tableScope().getByText("Claims intake")).toBeTruthy());
    expect(screen.queryByLabelText("Search agents")).toBeNull();
  });

  it("filters by search text across name and slug", async () => {
    renderTable(SIX_AGENTS);

    await waitFor(() => expect(tableScope().getByText("Claims intake")).toBeTruthy());
    expect(tableScope().getByText("Support desk")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Search agents"), { target: { value: "support" } });

    await waitFor(() => expect(tableScope().queryByText("Claims intake")).toBeNull());
    // The match is highlighted, so the name is split across a <mark> and a text node.
    const bodyRows = tableScope().getAllByRole("row").slice(1);
    expect(bodyRows.map((row) => row.querySelector("span.font-medium")?.textContent)).toEqual(["Support desk"]);
  });

  it("matches accent-insensitively, needs every word, highlights the match and clears on Escape", async () => {
    renderTable(SIX_AGENTS);
    await waitFor(() => expect(tableScope().getByText("Claims intake")).toBeTruthy());
    const search = screen.getByLabelText("Search agents");

    fireEvent.change(search, { target: { value: "cafe CONC" } });
    await waitFor(() => expect(tableScope().queryByText("Claims intake")).toBeNull());
    const marks = Array.from(
      document.querySelectorAll('[data-slot="responsive-table-table"] [data-slot="search-highlight"]'),
    ).map((node) => node.textContent);
    expect(marks).toEqual(expect.arrayContaining(["Café", "conc", "cafe"]));

    fireEvent.change(search, { target: { value: "cafe desk" } });
    expect((await screen.findAllByText("No agents match “cafe desk”")).length).toBeGreaterThan(0);

    fireEvent.keyDown(search, { key: "Escape" });
    await waitFor(() => expect(tableScope().getByText("Claims intake")).toBeTruthy());
    expect((search as HTMLInputElement).value).toBe("");
  });

  it("shows a no-matches state with Clear filters, distinct from the empty list", async () => {
    renderTable(SIX_AGENTS);
    await waitFor(() => expect(tableScope().getByText("Claims intake")).toBeTruthy());

    fireEvent.change(screen.getByLabelText("Search agents"), { target: { value: "nothing like this" } });
    expect(await screen.findByText("No agents match “nothing like this”")).toBeTruthy();
    expect(screen.queryByText("No agents yet")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() => expect(tableScope().getByText("Claims intake")).toBeTruthy());
  });

  it("remembers the status filter per person and ignores a stored value it doesn't recognise", async () => {
    const first = renderTable([
      agent({ id: "a-1", name: "Claims intake", slug: "claims-intake", published: true }),
      agent({ id: "a-2", name: "Support desk", slug: "support-desk", published: false }),
    ]);
    await waitFor(() => expect(tableScope().getByText("Claims intake")).toBeTruthy());
    fireEvent.click(screen.getByRole("radio", { name: /^Draft/ }));
    await waitFor(() => expect(tableScope().queryByText("Claims intake")).toBeNull());
    first.unmount();

    const second = renderTable([
      agent({ id: "a-1", name: "Claims intake", slug: "claims-intake", published: true }),
      agent({ id: "a-2", name: "Support desk", slug: "support-desk", published: false }),
    ]);
    await waitFor(() => expect(tableScope().getByText("Support desk")).toBeTruthy());
    expect(tableScope().queryByText("Claims intake")).toBeNull();
    expect(screen.getByRole("radio", { name: /^Draft/ }).getAttribute("aria-checked")).toBe("true");
    second.unmount();

    storageData.set("lkap.console.agents.filters.v1", JSON.stringify({ status: "bogus", pack: 42 }));
    renderTable([
      agent({ id: "a-1", name: "Claims intake", slug: "claims-intake", published: true }),
      agent({ id: "a-2", name: "Support desk", slug: "support-desk", published: false }),
    ]);
    await waitFor(() => expect(tableScope().getByText("Claims intake")).toBeTruthy());
    expect(tableScope().getByText("Support desk")).toBeTruthy();
    expect(screen.getByRole("radio", { name: /^All/ }).getAttribute("aria-checked")).toBe("true");
  });

  it("counts agents per status in the filter", async () => {
    renderTable([
      agent({ id: "a-1", name: "Claims intake", slug: "claims-intake", published: true }),
      agent({ id: "a-2", name: "Support desk", slug: "support-desk", published: false }),
      agent({ id: "a-3", name: "Old smoke test", slug: "smoke", archived_at: "2026-09-29T07:30:00Z" }),
    ]);
    await waitFor(() => expect(tableScope().getByText("Claims intake")).toBeTruthy());
    expect(screen.getByRole("radio", { name: /^All/ }).textContent).toBe("All2");
    expect(screen.getByRole("radio", { name: /^Live/ }).textContent).toBe("Live1");
    expect(screen.getByRole("radio", { name: /^Draft/ }).textContent).toBe("Draft1");
    expect(screen.getByRole("radio", { name: /^Archived/ }).textContent).toBe("Archived1");
    // The count has its own words in the accessible name, not "Live1".
    expect(screen.getByRole("radio", { name: "Live (1)" })).toBeTruthy();
  });

  it("filters by status (Live / Draft)", async () => {
    renderTable([
      agent({ id: "a-1", name: "Claims intake", slug: "claims-intake", published: true }),
      agent({ id: "a-2", name: "Support desk", slug: "support-desk", published: false }),
    ]);

    await waitFor(() => expect(tableScope().getByText("Claims intake")).toBeTruthy());

    fireEvent.click(screen.getByRole("radio", { name: /^Live/ }));
    await waitFor(() => expect(tableScope().queryByText("Support desk")).toBeNull());
    expect(tableScope().getByText("Claims intake")).toBeTruthy();

    fireEvent.click(screen.getByRole("radio", { name: /^Draft/ }));
    await waitFor(() => expect(tableScope().queryByText("Claims intake")).toBeNull());
    expect(tableScope().getByText("Support desk")).toBeTruthy();

    fireEvent.click(screen.getByRole("radio", { name: /^All/ }));
    await waitFor(() => expect(tableScope().getByText("Claims intake")).toBeTruthy());
    expect(tableScope().getByText("Support desk")).toBeTruthy();
  });

  // ------------------------------------------------------- V6-30 (F-5): archived agents
  it("lists archived agents only under Archived, with an Archived chip and never a Live one", async () => {
    renderTable([
      agent({ id: "a-1", name: "Claims intake", slug: "claims-intake", published: true }),
      agent({ id: "a-2", name: "Old smoke test", slug: "smoke", published: true, archived_at: "2026-09-29T07:30:00Z" }),
    ]);

    await waitFor(() => expect(tableScope().getByText("Claims intake")).toBeTruthy());
    expect(tableScope().queryByText("Old smoke test")).toBeNull();
    fireEvent.click(screen.getByRole("radio", { name: /^Live/ }));
    await waitFor(() => expect(tableScope().getByText("Claims intake")).toBeTruthy());
    expect(tableScope().queryByText("Old smoke test")).toBeNull();

    fireEvent.click(screen.getByRole("radio", { name: /^Archived/ }));
    await waitFor(() => expect(tableScope().getByText("Old smoke test")).toBeTruthy());
    expect(tableScope().queryByText("Claims intake")).toBeNull();
    expect(tableScope().getByText("Archived", { selector: "[data-tone]" })).toBeTruthy();
    expect(tableScope().queryByText("Live", { selector: "[data-tone]" })).toBeNull();
    expect(tableScope().queryByRole("button", { name: "Actions for Old smoke test" })).toBeNull();
    const cards = within(document.querySelector('[data-slot="responsive-table-cards"]') as HTMLElement);
    expect(cards.getByText("Archived", { selector: "[data-tone]" })).toBeTruthy();
    expect(cards.queryByText("Live", { selector: "[data-tone]" })).toBeNull();
  });

  it("restores an archived agent after confirming in a dialog", async () => {
    searchParams = new URLSearchParams("status=archived");
    const { fetchMock } = renderTable([
      agent({ id: "a-2", name: "Old smoke test", slug: "smoke", archived_at: "2026-09-29T07:30:00Z" }),
    ]);

    const restore = await waitFor(() => tableScope().getByRole("button", { name: "Restore Old smoke test" }));
    await waitFor(() => expect((restore as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(restore);

    const dialog = await screen.findByRole("dialog", { name: 'Restore "Old smoke test"?' });
    expect(within(dialog).getByText("It moves back to your agents as a draft.")).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: "Restore" }));

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([url, init]) => String(url).startsWith("/api/console/agents/a-2/unarchive") && init?.method === "POST",
        ),
      ).toBe(true),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("points to Archived when every agent is archived", async () => {
    renderTable([agent({ id: "a-2", name: "Old smoke test", slug: "smoke", archived_at: "2026-09-29T07:30:00Z" })]);

    expect((await screen.findAllByText("No active agents")).length).toBeGreaterThan(0);
    expect(screen.queryByText("No agents yet")).toBeNull();
    fireEvent.click(screen.getAllByRole("button", { name: "Show archived" })[0]);
    await waitFor(() => expect(tableScope().getByText("Old smoke test")).toBeTruthy());
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
  it("replaces the New agent button with a read-only note for a viewer (no dialog)", async () => {
    renderTable([], "viewer");
    await screen.findByText("No agents yet");
    // `builder`+ is required (auth/roles.py::ROUTE_POLICY "/v1/agents" write);
    // a viewer reads a note that names the next step instead of an unusable
    // control (docs/ui/AUDIT.md decision D12).
    expect(await screen.findByText("Ask a builder or admin to make changes.", { exact: false })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "New agent" })).toBeNull();
    expect(screen.queryByRole("link", { name: "New agent" })).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("hides Restore on archived rows for a viewer", async () => {
    searchParams = new URLSearchParams("status=archived");
    renderTable([agent({ id: "a-2", name: "Old smoke test", slug: "smoke", archived_at: "2026-09-29T07:30:00Z" })], "viewer");
    await waitFor(() => expect(tableScope().getByText("Old smoke test")).toBeTruthy());
    // Give the role query a chance to resolve, then check.
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.queryByRole("button", { name: "Restore Old smoke test" })).toBeNull();
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
