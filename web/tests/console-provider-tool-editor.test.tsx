import * as React from "react";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { HttpToolEditorDialog } from "@/components/console/tools/http-tool-editor-dialog";
import { ProviderToolEditorDialog, humanizeActionSlug } from "@/components/console/tools/provider-tool-editor-dialog";
import { ToolRow } from "@/components/console/tools/tool-row";
import type { ProviderToolDefinition, ToolOut } from "@/contracts/lkap-contracts";

/**
 * R-V5-8 (docs/v5/PLAN-V5.md V5-50): the dedicated `provider-tool-editor-dialog.tsx`
 * for a `ProviderToolDefinition` (a connected app's materialised action) —
 * wired into `tool-row.tsx` and `tools-list.tsx` in place of the hidden Edit
 * button ask #46 left there — plus the `execution-fields.tsx` extraction
 * shared with `http-tool-editor-dialog.tsx`.
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

function jsonResponse(body: unknown, status = 200): Response {
  return { ok: status < 400, status, json: async () => body } as Response;
}

/** Radix `Select` mirrors every item in a hidden native `<option>` too; scope to the open listbox. */
async function pickOption(text: string) {
  const listbox = await screen.findByRole("listbox");
  fireEvent.click(within(listbox).getByText(text));
}

function providerTool(overrides: Partial<ProviderToolDefinition> = {}): ToolOut & { definition: ProviderToolDefinition } {
  const definition: ProviderToolDefinition = {
    kind: "provider",
    provider: "composio",
    name: "googlecalendar_find_free_slots",
    description: "Find a free slot on the calendar.",
    parameters: {
      type: "object",
      properties: {
        calendar_id: { type: "string", description: "The calendar to search." },
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

const CONNECTION = {
  id: "conn_gcal",
  provider: "composio" as const,
  toolkit: "googlecalendar",
  toolkit_name: "Google Calendar",
  subject: "ws:ws1",
  status: "active" as const,
  method: "managed" as const,
  needs_reconnect: false,
  picked_actions: ["GOOGLECALENDAR_FIND_FREE_SLOTS"],
};

function stubFetch(overrides: (url: string, init?: RequestInit) => { status: number; body: unknown } | undefined = () => undefined) {
  const calls: { url: string; method: string; body: unknown }[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url, method, body });
    const override = overrides(url, init);
    if (override) return jsonResponse(override.body, override.status);
    if (url.includes("/auth/me")) return jsonResponse({ user: { id: "u1" }, workspaces: [{ id: "ws1", name: "WS", role: "admin", slug: "ws" }] });
    if (url.includes("/tool-providers/composio/connections")) return jsonResponse({ items: [CONNECTION], total: 1 });
    return jsonResponse({});
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, calls };
}

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("humanizeActionSlug", () => {
  it("strips the toolkit prefix and title-cases the rest", () => {
    expect(humanizeActionSlug("googlecalendar", "GOOGLECALENDAR_FIND_FREE_SLOTS")).toBe("Find Free Slots");
  });

  it("falls back to title-casing the whole slug when the prefix doesn't match", () => {
    expect(humanizeActionSlug("github", "SOME_OTHER_ACTION")).toBe("Some Other Action");
  });
});

describe("ProviderToolEditorDialog", () => {
  it("shows the read-only header (app name, action name, connection status) and the pinned parameters", async () => {
    stubFetch();
    const tool = providerTool();
    renderWithClient(
      <ProviderToolEditorDialog tool={tool} onSaved={vi.fn()} trigger={<button>Edit</button>} />,
    );
    fireEvent.click(screen.getByText("Edit"));

    expect(await screen.findByText("Edit App action")).toBeTruthy();
    expect(await screen.findByRole("img", { name: "Google Calendar" })).toBeTruthy();
    expect(screen.getByText("Find Free Slots")).toBeTruthy();
    expect(await screen.findByText("Connected")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Tools → Apps" })).toHaveProperty("href", expect.stringContaining("/console/tools?tab=apps"));

    // Pinned parameters, read-only.
    const calendarIdField = screen.getByText("calendar_id");
    expect(calendarIdField).toBeTruthy();
    expect(screen.getByText("duration_minutes")).toBeTruthy();
    expect(within(calendarIdField.closest("li")!).getByText("Required")).toBeTruthy();

    // The raw slug is tucked away in "Details", not shown in the main header (copy rule: no "slug" outside a disclosure).
    expect(screen.queryByText("GOOGLECALENDAR_FIND_FREE_SLOTS")).toBeNull();
    fireEvent.click(screen.getByText("Details"));
    expect(await screen.findByText("GOOGLECALENDAR_FIND_FREE_SLOTS")).toBeTruthy();
  });

  it("posts the edited name/description/timeout/max_result_chars/result_path/silent_reply, preserving the rest of the definition", async () => {
    const { fetchMock } = stubFetch((url) => {
      if (url.includes("/tools/tool-1") && url.endsWith("/tools/tool-1")) {
        return { status: 200, body: { ...providerTool(), name: "renamed_action" } };
      }
      return undefined;
    });
    const tool = providerTool();
    const onSaved = vi.fn();
    renderWithClient(<ProviderToolEditorDialog tool={tool} onSaved={onSaved} trigger={<button>Edit</button>} />);
    fireEvent.click(screen.getByText("Edit"));
    await screen.findByText("Edit App action");

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "renamed_action" } });
    fireEvent.change(screen.getByLabelText(/Description/), { target: { value: "New description." } });
    fireEvent.change(screen.getByLabelText("Timeout"), { target: { value: "20" } });
    fireEvent.change(screen.getByLabelText("Max result characters"), { target: { value: "2000" } });
    fireEvent.change(screen.getByLabelText(/Result JSON pointer/), { target: { value: "/data/slots" } });
    fireEvent.click(screen.getByLabelText("Silent reply"));

    fireEvent.click(screen.getByText("Save"));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());

    const call = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/tools/tool-1")) as [string, RequestInit];
    expect(call).toBeDefined();
    const body = JSON.parse(String(call[1].body)) as { kind: string; name: string; definition: ProviderToolDefinition };
    expect(body.kind).toBe("provider");
    expect(body.name).toBe("renamed_action");
    expect(body.definition).toMatchObject({
      name: "renamed_action",
      description: "New description.",
      timeout_s: 20,
      max_result_chars: 2000,
      result_path: "/data/slots",
      silent_reply: true,
      // Untouched fields survive the spread.
      tool_slug: "GOOGLECALENDAR_FIND_FREE_SLOTS",
      connection_id: "conn_gcal",
      subject: "ws:ws1",
      toolkit: "googlecalendar",
      risk: "write",
    });
  });

  describe("Refresh schema (POST .../tools/{id}/refresh-schema, R-V5-8/R-V5-6)", () => {
    it("diffs on click without applying, then Apply posts apply=true; nothing posts before either click", async () => {
      let refreshCalls = 0;
      const { fetchMock } = stubFetch((url) => {
        if (url.includes("/refresh-schema")) {
          refreshCalls += 1;
          const applied = url.includes("apply=true");
          return {
            status: 200,
            body: {
              tool_id: "tool-1",
              tool_slug: "GOOGLECALENDAR_FIND_FREE_SLOTS",
              changed: true,
              applied,
              schema_version_before: "3",
              schema_version_after: "4",
              added: ["time_zone"],
              removed: [],
              modified: ["duration_minutes"],
              required_before: ["calendar_id"],
              required_after: ["calendar_id"],
            },
          };
        }
        return undefined;
      });
      const tool = providerTool();
      renderWithClient(<ProviderToolEditorDialog tool={tool} onSaved={vi.fn()} trigger={<button>Edit</button>} />);
      fireEvent.click(screen.getByText("Edit"));
      await screen.findByText("Edit App action");

      expect(fetchMock.mock.calls.some(([url]) => String(url).includes("refresh-schema"))).toBe(false);

      fireEvent.click(screen.getByText("Refresh schema"));
      expect(await screen.findByText(/The app changed this action/)).toBeTruthy();
      expect(screen.getByText(/Added: time_zone/)).toBeTruthy();
      expect(screen.getByText(/Changed: duration_minutes/)).toBeTruthy();
      expect(screen.getByText(/Version: 3 → 4/)).toBeTruthy();
      expect(refreshCalls).toBe(1);
      const firstCall = fetchMock.mock.calls.find(([url]) => String(url).includes("refresh-schema"));
      expect(String(firstCall?.[0])).not.toContain("apply=true");

      fireEvent.click(screen.getByText("Apply"));
      await waitFor(() => expect(refreshCalls).toBe(2));
      const secondCall = fetchMock.mock.calls.filter(([url]) => String(url).includes("refresh-schema"))[1];
      expect(String(secondCall?.[0])).toContain("apply=true");
      expect(await screen.findByText("Applied.")).toBeTruthy();
    });

    it('shows "No changes" when the diff reports nothing new', async () => {
      stubFetch((url) => {
        if (url.includes("/refresh-schema")) {
          return {
            status: 200,
            body: {
              tool_id: "tool-1",
              tool_slug: "GOOGLECALENDAR_FIND_FREE_SLOTS",
              changed: false,
              applied: false,
              schema_version_before: "3",
              schema_version_after: "3",
              added: [],
              removed: [],
              modified: [],
              required_before: [],
              required_after: [],
            },
          };
        }
        return undefined;
      });
      const tool = providerTool();
      renderWithClient(<ProviderToolEditorDialog tool={tool} onSaved={vi.fn()} trigger={<button>Edit</button>} />);
      fireEvent.click(screen.getByText("Edit"));
      await screen.findByText("Edit App action");

      fireEvent.click(screen.getByText("Refresh schema"));
      expect(await screen.findByText("No changes since this action was added.")).toBeTruthy();
      expect(screen.queryByText("Apply")).toBeNull();
    });
  });

  it("a destructive action's Runs field hides the non-blocking options (the contract always blocks it)", async () => {
    stubFetch();
    const tool = providerTool({ risk: "destructive" });
    renderWithClient(<ProviderToolEditorDialog tool={tool} onSaved={vi.fn()} trigger={<button>Edit</button>} />);
    fireEvent.click(screen.getByText("Edit"));
    await screen.findByText("Edit App action");

    fireEvent.click(screen.getByLabelText("Runs"));
    const listbox = await screen.findByRole("listbox");
    expect(within(listbox).getByText("Agent default")).toBeTruthy();
    expect(within(listbox).getByText("Blocking")).toBeTruthy();
    expect(within(listbox).queryByText("In the background")).toBeNull();
    expect(within(listbox).queryByText("Automatic")).toBeNull();
  });
});

