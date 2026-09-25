import * as React from "react";

import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
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

// jsdom has no ResizeObserver; `EnableComposioDialog`'s override `Checkbox`
// (@radix-ui/react-checkbox, via `@radix-ui/react-use-size`) needs one the
// moment the Rotate dialog opens (see console-http-tool-editor.test.tsx for
// the Select-flavoured version of this same gap). Re-applied per test since
// `vi.unstubAllGlobals()` in `afterEach` clears it.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
beforeEach(() => {
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
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
  body: Record<string, unknown> | undefined;
}

/** `call.body?.foo` with a type jsdom's parsed JSON never gives us for free. */
function bodyField(call: Call, path: string[]): unknown {
  let value: unknown = call.body;
  for (const key of path) {
    if (typeof value !== "object" || value === null) return undefined;
    value = (value as Record<string, unknown>)[key];
  }
  return value;
}

function stubApi(overrides: (call: Call) => { status: number; body: unknown } | undefined = () => undefined) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const requestBody = init?.body ? JSON.parse(String(init.body)) : undefined;
      const call = { url: String(input), method: init?.method ?? "GET", body: requestBody };
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

  it("shows the shared OpenRouter key as a vendor-level key with a 'Used by' chip per kind it covers", async () => {
    // R-V4-7's follow-up (see `credentialDisplay` in `provider-meta.ts`): a
    // key stored under `openrouter-llm` (the credential home for
    // `openrouter-stt`/`-tts`/`-embedding`/`-image-gen`) used to read as
    // "OpenRouter" filed under "Language models" — indistinguishable from
    // an ordinary LLM-only key, which is exactly the bug report ("it still
    // registers it as a language model").
    const openrouterCredential: CredentialOut = {
      id: "cred_or",
      provider_id: "openrouter-llm",
      label: "OpenRouter key",
      fingerprint: "…or01",
      created_at: "2026-09-04T10:00:00Z",
      updated_at: "2026-09-04T10:00:00Z",
    };
    stubApi((call) =>
      call.url.includes("/credentials") && call.method === "GET"
        ? { status: 200, body: { items: [openrouterCredential], total: 1 } }
        : undefined,
    );
    renderPage();
    const table = await screen.findByRole("table", { name: "Credentials" });
    const row = within(table).getAllByRole("row")[1];
    // The credential's own name stays as given; the subtitle beneath it is
    // the vendor ("OpenRouter"), not the home's own kind-scoped label.
    expect(within(row).getByText("OpenRouter key")).toBeTruthy();
    expect(within(row).getByText("OpenRouter")).toBeTruthy();
    // The Kind column shows a chip per kind the shared key actually covers
    // — not just "Language models", the literal kind of its own registry
    // entry, which is what made it look LLM-only.
    for (const kind of ["Speech-to-text", "Language models", "Text-to-speech", "Image generation", "Embeddings"]) {
      expect(within(row).getByText(kind)).toBeTruthy();
    }
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

/**
 * The Composio row (docs/v5/COMPOSIO.md §6, D-V5-C13; docs/v5/_asks.md #4):
 * the workspace's key is an ordinary row here (`provider_id: "composio"`),
 * just with Validate/Rotate/Disable/Remove key instead of the generic
 * Test/Rotate/Rename/Delete menu; its per-app connections
 * (`provider_id: "tool-provider-account"`) are not vault keys and are
 * filtered out client-side until the api refuses them itself (ask #2).
 */
describe("Composio row", () => {
  const composioCredential: CredentialOut = {
    id: "cred_composio",
    provider_id: "composio",
    label: "Composio key · September 2026",
    fingerprint: "…c0c0",
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
  };
  const connectionRow: CredentialOut = {
    id: "conn_github",
    provider_id: "tool-provider-account",
    label: "",
    fingerprint: "…c1c1",
    created_at: "2026-09-05T00:00:00Z",
    updated_at: "2026-09-05T00:00:00Z",
  };

  function stubWithComposio(overrides: (call: Call) => { status: number; body: unknown } | undefined = () => undefined) {
    return stubApi((call) => {
      if (call.url.includes("/credentials") && call.method === "GET") {
        return { status: 200, body: { items: [composioCredential, connectionRow], total: 2 } };
      }
      if (call.url.endsWith("/tool-providers/composio/status")) {
        return {
          status: 200,
          body: {
            enabled: true,
            credential_id: "cred_composio",
            fingerprint: "…c0c0",
            last_test_ok: true,
            last_test_at: "2026-09-20T10:00:00Z",
            last_test_message: "Key works",
            connections: 1,
            paused_tools: 0,
          },
        };
      }
      return overrides(call);
    });
  }

  it("hides the connection row and shows the key row with its own chip", async () => {
    stubWithComposio();
    renderPage();
    const table = await screen.findByRole("table", { name: "Credentials" });
    expect(within(table).queryByText("conn_github")).toBeNull();
    expect(within(table).getByText("Composio key · September 2026")).toBeTruthy();
    expect(await within(table).findByText("Valid")).toBeTruthy();
  });

  it("Validate posts the stored-key test", async () => {
    const calls = stubWithComposio();
    renderPage();
    const table = await screen.findByRole("table", { name: "Credentials" });
    const row = within(table).getAllByRole("row").find((r) => within(r).queryByText("Composio key · September 2026"))!;
    fireEvent.keyDown(within(row).getByRole("button", { name: "Actions for Composio key · September 2026" }), { key: "Enter" });
    fireEvent.click(await screen.findByRole("menuitem", { name: "Validate" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/credentials/cred_composio/test"))).toBe(true));
  });

  it("Rotate opens the dialog for the same credential id", async () => {
    const calls = stubWithComposio((call) => (call.url.endsWith("/key/test") ? { status: 200, body: { ok: true, account_name: null, project_name: "Acme", toolkits_count: 10, message: "Key works" } } : undefined));
    renderPage();
    const table = await screen.findByRole("table", { name: "Credentials" });
    const row = within(table).getAllByRole("row").find((r) => within(r).queryByText("Composio key · September 2026"))!;
    fireEvent.keyDown(within(row).getByRole("button", { name: "Actions for Composio key · September 2026" }), { key: "Enter" });
    fireEvent.click(await screen.findByRole("menuitem", { name: "Rotate" }));
    const dialog = await screen.findByRole("dialog", { name: "Rotate the Composio key" });
    fireEvent.change(within(dialog).getByLabelText("Composio API key"), { target: { value: "sk_new" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Test key" }));
    await within(dialog).findByText(/Connected to/);
    fireEvent.click(within(dialog).getByRole("button", { name: "Replace key" }));
    await waitFor(() =>
      expect(calls.some((c) => c.method === "PUT" && c.url.endsWith("/credentials/cred_composio") && bodyField(c, ["secrets", "api_key"]) === "sk_new")).toBe(true),
    );
  });

  it("Disable confirms, then posts disable", async () => {
    const calls = stubWithComposio();
    renderPage();
    const table = await screen.findByRole("table", { name: "Credentials" });
    const row = within(table).getAllByRole("row").find((r) => within(r).queryByText("Composio key · September 2026"))!;
    fireEvent.keyDown(within(row).getByRole("button", { name: "Actions for Composio key · September 2026" }), { key: "Enter" });
    fireEvent.click(await screen.findByRole("menuitem", { name: "Disable" }));
    const dialog = await screen.findByRole("dialog", { name: "Turn off Apps?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Turn off" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/tool-providers/composio/disable"))).toBe(true));
  });

  it("Remove key opens the generic delete confirm", async () => {
    stubWithComposio();
    renderPage();
    const table = await screen.findByRole("table", { name: "Credentials" });
    const row = within(table).getAllByRole("row").find((r) => within(r).queryByText("Composio key · September 2026"))!;
    fireEvent.keyDown(within(row).getByRole("button", { name: "Actions for Composio key · September 2026" }), { key: "Enter" });
    fireEvent.click(await screen.findByRole("menuitem", { name: "Remove key" }));
    expect(await screen.findByRole("dialog", { name: "Delete Composio key · September 2026" })).toBeTruthy();
  });
});
