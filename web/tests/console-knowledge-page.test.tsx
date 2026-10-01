import * as React from "react";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { NAV_ITEMS, navLabelForPath } from "@/components/console/shell/nav-config";
import { MOVED_SETTINGS_TABS, movedSettingsTab } from "@/components/console/settings/moved-tabs";
import { KnowledgePageTabs, knowledgeTabFor, knowledgeTabHref } from "@/components/console/knowledge/knowledge-page-tabs";

/**
 * UI-R1: knowledge connections moved from Settings to a Connections tab on
 * the Knowledge page (`/console/knowledge?tab=connections`). The knowledge
 * base list stays the default, and the old Settings link redirects.
 */

const routerReplace = vi.fn();
let search = new URLSearchParams();
const redirect = vi.fn((url: string) => {
  throw new Error(`NEXT_REDIRECT ${url}`);
});
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: routerReplace, push: vi.fn() }),
  useSearchParams: () => search,
  usePathname: () => "/console/knowledge",
  redirect: (url: string) => redirect(url),
}));

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

beforeEach(() => {
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
  routerReplace.mockClear();
  redirect.mockClear();
});
afterEach(() => vi.unstubAllGlobals());

function stubApi(role: string) {
  const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input).split("?")[0];
      if (url.endsWith("/auth/me")) return json({ user: { id: "u1", email: "a@example.test" }, workspaces: [{ id: "ws1", name: "WS", slug: "ws", role }] });
      if (url.endsWith("/providers")) return json({ providers: [] });
      if (url.endsWith("/knowledge-connections")) return json({ items: [], total: 0 });
      if (url.endsWith("/knowledge-bases")) return json({ items: [], total: 0 });
      return json({ items: [], total: 0 });
    }),
  );
}

function renderPage(tab: string | null, role = "admin") {
  search = new URLSearchParams(tab ? { tab } : {});
  stubApi(role);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <KnowledgePageTabs />
    </QueryClientProvider>,
  );
}

describe("knowledge page tabs", () => {
  it.each([
    [null, "bases"],
    ["bases", "bases"],
    ["anything", "bases"],
    ["connections", "connections"],
  ] as const)("?tab=%s opens %s", (requested, tab) => {
    expect(knowledgeTabFor(requested)).toBe(tab);
  });

  it("keeps the bare path for the list and ?tab=connections for connections", () => {
    expect(knowledgeTabHref("bases")).toBe("/console/knowledge");
    expect(knowledgeTabHref("connections")).toBe("/console/knowledge?tab=connections");
  });

  it("opens on the knowledge base list by default", async () => {
    renderPage(null);
    expect(screen.getByRole("tab", { name: "Knowledge bases" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("tab", { name: "Connections" }).getAttribute("aria-selected")).toBe("false");
    expect(screen.queryByRole("button", { name: "Add connection" })).toBeNull();
  });

  it("opens Connections from ?tab=connections, with Add connection as the page's primary for admins", async () => {
    renderPage("connections");
    expect(screen.getByRole("tab", { name: "Connections" }).getAttribute("aria-selected")).toBe("true");
    expect(await screen.findByText("No knowledge connections yet")).toBeTruthy();
    const header = document.querySelector("header") ?? document.body;
    const add = await within(header as HTMLElement).findByRole("button", { name: "Add connection" });
    expect(add.getAttribute("data-variant")).toBe("primary");
    // One Add connection only: the card itself carries none on this page.
    expect(screen.getAllByRole("button", { name: "Add connection" })).toHaveLength(1);
  });

  it("gives builders a read-only note instead of Add connection", async () => {
    renderPage("connections", "builder");
    expect(await screen.findByText("No knowledge connections yet")).toBeTruthy();
    await waitFor(() => expect(screen.getByText(/Ask an admin to add or change connections/)).toBeTruthy());
    expect(screen.queryByRole("button", { name: "Add connection" })).toBeNull();
  });

  it("switches tabs through the URL", () => {
    renderPage(null);
    const connections = screen.getByRole("tab", { name: "Connections" });
    fireEvent.mouseDown(connections);
    fireEvent.click(connections);
    expect(routerReplace).toHaveBeenCalledWith("/console/knowledge?tab=connections", { scroll: false });
  });

  it("is still the Knowledge nav item and breadcrumb", () => {
    expect(navLabelForPath("/console/knowledge")).toBe("Knowledge");
    expect(NAV_ITEMS.every((item) => !item.href.includes("knowledge-connections"))).toBe(true);
  });
});

describe("the old Settings link", () => {
  it("maps ?tab=knowledge-connections to the Connections tab and nothing else", () => {
    expect(movedSettingsTab("knowledge-connections")).toBe("/console/knowledge?tab=connections");
    expect(movedSettingsTab("webhooks")).toBeNull();
    expect(movedSettingsTab(undefined)).toBeNull();
    expect(movedSettingsTab(["knowledge-connections"])).toBeNull();
    expect(Object.keys(MOVED_SETTINGS_TABS)).toEqual(["knowledge-connections"]);
  });

  it("redirects /console/settings?tab=knowledge-connections, and renders Settings for other tabs", async () => {
    const { default: ConsoleSettingsPage } = await import("@/app/console/settings/page");
    await expect(ConsoleSettingsPage({ searchParams: Promise.resolve({ tab: "knowledge-connections" }) })).rejects.toThrow(
      "NEXT_REDIRECT /console/knowledge?tab=connections",
    );
    expect(redirect).toHaveBeenCalledWith("/console/knowledge?tab=connections");

    redirect.mockClear();
    const page = await ConsoleSettingsPage({ searchParams: Promise.resolve({ tab: "webhooks" }) });
    expect(redirect).not.toHaveBeenCalled();
    expect(React.isValidElement(page)).toBe(true);
  });
});
