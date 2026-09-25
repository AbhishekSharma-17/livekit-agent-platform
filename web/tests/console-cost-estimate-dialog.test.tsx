import * as React from "react";
import { readFileSync } from "node:fs";
import path from "node:path";

import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { FormProvider, useForm } from "react-hook-form";

import { CostEstimateDialog } from "@/components/console/agents/editor/cost-estimate-dialog";
import { EditorContextProvider, type EditorContextValue } from "@/components/console/agents/editor/editor-context";
import { resetEstimateSettings } from "@/components/console/lib/cost-hooks";
import type { AgentEditorForm } from "@/components/console/lib/schemas";
import type { AgentOut } from "@/contracts/lkap-contracts";

/**
 * The Cost estimate dialog (docs/v4/COSTS.md §5 item 1, R-V4-50): the band,
 * an editable assumption refetching with its own key, "Use my workspace's
 * averages" gated at 10 sessions, the unpriced list's admin-only "Set a
 * price", and the no-jargon-outside-the-disclosure rule enforced on the
 * source itself (the acceptance's own grep, run here so a regression fails
 * the unit suite, not just a manual review).
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

beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  Element.prototype.scrollIntoView = vi.fn();
  // Every `DialogHarness` in this file estimates the same fixed agent id —
  // without this, "Use my workspace's averages" flipped on in one test
  // would still read as on in the next (the settings cache/localStorage is
  // module-level and per-agent, by design, so every surface reading the
  // same agent shares one object).
  resetEstimateSettings();
});
afterEach(() => {
  vi.unstubAllGlobals();
});

function makeAgentOut(overrides: Partial<AgentOut> = {}): AgentOut {
  return {
    id: "agent-1",
    slug: "agent-1",
    name: "Test agent",
    description: "",
    pack_id: "generic",
    ui_panel_id: "generic",
    published: false,
    config_version: 1,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    mode: "prompt",
    connection_id: null,
    limits: {},
    allowed_origins: [],
    config: {
      v: 2,
      instructions: "Be helpful.",
      pipeline: { mode: "cascaded", stt: null, llm: null, tts: null },
      voice: {},
      capabilities: { camera: false, screen_share: false, chat_input: true, vision_inject_per_turn: false },
      tools: { builtin_disabled: [], http_request_enabled: false, tool_ids: [], max_tool_steps: 3 },
      knowledge: { kb_ids: [], auto_inject: true, top_k: 4 },
      panel: { panel_id: "generic", layout: "wide", blocks: [] },
      recording: { enabled: false, audio_only: true, storage_config_id: null, retention_days: null },
      pack_settings: {},
      timezone: "UTC",
    },
    ...overrides,
  } as AgentOut;
}

const COST_ESTIMATE = {
  per_minute_usd: { low: "0.03", mid: "0.04", high: "0.05" },
  per_session_usd: { low: "0.15", mid: "0.20", high: "0.25" },
  session_minutes: 5,
  channel: "web",
  lines: [
    {
      slot: "tts",
      label: "Agent's voice",
      provider_id: "cartesia-tts",
      model: "sonic-3",
      unit: "chars",
      quantity_per_min: "405",
      quote: { provider_id: "cartesia-tts", unit: "chars", usd_per_unit: "0.00005", source: "table", as_of: "2026-09-23" },
      usd_per_min: "0.0203",
    },
    {
      slot: "llm",
      label: "Agent's thinking",
      provider_id: "acme-llm",
      model: null,
      unit: "tokens_in",
      note: "no price",
    },
  ],
  assumptions: [
    { key: "agent_talk_ratio", value: 0.45, low: 0.3, high: 0.6, unit: "share", source: "default", label: "How much the agent talks" },
    { key: "session_minutes", value: 5, low: 2, high: 15, unit: "minutes", source: "default", label: "Call length" },
  ],
  unpriced: ["Agent's thinking — acme-llm"],
  priced_share: 0.5,
  price_version: "2026-09-23",
  as_of: "2026-09-23",
  sources: ["table"],
  caveats: [],
};

interface FetchLog {
  method: string;
  url: string;
  body: unknown;
}

function stubFetch(role: "admin" | "builder", sessionsSampled = 3) {
  const calls: FetchLog[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    let body: unknown;
    if (typeof init?.body === "string") {
      try {
        body = JSON.parse(init.body);
      } catch {
        body = init.body;
      }
    }
    calls.push({ method, url, body });
    if (url.includes("/cost-estimates/assumptions")) {
      return { ok: true, status: 200, json: async () => ({ assumptions: COST_ESTIMATE.assumptions, sessions_sampled: sessionsSampled }) } as Response;
    }
    if (url.includes("/cost-estimates") && method === "POST") {
      // Mirrors R-V4-46: once the request carries `workspace_averages:
      // true`, the answer's assumption comes back `source: "workspace"`
      // with the workspace's own value, not the default.
      const workspaceAverages = (body as { workspace_averages?: boolean } | undefined)?.workspace_averages === true;
      const estimate = workspaceAverages
        ? {
            ...COST_ESTIMATE,
            assumptions: COST_ESTIMATE.assumptions.map((a) =>
              a.key === "agent_talk_ratio" ? { ...a, value: 0.52, source: "workspace" as const } : a,
            ),
          }
        : COST_ESTIMATE;
      return { ok: true, status: 200, json: async () => estimate } as Response;
    }
    if (url.includes("/workspace/prices")) {
      return { ok: true, status: 200, json: async () => ({ prices: [] }) } as Response;
    }
    if (url.includes("auth/me")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ user: { id: "u1", email: "a@b.test" }, workspaces: [{ id: "w1", name: "W", slug: "w", role }] }),
      } as Response;
    }
    return { ok: true, status: 200, json: async () => ({ items: [], total: 0 }) } as Response;
  });
  return { fetch: fn, calls };
}

function DialogHarness({ open = true }: { open?: boolean }) {
  const agent = makeAgentOut();
  const form = useForm<AgentEditorForm>({
    defaultValues: {
      name: agent.name,
      description: agent.description ?? "",
      ui_panel_id: agent.ui_panel_id,
      mode: "prompt",
      connection_id: null,
      limits: {},
      allowed_origins: [],
      config: {
        instructions: agent.config.instructions,
        pipeline: agent.config.pipeline,
        voice: agent.config.voice,
        capabilities: agent.config.capabilities,
        tools: agent.config.tools,
        knowledge: agent.config.knowledge,
        pack_settings: {},
        timezone: "UTC",
        recording: agent.config.recording,
        panel: agent.config.panel,
        flow: null,
        telephony: { transfer_targets: [] },
      },
    } as unknown as AgentEditorForm,
  });
  const ctx: EditorContextValue = {
    agent,
    sections: [],
    activeSection: "providers",
    goToSection: () => {},
    issues: [],
    focusIssue: () => {},
  };
  const [isOpen, setIsOpen] = React.useState(open);
  return (
    <FormProvider {...form}>
      <EditorContextProvider value={ctx}>
        <CostEstimateDialog open={isOpen} onOpenChange={setIsOpen} />
      </EditorContextProvider>
    </FormProvider>
  );
}

function withClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("CostEstimateDialog", () => {
  it("shows the per-minute band and the breakdown once the estimate resolves", async () => {
    vi.stubGlobal("fetch", stubFetch("admin").fetch);
    withClient(<DialogHarness />);
    const dialog = await screen.findByRole("dialog", { name: "Cost estimate" });
    await waitFor(() => expect(within(dialog).getByText(/≈ \$0\.0400\/min · estimate/)).toBeTruthy());
    expect(within(dialog).getByText(/typically \$0\.0300–\$0\.0500/)).toBeTruthy();
    expect(within(dialog).getByText("Agent's voice")).toBeTruthy();
    expect(within(dialog).getByText(/\$0\.0203\/min/)).toBeTruthy();
  });

  it("refetches with the edited assumption's own key when 'How much the agent talks' changes", async () => {
    const { fetch, calls } = stubFetch("admin");
    vi.stubGlobal("fetch", fetch);
    withClient(<DialogHarness />);
    const dialog = await screen.findByRole("dialog", { name: "Cost estimate" });
    const field = await within(dialog).findByLabelText("How much the agent talks");
    calls.length = 0;
    fireEvent.change(field, { target: { value: "0.6" } });
    await waitFor(
      () => {
        const posted = calls.find(
          (c) => c.method === "POST" && c.url.includes("cost-estimates") && (c.body as { assumptions?: Record<string, number> })?.assumptions?.agent_talk_ratio === 0.6,
        );
        expect(posted).toBeTruthy();
      },
      { timeout: 2000 },
    );
  });

  it("disables 'Use my workspace's averages' under 10 sessions, posts workspace_averages once enabled, and marks the assumption's source", async () => {
    const { fetch, calls } = stubFetch("admin", 12);
    vi.stubGlobal("fetch", fetch);
    withClient(<DialogHarness />);
    const dialog = await screen.findByRole("dialog", { name: "Cost estimate" });
    const toggle = await within(dialog).findByRole("switch", { name: /Use my workspace's averages/ });
    await waitFor(() => expect((toggle as HTMLButtonElement).disabled).toBe(false));
    calls.length = 0;
    fireEvent.click(toggle);
    await waitFor(() => {
      const posted = calls.find(
        (c) => c.method === "POST" && c.url.includes("cost-estimates") && (c.body as { workspace_averages?: boolean })?.workspace_averages === true,
      );
      expect(posted).toBeTruthy();
    });
    // The response's `Assumption.source: "workspace"` is visible, and the
    // field itself now shows the workspace's own value (not the default).
    await waitFor(() => expect(within(dialog).getByText("Your workspace's average")).toBeTruthy());
    expect((await within(dialog).findByLabelText("How much the agent talks") as HTMLInputElement).value).toBe("0.52");
  });

  it("needs-10-sessions notice and a disabled toggle when the workspace has fewer", async () => {
    vi.stubGlobal("fetch", stubFetch("admin", 3).fetch);
    withClient(<DialogHarness />);
    const dialog = await screen.findByRole("dialog", { name: "Cost estimate" });
    const toggle = await within(dialog).findByRole("switch", { name: /Use my workspace's averages/ });
    await waitFor(() => expect(screen.getByText(/this workspace has 3/)).toBeTruthy());
    expect((toggle as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows 'Set a price' for an admin on an unpriced line", async () => {
    vi.stubGlobal("fetch", stubFetch("admin").fetch);
    withClient(<DialogHarness />);
    const dialog = await screen.findByRole("dialog", { name: "Cost estimate" });
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Set a price" })).toBeTruthy());
  });

  it("never shows 'Set a price' for a builder", async () => {
    vi.stubGlobal("fetch", stubFetch("builder").fetch);
    withClient(<DialogHarness />);
    const dialog = await screen.findByRole("dialog", { name: "Cost estimate" });
    await waitFor(() => expect(within(dialog).getAllByText(/acme-llm/).length).toBeGreaterThan(0));
    expect(within(dialog).queryByRole("button", { name: "Set a price" })).toBeNull();
  });

  it("opens the Your prices dialog prefilled for the unpriced line", async () => {
    vi.stubGlobal("fetch", stubFetch("admin").fetch);
    withClient(<DialogHarness />);
    const dialog = await screen.findByRole("dialog", { name: "Cost estimate" });
    const setPrice = await waitFor(() => within(dialog).getByRole("button", { name: "Set a price" }));
    fireEvent.click(setPrice);
    const pricesDialog = await screen.findByRole("dialog", { name: "Your prices" });
    await waitFor(() => expect(within(pricesDialog).getByDisplayValue("acme-llm")).toBeTruthy());
  });
});

describe("CostEstimateDialog — source rules (R-V4-50, D-V4-47)", () => {
  const source = readFileSync(
    path.resolve(__dirname, "../src/components/console/agents/editor/cost-estimate-dialog.tsx"),
    "utf-8",
  );

  it("never imports the side-drawer primitive (R-V3-2)", () => {
    expect(source).not.toMatch(/@\/components\/ui\/sheet/);
  });

  it("keeps every jargon word inside TechnicalUnitDisclosure alone", () => {
    const banned = /token|egress|SIP|STT|TTS|LLM/g;
    const funcStart = source.indexOf("function TechnicalUnitDisclosure");
    expect(funcStart).toBeGreaterThan(-1);
    // Brace-match from the function's *body* opening `{` (after its
    // parameter list's own closing paren — the destructured `{ line }`
    // parameter has braces of its own) to the real closing one (a naive
    // `indexOf("\n}")` would stop at the nested `unitWords` object).
    const paramsEnd = source.indexOf(")", funcStart);
    const bodyStart = source.indexOf("{", paramsEnd);
    let depth = 0;
    let funcEnd = bodyStart;
    for (let i = bodyStart; i < source.length; i += 1) {
      if (source[i] === "{") depth += 1;
      else if (source[i] === "}") {
        depth -= 1;
        if (depth === 0) {
          funcEnd = i + 1;
          break;
        }
      }
    }
    expect(funcEnd).toBeGreaterThan(bodyStart);
    const before = source.slice(0, funcStart);
    const after = source.slice(funcEnd);
    expect(before.match(banned)).toBeNull();
    expect(after.match(banned)).toBeNull();
    expect(source.slice(funcStart, funcEnd).match(banned)).not.toBeNull();
  });
});
