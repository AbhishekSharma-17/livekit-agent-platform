import * as React from "react";

import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { toast } from "sonner";

import { AppCard } from "@/components/console/tools/apps/app-card";
import { ConnectionRow } from "@/components/console/tools/apps/connection-row";
import {
  connectionFixture,
  connectionFixtureAccounts,
  connectionPage,
  TOOLKIT_GITHUB_CONNECTED,
} from "./fixtures/apps";

// No `<Toaster/>` is mounted in these component tests, so `toast.*` renders
// nothing to the DOM — mocked and asserted on directly, the same pattern
// `console-create-agent.test.tsx` uses.
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

/**
 * R-V5-13 / docs/v5/PLAN-V5.md V5-54 card: the app card's account list
 * (label, status, Default chip, Rename, Make default, Reconnect,
 * Disconnect), "Add another account", and the plain 409 wording for a
 * duplicate account name.
 */

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const nativeMatches = Element.prototype.matches;
beforeAll(() => {
  // Radix `Dialog` checks `:popover-open`/`:modal` in jsdom, which throws
  // without this stub (same pattern as `console-apps.test.tsx`).
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
  body: Record<string, unknown> | undefined;
}

function bodyField(call: Call, path: string[]): unknown {
  let value: unknown = call.body;
  for (const key of path) {
    if (typeof value !== "object" || value === null) return undefined;
    value = (value as Record<string, unknown>)[key];
  }
  return value;
}

function stubApi(overrides: (call: Call) => { status: number; body: unknown } | undefined = () => undefined) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      const call = { url: String(input), method: init?.method ?? "GET", body };
      calls.push(call);
      const override = overrides(call);
      let status = 200;
      let responseBody: unknown;
      if (override) ({ status, body: responseBody } = override);
      else if (call.url.includes("/auth/me")) {
        responseBody = {
          user: { id: "u1", email: "admin@example.test" },
          workspaces: [{ id: "ws1", name: "Test workspace", slug: "test", role: "admin" }],
        };
      } else if (call.url.includes("/agents")) {
        responseBody = { items: [], total: 0 };
      } else if (call.url.includes("/tool-providers/composio/connections") && call.method === "GET") {
        responseBody = { items: [], total: 0 };
      } else responseBody = {};
      return { ok: status < 400, status, json: async () => responseBody } as Response;
    }),
  );
  return calls;
}

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

// No `@testing-library/jest-dom` in this project (see `console-apps.test.tsx`
// for the same pattern) — check the native `disabled` property instead of
// `.toBeDisabled()`.
function isDisabled(element: HTMLElement): boolean {
  return (element as HTMLButtonElement).disabled;
}

