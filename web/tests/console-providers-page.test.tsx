import * as React from "react";

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ProvidersCatalog } from "@/components/console/providers/providers-catalog";
import type { ConnectionOut, ProviderOut } from "@/contracts/lkap-contracts";

/**
 * `/console/providers?kind=` (V2-13, UI_UX_SPEC-V2-AMENDMENTS §2.2): kind
 * tabs, the enable switch (`PUT /providers/{id}/settings`), the verification
 * chip and per-connection install chips, and the collapsed "Not available"
 * section for `incompatible`/`removed` entries.
 */

// `router.replace` is a real navigation in the browser (it re-renders every
// `useSearchParams()` subscriber); the mock is a no-op, so tests that need a
// non-default tab set `searchParams` directly before rendering — the
// equivalent of a deep link — rather than clicking a tab button.
let searchParams = new URLSearchParams();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/console/providers",
  useSearchParams: () => searchParams,
}));

beforeEach(() => {
  searchParams = new URLSearchParams();
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
});

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

const CONNECTION_A: ConnectionOut = {
  id: "conn-a",
  slug: "cloud-a",
  name: "cloud-a",
  url: "wss://cloud-a.livekit.cloud",
  deployment_type: "cloud",
  status: "ok",
};
const CONNECTION_B: ConnectionOut = {
  id: "conn-b",
  slug: "self-a",
  name: "self-a",
  url: "ws://localhost:7880",
  deployment_type: "self_hosted",
  status: "ok",
};

const OPENAI: ProviderOut = {
  v: 2,
  id: "openai-realtime",
  kind: "realtime",
  label: "OpenAI Realtime",
  vendor: "OpenAI",
  availability: "available",
  worker_image: "full",
  enabled: true,
  installed_on: ["conn-a"],
  verification: "verified",
  package: "livekit-plugins-openai",
  python_class: "livekit.plugins.openai.realtime.RealtimeModel",
  requires_credential: true,
  secret_fields: [{ name: "api_key", label: "API key", type: "secret", required: true }],
  fields: [],
  models: [],
  default_model: null,
  capabilities: {},
};

const HEDRA_REMOVED: ProviderOut = {
  ...OPENAI,
  id: "hedra-avatar",
  kind: "avatar",
  label: "Hedra",
  vendor: "Hedra",
  availability: "removed",
  notes: "Vendor-disabled.",
  verification: "unverified",
};

function stubProviders(providers: ProviderOut[], connections: ConnectionOut[]) {
  const put = vi.fn();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/console/providers/") && url.endsWith("/settings")) {
        put(url, init?.body ? JSON.parse(String(init.body)) : undefined);
        return { ok: true, status: 200, json: async () => ({ ...providers[0], enabled: false }) } as Response;
      }
      if (url.includes("/api/console/providers")) return { ok: true, status: 200, json: async () => ({ v: 2, providers }) } as Response;
      if (url.includes("/api/console/connections")) return { ok: true, status: 200, json: async () => ({ items: connections, total: connections.length }) } as Response;
      if (url.includes("/api/console/credentials")) return { ok: true, status: 200, json: async () => ({ items: [], total: 0 }) } as Response;
      return { ok: true, status: 200, json: async () => ({}) } as Response;
    }),
  );
  return put;
}

describe("ProvidersCatalog", () => {
  it("shows the verification chip, install chips per connection, and the enable switch", async () => {
    stubProviders([OPENAI], [CONNECTION_A, CONNECTION_B]);
    renderWithClient(<ProvidersCatalog />);

    const row = await screen.findByText("OpenAI Realtime");
    const card = row.closest('[class*="rounded-lg"]') as HTMLElement;
    expect(within(card).getByText("Verified")).toBeTruthy();
    expect(within(card).getByText("on cloud-a")).toBeTruthy();
    expect(within(card).getByText("not on self-a")).toBeTruthy();
    expect(within(card).getByRole("switch")).toBeTruthy();
  });

  it("PUTs /providers/{id}/settings when the enable switch is toggled", async () => {
    const put = stubProviders([OPENAI], [CONNECTION_A]);
    renderWithClient(<ProvidersCatalog />);
    await screen.findByText("OpenAI Realtime");
    fireEvent.click(screen.getByRole("switch"));
    await waitFor(() =>
      expect(put).toHaveBeenCalledWith("/api/console/providers/openai-realtime/settings", {
        enabled: false,
        default_credential_id: null,
      }),
    );
  });

  it("collapses removed/incompatible entries under 'Not available' with the reason, so nobody files a bug", async () => {
    // `router.replace` in the mock has nothing to re-render off in jsdom
    // (see the `next/navigation` mock above), so the "Avatars" tab is
    // selected by starting the search params there instead of clicking —
    // equivalent to a deep link to `?kind=avatar`, which is the real
    // mechanism `selectTab` relies on in the browser.
    searchParams = new URLSearchParams("kind=avatar");
    stubProviders([HEDRA_REMOVED], [CONNECTION_A]);
    renderWithClient(<ProvidersCatalog />);
    const toggle = await screen.findByText(/Not available \(1\)/);
    expect(screen.queryByText("Hedra")).toBeNull();
    fireEvent.click(toggle);
    expect(await screen.findByText("Hedra")).toBeTruthy();
    expect(screen.getByText("Vendor-disabled.")).toBeTruthy();
  });
});
