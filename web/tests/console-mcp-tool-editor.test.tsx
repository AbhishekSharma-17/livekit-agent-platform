import * as React from "react";

import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { McpToolEditorDialog } from "@/components/console/tools/mcp-tool-editor-dialog";
import { mcpOauthStartRedirect, mcpOauthStatus, mcpOauthTool, mcpTestResultOk } from "./fixtures/mcp-presets";

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

/**
 * docs/v5/_asks.md #66 (V5-09 → V5-21): the dialog reads and posts
 * `definition.auth`, never the deprecated `headers`/`credential_id` mirrors,
 * and never echoes `cached_tools` back.
 */
// `window.location` isn't a stubGlobal target (`vi.unstubAllGlobals()` won't touch it),
// so the two `window.open` tests below restore the original descriptor themselves.
let originalLocationDescriptor: PropertyDescriptor | undefined;

function stubLocationAssign(assign: (url: string) => void) {
  originalLocationDescriptor = Object.getOwnPropertyDescriptor(window, "location");
  Object.defineProperty(window, "location", { value: { ...window.location, assign }, writable: true, configurable: true });
}

describe("McpToolEditorDialog — auth (docs/v5/_asks.md #66)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    if (originalLocationDescriptor) {
      Object.defineProperty(window, "location", originalLocationDescriptor);
      originalLocationDescriptor = undefined;
    }
  });

  it("posts auth: {kind: 'header', ...} and no top-level headers/credential_id/cached_tools", async () => {
    const fetchMock = stubFetch();
    const { getByText, getByLabelText } = renderWithClient(
      <McpToolEditorDialog agentId="agent_1" secretBagSpec={undefined} onSaved={vi.fn()} trigger={<button>New MCP server</button>} />,
    );
    fireEvent.click(getByText("New MCP server"));

    fireEvent.change(getByLabelText("Name"), { target: { value: "billing" } });
    fireEvent.change(getByLabelText("URL"), { target: { value: "https://mcp.example.com/stream" } });
    // Default auth mode with no preset chosen is "No authentication"; switch to Header.
    // Radix's `RadioGroupItem` is a `button[role=radio]`, not a native input — `getByLabelText`
    // resolves it through the wrapping `<Label htmlFor>`, same as `console-instructions-tab.test.tsx`.
    fireEvent.click(getByLabelText("Header (API key)"));
    fireEvent.change(getByLabelText("Headers"), { target: { value: '{"X-Api-Key": "{{ secret.KEY }}"}' } });

    fireEvent.click(getByText("Save server"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());

    const calls = fetchMock.mock.calls as unknown as [string, RequestInit][];
    const [, init] = calls.find(([url]) => !url.includes("auth/me")) as [string, RequestInit];
    const body = JSON.parse(init.body as string) as {
      definition: {
        auth?: { kind: string; headers?: Record<string, string> };
        headers?: Record<string, string>;
        credential_id?: string | null;
        cached_tools?: unknown;
        cached_at?: unknown;
      };
    };
    expect(body.definition.auth).toEqual({ kind: "header", headers: { "X-Api-Key": "{{ secret.KEY }}" }, credential_id: null });
    expect(body.definition.headers).toBeUndefined();
    expect(body.definition.credential_id).toBeUndefined();
    expect(body.definition.cached_tools).toBeUndefined();
    expect(body.definition.cached_at).toBeUndefined();
  });

  it("reading an oauth server preselects 'Sign in with the vendor' and shows the sign-in panel", async () => {
    stubFetch();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = typeof input === "string" ? input : input.toString();
        if (url.includes("oauth/status")) {
          return { ok: true, status: 200, json: async () => mcpOauthStatus({ status: "not_connected" }) } as Response;
        }
        return { ok: true, status: 200, json: async () => ({}) } as Response;
      }),
    );
    const tool = mcpOauthTool();
    renderWithClient(
      <McpToolEditorDialog agentId={null} tool={tool} secretBagSpec={undefined} onSaved={vi.fn()} trigger={<button>Edit</button>} />,
    );
    fireEvent.click(screen.getByText("Edit"));

    const radio = await screen.findByLabelText("Sign in with the vendor");
    expect(radio.getAttribute("aria-checked")).toBe("true");
    expect(await screen.findByRole("button", { name: "Sign in" })).toBeTruthy();
  });

  it("Sign in opens the vendor's page without also navigating this tab away", async () => {
    // `window.open(url, "_blank", "noopener")` always returns `null` per spec — even when
    // the popup opened — so a naive `if (!popup) location.assign(url)` fallback would
    // navigate the console away on *every* successful sign-in. Cover both outcomes.
    const tool = mcpOauthTool();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = typeof input === "string" ? input : input.toString();
        if (url.includes("oauth/status")) {
          return { ok: true, status: 200, json: async () => mcpOauthStatus({ status: "not_connected" }) } as Response;
        }
        if (url.includes("oauth/start")) {
          return { ok: true, status: 200, json: async () => mcpOauthStartRedirect() } as Response;
        }
        return { ok: true, status: 200, json: async () => ({}) } as Response;
      }),
    );
    const openMock = vi.fn().mockReturnValue({ opener: "not-null-yet" });
    vi.stubGlobal("open", openMock);
    const assignMock = vi.fn();
    stubLocationAssign(assignMock);

    renderWithClient(
      <McpToolEditorDialog agentId={null} tool={tool} secretBagSpec={undefined} onSaved={vi.fn()} trigger={<button>Edit</button>} />,
    );
    fireEvent.click(screen.getByText("Edit"));
    fireEvent.click(await screen.findByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(openMock).toHaveBeenCalledWith("https://mcp.linear.app/authorize?flow=1", "_blank"));
    // The popup handle is real (not blocked) — never fall back to navigating this tab.
    expect(assignMock).not.toHaveBeenCalled();
    expect(await screen.findByText("Waiting for you to finish signing in…")).toBeTruthy();
  });

  it("falls back to navigating this tab only when the popup is actually blocked", async () => {
    const tool = mcpOauthTool();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = typeof input === "string" ? input : input.toString();
        if (url.includes("oauth/status")) {
          return { ok: true, status: 200, json: async () => mcpOauthStatus({ status: "not_connected" }) } as Response;
        }
        if (url.includes("oauth/start")) {
          return { ok: true, status: 200, json: async () => mcpOauthStartRedirect() } as Response;
        }
        return { ok: true, status: 200, json: async () => ({}) } as Response;
      }),
    );
    vi.stubGlobal("open", vi.fn().mockReturnValue(null));
    const assignMock = vi.fn();
    stubLocationAssign(assignMock);

    renderWithClient(
      <McpToolEditorDialog agentId={null} tool={tool} secretBagSpec={undefined} onSaved={vi.fn()} trigger={<button>Edit</button>} />,
    );
    fireEvent.click(screen.getByText("Edit"));
    fireEvent.click(await screen.findByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(assignMock).toHaveBeenCalledWith("https://mcp.linear.app/authorize?flow=1"));
  });

  it("gates Test connection and Sign in behind a save once the draft has unsaved edits", async () => {
    stubFetch();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = typeof input === "string" ? input : input.toString();
        if (url.includes("oauth/status")) {
          return { ok: true, status: 200, json: async () => mcpOauthStatus({ status: "not_connected" }) } as Response;
        }
        return { ok: true, status: 200, json: async () => ({}) } as Response;
      }),
    );
    const tool = mcpOauthTool();
    renderWithClient(
      <McpToolEditorDialog agentId={null} tool={tool} secretBagSpec={undefined} onSaved={vi.fn()} trigger={<button>Edit</button>} />,
    );
    fireEvent.click(screen.getByText("Edit"));
    await screen.findByRole("button", { name: "Sign in" });

    fireEvent.change(screen.getByLabelText("URL"), { target: { value: "https://mcp.linear.app/mcp/readonly" } });

    expect(await screen.findByText("Save your changes first.")).toBeTruthy();
    const testButton = screen.getByRole("button", { name: "Test connection" }) as HTMLButtonElement;
    expect(testButton.disabled).toBe(true);
  });

  it("Test connection lists the server's tools as checkboxes that narrow Allowed tools", async () => {
    const tool = mcpOauthTool();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input.toString();
        const method = (init?.method ?? "GET").toUpperCase();
        if (url.includes("oauth/status")) {
          return { ok: true, status: 200, json: async () => mcpOauthStatus({ status: "not_connected" }) } as Response;
        }
        if (url.includes("/test") && method === "POST") {
          return { ok: true, status: 200, json: async () => mcpTestResultOk() } as Response;
        }
        return { ok: true, status: 200, json: async () => ({}) } as Response;
      }),
    );
    renderWithClient(
      <McpToolEditorDialog agentId={null} tool={tool} secretBagSpec={undefined} onSaved={vi.fn()} trigger={<button>Edit</button>} />,
    );
    fireEvent.click(screen.getByText("Edit"));

    fireEvent.click(await screen.findByRole("button", { name: "Test connection" }));
    expect(await screen.findByText("list_issues")).toBeTruthy();

    // Unchecking a discovered tool narrows "Allowed tools" to the rest.
    fireEvent.click(screen.getByLabelText("list_issues"));
    await waitFor(() => {
      const allowed = screen.getByLabelText("Allowed tools") as HTMLInputElement;
      expect(allowed.value.includes("list_issues")).toBe(false);
      expect(allowed.value.includes("create_issue")).toBe(true);
    });
  });
});
