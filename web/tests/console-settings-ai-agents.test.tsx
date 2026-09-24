import * as React from "react";

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AiAgentsTab } from "@/components/console/settings/ai-agents-tab";
import { writeAccessReason } from "@/components/console/lib/roles";
import type { ApiKeyOut, AuditOut } from "@/components/console/settings/api-types";

// `agent-activity-table.tsx` renders `next/link` for target links; matching
// `console-agents-list.test.tsx`'s convention avoids relying on whether
// Next's `Link` tolerates a missing app-router context in jsdom.
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/console/settings",
  useSearchParams: () => new URLSearchParams(),
}));

/**
 * "AI agents" settings tab (docs/v3/AGENT-ACCESS.md §5, PLAN-V3 V3-04
 * acceptance): the Connect dialog's full flow (the transcript warning gates
 * "Create key", the POST body, the reveal step, Local/Remote snippets), the
 * agent keys table (including revoke), the agent activity table, and role
 * gating. No `msw` dependency exists in this repo yet (`package.json` is
 * outside this card's exclusive files) — this follows the same
 * `vi.stubGlobal("fetch", …)` convention every other console dialog test
 * here already uses (see `console-providers-page.test.tsx`,
 * `console-credential-dialog.test.tsx`).
 */

const nativeMatches = Element.prototype.matches;

beforeEach(() => {
  // Radix asks jsdom for `:popover-open`/`:modal`, which jsdom doesn't
  // implement and which is very slow there (see console-credential-dialog
  // and console-editor-shell tests for the same guard).
  Element.prototype.matches = function matches(this: Element, selector: string) {
    if (selector === ":popover-open" || selector === ":modal") return false;
    return nativeMatches.call(this, selector);
  };
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
});

afterEach(() => {
  Element.prototype.matches = nativeMatches;
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

interface FetchFixture {
  role: "viewer" | "builder" | "admin" | "owner";
  apiKeys: ApiKeyOut[];
  auditRows: AuditOut[];
}

interface FetchStub {
  postCalls: { url: string; body: unknown }[];
  deleteCalls: string[];
  fixture: FetchFixture;
}

function agentKey(overrides: Partial<ApiKeyOut> = {}): ApiKeyOut {
  return {
    id: "key-1",
    workspace_id: "ws1",
    // Deliberately not "Claude Code" — that's also the Client column's
    // label for `client: "claude-code"`, and a table row would then show
    // the same text twice in different cells (Name vs. Client).
    name: "My Claude Code key",
    prefix: "lkap_ab12",
    scopes: ["agents:read", "sessions:read", "connections:read", "providers:read", "audit:read", "agents:write", "sessions:write"],
    created_by: "u1",
    created_at: "2026-09-01T00:00:00Z",
    last_used_at: null,
    revoked_at: null,
    expires_at: "2026-10-01T00:00:00Z",
    kind: "agent",
    client: "claude-code",
    last_client: null,
    ...overrides,
  };
}

function stubFetch(fixture: FetchFixture): FetchStub {
  const stub: FetchStub = { postCalls: [], deleteCalls: [], fixture };
  const apiKeys = [...fixture.apiKeys];

  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();

      if (url.includes("/api/console/auth/me")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            user: { id: "u1", email: "admin@example.test" },
            workspaces: [{ id: "ws1", name: "Test workspace", slug: "test", role: fixture.role }],
          }),
        } as Response;
      }

      if (url.includes("/api/console/api-keys") && method === "POST") {
        const body = init?.body ? JSON.parse(String(init.body)) : {};
        stub.postCalls.push({ url, body });
        const created: ApiKeyOut & { key: string } = {
          ...agentKey({
            id: `key-${apiKeys.length + 1}`,
            name: body.name,
            scopes: body.scopes,
            expires_at: body.expires_at,
            kind: body.kind ?? "standard",
            client: body.client ?? null,
          }),
          key: "lkap_freshly_minted_0123456789",
        };
        apiKeys.push(created);
        return { ok: true, status: 201, json: async () => created } as Response;
      }

      if (url.match(/\/api\/console\/api-keys\/[^/]+$/) && method === "DELETE") {
        const id = url.split("/").pop() as string;
        stub.deleteCalls.push(id);
        const row = apiKeys.find((k) => k.id === id);
        if (row) row.revoked_at = "2026-09-24T00:00:00Z";
        return { ok: true, status: 204, json: async () => ({}) } as Response;
      }

      if (url.includes("/api/console/api-keys")) {
        return { ok: true, status: 200, json: async () => ({ items: apiKeys, total: apiKeys.length }) } as Response;
      }

      if (url.includes("/api/console/audit")) {
        return { ok: true, status: 200, json: async () => ({ items: fixture.auditRows, total: fixture.auditRows.length }) } as Response;
      }

      return { ok: true, status: 200, json: async () => ({}) } as Response;
    }),
  );

  return stub;
}

