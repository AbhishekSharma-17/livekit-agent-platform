import * as React from "react";

import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { AppsTab } from "@/components/console/tools/apps/apps-tab";
import { appsStatusFixture, keyTestFailed, keyTestOk, STATUS_DISABLED, STATUS_NOT_SET_UP, toolkitPage } from "./fixtures/apps";

/**
 * The **Enable Composio** flow and the enabled header (docs/v5/COMPOSIO.md
 * §6, D-V5-C13): every acceptance bullet on the V5-22 card that starts "Enable:"
 * or "Header:". The enabled state renders `AppGallery`, whose category
 * `Select` needs `ResizeObserver` (jsdom has none — see
 * console-http-tool-editor.test.tsx, the only other suite that mounts one).
 */

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const nativeMatches = Element.prototype.matches;
beforeAll(() => {
  // Radix Dialog/DropdownMenu query `:popover-open`/`:modal`, unsupported by
  // jsdom (see console-editor-shell.test.tsx / console-credentials-page.test.tsx).
  Element.prototype.matches = function matches(this: Element, selector: string) {
    if (selector === ":popover-open" || selector === ":modal") return false;
    return nativeMatches.call(this, selector);
  };
});
afterAll(() => {
  Element.prototype.matches = nativeMatches;
});

// `vi.unstubAllGlobals()` runs after every test (it also clears the fetch
// stub), so this is re-applied per test rather than once in `beforeAll`.
beforeEach(() => {
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
});

interface Call {
  url: string;
  method: string;
  body: Record<string, unknown> | undefined;
}

// No `@testing-library/jest-dom` in this project (see console-create-agent.test.tsx
// for the same pattern) — check the native `disabled` property instead of `.toBeDisabled()`.
function isDisabled(element: HTMLElement): boolean {
  return (element as HTMLButtonElement).disabled;
}

/**
 * Write-gated buttons (`useWriteAccess`) read `disabled` until `GET /auth/me`
 * resolves — a separate query from the one `findByRole` already waited on —
 * so a click right after finding the button can land on it while it's still
 * disabled. Wait for the real state before clicking.
 */
async function clickWhenEnabled(button: HTMLElement) {
  await waitFor(() => expect(isDisabled(button)).toBe(false));
  fireEvent.click(button);
}

/** `call.body?.foo` with a type jsdom's parsed JSON never gives us for free. */
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
      } else if (call.url.includes("/tool-providers/composio/status")) {
        responseBody = STATUS_NOT_SET_UP;
      } else if (call.url.includes("/tool-providers/composio/toolkits")) {
        responseBody = toolkitPage();
      } else if (call.url.includes("/tool-providers/composio/connections")) {
        responseBody = { items: [], total: 0 };
      } else if (call.url.includes("/credentials") && call.method === "POST") {
        responseBody = { id: "cred_composio", provider_id: "composio", label: "Composio key", fingerprint: "…a1b2", created_at: "2026-09-24T00:00:00Z", updated_at: "2026-09-24T00:00:00Z" };
      } else responseBody = {};
      return { ok: status < 400, status, json: async () => responseBody } as Response;
    }),
  );
  return calls;
}

