import * as React from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { WebhooksTab } from "@/components/console/settings/webhooks-tab";

/** Webhooks tab (V2-14, V2-08's CRUD): create with one-time secret, test, deliveries, redeliver. */
function jsonResponse(body: unknown, status = 200) {
  return { ok: status < 400, status, json: async () => body } as Response;
}

const ENDPOINT = {
  id: "wh1",
  url: "https://example.com/hooks/lkap",
  events: ["session.ended"],
  description: "Prod sink",
  enabled: true,
  secret_prefix: "whsec_ab",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

function renderTab() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <WebhooksTab />
    </QueryClientProvider>,
  );
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
  Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input).split("?")[0];
      const method = init?.method ?? "GET";
      if (path.endsWith("/webhooks") && method === "GET") return jsonResponse({ items: [ENDPOINT], total: 1 });
      if (path.endsWith("/webhooks") && method === "POST") {
        return jsonResponse({ ...ENDPOINT, id: "wh2", secret: "whsec_ab12cd34ef" });
      }
      const url = path;
      if (url.includes("/deliveries") && method === "GET") {
        return jsonResponse({
          items: [
            {
              id: "d1",
              endpoint_id: "wh1",
              event_type: "session.ended",
              event_id: "e1",
              attempt: 3,
              status: "dead",
              last_status_code: 500,
              last_error: "connection refused",
              created_at: "2026-01-02T00:00:00Z",
            },
          ],
          total: 1,
        });
      }
      if (url.includes("/redeliver")) return jsonResponse({});
      if (path.endsWith("/auth/me")) {
        return jsonResponse({
          user: { id: "u1", email: "admin@example.test" },
          workspaces: [{ id: "ws1", name: "Test workspace", slug: "test", role: "admin" }],
        });
      }
      return jsonResponse({});
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("WebhooksTab", () => {
  it("lists endpoints with their subscribed events", async () => {
    renderTab();
    expect((await screen.findAllByText("https://example.com/hooks/lkap")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Session ended").length).toBeGreaterThan(0);
  });

  it("creates an endpoint and shows the signing secret once", async () => {
    renderTab();
    await screen.findAllByText("https://example.com/hooks/lkap");

    fireEvent.click(screen.getByRole("button", { name: "Add webhook" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("URL"), { target: { value: "https://example.com/hooks/two" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Create" }));

    expect(await within(dialog).findByText("whsec_ab12cd34ef")).toBeTruthy();
  });

  it("shows deliveries and offers to redeliver a dead one", async () => {
    renderTab();
    await screen.findAllByText("https://example.com/hooks/lkap");

    fireEvent.click(screen.getAllByRole("button", { name: "Deliveries" })[0]);
    expect(await screen.findByText("connection refused")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Redeliver" }));
  });
});

describe("webhook event picker parity (ask #76)", () => {
  it("offers exactly the api's KNOWN_EVENTS, call events included", async () => {
    const { readFileSync } = await import("node:fs");
    const path = await import("node:path");
    const source = readFileSync(path.resolve(__dirname, "../../api/src/lkap_api/webhooks/events.py"), "utf8");
    const constants = Object.fromEntries(
      Array.from(source.matchAll(/^([A-Z_]+) = "([a-z_.]+)"$/gm), (m) => [m[1], m[2]]),
    );
    const block = /KNOWN_EVENTS: tuple\[str, \.\.\.\] = \(([^)]*)\)/.exec(source)?.[1] ?? "";
    const known = block
      .split(",")
      .map((name) => name.trim())
      .filter(Boolean)
      .map((name) => constants[name]);
    const { KNOWN_WEBHOOK_EVENTS, WEBHOOK_EVENT_LABEL } = await import("@/components/console/settings/api-types");

    expect(KNOWN_WEBHOOK_EVENTS).toEqual(known);
    expect(known).toEqual(expect.arrayContaining(["call.started", "call.ended"]));
    expect(KNOWN_WEBHOOK_EVENTS.every((event) => WEBHOOK_EVENT_LABEL[event])).toBe(true);
  });
});
