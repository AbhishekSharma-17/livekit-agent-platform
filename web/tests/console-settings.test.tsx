import * as React from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ThemeProvider } from "@/components/console/shell/theme-provider";
import { SettingsTabs } from "@/components/console/settings/settings-tabs";
import type { HealthResponse } from "@/contracts/lkap-contracts";

let searchParams = new URLSearchParams();
const routerReplace = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: routerReplace, push: vi.fn() }),
  useSearchParams: () => searchParams,
}));

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

function stubFetch() {
  const health: HealthResponse = {
    ok: true,
    version: "2.1.0",
    livekit_url: "wss://example.livekit.cloud",
    packs: ["generic", "insurance_claim"],
    db: "ok",
  };
  // A minimal, URL-aware stub: the Workspace/Team/API keys/Webhooks tabs each
  // fire their own `GET` on mount (`auth/me`, `workspaces/{id}`,
  // `workspaces/{id}/members`, `api-keys`, `webhooks`), on top of
  // Environment's `/v1/health`. Every one of them answers with an empty-but
  // well-shaped page so each tab reaches its "nothing yet" empty state
  // instead of an error banner; the tab-specific test files exercise real
  // data and mutations.
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const json = async () => {
        if (url.includes("/auth/me")) {
          return {
            user: { id: "u1", email: "owner@local", name: "Owner", is_platform_admin: false },
            workspaces: [{ id: "w1", slug: "default", name: "Default", role: "owner" }],
          };
        }
        if (url.includes("/workspaces/w1/members")) return { items: [], total: 0 };
        if (url.split("?")[0].endsWith("/workspaces")) {
          return {
            items: [
              {
                id: "w1",
                slug: "default",
                name: "Default",
                settings: {},
                role: "owner",
                created_at: "2026-01-01T00:00:00Z",
                updated_at: "2026-01-01T00:00:00Z",
              },
            ],
            total: 1,
          };
        }
        if (url.includes("/api-keys")) return { items: [], total: 0 };
        if (url.includes("/webhooks")) return { items: [], total: 0 };
        return health;
      };
      return { ok: true, status: 200, json } as Response;
    }),
  );
}

function renderSettings() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <ThemeProvider>
      <QueryClientProvider client={client}>
        <SettingsTabs />
      </QueryClientProvider>
    </ThemeProvider>,
  );
}

beforeEach(() => {
  stubLocalStorage();
  stubMatchMedia();
  stubFetch();
});

afterEach(() => {
  vi.unstubAllGlobals();
  searchParams = new URLSearchParams();
  routerReplace.mockClear();
});

describe("SettingsTabs", () => {
  it("defaults to the Workspace tab and lists all six v2 tab ids plus the two this package fills in", () => {
    renderSettings();

    for (const label of ["Workspace", "Appearance", "Team", "API keys", "Webhooks", "Storage", "Environment", "Danger zone"]) {
      expect(screen.getByRole("tab", { name: label })).toBeTruthy();
    }
    expect(screen.getByRole("tab", { name: "Workspace" }).getAttribute("aria-selected")).toBe("true");
  });

  it("renders the real Workspace tab content, not a placeholder", async () => {
    renderSettings();
    expect(await screen.findByDisplayValue("Default")).toBeTruthy();
    expect(screen.getByText("default")).toBeTruthy();
  });

  /**
   * `Tabs` here is a fully controlled, URL-derived component (`value=active`
   * comes from `useSearchParams()`; `onValueChange` calls `router.replace`).
   * The real app relies on Next re-rendering this tree when the URL changes;
   * the mocked router here is a no-op spy, so clicking a trigger can't be
   * used to *observe* a switch in this test (there's nothing that would
   * re-run `useSearchParams`). What it — and Radix's own choice of
   * `mousedown` over `click` for `Tabs.Trigger`, which `fireEvent.click`
   * alone never fires in jsdom — leaves testable is exercised in two parts:
   * mounting with `?tab=` already set (below) proves each tab's content, and
   * `fireEvent.mouseDown` on the trigger still calls `onValueChange`
   * (`router.replace`) even though the controlled value doesn't move.
   */
  it("switches to Appearance and lets the theme radio change the preference", async () => {
    searchParams = new URLSearchParams("tab=appearance");
    renderSettings();

    const darkRadio = await screen.findByRole("radio", { name: /dark/i });
    fireEvent.click(darkRadio);

    await waitFor(() => expect(document.documentElement.classList.contains("dark")).toBe(true));
    expect(localStorage.getItem("lkap-theme")).toBe("dark");
  });

  it("renders read-only health data on the Environment tab", async () => {
    searchParams = new URLSearchParams("tab=environment");
    renderSettings();

    expect(await screen.findByText("2.1.0")).toBeTruthy();
    expect(screen.getByText("example.livekit.cloud")).toBeTruthy();
  });

  it("reads the initial tab from ?tab= and asks the router to navigate on a trigger's activation event", () => {
    searchParams = new URLSearchParams("tab=environment");
    renderSettings();
    expect(screen.getByRole("tab", { name: "Environment" }).getAttribute("aria-selected")).toBe("true");

    fireEvent.mouseDown(screen.getByRole("tab", { name: "Team" }));
    expect(routerReplace).toHaveBeenCalledWith("/console/settings?tab=team", { scroll: false });
  });
});
