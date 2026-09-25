import * as React from "react";

import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useFormContext } from "react-hook-form";
import { FileTextIcon, PlugIcon, WorkflowIcon, WrenchIcon } from "lucide-react";

import { AgentEditor } from "@/components/console/agents/agent-editor";
import { useSectionIssues } from "@/components/console/agents/editor/editor-context";
import type { EditorSectionDef, EditorSectionProps } from "@/components/console/agents/editor/types";
import { guardedHref } from "@/components/console/agents/editor/unsaved-guard";
import type { AgentEditorForm } from "@/components/console/lib/schemas";
import type { AgentOut, AgentUpdate, ValidationResult } from "@/contracts/lkap-contracts";

/**
 * The editor shell (WP-3) with stand-in sections: the real section
 * components belong to WP-4/WP-5 and have their own tests, so these sections
 * only bind one field each — enough to drive dirty state, validation dots
 * and issue lists through the shell.
 *
 * jsdom note: Radix popovers and menus position with floating-ui, whose
 * `isTopLayer` calls `element.matches(":popover-open")` / `(":modal")` on
 * every ancestor. jsdom's selector engine (nwsapi) takes seconds per open on
 * those pseudo-classes — the "Radix overlay hangs in jsdom" symptom. The
 * `beforeAll` below answers them with `false` (nothing is in the top layer in
 * jsdom), which brings an open from ~8 s to ~30 ms.
 */

const nativeMatches = Element.prototype.matches;
beforeAll(() => {
  Element.prototype.matches = function matches(this: Element, selector: string) {
    if (selector === ":popover-open" || selector === ":modal") return false;
    return nativeMatches.call(this, selector);
  };
});
afterAll(() => {
  Element.prototype.matches = nativeMatches;
});

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const routerReplace = vi.fn();
const routerPush = vi.fn();
let searchParams = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: routerReplace, push: routerPush }),
  usePathname: () => "/console/agents/a-1",
  useSearchParams: () => searchParams,
}));

function InstructionsStub() {
  const { register } = useFormContext<AgentEditorForm>();
  return (
    <label>
      Instructions
      <textarea {...register("config.instructions")} />
    </label>
  );
}

function ToolsStub({ agent }: EditorSectionProps) {
  const { issueFor } = useSectionIssues();
  const issue = issueFor("tools.tool_ids");
  return (
    <p>
      Tools of {agent.name}
      {issue ? <span data-testid="tools-field-issue">{issue.message}</span> : null}
    </p>
  );
}

function ProvidersStub() {
  return <p>Providers content</p>;
}

function FlowStub() {
  return <p>Flow canvas</p>;
}

const SECTIONS: EditorSectionDef[] = [
  { id: "providers", label: "Providers", icon: PlugIcon, order: 10, Component: ProvidersStub, issuePaths: ["pipeline"] },
  {
    id: "instructions",
    label: "Instructions & voice",
    icon: FileTextIcon,
    order: 20,
    Component: InstructionsStub,
    issuePaths: ["instructions", "voice"],
    issueKeywords: /\b(instruction|greeting)/i,
  },
  {
    id: "flow",
    label: "Flow",
    icon: WorkflowIcon,
    order: 30,
    Component: FlowStub,
    visible: ({ mode }) => mode === "flow",
    layout: "full",
  },
  {
    id: "tools",
    label: "Tools",
    icon: WrenchIcon,
    order: 50,
    Component: ToolsStub,
    issuePaths: ["tools"],
    issueKeywords: /\btool/i,
  },
];

const ref = (provider_id: string) => ({ provider_id, credential_id: null, model: null, fields: {} });

