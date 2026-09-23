import * as React from "react";

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ThemeProvider } from "@/components/console/shell/theme-provider";
import { ConsoleShell } from "@/components/console/shell/console-shell";
import { NAV_ITEMS } from "@/components/console/shell/nav-config";

// jsdom has neither; the shadcn `Sidebar` (use-mobile) needs matchMedia and
// Radix's popper positioning (dropdown/tooltip) needs a ResizeObserver —
// same pattern as tests/console-agents-list.test.tsx / theme-provider.test.tsx.
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

function stubFetch() {
  const fetchMock = vi.fn<(url: string) => Promise<Response>>(async (url) => {
    if (url.includes("/api/console/health")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ ok: true, version: "2.0.0", livekit_url: "wss://x.livekit.cloud", packs: [], db: "ok" }),
      } as Response;
    }
    // auth/me, connections, webhooks: not built yet in this wave (404).
    return {
      ok: false,
      status: 404,
      json: async () => ({ error: { code: "not_found", message: "not found" } }),
    } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderShell(children: React.ReactNode = <div>Page content</div>) {
  return render(
    <ThemeProvider>
      <ConsoleShell defaultSidebarOpen>{children}</ConsoleShell>
    </ThemeProvider>,
  );
}

(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;

beforeEach(() => {
  pathname = "/console";
  stubMatchMedia();
  stubLocalStorage();
  stubFetch();
  document.cookie = "";
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ConsoleShell", () => {
  it("renders every configured nav item once, grouped", async () => {
    renderShell();
    for (const item of NAV_ITEMS) {
      expect(await screen.findAllByRole("link", { name: item.label })).not.toHaveLength(0);
    }
  });

  it("marks the item matching the current route active", async () => {
    pathname = "/console/agents";
    renderShell();
    // The top bar's breadcrumb fallback also reads "Agents" for this route
    // (`navLabelForPath`) and is a `role="link"` `BreadcrumbPage`; scope to
    // the sidebar itself to find the nav item, not the breadcrumb.
    const sidebarInner = await waitFor(() => {
      const el = document.querySelector('[data-slot="sidebar-inner"]');
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    const link = within(sidebarInner).getByRole("link", { name: "Agents" });
    const button = link.closest("[data-sidebar='menu-button']");
    expect(button).not.toBeNull();
    expect(button?.getAttribute("data-active")).toBe("true");

    const overviewLink = within(sidebarInner).getByRole("link", { name: "Overview" });
    const overviewButton = overviewLink.closest("[data-sidebar='menu-button']");
    expect(overviewButton?.getAttribute("data-active")).toBe("false");
  });

  it("renders the skip-to-content link first and it targets #console-main", () => {
    renderShell();
    const skip = screen.getByText("Skip to content");
    expect(skip.getAttribute("href")).toBe("#console-main");
    expect(document.getElementById("console-main")).not.toBeNull();
  });

  /**
   * Opening the Radix `DropdownMenu` itself (its trigger fires on
   * `pointerdown`, which jsdom has no polyfill for and which reliably hangs
   * the test process — see the identical note in
   * tests/console-agents-list.test.tsx) is not exercised here. The switching
   * behaviour itself (persists under `lkap-theme`, updates `<html>`) is
   * covered by tests/theme-provider.test.tsx; this only checks the trigger
   * the shell renders shows the live preference.
   */
  it("theme menu trigger shows the current preference and is reachable from the sidebar footer", () => {
    renderShell();
    const triggers = screen.getAllByRole("button", { name: "Theme" });
    expect(triggers.length).toBeGreaterThan(0);
    expect(triggers[0].textContent).toContain("Light");
  });

  it("renders no workspace switcher while auth/me 404s (not built yet)", async () => {
    renderShell();
    await waitFor(() => expect(screen.queryByText(/loading/i)).toBeNull());
    expect(screen.queryByLabelText(/^Workspace:/)).toBeNull();
  });

  it("shows the docs link only when NEXT_PUBLIC_DOCS_URL is set", async () => {
    const original = process.env.NEXT_PUBLIC_DOCS_URL;
    process.env.NEXT_PUBLIC_DOCS_URL = "https://docs.example.com";
    try {
      renderShell();
      const link = await screen.findByRole("link", { name: "Documentation" });
      expect(link.getAttribute("href")).toBe("https://docs.example.com");
    } finally {
      process.env.NEXT_PUBLIC_DOCS_URL = original;
    }
  });

  it("has a mobile sidebar trigger that opens the menu dialog", async () => {
    // `use-mobile.ts` reads `window.innerWidth` on mount; force a phone width
    // so the shadcn `Sidebar` renders its mobile branch instead of the
    // desktop rail. Since UI_UX_SPEC-V2-AMENDMENTS §5 (no side drawers) that
    // branch is a modal "Menu" dialog, not a sheet from the left.
    const original = window.innerWidth;
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 375 });
    try {
      renderShell();
      const trigger = screen.getByRole("button", { name: /toggle sidebar/i });
      fireEvent.click(trigger);
      await waitFor(() => {
        expect(within(screen.getByRole("dialog", { name: "Menu" })).getAllByRole("link", { name: "Overview" }).length).toBeGreaterThan(
          0,
        );
      });
    } finally {
      Object.defineProperty(window, "innerWidth", { configurable: true, value: original });
    }
  });

  it("renders the page content passed as children", () => {
    renderShell(<div>Hello from a page</div>);
    expect(screen.getByText("Hello from a page")).toBeTruthy();
  });
});
