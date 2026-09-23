import * as React from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { AccountMenu, initialsFor } from "@/components/console/shell/account-menu";

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;

beforeAll(() => {
  // Radix menus in jsdom (editor README "Testing notes"): jsdom throws on
  // these selectors and Radix retries in a slow path, so an open menu costs
  // seconds per query without this.
  const matches = Element.prototype.matches;
  Element.prototype.matches = function (this: Element, selector: string) {
    if (selector === ":popover-open" || selector === ":modal") return false;
    return matches.call(this, selector);
  };
});

const ADA = { user: { id: "u1", email: "ada@example.com", name: "Ada Lovelace" }, workspaces: [] };
const BREAK_GLASS = {
  user: { id: "break-glass", email: "break-glass@lkap.local", name: "Break-glass admin", is_platform_admin: true },
  workspaces: [],
};

function stubFetch(me: unknown, meStatus = 200) {
  const calls: { url: string; method: string }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, method: init?.method ?? "GET" });
      if (url.includes("auth/me")) {
        return { ok: meStatus < 400, status: meStatus, json: async () => me } as Response;
      }
      return { ok: true, status: 204, json: async () => ({}), text: async () => "" } as Response;
    }),
  );
  return calls;
}

function renderMenu() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AccountMenu />
    </QueryClientProvider>,
  );
}

const signOut = vi.fn(async () => {});
vi.mock("@/components/console/lib/sign-out", () => ({
  BREAK_GLASS_USER_ID: "break-glass",
  signOut: () => signOut(),
}));

beforeEach(() => {
  signOut.mockClear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("initialsFor", () => {
  it.each([
    ["Ada Lovelace", "ada@example.com", "AL"],
    ["cher", "c@example.com", "C"],
    [undefined, "zed@example.com", "Z"],
    ["  ", "bo@example.com", "B"],
  ])("%s / %s → %s", (name, email, expected) => {
    expect(initialsFor(name, email)).toBe(expected);
  });
});

/** Ask V2-14-1: sign out from every console screen, not just Settings. */
describe("AccountMenu", () => {
  it("shows the signed-in user and signs out from the menu", async () => {
    stubFetch(ADA);
    renderMenu();
    const trigger = await screen.findByRole("button", { name: "Account: Ada Lovelace" });
    expect(trigger.textContent).toContain("AL");
    expect(trigger.textContent).toContain("ada@example.com");

    fireEvent.keyDown(trigger, { key: "Enter" });
    // Text queries, not `*ByRole`: role queries over an open Radix menu cost
    // seconds each in jsdom.
    const settings = await screen.findByText("Account settings");
    expect(settings.closest('[role="menuitem"]')?.getAttribute("href")).toBe("/console/settings?tab=workspace#account");
    fireEvent.click(screen.getByText("Sign out"));

    await waitFor(() => expect(signOut).toHaveBeenCalledTimes(1));
  });

  it("offers no sign-out for the break-glass admin token", async () => {
    stubFetch(BREAK_GLASS);
    renderMenu();
    const trigger = await screen.findByRole("button", { name: "Account: Break-glass admin" });
    fireEvent.keyDown(trigger, { key: "Enter" });
    expect(await screen.findByText(/no session to sign out of/)).toBeTruthy();
    expect(screen.queryByText("Sign out")).toBeNull();
  });

  it("renders nothing while auth/me is unavailable", async () => {
    const calls = stubFetch({ error: { code: "not_found", message: "not found" } }, 404);
    const { container } = renderMenu();
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    expect(container.querySelector('[data-testid="account-menu-trigger"]')).toBeNull();
  });
});
