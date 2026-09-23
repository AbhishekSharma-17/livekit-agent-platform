import * as React from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Overview } from "@/components/console/overview/overview";
import type { AgentOut, AgentPage, CredentialPage, HealthResponse, SessionOut, SessionPage } from "@/contracts/lkap-contracts";

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
}

function stubFetch(fixtures: Fixtures) {
  const fetchMock = vi.fn<(url: string) => Promise<Response>>(async (url) => {
    const respond = (body: unknown, status = 200) =>
      ({ ok: status < 400, status, json: async () => body }) as Response;

    if (url.includes("/api/console/health")) return respond(HEALTH);
    if (url.includes("/api/console/agents")) return respond(fixtures.agents ?? { items: [], total: 0 });
    if (url.includes("/api/console/credentials")) return respond(fixtures.credentials ?? { items: [], total: 0 });
    if (url.includes("/api/console/sessions")) return respond(fixtures.sessions ?? { items: [], total: 0 });
    // connections, webhooks, auth/me: not built yet in this wave.
    return respond({ error: { code: "not_found", message: "not found" } }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderOverview() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <Overview />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Overview", () => {
  it("shows every checklist row not-done with its action, and the empty states, on a fresh workspace", async () => {
    stubFetch({});
    renderOverview();

    expect(await screen.findByText("Create your first agent")).toBeTruthy();

    // Sign-in is always "done" in this v1-admin-token phase (no login flow exists yet).
    expect(screen.getByText("Sign in")).toBeTruthy();

    // "New agent" appears twice (the checklist row's action and Quick actions).
    expect(screen.getAllByRole("link", { name: "New agent" }).length).toBe(2);
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

  it("links quick actions to the right destinations", async () => {
    stubFetch({});
    renderOverview();
    await waitFor(() => expect(screen.getByText("Quick actions")).toBeTruthy());
    const quickActions = screen.getByText("Quick actions").closest("section") as HTMLElement;
    expect(within(quickActions).getByRole("link", { name: "New agent" }).getAttribute("href")).toBe(
      "/console/agents/new",
    );
    expect(within(quickActions).getByRole("link", { name: "New knowledge base" }).getAttribute("href")).toBe(
      "/console/knowledge",
    );
  });
});
