import * as React from "react";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { FormProvider, useForm } from "react-hook-form";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { toFormValues } from "@/components/console/agents/editor/form-values";
import { ConnectedAppsCard } from "@/components/console/agents/tabs/connected-apps-card";
import { ToolsList } from "@/components/console/tools/tools-list";
import { agentEditorFormSchema, type AgentEditorForm } from "@/components/console/lib/schemas";
import { zodResolver } from "@/components/console/lib/zod-resolver";
import type { AgentOut, ProvidersResponse, ToolOut, ToolPage } from "@/contracts/lkap-contracts";
import { ACTION_DELETE_REPO, actionFixture, actionPage, appsStatusFixture, connectionFixture, connectionPage } from "./fixtures/apps";

/**
 * V5-48 (docs/v5/COMPOSIO.md §6, the console-side half): the agent editor's
 * **Connected apps** card (`connected-apps-card.tsx`) and the read-only rows
 * it drives in the shared tools list (`tools-list.tsx`'s Kind column and
 * per-row actions).
 */

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
beforeEach(() => {
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
  // Radix `Select` (the mode picker) needs these in jsdom.
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn().mockReturnValue(false);
  Element.prototype.releasePointerCapture = vi.fn();
});
afterEach(() => vi.unstubAllGlobals());

const AGENT = {
  id: "agent-1",
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

interface Call {
  url: string;
  method: string;
  body: Record<string, unknown> | undefined;
}

function jsonResponse(body: unknown, status = 200): Response {
  return { ok: status < 400, status, json: async () => body } as Response;
}

/** Routes every request `ConnectedAppsCard` (and its `ActionsDialog`) can make. */
function stubApi(overrides: (call: Call) => { status: number; body: unknown } | undefined = () => undefined) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      const call = { url: String(input), method: init?.method ?? "GET", body };
      calls.push(call);
      const override = overrides(call);
      if (override) return jsonResponse(override.body, override.status);
      if (call.url.includes("/tool-providers/composio/status")) return jsonResponse(appsStatusFixture());
      if (call.url.includes("/tool-providers/composio/connections") && call.method === "GET") {
        return jsonResponse(connectionPage([connectionFixture()]));
      }
      if (/\/toolkits\/[^/?]+\/actions/.test(call.url)) return jsonResponse(actionPage());
      if (call.url.includes("/agents")) return jsonResponse({ items: [AGENT], total: 1 });
      if (call.url.includes("/materialise")) {
        return jsonResponse({ connection_id: "conn_github", picked_actions: ["GITHUB_LIST_REPOS"], tools_created: ["tool-1"] });
      }
      return jsonResponse({});
    }),
  );
  return calls;
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
          <ConnectedAppsCard agentId={agent.id} />
        </form>
      </FormProvider>
    </QueryClientProvider>
  );
}

