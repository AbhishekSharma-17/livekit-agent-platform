import * as React from "react";

import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { HttpToolEditorDialog } from "@/components/console/tools/http-tool-editor-dialog";
import { useInsertableField } from "@/components/console/tools/use-insertable-field";
import type { AgentOut } from "@/contracts/lkap-contracts";

/**
 * V6-11: the HTTP tool editor's new context UI — the "Insert value" picker (guarded against
 * the url's scheme/host/port), "Needs these values first" (`requires_vars`), "Read back
 * before calling" (`confirm_readback`) and "Put the result on the panel" (`bindings`).
 */

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

function renderWithClient(ui: React.ReactElement, agent?: AgentOut) {
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn().mockReturnValue(false);
  Element.prototype.releasePointerCapture = vi.fn();
  const client = new QueryClient();
  if (agent) client.setQueryData(["agents", agent.id], agent);
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

const AGENT_WITH_PANEL: AgentOut = {
  id: "agent_1",
  slug: "claims",
  name: "Claims",
  description: "",
  pack_id: "generic",
  ui_panel_id: "generic",
  published: false,
  config_version: 1,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
  config: {
    instructions: "Hi",
    pipeline: { mode: "cascaded" },
    flow: { v: 1, nodes: [], edges: [], variables: [{ name: "policy_no", type: "string" }] },
    panel: {
      panel_id: "generic",
      blocks: [
        { id: "card", type: "details", title: "Policy", config: { fields: [{ key: "holder", label: "Holder" }] } },
        { id: "history", type: "table", title: "History" },
      ],
    },
  },
};

/**
 * `InsertValueMenu`'s Radix `DropdownMenu` opens on `pointerdown`, which jsdom has no
 * `PointerEvent` for — `console-agents-list.test.tsx` documents that driving a Radix dropdown
 * open with `fireEvent` there hangs the test process rather than just failing, so (as that
 * file does) the open → pick flow isn't exercised through the DOM here. What the insert
 * itself does with a cursor position is a pure function (`useInsertableField`), tested
 * directly below via a tiny host component that skips the menu entirely.
 */
function InsertableFieldHarness({ initial }: { initial: string }) {
  const [value, setValue] = React.useState(initial);
  const field = useInsertableField<HTMLInputElement>(value, setValue);
  return (
    <>
      <input
        data-testid="field"
        ref={field.ref}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onSelect={field.trackCaret}
        onClick={field.trackCaret}
        onKeyUp={field.trackCaret}
      />
      <button type="button" onClick={() => field.insert("{{ ctx.channel }}")}>
        insert
      </button>
    </>
  );
}

describe("useInsertableField (the mechanism behind Insert value)", () => {
  it("inserts a token at the tracked cursor position, not at the end of the text", () => {
    render(<InsertableFieldHarness initial="https://api.example.com/items/end" />);
    const input = screen.getByTestId("field") as HTMLInputElement;
    const cursor = "https://api.example.com/items/".length;
    input.setSelectionRange(cursor, cursor);
    fireEvent.select(input);

    fireEvent.click(screen.getByText("insert"));

    expect(input.value).toBe("https://api.example.com/items/{{ ctx.channel }}end");
  });

  it("restores focus and places the caret right after the inserted token", async () => {
    render(<InsertableFieldHarness initial="https://api.example.com/items" />);
    const input = screen.getByTestId("field") as HTMLInputElement;
    input.setSelectionRange(input.value.length, input.value.length);
    fireEvent.select(input);

    fireEvent.click(screen.getByText("insert"));

    await waitFor(() => expect(document.activeElement).toBe(input));
    expect(input.selectionStart).toBe(input.value.length);
  });
});

describe("HttpToolEditorDialog — Insert value (V6-11)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("disables Insert value once the cursor sits in the url's scheme/host", async () => {
    stubFetch();
    const { getByText, getByPlaceholderText } = renderWithClient(
      <HttpToolEditorDialog agentId="agent_1" secretBagSpec={undefined} onSaved={vi.fn()} trigger={<button>New HTTP tool</button>} />,
    );
    fireEvent.click(getByText("New HTTP tool"));

    const urlInput = (await waitFor(() => getByPlaceholderText(/api\.example\.com\/items/))) as HTMLInputElement;
    fireEvent.change(urlInput, { target: { value: "https://api.example.com/items" } });
    // Inside "api.example.com", well before the first "/" after the host.
    urlInput.setSelectionRange(10, 10);
    fireEvent.select(urlInput);

    const insertButton = screen.getAllByText("Insert value")[0].closest("button") as HTMLButtonElement;
    expect(insertButton.disabled).toBe(true);
    expect(insertButton.title).toContain("scheme, host or port");
  });

  it("never offers Insert value beside Headers", async () => {
    stubFetch();
    const { getByText, queryAllByText } = renderWithClient(
      <HttpToolEditorDialog agentId="agent_1" secretBagSpec={undefined} onSaved={vi.fn()} trigger={<button>New HTTP tool</button>} />,
    );
    fireEvent.click(getByText("New HTTP tool"));

    await screen.findByLabelText("Headers");
    // Two "Insert value" buttons exist (url, body template) — none scoped to Headers, which
    // instead gets a plain-words hint explaining why.
    expect(queryAllByText("Insert value")).toHaveLength(2);
    expect(screen.getByText(/can't be used in headers/)).toBeTruthy();
  });
});

