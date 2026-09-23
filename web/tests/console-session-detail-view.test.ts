import * as React from "react";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StarIcon } from "lucide-react";

import {
  formatUsageLabel,
  formatUsageValue,
  SessionDetailView,
} from "@/components/console/sessions/session-detail-view";
import { BUILTIN_SESSION_TABS } from "@/components/console/sessions/detail/builtin-tabs";
import { resolveSessionTabs } from "@/components/console/sessions/detail/registry";
import type { SessionTabDef } from "@/components/console/sessions/detail/types";
import { panelAgent } from "@/components/console/sessions/session-panel-tab";
import { describeUsage } from "@/components/console/sessions/session-usage";
import type { AgentOut, SessionDetailOut, SessionEventOut } from "@/contracts/lkap-contracts";

/**
 * Session detail (docs/UI_UX_SPEC.md §4.10, §7.8 items 2–3): usage rendered
 * as label/value pairs (never JSON), the header, the tab registry with
 * `?tab=` deep links, the read-only panel snapshot, and error states.
 * (A `.ts` file by ownership, so elements are built with `createElement`.)
 */

const h = React.createElement;

describe("formatUsageLabel", () => {
  it("sentence-cases snake_case usage keys and keeps acronyms", () => {
    expect(formatUsageLabel("llm_completion_tokens")).toBe("LLM completion tokens");
    expect(formatUsageLabel("tts_characters_count")).toBe("TTS characters count");
    expect(formatUsageLabel("input_tokens")).toBe("Input tokens");
    expect(formatUsageLabel("eou_to_first_audio_ms")).toBe("EOU to first audio ms");
  });
});

describe("formatUsageValue", () => {
  it("formats integers with locale grouping", () => {
    expect(formatUsageValue(1234)).toBe("1,234");
  });

  it("formats non-integer numbers to two decimals", () => {
    expect(formatUsageValue(46.2345)).toBe("46.23");
  });

  it("passes through strings and booleans", () => {
    expect(formatUsageValue("ok")).toBe("ok");
    expect(formatUsageValue(true)).toBe("true");
  });

  it("renders null/undefined as an em dash", () => {
    expect(formatUsageValue(null)).toBe("—");
    expect(formatUsageValue(undefined)).toBe("—");
  });

  it("falls back to JSON for nested values (describeUsage never passes them)", () => {
    expect(formatUsageValue({ a: 1 })).toBe('{"a":1}');
  });
});

describe("describeUsage", () => {
  it("turns model_usage into provider/model rows with only the non-zero counters", () => {
    const { pairs, groups } = describeUsage({
      model_usage: [
        { type: "llm_usage", provider: "livekit", model: "google/gemini-3.5-flash", input_tokens: 15146, output_tokens: 256, input_cached_tokens: 0 },
        { type: "tts_usage", provider: "livekit", model: "inworld/inworld-tts-2", characters_count: 405, audio_duration: 22.159999 },
      ],
    });
    expect(pairs).toEqual([]);
    expect(groups).toHaveLength(1);
    expect(groups[0].label).toBe("Model usage");
    const [llm, tts] = groups[0].rows;
    expect([llm.kind, llm.title]).toEqual(["LLM", "livekit · google/gemini-3.5-flash"]);
    expect(llm.pairs.map((p) => [p.label, p.value])).toEqual([
      ["Input tokens", "15,146"],
      ["Output tokens", "256"],
    ]);
    expect(tts.pairs.map((p) => [p.label, p.value])).toEqual([
      ["Characters count", "405"],
      ["Audio duration", "22.2 s"],
    ]);
  });

  it("keeps flat counters as pairs and flattens nested dicts one level", () => {
    const { pairs, groups } = describeUsage({
      llm_prompt_tokens: 1200,
      stt_audio_duration: 0,
      extra: { cache_hits: 3, nested: { deep: 1 } },
    });
    expect(pairs.map((p) => [p.label, p.value])).toEqual([["LLM prompt tokens", "1,200"]]);
    expect(groups[0].rows[0].pairs.map((p) => [p.label, p.value])).toEqual([
      ["Cache hits", "3"],
      ["Nested · deep", "1"],
    ]);
    for (const pair of groups[0].rows[0].pairs) expect(pair.value).not.toContain("{");
  });

  it("returns nothing for missing usage", () => {
    expect(describeUsage(null)).toEqual({ pairs: [], groups: [] });
  });
});