function makeAgent(overrides: Partial<AgentOut> = {}): AgentOut {
  return {
    id: "a-1",
    slug: "claims-intake",
    name: "Claims intake",
    description: "Takes first notice of loss",
    pack_id: "insurance_claim",
    ui_panel_id: "insurance_notebook",
    published: false,
    config_version: 2,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-20T00:00:00Z",
    mode: "prompt",
    connection_id: null,
    limits: { max_concurrent_sessions: 5, max_session_duration_s: 1800, rate_per_ip_per_min: 6, rate_per_agent_per_min: 60 },
    allowed_origins: [],
    config: {
      v: 2,
      instructions: "Be helpful.",
      pipeline: {
        mode: "cascaded",
        stt: ref("livekit-inference-stt"),
        llm: ref("livekit-inference-llm"),
        tts: ref("livekit-inference-tts"),
        turn_handling: {},
      },
      voice: { greeting: "Hi", greeting_mode: "say", language: "en", allow_interruptions: true },
      capabilities: { camera: false, screen_share: false, chat_input: true, vision_inject_per_turn: true },
      tools: { builtin_disabled: [], http_request_enabled: false, tool_ids: [], max_tool_steps: 3 },
      knowledge: { kb_ids: [], auto_inject: true, top_k: 4 },
      panel: { panel_id: "insurance_notebook", layout: "wide", blocks: [] },
      recording: { enabled: false, audio_only: true, storage_config_id: null, retention_days: null },
      qa: { enabled: false, rubric_prompt: null, model: null },
      pack_settings: {},
      timezone: "UTC",
    },
    ...overrides,
  };
}

/** A minimal-but-shaped `CostEstimate` (docs/v4/COSTS.md §3.1) for the rail/dialog. */
const COST_ESTIMATE_FIXTURE = {
  per_minute_usd: { low: "0.03", mid: "0.04", high: "0.05" },
  per_session_usd: { low: "0.15", mid: "0.20", high: "0.25" },
  session_minutes: 5,
  channel: "web",
  lines: [
    {
      slot: "llm",
      label: "Agent's thinking",
      provider_id: "livekit-inference-llm",
      model: "openai/gpt-4o-mini",
      unit: "tokens_in",
      quantity_per_min: "7860",
      quote: { provider_id: "livekit-inference-llm", unit: "tokens_in", usd_per_unit: "0.00000015", source: "table", as_of: "2026-09-23" },
      usd_per_min: "0.0012",
    },
  ],
  assumptions: [
    { key: "agent_talk_ratio", value: 0.45, low: 0.3, high: 0.6, unit: "share", source: "default", label: "How much the agent talks" },
  ],
  unpriced: [],
  priced_share: 1,
  price_version: "2026-09-23",
  as_of: "2026-09-23",
  sources: ["table"],
  caveats: [],
};

interface Server {
  agent: AgentOut;
  validation: ValidationResult;
  /** When set, the next config PUT answers 422 with these details. */
  rejectConfig: { errors: string[]; warnings: string[] } | null;
  puts: AgentUpdate[];
  validateCalls: number;
  deleted: boolean;
}

function json(status: number, body: unknown): Response {
  return { ok: status < 400, status, statusText: String(status), json: async () => body } as Response;
}