describe("AiAgentsTab — role gating", () => {
  it("shows a read-only reason to a viewer, and no Connect button", async () => {
    stubFetch({ role: "viewer", apiKeys: [], auditRows: [] });
    renderWithClient(<AiAgentsTab />);

    expect(await screen.findByText(writeAccessReason("admin"))).toBeTruthy();
    expect(screen.queryByRole("button", { name: /connect an ai agent/i })).toBeNull();
  });

  it("shows the full tab to an admin", async () => {
    stubFetch({ role: "admin", apiKeys: [], auditRows: [] });
    renderWithClient(<AiAgentsTab />);

    expect(await screen.findByRole("button", { name: /connect an ai agent/i })).toBeTruthy();
    expect(screen.getByText("Agent keys")).toBeTruthy();
    expect(screen.getByText("Agent activity")).toBeTruthy();
  });
});

describe("ConnectAgentDialog — the full flow", () => {
  it("keeps Create key disabled until the transcript warning is acknowledged, then posts the Builder preset by default", async () => {
    const stub = stubFetch({ role: "admin", apiKeys: [], auditRows: [] });
    renderWithClient(<AiAgentsTab />);

    fireEvent.click(await screen.findByRole("button", { name: /connect an ai agent/i }));
    const createButton = await screen.findByRole("button", { name: /create key/i });
    expect((createButton as HTMLButtonElement).disabled).toBe(true);

    // Filling the name alone must not be enough — the warning gates creation.
    const nameInput = screen.getByLabelText(/^name$/i);
    fireEvent.change(nameInput, { target: { value: "My Claude Code key" } });
    expect((createButton as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(screen.getByRole("checkbox", { name: /coding agent's own transcript/i }));
    expect((createButton as HTMLButtonElement).disabled).toBe(false);

    fireEvent.click(createButton);

    await waitFor(() => expect(stub.postCalls).toHaveLength(1));
    const body = stub.postCalls[0].body as Record<string, unknown>;
    expect(body.name).toBe("My Claude Code key");
    expect(body.kind).toBe("agent");
    expect(body.client).toBe("claude-code");
    expect(body.scopes).toEqual([
      "agents:read",
      "sessions:read",
      "connections:read",
      "providers:read",
      "audit:read",
      "agents:write",
      "sessions:write",
    ]);
    // 30-day default expiry (docs/v3/PLAN-V3.md R-V3-9), within a minute of "now".
    const days = (Date.parse(body.expires_at as string) - Date.now()) / 86_400_000;
    expect(days).toBeGreaterThan(29.99);
    expect(days).toBeLessThan(30.01);

    // Step 2: the key is shown once.
    expect(await screen.findByText("lkap_freshly_minted_0123456789")).toBeTruthy();
    expect(screen.getByText(/won't be shown again/i)).toBeTruthy();
    // The default snippet tab (Claude Code, local) contains the key and api origin.
    expect(screen.getByText(/claude mcp add/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /^next$/i }));
    expect(await screen.findByText(/you're set/i)).toBeTruthy();
    expect(screen.getByText(/install_claude_skill\.sh/)).toBeTruthy();
    expect(screen.getByText(/lkap_guide/)).toBeTruthy();
  });

  it("ticking 'Allow outbound phone calls' adds calls:write to the request", async () => {
    const stub = stubFetch({ role: "admin", apiKeys: [], auditRows: [] });
    renderWithClient(<AiAgentsTab />);

    fireEvent.click(await screen.findByRole("button", { name: /connect an ai agent/i }));
    fireEvent.click(screen.getByRole("checkbox", { name: /allow outbound phone calls/i }));
    fireEvent.click(screen.getByRole("checkbox", { name: /coding agent's own transcript/i }));
    fireEvent.click(screen.getByRole("button", { name: /create key/i }));

    await waitFor(() => expect(stub.postCalls).toHaveLength(1));
    expect((stub.postCalls[0].body as { scopes: string[] }).scopes).toContain("calls:write");
  });

  it("Claude Code local snippet has the key, api origin and -s user; remote form has --transport http", async () => {
    stubFetch({ role: "admin", apiKeys: [], auditRows: [] });
    renderWithClient(<AiAgentsTab />);

    fireEvent.click(await screen.findByRole("button", { name: /connect an ai agent/i }));
    fireEvent.click(screen.getByRole("checkbox", { name: /coding agent's own transcript/i }));
    fireEvent.click(screen.getByRole("button", { name: /create key/i }));

    const snippet = await screen.findByText(/claude mcp add/);
    expect(snippet.textContent).toContain("-s user");
    expect(snippet.textContent).toContain("--transport stdio");
    expect(snippet.textContent).toContain("lkap_freshly_minted_0123456789");
  });

  it("disables the Remote choice with a hint when NEXT_PUBLIC_LKAP_MCP_PUBLIC_URL is unset", async () => {
    vi.stubEnv("NEXT_PUBLIC_LKAP_MCP_PUBLIC_URL", "");
    stubFetch({ role: "admin", apiKeys: [], auditRows: [] });
    renderWithClient(<AiAgentsTab />);

    fireEvent.click(await screen.findByRole("button", { name: /connect an ai agent/i }));
    await screen.findByText(/local \(stdio\)/i);
    const remoteRadio = screen.getByRole("radio", { name: /remote \(http\)/i });
    expect((remoteRadio as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/ask your operator to enable the remote mcp service/i)).toBeTruthy();
  });

  it("enables Remote and emits the --transport http / bearer-header snippet once the public url is set", async () => {
    vi.stubEnv("NEXT_PUBLIC_LKAP_MCP_PUBLIC_URL", "https://lkap.example.test/mcp");
    const stub = stubFetch({ role: "admin", apiKeys: [], auditRows: [] });
    renderWithClient(<AiAgentsTab />);

    fireEvent.click(await screen.findByRole("button", { name: /connect an ai agent/i }));
    const remoteRadio = await screen.findByRole("radio", { name: /remote \(http\)/i });
    expect((remoteRadio as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(remoteRadio);
    fireEvent.click(screen.getByRole("checkbox", { name: /coding agent's own transcript/i }));
    fireEvent.click(screen.getByRole("button", { name: /create key/i }));

    await waitFor(() => expect(stub.postCalls).toHaveLength(1));
    const snippet = await screen.findByText(/claude mcp add/);
    expect(snippet.textContent).toContain("--transport http");
    expect(snippet.textContent).toContain("https://lkap.example.test/mcp");
    expect(snippet.textContent).toContain("Authorization: Bearer");
  });
});

describe("AgentKeysTable", () => {
  it("shows kind/client/last use/last client/expiry and revokes through a confirm dialog", async () => {
    const stub = stubFetch({
      role: "admin",
      apiKeys: [agentKey()],
      auditRows: [],
    });
    renderWithClient(<AiAgentsTab />);

    // `ResponsiveTable` renders both the desktop table and the mobile card
    // list in the DOM at once (CSS switches which shows) — scope to the
    // `<table>` so the mobile card's repeat of the key name isn't ambiguous.
    const table = await screen.findByRole("table", { name: /agent keys/i });
    expect(within(table).getByText("My Claude Code key")).toBeTruthy();
    expect(within(table).getByText("Claude Code")).toBeTruthy(); // the Client column's label
    expect(within(table).getByText("Never")).toBeTruthy(); // last_used_at is null

    fireEvent.click(within(table).getByRole("button", { name: /revoke/i }));
    const dialog = await screen.findByRole("dialog", { name: /revoke "my claude code key"\?/i });
    fireEvent.click(within(dialog).getByRole("button", { name: /^revoke$/i }));

    await waitFor(() => expect(stub.deleteCalls).toEqual(["key-1"]));
  });
});

describe("AgentActivityTable", () => {
  it("renders only agent-originated rows, with their tool/client and a target link", async () => {
    const rows: AuditOut[] = [
      {
        id: 1,
        workspace_id: "ws1",
        actor_type: "api_key",
        actor_id: "key-1",
        action: "POST /v1/agents",
        target_type: "agents",
        target_id: "agent-abc12345",
        payload: { client: { product: "lkap-mcp", name: "claude-code", tool: "agent_create", call: "c1" } },
        ts: "2026-09-24T00:00:00Z",
      },
      {
        id: 2,
        workspace_id: "ws1",
        actor_type: "user",
        actor_id: "u1",
        action: "PUT /v1/workspaces/ws1",
        target_type: "workspace",
        target_id: "ws1",
        payload: {},
        ts: "2026-09-24T00:01:00Z",
      },
    ];
    stubFetch({ role: "admin", apiKeys: [agentKey()], auditRows: rows });
    renderWithClient(<AiAgentsTab />);

    // Scope to the desktop `<table>` — the mobile card rendering repeats the
    // tool/action text, which would otherwise make these ambiguous.
    const table = await screen.findByRole("table", { name: /agent activity/i });
    expect(within(table).getByText("agent_create")).toBeTruthy();
    expect(within(table).getByText("claude-code")).toBeTruthy();
    const link = within(table).getByRole("link", { name: /agents agent-ab/i });
    expect(link.getAttribute("href")).toBe("/console/agents/agent-abc12345");
    // The non-agent (user) row never renders.
    expect(screen.queryByText(/workspaces\/ws1/)).toBeNull();
  });

  it("shows the empty state when there is no agent activity", async () => {
    stubFetch({ role: "admin", apiKeys: [], auditRows: [] });
    renderWithClient(<AiAgentsTab />);
    expect(await screen.findByText("No agent changes yet")).toBeTruthy();
  });
});
