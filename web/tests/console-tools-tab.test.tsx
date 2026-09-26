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

/**
 * V5-48's `ConnectedAppsCard` mounts inside `ToolsTab` and calls
 * `useAppsStatus`/`useToolProviderConnections` — Apps starts "not set up" in
 * every test here unless a test says otherwise, so the card renders its
 * compact empty state rather than the mode picker.
 */
function stubFetch(appsEnabled = false) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/providers")) return jsonResponse(PROVIDERS);
      if (url.includes("/packs")) {
        return jsonResponse({
          items: [{ manifest: { id: "generic", tool_names: ["custom_pack_tool"] } }],
        });
      }
      if (url.includes("/tool-providers/composio/status")) return jsonResponse({ enabled: appsEnabled, credential_id: appsEnabled ? "cred-1" : null, connections: 0 });
      if (url.includes("/tool-providers/composio/connections")) return jsonResponse({ items: [], total: 0 });
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

  describe("Connected apps card (V5-48, docs/v5/COMPOSIO.md §6)", () => {
    it("mounts the Connected apps card, pointing at Tools -> Apps while Apps isn't set up", async () => {
      stubFetch(false);
      render(<Harness />);
      await screen.findByText("custom_pack_tool");

      expect(await screen.findByText("Connected apps", { selector: "h2" })).toBeTruthy();
      expect(screen.getByText("Apps aren't set up yet")).toBeTruthy();
      expect(screen.getByRole("link", { name: "Go to Apps" })).toBeTruthy();
    });

    it("shows the mode picker once Apps is enabled", async () => {
      stubFetch(true);
      render(<Harness />);
      await screen.findByText("custom_pack_tool");

      expect(await screen.findByText("How this agent uses apps")).toBeTruthy();
      expect(screen.queryByText("Apps aren't set up yet")).toBeNull();
    });

    it("R-V5-13: passes an app with several accounts through to the Connected apps card without error (the account chooser lives there)", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn(async (url: string) => {
          if (url.includes("/providers")) return jsonResponse(PROVIDERS);
          if (url.includes("/packs")) return jsonResponse({ items: [{ manifest: { id: "generic", tool_names: ["custom_pack_tool"] } }] });
          if (url.includes("/tool-providers/composio/status")) return jsonResponse({ enabled: true, credential_id: "cred-1", connections: 2 });
          if (url.includes("/tool-providers/composio/connections")) {
            return jsonResponse({
              items: [
                { id: "conn_work", provider: "composio", toolkit: "github", toolkit_name: "GitHub", subject: "ws:ws1", status: "active", method: "managed", needs_reconnect: false, picked_actions: [], agents_using: 0, label: "Work", is_default: true },
                { id: "conn_personal", provider: "composio", toolkit: "github", toolkit_name: "GitHub", subject: "ws:ws1", status: "active", method: "managed", needs_reconnect: false, picked_actions: [], agents_using: 0, label: "Personal", is_default: false },
              ],
              total: 2,
            });
          }
          if (url.includes("/tools")) return jsonResponse(EMPTY_TOOLS);
          if (/\/toolkits\/[^/?]+\/actions/.test(url)) return jsonResponse({ items: [], next_cursor: null, total: 0 });
          return jsonResponse({});
        }),
      );
      const agentInServerMode = {
        ...AGENT,
        config: { ...AGENT.config, tools: { apps: { mode: "server", allowed_toolkits: ["github"] } } },
      } as unknown as AgentOut;
      render(<Harness agent={agentInServerMode} />);
      await screen.findByText("custom_pack_tool");

      const accountsHeading = await screen.findByText("Which accounts");
      // Scoped to the accounts chooser itself: V5-28's "Calculate" built-in row
      // ("Works out sums…") also matches a loose /Work/ query on the whole page.
      const accountsSection = accountsHeading.closest("section") ?? accountsHeading.parentElement!;
      expect(within(accountsSection).getByText(/Work/)).toBeTruthy();
      expect(within(accountsSection).getByText(/Personal/)).toBeTruthy();
    });

    it("keeps a Composio app server / tool finder row out of the MCP servers section (it's managed from the card above)", async () => {
      // `tool_providers/provisioning.py` creates these with this exact
      // `agent_id`, so `useTools(agent.id)` would otherwise return them here
      // too, with the usual editable MCP row.
      vi.stubGlobal(
        "fetch",
        vi.fn(async (url: string) => {
          if (url.includes("/providers")) return jsonResponse(PROVIDERS);
          if (url.includes("/packs")) return jsonResponse({ items: [{ manifest: { id: "generic", tool_names: ["custom_pack_tool"] } }] });
          if (url.includes("/tool-providers/composio/status")) return jsonResponse({ enabled: true, credential_id: "cred-1", connections: 0 });
          if (url.includes("/tool-providers/composio/connections")) return jsonResponse({ items: [], total: 0 });
          if (url.includes("/tools")) {
            return jsonResponse({
              items: [
                {
                  id: "tool-server",
                  name: "composio_app_server",
                  kind: "mcp",
                  agent_id: AGENT.id,
                  enabled: true,
                  created_at: "2026-09-01T00:00:00Z",
                  updated_at: "2026-09-01T00:00:00Z",
                  definition: {
                    kind: "mcp",
                    name: "composio_app_server",
                    url: "https://backend.composio.dev/v3/mcp/abc",
                    origin: { provider: "composio", kind: "server", remote_id: "abc" },
                  },
                },
              ],
              total: 1,
            });
          }
          return jsonResponse({});
        }),
      );
      render(<Harness />);
      await screen.findByText("custom_pack_tool");

      expect(screen.getByText("No MCP servers yet")).toBeTruthy();
      expect(screen.queryByText("composio_app_server")).toBeNull();
    });
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

  describe("Network built-ins (V5-25, V5-28)", () => {
    const NETWORK_PROVIDERS: ProvidersResponse = {
      providers: [
        { id: "tavily-search", kind: "web_search", label: "Tavily", vendor: "Tavily", package: "", python_class: "" },
        {
          id: "twilio-sms",
          kind: "sms",
          label: "Twilio",
          vendor: "Twilio",
          package: "",
          python_class: "",
          fields: [{ name: "from_number", label: "Sending number", type: "string", required: true }],
        },
        { id: "http-tool-secret", kind: "secret_bag", label: "Tool secrets", vendor: "LKAP", package: "", python_class: "" },
      ],
    } as unknown as ProvidersResponse;

    function stubNetworkFetch() {
      vi.stubGlobal(
        "fetch",
        vi.fn(async (url: string) => {
          if (url.includes("/providers")) return jsonResponse(NETWORK_PROVIDERS);
          if (url.includes("/packs")) return jsonResponse({ items: [{ manifest: { id: "generic", tool_names: ["custom_pack_tool"] } }] });
          if (url.includes("/tool-templates")) return jsonResponse({ items: [] });
          if (url.includes("/credentials")) return jsonResponse({ items: [] });
          if (url.includes("/tool-providers/composio/status")) return jsonResponse({ enabled: false, credential_id: null, connections: 0 });
          if (url.includes("/tools")) return jsonResponse(EMPTY_TOOLS);
          return jsonResponse({});
        }),
      );
    }

    it("shows a switch for the plain utility built-ins (calculate, spell_back)", async () => {
      stubNetworkFetch();
      render(<Harness />);
      await screen.findByText("custom_pack_tool");

      expect((screen.getByRole("switch", { name: "Calculate" }) as HTMLButtonElement).getAttribute("data-state")).toBe(
        "checked",
      );
      expect((screen.getByRole("switch", { name: "Spell back" }) as HTMLButtonElement).getAttribute("data-state")).toBe(
        "checked",
      );
    });

    it("picking a Web search vendor writes config.tools.web_search", async () => {
      stubNetworkFetch();
      render(<Harness />);
      await screen.findByText("custom_pack_tool");

      expect(screen.getAllByText("Not set", { selector: "p" }).length).toBeGreaterThan(0);
      fireEvent.click(screen.getByRole("button", { name: "Edit web search" }));
      fireEvent.click(await screen.findByText("Tavily"));

      await waitFor(() => expect(latest?.config.tools.web_search?.provider_id).toBe("tavily-search"));
    });

    it("picking an SMS vendor writes config.tools.sms and points to the Telephony section for saved contacts", async () => {
      stubNetworkFetch();
      render(<Harness />);
      await screen.findByText("custom_pack_tool");

      fireEvent.click(screen.getByRole("button", { name: "Edit send a text message" }));
      fireEvent.click(await screen.findByText("Twilio"));

      await waitFor(() => expect(latest?.config.tools.sms?.provider_id).toBe("twilio-sms"));
      expect(screen.getByText(/saved numbers.*live in the Telephony section/)).toBeTruthy();
    });

    it("Read a web page: adds a site name, rejects a URL, and removes a chip", async () => {
      stubNetworkFetch();
      render(<Harness />);
      await screen.findByText("custom_pack_tool");

      const input = screen.getByPlaceholderText("docs.example.com");
      fireEvent.change(input, { target: { value: "https://docs.example.com" } });
      fireEvent.click(screen.getByText("Add"));
      expect(await screen.findByText(/Use the site name only/)).toBeTruthy();
      expect(latest?.config.tools.fetch_url_allowed_hosts ?? []).toEqual([]);

      fireEvent.change(input, { target: { value: "Docs.Example.com" } });
      fireEvent.click(screen.getByText("Add"));
      await waitFor(() => expect(latest?.config.tools.fetch_url_allowed_hosts).toEqual(["docs.example.com"]));

      fireEvent.click(screen.getByRole("button", { name: "Remove docs.example.com" }));
      await waitFor(() => expect(latest?.config.tools.fetch_url_allowed_hosts).toEqual([]));
    });

    it("Notify your team: turning it on fills in a default secret name and reveals the fields", async () => {
      stubNetworkFetch();
      render(<Harness />);
      await screen.findByText("custom_pack_tool");

      expect(screen.queryByLabelText("Secret name")).toBeNull();
      fireEvent.click(screen.getByRole("switch", { name: "Notify your team" }));

      expect((await screen.findByLabelText("Secret name")).getAttribute("value")).toBe("TEAM_WEBHOOK_URL");
      expect(screen.getByRole("switch", { name: "Also notify on escalation" })).toBeTruthy();
      expect(screen.getByRole("switch", { name: "Include the recent conversation" })).toBeTruthy();
      await waitFor(() => expect(latest?.config.tools.notify_team?.secret_name).toBe("TEAM_WEBHOOK_URL"));

      fireEvent.click(screen.getByRole("switch", { name: "Notify your team" }));
      await waitFor(() => expect(latest?.config.tools.notify_team).toBeNull());
    });

    it('the HTTP tools "Add" row offers a template dialog ("From a template")', async () => {
      stubNetworkFetch();
      render(<Harness />);
      await screen.findByText("custom_pack_tool");

      fireEvent.click(screen.getByRole("button", { name: /From a template/ }));
      expect(await screen.findByText("Add tools from a template")).toBeTruthy();
    });
  });

  describe("max_tool_steps warning (V4-13, BACKGROUND-TOOLS.md §7, R-V4-35)", () => {
    const AGENT_LOW_STEPS = {
      ...AGENT,
      config: { ...AGENT.config, tools: { execution_default: "auto" as const, max_tool_steps: 3 } },
    } as AgentOut;

    it('auto-opens Advanced and marks the field with data-issue-path="tools.max_tool_steps" (a server issue at that path routes to this section)', async () => {
      stubFetch();
      const { container } = render(<Harness agent={AGENT_LOW_STEPS} />);
      await screen.findByText("custom_pack_tool");

      // No manual "Advanced" click needed — the field must be reachable for focusFieldFor to find it.
      const input = await waitFor(() => screen.getByLabelText("Tool steps per turn") as HTMLInputElement);
      expect(input.getAttribute("data-issue-path")).toBe("tools.max_tool_steps");
      expect(container.querySelector('[data-issue-path="tools.max_tool_steps"]')).toBe(input);
    });

    it("stays collapsed when the default is blocking", async () => {
      stubFetch();
      render(<Harness />);
      await screen.findByText("custom_pack_tool");
      expect(screen.queryByLabelText("Tool steps per turn")).toBeNull();
    });
  });
});
