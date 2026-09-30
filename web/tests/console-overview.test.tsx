import * as React from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { Overview } from "@/components/console/overview/overview";
import type {
  AgentOut,
  AgentPage,
  ConnectionPage,
  CredentialPage,
  HealthResponse,
  SessionOut,
  SessionPage,
} from "@/contracts/lkap-contracts";

import { TEMPLATES } from "./fixtures/templates";

const routerPush = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: routerPush, replace: vi.fn() }),
  usePathname: () => "/console",
  useSearchParams: () => new URLSearchParams(),
}));

// Radix Dialog in jsdom: see console-editor-shell.test.tsx for why.
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

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const HEALTH: HealthResponse = {
  ok: true,
  version: "2.0.0",
  livekit_url: "wss://example.livekit.cloud",
  packs: ["generic"],
  db: "ok",
};

function agent(overrides: Partial<AgentOut> & { id: string; name: string; slug: string }): AgentOut {
  return {
    description: "",
    pack_id: "generic",
    ui_panel_id: "generic",
    published: false,
    config_version: 1,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-19T00:00:00Z",
    config: {
      instructions: "Be helpful.",
      pipeline: { mode: "cascaded", stt: { provider_id: "a" }, llm: { provider_id: "b" }, tts: { provider_id: "c" } },
    },
    ...overrides,
  };
}

function session(overrides: Partial<SessionOut> & { id: string }): SessionOut {
  return {
    agent_id: "agent-1",
    agent_name: "Stage9 Insurance",
    config_version: 1,
    created_at: "2026-09-19T00:00:00Z",
    pipeline_mode: "cascaded",
    room_name: "room-1",
    status: "ended",
    ...overrides,
  };
}

interface Fixtures {
  agents?: AgentPage;
  credentials?: CredentialPage;
  sessions?: SessionPage;
  /** `GET /v1/connections`; omitted → 404 (the route isn't there). */
  connections?: ConnectionPage;
  /** Answer `GET /v1/auth/me` with this role; omitted → 404 (no workspace accounts). */
  role?: "admin" | "viewer";
}

