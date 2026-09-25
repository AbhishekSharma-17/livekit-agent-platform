import * as React from "react";

import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { McpToolEditorDialog } from "@/components/console/tools/mcp-tool-editor-dialog";

/**
 * The per-tool execution options table (V4-13, BACKGROUND-TOOLS.md §7): rows
 * come from `allowed_tools` when set (free text otherwise), and only rows the
 * admin actually touched are posted in `tool_options` — an untouched row means
 * "no override", not "blocking with every default written out".
 */

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

function renderWithClient(ui: React.ReactElement) {
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
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
          kind: "mcp",
          name: "billing",
          definition: {
            kind: "mcp",
            name: "billing",
            url: "https://mcp.example.com/stream",
            allowed_tools: ["a", "b"],
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

describe("McpToolEditorDialog", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('shows two rows for allowed_tools = "a, b"', async () => {
    stubFetch();
    const { getByText, getByLabelText } = renderWithClient(
      <McpToolEditorDialog agentId="agent_1" secretBagSpec={undefined} onSaved={vi.fn()} trigger={<button>New MCP server</button>} />,
    );
    fireEvent.click(getByText("New MCP server"));

    fireEvent.change(getByLabelText("Allowed tools"), { target: { value: "a, b" } });

    const rows = await waitFor(() => screen.getAllByRole("row"));
    // One header row plus one per allowed tool.
    expect(rows).toHaveLength(3);
    expect(screen.getByText("a")).toBeTruthy();
    expect(screen.getByText("b")).toBeTruthy();
  });

  it("posts tool_options for only the rows the admin edited", async () => {
    const fetchMock = stubFetch();
    const onSaved = vi.fn();
    const { getByText, getByLabelText } = renderWithClient(
      <McpToolEditorDialog agentId="agent_1" secretBagSpec={undefined} onSaved={onSaved} trigger={<button>New MCP server</button>} />,
    );
    fireEvent.click(getByText("New MCP server"));

    fireEvent.change(getByLabelText("Name"), { target: { value: "billing" } });
    fireEvent.change(getByLabelText("URL"), { target: { value: "https://mcp.example.com/stream" } });
    fireEvent.change(getByLabelText("Allowed tools"), { target: { value: "a, b" } });

    // Only row "a" gets an override: mode → background, announce progress on.
    fireEvent.click(await screen.findByLabelText("a — runs"));
    await pickOption("In the background");
    fireEvent.click(screen.getByLabelText("a — announce progress"));

    fireEvent.click(getByText("Save server"));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());

    const calls = fetchMock.mock.calls as unknown as [string, RequestInit][];
    const call = calls.find(([url]) => !url.includes("auth/me"));
    const [, init] = call as [string, RequestInit];
    const body = JSON.parse(init.body as string) as {
      definition: { tool_options: Record<string, { mode?: string; report_progress?: boolean }> };
    };
    expect(Object.keys(body.definition.tool_options)).toEqual(["a"]);
    expect(body.definition.tool_options.a.mode).toBe("background");
    expect(body.definition.tool_options.a.report_progress).toBe(true);
  });

  it("drops a stale free-text row's override once allowed_tools narrows past it", async () => {
    // A row added while allowed_tools was empty, then hidden by narrowing
    // allowed_tools, must not still post an override the api would reject
    // ("not one of this server's allowed_tools").
    const fetchMock = stubFetch();
    const onSaved = vi.fn();
    const { getByText, getByLabelText, getByPlaceholderText } = renderWithClient(
      <McpToolEditorDialog agentId="agent_1" secretBagSpec={undefined} onSaved={onSaved} trigger={<button>New MCP server</button>} />,
    );
    fireEvent.click(getByText("New MCP server"));
    fireEvent.change(getByLabelText("Name"), { target: { value: "billing" } });
    fireEvent.change(getByLabelText("URL"), { target: { value: "https://mcp.example.com/stream" } });

    fireEvent.change(getByPlaceholderText("tool_name"), { target: { value: "stale_tool" } });
    fireEvent.click(getByText("Add"));
    expect(await screen.findByText("stale_tool")).toBeTruthy();
    fireEvent.click(await screen.findByLabelText("stale_tool — announce progress"));

    // Narrowing allowed_tools now hides the row from the table.
    fireEvent.change(getByLabelText("Allowed tools"), { target: { value: "a" } });
    expect(screen.queryByText("stale_tool")).toBeNull();

    fireEvent.click(getByText("Save server"));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());

    const calls = fetchMock.mock.calls as unknown as [string, RequestInit][];
    const [, init] = calls.find(([url]) => !url.includes("auth/me")) as [string, RequestInit];
    const body = JSON.parse(init.body as string) as { definition: { tool_options: Record<string, unknown> } };
    expect(Object.keys(body.definition.tool_options)).toEqual([]);
  });

  it("lets a free-text row be added and removed when allowed_tools is empty", async () => {
    stubFetch();
    const { getByText, getByPlaceholderText } = renderWithClient(
      <McpToolEditorDialog agentId="agent_1" secretBagSpec={undefined} onSaved={vi.fn()} trigger={<button>New MCP server</button>} />,
    );
    fireEvent.click(getByText("New MCP server"));

    const nameInput = getByPlaceholderText("tool_name");
    fireEvent.change(nameInput, { target: { value: "lookup_policy" } });
    fireEvent.click(getByText("Add"));

    expect(await screen.findByText("lookup_policy")).toBeTruthy();

    fireEvent.click(screen.getByLabelText("Remove lookup_policy"));
    await waitFor(() => expect(screen.queryByText("lookup_policy")).toBeNull());
  });
});
