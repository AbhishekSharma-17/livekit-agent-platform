import * as React from "react";

import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { AppCard } from "@/components/console/tools/apps/app-card";
import { ConnectionRow } from "@/components/console/tools/apps/connection-row";
import {
  UNIDENTIFIED_ACCOUNT,
  accountIdentity,
  accountName,
  appAccountName,
} from "@/components/console/tools/apps/account-identity";
import { connectionFixture, connectionPage, TOOLKIT_GITHUB_CONNECTED } from "./fixtures/apps";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

/**
 * V6-35: every connected app account says who it is signed in as (the
 * address, user name or workspace the app reports), in the accounts dialog,
 * on the app card and in every picker; an unidentified account says so and
 * offers "Check now".
 */

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

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
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
});
afterEach(() => {
  vi.unstubAllGlobals();
});

interface Call {
  url: string;
  method: string;
}

function stubApi(overrides: (call: Call) => unknown) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const call = { url: String(input), method: init?.method ?? "GET" };
      calls.push(call);
      let body = overrides(call);
      if (body === undefined) {
        if (call.url.includes("/auth/me")) {
          body = {
            user: { id: "u1", email: "admin@example.test" },
            workspaces: [{ id: "ws1", name: "Test workspace", slug: "test", role: "admin" }],
          };
        } else if (call.url.includes("/agents")) body = { items: [], total: 0 };
        else body = {};
      }
      return { ok: true, status: 200, json: async () => body } as Response;
    }),
  );
  return calls;
}

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

const WORK = connectionFixture({ id: "conn_work", label: "Gmail", identity: "sam@example.com", identity_kind: "email" });
const HOME = connectionFixture({
  id: "conn_home",
  label: "Gmail",
  is_default: false,
  status: "expired",
  needs_reconnect: true,
  identity: "lee@example.com",
  identity_kind: "email",
});

describe("account naming helpers (V6-35)", () => {
  it("names an account by its label and who it is, without repeating either", () => {
    expect(accountName(connectionFixture({ label: "Work", identity: "sam@example.com" }))).toBe("Work · sam@example.com");
    expect(accountName(connectionFixture({ label: "sam@example.com", identity: "sam@example.com" }))).toBe("sam@example.com");
    expect(accountName(connectionFixture({ label: "Work", identity: null }))).toBe("Work");
    expect(accountName(connectionFixture({ label: "", identity: "  " }), "GitHub")).toBe("GitHub");
    expect(accountIdentity(connectionFixture({ identity: "  " }))).toBeNull();
  });

  it("names an account with its app for a picker across apps, leaving out a label that only repeats the app", () => {
    expect(appAccountName(connectionFixture({ toolkit_name: "Gmail", label: "Gmail", identity: "sam@example.com" }))).toBe(
      "Gmail · sam@example.com",
    );
    expect(appAccountName(connectionFixture({ toolkit_name: "Gmail", label: "Work", identity: "sam@example.com" }))).toBe(
      "Gmail · Work · sam@example.com",
    );
    expect(appAccountName(connectionFixture({ toolkit_name: "Gmail", label: "Work", identity: null }))).toBe("Gmail · Work");
  });

  it("uses no em dash and no colon in what it shows", () => {
    for (const text of [
      accountName(WORK),
      appAccountName(WORK),
      UNIDENTIFIED_ACCOUNT,
    ]) {
      expect(text).not.toMatch(/[—:;]/);
    }
  });
});

describe("AppCard and its accounts dialog say which account is which (V6-35)", () => {
  it("lists who each account is on the card and in every dialog row, even when both labels are the app's name", async () => {
    stubApi((call) => {
      if (call.url.endsWith("/tool-providers/composio/connections")) return connectionPage([WORK, HOME]);
      if (call.url.endsWith("/connections/conn_work")) return WORK;
      if (call.url.endsWith("/connections/conn_home")) return HOME;
      return undefined;
    });
    renderWithClient(<AppCard toolkit={{ ...TOOLKIT_GITHUB_CONNECTED, slug: "github", name: "Gmail" }} />);

    const summary = await screen.findByRole("list", { name: "Gmail accounts" });
    expect(within(summary).getByText("Gmail · sam@example.com")).toBeTruthy();
    expect(within(summary).getByText("Gmail · lee@example.com")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Manage 2 accounts" }));
    const dialog = await screen.findByRole("dialog", { name: "Gmail accounts" });
    expect(await within(dialog).findByText("sam@example.com")).toBeTruthy();
    expect(await within(dialog).findByText("lee@example.com")).toBeTruthy();
    expect(within(dialog).getByText("Needs reconnect")).toBeTruthy();
    expect(within(dialog).getByText("Default")).toBeTruthy();
  });

  it('shows "Account not identified yet" with "Check now", which asks the app and shows the answer', async () => {
    const unknown = connectionFixture({ id: "conn_gh", label: "GitHub", identity: null });
    const calls = stubApi((call) => {
      if (call.url.includes("/connections/conn_gh") && call.url.includes("identify=true")) {
        return { ...unknown, identity: "@octo-sam", identity_kind: "username" };
      }
      if (call.url.endsWith("/connections/conn_gh")) return unknown;
      return undefined;
    });
    renderWithClient(<ConnectionRow connectionId="conn_gh" toolkit={TOOLKIT_GITHUB_CONNECTED} />);

    expect(await screen.findByText(UNIDENTIFIED_ACCOUNT)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Check now" }));

    expect(await screen.findByText("@octo-sam")).toBeTruthy();
    await waitFor(() => expect(screen.queryByText(UNIDENTIFIED_ACCOUNT)).toBeNull());
    expect(calls.some((c) => c.url.includes("/connections/conn_gh") && c.url.includes("identify=true"))).toBe(true);
  });

  it('offers no "Check now" on a broken account (the app cannot be asked until it is reconnected)', async () => {
    const broken = connectionFixture({ id: "conn_gh", status: "expired", needs_reconnect: true, identity: null });
    stubApi((call) => (call.url.endsWith("/connections/conn_gh") ? broken : undefined));
    renderWithClient(<ConnectionRow connectionId="conn_gh" toolkit={TOOLKIT_GITHUB_CONNECTED} />);

    expect(await screen.findByText(UNIDENTIFIED_ACCOUNT)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Check now" })).toBeNull();
  });
});
