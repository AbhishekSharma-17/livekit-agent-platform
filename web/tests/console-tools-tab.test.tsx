import * as React from "react";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { FormProvider, useForm } from "react-hook-form";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { toFormValues } from "@/components/console/agents/editor/form-values";
import { ToolsTab } from "@/components/console/agents/tabs/tools-tab";
import { agentEditorFormSchema, type AgentEditorForm } from "@/components/console/lib/schemas";
import { zodResolver } from "@/components/console/lib/zod-resolver";
import type { AgentOut, ProvidersResponse, ToolPage } from "@/contracts/lkap-contracts";

/**
 * §7.6 acceptance: no jargon labels ("Save & validate" is gone — attach still
 * happens on Save, the copy just says "Saved automatically to this agent");
 * the Knowledge/Vision built-in groups explain *why* they're off instead of
 * silently doing nothing.
 */

const AGENT = {
  id: "a-1",
  slug: "claims",
  name: "Claims",
  description: "",
  pack_id: "generic",
  ui_panel_id: "generic",
  published: false,
  config_version: 1,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
  config: { instructions: "Hi", pipeline: { mode: "cascaded" } },
} as AgentOut;

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
beforeEach(() => vi.stubGlobal("ResizeObserver", ResizeObserverStub));
afterEach(() => vi.unstubAllGlobals());

function jsonResponse(body: unknown, status = 200): Response {
  return { ok: status < 400, status, json: async () => body } as Response;
}

const EMPTY_TOOLS: ToolPage = { items: [], total: 0 };
const PROVIDERS: ProvidersResponse = { providers: [] };

function stubFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/providers")) return jsonResponse(PROVIDERS);
      if (url.includes("/packs")) {
        return jsonResponse({
          items: [{ manifest: { id: "generic", tool_names: ["custom_pack_tool"] } }],
        });
      }
      if (url.includes("/tools")) return jsonResponse(EMPTY_TOOLS);
      return jsonResponse({});
    }),
  );
}

function Harness({ agent = AGENT }: { agent?: AgentOut }) {
  const client = React.useMemo(() => new QueryClient({ defaultOptions: { queries: { retry: false } } }), []);
  const form = useForm<AgentEditorForm>({
    resolver: zodResolver(agentEditorFormSchema),
    defaultValues: toFormValues(agent),
    mode: "onChange",
  });
  return (
    <QueryClientProvider client={client}>
      <FormProvider {...form}>
        <form>
          <ToolsTab agent={agent} />
        </form>
      </FormProvider>
    </QueryClientProvider>
  );
}

describe("ToolsTab", () => {
  it("never says 'Save & validate'; explains that tools save with the agent", async () => {
    stubFetch();
    render(<Harness />);
    await screen.findByText("custom_pack_tool");

    expect(screen.queryByText(/Save & validate/)).toBeNull();
  });

  it("disables Knowledge and Vision groups with a reason until their capability is on", async () => {
    stubFetch();
    render(<Harness />);
    await screen.findByText("custom_pack_tool");

    const searchKnowledge = screen.getByRole("switch", { name: "Search knowledge" }) as HTMLButtonElement;
    expect(searchKnowledge.disabled).toBe(true);
    expect(screen.getAllByText("Attach a knowledge base first").length).toBeGreaterThan(0);

    const describeFrame = screen.getByRole("switch", { name: "Describe current frame" }) as HTMLButtonElement;
    expect(describeFrame.disabled).toBe(true);
    expect(screen.getAllByText("Turn on camera or screen share first").length).toBeGreaterThan(0);

    // Conversation tools have neither restriction and start enabled.
    const endCall = screen.getByRole("switch", { name: "End call" }) as HTMLButtonElement;
    expect(endCall.disabled).toBe(false);
    expect(endCall.getAttribute("data-state")).toBe("checked");
  });

  it("shows the pack's read-only tools in mono, and 'Tool steps per turn' behind Advanced", async () => {
    stubFetch();
    render(<Harness />);

    expect(await screen.findByText("custom_pack_tool")).toBeTruthy();
    expect(screen.queryByLabelText("Tool steps per turn")).toBeNull();

    fireEvent.click(screen.getByText("Advanced"));
    expect(await screen.findByLabelText("Tool steps per turn")).toBeTruthy();
  });

  it("shows empty states for HTTP tools and MCP servers with no leaky mechanism copy", async () => {
    stubFetch();
    render(<Harness />);
    await waitFor(() => expect(screen.getByText("No HTTP tools yet")).toBeTruthy());
    expect(screen.getByText("No MCP servers yet")).toBeTruthy();
  });

  it("says the pack registers no code tools when its manifest lists none", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.includes("/providers")) return jsonResponse(PROVIDERS);
        if (url.includes("/packs")) return jsonResponse({ items: [{ manifest: { id: "generic", tool_names: [] } }] });
        if (url.includes("/tools")) return jsonResponse(EMPTY_TOOLS);
        return jsonResponse({});
      }),
    );
    render(<Harness />);
    expect(await screen.findByText("This pack registers no code tools.")).toBeTruthy();
  });
});
