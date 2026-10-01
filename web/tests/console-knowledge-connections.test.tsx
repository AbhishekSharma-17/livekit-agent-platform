import * as React from "react";

import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { toast } from "sonner";

import { KnowledgeConnectionsTab } from "@/components/console/knowledge/knowledge-connections-tab";
import type { KnowledgeConnectionOut, ProviderSpec, ProvidersResponse } from "@/contracts/lkap-contracts";

/**
 * V5-24 console knowledge connections: Knowledge → Connections (moved from Settings in UI-R1)
 * (table + add/edit dialog, kind radio cards, Test connection, delete
 * naming the knowledge bases in use). No `<Toaster/>` is mounted in these
 * component tests — `sonner` is mocked and asserted on directly, the same
 * pattern `console-app-accounts.test.tsx` uses.
 */
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const nativeMatches = Element.prototype.matches;
beforeAll(() => {
  // Radix `Dialog`/`Select` check `:popover-open`/`:modal` in jsdom.
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

beforeEach(() => {
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn().mockReturnValue(false);
  Element.prototype.releasePointerCapture = vi.fn();
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.mocked(toast.success).mockClear();
  vi.mocked(toast.error).mockClear();
});

const QDRANT_PROVIDER: ProviderSpec = {
  v: 2,
  id: "qdrant",
  kind: "knowledge",
  label: "Qdrant",
  vendor: "Qdrant",
  status: "deferred",
  package: "",
  python_class: "",
  requires_credential: true,
  notes: "Keeps a knowledge base's vectors in your own Qdrant cluster; the text stays on the platform.",
  fields: [
    { name: "url", label: "Cluster address", type: "string", required: true, help: null, placeholder: "https://your-cluster.cloud.qdrant.io:6333", default: null, options: null, condition: null, positional: false, accept: null, catalog_kind: null, nested_model: null },
    { name: "collection", label: "Collection", type: "string", required: false, help: null, placeholder: null, default: "lkap_knowledge", options: null, condition: null, positional: false, accept: null, catalog_kind: null, nested_model: null },
    { name: "native_hybrid", label: "Keyword search in Qdrant", type: "boolean", required: false, help: null, placeholder: null, default: false, options: null, condition: null, positional: false, accept: null, catalog_kind: null, nested_model: null },
  ],
  secret_fields: [
    { name: "api_key", label: "Qdrant API key", type: "secret", required: true, help: "A database API key of the cluster; none for a local cluster.", placeholder: null, default: null, options: null, condition: null, positional: false, accept: null, catalog_kind: null, nested_model: null },
  ],
  models: [],
  default_model: null,
  capabilities: { video_input: false, tool_calling: false, silent_tool_reply: false, voices: [] },
} as unknown as ProviderSpec;

const VOYAGE_PROVIDER: ProviderSpec = {
  v: 2,
  id: "voyage-rerank",
  kind: "knowledge",
  label: "Voyage AI Rerank",
  vendor: "Voyage AI",
  status: "deferred",
  package: "",
  python_class: "",
  requires_credential: true,
  fields: [
    { name: "model", label: "Model", type: "model", required: false, help: null, placeholder: null, default: "rerank-2.5-lite", options: null, condition: null, positional: false, accept: null, catalog_kind: null, nested_model: null },
  ],
  secret_fields: [
    { name: "api_key", label: "Voyage AI API key", type: "secret", required: true, help: null, placeholder: null, default: null, options: null, condition: null, positional: false, accept: null, catalog_kind: null, nested_model: null },
  ],
  models: [],
  default_model: null,
  capabilities: { video_input: false, tool_calling: false, silent_tool_reply: false, voices: [] },
} as unknown as ProviderSpec;

const RAGIE_PROVIDER: ProviderSpec = {
  v: 2,
  id: "ragie",
  kind: "knowledge",
  label: "Ragie",
  vendor: "Ragie",
  status: "deferred",
  package: "",
  python_class: "",
  requires_credential: true,
  notes: "Searches documents you keep in Ragie; the platform stores none of them.",
  fields: [
    { name: "rerank", label: "Re-rank in Ragie", type: "boolean", required: false, help: null, placeholder: null, default: false, options: null, condition: null, positional: false, accept: null, catalog_kind: null, nested_model: null },
    { name: "recency_bias", label: "Prefer recent documents", type: "boolean", required: false, help: null, placeholder: null, default: false, options: null, condition: null, positional: false, accept: null, catalog_kind: null, nested_model: null },
  ],
  secret_fields: [
    { name: "api_key", label: "Ragie API key", type: "secret", required: true, help: null, placeholder: null, default: null, options: null, condition: null, positional: false, accept: null, catalog_kind: null, nested_model: null },
  ],
  models: [],
  default_model: null,
  capabilities: { video_input: false, tool_calling: false, silent_tool_reply: false, voices: [] },
} as unknown as ProviderSpec;

const PROVIDERS: ProvidersResponse = {
  providers: [QDRANT_PROVIDER, VOYAGE_PROVIDER, RAGIE_PROVIDER],
} as unknown as ProvidersResponse;

const QDRANT_CONNECTION: KnowledgeConnectionOut = {
  id: "kc1",
  name: "Prod Qdrant",
  kind: "qdrant",
  provider_id: "qdrant",
  settings: { url: "https://cluster.example:6333", collection: "lkap_knowledge", native_hybrid: false },
  credential_id: null,
  credential_fingerprint: null,
  status: "ok",
  last_checked_at: "2026-09-20T00:00:00Z",
  last_error: null,
  capabilities: { hybrid: false, filters: true, stores_text: false, namespaces: true, rerank: false, dimension: 768, version: "1.9" },
  knowledge_base_count: 1,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
};

const VOYAGE_CONNECTION: KnowledgeConnectionOut = {
  id: "kc2",
  name: "Voyage reranker",
  kind: "voyage_rerank",
  provider_id: "voyage-rerank",
  settings: { model: "rerank-2.5-lite" },
  credential_id: "cred1",
  credential_fingerprint: "…ab12",
  status: "unverified",
  last_checked_at: null,
  last_error: null,
  capabilities: { hybrid: false, filters: false, stores_text: false, namespaces: false, rerank: true, dimension: null, version: null },
  knowledge_base_count: 0,
  created_at: "2026-09-02T00:00:00Z",
  updated_at: "2026-09-02T00:00:00Z",
};

interface Call {
  url: string;
  method: string;
  body: unknown;
}

function jsonResponse(body: unknown, status = 200): Response {
  return { ok: status < 400, status, json: async () => body } as Response;
}

function stubApi(
  connections: KnowledgeConnectionOut[],
  extra?: (call: Call) => { status?: number; body?: unknown } | undefined,
  role: string = "admin",
) {
  const calls: Call[] = [];
  // A real api's `GET /credentials` reflects what was just created — the
  // Select in `CredentialPicker` needs the saved credential to actually be
  // in the (refetched) list, or Radix's own hidden native-select sync resets
  // a value with no matching option back to "" (a test-mock realism issue,
  // not a product one: a real server always answers its own just-written row).
  const createdCredentials: Array<{ id: string; provider_id: string; label: string; fingerprint: string; created_at: string; updated_at: string }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input).split("?")[0];
      const method = init?.method ?? "GET";
      const call: Call = { url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined };
      calls.push(call);

      const overridden = extra?.(call);
      if (overridden) return jsonResponse(overridden.body, overridden.status ?? 200);

      if (url.endsWith("/auth/me")) {
        return jsonResponse({
          user: { id: "u1", email: "admin@example.test" },
          workspaces: [{ id: "ws1", name: "Test workspace", slug: "test", role }],
        });
      }
      if (url.endsWith("/providers")) return jsonResponse(PROVIDERS);
      if (url.endsWith("/knowledge-connections") && method === "GET") {
        return jsonResponse({ items: connections, total: connections.length });
      }
      if (url.endsWith("/knowledge-connections") && method === "POST") {
        return jsonResponse({ ...QDRANT_CONNECTION, id: "kc-new", name: (call.body as { name: string }).name });
      }
      if (url.endsWith("/credentials") && method === "POST") {
        const body = call.body as { provider_id: string; label: string };
        const created = {
          id: `cred-${createdCredentials.length + 1}`,
          provider_id: body.provider_id,
          label: body.label,
          fingerprint: "…rag1",
          created_at: "2026-09-27T00:00:00Z",
          updated_at: "2026-09-27T00:00:00Z",
        };
        createdCredentials.push(created);
        return jsonResponse(created, 201);
      }
      if (url.endsWith("/credentials") && method === "GET") {
        return jsonResponse({ items: createdCredentials, total: createdCredentials.length });
      }
      return jsonResponse({});
    }),
  );
  return calls;
}

