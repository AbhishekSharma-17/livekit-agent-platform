import * as React from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiKeysTab } from "@/components/console/settings/api-keys-tab";

/** API keys tab (V2-14): list, create (one-time reveal), revoke (CONTRACTS-V2 §3.4). */
function jsonResponse(body: unknown, status = 200) {
  return { ok: status < 400, status, json: async () => body } as Response;
}

const ME = {
  user: { id: "u1", email: "owner@local", name: "Owner" },
  workspaces: [{ id: "w1", slug: "default", name: "Default", role: "owner" }],
};

function renderTab() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ApiKeysTab />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/auth/me")) return jsonResponse(ME);
      if (url.includes("/api-keys") && (!init?.method || init.method === "GET")) {
        return jsonResponse({
          items: [
            {
              id: "k1",
              workspace_id: "w1",
              name: "CI key",
              prefix: "lkap_abcd",
              scopes: ["sessions:read"],
              created_at: "2026-01-01T00:00:00Z",
              revoked_at: null,
            },
          ],
          total: 1,
        });
      }
      if (url.endsWith("/api-keys") && init?.method === "POST") {
        return jsonResponse({
          id: "k2",
          workspace_id: "w1",
          name: "New key",
          prefix: "lkap_efgh",
          scopes: ["sessions:read"],
          created_at: "2026-01-03T00:00:00Z",
          revoked_at: null,
          key: "lkap_efgh1234567890",
        });
      }
      return jsonResponse({});
    }),
  );
  Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });
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

describe("ApiKeysTab", () => {
  it("lists existing keys by their prefix, never the secret", async () => {
    renderTab();
    expect((await screen.findAllByText("CI key")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("lkap_abcd…").length).toBeGreaterThan(0);
  });

  it("creates a key and shows the raw key once", async () => {
    renderTab();
    await screen.findAllByText("CI key");

    fireEvent.click(screen.getByRole("button", { name: "Create key" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: "New key" } });
    fireEvent.click(within(dialog).getByRole("checkbox", { name: /sessions:read/ }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Create" }));

    expect(await within(dialog).findByText("lkap_efgh1234567890")).toBeTruthy();
  });

  it("requires at least one scope before creating", async () => {
    renderTab();
    await screen.findAllByText("CI key");

    fireEvent.click(screen.getByRole("button", { name: "Create key" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: "New key" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Create" }));

    expect(await within(dialog).findByText("Choose at least one scope.")).toBeTruthy();
  });

  it("asks before revoking, and only then sends the DELETE", async () => {
    renderTab();
    await screen.findAllByText("CI key");
    const fetchMock = vi.mocked(fetch);
    const deletes = () => fetchMock.mock.calls.filter(([, init]) => init?.method === "DELETE");

    // The table row and the phone card both carry the action.
    fireEvent.click(screen.getAllByRole("button", { name: "Revoke" })[0]);
    const dialog = await screen.findByRole("dialog", { name: 'Revoke "CI key"?' });
    expect(deletes()).toHaveLength(0);

    fireEvent.click(within(dialog).getByRole("button", { name: "Revoke key" }));
    await waitFor(() => expect(deletes()).toHaveLength(1));
    expect(String(deletes()[0][0])).toContain("/api-keys/k1");
  });
});
