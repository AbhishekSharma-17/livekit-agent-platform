import * as React from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { WorkerStatusNotice } from "@/components/console/connections/worker-status-notice";
import type { ConnectionOut, FleetStatus } from "@/contracts/lkap-contracts";

/**
 * V6-29 (ask #277, S6-27): **Copy worker settings** puts exactly the api's redacted text on
 * the clipboard — the `<…>` placeholders as the api wrote them, never a value of its own.
 */

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/console/connections",
  useSearchParams: () => new URLSearchParams(),
}));

/** What `GET /v1/connections/{id}/worker-env?format=env` answers since S6-27. */
const WORKER_ENV = [
  "# LKAP worker for connection 'Staging' (staging); agent name 'lkap-agent'.",
  "# Replace every <...> placeholder; the api never shows secrets.",
  "LIVEKIT_URL=wss://example.livekit.cloud",
  "LIVEKIT_API_KEY=<LIVEKIT_API_KEY …abcd>",
  "LIVEKIT_API_SECRET=<LIVEKIT_API_SECRET>",
  "LKAP_SERVICE_TOKEN=<LKAP_SERVICE_TOKEN>",
  "OTEL_EXPORTER_OTLP_ENDPOINT=https://otlp.example.com:4318",
  "OTEL_EXPORTER_OTLP_HEADERS=<OTEL_EXPORTER_OTLP_HEADERS>",
  "",
].join("\n");

const CONNECTION: ConnectionOut = {
  id: "conn-1",
  slug: "staging",
  name: "Staging",
  url: "wss://example.livekit.cloud",
  agent_name: "lkap-agent",
  deployment_type: "cloud",
  deployment_mode: "external",
  is_default: true,
  status: "ok",
  replicas: 1,
};

const NO_WORKERS: FleetStatus = { desired_replicas: 0, instances: [], ready_workers: 0, shared_agent_name_workers: 0 };

class StubResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

function stubFetch() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const body: unknown = url.includes("/worker-env") ? WORKER_ENV : url.includes("/fleet") ? NO_WORKERS : {};
    return {
      ok: true,
      status: 200,
      statusText: "",
      json: async () => body,
      text: async () => (typeof body === "string" ? body : JSON.stringify(body)),
    } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => {
  vi.stubGlobal("ResizeObserver", StubResizeObserver);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Copy worker settings", () => {
  it("puts exactly the api's redacted text on the clipboard, placeholders included", async () => {
    const fetchMock = stubFetch();
    const writeText = vi.fn<(text: string) => Promise<void>>(async () => {});
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <WorkerStatusNotice connection={CONNECTION} />
      </QueryClientProvider>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Copy worker settings" }));

    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    const copied = writeText.mock.calls[0][0];
    expect(copied).toBe(WORKER_ENV);
    expect(copied).toContain("OTEL_EXPORTER_OTLP_HEADERS=<OTEL_EXPORTER_OTLP_HEADERS>");
    expect(copied).not.toMatch(/Bearer|Authorization=/);
    expect(
      fetchMock.mock.calls.some(([input]) => String(input).endsWith("/api/console/connections/conn-1/worker-env?format=env")),
    ).toBe(true);
  });
});
