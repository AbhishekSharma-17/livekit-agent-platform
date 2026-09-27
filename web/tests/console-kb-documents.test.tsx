import * as React from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { KbDocuments } from "@/components/console/knowledge/kb-documents";
import { KbDetail } from "@/components/console/knowledge/kb-detail";
import { CreateKbDialog } from "@/components/console/knowledge/create-kb-dialog";
import { KB_UPLOAD_MAX_BYTES } from "@/components/console/lib/upload";
import type { KbDocumentOut, KbDocumentPage, KbOut, KbSourceOut, KnowledgeConnectionOut } from "@/contracts/lkap-contracts";

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function document_(overrides: Partial<KbDocumentOut>): KbDocumentOut {
  return {
    id: "doc-1",
    kb_id: "kb-1",
    filename: "handbook.md",
    mime: "text/markdown",
    bytes: 2048,
    status: "ready",
    chunk_count: 3,
    error: null,
    created_at: "2026-09-19T00:00:00Z",
    ...overrides,
  };
}

/** Big enough that jsdom's real `File.size` already exceeds the 25 MB cap. */
function bigFile(name: string, bytes: number): File {
  return new File([new Uint8Array(bytes)], name, { type: "text/plain" });
}

function jsonResponse(body: unknown, status = 200): Response {
  return { ok: status < 400, status, json: async () => body } as Response;
}

