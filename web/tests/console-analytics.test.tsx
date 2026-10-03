import * as React from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { agentHealth, AnalyticsView, toCsv } from "@/components/console/analytics/analytics-view";

// jsdom has no `scrollIntoView`; Radix Select calls it when opening (see
// console-provider-slot.test.tsx for the same stub).
const nativeScrollIntoView = Element.prototype.scrollIntoView;
beforeAll(() => {
  Element.prototype.scrollIntoView = function scrollIntoView() {};
});
afterAll(() => {
  Element.prototype.scrollIntoView = nativeScrollIntoView;
});

/** `/console/analytics?range=` (V2-14): summary cards, by-day bars, by-agent table. */
let searchParams = new URLSearchParams();
const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
  usePathname: () => "/console/analytics",
  useSearchParams: () => searchParams,
}));

const SUMMARY = {
  sessions: 12,
  minutes: 340.5,
  cost_usd: "4.20",
  estimated_usd: "4.00",
  accuracy_pct: 95,
  failed: 1,
  by_day: [
    { key: "2026-01-01", sessions: 5, minutes: 120, cost_usd: "2.00", estimated_usd: "1.90", failed: 0 },
    { key: "2026-01-02", sessions: 7, minutes: 220.5, cost_usd: "2.20", estimated_usd: "2.10", failed: 1 },
  ],
  by_agent: [{ key: "Concierge", sessions: 12, minutes: 340.5, cost_usd: "4.20", failed: 1 }],
  top_drivers: [
    { provider_id: "cartesia-tts", model: "sonic-3", unit: "chars", cost_usd: "2.50", share_pct: 60, estimated_usd: "2.30" },
    { provider_id: "livekit-agent", model: null, unit: "minutes", cost_usd: "1.70", share_pct: 40, estimated_usd: "1.60" },
  ],
};

function renderView() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AnalyticsView />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  searchParams = new URLSearchParams();
  replace.mockClear();
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input), "http://localhost:3000");
      if (url.pathname === "/api/console/providers") {
        return { ok: true, status: 200, json: async () => ({ providers: [] }) } as Response;
      }
      expect(url.pathname).toBe("/api/console/analytics/summary");
      return { ok: true, status: 200, json: async () => SUMMARY } as Response;
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AnalyticsView", () => {
  it("defaults to the 30d range and renders the summary cards", async () => {
    renderView();
    expect(await screen.findAllByText("12")).not.toHaveLength(0);
    expect(screen.getAllByText("$4.20").length).toBeGreaterThan(0);
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    const url = new URL(String(fetchMock.mock.calls[0][0]), "http://localhost:3000");
    expect(url.searchParams.get("range")).toBe("30d");
  });

  it("renders one bar-group per day and the by-agent table (V4-16: two series, actual and estimated)", async () => {
    renderView();
    await screen.findAllByText("12");
    expect(screen.getAllByText("Concierge").length).toBeGreaterThan(0);
    expect(
      document.querySelectorAll('[aria-label="Bar chart of actual and estimated cost per day"] > div'),
    ).toHaveLength(2);
  });

  it("shows the Estimated tile with accuracy and the Top cost drivers table (docs/v4/COSTS.md §5 item 6)", async () => {
    renderView();
    await screen.findAllByText("12");
    expect(screen.getAllByText("Estimated").length).toBeGreaterThan(0);
    expect(screen.getAllByText("$4.00").length).toBeGreaterThan(0);
    expect(screen.getByText("accuracy 95 %")).toBeTruthy();
    expect(screen.getByText("Top cost drivers")).toBeTruthy();
    // A plain label for a real provider kind and for a LiveKit pseudo id, never the raw provider_id alone.
    expect(screen.getAllByText("Call minutes").length).toBeGreaterThan(0);
    expect(screen.getAllByText("$1.60").length).toBeGreaterThan(0); // the estimated column
  });

  it("leads each cost driver's provider with its mark, and keeps the ids as text", async () => {
    const { container } = renderView();
    await screen.findAllByText("12");
    const names = [...container.querySelectorAll('table [data-slot="vendor-name"]')];
    const byText = (text: string) => names.find((el) => el.textContent === text);
    expect(byText("cartesia-tts")?.querySelector('[data-slot="vendor-mark"]')?.getAttribute("data-mark")).toBe("cartesia");
    expect(byText("livekit-agent")?.querySelector('[data-slot="vendor-mark"]')?.getAttribute("data-mark")).toBe("livekit");
    expect(byText("cartesia-tts")?.querySelector('[data-slot="vendor-mark"]')?.getAttribute("aria-hidden")).toBe("true");
    expect(container.textContent).toContain("sonic-3");
    // A job word such as "Call minutes" is not a vendor name, so no mark leads it (table or phone card).
    for (const label of screen.getAllByText("Call minutes")) expect(label.closest('[data-slot="vendor-name"]')).toBeNull();
  });

  it("changes the range through the URL", async () => {
    renderView();
    await screen.findAllByText("12");

    fireEvent.click(screen.getByRole("combobox", { name: "Date range" }));
    fireEvent.click(await screen.findByRole("option", { name: "Today" }));

    expect(replace).toHaveBeenCalledWith("/console/analytics?range=today", { scroll: false });
  });

  it("puts the date range and Refresh in the page header, beside the one h1", async () => {
    renderView();
    await screen.findAllByText("12");
    const header = document.querySelector('[data-slot="page-header"]') as HTMLElement;
    expect(header.querySelector("h1")?.textContent).toBe("Analytics");
    expect(header.querySelector('[aria-label="Date range"]')).not.toBeNull();
    expect(Array.from(header.querySelectorAll("button")).some((b) => b.textContent === "Refresh")).toBe(true);
  });

  it("marks each agent's health in words, and opens the Agents tab through the URL", async () => {
    renderView();
    await screen.findAllByText("12");
    // Concierge had a failed call in the range.
    expect(screen.getAllByText("Needs review").length).toBeGreaterThan(0);
    expect(agentHealth({ failed: 0, sessions: 3 }).label).toBe("Healthy");

    fireEvent.mouseDown(screen.getByRole("tab", { name: "Agents" }));
    expect(replace).toHaveBeenCalledWith("/console/analytics?tab=agents", { scroll: false });
  });

  it("shows the full table with Export CSV on a detail tab", async () => {
    searchParams = new URLSearchParams("tab=drivers");
    renderView();
    expect(await screen.findByText("What the money went on, largest first.")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Export CSV/ })).toBeTruthy();
    expect(screen.queryByText("Top cost drivers")).toBeNull();
  });

  it("builds CSV with quoted fields and blanks for missing values", () => {
    expect(toCsv(["Agent", "Cost"], [["Claims, desk", null], ['Say "hi"', 2]])).toBe(
      'Agent,Cost\r\n"Claims, desk",\r\n"Say ""hi""",2',
    );
  });

  it("says what went wrong in plain words, with Retry", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: false, status: 500, json: async () => ({ error: { code: "internal", message: "stack trace here" } }) }) as Response),
    );
    renderView();
    expect(await screen.findByText("Couldn't load analytics")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
    expect(screen.queryByText(/stack trace/)).toBeNull();
  });
});
