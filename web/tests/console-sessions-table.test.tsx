import * as React from "react";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { SessionsTable } from "@/components/console/sessions/sessions-table";
import {
  filterSessions,
  EMPTY_FILTERS,
  pageCount,
  paginate,
  rangeStart,
  sessionDurationMs,
  sessionStatusTone,
  sweptReason,
  usageTurns,
} from "@/components/console/sessions/session-model";
import type { AgentPage, ConnectionPage, SessionOut, SessionPage } from "@/contracts/lkap-contracts";

/**
 * Sessions list (docs/UI_UX_SPEC.md §4.10, §7.8 item 1; v2 amendments §3):
 * filters (agent, status, range, channel, connection), the duration column,
 * 25-row client-side pagination, and the muted chip for sweep-failed rows
 * (DECISIONS-W2 D-W2-2b).
 */

const routerReplace = vi.fn();
let searchParams = new URLSearchParams();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: routerReplace, push: vi.fn() }),
  usePathname: () => "/console/sessions",
  useSearchParams: () => searchParams,
}));

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function session(overrides: Partial<SessionOut>): SessionOut {
  return {
    id: "s-1",
    agent_id: "a-1",
    agent_name: "Claims desk",
    config_version: 1,
    room_name: "room-1",
    status: "created",
    pipeline_mode: "cascaded",
    created_at: "2026-09-19T00:00:00Z",
    started_at: null,
    ended_at: null,
    usage: null,
    error: null,
    channel: "web",
    connection_id: null,
    ...overrides,
  };
}

const requested: string[] = [];

function stubApi(items: SessionOut[], { total, agents = [], connections = [] }: { total?: number; agents?: AgentPage["items"]; connections?: ConnectionPage["items"] } = {}) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      requested.push(url);
      let body: unknown = { items: [], total: 0 };
      if (url.includes("/api/console/sessions")) body = { items, total: total ?? items.length } satisfies SessionPage;
      else if (url.includes("/api/console/agents")) body = { items: agents, total: agents.length };
      else if (url.includes("/api/console/connections")) body = { items: connections, total: connections.length };
      return { ok: true, status: 200, json: async () => body } as Response;
    }),
  );
}

beforeEach(() => {
  searchParams = new URLSearchParams();
  routerReplace.mockReset();
  requested.length = 0;
});

afterEach(() => {
  vi.unstubAllGlobals();
});

/** The desktop table (the card list below 768 px renders the same rows again). */
function table() {
  return within(screen.getByRole("table", { name: "Sessions" }));
}

async function loadedTable() {
  return within(await screen.findByRole("table", { name: "Sessions" }));
}

describe("SessionsTable — DECISIONS-W2 D-W2-2(b) swept-row chip", () => {
  it("shows a muted 'Never started' chip for a sweep-failed created row", async () => {
    stubApi([session({ id: "s-created", status: "failed", error: "never started", ended_at: "2026-09-19T00:10:00Z" })]);
    renderWithClient(<SessionsTable />);

    const chip = await (await loadedTable()).findByText("Never started");
    expect(chip.getAttribute("data-tone")).toBe("neutral");
    // The status chip itself is muted too, not the destructive "Failed".
    expect(table().getByText("Failed").getAttribute("data-tone")).toBe("neutral");
  });

  it("shows a muted 'Summary never received' chip for a sweep-failed active row", async () => {
    stubApi([
      session({
        id: "s-active",
        status: "failed",
        error: "summary never received",
        started_at: "2026-09-19T00:00:00Z",
        ended_at: "2026-09-19T06:00:00Z",
      }),
    ]);
    renderWithClient(<SessionsTable />);
    expect(await (await loadedTable()).findByText("Summary never received")).toBeTruthy();
  });

  it("keeps an ordinary failure in the danger tone without leaking its error text", async () => {
    stubApi([session({ id: "s-crash", status: "failed", error: "LLM connection reset" })]);
    renderWithClient(<SessionsTable />);

    const chip = await (await loadedTable()).findByText("Failed");
    expect(chip.closest("[data-slot=status-chip]")?.getAttribute("data-tone")).toBe("danger");
    expect(screen.queryByText("LLM connection reset")).toBeNull();
    expect(screen.queryByText("Never started")).toBeNull();
  });

  it("does not show a swept chip for a healthy ended session", async () => {
    stubApi([session({ id: "s-ended", status: "ended", started_at: "2026-09-19T00:00:00Z", ended_at: "2026-09-19T00:05:00Z" })]);
    renderWithClient(<SessionsTable />);
    expect(await (await loadedTable()).findByText("Ended")).toBeTruthy();
    expect(screen.queryByText("Never started")).toBeNull();
  });
});