function mockFetch({
  documents,
  onUpload,
}: {
  documents: KbDocumentOut[];
  onUpload?: () => void;
}) {
  const page: KbDocumentPage = { items: documents, total: documents.length };
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    if (method === "GET" && url.includes("/documents")) {
      return jsonResponse(page);
    }
    if (method === "POST" && url.includes("/documents")) {
      onUpload?.();
      return jsonResponse(document_({ id: "doc-new", filename: "new.md", status: "pending" }), 202);
    }
    if (url.includes("/auth/me")) {
      return jsonResponse({
        user: { id: "u1", email: "admin@example.test" },
        workspaces: [{ id: "ws1", name: "Test workspace", slug: "test", role: "admin" }],
      });
    }
    return jsonResponse({}, 200);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("KbDocuments — status rendering (docs/UI_UX_SPEC.md §7.7)", () => {
  it("renders pending as an 'Indexing…' chip, ready as 'Ready' and failed with its inline error", async () => {
    mockFetch({
      documents: [
        document_({ id: "d-pending", filename: "a.md", status: "pending" }),
        document_({ id: "d-ready", filename: "b.md", status: "ready" }),
        document_({ id: "d-failed", filename: "c.pdf", status: "failed", error: "Could not extract text" }),
      ],
    });

    renderWithClient(<KbDocuments kbId="kb-1" />);

    // The desktop table and the mobile card list both render the same rows
    // (docs/UI_UX_SPEC.md §2.7's `ResponsiveTable`, switched by CSS, not by
    // conditional rendering) — scope to the table so each status appears once.
    const table = within(await screen.findByRole("table", { name: "Documents" }));

    expect(await table.findByText("Indexing…")).toBeTruthy();
    expect(table.getByText("Ready")).toBeTruthy();
    expect(table.getByText("Failed")).toBeTruthy();
    expect(table.getByText("Could not extract text")).toBeTruthy();
    // Failed documents offer a retry.
    expect(table.getByText("Try again")).toBeTruthy();
  });

  it("shows the empty state when there are no documents", async () => {
    mockFetch({ documents: [] });
    renderWithClient(<KbDocuments kbId="kb-1" />);
    expect(await screen.findByText("No documents yet")).toBeTruthy();
  });
});

describe("KbDocuments — progress and file type (V5-10)", () => {
  it("shows a percentage while a document is pending with a progress value, and its file type", async () => {
    mockFetch({
      documents: [
        document_({ id: "d-pending", filename: "a.pdf", mime: "application/pdf", status: "pending", progress: 0.42 }),
        document_({ id: "d-ready", filename: "b.txt", mime: "text/plain", status: "ready" }),
      ],
    });

    renderWithClient(<KbDocuments kbId="kb-1" />);
    const table = within(await screen.findByRole("table", { name: "Documents" }));

    expect(await table.findByText("Indexing 42%")).toBeTruthy();
    expect(table.getByText("PDF")).toBeTruthy();
    expect(table.getByText("Text")).toBeTruthy();
  });
});

describe("KbDocuments — drop zone", () => {
  it("uploads a dropped file and refreshes the document list", async () => {
    const onUpload = vi.fn();
    const fetchMock = mockFetch({ documents: [], onUpload });

    renderWithClient(<KbDocuments kbId="kb-1" />);

    const dropzone = await screen.findByRole("button", { name: /drop files here/i });
    const file = new File(["policy text"], "policy.md", { type: "text/markdown" });

    fireEvent.drop(dropzone, { dataTransfer: { files: [file] } });

    await waitFor(() => expect(onUpload).toHaveBeenCalledTimes(1));

    const uploadCall = fetchMock.mock.calls.find(
      ([url, init]) => (init?.method ?? "GET") === "POST" && String(url).includes("/documents"),
    );
    expect(uploadCall).toBeTruthy();
    expect(uploadCall?.[1]?.body).toBeInstanceOf(FormData);
  });
});

describe("KbDocuments — 25 MB upload cap (docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md)", () => {
  it("rejects an oversized file locally with the cap message and never calls the api", async () => {
    const onUpload = vi.fn();
    const fetchMock = mockFetch({ documents: [], onUpload });

    renderWithClient(<KbDocuments kbId="kb-1" />);

    const dropzone = await screen.findByRole("button", { name: /drop files here/i });
    const huge = bigFile("scan.pdf", KB_UPLOAD_MAX_BYTES + 1);

    fireEvent.drop(dropzone, { dataTransfer: { files: [huge] } });

    expect(await screen.findByText(/larger than the 25 MB upload limit/i)).toBeTruthy();
    expect(onUpload).not.toHaveBeenCalled();
    const uploadCalls = fetchMock.mock.calls.filter(
      ([url, init]) => (init?.method ?? "GET") === "POST" && String(url).includes("/documents"),
    );
    expect(uploadCalls).toHaveLength(0);
  });
});

describe("KbDetail — 'Stored in' (V5-24: read-only, fixed once the knowledge base exists)", () => {
  function kb(overrides: Partial<KbOut>): KbOut {
    return {
      id: "kb-1",
      name: "Policy handbook",
      description: "",
      embedder_id: "fastembed-embedding",
      chunk_count: 0,
      document_count: 0,
      created_at: "2026-09-01T00:00:00Z",
      updated_at: "2026-09-01T00:00:00Z",
      ...overrides,
    };
  }

  function mockKbDetailFetch({
    theKb,
    connections = [],
    source,
  }: {
    theKb: KbOut;
    connections?: KnowledgeConnectionOut[];
    source?: KbSourceOut;
  }) {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input).split("?")[0];
        if (url.endsWith("/knowledge-bases/kb-1/source")) return jsonResponse(source ?? {});
        if (url.endsWith("/knowledge-bases/kb-1")) return jsonResponse(theKb);
        if (url.endsWith("/knowledge-connections")) return jsonResponse({ items: connections, total: connections.length });
        if (url.endsWith("/providers")) {
          return jsonResponse({
            providers: [
              { id: "qdrant", kind: "knowledge", label: "Qdrant", vendor: "Qdrant", package: "", python_class: "" },
            ],
          });
        }
        if (url.endsWith("/documents")) return jsonResponse({ items: [], total: 0 });
        return jsonResponse({});
      }),
    );
  }

  it("shows 'This platform' when the knowledge base has no connection", async () => {
    mockKbDetailFetch({ theKb: kb({ connection_id: null }) });
    renderWithClient(<KbDetail kbId="kb-1" />);
    expect(await screen.findByText("This platform")).toBeTruthy();
  });

  it("names the connection and its kind when the knowledge base stores vectors through one", async () => {
    const connection: KnowledgeConnectionOut = {
      id: "kc1",
      name: "Prod Qdrant",
      kind: "qdrant",
      provider_id: "qdrant",
      settings: { url: "https://cluster.example:6333", collection: "lkap_knowledge", native_hybrid: false },
      credential_id: null,
      credential_fingerprint: null,
      status: "ok",
      last_checked_at: null,
      last_error: null,
      capabilities: { hybrid: false, filters: true, stores_text: false, namespaces: true, rerank: false, dimension: 768, version: null },
      knowledge_base_count: 1,
      created_at: "2026-09-01T00:00:00Z",
      updated_at: "2026-09-01T00:00:00Z",
    };
    mockKbDetailFetch({ theKb: kb({ connection_id: "kc1", external_ref: "lkap_knowledge" }), connections: [connection] });
    renderWithClient(<KbDetail kbId="kb-1" />);
    const storedIn = await screen.findByText("Prod Qdrant (Qdrant)");
    // Fixed once created: it's a plain description-list value, not a button or a link.
    expect(storedIn.closest("dd")).toBeTruthy();
    expect(storedIn.querySelector("button, a")).toBeNull();
  });
});

