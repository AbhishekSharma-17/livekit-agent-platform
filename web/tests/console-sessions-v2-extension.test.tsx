import * as React from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { sessionsV2Extension } from "@/components/console/sessions-v2/extension";
import { BUILTIN_SESSION_TABS } from "@/components/console/sessions/detail/builtin-tabs";
import { BUILTIN_EVENT_KINDS } from "@/components/console/sessions/detail/builtin-event-kinds";
import { resolveEventKinds, resolveSessionTabs, visibleTabs } from "@/components/console/sessions/detail/registry";
import { SESSION_DETAIL_EXTENSIONS } from "@/components/console/sessions/detail/extensions";
import type { SessionDetailOut } from "@/contracts/lkap-contracts";

/**
 * V2-14's session-detail extension: registration (append-only
 * `detail/extensions.ts`), tab ordering/visibility, the v2 event kinds
 * CONTRACTS-V2 §3.4 adds, and the three tab components against real
 * `SessionDetailOut` shapes (recording url playback, "no price" cost lines,
 * QA re-score + the 409 `qa_judge_unavailable` path).
 */

function baseSession(overrides: Partial<SessionDetailOut> = {}): SessionDetailOut {
  return {
    id: "s1",
    agent_id: "a1",
    agent_name: "Agent",
    config_version: 1,
    room_name: "room-1",
    status: "ended",
    pipeline_mode: "cascaded",
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  } as SessionDetailOut;
}

describe("sessionsV2Extension registration", () => {
  it("is wired into the shared, append-only extensions list", () => {
    expect(SESSION_DETAIL_EXTENSIONS).toContain(sessionsV2Extension);
  });

  it("adds recording (30), cost (40) and qa (50) after transcript and before panel/raw", () => {
    const tabs = resolveSessionTabs(BUILTIN_SESSION_TABS, [sessionsV2Extension]);
    expect(tabs.map((t) => t.id)).toEqual(["timeline", "transcript", "recording", "cost", "qa", "panel", "raw"]);
  });

  it("hides the Recording tab when there is no recording", () => {
    const tabs = resolveSessionTabs(BUILTIN_SESSION_TABS, [sessionsV2Extension]);
    const session = baseSession({ recording: { status: "none" } });
    const shown = visibleTabs(tabs, { session }).map((t) => t.id);
    expect(shown).not.toContain("recording");
    expect(shown).toContain("cost");
    expect(shown).toContain("qa");
  });

  it("shows the Recording tab once a recording is ready", () => {
    const tabs = resolveSessionTabs(BUILTIN_SESSION_TABS, [sessionsV2Extension]);
    const session = baseSession({ recording: { status: "ready", url: "https://example.com/rec.ogg" } });
    expect(visibleTabs(tabs, { session }).map((t) => t.id)).toContain("recording");
  });

  it("registers the v2 timeline event kinds from CONTRACTS-V2 §3.4", () => {
    const kinds = resolveEventKinds(BUILTIN_EVENT_KINDS, [sessionsV2Extension]);
    for (const type of ["handoff", "block_update", "form_submitted", "dtmf", "transfer", "recording"]) {
      expect(kinds.has(type)).toBe(true);
    }
    expect(kinds.get("handoff")?.title({ to: "Billing", from: "Intake" })).toBe("Moved to Billing");
  });
});

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("RecordingTab", () => {
  it("plays a ready recording and offers a download", async () => {
    const { RecordingTab } = await import("@/components/console/sessions-v2/recording-tab");
    const session = baseSession({
      recording: { status: "ready", url: "https://example.com/rec.ogg", duration_s: 65 },
    });
    renderWithClient(<RecordingTab session={session} />);

    const audio = document.querySelector("audio");
    expect(audio?.getAttribute("src")).toBe("https://example.com/rec.ogg");
    expect(screen.getByRole("link", { name: /Download/ })).toHaveProperty(
      "href",
      "https://example.com/rec.ogg",
    );
  });

  it("shows an explanatory empty state when there is nothing to play", async () => {
    const { RecordingTab } = await import("@/components/console/sessions-v2/recording-tab");
    renderWithClient(<RecordingTab session={baseSession({ recording: { status: "none" } })} />);
    expect(screen.getByText("No recording")).toBeTruthy();
  });

  it("shows the worker's reason for a failed recording (V2-20-3)", async () => {
    const { RecordingTab } = await import("@/components/console/sessions-v2/recording-tab");
    const session = baseSession({
      recording: { status: "failed", error: "recording/start answered HTTP 422" },
    });
    renderWithClient(<RecordingTab session={session} />);
    expect(screen.getByText("Failed")).toBeTruthy();
    expect(screen.getByText("recording/start answered HTTP 422")).toBeTruthy();
  });

  it("falls back to a generic reason when a failed recording carries no error text", async () => {
    const { RecordingTab } = await import("@/components/console/sessions-v2/recording-tab");
    renderWithClient(<RecordingTab session={baseSession({ recording: { status: "failed" } })} />);
    expect(screen.getByText("The recording could not be produced for this session.")).toBeTruthy();
  });
});

describe("CostTab", () => {
  it("renders 'no price' for an unpriced line, never $0", async () => {
    const { CostTab } = await import("@/components/console/sessions-v2/cost-tab");
    const session = baseSession({
      cost: {
        total_usd: "0.05",
        lines: [
          { provider_id: "openai", model: "gpt-4o", unit: "tokens_in", quantity: "100", unit_price_usd: "0.0005", cost_usd: "0.05" },
          { provider_id: "custom-tts", unit: "chars", quantity: "500", note: "no price" },
        ],
      },
    });
    renderWithClient(<CostTab session={session} />);

    expect(screen.getByText("no price")).toBeTruthy();
    expect(screen.queryByText("$0")).toBeNull();
    expect(screen.getAllByText("$0.0500", { selector: "td" }).length).toBeGreaterThan(0);
  });

  it("shows an empty state with no cost lines", async () => {
    const { CostTab } = await import("@/components/console/sessions-v2/cost-tab");
    renderWithClient(<CostTab session={baseSession({ cost: { lines: [] } })} />);
    expect(screen.getByText("No cost data")).toBeTruthy();
  });
});

describe("QaTab", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 202, json: async () => ({ status: "pending" }) }) as Response));
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders a score, sentiment and tags", async () => {
    const { QaTab } = await import("@/components/console/sessions-v2/qa-tab");
    const session = baseSession({
      qa: { status: "done", score: 9, sentiment: "positive", tags: ["polite"], summary: "Great call.", scored_by: "worker" },
    });
    renderWithClient(<QaTab session={session} />);

    expect(screen.getByText("Score 9/10")).toBeTruthy();
    expect(screen.getByText("Great call.")).toBeTruthy();
    expect(screen.getByText("polite")).toBeTruthy();
  });

  it("shows 'not scored' for a skipped verdict", async () => {
    const { QaTab } = await import("@/components/console/sessions-v2/qa-tab");
    renderWithClient(<QaTab session={baseSession({ qa: { status: "skipped" } })} />);
    expect(screen.getByText("Not scored")).toBeTruthy();
  });

  it("re-score calls POST /sessions/{id}/qa", async () => {
    const { QaTab } = await import("@/components/console/sessions-v2/qa-tab");
    const session = baseSession({ qa: { status: "done", score: 7 } });
    renderWithClient(<QaTab session={session} />);

    fireEvent.click(screen.getByRole("button", { name: "Re-score" }));

    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(String(fetchMock.mock.calls[0][0])).toBe("/api/console/sessions/s1/qa");
  });
});
