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

function mockFetch(response: KbSearchResponse) {
  const fetchMock = vi.fn(async () => ({ ok: true, status: 200, json: async () => response }) as Response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("KbSearchPanel — 'Try a question' (docs/UI_UX_SPEC.md §7.7 item 3)", () => {
  it("highlights the query terms in the result text and shows the score", async () => {
    mockFetch({
      hits: [
        {
          chunk_id: "c-1",
          document_id: "d-1",
          filename: "handbook.md",
          score: 0.812,
          text: "The deductible applies once per claim year.",
        },
      ],
    });

    renderWithClient(<KbSearchPanel kbId="kb-1" />);

    fireEvent.change(screen.getByLabelText("Try a question"), { target: { value: "deductible" } });
    fireEvent.click(screen.getByRole("button", { name: /search/i }));

    expect(await screen.findByText("handbook.md")).toBeTruthy();
    expect(await screen.findByText("81")).toBeTruthy();

    const mark = document.querySelector("mark");
    expect(mark?.textContent?.toLowerCase()).toBe("deductible");
  });

  it("shows an empty-result hint when there are no hits", async () => {
    mockFetch({ hits: [] });

    renderWithClient(<KbSearchPanel kbId="kb-1" />);

    fireEvent.change(screen.getByLabelText("Try a question"), { target: { value: "asbestos" } });
    fireEvent.click(screen.getByRole("button", { name: /search/i }));

    expect(await screen.findByText("No matches")).toBeTruthy();
    expect(screen.getByText(/Try different words or upload more documents\.?/)).toBeTruthy();
  });

  it("does not search an empty query", async () => {
    const fetchMock = mockFetch({ hits: [] });
    renderWithClient(<KbSearchPanel kbId="kb-1" />);

    const button = screen.getByRole("button", { name: /search/i });
    expect(button).toHaveProperty("disabled", true);
    fireEvent.click(button);

    await waitFor(() => expect(fetchMock).not.toHaveBeenCalled());
  });
});
