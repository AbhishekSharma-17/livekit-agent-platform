import * as React from "react";

import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { toast } from "sonner";

import { KnowledgeConnectionsTab } from "@/components/console/settings/knowledge-connections-tab";
import type { KnowledgeConnectionOut, ProviderSpec, ProvidersResponse } from "@/contracts/lkap-contracts";

/**
 * V5-24 console knowledge connections: Settings → Knowledge connections
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

const PROVIDERS: ProvidersResponse = { providers: [QDRANT_PROVIDER, VOYAGE_PROVIDER] } as unknown as ProvidersResponse;

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
      if (url.includes("/credentials")) return jsonResponse({ items: [] });
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

    fireEvent.click(screen.getByRole("button", { name: "Test" }));
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

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    fireEvent.click(await screen.findByRole("button", { name: "Delete connection" }));

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        'Couldn\'t delete "Prod Qdrant" — knowledge connection \'Prod Qdrant\' still stores 1 knowledge base(s): Policy handbook',
      ),
    );
  });

  it("hides write actions for a builder (server needs admin for add/edit/delete/test)", async () => {
    renderTab([QDRANT_CONNECTION], undefined, "builder");
    await screen.findAllByText("Prod Qdrant");

    expect(screen.getByRole("button", { name: "Add connection" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "Edit" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "Delete" }).hasAttribute("disabled")).toBe(true);
  });
});
