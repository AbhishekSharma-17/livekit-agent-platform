import * as React from "react";

import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import {
  WorkspacePricesButton,
  WorkspacePricesDialog,
} from "@/components/console/settings/workspace-prices-dialog";
import type { WorkspacePrice } from "@/contracts/lkap-contracts";

/**
 * "Your prices" (docs/v4/COSTS.md §5 item 1, `PUT /v1/workspace/prices`):
 * loads the stored table, edits and additions are saved together with every
 * untouched row (the route replaces the whole list — R-V4-44's owner note),
 * client-side validation (0–1000 USD) blocks a bad save, the `prefill` a
 * "Set a price" link passes seeds one blank row, and the entry button is
 * admin-gated (docs/v2/_asks.md V2-20-5's pattern).
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

const STORED: WorkspacePrice[] = [
  { provider_id: "bey-avatar", model: null, unit: "minutes", usd_per_unit: "0.10", note: "Starter plan", as_of: "2026-09-01" },
  { provider_id: "eleven-tts", model: "eleven_flash_v2_5", unit: "chars", usd_per_unit: "0.00003", note: null, as_of: "2026-09-10" },
];

interface Log {
  method: string;
  url: string;
  body: unknown;
}

function stubFetch(role: "admin" | "builder" = "admin") {
  const calls: Log[] = [];
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
    if (url.includes("/workspace/prices") && method === "GET") {
      return { ok: true, status: 200, json: async () => ({ prices: STORED }) } as Response;
    }
    if (url.includes("/workspace/prices") && method === "PUT") {
      return { ok: true, status: 200, json: async () => body } as Response;
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

function withClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("WorkspacePricesDialog", () => {
  it("loads the stored prices into the table", async () => {
    vi.stubGlobal("fetch", stubFetch().fetch);
    withClient(<WorkspacePricesDialog open onOpenChange={() => {}} />);
    await waitFor(() => expect(screen.getByDisplayValue("bey-avatar")).toBeTruthy());
    expect(screen.getByDisplayValue("eleven-tts")).toBeTruthy();
    expect(screen.getByDisplayValue("0.10")).toBeTruthy();
  });

  it("saves an edit plus every untouched stored row (the route replaces the whole list)", async () => {
    const { fetch, calls } = stubFetch();
    vi.stubGlobal("fetch", fetch);
    withClient(<WorkspacePricesDialog open onOpenChange={() => {}} />);
    await waitFor(() => expect(screen.getByDisplayValue("0.10")).toBeTruthy());

    fireEvent.change(screen.getByDisplayValue("0.10"), { target: { value: "0.12" } });
    fireEvent.click(screen.getByRole("button", { name: "Save prices" }));

    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    const put = calls.find((c) => c.method === "PUT")!;
    const prices = (put.body as { prices: WorkspacePrice[] }).prices;
    expect(prices).toHaveLength(2);
    const edited = prices.find((p) => p.provider_id === "bey-avatar")!;
    expect(String(edited.usd_per_unit)).toBe("0.12");
    const untouched = prices.find((p) => p.provider_id === "eleven-tts")!;
    expect(String(untouched.usd_per_unit)).toBe("0.00003");
    expect(untouched.model).toBe("eleven_flash_v2_5");
  });

  it("refuses to save a USD amount outside 0–1000", async () => {
    const { fetch, calls } = stubFetch();
    vi.stubGlobal("fetch", fetch);
    withClient(<WorkspacePricesDialog open onOpenChange={() => {}} />);
    await waitFor(() => expect(screen.getByDisplayValue("0.10")).toBeTruthy());

    fireEvent.change(screen.getByDisplayValue("0.10"), { target: { value: "1001" } });
    fireEvent.click(screen.getByRole("button", { name: "Save prices" }));

    expect(await screen.findByText("Between 0 and 1000")).toBeTruthy();
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
  });

  it("seeds one blank row from a 'Set a price' prefill, without duplicating an existing one", async () => {
    vi.stubGlobal("fetch", stubFetch().fetch);
    const { rerender } = withClient(
      <WorkspacePricesDialog
        open
        onOpenChange={() => {}}
        prefill={{ provider_id: "openai-realtime", model: "gpt-realtime", unit: "audio_tokens_in" }}
      />,
    );
    await waitFor(() => expect(screen.getByDisplayValue("openai-realtime")).toBeTruthy());
    // Still both stored rows, plus the one new blank row for the prefill.
    expect(screen.getByDisplayValue("bey-avatar")).toBeTruthy();
    expect(screen.getByDisplayValue("eleven-tts")).toBeTruthy();

    // A prefill matching an already-stored row adds nothing.
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    rerender(
      <QueryClientProvider client={client}>
        <WorkspacePricesDialog open onOpenChange={() => {}} prefill={{ provider_id: "bey-avatar", model: null, unit: "minutes" }} />
      </QueryClientProvider>,
    );
  });
});

describe("WorkspacePricesButton — admin gating (docs/v2/_asks.md V2-20-5)", () => {
  it("is enabled for an admin", async () => {
    vi.stubGlobal("fetch", stubFetch("admin").fetch);
    withClient(<WorkspacePricesButton />);
    await screen.findByRole("button", { name: "Your prices" });
    // GatedButton swaps in a whole new element between its disabled (loading)
    // and allowed branches, so the button must be re-queried on every poll —
    // a captured reference from before the role resolves goes stale.
    await waitFor(() => expect((screen.getByRole("button", { name: "Your prices" }) as HTMLButtonElement).disabled).toBe(false));
  });

  it("is disabled for a builder", async () => {
    vi.stubGlobal("fetch", stubFetch("builder").fetch);
    withClient(<WorkspacePricesButton />);
    await screen.findByRole("button", { name: "Your prices" });
    await waitFor(() => expect((screen.getByRole("button", { name: "Your prices" }) as HTMLButtonElement).disabled).toBe(true));
  });

  it("opens the dialog on click", async () => {
    vi.stubGlobal("fetch", stubFetch("admin").fetch);
    withClient(<WorkspacePricesButton />);
    await screen.findByRole("button", { name: "Your prices" });
    await waitFor(() => expect((screen.getByRole("button", { name: "Your prices" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "Your prices" }));
    const dialog = await screen.findByRole("dialog", { name: "Your prices" });
    expect(within(dialog).getByText(/Prices you enter here/)).toBeTruthy();
  });
});
