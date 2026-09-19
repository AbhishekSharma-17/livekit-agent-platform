import * as React from "react";

import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { SessionsTable } from "@/components/console/sessions/sessions-table";
import type { SessionOut, SessionPage } from "@/contracts/lkap-contracts";

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
    ...overrides,
  };
}

function stubFetchOnce(items: SessionOut[]) {
  const page: SessionPage = { items, total: items.length };
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ ok: true, status: 200, json: async () => page }) as Response),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("SessionsTable — DECISIONS-W2 D-W2-2(b) swept-row chip", () => {
  it("shows a muted 'never started' chip for a sweep-failed created row", async () => {
    stubFetchOnce([
      session({ id: "s-created", status: "failed", error: "never started", ended_at: "2026-09-19T00:10:00Z" }),
    ]);

    renderWithClient(<SessionsTable />);

    expect(await screen.findByText("never started")).toBeTruthy();
    expect(screen.getByText("failed")).toBeTruthy();
  });

  it("shows a muted 'summary never received' chip for a sweep-failed active row", async () => {
    stubFetchOnce([
      session({
        id: "s-active",
        status: "failed",
        error: "summary never received",
        started_at: "2026-09-19T00:00:00Z",
        ended_at: "2026-09-19T06:00:00Z",
      }),
    ]);

    renderWithClient(<SessionsTable />);

    expect(await screen.findByText("summary never received")).toBeTruthy();
  });

  it("does not show a swept chip for an ordinary failure", async () => {
    stubFetchOnce([session({ id: "s-crash", status: "failed", error: "LLM connection reset" })]);

    renderWithClient(<SessionsTable />);

    expect(await screen.findByText("failed")).toBeTruthy();
    expect(screen.queryByText("LLM connection reset")).toBeNull();
    expect(screen.queryByText("never started")).toBeNull();
  });

  it("does not show a swept chip for a healthy ended session", async () => {
    stubFetchOnce([session({ id: "s-ended", status: "ended", ended_at: "2026-09-19T00:05:00Z" })]);

    renderWithClient(<SessionsTable />);

    expect(await screen.findByText("ended")).toBeTruthy();
    expect(screen.queryByText("never started")).toBeNull();
  });
});