function stubServer(agent: AgentOut, validation: ValidationResult = { ok: true, errors: [], warnings: [] }): Server {
  const server: Server = { agent, validation, rejectConfig: null, puts: [], validateCalls: 0, deleted: false };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = (typeof input === "string" ? input : input.toString()).replace("/api/console/", "");
      const method = init?.method ?? "GET";
      const path = url.split("?")[0];
      if (path === `agents/${server.agent.id}` && method === "GET") return json(200, server.agent);
      if (path === `agents/${server.agent.id}/validate` && method === "POST") {
        server.validateCalls += 1;
        return json(200, server.validation);
      }
      if (path === `agents/${server.agent.id}` && method === "PUT") {
        const body = JSON.parse(init?.body as string) as AgentUpdate;
        server.puts.push(body);
        if (body.config && server.rejectConfig) {
          return json(422, {
            error: { code: "unprocessable_entity", message: "agent configuration is invalid", details: server.rejectConfig },
          });
        }
        const next: AgentOut = {
          ...server.agent,
          ...(body.name != null ? { name: body.name } : {}),
          ...(body.description != null ? { description: body.description } : {}),
          ...(body.published != null ? { published: body.published } : {}),
          ...(body.config ? { config: body.config, config_version: server.agent.config_version + 1 } : {}),
        };
        server.agent = next;
        return json(200, next);
      }
      if (path === `agents/${server.agent.id}` && method === "DELETE") {
        server.deleted = true;
        return { ok: true, status: 204, json: async () => undefined } as Response;
      }
      if (path === "providers") return json(200, { providers: [] });
      if (path === "packs") return json(200, { items: [] });
      if (path === "tools") return json(200, { items: [], total: 0 });
      // V4-16: the rail's shared cost estimate (docs/v4/COSTS.md §5 item 1) —
      // every editor render debounces one of these, so every stub server
      // needs an answer or the shell's fetch mock throws for it.
      if (path === "cost-estimates" && method === "POST") return json(200, COST_ESTIMATE_FIXTURE);
      if (path === "cost-estimates/assumptions" && method === "GET") {
        return json(200, { assumptions: COST_ESTIMATE_FIXTURE.assumptions, sessions_sampled: 3 });
      }
      if (path === "auth/me") {
        return json(200, {
          user: { id: "u1", email: "admin@example.test" },
          workspaces: [{ id: "ws1", name: "Test workspace", slug: "test", role: "admin" }],
        });
      }
      throw new Error(`Unhandled fetch: ${method} ${url}`);
    }),
  );
  return server;
}

function renderEditor(agent = makeAgent(), validation?: ValidationResult) {
  const server = stubServer(agent, validation);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const view = render(
    <QueryClientProvider client={client}>
      <AgentEditor agentId={agent.id} sections={SECTIONS} />
    </QueryClientProvider>,
  );
  return { ...view, server };
}

/** The vertical (≥ 1024 px) nav; the horizontal bar renders the same items for small screens. */
function listNav() {
  const nav = document.querySelector('nav[data-variant="list"]');
  if (!nav) throw new Error("section nav not rendered");
  return within(nav as HTMLElement);
}

/**
 * An open dialog or popover (`role="dialog"`), optionally by its accessible
 * name. Queried with selectors: `findByRole` over the whole editor is slow
 * once Radix marks the rest of the page `aria-hidden`.
 */
async function findDialog(name?: string | RegExp): Promise<HTMLElement> {
  return waitFor(() => {
    const dialogs = Array.from(document.querySelectorAll<HTMLElement>('[role="dialog"]'));
    const match = dialogs.find((el) => {
      if (name === undefined) return true;
      const labelId = el.getAttribute("aria-labelledby");
      const label = (labelId ? document.getElementById(labelId)?.textContent : null) ?? "";
      return typeof name === "string" ? label === name : name.test(label);
    });
    if (!match) throw new Error(`no dialog${name ? ` named ${String(name)}` : ""}`);
    return match;
  });
}

function header() {
  const el = document.querySelector('[data-slot="agent-editor-header"]');
  if (!el) throw new Error("header not rendered");
  return within(el as HTMLElement);
}

async function ready() {
  await screen.findByRole("heading", { level: 1, name: "Claims intake" });
  // `canWrite` (docs/v2/_asks.md V2-20-5) resolves from a separate `auth/me`
  // query; wait for it so Save/Publish aren't still showing their
  // role-disabled state when a test's first action clicks them.
  await waitFor(() => {
    const trigger = (screen.queryByRole("button", { name: "Publish" }) ??
      screen.queryByRole("button", { name: "Unpublish" })) as HTMLButtonElement | null;
    if (trigger) expect(trigger.disabled).toBe(false);
  });
}

