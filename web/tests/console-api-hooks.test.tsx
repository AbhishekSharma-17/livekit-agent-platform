import * as React from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  useHealth,
  useSessions,
  useTestCredential,
  useUpdateCredential,
} from "@/components/console/lib/api-hooks";
import type { HealthResponse, SessionOut, SessionPage } from "@/contracts/lkap-contracts";

function wrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

function stubFetch(body: unknown) {
  const fetchMock = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(async () => ({
    ok: true,
    status: 200,
    json: async () => body,
  }) as Response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("WP-0 console hooks", () => {
  it("useHealth reads GET /v1/health through the console proxy", async () => {
    const health: HealthResponse = {
      ok: true,
      version: "0.1.0",
      livekit_url: "wss://example.livekit.cloud",
      packs: ["generic", "insurance_claim"],
      db: "ok",
    };
    const fetchMock = stubFetch(health);
    const { result } = renderHook(() => useHealth(), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(health);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/console/health");
  });

  it("useSessions applies an optional client-side limit without changing total", async () => {
    const items = Array.from({ length: 12 }, (_, i) => ({ id: `s-${i}` }) as SessionOut);
    const page: SessionPage = { items, total: 12 };
    stubFetch(page);
    const { result } = renderHook(() => useSessions(undefined, undefined, 8), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.items).toHaveLength(8);
    expect(result.current.data?.items[0].id).toBe("s-0");
    expect(result.current.data?.total).toBe(12);
  });

  it("useSessions without a limit returns every item", async () => {
    const items = Array.from({ length: 3 }, (_, i) => ({ id: `s-${i}` }) as SessionOut);
    stubFetch({ items, total: 3 });
    const { result } = renderHook(() => useSessions(), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.items).toHaveLength(3);
  });

  it("useTestCredential POSTs to /credentials/{id}/test", async () => {
    const fetchMock = stubFetch({ ok: false, message: "401 from vendor" });
    const { result } = renderHook(() => useTestCredential(), { wrapper: wrapper() });
    let outcome: unknown;
    await act(async () => {
      outcome = await result.current.mutateAsync("cred-1");
    });
    expect(outcome).toEqual({ ok: false, message: "401 from vendor" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/console/credentials/cred-1/test");
    expect(init?.method).toBe("POST");
  });

  it("useUpdateCredential PUTs the update body (secrets omitted keeps them)", async () => {
    const fetchMock = stubFetch({ id: "cred-1", label: "Renamed" });
    const { result } = renderHook(() => useUpdateCredential(), { wrapper: wrapper() });
    await act(async () => {
      await result.current.mutateAsync({ id: "cred-1", body: { label: "Renamed" } });
    });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/console/credentials/cred-1");
    expect(init?.method).toBe("PUT");
    expect(JSON.parse(String(init?.body))).toEqual({ label: "Renamed" });
  });
});
