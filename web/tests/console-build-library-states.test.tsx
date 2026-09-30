import * as React from "react";

import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { DatasetDetail } from "@/components/console/datasets/dataset-detail";
import { DatasetList } from "@/components/console/datasets/dataset-list";
import { KbList } from "@/components/console/knowledge/kb-list";
import type { DatasetOut, KbOut } from "@/contracts/lkap-contracts";

/**
 * Build library states (S4, docs/ui/DESIGN-SYSTEM.md sections 8 and 9): list
 * search appears at six items, matches accent-insensitively with highlights,
 * has its own "no matches" state with Clear filters, and a failed import on
 * the detail page is a page-level alert that never shows technical text.
 */
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function jsonResponse(body: unknown, status = 200): Response {
  return { ok: status < 400, status, json: async () => body } as Response;
}

function dataset(overrides: Partial<DatasetOut>): DatasetOut {
  return {
    id: "ds_1",
    name: "Policy directory",
    slug: "policy-directory",
    format: "csv",
    columns: [],
    key_columns: [{ name: "policy_number", type: "string" }],
    row_count: 42,
    sha256: "abc",
    status: "ready",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function stubApi(routes: Record<string, unknown>, role: "admin" | "viewer" = "admin") {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input).split("?")[0];
      if (url.includes("auth/me")) {
        return jsonResponse({ user: { id: "u1", email: "a@b.test" }, workspaces: [{ id: "w1", name: "W", slug: "w", role }] });
      }
      for (const [suffix, body] of Object.entries(routes)) {
        if (url.endsWith(suffix)) return jsonResponse(body);
      }
      return jsonResponse({ items: [], total: 0 });
    }),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

const SIX = [
  dataset({ id: "d1", name: "Policy directory" }),
  dataset({ id: "d2", name: "Café menu", key_columns: [{ name: "item", type: "string" }] }),
  dataset({ id: "d3", name: "Branch phones", key_columns: [{ name: "branch", type: "string" }] }),
  dataset({ id: "d4", name: "Agents on call", key_columns: [{ name: "agent", type: "string" }] }),
  dataset({ id: "d5", name: "Claim codes", key_columns: [{ name: "code", type: "string" }] }),
  dataset({ id: "d6", name: "Holiday hours", key_columns: [{ name: "date", type: "string" }] }),
];

describe("list search (datasets)", () => {
  it("appears at six items, matches without accents and highlights the match", async () => {
    stubApi({ "/datasets": { items: SIX, total: SIX.length } });
    renderWithClient(<DatasetList />);

    const search = await screen.findByRole("searchbox", { name: "Search lookup tables" });
    fireEvent.change(search, { target: { value: "cafe" } });

    const table = screen.getByRole("table", { name: "Lookup tables" });
    await waitFor(() => expect(within(table).queryByText("Policy directory")).toBeNull());
    const mark = table.querySelector("mark");
    expect(mark?.textContent).toBe("Café");
  });

  it("shows a distinct no-matches state, and Clear filters brings every row back", async () => {
    stubApi({ "/datasets": { items: SIX, total: SIX.length } });
    renderWithClient(<DatasetList />);

    fireEvent.change(await screen.findByRole("searchbox", { name: "Search lookup tables" }), { target: { value: "zebra" } });
    expect(await screen.findByText("No lookup tables match “zebra”")).toBeTruthy();
    expect(screen.queryByText("No lookup tables yet")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    const table = await screen.findByRole("table", { name: "Lookup tables" });
    expect(within(table).getByText("Holiday hours")).toBeTruthy();
  });

  it("Escape clears the search", async () => {
    stubApi({ "/datasets": { items: SIX, total: SIX.length } });
    renderWithClient(<DatasetList />);

    const search = (await screen.findByRole("searchbox", { name: "Search lookup tables" })) as HTMLInputElement;
    fireEvent.change(search, { target: { value: "zebra" } });
    await screen.findByText("No lookup tables match “zebra”");
    fireEvent.keyDown(search, { key: "Escape" });
    expect(search.value).toBe("");
    expect(await screen.findByRole("table", { name: "Lookup tables" })).toBeTruthy();
  });
});

describe("list search (knowledge)", () => {
  it("stays out of the way below six knowledge bases", async () => {
    const kb: KbOut = {
      id: "kb-1",
      name: "Policy handbook",
      description: "",
      embedder_id: "fastembed-embedding",
      chunk_count: 3,
      document_count: 1,
      created_at: "2026-09-01T00:00:00Z",
      updated_at: "2026-09-01T00:00:00Z",
    };
    stubApi({ "/knowledge-bases": { items: [kb], total: 1 } });
    renderWithClient(<KbList />);

    expect(await screen.findByRole("table", { name: "Knowledge bases" })).toBeTruthy();
    expect(screen.queryByRole("searchbox")).toBeNull();
  });
});

describe("DatasetDetail — failed import", () => {
  it("shows a page-level alert with plain copy instead of the worker's technical error, and no delete for a viewer", async () => {
    stubApi(
      {
        "/datasets/ds_9": dataset({
          id: "ds_9",
          name: "Broken upload",
          status: "failed",
          error: "Traceback (most recent call last): psycopg.errors.UndefinedTable",
        }),
      },
      "viewer",
    );
    renderWithClient(<DatasetDetail datasetId="ds_9" />);

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("The import failed")).toBeTruthy();
    expect(alert.textContent).toContain("Check the file and upload it again.");
    expect(screen.queryByText(/Traceback|psycopg/)).toBeNull();
    await waitFor(() => expect(screen.queryByRole("button", { name: /Delete lookup table/ })).toBeNull());
  });
});
