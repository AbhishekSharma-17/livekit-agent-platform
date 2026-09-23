import * as React from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { KbDocuments } from "@/components/console/knowledge/kb-documents";
import { KB_UPLOAD_MAX_BYTES } from "@/components/console/lib/upload";
import type { KbDocumentOut, KbDocumentPage } from "@/contracts/lkap-contracts";

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
