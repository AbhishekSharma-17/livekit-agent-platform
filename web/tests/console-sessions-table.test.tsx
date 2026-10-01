import * as React from "react";

import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import {
  exportCsvHref,
  readStoredFilters,
  SESSION_FILTERS_STORAGE_KEY,
  SessionsTable,
} from "@/components/console/sessions/sessions-table";
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
import type { AgentOut, AgentPage, ConnectionPage, SessionOut, SessionPage } from "@/contracts/lkap-contracts";

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

// The "Columns" picker (V5-34) is a `SearchableSelect` (cmdk + a Radix
// Popover), which needs the same jsdom shims `tests/searchable-select.test.tsx`
// uses for the same combination: `:popover-open`/`:modal`, `scrollIntoView`
// and `ResizeObserver`.
const nativeMatches = Element.prototype.matches;
const nativeScrollIntoView = Element.prototype.scrollIntoView;
beforeAll(() => {
  Element.prototype.matches = function matches(this: Element, selector: string) {
    if (selector === ":popover-open" || selector === ":modal") return false;
    return nativeMatches.call(this, selector);
  };
  Element.prototype.scrollIntoView = function scrollIntoView() {};
});
afterAll(() => {
  Element.prototype.matches = nativeMatches;
  Element.prototype.scrollIntoView = nativeScrollIntoView;
});

beforeEach(() => {
  searchParams = new URLSearchParams();
  routerReplace.mockReset();
  requested.length = 0;
  // Filters and the search query are remembered in localStorage; each test gets
  // a fresh in-memory one (Node's own experimental global shadows jsdom's).
  const data = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    get length() {
      return data.size;
    },
    clear: () => data.clear(),
    getItem: (key: string) => data.get(key) ?? null,
    key: (index: number) => Array.from(data.keys())[index] ?? null,
    removeItem: (key: string) => {
      data.delete(key);
    },
    setItem: (key: string, value: string) => {
      data.set(key, String(value));
    },
  } satisfies Storage);
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
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