describe("Edit wiring — tool-row.tsx / tools-list.tsx open the app-action editor, never the MCP one (ask #46, R-V5-8)", () => {
  it("ToolRow's Edit button opens ProviderToolEditorDialog for a provider-kind tool", async () => {
    stubFetch();
    const tool = providerTool();
    renderWithClient(
      <ToolRow
        tool={tool}
        agentId="agent-1"
        attached={true}
        onToggleAttach={vi.fn()}
        onSaved={vi.fn()}
        onDeleted={vi.fn()}
        secretBagSpec={undefined}
      />,
    );

    const editButton = (await screen.findByRole("button", { name: "Edit" })) as HTMLButtonElement;
    await waitFor(() => expect(editButton.disabled).toBe(false));
    fireEvent.click(editButton);

    expect(await screen.findByText("Edit App action")).toBeTruthy();
    expect(screen.queryByText("Edit MCP server")).toBeNull();
  });
});

describe("execution fields post the same shape from both dialogs (R-V5-8: one shared component)", () => {
  it("HTTP and provider editors post an identical `execution` object for identical input", async () => {
    let httpBody: unknown;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (init?.body) httpBody = JSON.parse(String(init.body));
        if (url.includes("/auth/me")) return jsonResponse({ user: { id: "u1" }, workspaces: [] });
        return jsonResponse({
          id: "http-1",
          agent_id: "agent-1",
          kind: "http",
          name: "lookup",
          definition: { kind: "http", name: "lookup", description: "", parameters: {}, method: "POST", url: "https://api.example.com" },
          enabled: true,
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        });
      }),
    );
    renderWithClient(
      <HttpToolEditorDialog agentId="agent-1" secretBagSpec={undefined} onSaved={vi.fn()} trigger={<button>New HTTP tool</button>} />,
    );
    fireEvent.click(screen.getByText("New HTTP tool"));
    fireEvent.change(screen.getByLabelText(/^Name$/), { target: { value: "lookup" } });
    fireEvent.change(screen.getByLabelText(/Allowed hosts/), { target: { value: "api.example.com" } });
    fireEvent.click(screen.getByLabelText("Runs"));
    await pickOption("In the background");
    fireEvent.change(screen.getByLabelText("What the agent says first"), { target: { value: "On it." } });
    fireEvent.click(screen.getByText("Save tool"));
    await waitFor(() => expect(httpBody).toBeDefined());
    const httpExecution = (httpBody as { definition: { execution: unknown } }).definition.execution;

    let providerBody: unknown;
    stubFetch((url, init) => {
      if (url.endsWith("/tools/tool-1") && init?.method === "PUT") {
        providerBody = init?.body ? JSON.parse(String(init.body)) : undefined;
        return { status: 200, body: providerTool() };
      }
      return undefined;
    });
    const tool = providerTool();
    renderWithClient(<ProviderToolEditorDialog tool={tool} onSaved={vi.fn()} trigger={<button>Edit action</button>} />);
    fireEvent.click(screen.getByText("Edit action"));
    await screen.findByText("Edit App action");
    fireEvent.click(screen.getByLabelText("Runs"));
    await pickOption("In the background");
    fireEvent.change(screen.getByLabelText("What the agent says first"), { target: { value: "On it." } });
    fireEvent.click(screen.getByText("Save"));
    await waitFor(() => expect(providerBody).toBeDefined());
    const providerExecution = (providerBody as { definition: { execution: unknown } }).definition.execution;

    expect(providerExecution).toEqual(httpExecution);
  });
});