describe("KbDetail — managed search (Ragie, V5-45/ask #232): no upload, its own source panel", () => {
  function kb(overrides: Partial<KbOut>): KbOut {
    return {
      id: "kb-1",
      name: "Harbor Lane",
      description: "",
      embedder_id: "fastembed-embedding",
      chunk_count: 0,
      document_count: 42,
      kind: "external",
      external_ref: "policies",
      created_at: "2026-09-01T00:00:00Z",
      updated_at: "2026-09-01T00:00:00Z",
      ...overrides,
    };
  }

  function mockExternalKbFetch(source: KbSourceOut) {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input).split("?")[0];
        if (url.endsWith("/knowledge-bases/kb-1/source")) return jsonResponse(source);
        if (url.endsWith("/knowledge-bases/kb-1")) return jsonResponse(kb({}));
        if (url.endsWith("/knowledge-connections")) return jsonResponse({ items: [], total: 0 });
        if (url.endsWith("/providers")) return jsonResponse({ providers: [] });
        return jsonResponse({});
      }),
    );
  }

  it("shows the partition and live document count instead of Embedder/Chunks, and hides the upload controls", async () => {
    mockExternalKbFetch({
      ok: true,
      kind: "ragie",
      external_ref: "policies",
      document_count: 42,
      last_synced_at: "2026-09-26T00:00:00Z",
      checked_at: "2026-09-27T00:00:00Z",
    });
    renderWithClient(<KbDetail kbId="kb-1" />);

    expect(await screen.findByText("Source")).toBeTruthy();
    // The description list's own "Documents" count and the source card's live
    // count both read "42 documents" (the same number, two honest places).
    expect((await screen.findAllByText("42 documents")).length).toBeGreaterThan(0);
    expect(screen.getByText("Connected")).toBeTruthy();
    expect(screen.getByText("Partition")).toBeTruthy();
    expect(screen.getByText("policies")).toBeTruthy();

    // No Embedder column for a knowledge base with no local embedder, and no upload/import affordance.
    expect(screen.queryByText("Embedder")).toBeNull();
    expect(screen.queryByRole("button", { name: /drop files here/i })).toBeNull();
  });

  it("shows a Problem status and the vendor's message when the source is unreachable", async () => {
    mockExternalKbFetch({ ok: false, kind: "ragie", message: "the key for this connection was deleted", checked_at: "2026-09-27T00:00:00Z" });
    renderWithClient(<KbDetail kbId="kb-1" />);

    expect(await screen.findByText("Problem")).toBeTruthy();
    expect(screen.getByText("the key for this connection was deleted")).toBeTruthy();
  });
});

describe("KbList — 'Managed search' chip (V5-45/ask #232)", () => {
  it("shows a Managed search chip for an external knowledge base, in both the table and the phone card", async () => {
    const { KbList } = await import("@/components/console/knowledge/kb-list");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input).split("?")[0];
        if (url.endsWith("/knowledge-bases")) {
          return jsonResponse({
            items: [
              {
                id: "kb-1",
                name: "Harbor Lane",
                description: "",
                embedder_id: "fastembed-embedding",
                chunk_count: 0,
                document_count: 42,
                kind: "external",
                external_ref: "policies",
                created_at: "2026-09-01T00:00:00Z",
                updated_at: "2026-09-01T00:00:00Z",
              },
            ],
            total: 1,
          });
        }
        return jsonResponse({});
      }),
    );
    renderWithClient(<KbList />);
    expect((await screen.findAllByText("Managed search")).length).toBeGreaterThan(0);
  });
});

