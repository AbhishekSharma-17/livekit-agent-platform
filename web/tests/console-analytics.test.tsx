import * as React from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { AnalyticsView } from "@/components/console/analytics/analytics-view";

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
  failed: 1,
  by_day: [
    { key: "2026-01-01", sessions: 5, minutes: 120, cost_usd: "2.00", failed: 0 },
    { key: "2026-01-02", sessions: 7, minutes: 220.5, cost_usd: "2.20", failed: 1 },
  ],
  by_agent: [{ key: "Concierge", sessions: 12, minutes: 340.5, cost_usd: "4.20", failed: 1 }],
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

  it("renders one bar per day and the by-agent table", async () => {
    renderView();
    await screen.findAllByText("12");
    expect(screen.getAllByText("Concierge").length).toBeGreaterThan(0);
    expect(document.querySelectorAll('[aria-label="Bar chart of sessions per day"] > div')).toHaveLength(2);
  });

  it("changes the range through the URL", async () => {
    renderView();
    await screen.findAllByText("12");

    fireEvent.click(screen.getByRole("combobox", { name: "Date range" }));
    fireEvent.click(await screen.findByRole("option", { name: "Today" }));

    expect(replace).toHaveBeenCalledWith("/console/analytics?range=today", { scroll: false });
  });
});
