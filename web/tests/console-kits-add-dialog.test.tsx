import * as React from "react";

import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { AddKitDialog } from "@/components/console/tools/kits/add-kit-dialog";
import type { AgentOut, ToolKit, ToolKitInstantiated } from "@/contracts/lkap-contracts";

/**
 * The Add-kit dialog (V6-19, D-V6-26; ask #142): the card's own acceptance —
 * "the Add-kit dialog lists every tool, block and instruction change before
 * Add" — plus the two-call shape (`dry_run: true` for Preview, then a plain
 * `POST` for Add) and the plain-words rule (no "dry run"/"instantiate" in
 * the UI copy).
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
});
afterEach(() => {
  vi.unstubAllGlobals();
});

function makeAgent(): AgentOut {
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
  } as AgentOut;
}

function makeKit(): ToolKit {
  return {
    id: "record_lookup",
    name: "Look a record up",
    summary: "Finds a caller's record by phone or policy number.",
    default_prefix: "record",
    default_variant: "http",
    variants: [
      {
        id: "http",
        label: "Call an API",
        summary: "Calls the business's own lookup API.",
        source: "http",
        tools: [
          {
            key: "lookup",
            label: "Look up",
            risk: "read",
            definition: {
              kind: "http",
              name: "record_lookup",
              description: "Look a record up.",
              parameters: { type: "object", properties: {}, required: [] },
              method: "POST",
              url: "https://{{ kit.base_url }}/lookup",
              allowed_hosts: ["{{ kit.base_url }}"],
            },
          },
        ],
        requires: { secret_names: [], apps: [], min_key_columns: 1, sms: false },
        blocks: [],
        variables: [],
        rules: [],
        configures: [],
      },
    ],
    defaults: [
      { name: "base_url", label: "API address", kind: "url", required: true, example: "api.example.com", variants: [] },
    ],
    blocks: [{ id: "record_results", type: "details", order: 0 }],
    instructions_snippet: "Use record_lookup to find the caller's record.",
    variables: [],
    rules: [],
  } as unknown as ToolKit;
}

function previewResult(): ToolKitInstantiated {
  return {
    kit_id: "record_lookup",
    variant: "http",
    prefix: "record",
    dry_run: true,
    changes: [
      { kind: "tool", id: "record_lookup", label: "record_lookup", status: "added" },
      { kind: "block", id: "record_results", label: "Record", status: "added" },
      { kind: "instructions", id: "record_lookup:record", label: "Instructions", status: "added" },
    ],
    tools: [],
    tool_ids: [],
    instructions_snippet: "<!-- kit:record_lookup:record -->\nUse record_lookup to find the caller's record.\n<!-- /kit:record_lookup:record -->",
    notes: [],
    validation: { ok: true },
  };
}

interface FetchCall {
  method: string;
  url: string;
  body: unknown;
}

function stubFetch(instantiated: ToolKitInstantiated) {
  const calls: FetchCall[] = [];
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

    if (url.includes("auth/me")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ user: { id: "u1", email: "a@b.test" }, workspaces: [{ id: "w1", name: "W", slug: "w", role: "admin" }] }),
      } as Response;
    }
    if (url.includes("/tool-kits/") && url.includes("/instantiate") && method === "POST") {
      const dryRun = (body as { dry_run?: boolean } | undefined)?.dry_run === true;
      return { ok: true, status: 200, json: async () => ({ ...instantiated, dry_run: dryRun }) } as Response;
    }
    if (url.includes("providers")) {
      return { ok: true, status: 200, json: async () => ({ providers: [] }) } as Response;
    }
    if (url.includes("datasets")) {
      return { ok: true, status: 200, json: async () => ({ items: [], total: 0 }) } as Response;
    }
    if (url.includes("connections")) {
      return { ok: true, status: 200, json: async () => ({ items: [] }) } as Response;
    }
    return { ok: true, status: 200, json: async () => ({ items: [], total: 0 }) } as Response;
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

function renderDialog(kit: ToolKit, agent: AgentOut) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <AddKitDialog kit={kit} agent={agent} trigger={<button type="button">Add to this agent</button>} />
    </QueryClientProvider>,
  );
}

describe("AddKitDialog", () => {
  it("previews before it can add, listing every tool, block and instruction change", async () => {
    const calls = stubFetch(previewResult());
    renderDialog(makeKit(), makeAgent());

    fireEvent.click(screen.getByRole("button", { name: "Add to this agent" }));
    const dialog = await screen.findByRole("dialog", { name: /Add “Look a record up”/ });

    // The required setting must be filled before Preview is enabled.
    const previewButton = () => screen.getByRole("button", { name: "Preview" }) as HTMLButtonElement;
    expect(previewButton().disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("API address"), { target: { value: "api.example.com" } });
    await waitFor(() => expect(previewButton().disabled).toBe(false));

    // Add is never available before a preview.
    expect(screen.queryByRole("button", { name: "Add" })).toBeNull();

    fireEvent.click(previewButton());

    // Every one of the three change kinds the card names shows up, grouped and labelled.
    await waitFor(() => expect(dialog.textContent).toContain("record_lookup"));
    expect(dialog.textContent).toContain("Tools");
    expect(dialog.textContent).toContain("Panel blocks");
    expect(dialog.textContent).toContain("Instructions");
    expect(dialog.textContent).toContain("Record");
    expect(dialog.textContent).toContain("Will add");

    // Exactly one call so far, and it carried `dry_run: true` — never the internal word in the UI.
    const kitCalls = calls.filter((c) => c.url.includes("/instantiate"));
    expect(kitCalls).toHaveLength(1);
    expect(kitCalls[0].body).toMatchObject({ dry_run: true });
    expect(dialog.textContent?.toLowerCase()).not.toMatch(/dry run|instantiate/);

    const addButton = (await screen.findByRole("button", { name: "Add" })) as HTMLButtonElement;
    expect(addButton.disabled).toBe(false);
    fireEvent.click(addButton);

    await waitFor(() => expect(calls.filter((c) => c.url.includes("/instantiate"))).toHaveLength(2));
    const secondCall = calls.filter((c) => c.url.includes("/instantiate"))[1];
    expect(secondCall.body).toMatchObject({ dry_run: false });
  });

  it("clears the preview (and hides Add again) when the form changes after previewing", async () => {
    stubFetch(previewResult());
    renderDialog(makeKit(), makeAgent());
    fireEvent.click(screen.getByRole("button", { name: "Add to this agent" }));
    await screen.findByRole("dialog");
    fireEvent.change(screen.getByLabelText("API address"), { target: { value: "api.example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Preview" }));
    await screen.findByRole("button", { name: "Add" });

    fireEvent.change(screen.getByLabelText("API address"), { target: { value: "api2.example.com" } });
    expect(screen.queryByRole("button", { name: "Add" })).toBeNull();
    expect(screen.getByRole("button", { name: "Preview" })).toBeTruthy();
  });
});