beforeEach(() => {
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
  window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;
  Element.prototype.scrollIntoView = vi.fn();
  searchParams = new URLSearchParams();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("editor header", () => {
  it("shows the name as a heading, slug, status, connection and mode chips", async () => {
    renderEditor();
    await ready();
    expect(screen.getByText("/claims-intake")).toBeTruthy();
    expect(header().getByText("Draft")).toBeTruthy();
    expect(header().getByText("Default connection")).toBeTruthy();
    expect(document.querySelector('[data-slot="mode-chip"]')?.textContent).toContain("Prompt");
    // The publish Switch is gone; Publish is a button.
    expect(screen.queryByRole("switch")).toBeNull();
    expect(screen.getByRole("button", { name: "Publish" })).toBeTruthy();
    expect(screen.getByRole("link", { name: /Agents/ }).getAttribute("href")).toBe("/console/agents");
  });

  it("keeps Save disabled until the form is dirty and shows the unsaved indicator", async () => {
    renderEditor();
    await ready();
    const save = screen.getByRole("button", { name: "Save" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    expect(screen.queryByText("Unsaved changes")).toBeNull();

    fireEvent.click(listNav().getByRole("button", { name: "Instructions & voice" }));
    fireEvent.change(screen.getByLabelText("Instructions"), { target: { value: "Be brief." } });

    await screen.findByText("Unsaved changes");
    // `canWrite` (docs/v2/_asks.md V2-20-5) resolves from a separate
    // `auth/me` query that may still be in flight; wait for it rather than
    // asserting the instant "Unsaved changes" appears.
    await waitFor(() => expect(save.disabled).toBe(false));
  });

  it("edits the name inline: Enter applies, Escape cancels", async () => {
    renderEditor();
    await ready();
    fireEvent.click(screen.getByRole("button", { name: "Edit name" }));
    const input = screen.getByLabelText("Agent name");
    fireEvent.change(input, { target: { value: "Claims desk" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await screen.findByRole("heading", { level: 1, name: "Claims desk" });
    expect(screen.getByText("Unsaved changes")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Edit name" }));
    const again = screen.getByLabelText("Agent name");
    fireEvent.change(again, { target: { value: "Something else" } });
    fireEvent.keyDown(again, { key: "Escape" });
    await screen.findByRole("heading", { level: 1, name: "Claims desk" });
  });

  it("refuses an empty name", async () => {
    renderEditor();
    await ready();
    fireEvent.click(screen.getByRole("button", { name: "Edit name" }));
    const input = screen.getByLabelText("Agent name");
    fireEvent.change(input, { target: { value: "   " } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(await screen.findByText("Name is required")).toBeTruthy();
    expect(screen.queryByRole("heading", { level: 1 })).toBeNull();
  });
});

describe("overflow menu", () => {
  it("deletes the agent after confirmation and returns to the list", async () => {
    const { server } = renderEditor();
    await ready();
    fireEvent.keyDown(screen.getByRole("button", { name: "More agent actions" }), { key: "Enter" });
    fireEvent.click(await screen.findByRole("menuitem", { name: "Delete agent" }));

    const dialog = await findDialog(/Delete/);
    expect(within(dialog).getByText(/sessions are kept for the audit trail/)).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete agent" }));
    await waitFor(() => expect(server.deleted).toBe(true));
    expect(routerPush).toHaveBeenCalledWith("/console/agents");
  });
});

describe("section navigation", () => {
  it("opens the default section, hides flow in prompt mode and switches via the nav", async () => {
    renderEditor();
    await ready();
    expect(screen.getByRole("region", { name: "Providers section" })).toBeTruthy();
    expect(listNav().queryByRole("button", { name: /Flow/ })).toBeNull();

    fireEvent.click(listNav().getByRole("button", { name: "Tools" }));
    expect(screen.getByRole("region", { name: "Tools section" })).toBeTruthy();
    expect(screen.getByText("Tools of Claims intake")).toBeTruthy();
    expect(listNav().getByRole("button", { name: "Tools" }).getAttribute("aria-current")).toBe("page");
    expect(routerReplace).toHaveBeenCalledWith("/console/agents/a-1?section=tools", { scroll: false });
  });

  it("deep-links with ?section= and falls back to providers for unknown ids", async () => {
    searchParams = new URLSearchParams("section=instructions");
    const { unmount } = renderEditor();
    await ready();
    expect(screen.getByRole("region", { name: "Instructions & voice section" })).toBeTruthy();
    unmount();

    searchParams = new URLSearchParams("section=nope");
    renderEditor();
    await ready();
    expect(screen.getByRole("region", { name: "Providers section" })).toBeTruthy();
  });

  it("shows the flow section (full width, no rail) for flow agents", async () => {
    searchParams = new URLSearchParams("section=flow");
    renderEditor(makeAgent({ mode: "flow" }));
    await ready();
    expect(screen.getByText("Flow canvas")).toBeTruthy();
    expect(document.querySelector('[data-slot="mode-chip"]')?.textContent).toContain("Flow");
    expect(listNav().getByRole("button", { name: "Flow" }).getAttribute("aria-current")).toBe("page");
    expect(screen.queryByRole("complementary", { name: "Agent summary" })).toBeNull();
  });

  it("moves focus with the arrow keys and switches on Enter", async () => {
    renderEditor();
    await ready();
    const providers = listNav().getByRole("button", { name: "Providers" });
    providers.focus();
    fireEvent.keyDown(providers, { key: "ArrowDown" });
    const instructions = listNav().getByRole("button", { name: "Instructions & voice" });
    expect(document.activeElement).toBe(instructions);
    expect(instructions.getAttribute("tabindex")).toBe("0");
    expect(providers.getAttribute("tabindex")).toBe("-1");
    fireEvent.keyDown(instructions, { key: "End" });
    expect(document.activeElement).toBe(listNav().getByRole("button", { name: "Tools" }));
    fireEvent.click(document.activeElement as HTMLElement);
    expect(screen.getByRole("region", { name: "Tools section" })).toBeTruthy();
  });
});

describe("validation", () => {
  it("validates on open and puts dots on sections plus an issue list in the active one", async () => {
    renderEditor(makeAgent(), {
      ok: false,
      errors: ["pipeline.stt: unknown provider 'nope'"],
      warnings: ["tool 'lookup' was deleted"],
    });
    await ready();
    await waitFor(() => expect(listNav().getByRole("button", { name: /Providers.*Has errors/ })).toBeTruthy());
    expect(listNav().getByRole("button", { name: /Tools.*Has warnings/ })).toBeTruthy();

    const issues = screen.getByRole("region", { name: "Issues in this section" });
    expect(within(issues).getByText("Unknown provider 'nope'")).toBeTruthy();
    expect(within(issues).getByText("pipeline.stt")).toBeTruthy();
    expect(within(issues).queryByText(/lookup/)).toBeNull();

    fireEvent.click(listNav().getByRole("button", { name: /Tools/ }));
    expect(within(screen.getByRole("region", { name: "Issues in this section" })).getByText(/lookup/)).toBeTruthy();
  });

  it("uses issues[] paths when the api sends them, and exposes them to sections through useSectionIssues", async () => {
    searchParams = new URLSearchParams("section=tools");
    renderEditor(makeAgent(), {
      ok: true,
      warnings: ["unused"],
      issues: [{ path: "tools.tool_ids", message: "A tool was deleted", severity: "warning" }],
    });
    await ready();
    expect(await screen.findByTestId("tools-field-issue")).toBeTruthy();
    expect(screen.getByTestId("tools-field-issue").textContent).toBe("A tool was deleted");
  });
});

describe("saving", () => {
  async function dirtyInstructions(value = "Be brief.") {
    fireEvent.click(listNav().getByRole("button", { name: "Instructions & voice" }));
    fireEvent.change(screen.getByLabelText("Instructions"), { target: { value } });
    await screen.findByText("Unsaved changes");
  }

  it("PUTs AgentConfig v2 with the untouched v2 fields, then validates", async () => {
    const { server } = renderEditor();
    await ready();
    await dirtyInstructions();
    const validatesBefore = server.validateCalls;

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(server.puts).toHaveLength(1));
    const body = server.puts[0];
    expect(body.config?.v).toBe(2);
    expect(body.config?.instructions).toBe("Be brief.");
    expect(body.config?.panel).toEqual({ panel_id: "insurance_notebook", layout: "wide", blocks: [] });
    expect(body.config?.qa).toEqual({ enabled: false, rubric_prompt: null, model: null });
    expect(body.config?.pipeline.realtime).toBeNull();
    expect(body).not.toHaveProperty("published");
    await waitFor(() => expect(server.validateCalls).toBe(validatesBefore + 1));
    await waitFor(() => expect(screen.queryByText("Unsaved changes")).toBeNull());
    expect(await screen.findByText("Configuration looks good")).toBeTruthy();
  });

  it("saves on Cmd/Ctrl+S only when dirty", async () => {
    const { server } = renderEditor();
    await ready();
    fireEvent.keyDown(window, { key: "s", metaKey: true });
    await act(async () => {});
    expect(server.puts).toHaveLength(0);

    await dirtyInstructions();
    fireEvent.keyDown(window, { key: "s", ctrlKey: true });
    await waitFor(() => expect(server.puts).toHaveLength(1));
  });

  it("maps a 422 on save to the sections and moves to the first one with an error", async () => {
    const { server } = renderEditor();
    await ready();
    await dirtyInstructions();
    server.rejectConfig = { errors: ["pipeline.tts is required when mode is 'cascaded'"], warnings: [] };

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(screen.getByRole("region", { name: "Providers section" })).toBeTruthy());
    expect(listNav().getByRole("button", { name: /Providers.*Has errors/ })).toBeTruthy();
    expect(screen.getByText("Unsaved changes")).toBeTruthy();
  });

  it("blocks client-invalid forms and marks the section", async () => {
    const { server } = renderEditor();
    await ready();
    await dirtyInstructions("");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(listNav().getByRole("button", { name: /Instructions & voice.*Has errors/ })).toBeTruthy(),
    );
    expect(server.puts).toHaveLength(0);
    expect(within(screen.getByRole("region", { name: "Issues in this section" })).getByText("Instructions are required")).toBeTruthy();
  });
});

describe("publish", () => {
  it("validates the saved config on open and publishes", async () => {
    const { server } = renderEditor();
    await ready();
    fireEvent.click(screen.getByRole("button", { name: "Publish" }));
    const dialog = await findDialog();
    expect(within(dialog).getByText(/answer calls from anyone with the link/)).toBeTruthy();
    await within(dialog).findByText("Configuration looks good");
    expect(within(dialog).getByText("http://localhost:3000/s/claims-intake")).toBeTruthy();

    fireEvent.click(within(dialog).getByRole("button", { name: "Publish" }));
    await waitFor(() => expect(server.puts.at(-1)).toEqual({ published: true }));
    await screen.findByRole("button", { name: "Unpublish" });
  });

  it("blocks publishing while the saved config has errors", async () => {
    renderEditor(makeAgent(), { ok: false, errors: ["pipeline.stt: unknown provider"], warnings: [] });
    await ready();
    fireEvent.click(screen.getByRole("button", { name: "Publish" }));
    const dialog = await findDialog();
    await within(dialog).findByText("Fix 1 issue first");
    expect((within(dialog).getByRole("button", { name: "Publish" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("asks for an explicit 'Publish anyway' when there are warnings", async () => {
    renderEditor(makeAgent(), { ok: true, errors: [], warnings: ["pipeline.llm: model 'x' is not in the registry"] });
    await ready();
    fireEvent.click(screen.getByRole("button", { name: "Publish" }));
    const dialog = await findDialog();
    const button = await within(dialog).findByRole("button", { name: "Publish anyway" });
    expect((button as HTMLButtonElement).disabled).toBe(false);
  });

  it("offers Save and publish when the form is dirty", async () => {
    const { server } = renderEditor();
    await ready();
    fireEvent.click(listNav().getByRole("button", { name: "Instructions & voice" }));
    fireEvent.change(screen.getByLabelText("Instructions"), { target: { value: "Be brief." } });
    await screen.findByText("Unsaved changes");

    fireEvent.click(screen.getByRole("button", { name: "Publish" }));
    const dialog = await findDialog();
    expect(within(dialog).getByText("You have unsaved changes")).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: "Save and publish" }));

    await waitFor(() => expect(server.puts.at(-1)).toEqual({ published: true }));
    expect(server.puts[0].config?.instructions).toBe("Be brief.");
  });

  it("unpublishes a live agent after confirmation", async () => {
    const { server } = renderEditor(makeAgent({ published: true }));
    await ready();
    fireEvent.click(screen.getByRole("button", { name: "Unpublish" }));
    const popover = await findDialog();
    expect(within(popover).getByRole("link", { name: /Open page/ }).getAttribute("href")).toBe(
      "http://localhost:3000/s/claims-intake",
    );
    fireEvent.click(within(popover).getByRole("button", { name: "Unpublish" }));

    const confirm = await findDialog(/Unpublish/);
    expect(within(confirm).getByText("The public link stops answering immediately.")).toBeTruthy();
    fireEvent.click(within(confirm).getByRole("button", { name: "Unpublish" }));
    await waitFor(() => expect(server.puts.at(-1)).toEqual({ published: false }));
  });
});

describe("test call", () => {
  it("links to the test page in a new tab when the form is clean", async () => {
    renderEditor();
    await ready();
    const link = screen.getByRole("link", { name: /Test call/ });
    expect(link.getAttribute("href")).toBe("/s/claims-intake?mode=test");
    expect(link.getAttribute("target")).toBe("_blank");
  });

  it("asks to save first when the form is dirty", async () => {
    const { server } = renderEditor();
    await ready();
    fireEvent.click(listNav().getByRole("button", { name: "Instructions & voice" }));
    fireEvent.change(screen.getByLabelText("Instructions"), { target: { value: "Be brief." } });
    await screen.findByText("Unsaved changes");

    const tab = { opener: {}, location: { href: "" }, close: vi.fn() };
    const open = vi.fn(() => tab);
    vi.stubGlobal("open", open);

    fireEvent.click(screen.getByRole("button", { name: /Test call/ }));
    const dialog = await findDialog();
    expect(within(dialog).getByRole("link", { name: "Test the saved version" }).getAttribute("href")).toBe(
      "/s/claims-intake?mode=test",
    );
    fireEvent.click(within(dialog).getByRole("button", { name: "Save and test" }));

    await waitFor(() => expect(tab.location.href).toBe("/s/claims-intake?mode=test"));
    expect(open).toHaveBeenCalledWith("about:blank", "_blank");
    expect(tab.opener).toBeNull();
    expect(server.puts[0].config?.instructions).toBe("Be brief.");
  });
});

describe("unsaved guard", () => {
  it("confirms before leaving through an in-app link while dirty", async () => {
    renderEditor();
    await ready();
    fireEvent.click(listNav().getByRole("button", { name: "Instructions & voice" }));
    fireEvent.change(screen.getByLabelText("Instructions"), { target: { value: "Be brief." } });
    await screen.findByText("Unsaved changes");

    fireEvent.click(screen.getByRole("link", { name: /Agents/ }));
    const dialog = await findDialog("Leave without saving?");
    fireEvent.click(within(dialog).getByRole("button", { name: "Leave without saving" }));
    expect(routerPush).toHaveBeenCalledWith("/console/agents");
  });

  it("asks the browser to confirm unloads only while dirty", async () => {
    renderEditor();
    await ready();
    const clean = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(clean);
    expect(clean.defaultPrevented).toBe(false);

    fireEvent.click(listNav().getByRole("button", { name: "Instructions & voice" }));
    fireEvent.change(screen.getByLabelText("Instructions"), { target: { value: "Be brief." } });
    await screen.findByText("Unsaved changes");
    const dirty = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(dirty);
    expect(dirty.defaultPrevented).toBe(true);
  });

  describe("guardedHref", () => {
    const current = new URL("http://localhost:3000/console/agents/a-1?section=tools");

    function click(anchor: Partial<HTMLAnchorElement> & { href: string }, init: MouseEventInit = {}) {
      const a = document.createElement("a");
      a.setAttribute("href", anchor.href);
      if (anchor.target) a.target = anchor.target;
      document.body.appendChild(a);
      const event = new MouseEvent("click", { bubbles: true, cancelable: true, button: 0, ...init });
      Object.defineProperty(event, "target", { value: a });
      const result = guardedHref(event, current);
      a.remove();
      return result;
    }

    it.each([
      ["/console/sessions", {}, {}, "/console/sessions"],
      ["/console/agents/a-1?section=providers", {}, {}, "/console/agents/a-1?section=providers"],
      ["/console/agents/a-1?section=tools", {}, {}, null],
      ["https://example.com/", {}, {}, null],
      ["/console/sessions", { target: "_blank" }, {}, null],
      ["/console/sessions", {}, { metaKey: true }, null],
    ])("%s %j %j → %s", (href, anchor, init, expected) => {
      expect(click({ href, ...anchor }, init)).toBe(expected);
    });
  });
});

describe("summary rail", () => {
  it("summarises the agent and links rows to their sections", async () => {
    renderEditor();
    await ready();
    const rail = screen.getByRole("complementary", { name: "Agent summary" });
    expect(within(rail).getByText("Claim notebook")).toBeTruthy();
    expect(within(rail).getByText("8 built-in")).toBeTruthy();
    expect(within(rail).getByText("None attached")).toBeTruthy();
    expect(within(rail).getByText("Version 2")).toBeTruthy();
    expect((within(rail).getByLabelText("Description") as HTMLTextAreaElement).value).toBe("Takes first notice of loss");

    fireEvent.click(within(rail).getByRole("button", { name: /Tools: open the Tools section/ }));
    expect(screen.getByRole("region", { name: "Tools section" })).toBeTruthy();
  });

  it("edits the description as part of the form", async () => {
    const { server } = renderEditor();
    await ready();
    const rail = screen.getByRole("complementary", { name: "Agent summary" });
    fireEvent.change(within(rail).getByLabelText("Description"), { target: { value: "Answers claim calls" } });
    await screen.findByText("Unsaved changes");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(server.puts[0]?.description).toBe("Answers claim calls"));
  });

  it("shows the estimated cost with its band and opens the estimate dialog (docs/v4/COSTS.md §5 item 1)", async () => {
    renderEditor();
    await ready();
    const rail = screen.getByRole("complementary", { name: "Agent summary" });
    await waitFor(() => expect(within(rail).getByText(/≈ \$0\.0400\/min · estimate/)).toBeTruthy(), { timeout: 3000 });
    expect(within(rail).getByText(/typically \$0\.0300–\$0\.0500/)).toBeTruthy();

    fireEvent.click(within(rail).getByRole("button", { name: /Cost/ }));
    const dialog = await findDialog("Cost estimate");
    expect(within(dialog).getByText(/Agent's thinking/)).toBeTruthy();
  });
});
