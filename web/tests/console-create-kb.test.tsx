import * as React from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CreateKbDialog } from "@/components/console/knowledge/create-kb-dialog";
import type { KnowledgeConnectionOut, KnowledgeConnectionPage } from "@/contracts/lkap-contracts";

const toasts = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock("sonner", () => ({ toast: toasts }));
vi.mock("@/components/console/lib/roles", () => ({
  useWriteAccess: () => ({ role: "builder", canWrite: true, isLoading: false }),
  writeAccessReason: () => "",
}));

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function jsonResponse(body: unknown, status = 200): Response {
  return { ok: status < 400, status, json: async () => body } as Response;
}

function connection(overrides: Partial<KnowledgeConnectionOut> = {}): KnowledgeConnectionOut {
  return {
    id: "conn-ragie",
    name: "Ragie — policies",
    kind: "ragie",
    provider_id: "ragie",
    settings: { rerank: false, recency_bias: false },
    credential_id: "cred-1",
    credential_fingerprint: "ab12",
    status: "ok",
    capabilities: { managed_search: true, hybrid: true },
    knowledge_base_count: 0,
    created_at: "2026-09-27T00:00:00Z",
    updated_at: "2026-09-27T00:00:00Z",
    ...overrides,
  };
}

function stubApi(connections: KnowledgeConnectionOut[]) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    if (url.includes("/knowledge-connections")) {
      return jsonResponse({ items: connections, total: connections.length } satisfies KnowledgeConnectionPage);
    }
    if (method === "POST" && url.includes("/knowledge-bases")) {
      const body = JSON.parse(String(init?.body));
      return jsonResponse({ id: "kb-new", ...body }, 201);
    }
    return jsonResponse({});
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function postedBody(fetchMock: ReturnType<typeof stubApi>) {
  const call = fetchMock.mock.calls.find(
    ([url, init]) => (init?.method ?? "GET") === "POST" && String(url).includes("/knowledge-bases"),
  );
  return call ? JSON.parse(String(call[1]?.body)) : undefined;
}

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

beforeEach(() => {
  // Radix radio groups measure themselves.
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
});

afterEach(() => {
  vi.unstubAllGlobals();
  toasts.success.mockReset();
  toasts.error.mockReset();
});

describe("CreateKbDialog — managed search (docs/v5/PLAN-V5.md V5-45)", () => {
  it("still creates an uploaded knowledge base with the embedder by default", async () => {
    const fetchMock = stubApi([]);
    renderWithClient(<CreateKbDialog />);

    fireEvent.click(screen.getByRole("button", { name: /new knowledge base/i }));
    fireEvent.change(await screen.findByLabelText(/^Name/), { target: { value: "Policy handbook" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(postedBody(fetchMock)).toBeTruthy());
    expect(postedBody(fetchMock)).toEqual({ name: "Policy handbook", embedder_id: "fastembed-embedding" });
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("knowledge-connections"))).toBe(false);
  });

  it("creates a managed search knowledge base with the Ragie connection and partition", async () => {
    const fetchMock = stubApi([
      connection(),
      connection({ id: "conn-qdrant", name: "Qdrant", kind: "qdrant", provider_id: "qdrant" }),
    ]);
    renderWithClient(<CreateKbDialog />);

    fireEvent.click(screen.getByRole("button", { name: /new knowledge base/i }));
    fireEvent.change(await screen.findByLabelText(/^Name/), { target: { value: "Harbor Lane" } });
    fireEvent.click(screen.getByRole("radio", { name: /managed search/i }));

    // The only Ragie connection is picked for the user; the vector store is not offered.
    const ragie = await screen.findByRole("radio", { name: /Ragie — policies/ });
    await waitFor(() => expect(ragie.getAttribute("aria-checked")).toBe("true"));
    expect(screen.queryByRole("radio", { name: /Qdrant/ })).toBeNull();
    expect(screen.queryByText("Embedder")).toBeNull();

    fireEvent.change(screen.getByLabelText(/^Ragie partition/), { target: { value: "harbor-lane" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(postedBody(fetchMock)).toBeTruthy());
    expect(postedBody(fetchMock)).toEqual({
      name: "Harbor Lane",
      kind: "external",
      connection_id: "conn-ragie",
      external_ref: "harbor-lane",
    });
  });

  it("refuses a partition Ragie would not accept, before posting", async () => {
    const fetchMock = stubApi([connection()]);
    renderWithClient(<CreateKbDialog />);

    fireEvent.click(screen.getByRole("button", { name: /new knowledge base/i }));
    fireEvent.change(await screen.findByLabelText(/^Name/), { target: { value: "Harbor Lane" } });
    fireEvent.click(screen.getByRole("radio", { name: /managed search/i }));
    await screen.findByRole("radio", { name: /Ragie — policies/ });
    fireEvent.change(screen.getByLabelText(/^Ragie partition/), { target: { value: "Harbor Lane" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(toasts.error).toHaveBeenCalled());
    expect(String(toasts.error.mock.calls[0][0])).toMatch(/lower-case/);
    expect(postedBody(fetchMock)).toBeUndefined();
  });

  it("says plainly when there is no Ragie connection yet", async () => {
    stubApi([]);
    renderWithClient(<CreateKbDialog />);

    fireEvent.click(screen.getByRole("button", { name: /new knowledge base/i }));
    fireEvent.click(await screen.findByRole("radio", { name: /managed search/i }));

    expect(await screen.findByText(/No Ragie connection yet/)).toBeTruthy();
    expect(document.body.textContent ?? "").not.toMatch(/external_ref|kind|sheet/i);
  });
});
