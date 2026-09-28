import * as React from "react";

import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { McpToolEditorDialog } from "@/components/console/tools/mcp-tool-editor-dialog";
import type { ToolOut } from "@/contracts/lkap-contracts";

/**
 * V6-11: the MCP editor's per-tool "Tool context" disclosure — `requires_vars`,
 * `confirm_readback`, `bindings` and `pinned_arguments` (`ToolContextSpec`), one per allowed
 * tool name, following the same "only rows the admin touched are posted" convention as
 * `tool_options` (D-V4-32).
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
            allowed_tools: ["lookup_invoice"],
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

const EXISTING_TOOL: ToolOut = {
  id: "tool_1",
  agent_id: "agent_1",
  kind: "mcp",
  name: "billing",
  enabled: true,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  definition: {
    kind: "mcp",
    name: "billing",
    url: "https://mcp.example.com/stream",
    allowed_tools: ["lookup_invoice"],
    cached_tools: [
      {
        name: "lookup_invoice",
        input_schema: { type: "object", properties: { invoice_id: { type: "string" }, email: { type: "string" } } },
      },
    ],
  },
};

describe("McpToolEditorDialog — per-tool context (V6-11)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("adds requires_vars, confirm_readback and bindings under a tool's own disclosure and posts them scoped to that tool", async () => {
    const fetchMock = stubFetch();
    const onSaved = vi.fn();
    const { getByText } = renderWithClient(
      <McpToolEditorDialog agentId="agent_1" tool={EXISTING_TOOL} secretBagSpec={undefined} onSaved={onSaved} trigger={<button>Edit server</button>} />,
    );
    fireEvent.click(getByText("Edit server"));

    fireEvent.click(await screen.findByText("Tool context — lookup_invoice"));

    // requires_vars
    fireEvent.change(screen.getByPlaceholderText("policy_no"), { target: { value: "account_id" } });
    fireEvent.click(screen.getByRole("button", { name: "Add a required variable" }));
    expect(screen.getByText("account_id")).toBeTruthy();

    // confirm_readback: the tool's cached schema names invoice_id/email as checkboxes.
    const emailCheckbox = screen.getByLabelText("email", { selector: "input" }) as HTMLInputElement;
    fireEvent.click(emailCheckbox);
    expect(emailCheckbox.checked).toBe(true);

    // bindings: no agent panel loaded, so the default kind is "status" — switch to "var".
    fireEvent.click(screen.getByText("Add a binding"));
    fireEvent.click(screen.getByLabelText("Binding 1 — goes to"));
    fireEvent.click(await screen.findByRole("option", { name: "A variable" }));
    fireEvent.change(screen.getByLabelText("Binding 1 — variable name"), { target: { value: "account_email" } });

    fireEvent.click(getByText("Save server"));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());

    const calls = fetchMock.mock.calls as unknown as [string, RequestInit][];
    const [, init] = calls.find(([url]) => !url.includes("auth/me")) as [string, RequestInit];
    const body = JSON.parse(init.body as string) as {
      definition: { tool_context?: Record<string, { requires_vars?: string[]; confirm_readback?: string[]; bindings?: { path: string; to: string }[] }> };
    };
    const context = body.definition.tool_context?.lookup_invoice;
    expect(context?.requires_vars).toEqual(["account_id"]);
    expect(context?.confirm_readback).toEqual(["email"]);
    expect(context?.bindings).toEqual([{ path: "", to: "var:account_email" }]);
  });

  it("never posts tool_context for a tool whose disclosure the admin never opened", async () => {
    const fetchMock = stubFetch();
    const onSaved = vi.fn();
    const { getByText } = renderWithClient(
      <McpToolEditorDialog agentId="agent_1" tool={EXISTING_TOOL} secretBagSpec={undefined} onSaved={onSaved} trigger={<button>Edit server</button>} />,
    );
    fireEvent.click(getByText("Edit server"));
    fireEvent.click(getByText("Save server"));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());

    const calls = fetchMock.mock.calls as unknown as [string, RequestInit][];
    const [, init] = calls.find(([url]) => !url.includes("auth/me")) as [string, RequestInit];
    const body = JSON.parse(init.body as string) as { definition: { tool_context?: unknown } };
    expect(body.definition.tool_context).toBeUndefined();
  });

  it("keeps a stored tool_context unchanged when its disclosure isn't touched", async () => {
    const fetchMock = stubFetch();
    const onSaved = vi.fn();
    const tool: ToolOut = {
      ...EXISTING_TOOL,
      definition: {
        ...EXISTING_TOOL.definition,
        tool_context: {
          lookup_invoice: {
            requires_vars: ["account_id"],
            confirm_readback: ["email"],
            bindings: [{ path: "/holder", to: "status" }],
            pinned_arguments: { invoice_id: "INV-1" },
          },
        },
      },
    };
    const { getByText } = renderWithClient(
      <McpToolEditorDialog agentId="agent_1" tool={tool} secretBagSpec={undefined} onSaved={onSaved} trigger={<button>Edit server</button>} />,
    );
    fireEvent.click(getByText("Edit server"));
    fireEvent.click(getByText("Save server"));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());

    const calls = fetchMock.mock.calls as unknown as [string, RequestInit][];
    const [, init] = calls.find(([url]) => !url.includes("auth/me")) as [string, RequestInit];
    const body = JSON.parse(init.body as string) as {
      definition: { tool_context?: Record<string, { requires_vars?: string[]; confirm_readback?: string[]; bindings?: unknown; pinned_arguments?: unknown }> };
    };
    expect(body.definition.tool_context?.lookup_invoice).toEqual({
      requires_vars: ["account_id"],
      confirm_readback: ["email"],
      bindings: [{ path: "/holder", to: "status" }],
      pinned_arguments: { invoice_id: "INV-1" },
    });
  });

  it("adds a fixed (pinned) value under a tool's disclosure", async () => {
    const fetchMock = stubFetch();
    const onSaved = vi.fn();
    const { getByText } = renderWithClient(
      <McpToolEditorDialog agentId="agent_1" tool={EXISTING_TOOL} secretBagSpec={undefined} onSaved={onSaved} trigger={<button>Edit server</button>} />,
    );
    fireEvent.click(getByText("Edit server"));
    fireEvent.click(await screen.findByText("Tool context — lookup_invoice"));

    fireEvent.change(screen.getByPlaceholderText("argument_name"), { target: { value: "invoice_id" } });
    fireEvent.click(screen.getByRole("button", { name: "Add a fixed value" }));

    const valueInput = screen.getByLabelText("invoice_id — value") as HTMLInputElement;
    fireEvent.change(valueInput, { target: { value: "INV-1" } });

    fireEvent.click(getByText("Save server"));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());

    const calls = fetchMock.mock.calls as unknown as [string, RequestInit][];
    const [, init] = calls.find(([url]) => !url.includes("auth/me")) as [string, RequestInit];
    const body = JSON.parse(init.body as string) as {
      definition: { tool_context?: Record<string, { pinned_arguments?: Record<string, unknown> }> };
    };
    expect(body.definition.tool_context?.lookup_invoice.pinned_arguments).toEqual({ invoice_id: "INV-1" });
  });
});