describe("AppCard — several accounts of one app (R-V5-13)", () => {
  it('shows "2 accounts" and, once opened, both account rows with the Default chip', async () => {
    const accounts = connectionFixtureAccounts();
    stubApi((call) => {
      if (call.url.endsWith("/tool-providers/composio/connections") && call.method === "GET") {
        return { status: 200, body: connectionPage(accounts) };
      }
      if (call.url.endsWith(`/connections/${accounts[0].id}`)) return { status: 200, body: accounts[0] };
      if (call.url.endsWith(`/connections/${accounts[1].id}`)) return { status: 200, body: accounts[1] };
      return undefined;
    });
    renderWithClient(<AppCard toolkit={TOOLKIT_GITHUB_CONNECTED} />);

    expect(await screen.findByText("2 accounts")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Manage 2 accounts" }));

    const dialog = await screen.findByRole("dialog", { name: "GitHub accounts" });
    expect(await within(dialog).findByText("Work")).toBeTruthy();
    expect(within(dialog).getByText("Personal")).toBeTruthy();
    expect(within(dialog).getByText("Default")).toBeTruthy();
  });

  it('"Add another account" posts connect with the label, and never shows a Connect button (the app is already connected)', async () => {
    const calls = stubApi((call) => {
      if (/\/toolkits\/[^/?]+$/.test(call.url)) return { status: 200, body: TOOLKIT_GITHUB_CONNECTED };
      if (call.url.endsWith("/connections/conn_github")) return { status: 200, body: connectionFixture() };
      if (call.url.endsWith("/tool-providers/composio/connections") && call.method === "POST") {
        return { status: 201, body: { connection_id: "conn_new", status: "active", redirect_url: null, expires_at: null } };
      }
      return undefined;
    });
    renderWithClient(<AppCard toolkit={TOOLKIT_GITHUB_CONNECTED} />);

    // `useWriteAccess` resolves from `/auth/me`; the button is disabled until it does.
    const addAccountButton = await screen.findByRole("button", { name: "Add another account" });
    await waitFor(() => expect(isDisabled(addAccountButton)).toBe(false));
    fireEvent.click(addAccountButton);
    const dialog = await screen.findByRole("dialog", { name: "Add another GitHub account" });
    expect(within(dialog).getByText(/sign out of GitHub/)).toBeTruthy();
    await within(dialog).findByText("Managed — one click");
    fireEvent.change(within(dialog).getByLabelText("Name this account"), { target: { value: "Personal" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Connect" }));

    await waitFor(() =>
      expect(calls.some((c) => c.url.endsWith("/tool-providers/composio/connections") && c.method === "POST")).toBe(true),
    );
    const call = calls.find((c) => c.url.endsWith("/tool-providers/composio/connections") && c.method === "POST")!;
    expect(bodyField(call, ["label"])).toBe("Personal");
    expect(screen.queryByRole("button", { name: "Connect" })).toBeNull();
  });
});

describe("ConnectionRow — Rename and Make default (R-V5-13)", () => {
  it("Rename posts the PATCH with the trimmed label", async () => {
    const calls = stubApi((call) => {
      if (call.url.endsWith("/connections/conn_gh") && call.method === "GET") {
        return { status: 200, body: connectionFixture({ id: "conn_gh", label: "GitHub" }) };
      }
      if (call.url.endsWith("/connections/conn_gh") && call.method === "PATCH") {
        return { status: 200, body: connectionFixture({ id: "conn_gh", label: "Work" }) };
      }
      return undefined;
    });
    renderWithClient(<ConnectionRow connectionId="conn_gh" toolkit={{ slug: "github", name: "GitHub", auth_fields: {} }} />);

    fireEvent.click(await screen.findByRole("button", { name: "Rename" }));
    const dialog = await screen.findByRole("dialog", { name: "Rename this GitHub account" });
    fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: "  Work  " } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Rename" }));

    await waitFor(() => expect(calls.some((c) => c.method === "PATCH" && c.url.endsWith("/connections/conn_gh"))).toBe(true));
    const call = calls.find((c) => c.method === "PATCH")!;
    expect(bodyField(call, ["label"])).toBe("Work");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("Make default posts is_default and moves the Default chip onto this account", async () => {
    let isDefault = false;
    const calls = stubApi((call) => {
      if (call.url.endsWith("/connections/conn_personal") && call.method === "GET") {
        return { status: 200, body: connectionFixture({ id: "conn_personal", label: "Personal", is_default: isDefault }) };
      }
      if (call.url.endsWith("/connections/conn_personal") && call.method === "PATCH") {
        isDefault = true;
        return { status: 200, body: connectionFixture({ id: "conn_personal", label: "Personal", is_default: true }) };
      }
      return undefined;
    });
    renderWithClient(<ConnectionRow connectionId="conn_personal" toolkit={{ slug: "github", name: "GitHub", auth_fields: {} }} />);

    expect(await screen.findByText("Personal")).toBeTruthy();
    expect(screen.queryByText("Default")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Make default" }));

    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    const call = calls.find((c) => c.method === "PATCH")!;
    expect(bodyField(call, ["is_default"])).toBe(true);
    expect(await screen.findByText("Default")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Make default" })).toBeNull();
  });

  it("a 409 on rename shows the plain, agreed wording — not the api's own duplicate-label message", async () => {
    stubApi((call) => {
      if (call.url.endsWith("/connections/conn_gh") && call.method === "GET") {
        return { status: 200, body: connectionFixture({ id: "conn_gh", label: "GitHub" }) };
      }
      if (call.url.endsWith("/connections/conn_gh") && call.method === "PATCH") {
        return {
          status: 409,
          body: { error: { code: "conflict", message: "another account of this app is already called 'Work'; pick another name" } },
        };
      }
      return undefined;
    });
    renderWithClient(<ConnectionRow connectionId="conn_gh" toolkit={{ slug: "github", name: "GitHub", auth_fields: {} }} />);

    fireEvent.click(await screen.findByRole("button", { name: "Rename" }));
    const dialog = await screen.findByRole("dialog", { name: "Rename this GitHub account" });
    fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: "Work" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Rename" }));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Couldn't rename — Another account of this app already uses that name"));
  });
});
