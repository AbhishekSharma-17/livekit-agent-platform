import { readFileSync } from "node:fs";
import path from "node:path";

import * as React from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import {
  DEV_VIEW_AS_KEY,
  devViewAsAllowed,
  setDevViewAs,
  viewAsRole,
} from "@/components/console/lib/dev-view-as";
import { AccountMenu } from "@/components/console/shell/account-menu";
import { DevViewAsBadge } from "@/components/console/shell/dev-view-as";
import { IfCan } from "@/components/console/shared/permission";
import { useActiveWorkspace } from "@/components/console/settings/use-settings-queries";

/**
 * Decision O6: a development-only "view as" role switch, so the builder and
 * viewer views can be render-checked while the dev server runs with the
 * admin bypass. UI only: it lowers the role the console renders for and
 * never touches what is sent to the server.
 */

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

beforeAll(() => {
  const matches = Element.prototype.matches;
  Element.prototype.matches = function (this: Element, selector: string) {
    if (selector === ":popover-open" || selector === ":modal") return false;
    return matches.call(this, selector);
  };
});

const OWNER_WORKSPACE = { id: "ws1", name: "Acme", slug: "acme", role: "owner" as const };
const BREAK_GLASS = {
  user: { id: "break-glass", email: "break-glass@lkap.local", name: "Break-glass admin", is_platform_admin: true },
  workspaces: [OWNER_WORKSPACE],
};
const ADA = { user: { id: "u1", email: "ada@example.com", name: "Ada Lovelace" }, workspaces: [OWNER_WORKSPACE] };

function stubMe(me: unknown) {
  const calls: { url: string; method: string; headers?: HeadersInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url: String(url), method: init?.method ?? "GET", headers: init?.headers });
      if (String(url).includes("auth/me")) return new Response(JSON.stringify(me), { status: 200, headers: { "Content-Type": "application/json" } });
      return new Response(JSON.stringify({ items: [], total: 0 }), { status: 200, headers: { "Content-Type": "application/json" } });
    }),
  );
  return calls;
}

