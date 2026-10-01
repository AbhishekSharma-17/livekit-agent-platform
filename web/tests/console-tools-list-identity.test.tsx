import * as React from "react";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ToolsList } from "@/components/console/tools/tools-list";
import { humanizeToolName, toolService, toolTitle } from "@/components/console/tools/tool-identity";
import type { ToolOut, ToolPage } from "@/contracts/lkap-contracts";
import { connectionFixture, connectionPage } from "./fixtures/apps";

/**
 * The Tools list's row identity: a human title instead of the raw tool name,
 * the technical name kept small beside it (and searchable), and a leading
 * mark for the app, server or kind.
 */

describe("humanizeToolName", () => {
  it.each([
    ["gmail_send_email", ["gmail"], "Send email"],
    ["gmail_list_labels", ["Gmail"], "List labels"],
    ["googlecalendar_find_free_slots", ["Google Calendar"], "Find free slots"],
    ["customer_lookup", [], "Customer lookup"],
    ["policy_lookup", [], "Policy lookup"],
    ["customerLookup", [], "Customer lookup"],
    ["get-order-id", [], "Get order ID"],
    ["send_sms_reminder", [], "Send SMS reminder"],
    ["getURL", [], "Get URL"],
    ["linear", [], "Linear"],
    // the prefix only goes when it is the app's
    ["gmail_send_email", ["slack"], "Gmail send email"],
    // already words, or brand cased: left alone
    ["Policy lookup", [], "Policy lookup"],
    ["GitHub", [], "GitHub"],
    ["HubSpot", ["hubspot"], "HubSpot"],
  ] as const)("%s (prefix %j) → %s", (name, prefixes, title) => {
    expect(humanizeToolName(name, prefixes)).toBe(title);
  });

  it("keeps the whole name when stripping the prefix would leave nothing", () => {
    expect(humanizeToolName("gmail", ["gmail"])).toBe("Gmail");
  });
});

function tool(overrides: Partial<ToolOut> & Pick<ToolOut, "id" | "name" | "kind" | "definition">): ToolOut {
  return {
    agent_id: null,
    enabled: true,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    ...overrides,
  } as ToolOut;
}

const GMAIL = tool({
  id: "t-gmail",
  name: "gmail_send_email",
  kind: "provider",
  definition: {
    kind: "provider",
    provider: "composio",
    name: "gmail_send_email",
    description: "Sends an email from the connected inbox to the recipients the caller names, with a subject and body.",
    parameters: {},
    tool_slug: "GMAIL_SEND_EMAIL",
    connection_id: "conn_gmail",
    toolkit: "gmail",
    subject: "ws:ws1",
  },
} as Partial<ToolOut> & Pick<ToolOut, "id" | "name" | "kind" | "definition">);

const LOOKUP = tool({
  id: "t-lookup",
  name: "policy_lookup",
  kind: "dataset",
  definition: { kind: "dataset", name: "policy_lookup", description: "Find a policy", dataset_id: "ds1", key_columns: ["policy_number"] },
} as Partial<ToolOut> & Pick<ToolOut, "id" | "name" | "kind" | "definition">);

const HTTP = tool({
  id: "t-http",
  name: "customer_lookup",
  kind: "http",
  definition: { kind: "http", name: "customer_lookup", description: "Look up a customer", method: "GET", url: "https://api.example.test/customers" },
} as Partial<ToolOut> & Pick<ToolOut, "id" | "name" | "kind" | "definition">);

const LINEAR_MCP = tool({
  id: "t-linear",
  name: "linear",
  kind: "mcp",
  definition: { kind: "mcp", name: "linear", url: "https://mcp.linear.app/mcp" },
} as Partial<ToolOut> & Pick<ToolOut, "id" | "name" | "kind" | "definition">);

const PLAIN_MCP = tool({
  id: "t-mcp",
  name: "my_mcp_server",
  kind: "mcp",
  definition: { kind: "mcp", name: "my_mcp_server", url: "https://example.test/mcp" },
} as Partial<ToolOut> & Pick<ToolOut, "id" | "name" | "kind" | "definition">);

describe("toolTitle and toolService", () => {
  it("titles an app action without its app prefix and names the app", () => {
    expect(toolTitle(GMAIL, "Gmail")).toBe("Send email");
    expect(toolService(GMAIL, "Gmail")).toBe("Gmail");
    expect(toolService(GMAIL)).toBe("gmail");
  });

  it("finds an MCP server's brand from its preset or host, and nothing for an unknown host", () => {
    expect(toolService(LINEAR_MCP)).toBe("Linear");
    expect(toolService(PLAIN_MCP)).toBeNull();
    expect(toolService(HTTP)).toBeNull();
    expect(toolService(LOOKUP)).toBeNull();
  });
});

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
beforeEach(() => vi.stubGlobal("ResizeObserver", ResizeObserverStub));
afterEach(() => vi.unstubAllGlobals());

