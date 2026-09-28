import * as React from "react";

import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { toast } from "sonner";

import { DatasetList } from "@/components/console/datasets/dataset-list";
import type { DatasetOut } from "@/contracts/lkap-contracts";

/**
 * `/console/datasets` (V6-19): the list shows every lookup table with its import status and
 * columns, and a refused delete (409 — a tool still uses it) surfaces the api's own message
 * rather than a generic failure. `sonner` is mocked and asserted on directly
 * (`console-knowledge-connections.test.tsx`'s pattern) — no `<Toaster/>` is mounted here.
 */
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient();
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function makeDataset(overrides: Partial<DatasetOut> = {}): DatasetOut {
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

function stubFetch(datasets: DatasetOut[], deleteStatus = 204) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("auth/me")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ user: { id: "u1", email: "a@b.test" }, workspaces: [{ id: "w1", name: "W", slug: "w", role: "admin" }] }),
      } as Response;
    }
    if (url.includes("/datasets/") && init?.method === "DELETE") {
      if (deleteStatus === 204) return { ok: true, status: 204, json: async () => undefined } as Response;
      return {
        ok: false,
        status: deleteStatus,
        json: async () => ({ error: { code: "conflict", message: "Used by tool 'lookup_policy'." } }),
      } as Response;
    }
    if (url.includes("/datasets")) {
      return { ok: true, status: 200, json: async () => ({ items: datasets, total: datasets.length }) } as Response;
    }
    return { ok: true, status: 200, json: async () => ({ items: [], total: 0 }) } as Response;
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

describe("DatasetList", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows every table with its status and match-on columns", async () => {
    stubFetch([
      makeDataset(),
      makeDataset({ id: "ds_2", name: "Still importing", status: "pending", progress: 0.4, key_columns: [] }),
      makeDataset({ id: "ds_3", name: "Broken file", status: "failed", error: "Bad header row.", key_columns: [] }),
    ]);
    renderWithClient(<DatasetList />);

    // `ResponsiveTable` renders a desktop table and a mobile card list for the same rows at
    // once (CSS picks which shows); scope to the table so each row's text is found once.
    const table = await screen.findByRole("table", { name: "Lookup tables" });
    expect(within(table).getByText("Policy directory")).toBeTruthy();
    expect(within(table).getByText("policy_number")).toBeTruthy();
    expect(within(table).getByText("Ready")).toBeTruthy();
    expect(within(table).getByText(/Importing/)).toBeTruthy();
    expect(within(table).getByText("Failed")).toBeTruthy();
    expect(within(table).getByText("Bad header row.")).toBeTruthy();
  });

  it("shows the api's own message when delete is refused (a tool still uses the table)", async () => {
    stubFetch([makeDataset()], 409);
    renderWithClient(<DatasetList />);
    const table = await screen.findByRole("table", { name: "Lookup tables" });

    fireEvent.click(within(table).getByRole("button", { name: "Delete Policy directory" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Confirm" }));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(expect.stringMatching(/Used by tool 'lookup_policy'/)));
  });
});
