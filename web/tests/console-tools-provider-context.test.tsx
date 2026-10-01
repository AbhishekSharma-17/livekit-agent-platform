import * as React from "react";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ProviderToolEditorDialog } from "@/components/console/tools/provider-tool-editor-dialog";
import type { ProviderToolDefinition, ToolOut } from "@/contracts/lkap-contracts";

/**
 * V6-11: the app-action editor's session values/variables fields and its "Fixed values"
 * (`pinned_arguments`) editor — the fields `ProviderToolDefinition` carries directly (unlike
 * MCP's per-tool `tool_context`; ask #34: "pinned_arguments exist on app actions and MCP
 * tools only").
 */

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
beforeEach(() => {
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn().mockReturnValue(false);
  Element.prototype.releasePointerCapture = vi.fn();
});
afterEach(() => vi.unstubAllGlobals());

function providerTool(overrides: Partial<ProviderToolDefinition> = {}): ToolOut & { definition: ProviderToolDefinition } {
  const definition: ProviderToolDefinition = {
    kind: "provider",
    provider: "composio",
    name: "googlecalendar_find_free_slots",
    description: "Find a free slot on the calendar.",
    parameters: {
      type: "object",
      properties: {
        calendar_id: { type: "string" },
        duration_minutes: { type: "integer" },
      },
      required: ["calendar_id"],
    },
    tool_slug: "GOOGLECALENDAR_FIND_FREE_SLOTS",
    toolkit: "googlecalendar",
    connection_id: "conn_gcal",
    subject: "ws:ws1",
    timeout_s: 10,
    max_result_chars: 1500,
    result_path: "data",
    silent_reply: false,
    risk: "write",
    schema_version: "3",
    execution: {},
    ...overrides,
  };
  return {
    id: "tool-1",
    name: definition.name,
    kind: "provider",
    agent_id: null,
    enabled: true,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    definition,
  } as ToolOut & { definition: ProviderToolDefinition };
}

function stubFetch() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/auth/me")) {
      return { ok: true, status: 200, json: async () => ({ user: { id: "u1" }, workspaces: [{ id: "ws1", name: "WS", role: "admin", slug: "ws" }] }) } as Response;
    }
    if (url.includes("/tool-providers/composio/connections")) {
      return { ok: true, status: 200, json: async () => ({ items: [], total: 0 }) } as Response;
    }
    const body = providerTool();
    return { ok: true, status: 200, json: async () => body } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("ProviderToolEditorDialog — session values, read-back, bindings, fixed values (V6-11)", () => {
  it("keeps requires_vars/confirm_readback/bindings/pinned_arguments already on the definition when nothing is touched", async () => {
    const fetchMock = stubFetch();
    const onSaved = vi.fn();
    const tool = providerTool({
      requires_vars: ["policy_no"],
      confirm_readback: ["calendar_id"],
      bindings: [{ path: "/slot", to: "status" }],
      pinned_arguments: { duration_minutes: 30 },
    });
    renderWithClient(<ProviderToolEditorDialog tool={tool} onSaved={onSaved} trigger={<button>Edit</button>} />);
    fireEvent.click(screen.getByText("Edit"));
    await screen.findByText("Edit App action");

    fireEvent.click(screen.getByText("Save"));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());

    const calls = fetchMock.mock.calls as unknown as [string, RequestInit][];
    const [, init] = calls.find(([url]) => !url.includes("auth/me") && !url.includes("connections")) as [string, RequestInit];
    const body = JSON.parse(init.body as string) as { definition: ProviderToolDefinition };
    expect(body.definition.requires_vars).toEqual(["policy_no"]);
    expect(body.definition.confirm_readback).toEqual(["calendar_id"]);
    expect(body.definition.bindings).toEqual([{ path: "/slot", to: "status" }]);
    expect(body.definition.pinned_arguments).toEqual({ duration_minutes: 30 });
  });

  it("adds a fixed value with the Insert value helper and posts it pinned", async () => {
    const fetchMock = stubFetch();
    const onSaved = vi.fn();
    const tool = providerTool();
    renderWithClient(<ProviderToolEditorDialog tool={tool} onSaved={onSaved} trigger={<button>Edit</button>} />);
    fireEvent.click(screen.getByText("Edit"));
    await screen.findByText("Edit App action");

    fireEvent.change(screen.getByPlaceholderText("argument_name"), { target: { value: "duration_minutes" } });
    fireEvent.click(screen.getByRole("button", { name: "Add a fixed value" }));

    // Default type is "Text"; switch it to "Number" and set the value.
    fireEvent.click(screen.getByLabelText("duration_minutes: type"));
    fireEvent.click(await screen.findByRole("option", { name: "Number" }));
    fireEvent.change(screen.getByLabelText("duration_minutes: value"), { target: { value: "45" } });

    fireEvent.click(screen.getByText("Save"));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());

    const calls = fetchMock.mock.calls as unknown as [string, RequestInit][];
    const [, init] = calls.find(([url]) => !url.includes("auth/me") && !url.includes("connections")) as [string, RequestInit];
    const body = JSON.parse(init.body as string) as { definition: ProviderToolDefinition };
    expect(body.definition.pinned_arguments).toEqual({ duration_minutes: 45 });
  });

  it("picks confirm_readback from checkboxes limited to the action's own arguments", async () => {
    const fetchMock = stubFetch();
    const onSaved = vi.fn();
    const tool = providerTool();
    renderWithClient(<ProviderToolEditorDialog tool={tool} onSaved={onSaved} trigger={<button>Edit</button>} />);
    fireEvent.click(screen.getByText("Edit"));
    await screen.findByText("Edit App action");

    // Only the action's own two arguments are offered — no free-text fallback needed here.
    fireEvent.click(screen.getByLabelText("calendar_id", { selector: "input" }));
    fireEvent.click(screen.getByLabelText("duration_minutes", { selector: "input" }));

    fireEvent.click(screen.getByText("Save"));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());

    const calls = fetchMock.mock.calls as unknown as [string, RequestInit][];
    const [, init] = calls.find(([url]) => !url.includes("auth/me") && !url.includes("connections")) as [string, RequestInit];
    const body = JSON.parse(init.body as string) as { definition: ProviderToolDefinition };
    expect(body.definition.confirm_readback).toEqual(["calendar_id", "duration_minutes"]);
  });
});
