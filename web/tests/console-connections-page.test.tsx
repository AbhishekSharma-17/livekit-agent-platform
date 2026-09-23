import * as React from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ConnectionCreateForm } from "@/components/console/connections/connection-create-form";
import { ConnectionsTable } from "@/components/console/connections/connections-table";
import type { ConnectionOut, ConnectionPage } from "@/contracts/lkap-contracts";

/**
 * `/console/connections` list and `/console/connections/new` (V2-13,
 * UI_UX_SPEC-V2-AMENDMENTS §2.1). Covers the card's acceptance line "test
 * button shows capability chips from the mocked response".
 */

const routerReplace = vi.fn();
const routerPush = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: routerReplace, push: routerPush }),
  usePathname: () => "/console/connections",
  useSearchParams: () => new URLSearchParams(),
}));

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

/** Full write access by default: these tests predate role gating (V2-20-5) and assume it. */
const ADMIN_ME = {
  user: { id: "u1", email: "admin@example.test" },
  workspaces: [{ id: "ws1", name: "Test workspace", slug: "test", role: "admin" }],
};

function stubFetch(handler: (url: string, init?: RequestInit) => unknown) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/auth/me")) return { ok: true, status: 200, json: async () => ADMIN_ME } as Response;
    return { ok: true, status: 200, json: async () => handler(url, init) } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
  routerReplace.mockClear();
  routerPush.mockClear();
});

/** The `Switch` (Radix) needs a `ResizeObserver`, absent in jsdom. */
class StubResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
beforeEach(() => {
  vi.stubGlobal("ResizeObserver", StubResizeObserver);
});

const CONNECTION: ConnectionOut = {
  id: "conn-1",
  slug: "cloud-a",
  name: "cloud-a",
  url: "wss://cloud-a.livekit.cloud",
  deployment_type: "cloud",
  deployment_mode: "supervised",
  is_default: true,
  status: "ok",
  replicas: 1,
};

describe("ConnectionsTable", () => {
  it("renders a connection's name, type, status and default star", async () => {
    const page: ConnectionPage = { items: [CONNECTION], total: 1 };
    stubFetch((url) => (url.includes("/fleet") ? { desired_replicas: 1, instances: [] } : page));
    renderWithClient(<ConnectionsTable />);
    // The mobile card list renders the same row again below 768px (both live
    // in the DOM — `ResponsiveTable` switches with CSS, not conditional
    // rendering) — scope to the desktop `<table>` to query a single match.
    const table = within(await screen.findByRole("table", { name: "Connections" }));
    expect(table.getByText("cloud-a")).toBeTruthy();
    expect(table.getByText("OK")).toBeTruthy();
    expect(table.getByText("LiveKit Cloud")).toBeTruthy();
    expect(screen.getAllByLabelText("Default connection").length).toBeGreaterThan(0);
  });

  it("shows an empty state with a New connection link when there are none", async () => {
    stubFetch(() => ({ items: [], total: 0 }));
    renderWithClient(<ConnectionsTable />);
    expect(await screen.findByText("No connections yet")).toBeTruthy();
    // `canWrite` (docs/v2/_asks.md V2-20-5) resolves from a separate
    // `auth/me` query — the link only replaces the disabled fallback button
    // once it settles.
    expect(await screen.findByRole("link", { name: "New connection" })).toBeTruthy();
  });
});

describe("ConnectionCreateForm", () => {
  it("keeps Save disabled until Test connection passes, then shows the capability chips from the response", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/connections/test") && init?.method === "POST") {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            ok: true,
            message: "Connected in 120ms.",
            capabilities: { inference_available: true, sip_enabled: false, egress_enabled: true },
            latency_ms: 120,
          }),
        } as Response;
      }
      return { ok: true, status: 200, json: async () => ({}) } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithClient(<ConnectionCreateForm />);

    const saveButton = screen.getByRole("button", { name: "Create connection" }) as HTMLButtonElement;
    expect(saveButton.disabled).toBe(true);

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "cloud-a" } });
    fireEvent.change(screen.getByLabelText("URL"), { target: { value: "wss://cloud-a.livekit.cloud" } });
    fireEvent.change(screen.getByLabelText("API key"), { target: { value: "key-123" } });
    fireEvent.change(screen.getByLabelText("API secret"), { target: { value: "secret-456" } });

    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));

    expect(await screen.findByText("Connection OK")).toBeTruthy();
    expect(screen.getByText("Connected in 120ms.")).toBeTruthy();
    // Capability chips from the mocked response (UI_UX_SPEC-V2-AMENDMENTS §2.1).
    expect(screen.getByText("Inference")).toBeTruthy();
    expect(screen.getByText("Egress")).toBeTruthy();
    await waitFor(() => expect(saveButton.disabled).toBe(false));
  });

  it("disables Save again once a tested field changes", async () => {
    stubFetch(() => ({ ok: true, message: "OK", capabilities: {} }));
    renderWithClient(<ConnectionCreateForm />);
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "cloud-a" } });
    fireEvent.change(screen.getByLabelText("URL"), { target: { value: "wss://cloud-a.livekit.cloud" } });
    fireEvent.change(screen.getByLabelText("API key"), { target: { value: "key-123" } });
    fireEvent.change(screen.getByLabelText("API secret"), { target: { value: "secret-456" } });
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    await waitFor(() => expect((screen.getByRole("button", { name: "Create connection" }) as HTMLButtonElement).disabled).toBe(false));

    fireEvent.change(screen.getByLabelText("API key"), { target: { value: "key-456" } });
    expect((screen.getByRole("button", { name: "Create connection" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/test again to enable Save/)).toBeTruthy();
  });
});