describe("ConnectedAppsCard", () => {
  it("shows an empty state pointing at Tools -> Apps when Apps isn't set up", async () => {
    stubApi((call) => (call.url.includes("/tool-providers/composio/status") ? { status: 200, body: appsStatusFixture({ enabled: false }) } : undefined));
    render(<Harness />);

    expect(await screen.findByText("Apps aren't set up yet")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Go to Apps" })).toHaveProperty("href", expect.stringContaining("/console/tools?tab=apps"));
  });

  it("renders the four modes and marks the picker with data-issue-path=\"tools.apps.mode\" (a server issue at that path focuses the card)", async () => {
    stubApi();
    const { container } = render(<Harness />);
    await screen.findByText("Connected apps", { selector: "h2" });

    const trigger = container.querySelector('[data-issue-path="tools.apps.mode"]');
    expect(trigger).toBeTruthy();
    fireEvent.click(trigger!);
    const listbox = await screen.findByRole("listbox");
    expect(within(listbox).getByText("Off")).toBeTruthy();
    expect(within(listbox).getByText("Use picked actions (recommended)")).toBeTruthy();
    expect(within(listbox).getByText("Let the agent use an app server")).toBeTruthy();
    expect(within(listbox).getByText("Let the agent find tools itself")).toBeTruthy();
  });

  it("shows the latency note only for the two dynamic modes", async () => {
    stubApi();
    const { container } = render(<Harness />);
    await screen.findByText("Connected apps", { selector: "h2" });
    const latency = /looks tools up during the call/;

    expect(screen.queryByText(latency)).toBeNull(); // starts "off"

    async function selectMode(label: string) {
      fireEvent.click(container.querySelector('[data-issue-path="tools.apps.mode"]')!);
      const listbox = await screen.findByRole("listbox");
      fireEvent.click(within(listbox).getByText(label));
    }

    await selectMode("Use picked actions (recommended)");
    expect(screen.queryByText(latency)).toBeNull();

    await selectMode("Let the agent use an app server");
    expect(await screen.findByText(latency)).toBeTruthy();

    await selectMode("Let the agent find tools itself");
    expect(await screen.findByText(latency)).toBeTruthy();
  });

  it('in "Use picked actions" mode, an app\'s Actions button opens the picker preselected to attach to this agent, and posts materialise with this agent_id', async () => {
    const calls = stubApi((call) => {
      if (/\/toolkits\/[^/?]+\/actions/.test(call.url)) return { status: 200, body: actionPage([actionFixture()]) };
      if (call.url.endsWith("/tools") && call.method === "GET") {
        return {
          status: 200,
          body: {
            items: [
              {
                id: "tool-1",
                name: "googlecalendar_list_repos",
                kind: "provider",
                agent_id: null,
                enabled: true,
                created_at: "2026-09-01T00:00:00Z",
                updated_at: "2026-09-01T00:00:00Z",
                definition: {
                  kind: "provider",
                  name: "googlecalendar_list_repos",
                  description: "List repos",
                  parameters: {},
                  tool_slug: "GITHUB_LIST_REPOS",
                  connection_id: "conn_github",
                  subject: "ws:ws1",
                },
              },
            ],
            total: 1,
          },
        };
      }
      return undefined;
    });
    const { container } = render(<Harness />);
    await screen.findByText("Connected apps", { selector: "h2" });

    fireEvent.click(container.querySelector('[data-issue-path="tools.apps.mode"]')!);
    fireEvent.click(within(await screen.findByRole("listbox")).getByText("Use picked actions (recommended)"));

    fireEvent.click(await screen.findByRole("button", { name: "Actions" }));
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByText("List repositories");
    fireEvent.click(within(dialog).getByRole("checkbox", { name: /List repositories/ }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Add as tools and attach to agent" }));

    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/materialise") && c.method === "POST")).toBe(true));
    const call = calls.find((c) => c.url.endsWith("/materialise"))!;
    expect(call.body?.agent_id).toBe(AGENT.id);

    // The attach happened server-side as this agent's own config save
    // (`attach_tools`) — without merging the result into this form's
    // `tool_ids`, the next Save from this open editor would post the stale
    // (empty) list and silently un-attach the action just added.
    await waitFor(() => expect(latest?.config.tools.tool_ids).toContain("tool-1"));
    // And it's visible right away as an attached-action chip, not just in
    // `tool_ids` — a materialised action is a *shared* tool with no row of
    // its own in this agent's "owned tools" query.
    expect(await screen.findByText("googlecalendar_list_repos")).toBeTruthy();
  });

  it("removing an attached action chip detaches it (tool_ids), without deleting the tool", async () => {
    stubApi((call) => {
      if (call.url.includes("/tools") && call.method === "GET") {
        return {
          status: 200,
          body: {
            items: [
              {
                id: "tool-1",
                name: "googlecalendar_list_repos",
                kind: "provider",
                agent_id: null,
                enabled: true,
                created_at: "2026-09-01T00:00:00Z",
                updated_at: "2026-09-01T00:00:00Z",
                definition: {
                  kind: "provider",
                  name: "googlecalendar_list_repos",
                  description: "List repos",
                  parameters: {},
                  tool_slug: "GITHUB_LIST_REPOS",
                  connection_id: "conn_github",
                  subject: "ws:ws1",
                },
              },
            ],
            total: 1,
          },
        };
      }
      return undefined;
    });
    const agentWithTool = {
      ...AGENT,
      config: { ...AGENT.config, tools: { tool_ids: ["tool-1"], apps: { mode: "actions" } } },
    } as unknown as AgentOut;
    render(<Harness agent={agentWithTool} />);
    await screen.findByText("Connected apps", { selector: "h2" });

    const chip = await screen.findByText("googlecalendar_list_repos");
    fireEvent.click(within(chip.closest("li")!).getByRole("button", { name: /Remove/ }));

    await waitFor(() => expect(latest?.config.tools.tool_ids ?? []).not.toContain("tool-1"));
  });

  it("keeps the mode picker reachable (data-issue-path intact) when the workspace turns Apps off but this agent is still on a dynamic mode", async () => {
    stubApi((call) => (call.url.includes("/tool-providers/composio/status") ? { status: 200, body: appsStatusFixture({ enabled: false }) } : undefined));
    const agentInServerMode = {
      ...AGENT,
      config: { ...AGENT.config, tools: { apps: { mode: "server" } } },
    } as unknown as AgentOut;
    const { container } = render(<Harness agent={agentInServerMode} />);

    // Not the pure onboarding empty state — the picker (and its
    // `data-issue-path`, which `apps_issues`' `tools.apps.mode` error needs
    // to focus) must stay reachable so a builder can fix it.
    expect(await screen.findByText(/turned off for this workspace/)).toBeTruthy();
    expect(container.querySelector('[data-issue-path="tools.apps.mode"]')).toBeTruthy();
    expect(screen.queryByText("Apps aren't set up yet")).toBeNull();
  });

  it("in the app-server mode, a destructive action starts unchecked in \"Actions the agent may take\"", async () => {
    stubApi((call) => {
      if (/\/toolkits\/[^/?]+\/actions/.test(call.url)) return { status: 200, body: actionPage([actionFixture(), ACTION_DELETE_REPO]) };
      return undefined;
    });
    const { container } = render(<Harness />);
    await screen.findByText("Connected apps", { selector: "h2" });

    fireEvent.click(container.querySelector('[data-issue-path="tools.apps.mode"]')!);
    fireEvent.click(within(await screen.findByRole("listbox")).getByText("Let the agent use an app server"));

    const readCheckbox = await screen.findByRole("checkbox", { name: /List repositories/ });
    const destructiveCheckbox = await screen.findByRole("checkbox", { name: /Delete a repository/ });
    expect(readCheckbox.getAttribute("aria-checked")).toBe("true");
    expect(destructiveCheckbox.getAttribute("aria-checked")).toBe("false");
  });
});

describe("agentEditorFormSchema — tools.apps survives the zod parse (docs/v5/_asks.md #25)", () => {
  it("does not strip config.tools.apps.mode on a resolver parse (a z.object with no `apps` field silently drops it)", async () => {
    // A fully valid config (unlike the other fixtures' minimal `AGENT`, whose
    // incomplete `pipeline` is fine for component tests but would fail this
    // resolver on unrelated fields, leaving `result.values` = {} either way).
    const values: AgentEditorForm = {
      name: "Claims",
      description: "",
      ui_panel_id: "generic",
      mode: "prompt",
      connection_id: null,
      limits: { max_concurrent_sessions: 5, max_session_duration_s: 1800, rate_per_ip_per_min: 6, rate_per_agent_per_min: 60 },
      allowed_origins: [],
      config: {
        instructions: "Hi",
        pipeline: {
          mode: "cascaded",
          stt: { provider_id: "deepgram" },
          llm: { provider_id: "openai" },
          tts: { provider_id: "deepgram-tts" },
          avatar_options: { participant_name: "Avatar", video_quality: null, idle_timeout_s: null, max_duration_s: null },
          turn_handling: {},
        },
        voice: { greeting: "hi", greeting_mode: "say", language: "en", allow_interruptions: true, thinking_sound: "none" },
        capabilities: { camera: false, screen_share: false, chat_input: true, vision_inject_per_turn: true },
        tools: {
          builtin_disabled: [],
          http_request_enabled: false,
          tool_ids: [],
          max_tool_steps: 3,
          execution_default: "blocking",
          builtin_execution: {},
          apps: { mode: "server", allowed_toolkits: ["github"], denied_actions: [], router: { search: true, execute: true, manage_connections: false } },
        },
        knowledge: { kb_ids: [], auto_inject: true, top_k: 4 },
        pack_settings: {},
        timezone: "UTC",
        recording: { enabled: false, audio_only: true, storage_config_id: null, retention_days: null },
        panel: { panel_id: "composite", layout: "side", blocks: [] },
        telephony: { transfer_targets: [] },
      },
    } as unknown as AgentEditorForm;

    const result = await zodResolver(agentEditorFormSchema)(values, undefined, { fields: {}, shouldUseNativeValidation: false });
    expect(result.errors).toEqual({});
    expect((result.values as AgentEditorForm).config.tools.apps?.mode).toBe("server");
    expect((result.values as AgentEditorForm).config.tools.apps?.allowed_toolkits).toEqual(["github"]);
  });
});

// ---- tools-list.tsx: the read-only rows for Composio-origin entries ----

const PROVIDERS: ProvidersResponse = { providers: [] };

function providerTool(overrides: Partial<ToolOut> = {}): ToolOut {
  return {
    id: "tool-app-1",
    name: "googlecalendar_find_free_slots",
    kind: "provider",
    agent_id: null,
    enabled: true,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    definition: {
      kind: "provider",
      provider: "composio",
      name: "googlecalendar_find_free_slots",
      description: "Find a free slot on the calendar.",
      parameters: {},
      tool_slug: "GOOGLECALENDAR_FIND_FREE_SLOTS",
      connection_id: "conn_github",
      toolkit: "googlecalendar",
      subject: "ws:ws1",
    },
    ...overrides,
  } as ToolOut;
}

function mcpOriginTool(kind: "server" | "router", overrides: Partial<ToolOut> = {}): ToolOut {
  return {
    id: `tool-mcp-${kind}`,
    name: kind === "server" ? "composio_app_server" : "composio_tool_router",
    kind: "mcp",
    agent_id: null,
    enabled: true,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    definition: {
      kind: "mcp",
      name: kind === "server" ? "composio_app_server" : "composio_tool_router",
      url: "https://backend.composio.dev/v3/mcp/abc?user_id=ws:ws1",
      origin: { provider: "composio", kind, remote_id: "abc" },
    },
    ...overrides,
  } as ToolOut;
}

function stubToolsListApi(tools: ToolOut[]) {
  const page: ToolPage = { items: tools, total: tools.length };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/providers")) return jsonResponse(PROVIDERS);
      if (url.includes("/tool-providers/composio/connections")) return jsonResponse(connectionPage([connectionFixture({ id: "conn_github", toolkit: "googlecalendar", toolkit_name: "Google Calendar" })]));
      if (url.includes("/tools")) return jsonResponse(page);
      if (url.includes("/agents")) return jsonResponse({ items: [], total: 0 });
      return jsonResponse({});
    }),
  );
}