describe("CreateKbDialog — 'Where is this knowledge stored?' (V5-24)", () => {
  const QDRANT_CONNECTION: KnowledgeConnectionOut = {
    id: "kc1",
    name: "Prod Qdrant",
    kind: "qdrant",
    provider_id: "qdrant",
    settings: { url: "https://cluster.example:6333", collection: "lkap_knowledge", native_hybrid: false },
    credential_id: null,
    credential_fingerprint: null,
    status: "ok",
    last_checked_at: null,
    last_error: null,
    capabilities: { hybrid: false, filters: true, stores_text: false, namespaces: true, rerank: false, dimension: 768, version: null },
    knowledge_base_count: 0,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
  };

  beforeEach(() => {
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    );
    Element.prototype.scrollIntoView = vi.fn();
    Element.prototype.hasPointerCapture = vi.fn().mockReturnValue(false);
    Element.prototype.releasePointerCapture = vi.fn();
  });

  interface Call {
    url: string;
    method: string;
    body: unknown;
  }

  function mockCreateKbFetch(connections: KnowledgeConnectionOut[]) {
    const calls: Call[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input).split("?")[0];
        const method = init?.method ?? "GET";
        const call: Call = { url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined };
        calls.push(call);
        if (url.endsWith("/knowledge-connections")) return jsonResponse({ items: connections, total: connections.length });
        if (url.endsWith("/knowledge-bases") && method === "POST") {
          const created: KbOut = {
            id: "kb-new",
            name: (call.body as { name: string }).name,
            description: "",
            embedder_id: "fastembed-embedding",
            chunk_count: 0,
            document_count: 0,
            created_at: "2026-09-01T00:00:00Z",
            updated_at: "2026-09-01T00:00:00Z",
          };
          return jsonResponse(created, 201);
        }
        if (url.endsWith("/auth/me")) {
          return jsonResponse({
            user: { id: "u1", email: "admin@example.test" },
            workspaces: [{ id: "ws1", name: "Test workspace", slug: "test", role: "admin" }],
          });
        }
        return jsonResponse({});
      }),
    );
    return calls;
  }

  /** The trigger starts `disabled` until `useWriteAccess()`'s `/auth/me` resolves. */
  async function openDialog() {
    const trigger = await screen.findByRole("button", { name: /New knowledge base/i });
    await waitFor(() => expect(trigger.hasAttribute("disabled")).toBe(false));
    fireEvent.click(trigger);
    return screen.findByRole("dialog");
  }

  it("posts connection_id when 'A knowledge connection' is picked (auto-selects the only one)", async () => {
    const calls = mockCreateKbFetch([QDRANT_CONNECTION]);
    renderWithClient(<CreateKbDialog />);

    const dialog = await openDialog();
    fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: "Claims policies" } });
    fireEvent.click(within(dialog).getByRole("radio", { name: /^A knowledge connection/ }));
    // The connections list is fetched lazily, once this choice is picked; wait for
    // the only one to be auto-selected before submitting.
    await waitFor(() =>
      expect(within(dialog).getByRole("radio", { name: "Prod Qdrant" }).getAttribute("aria-checked")).toBe("true"),
    );
    fireEvent.click(within(dialog).getByRole("button", { name: "Create" }));

    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/knowledge-bases"))).toBe(true));
    const post = calls.find((c) => c.method === "POST" && c.url.endsWith("/knowledge-bases"))!;
    expect(post.body).toMatchObject({ name: "Claims policies", connection_id: "kc1" });
  });

  it("omits connection_id entirely when Platform default is left selected, and never fetches the connections list", async () => {
    const calls = mockCreateKbFetch([QDRANT_CONNECTION]);
    renderWithClient(<CreateKbDialog />);

    const dialog = await openDialog();
    fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: "Claims policies" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Create" }));

    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/knowledge-bases"))).toBe(true));
    const post = calls.find((c) => c.method === "POST" && c.url.endsWith("/knowledge-bases"))!;
    expect(post.body).toEqual({ name: "Claims policies", embedder_id: "fastembed-embedding" });
    expect(calls.some((c) => c.url.endsWith("/knowledge-connections"))).toBe(false);
  });

  it("says plainly when there are no knowledge connections yet, with a link to add one", async () => {
    mockCreateKbFetch([]);
    renderWithClient(<CreateKbDialog />);

    const dialog = await openDialog();
    fireEvent.click(within(dialog).getByRole("radio", { name: /^A knowledge connection/ }));

    expect(await within(dialog).findByText(/No knowledge connections yet\./)).toBeTruthy();
    expect(within(dialog).getByRole("link", { name: "Add one in Settings" }).getAttribute("href")).toBe(
      "/console/settings?tab=knowledge-connections",
    );
  });
});