function renderTab(connections: KnowledgeConnectionOut[], extra?: (call: Call) => { status?: number; body?: unknown } | undefined, role?: string) {
  const calls = stubApi(connections, extra, role);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <KnowledgeConnectionsTab />
    </QueryClientProvider>,
  );
  return calls;
}

describe("KnowledgeConnectionsTab", () => {
  it("lists connections with their status, key and knowledge-base count", async () => {
    renderTab([QDRANT_CONNECTION, VOYAGE_CONNECTION]);
    // `ResponsiveTable` renders both the table and the card list in the DOM
    // (CSS switches between them), so anything in both needs `getAllByText`.
    expect((await screen.findAllByText("Prod Qdrant")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Working").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Not tested yet").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Voyage reranker").length).toBeGreaterThan(0);
    expect(screen.getByText("…ab12")).toBeTruthy();
    expect(screen.getAllByText("No key").length).toBeGreaterThan(0);
  });

  it("creates a Qdrant connection (no key needed) posting name, kind and settings", async () => {
    const calls = renderTab([]);
    await screen.findByText("No knowledge connections yet");

    fireEvent.click(screen.getByRole("button", { name: "Add connection" }));
    const dialog = await screen.findByRole("dialog");
    // Qdrant is the default kind (first vector-store card); its fields render once the
    // registry (`GET /v1/providers`) resolves.
    const urlInput = await within(dialog).findByLabelText("Cluster address");
    fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: "My cluster" } });
    fireEvent.change(urlInput, { target: { value: "https://my-cluster.example:6333" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Add connection" }));

    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/knowledge-connections"))).toBe(true));
    const post = calls.find((c) => c.method === "POST" && c.url.endsWith("/knowledge-connections"))!;
    expect(post.body).toMatchObject({
      name: "My cluster",
      kind: "qdrant",
      settings: { url: "https://my-cluster.example:6333", collection: "lkap_knowledge", native_hybrid: false },
      credential_id: null,
    });
    expect(await within(dialog).findByRole("heading", { name: '"My cluster" added' })).toBeTruthy();
    expect(within(dialog).getByRole("button", { name: "Test connection" })).toBeTruthy();
  });

  it("editing only the name sends just {name} — never resets a working connection to 'Not tested yet'", async () => {
    // `update_connection` (api) sets `status` back to `unverified` whenever
    // `settings` or `credential_id` is present in the PUT body at all, even
    // unchanged — so a bare rename must omit both.
    const calls = renderTab([QDRANT_CONNECTION]);
    await screen.findAllByText("Prod Qdrant");
    const table = within(await screen.findByRole("table", { name: "Knowledge connections" }));

    fireEvent.click(table.getByRole("button", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog");
    const nameInput = await within(dialog).findByLabelText("Name");
    expect((nameInput as HTMLInputElement).value).toBe("Prod Qdrant");
    fireEvent.change(nameInput, { target: { value: "Prod Qdrant (renamed)" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    const put = calls.find((c) => c.method === "PUT")!;
    expect(put.url).toContain("/knowledge-connections/kc1");
    expect(put.body).toEqual({ name: "Prod Qdrant (renamed)" });
  });

  it("offers Ragie grouped under 'Managed search', with its rerank/recency_bias fields and a required key (ask #234)", async () => {
    renderTab([]);
    await screen.findByText("No knowledge connections yet");

    fireEvent.click(screen.getByRole("button", { name: "Add connection" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("Managed search")).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("radio", { name: /^Ragie/ }));

    expect(await within(dialog).findByLabelText("Re-rank in Ragie")).toBeTruthy();
    expect(within(dialog).getByLabelText("Prefer recent documents")).toBeTruthy();
    // Ragie needs a key (`KEY_REQUIRED` in `knowledge_connections/settings.py`), unlike Qdrant/Weaviate.
    expect(await within(dialog).findByText(/No Ragie keys yet/)).toBeTruthy();

    // Submitting without a key is refused inline, before any request.
    fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: "Ragie prod" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Add connection" }));
    expect(await within(dialog).findByText("Choose or add a key.")).toBeTruthy();
  });

  it("creates a Ragie connection, picking an existing key and posting its rerank/recency_bias settings", async () => {
    // An existing Ragie credential, so the picker's Select has something to choose
    // (the inline "Add key" flow itself is `console-credential-picker.test.tsx`'s).
    const calls = renderTab([], (call) =>
      call.url.endsWith("/credentials") && call.method === "GET"
        ? { body: { items: [{ id: "cred-ragie", provider_id: "ragie", label: "Ragie prod key", fingerprint: "…rag1", created_at: "2026-09-27T00:00:00Z", updated_at: "2026-09-27T00:00:00Z" }], total: 1 } }
        : undefined,
    );
    await screen.findByText("No knowledge connections yet");

    fireEvent.click(screen.getByRole("button", { name: "Add connection" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(await within(dialog).findByRole("radio", { name: /^Ragie/ }));
    fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: "Ragie prod" } });
    fireEvent.click(await within(dialog).findByLabelText("Re-rank in Ragie"));

    // Pick the existing key from the Select (the same open-then-click-the-item
    // pattern `console-knowledge-tab.test.tsx` uses for the re-rank picker).
    fireEvent.click(within(dialog).getByLabelText("Key"));
    const listbox = await screen.findByRole("listbox");
    fireEvent.click(within(listbox).getByText(/Ragie prod key/));

    fireEvent.click(within(dialog).getByRole("button", { name: "Add connection" }));

    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/knowledge-connections"))).toBe(true));
    const post = calls.find((c) => c.method === "POST" && c.url.endsWith("/knowledge-connections"))!;
    expect(post.body).toMatchObject({
      name: "Ragie prod",
      kind: "ragie",
      settings: { rerank: true, recency_bias: false },
      credential_id: "cred-ragie",
    });
  });

  it("Test connection renders the fixture's collections and a dimension mismatch message", async () => {
    const message =
      "Qdrant collection 'lkap_knowledge' holds 384-dimension vectors, but knowledge bases here are built with 768-dimension vectors";
    renderTab([QDRANT_CONNECTION], (call) =>
      call.url.endsWith("/knowledge-connections/kc1/test") && call.method === "POST"
        ? {
            body: {
              ok: false,
              status: "error",
              message,
              collections: ["lkap_knowledge", "archive"],
              target: "lkap_knowledge",
              target_exists: true,
              dimension_expected: 768,
              dimension_found: 384,
              capabilities: { hybrid: false, filters: true, stores_text: false, namespaces: true, rerank: false, dimension: 384, version: "1.9" },
              checked_at: "2026-09-27T00:00:00Z",
            },
          }
        : undefined,
    );
    await screen.findAllByText("Prod Qdrant");
    // `ResponsiveTable` puts Test/Edit/Delete in both the table and the
    // mobile card (so a phone-width view stays actionable) — scope to the
    // table so each action button resolves to exactly one match.
    const table = within(await screen.findByRole("table", { name: "Knowledge connections" }));

    fireEvent.click(table.getByRole("button", { name: "Test" }));
    // Exact-string matches only (not a regex substring test): each of these
    // texts sits alone in its own element, so an exact match can't also hit
    // a wrapping element that carries extra sibling text (the status chip).
    expect(await screen.findByText(message)).toBeTruthy();
    expect(screen.getByText("lkap_knowledge, archive")).toBeTruthy();
    expect(screen.getByText("Problem")).toBeTruthy();
  });

  it("delete fails with 409 and names the knowledge base still stored", async () => {
    renderTab([QDRANT_CONNECTION], (call) =>
      call.url.endsWith("/knowledge-connections/kc1") && call.method === "DELETE"
        ? {
            status: 409,
            body: {
              error: {
                code: "conflict",
                message: "knowledge connection 'Prod Qdrant' still stores 1 knowledge base(s): Policy handbook",
                details: { knowledge_bases: [{ id: "kb1", name: "Policy handbook" }] },
              },
            },
          }
        : undefined,
    );
    await screen.findAllByText("Prod Qdrant");
    const table = within(await screen.findByRole("table", { name: "Knowledge connections" }));

    fireEvent.click(table.getByRole("button", { name: "Delete" }));
    fireEvent.click(await screen.findByRole("button", { name: "Delete connection" }));

    // The confirm dialog stays open and says why, in place: friendlyError keeps
    // the api's authored 409 sentence, capitalised and full-stopped.
    const dialog = within(await screen.findByRole("alertdialog", { name: 'Delete "Prod Qdrant"?' }));
    expect(
      await dialog.findByText(/Knowledge connection 'Prod Qdrant' still stores 1 knowledge base\(s\): Policy handbook\./),
    ).toBeTruthy();
    expect(toast.success).not.toHaveBeenCalled();
  });

  it("hides write actions for a builder (server needs admin for add/edit/delete/test)", async () => {
    renderTab([QDRANT_CONNECTION], undefined, "builder");
    await screen.findAllByText("Prod Qdrant");
    const table = within(await screen.findByRole("table", { name: "Knowledge connections" }));

    // D12: controls a builder can't use aren't rendered; a note names who can act instead.
    expect(await screen.findByText("Ask an admin to add or change connections.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Add connection" })).toBeNull();
    expect(table.queryByRole("button", { name: "Test" })).toBeNull();
    expect(table.queryByRole("button", { name: "Edit" })).toBeNull();
    expect(table.queryByRole("button", { name: "Delete" })).toBeNull();
  });
});
