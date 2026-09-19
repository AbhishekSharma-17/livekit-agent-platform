import * as React from "react";

import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { HttpToolEditorDialog } from "@/components/console/tools/http-tool-editor-dialog";

/**
 * F-05 (docs/REVIEW-FINAL.md WP-C): the no-code HTTP-tool editor let an
 * author save a tool with an empty `allowed_hosts`, which passed the
 * console's "Dry run" but then failed every live call once the worker's
 * fail-closed allowlist rejected it. These tests pin the two console-side
 * fixes: the field is pre-filled from the URL, and Save refuses an empty
 * list instead of persisting it.
 */

// jsdom has no ResizeObserver; the shadcn `Select` (used for the Method
// field) needs one to mount at all, via @radix-ui/react-use-size. No other
// suite in this repo renders a `Select`-bearing dialog yet, so this stub is
// local to this file rather than the shared vitest setup.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

function renderWithClient(ui: React.ReactElement) {
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
  const client = new QueryClient();
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function stubFetch() {
  const fetchMock = vi.fn(
    async () =>
      ({
        ok: true,
        status: 201,
        json: async () => ({
          id: "tool_1",
          agent_id: "agent_1",
          kind: "http",
          name: "lookup_weather",
          definition: {
            kind: "http",
            name: "lookup_weather",
            description: "",
            parameters: { type: "object", properties: {} },
            method: "GET",
            url: "https://api.example.com/weather",
            allowed_hosts: ["api.example.com"],
          },
          enabled: true,
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        }),
      }) as Response,
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("HttpToolEditorDialog", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("pre-fills allowed_hosts from the URL's hostname when the field is empty", async () => {
    stubFetch();
    const { getByText, getByLabelText, getByPlaceholderText } = renderWithClient(
      <HttpToolEditorDialog
        agentId="agent_1"
        secretBagSpec={undefined}
        onSaved={vi.fn()}
        trigger={<button>New HTTP tool</button>}
      />,
    );

    fireEvent.click(getByText("New HTTP tool"));

    const urlInput = await waitFor(() => getByPlaceholderText(/api\.example\.com\/items/));
    fireEvent.change(urlInput, { target: { value: "https://api.example.com/items/{{ item_id }}" } });

    const allowedHostsInput = getByLabelText(/Allowed hosts/) as HTMLInputElement;
    expect(allowedHostsInput.value).toBe("api.example.com");
  });

  it("does not overwrite allowed_hosts once the author has typed into it", async () => {
    stubFetch();
    const { getByText, getByLabelText, getByPlaceholderText } = renderWithClient(
      <HttpToolEditorDialog
        agentId="agent_1"
        secretBagSpec={undefined}
        onSaved={vi.fn()}
        trigger={<button>New HTTP tool</button>}
      />,
    );

    fireEvent.click(getByText("New HTTP tool"));

    const allowedHostsInput = getByLabelText(/Allowed hosts/) as HTMLInputElement;
    fireEvent.change(allowedHostsInput, { target: { value: "manually.example.com" } });

    const urlInput = await waitFor(() => getByPlaceholderText(/api\.example\.com\/items/));
    fireEvent.change(urlInput, { target: { value: "https://api.example.com/items" } });

    expect(allowedHostsInput.value).toBe("manually.example.com");
  });

  it("marks allowed_hosts as required and refuses to save an empty list", async () => {
    const fetchMock = stubFetch();
    const onSaved = vi.fn();
    const { getByText, getByLabelText, getByPlaceholderText } = renderWithClient(
      <HttpToolEditorDialog agentId="agent_1" secretBagSpec={undefined} onSaved={onSaved} trigger={<button>New HTTP tool</button>} />,
    );

    fireEvent.click(getByText("New HTTP tool"));

    const allowedHostsInput = getByLabelText(/Allowed hosts/) as HTMLInputElement;
    expect(allowedHostsInput.required).toBe(true);

    fireEvent.change(getByLabelText(/^Name$/), { target: { value: "lookup_weather" } });
    const urlInput = await waitFor(() => getByPlaceholderText(/api\.example\.com\/items/));
    // A URL whose host part is entirely template placeholders cannot be
    // parsed, so pre-fill does not kick in and allowed_hosts stays empty.
    fireEvent.change(urlInput, { target: { value: "https://{{ host }}/items" } });
    expect(allowedHostsInput.value).toBe("");

    fireEvent.click(getByText("Save tool"));

    await waitFor(() => expect(getByText("Save tool")).toBeTruthy());
    expect(fetchMock).not.toHaveBeenCalled();
    expect(onSaved).not.toHaveBeenCalled();
  });
});