function stubFetch(fixtures: Fixtures) {
  const fetchMock = vi.fn<(url: string) => Promise<Response>>(async (url) => {
    const respond = (body: unknown, status = 200) =>
      ({ ok: status < 400, status, json: async () => body }) as Response;

    if (url.includes("/api/console/health")) return respond(HEALTH);
    if (url.includes("/api/console/agents")) return respond(fixtures.agents ?? { items: [], total: 0 });
    if (url.includes("/api/console/credentials")) return respond(fixtures.credentials ?? { items: [], total: 0 });
    if (url.includes("/api/console/sessions")) return respond(fixtures.sessions ?? { items: [], total: 0 });
    if (url.includes("/api/console/templates")) return respond(TEMPLATES);
    if (url.includes("/api/console/connections") && fixtures.connections) return respond(fixtures.connections);
    if (url.includes("/api/console/auth/me") && fixtures.role) {
      return respond({
        user: { id: "u1", email: "admin@example.test" },
        workspaces: [{ id: "ws1", name: "Test workspace", slug: "test", role: fixtures.role }],
      });
    }
    // connections, webhooks, auth/me: not built yet in this wave.
    return respond({ error: { code: "not_found", message: "not found" } }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** A stat card's value, found by its label ("Agents", "Live" …). */
function statValue(label: string): string | null | undefined {
  const card = screen
    .getAllByText(label)
    .map((node) => node.closest('[data-slot="stat-card"]'))
    .find(Boolean) as HTMLElement | undefined;
  return card?.querySelector(".text-stat")?.textContent;
}

function renderOverview() {
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <Overview />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("Overview", () => {
  it("shows every checklist row not-done with its action, and the empty states, on a fresh workspace", async () => {
    // A builder or admin sees the New agent actions (a viewer gets a read-only note, below).
    stubFetch({ role: "admin" });
    renderOverview();

    expect(await screen.findByText("Create your first agent")).toBeTruthy();

    // Sign-in is always "done" in this v1-admin-token phase (no login flow exists yet).
    expect(screen.getByText("Sign in")).toBeTruthy();

    // "New agent" appears twice (the page header's primary action and the
    // checklist row's action), both buttons that open the dialog — never
    // links to /console/agents/new.
    await waitFor(() => expect(screen.getAllByRole("button", { name: "New agent" }).length).toBe(2));
    expect(screen.queryAllByRole("link", { name: "New agent" })).toHaveLength(0);
    expect(await screen.findByText("No calls yet")).toBeTruthy();
    expect(await screen.findByText("No agents are published")).toBeTruthy();

    // Stat grid: 0 agents, 0 live, 0 sessions.
    await waitFor(() => expect(statValue("Agents")).toBe("0"));
    expect(statValue("Live")).toBe("0");
    expect(statValue("Sessions, last 7 days")).toBe("0");
  });

  it("flips rows to done and swaps the empty states for real data once the workspace has content", async () => {
    const agents: AgentPage = {
      total: 2,
      items: [
        agent({ id: "a-1", name: "Stage9 Insurance", slug: "stage9-insurance", published: true }),
        agent({
          id: "a-2",
          name: "Support desk",
          slug: "support-desk",
          config: {
            instructions: "Help.",
            pipeline: { mode: "cascaded", stt: { provider_id: "a" }, llm: { provider_id: "b" }, tts: { provider_id: "c" } },
            recording: { enabled: true },
          },
        }),
      ],
    };
    const credentials: CredentialPage = {
      total: 1,
      items: [{ id: "c-1", provider_id: "openai-llm", label: "OpenAI key", fingerprint: "ab12", created_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-01T00:00:00Z" }],
    };
    const sessions: SessionPage = {
      total: 1,
      items: [session({ id: "s-1", agent_id: "a-1", agent_name: "Stage9 Insurance", status: "active" })],
    };

    stubFetch({ agents, credentials, sessions });
    renderOverview();

    await waitFor(() => expect(screen.getAllByText("Stage9 Insurance").length).toBeGreaterThan(0));

    // Recording row is done for this workspace (agent a-2 has it enabled) — its action no longer renders.
    await waitFor(() => expect(screen.queryByRole("link", { name: "Open recording" })).toBeNull());

    // Live now + the active session's status chip both read "Live".
    expect(screen.getAllByText("Live").length).toBeGreaterThan(0);

    // Recent sessions: one row, no more "No calls yet" empty state.
    expect(screen.queryByText("No calls yet")).toBeNull();

    // Stat grid reflects the fixtures.
    expect(statValue("Agents")).toBe("2");
    expect(statValue("Live")).toBe("1");
  });

  it("counts neither archived agents nor archived published ones as live", async () => {
    const agents: AgentPage = {
      total: 4,
      items: [
        agent({ id: "a-1", name: "Front desk", slug: "front-desk", published: true }),
        agent({ id: "a-2", name: "Claims", slug: "claims" }),
        agent({ id: "a-3", name: "Old desk", slug: "old-desk", published: true, archived_at: "2026-09-10T00:00:00Z" }),
        agent({ id: "a-4", name: "Old claims", slug: "old-claims", archived_at: "2026-09-10T00:00:00Z" }),
      ],
    };
    stubFetch({ agents });
    renderOverview();

    await waitFor(() => expect(statValue("Agents")).toBe("2"));
    expect(statValue("Live")).toBe("1");
    expect(screen.getByText("2 archived agents not counted")).toBeTruthy();
    // Live now lists only the unarchived published agent.
    const liveNow = screen.getByText("Live now").closest("section") as HTMLElement;
    expect(within(liveNow).getByRole("link", { name: "Front desk" })).toBeTruthy();
    expect(within(liveNow).queryByRole("link", { name: "Old desk" })).toBeNull();
  });

  it("says when the loaded page may not hold every session of the week", async () => {
    const now = new Date().toISOString();
    const sessions: SessionPage = {
      total: 120,
      items: Array.from({ length: 50 }, (_, index) => session({ id: `s-${index}`, created_at: now })),
    };
    stubFetch({ sessions });
    renderOverview();
    await waitFor(() => expect(statValue("Sessions, last 7 days")).toBe("50+"));
  });

  it("shows an error with Retry in each card when the api fails", async () => {
    vi.stubGlobal("ResizeObserver", ResizeObserverStub);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: false, status: 500, json: async () => ({ error: { code: "internal", message: "boom" } }) }) as Response),
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <Overview />
      </QueryClientProvider>,
    );
    expect(await screen.findByText("Couldn't load the overview numbers")).toBeTruthy();
    expect(await screen.findByText("Couldn't load recent sessions")).toBeTruthy();
    expect(await screen.findByText("Couldn't load live agents")).toBeTruthy();
    expect(await screen.findByText("Couldn't load the setup checklist")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "Retry" }).length).toBeGreaterThanOrEqual(4);
    expect(screen.queryByText(/boom/)).toBeNull();
  });

  it("links quick actions to the right destinations; the header's New agent opens the dialog", async () => {
    stubFetch({ role: "admin" });
    renderOverview();
    await waitFor(() => expect(screen.getByText("Quick actions")).toBeTruthy());
    const quickActions = screen.getByText("Quick actions").closest("section") as HTMLElement;
    expect(within(quickActions).getByRole("link", { name: "New knowledge base" }).getAttribute("href")).toBe(
      "/console/knowledge",
    );

    const header = document.querySelector('[data-slot="page-header"]') as HTMLElement;
    const newAgent = within(header).getByRole("button", { name: "New agent" }) as HTMLButtonElement;
    await waitFor(() => expect(newAgent.disabled).toBe(false));
    fireEvent.click(newAgent);
    expect(await screen.findByRole("dialog", { name: "New agent" })).toBeTruthy();
    await waitFor(() => expect(screen.getByRole("radio", { name: "Blank agent" }).getAttribute("aria-checked")).toBe("true"));
    expect(routerPush).not.toHaveBeenCalled();
  });

  it("opens the dialog from the setup checklist's New agent action", async () => {
    stubFetch({ role: "admin" });
    renderOverview();
    await screen.findByText("Create your first agent");
    const checklist = screen.getByText("Set up LKAP").closest("section") as HTMLElement;
    // Gated (a disabled button) until the role is known, then a live one.
    const enabled = () => within(checklist).getByRole("button", { name: "New agent" }) as HTMLButtonElement;
    await waitFor(() => expect(enabled().disabled).toBe(false));
    fireEvent.click(enabled());
    expect(await screen.findByRole("dialog", { name: "New agent" })).toBeTruthy();
  });

  it("replaces New agent with a read-only note for a viewer (D12)", async () => {
    stubFetch({ role: "viewer" });
    renderOverview();
    await screen.findByText("Quick actions");
    await waitFor(() => expect(screen.queryAllByRole("button", { name: "New agent" })).toHaveLength(0));
    expect(screen.getAllByText("Ask a builder or admin to create agents.").length).toBeGreaterThan(0);
  });
});

describe("Setup checklist — connection row", () => {
  function connection(id: string, status: "ok" | "unverified" | "error") {
    return { id, name: `Conn ${id}`, slug: id, url: "wss://example.test", status };
  }

  /** The row carries its state (a check icon, and "Done:" for screen readers, when done). */
  function connectionRowDone(): boolean {
    const row = screen.getByText("A LiveKit connection is tested").closest('[data-slot="section-row"]') as HTMLElement;
    return row.getAttribute("data-state") === "done";
  }

  it("is not ticked when the only connection is unverified", async () => {
    stubFetch({ connections: { items: [connection("c1", "unverified")], total: 1 } });
    renderOverview();
    await screen.findByText("Run Test on a connection to confirm it can host agents.");
    expect(connectionRowDone()).toBe(false);
    expect(screen.getByRole("link", { name: "Open connections" })).toBeTruthy();
  });

  it("is not ticked when the last test failed", async () => {
    stubFetch({ connections: { items: [connection("c1", "error")], total: 1 } });
    renderOverview();
    await screen.findByText("Run Test on a connection to confirm it can host agents.");
    expect(connectionRowDone()).toBe(false);
  });

  it("is ticked once a connection has passed its test", async () => {
    stubFetch({ connections: { items: [connection("c1", "unverified"), connection("c2", "ok")], total: 2 } });
    renderOverview();
    await screen.findByText("At least one connection has passed its test and can host agents.");
    expect(connectionRowDone()).toBe(true);
    expect(screen.queryByRole("link", { name: "Open connections" })).toBeNull();
  });

  it("asks for a connection when there are none", async () => {
    stubFetch({ connections: { items: [], total: 0 } });
    renderOverview();
    expect(await screen.findByText("Add a LiveKit Cloud or self-hosted connection, then test it.")).toBeTruthy();
    expect(connectionRowDone()).toBe(false);
  });
});
