import * as React from "react";

import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { DatasetToolEditorDialog } from "@/components/console/tools/dataset-tool-editor-dialog";
import type { DatasetOut, ToolOut } from "@/contracts/lkap-contracts";

/**
 * The lookup-table tool editor (V6-19, D-V6-27; ask #104): until this dialog
 * existed, `tool-row.tsx` showed a `kind: "dataset"` tool as plain text with
 * no way to edit it. Covers the basics: only a `ready` table is offered, its
 * columns seed the match-on and returned-column checklists, and Save refuses
 * a fixed value that names a column the tool doesn't match on.
 */

function renderWithClient(ui: React.ReactElement) {
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
  const client = new QueryClient();
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

/** Radix `Select` mirrors every item in a hidden native `<option>` too; scope to the open listbox (`console-http-tool-editor.test.tsx`'s pattern). */
async function pickOption(text: string) {
  const listbox = await screen.findByRole("listbox");
  fireEvent.click(within(listbox).getByText(text));
}

function makeDataset(overrides: Partial<DatasetOut> = {}): DatasetOut {
  return {
    id: "ds_1",
    name: "Policy directory",
    slug: "policy-directory",
    format: "csv",
    columns: [
      { name: "policy_number", label: "Policy Number", type: "string", key: true },
      { name: "phone", label: "Phone", type: "phone", key: true },
      { name: "holder_name", label: "Holder Name", type: "string", key: false },
    ],
    key_columns: [
      { name: "policy_number", type: "string" },
      { name: "phone", type: "phone" },
    ],
    row_count: 10,
    sha256: "abc",
    status: "ready",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

interface FetchCall {
  method: string;
  url: string;
  body: unknown;
}

function stubFetch(datasets: DatasetOut[], created: Partial<ToolOut> = {}) {
  const calls: FetchCall[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    let body: unknown;
    if (typeof init?.body === "string") {
      try {
        body = JSON.parse(init.body);
      } catch {
        body = init.body;
      }
    }
    calls.push({ method, url, body });
    if (url.includes("/datasets") && method === "GET") {
      return { ok: true, status: 200, json: async () => ({ items: datasets, total: datasets.length }) } as Response;
    }
    if (url.includes("/tools") && method === "POST") {
      return {
        ok: true,
        status: 201,
        json: async () => ({
          id: "tool_1",
          agent_id: "agent_1",
          kind: "dataset",
          name: "lookup_policy",
          definition: (body as { definition: unknown }).definition,
          enabled: true,
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
          ...created,
        }),
      } as Response;
    }
    return { ok: true, status: 200, json: async () => ({ items: [], total: 0 }) } as Response;
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

describe("DatasetToolEditorDialog", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("offers only ready tables and seeds the column checklists from it", async () => {
    stubFetch([makeDataset(), makeDataset({ id: "ds_2", name: "Still importing", status: "pending" })]);
    renderWithClient(
      <DatasetToolEditorDialog agentId="agent_1" onSaved={vi.fn()} trigger={<button>New lookup tool</button>} />,
    );
    fireEvent.click(screen.getByText("New lookup tool"));
    await screen.findByRole("dialog");

    fireEvent.click(screen.getByLabelText("Table"));
    const listbox = await screen.findByRole("listbox");
    expect(listbox.textContent).toContain("Policy directory");
    expect(listbox.textContent).not.toContain("Still importing");
    await pickOption("Policy directory");

    await waitFor(() => expect(screen.getByText("policy_number (string)")).toBeTruthy());
    expect(screen.getByText("phone (phone)")).toBeTruthy();
    expect(screen.getByText("Holder Name")).toBeTruthy();
  });

  it("saves a lookup tool with the picked columns", async () => {
    const calls = stubFetch([makeDataset()]);
    const onSaved = vi.fn();
    renderWithClient(<DatasetToolEditorDialog agentId="agent_1" onSaved={onSaved} trigger={<button>New lookup tool</button>} />);
    fireEvent.click(screen.getByText("New lookup tool"));
    await screen.findByRole("dialog");

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "lookup_policy" } });
    fireEvent.click(screen.getByLabelText("Table"));
    await pickOption("Policy directory");
    await waitFor(() => screen.getByText("policy_number (string)"));
    fireEvent.click(screen.getByLabelText("policy_number (string)"));

    fireEvent.click(screen.getByRole("button", { name: "Save tool" }));

    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    const toolCall = calls.find((c) => c.url.includes("/tools") && c.method === "POST");
    expect(toolCall?.body).toMatchObject({
      kind: "dataset",
      name: "lookup_policy",
      definition: { kind: "dataset", dataset_id: "ds_1", key_columns: ["policy_number"] },
    });
  });

  it("refuses a fixed value that names a column the tool doesn't match on", async () => {
    stubFetch([makeDataset()]);
    renderWithClient(<DatasetToolEditorDialog agentId="agent_1" onSaved={vi.fn()} trigger={<button>New lookup tool</button>} />);
    fireEvent.click(screen.getByText("New lookup tool"));
    await screen.findByRole("dialog");

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "lookup_policy" } });
    fireEvent.click(screen.getByLabelText("Table"));
    await pickOption("Policy directory");
    await waitFor(() => screen.getByText("phone (phone)"));
    // Match on phone only, but pin a value for policy_number — outside the match-on set.
    fireEvent.click(screen.getByLabelText("phone (phone)"));
    fireEvent.change(screen.getByPlaceholderText("argument_name"), { target: { value: "policy_number" } });
    fireEvent.click(screen.getByRole("button", { name: "Add a fixed value" }));

    fireEvent.click(screen.getByRole("button", { name: "Save tool" }));
    expect(await screen.findByText(/Fixed values can only name a match-on column/)).toBeTruthy();
  });
});