function withClient(node: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

function RoleProbe() {
  const { workspace } = useActiveWorkspace();
  return <p data-testid="role">{workspace?.role ?? "none"}</p>;
}

/** Node ≥ 22 ships an inert global `localStorage` that shadows jsdom's (as in console-agents-list.test.tsx). */
function memoryStorage(): Storage {
  const data = new Map<string, string>();
  return {
    get length() {
      return data.size;
    },
    clear: () => data.clear(),
    getItem: (key) => data.get(key) ?? null,
    key: (index) => [...data.keys()][index] ?? null,
    removeItem: (key) => void data.delete(key),
    setItem: (key, value) => void data.set(key, String(value)),
  };
}

beforeEach(() => {
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
  vi.stubGlobal("localStorage", memoryStorage());
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("viewAsRole", () => {
  it.each([
    ["owner", "viewer", true, "viewer"],
    ["owner", "builder", true, "builder"],
    ["owner", null, true, "owner"],
    ["builder", "admin", true, "builder"],
    ["owner", "viewer", false, "owner"],
  ] as const)("real %s, choice %s, allowed %s → %s", (real, choice, allowed, shown) => {
    expect(viewAsRole(real, choice, allowed)).toBe(shown);
  });
});

describe("devViewAsAllowed", () => {
  it("is off outside development, whoever is signed in", () => {
    vi.stubEnv("NODE_ENV", "production");
    expect(devViewAsAllowed("break-glass")).toBe(false);
    vi.stubEnv("NODE_ENV", "test");
    expect(devViewAsAllowed("break-glass")).toBe(false);
  });

  it("is on in development only under the admin bypass", () => {
    vi.stubEnv("NODE_ENV", "development");
    expect(devViewAsAllowed("break-glass")).toBe(true);
    expect(devViewAsAllowed("u1")).toBe(false);
    expect(devViewAsAllowed(undefined)).toBe(false);
  });

  it("does not even save a choice outside development", () => {
    vi.stubEnv("NODE_ENV", "production");
    setDevViewAs("viewer");
    expect(window.localStorage.getItem(DEV_VIEW_AS_KEY)).toBeNull();
  });
});

describe("the role the console renders for (useActiveWorkspace)", () => {
  it("is lowered to the stored choice in development under the admin bypass", async () => {
    vi.stubEnv("NODE_ENV", "development");
    window.localStorage.setItem(DEV_VIEW_AS_KEY, "viewer");
    stubMe(BREAK_GLASS);
    withClient(<RoleProbe />);
    await waitFor(() => expect(screen.getByTestId("role").textContent).toBe("viewer"));
  });

  it("follows a change made in this tab", async () => {
    vi.stubEnv("NODE_ENV", "development");
    stubMe(BREAK_GLASS);
    withClient(<RoleProbe />);
    await waitFor(() => expect(screen.getByTestId("role").textContent).toBe("owner"));
    act(() => setDevViewAs("builder"));
    expect(screen.getByTestId("role").textContent).toBe("builder");
    act(() => setDevViewAs(null));
    expect(screen.getByTestId("role").textContent).toBe("owner");
  });

  it("ignores a stored choice for a real signed-in person", async () => {
    vi.stubEnv("NODE_ENV", "development");
    window.localStorage.setItem(DEV_VIEW_AS_KEY, "viewer");
    stubMe(ADA);
    withClient(<RoleProbe />);
    await waitFor(() => expect(screen.getByTestId("role").textContent).toBe("owner"));
  });

  it("ignores it outside development", async () => {
    vi.stubEnv("NODE_ENV", "production");
    window.localStorage.setItem(DEV_VIEW_AS_KEY, "viewer");
    stubMe(BREAK_GLASS);
    withClient(<RoleProbe />);
    await waitFor(() => expect(screen.getByTestId("role").textContent).toBe("owner"));
  });

  it("drives the permission pattern, without changing any request", async () => {
    vi.stubEnv("NODE_ENV", "development");
    window.localStorage.setItem(DEV_VIEW_AS_KEY, "viewer");
    const calls = stubMe(BREAK_GLASS);
    withClient(
      <IfCan min="builder" fallback={<p>Read only</p>}>
        <button type="button">Edit</button>
      </IfCan>,
    );
    expect(await screen.findByText("Read only")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
    // Nothing role-shaped goes to the server: the only request is the plain `auth/me` read.
    expect(calls.map((call) => `${call.method} ${call.url}`)).toEqual([expect.stringMatching(/^GET .*auth\/me$/)]);
  });
});

describe("the account menu's Development group", () => {
  it("offers View as in development under the admin bypass, and saves the choice per browser", async () => {
    vi.stubEnv("NODE_ENV", "development");
    stubMe(BREAK_GLASS);
    withClient(<AccountMenu />);
    fireEvent.click(await screen.findByTestId("account-menu-trigger"));
    const group = await screen.findByRole("group", { name: "Development" });
    expect(within(group).getByText("Changes what the console shows, not what the server allows.")).toBeTruthy();
    const select = within(group).getByRole("combobox", { name: "View the console as" });
    expect(select.textContent).toContain("Owner (your role)");

    act(() => setDevViewAs("viewer"));
    expect(window.localStorage.getItem(DEV_VIEW_AS_KEY)).toBe("viewer");
    await waitFor(() => expect(within(group).getByRole("combobox", { name: "View the console as" }).textContent).toContain("Viewer"));
  });

  it("is absent outside development and for a real signed-in person", async () => {
    vi.stubEnv("NODE_ENV", "production");
    stubMe(BREAK_GLASS);
    const { unmount } = withClient(<AccountMenu />);
    fireEvent.click(await screen.findByTestId("account-menu-trigger"));
    await screen.findByText(/break-glass admin token/);
    expect(screen.queryByRole("group", { name: "Development" })).toBeNull();
    unmount();

    vi.stubEnv("NODE_ENV", "development");
    stubMe(ADA);
    withClient(<AccountMenu />);
    fireEvent.click(await screen.findByTestId("account-menu-trigger"));
    await screen.findByRole("button", { name: "Sign out" });
    expect(screen.queryByRole("group", { name: "Development" })).toBeNull();
  });
});

describe("the render check", () => {
  it("sets the same storage key the switch reads, and runs console routes as owner, builder and viewer", () => {
    const spec = readFileSync(path.resolve(__dirname, "../e2e/render-check.spec.ts"), "utf8");
    expect(spec.match(/VIEW_AS_STORAGE_KEY = "([^"]+)"/)?.[1]).toBe(DEV_VIEW_AS_KEY);
    expect(spec).toContain('const ROLES = ["owner", "builder", "viewer"] as const;');
  });
});

describe("the top bar reminder", () => {
  it("names the role while it is lowered, and hides at the real role", async () => {
    vi.stubEnv("NODE_ENV", "development");
    window.localStorage.setItem(DEV_VIEW_AS_KEY, "builder");
    stubMe(BREAK_GLASS);
    const { container } = withClient(<DevViewAsBadge />);
    await waitFor(() => expect(container.textContent).toBe("Viewing as builder (development)"));
    expect(container.querySelector('[data-slot="dev-view-as-badge"]')?.getAttribute("data-role")).toBe("builder");
    act(() => setDevViewAs(null));
    expect(container.textContent).toBe("");
  });
});
