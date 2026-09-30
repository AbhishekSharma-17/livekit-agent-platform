import * as React from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ConnectionDetail } from "@/components/console/connections/connection-detail";
import { FleetCard } from "@/components/console/connections/fleet-card";
import type { ConnectionOut, FleetStatus } from "@/contracts/lkap-contracts";

/**
 * `/console/connections/[id]` as the Detail / record archetype (docs/ui/DESIGN-SYSTEM.md
 * section 7.4, S2): the header's one primary for admins and a read-only note for
 * everyone else (D12), the Danger zone's typed confirmation, and the Fleet tab's
 * layout-mirroring loading state and confirmed Stop.
 */

const routerPush = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: routerPush }),
  usePathname: () => "/console/connections/conn-1",
  useSearchParams: () => new URLSearchParams(),
}));

const CONNECTION: ConnectionOut = {
  id: "conn-1",
  slug: "staging",
  name: "Staging",
  url: "wss://example.livekit.cloud",
  agent_name: "lkap-agent",
  deployment_type: "cloud",
  deployment_mode: "supervised",
  is_default: false,
  status: "ok",
  replicas: 1,
};

const FLEET: FleetStatus = {
  desired_replicas: 1,
  ready_workers: 1,
  shared_agent_name_workers: 0,
  instances: [{ instance_key: "worker-a", status: "ready", managed_by: "supervisor", image: "slim" }],
};

function me(role: string) {
  return {
    user: { id: "u1", email: "person@example.test" },
    workspaces: [{ id: "ws1", name: "Test workspace", slug: "test", role }],
  };
}

function stub(role: string, route?: (url: string, init?: RequestInit) => unknown) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const body =
      route?.(url, init) ??
      (url.includes("/auth/me") ? me(role) : url.includes("/fleet") ? FLEET : url.includes("/connections/conn-1") ? CONNECTION : {});
    return { ok: true, status: 200, statusText: "", json: async () => body, text: async () => JSON.stringify(body) } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  const data = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    get length() {
      return data.size;
    },
    clear: () => data.clear(),
    getItem: (key: string) => data.get(key) ?? null,
    key: (index: number) => Array.from(data.keys())[index] ?? null,
    removeItem: (key: string) => void data.delete(key),
    setItem: (key: string, value: string) => void data.set(key, String(value)),
  } satisfies Storage);
});

afterEach(() => {
  vi.unstubAllGlobals();
  routerPush.mockClear();
});

describe("ConnectionDetail", () => {
  it("shows a back link, the status as a word and Test connection as the one primary for an admin", async () => {
    stub("admin");
    renderWithClient(<ConnectionDetail connectionId="conn-1" />);
    expect(await screen.findByRole("heading", { level: 1, name: "Staging" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Back to connections" }).getAttribute("href")).toBe("/console/connections");
    const header = screen.getByRole("banner");
    expect(within(header).getByText("Working")).toBeTruthy();
    const test = await within(header).findByRole("button", { name: "Test connection" });
    expect(test.getAttribute("data-variant")).toBe("primary");
    expect(within(header).getByRole("button", { name: "Refresh" })).toBeTruthy();
  });

  it("offers a viewer a read-only note instead of the primary, and no Danger zone action", async () => {
    stub("viewer");
    renderWithClient(<ConnectionDetail connectionId="conn-1" />);
    await screen.findByRole("heading", { level: 1, name: "Staging" });
    expect(await screen.findByText("Ask an admin to test or change this connection.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Test connection" })).toBeNull();
    expect(screen.getByText("Ask an admin to delete this connection.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Delete connection" })).toBeNull();
  });

  it("deletes from the Danger zone only after the name is typed, then goes back to the list", async () => {
    const fetchMock = stub("admin");
    renderWithClient(<ConnectionDetail connectionId="conn-1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Delete connection" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Delete “Staging”?" });
    const confirm = within(dialog).getByRole("button", { name: "Delete connection" }) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    fireEvent.change(within(dialog).getByLabelText("Connection name"), { target: { value: "Staging" } });
    expect(confirm.disabled).toBe(false);
    fireEvent.click(confirm);
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([input, init]) => String(input).endsWith("/connections/conn-1") && init?.method === "DELETE")).toBe(true),
    );
    await waitFor(() => expect(routerPush).toHaveBeenCalledWith("/console/connections"));
  });
});

describe("FleetCard", () => {
  it("shows a loading state that mirrors the pool card, then the ready count as a stat", async () => {
    stub("admin");
    renderWithClient(<FleetCard connection={CONNECTION} />);
    expect(screen.getByText("Loading the worker pool")).toBeTruthy();
    expect(await screen.findByText("1/1")).toBeTruthy();
    const table = within(screen.getByRole("table", { name: "Worker instances" }));
    expect(table.getByText("Ready")).toBeTruthy();
  });

  it("asks before stopping the pool", async () => {
    const fetchMock = stub("admin");
    renderWithClient(<FleetCard connection={CONNECTION} />);
    fireEvent.click(await screen.findByRole("button", { name: "Stop" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Stop the worker pool?" });
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
    fireEvent.click(within(dialog).getByRole("button", { name: "Stop pool" }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([input, init]) => String(input).includes("/fleet") && init?.method === "POST" && String(init.body).includes('"stop"')),
      ).toBe(true),
    );
  });
});