describe("SessionsTable — V5-38 Live indicator", () => {
  it("shows a 'Listen in' link to the Live tab only on an active session", async () => {
    stubApi([
      session({ id: "s-active", status: "active", started_at: "2026-09-19T00:00:00Z" }),
      session({ id: "s-ended", status: "ended", started_at: "2026-09-19T00:00:00Z", ended_at: "2026-09-19T00:01:00Z" }),
    ]);
    renderWithClient(<SessionsTable />);
    const t = await loadedTable();

    const listenLink = await t.findByRole("link", { name: /listen in/i });
    expect(listenLink.getAttribute("href")).toBe("/console/sessions/s-active?tab=live");

    // Only one row is active — the ended row gets no listen-in link.
    expect(t.getAllByRole("link", { name: /listen in/i })).toHaveLength(1);
  });

  it("shows no 'Listen in' link when nothing is active", async () => {
    stubApi([session({ id: "s-created", status: "created" })]);
    renderWithClient(<SessionsTable />);
    await loadedTable();
    expect(screen.queryByRole("link", { name: /listen in/i })).toBeNull();
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
    // Never started → "Not set" duration.
    const neverRow = table().getByText("room-never").closest("tr") as HTMLElement;
    expect(within(neverRow).getAllByText("Not set").length).toBeGreaterThanOrEqual(2);
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
    expect(screen.getByText("1 to 25 of 30")).toBeTruthy();
    expect(screen.getByText("Page 1 of 2")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Previous" }) as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(table().getByText("room-29")).toBeTruthy();
    expect(table().queryByText("room-0")).toBeNull();
    expect(screen.getByText("26 to 30 of 30")).toBeTruthy();
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

describe("SessionsTable — Cost column (docs/v4/COSTS.md §5 item 7)", () => {
  it("shows the actual cost with the estimate muted beside it", async () => {
    stubApi([session({ id: "priced", cost_usd: "0.12", estimated_usd: "0.10" })]);
    renderWithClient(<SessionsTable />);
    const row = (await (await loadedTable()).findByText("room-1")).closest("tr") as HTMLElement;
    expect(within(row).getByText("$0.1200")).toBeTruthy();
    expect(within(row).getByText("(≈ $0.1000)")).toBeTruthy();
  });

  it("falls back to the estimate alone before a session has an actual cost", async () => {
    stubApi([session({ id: "est-only", estimated_usd: "0.04" })]);
    renderWithClient(<SessionsTable />);
    const row = (await (await loadedTable()).findByText("room-1")).closest("tr") as HTMLElement;
    expect(within(row).getByText(/≈ \$0\.0400 · estimate/)).toBeTruthy();
  });

  it("never shows $0 for a session with neither figure", async () => {
    stubApi([session({ id: "bare" })]);
    renderWithClient(<SessionsTable />);
    await loadedTable();
    expect(screen.queryByText("$0")).toBeNull();
    expect(screen.queryByText(/\$0\.00/)).toBeNull();
  });
});

describe("SessionsTable — search", () => {
  const rows = Array.from({ length: 6 }, (_, index) =>
    session({ id: String(index), agent_id: `a-${index}`, agent_name: index === 3 ? "Zoë's front desk" : `Claims desk ${index}`, room_name: `room-${index}`, status: "ended" }),
  );

  it("searches from 6 sessions, accent-insensitively, and says when nothing matches", async () => {
    stubApi(rows);
    renderWithClient(<SessionsTable />);
    await (await loadedTable()).findByText("room-0");
    const search = screen.getByRole("searchbox", { name: "Search sessions" });

    fireEvent.change(search, { target: { value: "zoe front" } });
    expect(table().getByText("room-3")).toBeTruthy();
    expect(table().queryByText("room-0")).toBeNull();
    expect(document.querySelector("[data-slot=search-highlight]")).not.toBeNull();

    fireEvent.change(search, { target: { value: "nothing like this" } });
    expect(screen.getByText("No sessions match “nothing like this”")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(table().getByText("room-0")).toBeTruthy();
  });

  it("offers no search under 6 sessions", async () => {
    stubApi(rows.slice(0, 5));
    renderWithClient(<SessionsTable />);
    await (await loadedTable()).findByText("room-0");
    expect(screen.queryByRole("searchbox", { name: "Search sessions" })).toBeNull();
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

    const group = within(screen.getByRole("radiogroup", { name: "Filter by status" }));
    fireEvent.click(group.getByRole("radio", { name: /^Failed/ }));
    expect(group.getByRole("radio", { name: /^Failed/ }).getAttribute("aria-checked")).toBe("true");
    expect(table().queryByText("room-web")).toBeNull();
    expect(table().getByText("room-sip")).toBeTruthy();
    expect(routerReplace).toHaveBeenLastCalledWith("/console/sessions?status=failed", { scroll: false });

    fireEvent.click(group.getByRole("radio", { name: /^Active/ }));
    expect(screen.getByText("No sessions match these filters")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(table().getByText("room-web")).toBeTruthy();
    expect(routerReplace).toHaveBeenLastCalledWith("/console/sessions", { scroll: false });
  });

  it("counts each status beside its filter", async () => {
    stubApi(rows);
    renderWithClient(<SessionsTable />);
    await (await loadedTable()).findByText("room-web");
    const group = within(screen.getByRole("radiogroup", { name: "Filter by status" }));
    expect(group.getByRole("radio", { name: /^All/ }).textContent).toBe("All2");
    expect(group.getByRole("radio", { name: /^Failed/ }).textContent).toBe("Failed1");
    expect(group.getByRole("radio", { name: /^Active/ }).textContent).toBe("Active0");
  });

  it("remembers the filters and restores them when the URL has none", async () => {
    stubApi(rows);
    const first = renderWithClient(<SessionsTable />);
    await (await loadedTable()).findByText("room-web");
    fireEvent.click(within(screen.getByRole("radiogroup", { name: "Filter by status" })).getByRole("radio", { name: /^Failed/ }));
    first.unmount();

    routerReplace.mockReset();
    renderWithClient(<SessionsTable />);
    expect(await (await loadedTable()).findByText("room-sip")).toBeTruthy();
    expect(table().queryByText("room-web")).toBeNull();
    expect(routerReplace).toHaveBeenCalledWith("/console/sessions?status=failed", { scroll: false });
  });

  it("lets the URL win over remembered filters", async () => {
    window.localStorage.setItem(SESSION_FILTERS_STORAGE_KEY, JSON.stringify({ status: "failed" }));
    searchParams = new URLSearchParams("channel=web");
    stubApi(rows);
    renderWithClient(<SessionsTable />);
    expect(await (await loadedTable()).findByText("room-web")).toBeTruthy();
    expect(table().queryByText("room-sip")).toBeNull();
  });

  it("drops a remembered agent that no longer exists", async () => {
    window.localStorage.setItem(SESSION_FILTERS_STORAGE_KEY, JSON.stringify({ agentId: "a-gone" }));
    stubApi(rows);
    renderWithClient(<SessionsTable />);
    expect(await (await loadedTable()).findByText("room-web")).toBeTruthy();
    expect(table().getByText("room-sip")).toBeTruthy();
  });

  it("validates remembered filters on read", () => {
    expect(readStoredFilters(null)).toEqual(EMPTY_FILTERS);
    expect(readStoredFilters("not json")).toEqual(EMPTY_FILTERS);
    expect(readStoredFilters(JSON.stringify({ status: "exploded", channel: "fax", range: "90d", agentId: "<script>" }))).toEqual(
      EMPTY_FILTERS,
    );
    expect(readStoredFilters(JSON.stringify({ status: "failed", channel: "sip_in", range: "7d", agentId: "a-1" }))).toEqual({
      ...EMPTY_FILTERS,
      status: "failed",
      channel: "sip_in",
      range: "7d",
      agentId: "a-1",
    });
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

// V5-34: `GET /v1/sessions/export.csv` (a new route, ask #178(7) — the console
// gets a button, not an extension of an existing one) and the optional
// post-call field columns (`config.qa.fields` of the single filtered agent).
describe("SessionsTable — export CSV and post-call field columns (V5-34)", () => {
  function agent(overrides: Partial<AgentOut> = {}): AgentOut {
    return {
      id: "a-1",
      slug: "claims",
      name: "Claims desk",
      description: "",
      pack_id: "generic",
      ui_panel_id: "generic",
      published: false,
      config_version: 1,
      created_at: "2026-09-01T00:00:00Z",
      updated_at: "2026-09-01T00:00:00Z",
      config: { instructions: "Hi", pipeline: { mode: "cascaded" } },
      ...overrides,
    } as AgentOut;
  }

  it("exportCsvHref carries the same filters as the list", () => {
    expect(exportCsvHref(EMPTY_FILTERS)).toBe("/api/console/sessions/export.csv");
    expect(exportCsvHref({ agentId: "a-1", status: "ended", channel: "web", connectionId: "c-1", range: "" })).toBe(
      "/api/console/sessions/export.csv?agent_id=a-1&status=ended&channel=web&connection_id=c-1",
    );
    const href = exportCsvHref({ ...EMPTY_FILTERS, range: "today" });
    expect(href).toContain("from=");
  });

  it("offers an Export CSV link to the same route with no filters applied", async () => {
    stubApi([session({ id: "1" })]);
    renderWithClient(<SessionsTable />);
    await loadedTable();
    const link = screen.getByRole("link", { name: /Export CSV/ });
    expect(link.getAttribute("href")).toBe("/api/console/sessions/export.csv");
  });

  it("offers no Columns picker with no single agent filtered", async () => {
    stubApi([session({ id: "1" })], { agents: [agent({ config: { instructions: "Hi", pipeline: { mode: "cascaded" }, qa: { fields: [{ name: "claim_type" }] } } })] });
    renderWithClient(<SessionsTable />);
    await loadedTable();
    expect(screen.queryByRole("combobox", { name: "Columns" })).toBeNull();
  });

  it("offers the filtered agent's post-call fields as toggleable columns", async () => {
    searchParams = new URLSearchParams("agent=a-1");
    stubApi([session({ id: "1", agent_id: "a-1" })], {
      agents: [
        agent({
          config: {
            instructions: "Hi",
            pipeline: { mode: "cascaded" },
            qa: { fields: [{ name: "claim_type", type: "text" }, { name: "injury", type: "boolean" }] },
          },
        }),
      ],
    });
    renderWithClient(<SessionsTable />);
    await loadedTable();

    expect(table().queryByText("Claim type")).toBeNull();
    fireEvent.click(screen.getByRole("combobox", { name: "Columns" }));
    fireEvent.click(screen.getByRole("option", { name: "Claim type" }));
    const header = table().getByText("Claim type");
    expect(header).toBeTruthy();
    // Not in the (not yet extended) list payload — the column renders honestly as "Not set",
    // in the cell right under the new header (never "Not mentioned", reserved for a value
    // the api actually returned as `null`).
    const columnIndex = Array.from(header.closest("tr")?.children ?? []).indexOf(header.closest("th")!);
    const bodyRow = table().getAllByRole("row")[1];
    expect(bodyRow.children[columnIndex]?.textContent).toBe("Not set");
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