// ---------------------------------------------------------------------------
// The detail view
// ---------------------------------------------------------------------------

const routerReplace = vi.fn();
let searchParams = new URLSearchParams();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: routerReplace, push: vi.fn() }),
  usePathname: () => "/console/sessions/s-1",
  useSearchParams: () => searchParams,
}));

function detail(overrides: Partial<SessionDetailOut> = {}): SessionDetailOut {
  return {
    id: "s-1",
    agent_id: "a-1",
    agent_name: "Claims desk",
    config_version: 3,
    room_name: "lkap-s1",
    status: "ended",
    pipeline_mode: "cascaded",
    created_at: "2026-09-18T20:17:04Z",
    started_at: "2026-09-18T20:17:06Z",
    ended_at: "2026-09-18T20:18:03Z",
    usage: { model_usage: [{ type: "llm_usage", provider: "livekit", model: "gemini", input_tokens: 10 }] },
    error: null,
    channel: "web",
    connection_id: "c-1",
    transcript: [
      { role: "user", text: "My basement flooded", ts: Date.parse("2026-09-18T20:17:08Z") / 1000 },
      { role: "assistant", text: "Sorry to hear that", ts: Date.parse("2026-09-18T20:17:10Z") / 1000 },
    ],
    final_ui_state: null,
    ...overrides,
  };
}

const EVENTS: SessionEventOut[] = [
  { id: 1, ts: "2026-09-18T20:17:07Z", type: "agent_state", payload: { state: "listening" } },
  { id: 2, ts: "2026-09-18T20:17:09Z", type: "tool_call_started", payload: { call_id: "c", tool: "lookup_policy", args_redacted: { policy: "H0-1" } } },
  { id: 3, ts: "2026-09-18T20:17:09.01Z", type: "tool_call_ended", payload: { call_id: "c", tool: "lookup_policy", status: "done", duration_ms: 10 } },
  { id: 4, ts: "2026-09-18T20:17:20Z", type: "error", payload: { message: "TTS timeout" } },
];

function agent(overrides: Partial<AgentOut> = {}): AgentOut {
  return {
    id: "a-1",
    name: "Claims desk",
    slug: "claims-desk",
    description: "Takes first notice of loss",
    pack_id: "generic",
    ui_panel_id: "composite",
    published: true,
    config_version: 3,
    created_at: "2026-09-18T00:00:00Z",
    updated_at: "2026-09-18T00:00:00Z",
    config: { pipeline: { mode: "cascaded" }, capabilities: { camera: true } } as AgentOut["config"],
    ...overrides,
  };
}

function stubApi(session: SessionDetailOut | { status: number }, { events = EVENTS, agentOut = agent() }: { events?: SessionEventOut[]; agentOut?: AgentOut | null } = {}) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const reply = (status: number, body: unknown) => ({ ok: status < 400, status, statusText: "x", json: async () => body }) as Response;
      if (url.includes("/events")) return reply(200, { items: events, total: events.length });
      if (url.includes("/api/console/sessions/")) {
        if ("status" in session && typeof session.status === "number") {
          return reply(session.status, { error: { code: "not_found", message: "unknown session 's-1'" } });
        }
        return reply(200, session);
      }
      if (url.includes("/api/console/agents/")) {
        return agentOut ? reply(200, agentOut) : reply(404, { error: { code: "not_found", message: "gone" } });
      }
      if (url.includes("/api/console/connections")) {
        return reply(200, { items: [{ id: "c-1", name: "Cloud EU", slug: "cloud-eu", url: "wss://x" }], total: 1 });
      }
      return reply(200, {});
    }),
  );
}

function renderView(props: { tabs?: SessionTabDef[] } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(h(QueryClientProvider, { client }, h(SessionDetailView, { sessionId: "s-1", ...props })));
}

