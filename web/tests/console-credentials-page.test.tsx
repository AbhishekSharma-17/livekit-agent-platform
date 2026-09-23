import * as React from "react";

import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { CredentialList, credentialUsage } from "@/components/console/registry/credential-list";
import type { AgentOut, CredentialOut, ProviderSpec, ToolOut } from "@/contracts/lkap-contracts";
import providersJson from "../../contracts/generated/providers.json";

/**
 * `/console/keys` body (docs/UI_UX_SPEC.md §4.5; v2 amendments: a
 * flat list linked from Providers). The route file only renders
 * `<CredentialList />`.
 */

const REGISTRY = (providersJson as { providers: ProviderSpec[] }).providers;

const nativeMatches = Element.prototype.matches;
beforeAll(() => {
  // See console-editor-shell.test.tsx (Radix menus/dialogs in jsdom).
  Element.prototype.matches = function matches(this: Element, selector: string) {
    if (selector === ":popover-open" || selector === ":modal") return false;
    return nativeMatches.call(this, selector);
  };
});
afterAll(() => {
  Element.prototype.matches = nativeMatches;
});

const credentials: CredentialOut[] = [
  {
    id: "cred_tts",
    provider_id: "elevenlabs-tts",
    label: "Voices prod",
    fingerprint: "…a1b2",
    created_at: "2026-09-01T10:00:00Z",
    updated_at: "2026-09-01T10:00:00Z",
  },
  {
    id: "cred_llm",
    provider_id: "openai-llm",
    label: "OpenAI team key",
    fingerprint: "…c3d4",
    created_at: "2026-09-02T10:00:00Z",
    updated_at: "2026-09-02T10:00:00Z",
  },
  {
    id: "cred_bag",
    provider_id: "http-tool-secret",
    label: "CRM token",
    fingerprint: "…e5f6",
    created_at: "2026-09-03T10:00:00Z",
    updated_at: "2026-09-03T10:00:00Z",
  },
];

function agent(id: string, name: string, llmCredential: string | null): AgentOut {
  return {
    id,
    name,
    slug: id,
    pack_id: "generic",
    ui_panel_id: "generic",
    published: false,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    config: {
      pipeline: {
        mode: "cascaded",
        stt: { provider_id: "livekit-inference-stt", credential_id: null, model: null, fields: {} },
        llm: { provider_id: "openai-llm", credential_id: llmCredential, model: null, fields: {} },
        tts: { provider_id: "elevenlabs-tts", credential_id: "cred_tts", model: null, fields: {} },
        workflow_llm: { provider_id: "openai-llm", credential_id: llmCredential, model: null, fields: {} },
      },
    },
  } as unknown as AgentOut;
}

const agents = [agent("a1", "Claims intake", "cred_llm"), agent("a2", "Front desk", null)];
const tools: ToolOut[] = [
  {
    id: "t1",
    name: "lookup_customer",
    kind: "http",
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    definition: { name: "lookup_customer", description: "", parameters: {}, url: "https://crm", credential_id: "cred_bag" },
  } as unknown as ToolOut,
];

interface Call {
  url: string;
  method: string;
}

function stubApi(overrides: (call: Call) => { status: number; body: unknown } | undefined = () => undefined) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const call = { url: String(input), method: init?.method ?? "GET" };
      calls.push(call);
      const override = overrides(call);
      let status = 200;
      let body: unknown;
      if (override) ({ status, body } = override);
      else if (call.url.includes("/auth/me")) {
        body = {
          user: { id: "u1", email: "admin@example.test" },
          workspaces: [{ id: "ws1", name: "Test workspace", slug: "test", role: "admin" }],
        };
      } else if (call.url.includes("/providers")) body = { providers: REGISTRY };
      else if (call.url.includes("/credentials")) body = { items: credentials, total: credentials.length };
      else if (call.url.includes("/agents")) body = { items: agents, total: agents.length };
      else if (call.url.includes("/tools")) body = { items: tools, total: tools.length };
      else body = {};
      return { ok: status < 400, status, json: async () => body } as Response;
    }),
  );
  return calls;
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <CredentialList />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("credentialUsage", () => {
  it("joins agents over every pipeline slot (once per agent) and tools over definition.credential_id", () => {
    const usage = credentialUsage(agents, tools);
    expect(usage.get("cred_llm")?.agents).toEqual([{ id: "a1", name: "Claims intake" }]);
    expect(usage.get("cred_tts")?.agents.map((a) => a.id)).toEqual(["a1", "a2"]);
    expect(usage.get("cred_bag")?.tools).toEqual([{ id: "t1", name: "lookup_customer" }]);
    expect(usage.has("livekit-inference-stt")).toBe(false);
  });
});

