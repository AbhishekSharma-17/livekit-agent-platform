import * as React from "react";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

let latest: AgentEditorForm | null = null;

function Harness({ agent = AGENT }: { agent?: AgentOut }) {
  const client = React.useMemo(() => new QueryClient({ defaultOptions: { queries: { retry: false } } }), []);
  const form = useForm<AgentEditorForm>({
    resolver: zodResolver(agentEditorFormSchema),
    defaultValues: toFormValues(agent),
    mode: "onChange",
  });
  latest = form.watch();
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

  describe("built-in execution chips (V4-13, BACKGROUND-TOOLS.md §7)", () => {
    beforeEach(() => {
      // Radix `Select` (the Execution dialog's "Runs" picker) needs these in jsdom.
      Element.prototype.scrollIntoView = vi.fn();
      Element.prototype.hasPointerCapture = vi.fn().mockReturnValue(false);
      Element.prototype.releasePointerCapture = vi.fn();
    });

    /** Radix `Select` mirrors every item in a hidden native `<option>` too; scope to the open listbox. */
    async function pickOption(text: string) {
      const listbox = await screen.findByRole("listbox");
      fireEvent.click(within(listbox).getByText(text));
    }

    it("shows an Execution chip only on search_knowledge, describe_current_frame and http_request", async () => {
      stubFetch();
      render(<Harness />);
      await screen.findByText("custom_pack_tool");

      expect(screen.getAllByText("Execution")).toHaveLength(3);
      // Not on a builtin outside BACKGROUNDABLE_BUILTINS, e.g. "End call".
      const endCallRow = screen.getByRole("switch", { name: "End call" }).closest("[data-slot='field']");
      expect(endCallRow ? within(endCallRow as HTMLElement).queryByText("Execution") : null).toBeNull();
    });

    it('opens the dialog and writes "tools.builtin_execution.search_knowledge" on Save', async () => {
      stubFetch();
      render(<Harness />);
      await screen.findByText("custom_pack_tool");

      // Knowledge group renders first among the chip-bearing rows.
      fireEvent.click(screen.getAllByText("Execution")[0]);
      expect(await screen.findByText("Execution — Search knowledge")).toBeTruthy();

      fireEvent.click(screen.getByLabelText("Runs"));
      await pickOption("Automatic — background only if slow");

      fireEvent.click(screen.getByText("Save"));

      await waitFor(() => expect(latest?.config.tools.builtin_execution.search_knowledge?.mode).toBe("auto"));
      expect(latest?.config.tools.builtin_execution.search_knowledge?.auto_threshold_ms).toBe(700);
    });
  });
});