describe("SessionsTable — V2-20-3 failed-recording chip", () => {
  it("shows a danger 'Recording failed' chip on an otherwise-ended session", async () => {
    stubApi([
      session({
        id: "s-rec-failed",
        status: "ended",
        started_at: "2026-09-19T00:00:00Z",
        ended_at: "2026-09-19T00:05:00Z",
        recording_status: "failed",
      }),
    ]);
    renderWithClient(<SessionsTable />);

    const chip = await (await loadedTable()).findByText("Recording failed");
    expect(chip.closest("[data-slot=status-chip]")?.getAttribute("data-tone")).toBe("danger");
    // The session's own status chip is unaffected — the row ended fine.
    expect(table().getByText("Ended")).toBeTruthy();
  });

  it("shows no recording chip when the recording never failed", async () => {
    stubApi([session({ id: "s-ok", status: "ended", recording_status: "ready" })]);
    renderWithClient(<SessionsTable />);
    await loadedTable();
    expect(screen.queryByText("Recording failed")).toBeNull();
  });
});

describe("SessionsTable — columns, links, pagination", () => {
  it("asks the api for its maximum page and renders duration, channel, mode and a row link", async () => {
    stubApi([
      session({
        id: "abc",
        status: "ended",
        started_at: "2026-09-19T00:00:00Z",
        ended_at: "2026-09-19T00:01:57Z",
        channel: "sip_in",
        pipeline_mode: "realtime",
        usage: { turns: 12 },
      }),
      session({ id: "never", status: "failed", error: "never started", room_name: "room-never" }),
    ]);
    renderWithClient(<SessionsTable />);

    expect(await (await loadedTable()).findByText("1m 57s")).toBeTruthy();
    // The api's page-size cap is 200 (V2-12 tightened it from 500, ask #67);
    // `useSessionList` pages through 200-row requests up to the 500-row
    // window instead, so the *first* request is `limit=200`, not `limit=500`.
    expect(requested.some((url) => url.includes("/api/console/sessions?limit=200&offset=0"))).toBe(true);
    expect(table().getByText("Phone (inbound)")).toBeTruthy();
    expect(table().getByText("Realtime")).toBeTruthy();
    expect(table().getByText("12")).toBeTruthy();
    // Never started → "—" duration.
    const neverRow = table().getByText("room-never").closest("tr") as HTMLElement;
    expect(within(neverRow).getAllByText("—").length).toBeGreaterThanOrEqual(2);
    const link = table().getAllByRole("link")[0];
    expect(link.getAttribute("href")).toBe("/console/sessions/abc");
  });

  it("paginates 25 per page and writes the page to the URL", async () => {
    const many = Array.from({ length: 30 }, (_, i) =>
      session({ id: `s-${i}`, room_name: `room-${i}`, created_at: new Date(Date.UTC(2026, 8, 19, 0, i)).toISOString() }),
    );
    stubApi(many);
    renderWithClient(<SessionsTable />);

    expect(await (await loadedTable()).findByText("room-0")).toBeTruthy();
    expect(table().queryByText("room-25")).toBeNull();
    expect(screen.getByText("1–25 of 30")).toBeTruthy();
    expect(screen.getByText("Page 1 of 2")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Previous" }) as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(table().getByText("room-29")).toBeTruthy();
    expect(table().queryByText("room-0")).toBeNull();
    expect(screen.getByText("26–30 of 30")).toBeTruthy();
    expect(routerReplace).toHaveBeenLastCalledWith("/console/sessions?page=2", { scroll: false });
  });

  it("says when the api holds more sessions than one fetch returns", async () => {
    stubApi([session({ id: "x" })], { total: 812 });
    renderWithClient(<SessionsTable />);
    expect(await screen.findByText(/newest 500 of 812 loaded/)).toBeTruthy();
  });

  it("shows the first-run empty state when there are no sessions", async () => {
    stubApi([]);
    renderWithClient(<SessionsTable />);
    expect(await screen.findByText("No sessions yet")).toBeTruthy();
  });
});

describe("SessionsTable — filters", () => {
  const rows = [
    session({ id: "1", agent_id: "a-1", agent_name: "Claims desk", room_name: "room-web", status: "ended", channel: "web", connection_id: "c-1" }),
    session({ id: "2", agent_id: "a-2", agent_name: "Front desk", room_name: "room-sip", status: "failed", error: "boom", channel: "sip_in", connection_id: "c-2" }),
  ];

  it("filters by status chip, resets to page 1 and clears from the empty state", async () => {
    stubApi(rows);
    renderWithClient(<SessionsTable />);
    await (await loadedTable()).findByText("room-web");

    const group = within(screen.getByRole("group", { name: "Filter by status" }));
    fireEvent.click(group.getByRole("button", { name: "Failed" }));
    expect(group.getByRole("button", { name: "Failed" }).getAttribute("aria-pressed")).toBe("true");
    expect(table().queryByText("room-web")).toBeNull();
    expect(table().getByText("room-sip")).toBeTruthy();
    expect(routerReplace).toHaveBeenLastCalledWith("/console/sessions?status=failed", { scroll: false });

    fireEvent.click(group.getByRole("button", { name: "Active" }));
    expect(screen.getByText("No sessions match these filters")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(table().getByText("room-web")).toBeTruthy();
    expect(routerReplace).toHaveBeenLastCalledWith("/console/sessions", { scroll: false });
  });

  it("restores channel, connection and agent filters from the URL", async () => {
    searchParams = new URLSearchParams("channel=sip_in");
    stubApi(rows);
    const first = renderWithClient(<SessionsTable />);
    expect(await (await loadedTable()).findByText("room-sip")).toBeTruthy();
    expect(table().queryByText("room-web")).toBeNull();
    first.unmount();

    searchParams = new URLSearchParams("connection=c-1");
    const second = renderWithClient(<SessionsTable />);
    expect(await (await loadedTable()).findByText("room-web")).toBeTruthy();
    expect(table().queryByText("room-sip")).toBeNull();
    second.unmount();

    searchParams = new URLSearchParams("agent=a-2&status=failed");
    renderWithClient(<SessionsTable />);
    expect(await (await loadedTable()).findByText("room-sip")).toBeTruthy();
    expect(screen.getByText(/filtered from 2/)).toBeTruthy();
  });

  it("labels the connection and agent filters from the api lists", async () => {
    stubApi(rows, {
      connections: [{ id: "c-1", name: "Cloud EU", slug: "cloud-eu", url: "wss://x" }],
    });
    renderWithClient(<SessionsTable />);
    await (await loadedTable()).findByText("room-web");
    expect(screen.getByRole("combobox", { name: "Filter by connection" })).toBeTruthy();
    expect(screen.getByRole("combobox", { name: "Filter by channel" })).toBeTruthy();
    expect(screen.getByRole("combobox", { name: "Filter by agent" })).toBeTruthy();
    expect(screen.getByRole("combobox", { name: "Filter by date" })).toBeTruthy();
  });
});

describe("session-model helpers", () => {
  it("measures duration only for sessions that started", () => {
    expect(sessionDurationMs({ status: "ended", started_at: "2026-09-19T00:00:00Z", ended_at: "2026-09-19T00:00:42Z" })).toBe(42_000);
    expect(sessionDurationMs({ status: "failed", started_at: null, ended_at: "2026-09-19T00:10:00Z" })).toBeNull();
    const now = Date.parse("2026-09-19T00:03:00Z");
    expect(sessionDurationMs({ status: "active", started_at: "2026-09-19T00:00:00Z", ended_at: null }, now)).toBe(180_000);
    expect(sessionDurationMs({ status: "ended", started_at: "2026-09-19T00:01:00Z", ended_at: "2026-09-19T00:00:00Z" })).toBeNull();
  });

  it.each([
    ["never started", "neutral"],
    ["summary never received", "neutral"],
    ["LLM crashed", "danger"],
  ])("tones a failed row with error %j as %s", (error, tone) => {
    expect(sessionStatusTone({ status: "failed", error })).toBe(tone);
  });

  it("only treats failed rows as swept", () => {
    expect(sweptReason({ status: "ended", error: "never started" })).toBeNull();
    expect(sessionStatusTone({ status: "active", error: null })).toBe("live");
  });

  it("filters by date range from the viewer's midnight or a rolling window", () => {
    const now = Date.parse("2026-09-23T12:00:00Z");
    const rows = [
      session({ id: "old", created_at: "2026-08-01T00:00:00Z" }),
      session({ id: "week", created_at: "2026-09-20T00:00:00Z" }),
    ];
    expect(filterSessions(rows, { ...EMPTY_FILTERS, range: "7d" }, now).map((s) => s.id)).toEqual(["week"]);
    expect(filterSessions(rows, { ...EMPTY_FILTERS, range: "30d" }, now).map((s) => s.id)).toEqual(["week"]);
    expect(filterSessions(rows, EMPTY_FILTERS, now)).toHaveLength(2);
    const midnight = new Date(now);
    midnight.setHours(0, 0, 0, 0);
    expect(rangeStart("today", now)).toBe(midnight.getTime());
    expect(rangeStart("", now)).toBeNull();
  });

  it("treats a missing channel as web", () => {
    const rows = [session({ id: "legacy", channel: undefined })];
    expect(filterSessions(rows, { ...EMPTY_FILTERS, channel: "web" })).toHaveLength(1);
    expect(filterSessions(rows, { ...EMPTY_FILTERS, channel: "api" })).toHaveLength(0);
  });

  it("pages 25 at a time and clamps out-of-range pages", () => {
    const rows = Array.from({ length: 51 }, (_, i) => i);
    expect(pageCount(0)).toBe(1);
    expect(pageCount(51)).toBe(3);
    expect(paginate(rows, 3)).toEqual([50]);
    expect(paginate(rows, 99)).toEqual([50]);
    expect(paginate(rows, 0)).toHaveLength(25);
  });

  it("reads a turn counter from usage only when present", () => {
    expect(usageTurns(null)).toBeNull();
    expect(usageTurns({ model_usage: [] })).toBeNull();
    expect(usageTurns({ turn_count: 4 })).toBe(4);
  });
});