describe("CredentialList", () => {
  it("lists every key in one flat table sorted by kind, with kind, fingerprint and usage", async () => {
    stubApi();
    renderPage();
    const table = await screen.findByRole("table", { name: "Credentials" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((row) => within(row).getAllByRole("cell")[0].textContent)).toEqual([
      expect.stringContaining("OpenAI team key"),
      expect.stringContaining("Voices prod"),
      expect.stringContaining("CRM token"),
    ]);
    expect(within(rows[0]).getByText("Language models")).toBeTruthy();
    expect(within(rows[0]).getByText("…c3d4")).toBeTruthy();
    await waitFor(() => expect(within(rows[1]).getByText("Used by 2 agents")).toBeTruthy());
    expect(within(rows[2]).getByText("Used by 1 tool")).toBeTruthy();
    // Fingerprints are copyable (not secrets).
    expect(within(rows[0]).getByRole("button", { name: "Copy fingerprint" })).toBeTruthy();
  });

  it("shows an empty state with the add action when there are no keys", async () => {
    stubApi((call) => (call.url.includes("/credentials") ? { status: 200, body: { items: [], total: 0 } } : undefined));
    renderPage();
    expect(await screen.findByText("No credentials yet")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Add credential" })).toBeTruthy();
  });

  it("tests a key from the row menu and shows the result inline", async () => {
    const calls = stubApi((call) =>
      call.url.endsWith("/credentials/cred_llm/test")
        ? { status: 200, body: { ok: false, message: "Incorrect API key provided" } }
        : undefined,
    );
    renderPage();
    const table = await screen.findByRole("table", { name: "Credentials" });
    const row = within(table).getAllByRole("row")[1];
    fireEvent.keyDown(within(row).getByRole("button", { name: "Actions for OpenAI team key" }), { key: "Enter" });
    fireEvent.click(await screen.findByRole("menuitem", { name: "Test" }));

    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/credentials/cred_llm/test"))).toBe(true));
    expect(await within(row).findByText("Incorrect API key provided")).toBeTruthy();
  });

  it("explains a 409 on delete with links to the agents that still use the key", async () => {
    stubApi((call) =>
      call.method === "DELETE"
        ? { status: 409, body: { error: { code: "conflict", message: "credential is referenced by 1 agent" } } }
        : undefined,
    );
    renderPage();
    const table = await screen.findByRole("table", { name: "Credentials" });
    // Wait for the usage join so the dialog can name the agents.
    await waitFor(() => expect(within(table).getByText("Used by 1 agent")).toBeTruthy());
    const row = within(table).getAllByRole("row")[1];
    fireEvent.keyDown(within(row).getByRole("button", { name: "Actions for OpenAI team key" }), { key: "Enter" });
    fireEvent.click(await screen.findByRole("menuitem", { name: "Delete" }));

    const dialog = await screen.findByRole("dialog", { name: "Delete OpenAI team key" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete credential" }));
    const alert = await within(dialog).findByRole("alert");
    expect(alert.textContent).toContain("change those agents first");
    expect(within(alert).getByRole("link", { name: "Claims intake" }).getAttribute("href")).toBe(
      "/console/agents/a1?section=providers",
    );
  });

  it("opens the dialog in rotate mode from the row menu", async () => {
    stubApi();
    renderPage();
    const table = await screen.findByRole("table", { name: "Credentials" });
    const row = within(table).getAllByRole("row")[1];
    fireEvent.keyDown(within(row).getByRole("button", { name: "Actions for OpenAI team key" }), { key: "Enter" });
    fireEvent.click(await screen.findByRole("menuitem", { name: "Rotate" }));
    expect(await screen.findByRole("heading", { name: "Rotate OpenAI team key" })).toBeTruthy();
    expect(screen.getByText("Current key")).toBeTruthy();
  });
});
