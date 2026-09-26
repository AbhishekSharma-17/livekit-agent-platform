import * as React from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { KbSearchPanel } from "@/components/console/knowledge/kb-search-panel";
import type { KbSearchResponse } from "@/contracts/lkap-contracts";

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

/**
 * Routes each `POST .../search` call by its `{mode, rerank}` body to a
 * fixture keyed the same way `kb-search-panel.tsx`'s `COLUMNS` are, so each
 * of the four columns can be pinned to what it actually asked for.
 */
function mockFetchByColumn(byColumn: Partial<Record<string, KbSearchResponse>>) {
  const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
    const body = init?.body ? JSON.parse(String(init.body)) : {};
    const key =
      body.mode === "hybrid" && body.rerank === "local"
        ? "hybrid_rerank"
        : body.mode === "vector" && body.rerank === "local"
          ? "vector_rerank"
          : body.mode === "hybrid"
            ? "hybrid"
            : "vector";
    const response = byColumn[key] ?? { hits: [] };
    return { ok: true, status: 200, json: async () => response } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function hit(overrides: Partial<KbSearchResponse["hits"][number]> = {}): KbSearchResponse["hits"][number] {
  return {
    chunk_id: "c-1",
    document_id: "d-1",
    filename: "handbook.md",
    score: 0.812,
    text: "The deductible applies once per claim year.",
    ...overrides,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("KbSearchPanel — four-column test search (docs/v5/PLAN-V5.md V5-10)", () => {
  it("runs all four columns and shows each one's own hits, score and locator", async () => {
    mockFetchByColumn({
      vector: { hits: [hit({ chunk_id: "v-1", filename: "vector-hit.md", score: 0.7 })] },
      hybrid: {
        hits: [
          hit({
            chunk_id: "h-1",
            filename: "hybrid-hit.md",
            score: 0.75,
            meta: { page: 3, heading_path: ["Deductibles", "Wind and hail"] },
          }),
        ],
      },
      vector_rerank: { hits: [hit({ chunk_id: "vr-1", filename: "vector-rerank-hit.md", score: 0.9 })] },
      hybrid_rerank: { hits: [hit({ chunk_id: "hr-1", filename: "hybrid-rerank-hit.md", score: 0.95 })] },
    });

    renderWithClient(<KbSearchPanel kbId="kb-1" />);

    fireEvent.change(screen.getByLabelText("Try a question"), { target: { value: "deductible" } });
    fireEvent.click(screen.getByRole("button", { name: /search/i }));

    expect(await screen.findByText("vector-hit.md")).toBeTruthy();
    expect(await screen.findByText("hybrid-hit.md")).toBeTruthy();
    expect(await screen.findByText("vector-rerank-hit.md")).toBeTruthy();
    expect(await screen.findByText("hybrid-rerank-hit.md")).toBeTruthy();

    // Column labels, plain wording — no jargon (RRF, tsvector, FTS, rerank_score).
    expect(screen.getByText("Vector")).toBeTruthy();
    expect(screen.getByText("Hybrid")).toBeTruthy();
    expect(screen.getByText("Vector + re-ranked")).toBeTruthy();
    expect(screen.getByText("Hybrid + re-ranked")).toBeTruthy();

    // Score and locator for the hybrid hit.
    expect(await screen.findByText("75%")).toBeTruthy();
    expect(await screen.findByText("page 3 · Deductibles › Wind and hail")).toBeTruthy();

    // The query term is highlighted in a result's text.
    const mark = document.querySelector("mark");
    expect(mark?.textContent?.toLowerCase()).toBe("deductible");
  });

  it("shows a single empty state when every column comes back empty", async () => {
    mockFetchByColumn({});

    renderWithClient(<KbSearchPanel kbId="kb-1" />);

    fireEvent.change(screen.getByLabelText("Try a question"), { target: { value: "asbestos" } });
    fireEvent.click(screen.getByRole("button", { name: /search/i }));

    expect(await screen.findByText("No matches")).toBeTruthy();
    expect(screen.getByText(/Try different words or upload more documents\.?/)).toBeTruthy();
  });

  it("does not search an empty query", async () => {
    const fetchMock = mockFetchByColumn({});
    renderWithClient(<KbSearchPanel kbId="kb-1" />);

    const button = screen.getByRole("button", { name: /search/i });
    expect(button).toHaveProperty("disabled", true);
    fireEvent.click(button);

    await waitFor(() => expect(fetchMock).not.toHaveBeenCalled());
  });

  it("keeps the other three columns' results when one column's request fails", async () => {
    let call = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) => {
        const body = init?.body ? JSON.parse(String(init.body)) : {};
        call += 1;
        if (body.mode === "vector" && body.rerank === "none") {
          return { ok: false, status: 500, json: async () => ({ error: { code: "kb_error", message: "boom" } }) } as Response;
        }
        return { ok: true, status: 200, json: async () => ({ hits: [hit({ chunk_id: `c-${call}` })] }) } as Response;
      }),
    );

    renderWithClient(<KbSearchPanel kbId="kb-1" />);
    fireEvent.change(screen.getByLabelText("Try a question"), { target: { value: "deductible" } });
    fireEvent.click(screen.getByRole("button", { name: /search/i }));

    // Three columns still show a hit even though the Vector column's request failed.
    await waitFor(() => expect(screen.getAllByText("handbook.md").length).toBe(3));
  });
});
