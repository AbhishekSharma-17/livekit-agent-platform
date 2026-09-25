import * as React from "react";

import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
  // Radix `Select` (the V4-13 "Runs" picker) needs these in jsdom.
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn().mockReturnValue(false);
  Element.prototype.releasePointerCapture = vi.fn();
  const client = new QueryClient();
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

/** Radix `Select` mirrors every item in a hidden native `<option>` too; scope to the open listbox. */
async function pickOption(text: string) {
  const listbox = await screen.findByRole("listbox");
  fireEvent.click(within(listbox).getByText(text));
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
    // `useWriteAccess` (docs/v2/_asks.md V2-20-5/V2-21-2) reads `auth/me` on
    // every mount regardless of whether the credential picker renders — a
    // harmless read, unlike the tool create/update call this test guards
    // against.
    const calls = fetchMock.mock.calls as unknown as [string, unknown][];
    expect(calls.filter(([url]) => url !== "/api/console/auth/me")).toHaveLength(0);
    expect(onSaved).not.toHaveBeenCalled();
  });

  describe("execution fields (V4-13, BACKGROUND-TOOLS.md §7)", () => {
    /** Fills in every field the "Allowed hosts"-empty-list guard needs, nothing execution-related. */
    function fillBasics(getByLabelText: (text: RegExp) => HTMLElement) {
      fireEvent.change(getByLabelText(/^Name$/), { target: { value: "lookup_weather" } });
      fireEvent.change(getByLabelText(/Allowed hosts/), { target: { value: "api.example.com" } });
    }

    it('choosing "Automatic" shows the threshold field with 700', async () => {
      stubFetch();
      const { getByText, getByLabelText } = renderWithClient(
        <HttpToolEditorDialog agentId="agent_1" secretBagSpec={undefined} onSaved={vi.fn()} trigger={<button>New HTTP tool</button>} />,
      );
      fireEvent.click(getByText("New HTTP tool"));

      expect(screen.queryByLabelText("Switches to background after")).toBeNull();

      fireEvent.click(getByLabelText("Runs"));
      await pickOption("Automatic");

      const threshold = await waitFor(() => getByLabelText("Switches to background after") as HTMLInputElement);
      expect(threshold.value).toBe("700");
    });

    it("posts definition.execution matching the form", async () => {
      const fetchMock = stubFetch();
      const onSaved = vi.fn();
      const { getByText, getByLabelText } = renderWithClient(
        <HttpToolEditorDialog agentId="agent_1" secretBagSpec={undefined} onSaved={onSaved} trigger={<button>New HTTP tool</button>} />,
      );
      fireEvent.click(getByText("New HTTP tool"));
      fillBasics(getByLabelText);

      fireEvent.click(getByLabelText("Runs"));
      await pickOption("In the background");

      fireEvent.change(getByLabelText("What the agent says first"), { target: { value: "Fetching that now." } });
      fireEvent.change(getByLabelText("Fillers while waiting"), { target: { value: "Still checking.\nAlmost there.\n\n  " } });
      fireEvent.change(getByLabelText("First filler after"), { target: { value: "3" } });
      fireEvent.change(getByLabelText("Then every"), { target: { value: "6" } });

      fireEvent.click(getByLabelText("Can be cancelled"));
      await pickOption("No");

      fireEvent.click(getByLabelText("Repeated calls"));
      await pickOption("Allow it");

      fireEvent.change(getByLabelText("Give up after"), { target: { value: "45" } });

      fireEvent.click(getByText("Save tool"));
      await waitFor(() => expect(onSaved).toHaveBeenCalled());

      const calls = fetchMock.mock.calls as unknown as [string, RequestInit][];
      const call = calls.find(([url]) => !url.includes("auth/me"));
      expect(call).toBeDefined();
      const [, init] = call as [string, RequestInit];
      const body = JSON.parse(init.body as string) as { definition: { execution: Record<string, unknown> } };
      expect(body.definition.execution).toEqual({
        mode: "background",
        announce: "Fetching that now.",
        auto_threshold_ms: 700,
        fillers: ["Still checking.", "Almost there."],
        filler_delay_s: 3,
        filler_interval_s: 6,
        cancellable: false,
        on_duplicate: "allow",
        duplicate_scope: "name_and_args",
        max_duration_s: 45,
      });
    });

    it('switching "Silent reply" on with "In the background" selected shows the validator message and disables Save', async () => {
      stubFetch();
      const { getByText, getByLabelText } = renderWithClient(
        <HttpToolEditorDialog agentId="agent_1" secretBagSpec={undefined} onSaved={vi.fn()} trigger={<button>New HTTP tool</button>} />,
      );
      fireEvent.click(getByText("New HTTP tool"));
      fillBasics(getByLabelText);

      fireEvent.click(getByLabelText("Runs"));
      await pickOption("In the background");

      const saveButton = getByText("Save tool") as HTMLButtonElement;
      expect(saveButton.disabled).toBe(false);

      fireEvent.click(getByLabelText("Silent reply"));

      await waitFor(() =>
        expect(
          screen.getAllByText(/has silent_reply on, which would swallow its background announcement; turn one of them off/),
        ).not.toHaveLength(0),
      );
      expect(saveButton.disabled).toBe(true);
    });

    it("shows the confirm-default note for a non-GET method", async () => {
      stubFetch();
      const { getByText } = renderWithClient(
        <HttpToolEditorDialog agentId="agent_1" secretBagSpec={undefined} onSaved={vi.fn()} trigger={<button>New HTTP tool</button>} />,
      );
      fireEvent.click(getByText("New HTTP tool"));

      // The draft defaults to POST, so the note is visible without touching the Method field.
      expect(getByText("This tool changes something; the agent asks before running it twice.")).toBeTruthy();
    });
  });
});