function renderToolsList() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ToolsList />
    </QueryClientProvider>,
  );
}

/**
 * `ResponsiveTable` renders the desktop table and the mobile card list at
 * the same time (CSS, not conditional rendering, picks which shows —
 * `console-agents-list.test.tsx` established this pattern), so row-content
 * assertions are scoped to the table half to avoid an ambiguous match with
 * its card twin.
 */
function tableScope() {
  const el = document.querySelector('[data-slot="responsive-table-table"]');
  if (!el) throw new Error("responsive table not rendered");
  return within(el as HTMLElement);
}

describe("ToolsList — read-only rows for Composio-origin entries (docs/v5/_asks.md #16)", () => {
  it("shows an App chip (with the connected app's name) for a provider tool, instead of HTTP/MCP", async () => {
    stubToolsListApi([providerTool()]);
    renderToolsList();

    await waitFor(() => expect(tableScope().getByText("googlecalendar_find_free_slots")).toBeTruthy());
    const table = tableScope();
    expect(table.getByText("App")).toBeTruthy();
    // The chip's identity mark carries the *connected app's* name (resolved via
    // `connection_id` against the connections list, docs/v5/_asks.md #16),
    // not just the tool's own raw `toolkit` slug.
    expect(table.getByRole("img", { name: "Google Calendar" })).toBeTruthy();
    expect(table.queryByText("MCP")).toBeNull();
  });

  it('shows "App server" for a server-origin MCP entry, read-only, with no edit or delete button', async () => {
    stubToolsListApi([mcpOriginTool("server")]);
    renderToolsList();

    await waitFor(() => expect(tableScope().getByText("composio_app_server")).toBeTruthy());
    const table = tableScope();
    expect(table.getByText("App server")).toBeTruthy();
    expect(table.getByText("Managed from the Connected apps card")).toBeTruthy();
    expect(table.queryByRole("button", { name: /Edit composio_app_server/ })).toBeNull();
    expect(table.queryByRole("button", { name: /Delete composio_app_server/ })).toBeNull();
  });

  it('shows "Tool finder" for a router-origin MCP entry, read-only', async () => {
    stubToolsListApi([mcpOriginTool("router")]);
    renderToolsList();

    await waitFor(() => expect(tableScope().getByText("composio_tool_router")).toBeTruthy());
    const table = tableScope();
    expect(table.getByText("Tool finder")).toBeTruthy();
    expect(table.getByText("Managed from the Connected apps card")).toBeTruthy();
  });

  it("still shows a plain MCP row (with its usual edit/delete actions) for a non-origin MCP server", async () => {
    stubToolsListApi([
      {
        id: "tool-plain-mcp",
        name: "my_mcp_server",
        kind: "mcp",
        agent_id: null,
        enabled: true,
        created_at: "2026-09-01T00:00:00Z",
        updated_at: "2026-09-01T00:00:00Z",
        definition: { kind: "mcp", name: "my_mcp_server", url: "https://example.test/mcp" },
      } as ToolOut,
    ]);
    renderToolsList();

    await waitFor(() => expect(tableScope().getByText("my_mcp_server")).toBeTruthy());
    const table = tableScope();
    expect(table.getByText("MCP")).toBeTruthy();
    expect(table.getByRole("button", { name: "Edit my_mcp_server" })).toBeTruthy();
    expect(table.getByRole("button", { name: "Delete my_mcp_server" })).toBeTruthy();
  });
});