function renderTab() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AppsTab />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AppsTab — Enable Composio", () => {
  it("renders the empty state with no key row", async () => {
    stubApi();
    renderTab();
    expect(await screen.findByText("Connect Composio to see your apps")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Enable Composio" })).toBeTruthy();
  });

  it("Test key posts the pasted value and renders the name and app count", async () => {
    const calls = stubApi((call) =>
      call.url.endsWith("/tool-providers/composio/key/test") ? { status: 200, body: keyTestOk() } : undefined,
    );
    renderTab();
    fireEvent.click(await screen.findByRole("button", { name: "Enable Composio" }));
    const dialog = await screen.findByRole("dialog", { name: "Enable Composio" });
    fireEvent.change(within(dialog).getByLabelText("Composio API key"), { target: { value: "sk_live_abc123" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Test key" }));

    await waitFor(() =>
      expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/key/test") && bodyField(c, ["api_key"]) === "sk_live_abc123")).toBe(true),
    );
    expect(await within(dialog).findByText("Connected to Acme Workspace — 512 apps available")).toBeTruthy();
  });

  it("renders the vendor's error on a failed test and never sends the key to any other route", async () => {
    const calls = stubApi((call) =>
      call.url.endsWith("/key/test") ? { status: 200, body: keyTestFailed("Incorrect API key provided") } : undefined,
    );
    renderTab();
    fireEvent.click(await screen.findByRole("button", { name: "Enable Composio" }));
    const dialog = await screen.findByRole("dialog", { name: "Enable Composio" });
    fireEvent.change(within(dialog).getByLabelText("Composio API key"), { target: { value: "sk_bad" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Test key" }));

    expect(await within(dialog).findByText("Incorrect API key provided")).toBeTruthy();
    // Save stays disabled: no credential was ever created from a failing test.
    expect(isDisabled(within(dialog).getByRole("button", { name: "Save key" }))).toBe(true);
    expect(calls.some((c) => c.url.includes("/credentials") && c.method === "POST")).toBe(false);
  });

  it("gates Save on a passing test, unless the override is ticked", async () => {
    stubApi((call) => (call.url.endsWith("/key/test") ? { status: 200, body: keyTestFailed() } : undefined));
    renderTab();
    fireEvent.click(await screen.findByRole("button", { name: "Enable Composio" }));
    const dialog = await screen.findByRole("dialog", { name: "Enable Composio" });
    fireEvent.change(within(dialog).getByLabelText("Composio API key"), { target: { value: "sk_bad" } });

    // No test run yet: Save is disabled.
    expect(isDisabled(within(dialog).getByRole("button", { name: "Save key" }))).toBe(true);

    fireEvent.click(within(dialog).getByRole("button", { name: "Test key" }));
    await within(dialog).findByText(/Test failed|Incorrect API key/);
    expect(isDisabled(within(dialog).getByRole("button", { name: "Save key" }))).toBe(true);

    fireEvent.click(within(dialog).getByRole("checkbox", { name: /Save this key anyway/ }));
    expect(isDisabled(within(dialog).getByRole("button", { name: "Save key" }))).toBe(false);
  });

  it("after Save the gallery renders in place, no navigation", async () => {
    let enabled = false;
    const calls = stubApi((call) => {
      if (call.url.endsWith("/key/test")) return { status: 200, body: keyTestOk() };
      if (call.url.endsWith("/tool-providers/composio/status")) {
        return { status: 200, body: enabled ? appsStatusFixture() : STATUS_NOT_SET_UP };
      }
      if (call.url.endsWith("/tool-providers/composio/enable") && call.method === "POST") {
        enabled = true;
        return { status: 200, body: appsStatusFixture() };
      }
      return undefined;
    });
    renderTab();
    fireEvent.click(await screen.findByRole("button", { name: "Enable Composio" }));
    const dialog = await screen.findByRole("dialog", { name: "Enable Composio" });
    fireEvent.change(within(dialog).getByLabelText("Composio API key"), { target: { value: "sk_live_abc123" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Test key" }));
    await within(dialog).findByText(/Connected to/);
    fireEvent.click(within(dialog).getByRole("button", { name: "Save key" }));

    await waitFor(() => expect(screen.getByRole("heading", { name: "Key saved" })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "Done" }));

    // The gallery renders in the same tab body — no route change.
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(await screen.findByText("Valid")).toBeTruthy();
    expect(calls.some((c) => c.url.includes("/credentials") && c.method === "POST" && bodyField(c, ["provider_id"]) === "composio")).toBe(true);
    expect(calls.some((c) => c.url.endsWith("/tool-providers/composio/enable") && c.method === "POST")).toBe(true);
  });
});

describe("AppsTab — the enabled header", () => {
  it("reads the status fixture's chip and last-tested time", async () => {
    stubApi((call) => (call.url.endsWith("/tool-providers/composio/status") ? { status: 200, body: appsStatusFixture() } : undefined));
    renderTab();
    expect(await screen.findByText("Valid")).toBeTruthy();
    expect(screen.getByText("1 connected · 0 paused")).toBeTruthy();
  });

  it("Validate posts the stored-credential test", async () => {
    const calls = stubApi((call) =>
      call.url.endsWith("/tool-providers/composio/status") ? { status: 200, body: appsStatusFixture() } : undefined,
    );
    renderTab();
    await clickWhenEnabled(await screen.findByRole("button", { name: "Validate" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/credentials/cred_composio/test"))).toBe(true));
  });

  it("Rotate posts to the same credential id", async () => {
    const calls = stubApi((call) => {
      if (call.url.endsWith("/tool-providers/composio/status")) return { status: 200, body: appsStatusFixture() };
      if (call.url.endsWith("/key/test")) return { status: 200, body: keyTestOk() };
      return undefined;
    });
    renderTab();
    await clickWhenEnabled(await screen.findByRole("button", { name: "Rotate" }));
    const dialog = await screen.findByRole("dialog", { name: "Rotate the Composio key" });
    fireEvent.change(within(dialog).getByLabelText("Composio API key"), { target: { value: "sk_new_key" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Test key" }));
    await within(dialog).findByText(/Connected to/);
    fireEvent.click(within(dialog).getByRole("button", { name: "Replace key" }));

    await waitFor(() =>
      expect(
        calls.some((c) => c.method === "PUT" && c.url.endsWith("/credentials/cred_composio") && bodyField(c, ["secrets", "api_key"]) === "sk_new_key"),
      ).toBe(true),
    );
  });

  it("Disable confirms, then posts disable", async () => {
    const calls = stubApi((call) => {
      if (call.url.endsWith("/tool-providers/composio/status")) return { status: 200, body: appsStatusFixture() };
      return undefined;
    });
    renderTab();
    await clickWhenEnabled(await screen.findByRole("button", { name: "Disable" }));
    const dialog = await screen.findByRole("dialog", { name: "Turn off Apps?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Turn off" }));

    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/tool-providers/composio/disable"))).toBe(true));
  });

  it("shows a plain 'turned off' prompt and posts enable, no key dialog", async () => {
    const calls = stubApi((call) => (call.url.endsWith("/tool-providers/composio/status") ? { status: 200, body: STATUS_DISABLED } : undefined));
    renderTab();
    expect(await screen.findByText("Apps are turned off")).toBeTruthy();
    await clickWhenEnabled(screen.getByRole("button", { name: "Turn on" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/tool-providers/composio/enable"))).toBe(true));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("links to Console -> Keys", async () => {
    stubApi((call) => (call.url.endsWith("/tool-providers/composio/status") ? { status: 200, body: appsStatusFixture() } : undefined));
    renderTab();
    const link = await screen.findByRole("link", { name: "Also in Keys" });
    expect(link.getAttribute("href")).toBe("/console/keys");
  });
});