beforeEach(() => {
  searchParams = new URLSearchParams();
  routerReplace.mockReset();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("SessionDetailView — header and stats", () => {
  it("links the agent, shows status, channel, mode, config, connection and room", async () => {
    stubApi(detail());
    renderView();

    const title = await screen.findByRole("link", { name: "Claims desk" });
    expect(title.getAttribute("href")).toBe("/console/agents/a-1");
    expect(screen.getByText("Ended")).toBeTruthy();
    expect(screen.getByText("Web")).toBeTruthy();
    expect(screen.getByText("Cascaded")).toBeTruthy();
    expect(screen.getByText("v3")).toBeTruthy();
    expect(screen.getByText("57s")).toBeTruthy();
    expect(screen.getByText("lkap-s1")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Copy room name" })).toBeTruthy();
    expect(await screen.findByText("Cloud EU")).toBeTruthy();
  });

  it("counts turns, tool calls and errors and renders usage as rows, not JSON", async () => {
    stubApi(detail());
    renderView();

    const summary = await screen.findByRole("region", { name: "Call summary" });
    await within(summary).findByText("Tool calls");
    const value = (term: string) => within(summary).getByText(term).nextElementSibling?.textContent;
    await vi.waitFor(() => expect(value("Tool calls")).toBe("1"));
    expect(value("Turns")).toBe("2");
    expect(value("Errors")).toBe("1");
    expect(within(summary).getByText("livekit · gemini")).toBeTruthy();
    expect(summary.textContent).not.toContain("{");
  });

  it("shows v2 fields only when the api has them", async () => {
    stubApi(
      detail({
        cost: { total_usd: 0.0123, lines: [] },
        qa: { score: 9, status: "done" },
        recording: { status: "ready" },
        latency: { eou_to_first_audio_ms_p50: 820 },
        channel: "sip_in",
      }),
    );
    renderView();
    expect(await screen.findByText("$0.0123")).toBeTruthy();
    expect(screen.getByText("QA 9/10")).toBeTruthy();
    expect(screen.getByText("Recording ready")).toBeTruthy();
    expect(screen.getByText("Phone (inbound)")).toBeTruthy();
    expect(screen.getByText("820 ms")).toBeTruthy();
  });

  it("puts a real failure in a danger alert but keeps swept rows muted", async () => {
    stubApi(detail({ status: "failed", error: "LLM rejected its configuration", started_at: null }));
    const first = renderView();
    expect(await screen.findByText("LLM rejected its configuration")).toBeTruthy();
    expect(screen.getByText("The call failed.")).toBeTruthy();
    first.unmount();

    stubApi(detail({ status: "failed", error: "never started", started_at: null, transcript: null }), { events: [] });
    renderView();
    expect(await screen.findByText("Never started")).toBeTruthy();
    expect(screen.queryByText("The call failed.")).toBeNull();
    expect(screen.getByText("Created")).toBeTruthy();
  });

  it("shows a not-found state for an unknown session", async () => {
    stubApi({ status: 404 });
    renderView();
    expect(await screen.findByText("This session doesn't exist")).toBeTruthy();
    expect(screen.getByRole("link", { name: "All sessions" }).getAttribute("href")).toBe("/console/sessions");
  });
});

describe("SessionDetailView — tabs", () => {
  it("opens Timeline by default with the built-in tabs plus V2-14's Cost/QA in order (Recording hidden — no recording on this fixture)", async () => {
    stubApi(detail());
    renderView();
    const tablist = await screen.findByRole("tablist", { name: "Session views" });
    expect(within(tablist).getAllByRole("tab").map((t) => t.textContent)).toEqual([
      "Timeline",
      "Transcript",
      "Cost",
      "QA",
      "Panel at end of call",
      "Raw events",
    ]);
    expect(within(tablist).getByRole("tab", { name: "Timeline" }).getAttribute("aria-selected")).toBe("true");
    expect(await screen.findByText("lookup_policy")).toBeTruthy();
  });

  it("writes the chosen tab to ?tab= and reads it back", async () => {
    stubApi(detail());
    const view = renderView();
    const tab = await screen.findByRole("tab", { name: "Raw events" });
    fireEvent.mouseDown(tab, { button: 0 });
    expect(routerReplace).toHaveBeenLastCalledWith("/console/sessions/s-1?tab=raw", { scroll: false });
    view.unmount();

    searchParams = new URLSearchParams("tab=raw");
    renderView();
    expect((await screen.findByRole("tab", { name: "Raw events" })).getAttribute("aria-selected")).toBe("true");
    expect(await screen.findByText("4 events")).toBeTruthy();
    for (const pre of document.querySelectorAll("pre")) expect(pre.closest("details")).not.toBeNull();
  });

  it("falls back to Timeline for an unknown tab and renders an injected registry tab", async () => {
    // A genuinely unregistered id — "qa" itself is real now that V2-14 has
    // landed its extension (see the "opens Timeline by default" test above),
    // so it can no longer stand in for "unknown" against the live registry.
    searchParams = new URLSearchParams("tab=totally-unregistered-id");
    stubApi(detail());
    const QaTab = ({ session }: { session: SessionDetailOut }) => h("p", null, `QA for ${session.id}`);
    // Exactly how V2-14 plugs in: an extension resolved against the built-ins.
    const withQa = resolveSessionTabs(BUILTIN_SESSION_TABS, [
      { id: "V2-14", tabs: [{ id: "qa", label: "QA", icon: StarIcon, order: 50, Component: QaTab }] },
    ]);

    const view = renderView();
    expect((await screen.findByRole("tab", { name: "Timeline" })).getAttribute("aria-selected")).toBe("true");
    view.unmount();

    searchParams = new URLSearchParams("tab=qa");
    renderView({ tabs: withQa });
    expect(await screen.findByText("QA for s-1")).toBeTruthy();
    const names = within(screen.getByRole("tablist")).getAllByRole("tab").map((t) => t.textContent);
    expect(names.indexOf("QA")).toBe(names.indexOf("Panel at end of call") - 1);
  });

  it("shows the transcript tab with speakers", async () => {
    searchParams = new URLSearchParams("tab=transcript");
    stubApi(detail());
    renderView();
    const transcript = await screen.findByRole("list", { name: "Transcript" });
    expect(within(transcript).getByText("You")).toBeTruthy();
    expect(within(transcript).getByText("Claims desk")).toBeTruthy();
    expect(screen.getByText("2 turns")).toBeTruthy();
  });
});

describe("Panel at end of call", () => {
  const finalState = {
    v: 1 as const,
    status: { key: "needs_docs", label: "Needs docs", tone: "warning" as const },
    notes: [{ id: "n1", text: "Basement flooded", kind: "note" as const, tone: "neutral" as const, ts: 1 }],
  };

  it("renders the final UI state read-only through the composite panel (V2-11)", async () => {
    searchParams = new URLSearchParams("tab=panel");
    stubApi(detail({ final_ui_state: finalState as SessionDetailOut["final_ui_state"] }));
    renderView();
    expect(await screen.findByText(/Read-only snapshot of the panel/)).toBeTruthy();
    await vi.waitFor(() =>
      expect(document.querySelector("[data-slot=session-panel-snapshot]")?.getAttribute("data-panel-id")).toBe("composite"),
    );
    expect(screen.queryByText(/isn't available yet/)).toBeNull();
    // A block panel saved without blocks shows the default four (notes among them).
    expect(screen.getByText("Basement flooded")).toBeTruthy();
    expect(document.querySelector("[data-testid=composite-panel]")).toBeTruthy();
  });

  it("uses the agent's registered panel when there is one", async () => {
    searchParams = new URLSearchParams("tab=panel");
    stubApi(detail({ final_ui_state: finalState as SessionDetailOut["final_ui_state"] }), {
      agentOut: agent({ ui_panel_id: "insurance_notebook" }),
    });
    renderView();
    await screen.findByText(/Read-only snapshot of the panel/);
    await vi.waitFor(() =>
      expect(document.querySelector("[data-slot=session-panel-snapshot]")?.getAttribute("data-panel-id")).toBe("insurance_notebook"),
    );
    expect(screen.queryByText(/isn't available yet/)).toBeNull();
  });

  it("falls back to the generic panel straight away when the agent was deleted", async () => {
    searchParams = new URLSearchParams("tab=panel");
    stubApi(detail({ final_ui_state: finalState as SessionDetailOut["final_ui_state"] }), { agentOut: null });
    renderView();
    expect(await screen.findByText(/The agent no longer exists/)).toBeTruthy();
    expect(document.querySelector("[data-slot=session-panel-snapshot]")?.getAttribute("data-panel-id")).toBe("generic");
  });

  it("shows an empty state when no final state was saved", async () => {
    searchParams = new URLSearchParams("tab=panel");
    stubApi(detail());
    renderView();
    expect(await screen.findByText("No panel state was saved for this call")).toBeTruthy();
  });

  it("builds the panel's agent from the session when the agent is gone", () => {
    const fromSession = panelAgent(detail(), undefined);
    expect(fromSession).toMatchObject({ id: "a-1", name: "Claims desk", ui_panel_id: "", pipeline_mode: "cascaded" });
    const fromAgent = panelAgent(detail(), agent());
    expect(fromAgent).toMatchObject({ slug: "claims-desk", ui_panel_id: "composite", capabilities: { camera: true } });
  });
});