describe("HttpToolEditorDialog — session values and variables (V6-11)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("adds and removes a required variable", async () => {
    stubFetch();
    const { getByText, getByPlaceholderText, getByLabelText } = renderWithClient(
      <HttpToolEditorDialog agentId="agent_1" secretBagSpec={undefined} onSaved={vi.fn()} trigger={<button>New HTTP tool</button>} />,
    );
    fireEvent.click(getByText("New HTTP tool"));

    const input = await waitFor(() => getByPlaceholderText("policy_no"));
    fireEvent.change(input, { target: { value: "policy_no" } });
    fireEvent.click(screen.getByRole("button", { name: "Add a required variable" }));

    expect(getByText("policy_no")).toBeTruthy();
    fireEvent.click(getByLabelText("Remove policy_no"));
    expect(screen.queryByText("policy_no")).toBeNull();
  });

  it("picks confirm_readback from the tool's own arguments once the JSON Schema names them", async () => {
    stubFetch();
    const { getByLabelText } = renderWithClient(
      <HttpToolEditorDialog agentId="agent_1" secretBagSpec={undefined} onSaved={vi.fn()} trigger={<button>New HTTP tool</button>} />,
    );
    fireEvent.click(screen.getByText("New HTTP tool"));

    const parametersField = await screen.findByLabelText("Parameters (JSON Schema)");
    fireEvent.change(parametersField, {
      target: { value: JSON.stringify({ type: "object", properties: { email: { type: "string" } }, required: [] }) },
    });

    const checkbox = (await screen.findByLabelText("email", { selector: "input" })) as HTMLInputElement;
    fireEvent.click(checkbox);
    expect(checkbox.checked).toBe(true);
    void getByLabelText;
  });

  it("completes a binding to a details card and posts the target string", async () => {
    const fetchMock = stubFetch();
    const onSaved = vi.fn();
    const { getByText, getByLabelText, getByPlaceholderText } = renderWithClient(
      <HttpToolEditorDialog agentId="agent_1" secretBagSpec={undefined} onSaved={onSaved} trigger={<button>New HTTP tool</button>} />,
      AGENT_WITH_PANEL,
    );
    fireEvent.click(getByText("New HTTP tool"));

    fireEvent.change(getByLabelText("Name"), { target: { value: "lookup_policy" } });
    const urlInput = await waitFor(() => getByPlaceholderText(/api\.example\.com\/items/));
    fireEvent.change(urlInput, { target: { value: "https://api.example.com/policy" } });

    fireEvent.click(getByText("Add a binding"));
    // "A card's field" is the default kind for the first row when the agent has a details block.
    fireEvent.change(getByLabelText("Binding 1 — which part of the result"), { target: { value: "/holder" } });
    fireEvent.change(getByLabelText("Binding 1 — field key"), { target: { value: "holder" } });

    fireEvent.click(getByText("Save tool"));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());

    const calls = fetchMock.mock.calls as unknown as [string, RequestInit][];
    const [, init] = calls.find(([url]) => !url.includes("auth/me")) as [string, RequestInit];
    const body = JSON.parse(init.body as string) as { definition: { bindings?: { path: string; to: string }[] } };
    expect(body.definition.bindings).toEqual([{ path: "/holder", to: "details:card.holder" }]);
  });

  it("blocks Save while a binding is missing its target details", async () => {
    stubFetch();
    const onSaved = vi.fn();
    const { getByText, getByLabelText, getByPlaceholderText } = renderWithClient(
      <HttpToolEditorDialog agentId="agent_1" secretBagSpec={undefined} onSaved={onSaved} trigger={<button>New HTTP tool</button>} />,
    );
    fireEvent.click(getByText("New HTTP tool"));
    fireEvent.change(getByLabelText("Name"), { target: { value: "lookup_policy" } });
    const urlInput = await waitFor(() => getByPlaceholderText(/api\.example\.com\/items/));
    fireEvent.change(urlInput, { target: { value: "https://api.example.com/policy" } });

    // No agent panel loaded — the default kind is "status" (needs nothing further); switch it
    // to "A checklist item" and leave the item id blank to produce an incomplete row.
    fireEvent.click(getByText("Add a binding"));
    fireEvent.click(getByLabelText("Binding 1 — goes to"));
    fireEvent.click(await screen.findByRole("option", { name: "A checklist item" }));

    fireEvent.click(getByText("Save tool"));
    await screen.findByText("Finish choosing where each binding goes before saving.");
    expect(onSaved).not.toHaveBeenCalled();

    // Filling the item id clears the gate and Save goes through.
    fireEvent.change(getByLabelText("Binding 1 — checklist item id"), { target: { value: "item-1" } });
    fireEvent.click(getByText("Save tool"));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
  });
});
