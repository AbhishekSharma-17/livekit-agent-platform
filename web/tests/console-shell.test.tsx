import * as React from "react";

import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { ThemeProvider } from "@/components/console/shell/theme-provider";
import { ConsoleShell } from "@/components/console/shell/console-shell";
import { HideBottomTabBar } from "@/components/console/shell/bottom-tab-bar";
import { NAV_GROUPS, NAV_ITEMS, formatNavCount, tabBarItems } from "@/components/console/shell/nav-config";

// jsdom has no matchMedia (next-themes and `use-mobile` subscribe to it) and
// no ResizeObserver (Radix popper positioning) — same pattern as
// tests/console-agents-list.test.tsx / theme-provider.test.tsx.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

function stubMatchMedia() {
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  );
}

function stubLocalStorage(): Storage {
  const data = new Map<string, string>();
  const storage: Storage = {
    get length() {
      return data.size;
    },
    clear: () => data.clear(),
    getItem: (key) => data.get(key) ?? null,
    key: (index) => Array.from(data.keys())[index] ?? null,
    removeItem: (key) => {
      data.delete(key);
    },
    setItem: (key, value) => {
      data.set(key, String(value));
    },
  };
  vi.stubGlobal("localStorage", storage);
  return storage;
}

let pathname = "/console";

