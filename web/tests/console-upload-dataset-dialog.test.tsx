import * as React from "react";

import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { UploadDatasetDialog } from "@/components/console/datasets/upload-dataset-dialog";

/**
 * The upload dialog (V6-19, D-V6-27; ask #104): the file's header row is sniffed client-side
 * (`dataset-columns.ts`) into a checklist — "match on this" + a type — and `key_columns` is
 * built from only the checked ones, sent as the JSON string the api's `POST /v1/datasets`
 * expects (`multipart`, via `upload.ts`'s `uploadDataset`).
 */

// jsdom's `File`/`Blob` has no `.text()` (unlike a real browser's, or Node's own global
// `File`) — the dialog reads the picked file with it to sniff its columns, so this test
// environment needs the same small polyfill a browser already provides.
if (typeof File.prototype.text !== "function") {
  File.prototype.text = function readAsText(this: File) {
    return new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result ?? ""));
      reader.onerror = () => reject(reader.error);
      reader.readAsText(this);
    });
  };
}

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient();
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

interface FetchCall {
  url: string;
  form: FormData;
}

function stubFetch() {
  const calls: FetchCall[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("auth/me")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ user: { id: "u1", email: "a@b.test" }, workspaces: [{ id: "w1", name: "W", slug: "w", role: "admin" }] }),
      } as Response;
    }
    if (url.includes("/datasets") && init?.method === "POST") {
      calls.push({ url, form: init.body as FormData });
      return {
        ok: true,
        status: 201,
        json: async () => ({
          id: "ds_1",
          name: "Policy directory",
          slug: "policy-directory",
          format: "csv",
          columns: [],
          key_columns: [],
          row_count: 0,
          sha256: "abc",
          status: "pending",
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        }),
      } as Response;
    }
    return { ok: true, status: 200, json: async () => ({ items: [], total: 0 }) } as Response;
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

describe("UploadDatasetDialog", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("sniffs the header row and lets the admin pick which columns to match on", async () => {
    stubFetch();
    renderWithClient(<UploadDatasetDialog />);
    const trigger = () => screen.getByRole("button", { name: "Upload a lookup table" }) as HTMLButtonElement;
    await waitFor(() => expect(trigger().disabled).toBe(false));
    fireEvent.click(trigger());
    await screen.findByRole("dialog");

    const file = new File(["policy_number,phone,name\n123,555-0100,Jo"], "policies.csv", { type: "text/csv" });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => expect(screen.getByText("policy_number")).toBeTruthy());
    expect(screen.getByText("phone")).toBeTruthy();
    expect(screen.getByText("name")).toBeTruthy();
    // A type picker only shows once a column is checked (it's meaningless otherwise).
    expect(screen.queryByLabelText("policy_number: type")).toBeNull();
  });

  it("uploads only the checked columns, typed, as key_columns", async () => {
    const calls = stubFetch();
    renderWithClient(<UploadDatasetDialog />);
    const trigger = () => screen.getByRole("button", { name: "Upload a lookup table" }) as HTMLButtonElement;
    await waitFor(() => expect(trigger().disabled).toBe(false));
    fireEvent.click(trigger());
    await screen.findByRole("dialog");

    const file = new File(["policy_number,phone,name\n123,555-0100,Jo"], "policies.csv", { type: "text/csv" });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });
    await waitFor(() => screen.getByText("policy_number"));

    fireEvent.click(screen.getByText("policy_number"));
    fireEvent.click(screen.getByText("phone"));
    // `name` stays unchecked — a returned column, not a match-on one.

    fireEvent.click(screen.getByRole("button", { name: "Upload" }));

    await waitFor(() => expect(calls).toHaveLength(1));
    const form = calls[0].form;
    expect(form.get("name")).toBe("policies");
    const keyColumns = JSON.parse(form.get("key_columns") as string);
    expect(keyColumns).toEqual({ policy_number: "string", phone: "string" });
    expect(form.get("file")).toBeInstanceOf(File);
  });
});