function stubApi(tools: ToolOut[]) {
  const page: ToolPage = { items: tools, total: tools.length };
  const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/auth/me")) return json({ user: { id: "u1" }, workspaces: [{ id: "ws1", name: "WS", role: "admin", slug: "ws" }] });
      if (url.includes("/providers")) return json({ providers: [] });
      if (url.includes("/tool-providers/composio/connections"))
        return json(connectionPage([connectionFixture({ id: "conn_gmail", toolkit: "gmail", toolkit_name: "Gmail" })]));
      if (url.includes("/tools")) return json(page);
      if (url.includes("/agents")) return json({ items: [], total: 0 });
      return json({});
    }),
  );
}

function renderList() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ToolsList />
    </QueryClientProvider>,
  );
}

function table() {
  return within(document.querySelector('[data-slot="responsive-table-table"]') as HTMLElement);
}

function rowOf(text: string): HTMLElement {
  return table().getByText(text).closest("tr") as HTMLElement;
}

describe("ToolsList rows", () => {
  it("leads with a human title and keeps the technical name small, in mono", async () => {
    stubApi([GMAIL, LOOKUP, HTTP]);
    renderList();
    await waitFor(() => expect(table().getByText("Send email")).toBeTruthy());
    expect(table().getByText("Policy lookup")).toBeTruthy();
    expect(table().getByText("Customer lookup")).toBeTruthy();

    const name = table().getByText("gmail_send_email");
    expect(name.closest('[data-slot="tool-name"]')?.tagName).toBe("CODE");
    expect(name.closest('[data-slot="tool-name"]')?.className).toContain("font-mono");
    // The title is not the raw name.
    expect(table().queryByText("gmail_send_email", { selector: ".font-medium, .font-medium *" })).toBeNull();
  });

  it("gives each row its leading mark: the app's real mark, a table, a globe, a server brand or a plug", async () => {
    stubApi([GMAIL, LOOKUP, HTTP, LINEAR_MCP, PLAIN_MCP]);
    renderList();
    await waitFor(() => expect(table().getByText("Send email")).toBeTruthy());

    const lead = (row: HTMLElement) => row.querySelector("td")!.querySelector('[data-slot="vendor-mark"], [data-slot="tool-mark"]')!;
    expect(lead(rowOf("Send email")).getAttribute("data-mark")).toBe("gmail");
    expect(lead(rowOf("Policy lookup")).getAttribute("data-icon")).toBe("table");
    expect(lead(rowOf("Customer lookup")).getAttribute("data-icon")).toBe("globe");
    expect(lead(rowOf("Linear")).getAttribute("data-mark")).toBe("linear");
    expect(lead(rowOf("My MCP server")).getAttribute("data-icon")).toBe("plug");
    for (const text of ["Send email", "Policy lookup", "Customer lookup", "Linear", "My MCP server"]) {
      expect(lead(rowOf(text)).getAttribute("aria-hidden"), text).toBe("true");
    }
  });

  it("shows the app's mark once, leading the row, and plain “App” in the Kind column, named for assistive tech", async () => {
    stubApi([GMAIL]);
    renderList();
    await waitFor(() => expect(table().getByText("Send email")).toBeTruthy());
    const row = rowOf("Send email");
    // One mark per row: the leading one in the name column.
    expect(row.querySelectorAll('[data-mark="gmail"]')).toHaveLength(1);
    expect(row.querySelector("td")!.querySelector('[data-mark="gmail"]')).not.toBeNull();
    const kindCell = row.querySelectorAll("td")[1];
    expect(kindCell.querySelector('[data-slot="vendor-mark"], [data-slot="tool-mark"], [data-mark]')).toBeNull();
    expect(within(kindCell).getByText("App")).toBeTruthy();
    // The leading mark is decorative, so the Kind cell names the app for assistive tech.
    const named = within(kindCell).getByText(", Gmail");
    expect(named.className).toContain("sr-only");
  });

  it("lets the name column give way so every other column shows in full", async () => {
    stubApi([GMAIL]);
    renderList();
    await waitFor(() => expect(table().getByText("Send email")).toBeTruthy());
    const nameCell = rowOf("Send email").querySelector("td")!;
    expect(nameCell.className).toContain("max-w-0");
    expect(nameCell.className).toContain("w-full");
    const summary = table().getByText(/Sends an email from the connected inbox/);
    expect(summary.closest(".truncate")).toBeTruthy();
  });

  it("matches the search on the human title and on the technical name", async () => {
    stubApi([GMAIL, LOOKUP, HTTP, LINEAR_MCP, PLAIN_MCP, { ...HTTP, id: "t-http-2", name: "order_status" } as ToolOut]);
    renderList();
    await waitFor(() => expect(table().getByText("Send email")).toBeTruthy());
    const search = screen.getByRole("searchbox");
    // Matches are highlighted (split into <mark>s), so read whole rows.
    const titles = () => table().queryAllByRole("row").slice(1).map((row) => row.querySelector(".font-medium")?.textContent).filter(Boolean);

    fireEvent.change(search, { target: { value: "send email" } });
    await waitFor(() => expect(titles()).toEqual(["Send email"]));

    fireEvent.change(search, { target: { value: "gmail_send" } });
    await waitFor(() => expect(titles()).toEqual(["Send email"]));

    fireEvent.change(search, { target: { value: "order status" } });
    await waitFor(() => expect(titles()).toEqual(["Order status"]));
  });
});