vi.mock("next/navigation", () => ({
  usePathname: () => pathname,
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

interface FetchOptions {
  /** `auth/me` body; omitted = 404 (the admin-token mode, auth not wired up). */
  me?: unknown;
  /** How many sessions `sessions?status=active` returns. */
  liveSessions?: number;
}

function json(status: number, body: unknown): Response {
  return { ok: status < 400, status, json: async () => body } as Response;
}

function stubFetch({ me, liveSessions = 0 }: FetchOptions = {}) {
  const fetchMock = vi.fn<(url: string) => Promise<Response>>(async (url) => {
    if (url.includes("/api/console/health")) {
      return json(200, { ok: true, version: "2.0.0", livekit_url: "wss://x.livekit.cloud", packs: [], db: "ok" });
    }
    if (url.includes("/api/console/auth/me") && me !== undefined) return json(200, me);
    if (url.includes("/api/console/sessions") && url.includes("status=active")) {
      const items = Array.from({ length: liveSessions }, (_, i) => ({ id: `s${i}`, status: "active" }));
      return json(200, { items });
    }
    // auth/me, connections, webhooks: not built yet in this wave (404).
    return json(404, { error: { code: "not_found", message: "not found" } });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderShell(children: React.ReactNode = <div>Page content</div>) {
  return render(
    <ThemeProvider>
      <ConsoleShell>{children}</ConsoleShell>
    </ThemeProvider>,
  );
}

function setViewportWidth(width: number) {
  Object.defineProperty(window, "innerWidth", { configurable: true, value: width });
}

const ORIGINAL_WIDTH = window.innerWidth;

(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;

beforeAll(() => {
  // Radix layers in jsdom (editor README "Testing notes"): jsdom throws on
  // these selectors and Radix retries in a slow path without this.
  const matches = Element.prototype.matches;
  Element.prototype.matches = function (this: Element, selector: string) {
    if (selector === ":popover-open" || selector === ":modal") return false;
    return matches.call(this, selector);
  };
});

beforeEach(() => {
  pathname = "/console";
  setViewportWidth(1440);
  stubMatchMedia();
  stubLocalStorage();
  stubFetch();
  document.cookie = "";
});

afterEach(() => {
  setViewportWidth(ORIGINAL_WIDTH);
  vi.unstubAllGlobals();
});

function desktopSidebar(): HTMLElement {
  const el = document.querySelector('[data-slot="sidebar"] [data-slot="sidebar-inner"]');
  expect(el).not.toBeNull();
  return el as HTMLElement;
}

function tabBar(): HTMLElement | null {
  return document.querySelector('[data-slot="bottom-tab-bar"]');
}

describe("ConsoleShell", () => {
  it("renders every configured nav item once, grouped", async () => {
    renderShell();
    for (const item of NAV_ITEMS) {
      expect(await screen.findAllByRole("link", { name: item.label })).not.toHaveLength(0);
    }
    const sidebar = desktopSidebar();
    for (const item of NAV_ITEMS) {
      expect(within(sidebar).getAllByRole("link", { name: item.label })).toHaveLength(1);
    }
  });

  it("groups the nav under Build, Connect, Observe and Settings labels", () => {
    renderShell();
    const sidebar = desktopSidebar();
    for (const label of ["Build", "Connect", "Observe", "Settings"]) {
      const group = NAV_GROUPS.find((g) => g.label === label);
      expect(group).toBeDefined();
      const list = within(sidebar).getByRole("list", { name: label });
      for (const item of group!.items.filter((i) => !i.hidden)) {
        expect(within(list).getByRole("link", { name: item.label })).toBeTruthy();
      }
    }
  });

  it("marks the item matching the current route active", async () => {
    pathname = "/console/agents";
    renderShell();
    // The top bar's breadcrumb fallback also reads "Agents" for this route
    // (`navLabelForPath`) and is a `role="link"` `BreadcrumbPage`; scope to
    // the sidebar itself to find the nav item, not the breadcrumb.
    const sidebarInner = await waitFor(() => desktopSidebar());
    const link = within(sidebarInner).getByRole("link", { name: "Agents" });
    expect(link.getAttribute("data-active")).toBe("true");
    expect(link.getAttribute("aria-current")).toBe("page");

    const overviewLink = within(sidebarInner).getByRole("link", { name: "Overview" });
    expect(overviewLink.getAttribute("data-active")).toBe("false");
    expect(overviewLink.getAttribute("aria-current")).toBeNull();
  });

  it("shows the active location in the breadcrumb and the tab bar too", () => {
    pathname = "/console/sessions";
    renderShell();
    const crumb = within(screen.getByRole("navigation", { name: "breadcrumb" })).getByText("Sessions");
    expect(crumb.getAttribute("aria-current")).toBe("page");
    const tab = within(tabBar()!).getByRole("link", { name: "Sessions" });
    expect(tab.getAttribute("aria-current")).toBe("page");
  });

  it("renders the skip-to-content link first and it targets #main-content", () => {
    renderShell();
    const skip = screen.getByText("Skip to content");
    expect(skip.getAttribute("href")).toBe("#main-content");
    const focusables = document.querySelectorAll<HTMLElement>("a[href], button, [tabindex]:not([tabindex='-1'])");
    expect(focusables[0]).toBe(skip);
    const main = screen.getByRole("main");
    expect(main.id).toBe("main-content");
    expect(main.getAttribute("tabindex")).toBe("-1");
  });

  it("puts the page inside the main panel under a 56 px sticky top bar", () => {
    renderShell(<div>Hello from a page</div>);
    const panel = document.querySelector('[data-slot="main-panel"]') as HTMLElement;
    expect(panel).not.toBeNull();
    const topBar = within(panel).getByRole("banner");
    expect(topBar.className).toContain("sticky");
    expect(topBar.className).toContain("h-topbar");
    expect(within(panel).getByRole("main").textContent).toContain("Hello from a page");
  });

  it("gives pages without `Page` the default container, and leaves `Page` to size itself", () => {
    renderShell(<section data-slot="page">A page</section>);
    const frame = document.querySelector('[data-slot="page-frame"]') as HTMLElement;
    // The container applies only while no `[data-slot=page]` is inside.
    expect(frame.className).toContain("[&:not(:has([data-slot=page]))]:max-w-[1200px]");
    expect(frame.matches(":has([data-slot=page])")).toBe(true);
  });

  /**
   * The theme switcher lives in the account menu (spec 7.1). With `auth/me`
   * unavailable (admin-token mode) the menu is "Preferences", so appearance
   * stays reachable. Switching itself (persists under `lkap-theme`, updates
   * `<html>`) is covered by tests/theme-provider.test.tsx.
   */
  it("keeps the theme switcher reachable from the sidebar foot, defaulting to System", async () => {
    renderShell();
    const trigger = await within(desktopSidebar()).findByRole("button", { name: "Preferences" });
    fireEvent.click(trigger);
    const switcher = await screen.findByRole("radiogroup", { name: "Theme" });
    const system = within(switcher).getByRole("radio", { name: "System" });
    // No stored preference: the default is the system setting (UI-1, decision D2).
    await waitFor(() => expect(system.getAttribute("aria-checked")).toBe("true"));
    expect(within(switcher).getByRole("radio", { name: "Light" })).toBeTruthy();
    expect(within(switcher).getByRole("radio", { name: "Dark" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Theme" })).toBeNull();
  });

  it("renders no workspace switcher while auth/me 404s (not built yet)", async () => {
    renderShell();
    await waitFor(() => expect(screen.queryByText(/loading/i)).toBeNull());
    expect(screen.queryByLabelText(/^Workspace:/)).toBeNull();
  });

  it("names the workspace as the breadcrumb's context once auth/me knows it", async () => {
    stubFetch({
      me: { user: { id: "u1", email: "ada@example.com", name: "Ada" }, workspaces: [{ id: "w1", name: "Acme", role: "admin", slug: "acme" }] },
    });
    pathname = "/console/agents";
    renderShell();
    const breadcrumb = screen.getByRole("navigation", { name: "breadcrumb" });
    const context = await within(breadcrumb).findByRole("link", { name: "Acme" });
    expect(context.getAttribute("href")).toBe("/console");
    expect(within(breadcrumb).getByText("Agents").getAttribute("aria-current")).toBe("page");
  });

  it("shows the docs link only when NEXT_PUBLIC_DOCS_URL is set", async () => {
    const original = process.env.NEXT_PUBLIC_DOCS_URL;
    process.env.NEXT_PUBLIC_DOCS_URL = "https://docs.example.com";
    try {
      renderShell();
      const link = await within(desktopSidebar()).findByRole("link", { name: "Documentation" });
      expect(link.getAttribute("href")).toBe("https://docs.example.com");
    } finally {
      process.env.NEXT_PUBLIC_DOCS_URL = original;
    }
  });

  it("has a hamburger that opens the Menu dialog and gets focus back when it closes", async () => {
    // At 820 px and below the sidebar is the full-screen "Menu" dialog
    // (decision D7: no side sheets).
    setViewportWidth(820);
    renderShell();
    const trigger = screen.getByRole("button", { name: "Open menu" });
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(trigger);
    const dialog = await screen.findByRole("dialog", { name: "Menu" });
    expect(within(dialog).getAllByRole("link", { name: "Overview" }).length).toBeGreaterThan(0);
    expect(await within(dialog).findByRole("button", { name: "Preferences" })).toBeTruthy();
    expect(trigger.getAttribute("aria-expanded")).toBe("true");

    fireEvent.keyDown(dialog, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Menu" })).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });

  it("closes the Menu dialog when the viewport widens past 820 px", async () => {
    setViewportWidth(390);
    renderShell();
    fireEvent.click(screen.getByRole("button", { name: "Open menu" }));
    await screen.findByRole("dialog", { name: "Menu" });
    act(() => {
      setViewportWidth(1024);
      window.dispatchEvent(new Event("resize"));
    });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Menu" })).toBeNull());
  });

  it("renders the page content passed as children", () => {
    renderShell(<div>Hello from a page</div>);
    expect(screen.getByText("Hello from a page")).toBeTruthy();
  });
});

describe("phone bottom tab bar", () => {
  it("gives builders Overview, Agents, Sessions, Knowledge and More", async () => {
    renderShell();
    const bar = tabBar();
    expect(bar).not.toBeNull();
    expect(bar!.getAttribute("aria-label")).toBe("Quick navigation");
    const names = within(bar!)
      .getAllByRole("link")
      .map((link) => link.textContent);
    expect(names).toEqual(["Overview", "Agents", "Sessions", "Knowledge"]);
    expect(within(bar!).getByRole("button", { name: "More" })).toBeTruthy();
  });

  it("gives viewers Overview, Sessions, Analytics and More", async () => {
    stubFetch({
      me: { user: { id: "u2", email: "val@example.com", name: "Val" }, workspaces: [{ id: "w1", name: "Acme", role: "viewer", slug: "acme" }] },
    });
    renderShell();
    await waitFor(() =>
      expect(
        within(tabBar()!)
          .getAllByRole("link")
          .map((link) => link.textContent),
      ).toEqual(["Overview", "Sessions", "Analytics"]),
    );
    expect(within(tabBar()!).getByRole("button", { name: "More" })).toBeTruthy();
  });

  it("caps the live-session badge at 9+ and pads the page by the bar's height", async () => {
    stubFetch({ liveSessions: 12 });
    renderShell();
    const badge = await waitFor(() => {
      const el = tabBar()!.querySelector('[data-slot="tab-badge"]');
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    expect(badge.textContent).toBe("9+");
    expect(within(tabBar()!).getByRole("link", { name: /^Sessions\s*,\s*12 live$/ })).toBeTruthy();
    expect(screen.getByRole("main").className).toContain("max-[640px]:pb-[calc(var(--layout-bottombar)+env(safe-area-inset-bottom))]");
    expect(document.querySelector('[data-slot="console-shell"]')?.hasAttribute("data-tab-bar")).toBe(true);
  });

  it("opens the Menu dialog from More and returns focus to it", async () => {
    setViewportWidth(390);
    renderShell();
    const more = within(tabBar()!).getByRole("button", { name: "More" });
    fireEvent.click(more);
    const dialog = await screen.findByRole("dialog", { name: "Menu" });
    fireEvent.keyDown(dialog, { key: "Escape" });
    await waitFor(() => expect(document.activeElement).toBe(more));
  });

  it("is not rendered on a full-screen task page", () => {
    renderShell(
      <>
        <HideBottomTabBar />
        <div>Task page</div>
      </>,
    );
    expect(tabBar()).toBeNull();
    expect(screen.getByRole("main").className).not.toContain("--layout-bottombar");
    expect(document.querySelector('[data-slot="console-shell"]')?.hasAttribute("data-tab-bar")).toBe(false);
  });
});

describe("nav helpers", () => {
  it.each([
    [undefined, undefined],
    [0, undefined],
    [3, "3"],
    [9, "9"],
    [10, "9+"],
    [250, "9+"],
  ])("formatNavCount(%s) → %s", (count, expected) => {
    expect(formatNavCount(count)).toBe(expected);
  });

  it("keeps each tab bar set to at most five destinations including More", () => {
    expect(tabBarItems("builder").length + 1).toBeLessThanOrEqual(5);
    expect(tabBarItems("viewer").length + 1).toBeLessThanOrEqual(5);
  });
});
