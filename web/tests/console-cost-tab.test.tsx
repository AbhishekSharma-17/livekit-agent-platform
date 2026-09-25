import * as React from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CostTab } from "@/components/console/sessions-v2/cost-tab";
import type { SessionDetailOut } from "@/contracts/lkap-contracts";

/**
 * The Cost tab's V4-16 additions (docs/v4/COSTS.md §5 item 5, D-V4-46): the
 * Estimated / Actual / Difference tiles ("no estimate" for a session without
 * a snapshot; a "Vendor charged" tile once reconciled), the lines table's
 * "Estimated" and "Vendor charged" columns, the "Why it differs" list (one
 * sentence per driver naming the quantity and the delta), and the "Prices as
 * of" footer. `console-sessions-v2-extension.test.tsx` covers the pre-V4-16
 * basics (the "no price" literal, the empty state) and is left alone.
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

function renderTab(session: SessionDetailOut) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <CostTab session={session} />
    </QueryClientProvider>,
  );
}

describe("CostTab — estimate vs actual (V4-16)", () => {
  it("shows 'no estimate' for a session with actual cost but no snapshot", () => {
    const session = baseSession({
      cost: {
        total_usd: "0.20",
        lines: [{ provider_id: "openai", unit: "tokens_in", quantity: "100", unit_price_usd: "0.002", cost_usd: "0.20" }],
      },
    });
    renderTab(session);
    expect(screen.getAllByText("no estimate").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Estimated").length).toBeGreaterThan(0);
  });

  it("shows the Estimated, Actual and Difference tiles once both figures exist", () => {
    const session = baseSession({
      cost: {
        total_usd: "0.22",
        estimated_usd: "0.20",
        variance_usd: "0.02",
        variance_pct: 10,
        estimate_as_of: "2026-09-23",
        lines: [{ provider_id: "openai", unit: "tokens_in", quantity: "100", unit_price_usd: "0.002", cost_usd: "0.22" }],
        drivers: [
          {
            slot: "llm",
            provider_id: "openai",
            model: null,
            unit: "tokens_in",
            estimated_quantity: "90",
            actual_quantity: "100",
            estimated_usd: "0.18",
            actual_usd: "0.22",
            delta_usd: "0.04",
            reason: "more turns",
          },
        ],
      },
    });
    renderTab(session);
    expect(screen.getByText("$0.2000", { selector: "p" })).toBeTruthy();
    expect(screen.getByText(/\+\$0\.0200 \(\+10%\)/)).toBeTruthy();
    expect(screen.getByText("Why it differs")).toBeTruthy();
    expect(screen.getByText(/Agent's thinking: more turns/)).toBeTruthy();
    expect(screen.getByText(/100 vs 90 expected/)).toBeTruthy();
    expect(screen.getByText("Prices as of 2026-09-23.")).toBeTruthy();
  });

  it("shows a Vendor charged tile and column once reconciled", () => {
    const session = baseSession({
      cost: {
        total_usd: "0.20",
        estimated_usd: "0.19",
        reconciled_usd: "0.21",
        lines: [
          { provider_id: "openrouter-llm", unit: "tokens_in", quantity: "100", unit_price_usd: "0.002", cost_usd: "0.20", vendor_usd: "0.21" },
        ],
      },
    });
    renderTab(session);
    expect(screen.getAllByText("Vendor charged").length).toBeGreaterThanOrEqual(2); // the tile label and the column header
    expect(screen.getAllByText("$0.2100").length).toBeGreaterThan(0);
  });

  it("keeps 'no price' the literal for an unpriced line even with drivers present", () => {
    const session = baseSession({
      cost: {
        total_usd: "0.05",
        estimated_usd: "0.05",
        lines: [
          { provider_id: "openai", model: "gpt-4o", unit: "tokens_in", quantity: "100", unit_price_usd: "0.0005", cost_usd: "0.05" },
          { provider_id: "custom-tts", unit: "chars", quantity: "500", note: "no price" },
        ],
      },
    });
    renderTab(session);
    expect(screen.getByText("no price")).toBeTruthy();
    expect(screen.queryByText("$0")).toBeNull();
  });

  it("does not show the empty state when there is an estimate but no actual lines yet", () => {
    const session = baseSession({ cost: { lines: [], estimated_usd: "0.04" } });
    renderTab(session);
    expect(screen.queryByText("No cost data")).toBeNull();
    expect(screen.getByText("$0.0400", { selector: "p" })).toBeTruthy();
  });
});
