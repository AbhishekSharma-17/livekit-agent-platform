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
    stubFetch({});
    renderOverview();

    expect(await screen.findByText("Create your first agent")).toBeTruthy();

    // Sign-in is always "done" in this v1-admin-token phase (no login flow exists yet).
    expect(screen.getByText("Sign in")).toBeTruthy();

    // "New agent" appears twice (the checklist row's action and Quick actions),
    // both buttons that open the dialog — never links to /console/agents/new.
    expect(screen.getAllByRole("button", { name: "New agent" }).length).toBe(2);
    expect(screen.queryAllByRole("link", { name: "New agent" })).toHaveLength(0);
    expect(await screen.findByText("No calls yet")).toBeTruthy();
    expect(await screen.findByText("No agents are published")).toBeTruthy();

    // Status line: 0 agents, 0 live, 0 sessions.
    expect(screen.getByText(/0 agents/)).toBeTruthy();
    expect(screen.getByText(/0 live/)).toBeTruthy();
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

    // Status line reflects the fixtures.
    expect(screen.getByText(/2 agents/)).toBeTruthy();
    expect(screen.getByText(/1 live/)).toBeTruthy();
  });

  it("links quick actions to the right destinations; New agent opens the dialog", async () => {
    stubFetch({ role: "admin" });
    renderOverview();
    await waitFor(() => expect(screen.getByText("Quick actions")).toBeTruthy());
    const quickActions = screen.getByText("Quick actions").closest("section") as HTMLElement;
    expect(within(quickActions).getByRole("link", { name: "New knowledge base" }).getAttribute("href")).toBe(
      "/console/knowledge",
    );

    const newAgent = within(quickActions).getByRole("button", { name: "New agent" }) as HTMLButtonElement;
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

  it("disables New agent for a viewer", async () => {
    stubFetch({ role: "viewer" });
    renderOverview();
    await screen.findByText("Quick actions");
    await waitFor(() => {
      for (const button of screen.getAllByRole("button", { name: "New agent" })) {
        expect((button as HTMLButtonElement).disabled).toBe(true);
      }
    });
  });
});

describe("Setup checklist — connection row", () => {
  function connection(id: string, status: "ok" | "unverified" | "error") {
    return { id, name: `Conn ${id}`, slug: id, url: "wss://example.test", status };
  }

  /** The row's icon is a check only when done; the title row carries the state. */
  function connectionRowDone(): boolean {
    const row = screen.getByText("A LiveKit connection is tested").closest('[data-slot="section-row"]') as HTMLElement;
    return row.querySelector("svg.text-success") !== null;
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
