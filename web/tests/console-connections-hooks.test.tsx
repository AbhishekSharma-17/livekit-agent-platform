import * as React from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  downloadDeployBundle,
  fetchWorkerEnv,
  useConnectionFleet,
  useConnections,
  useCreateConnection,
  useFleetAction,
  useRotateConnection,
  useTestConnection,
  useTestUnsavedConnection,
} from "@/hooks/useConnections";
import { useCatalog, useRefreshCatalog } from "@/hooks/useCatalog";
import { useUpdateProviderSettings } from "@/hooks/useProviders";
import type { ConnectionOut, ConnectionPage, FleetStatus } from "@/contracts/lkap-contracts";

/**
 * V2-13's connections/providers/catalog hooks (`web/src/hooks/{useConnections,useProviders,useCatalog}.ts`).
 * Mirrors the mocking convention of `console-api-hooks.test.tsx` (WP-0): a
 * stubbed global `fetch`, asserting the exact `/api/console/*` path and verb
 * rather than mocking `@/lib/api` itself, so a real bug in `apiRequest`
 * would still surface here.
 */

function wrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

function stubFetch(handler: (url: string, init?: RequestInit) => unknown) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const body = handler(url, init);
    return {
      ok: true,
      status: 200,
      headers: new Headers({ "content-type": "application/json" }),
      json: async () => body,
      text: async () => (typeof body === "string" ? body : JSON.stringify(body)),
      blob: async () => new Blob([typeof body === "string" ? body : JSON.stringify(body)]),
    } as unknown as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

const CONNECTION: ConnectionOut = {
  id: "conn-1",
  slug: "cloud-a",
  name: "cloud-a",
  url: "wss://cloud-a.livekit.cloud",
  deployment_type: "cloud",
  deployment_mode: "external",
  is_default: true,
  status: "ok",
};

describe("useConnections", () => {
  it("lists connections through the proxy", async () => {
    const page: ConnectionPage = { items: [CONNECTION], total: 1 };
    const fetchMock = stubFetch(() => page);
    const { result } = renderHook(() => useConnections(), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.items).toEqual([CONNECTION]);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/console/connections");
  });

  it("useCreateConnection POSTs the create payload", async () => {
    const fetchMock = stubFetch(() => CONNECTION);
    const { result } = renderHook(() => useCreateConnection(), { wrapper: wrapper() });
    await act(async () => {
      await result.current.mutateAsync({
        name: "cloud-a",
        slug: "cloud-a",
        url: "wss://cloud-a.livekit.cloud",
        api_key: "k",
        api_secret: "s",
      });
    });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/console/connections");
    expect(init?.method).toBe("POST");
  });

  it("useTestUnsavedConnection POSTs to /connections/test (nothing stored)", async () => {
    const fetchMock = stubFetch(() => ({ ok: true, message: "Connected", capabilities: { inference_available: true } }));
    const { result } = renderHook(() => useTestUnsavedConnection(), { wrapper: wrapper() });
    let outcome: unknown;
    await act(async () => {
      outcome = await result.current.mutateAsync({ name: "x", slug: "x", url: "wss://x", api_key: "k", api_secret: "s" });
    });
    expect(outcome).toEqual({ ok: true, message: "Connected", capabilities: { inference_available: true } });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/console/connections/test");
  });

  it("useTestConnection POSTs to /connections/{id}/test", async () => {
    const fetchMock = stubFetch(() => ({ ok: true, message: "OK", capabilities: {} }));
    const { result } = renderHook(() => useTestConnection(), { wrapper: wrapper() });
    await act(async () => {
      await result.current.mutateAsync("conn-1");
    });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/console/connections/conn-1/test");
  });

  it("useRotateConnection POSTs the new key/secret", async () => {
    const fetchMock = stubFetch(() => CONNECTION);
    const { result } = renderHook(() => useRotateConnection("conn-1"), { wrapper: wrapper() });
    await act(async () => {
      await result.current.mutateAsync({ api_key: "new-key", api_secret: "new-secret" });
    });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/console/connections/conn-1/rotate");
    expect(JSON.parse(String(init?.body))).toEqual({ api_key: "new-key", api_secret: "new-secret" });
  });

  it("useConnectionFleet + useFleetAction read and act on the fleet (docs/v2/_asks.md #48)", async () => {
    const status: FleetStatus = { desired_replicas: 1, instances: [], image: "slim" };
    stubFetch(() => status);
    const { result } = renderHook(() => useConnectionFleet("conn-1"), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(status);

    const fetchMock = stubFetch(() => ({ ...status, restart_generation: 1 }));
    const { result: actionResult } = renderHook(() => useFleetAction("conn-1"), { wrapper: wrapper() });
    await act(async () => {
      await actionResult.current.mutateAsync({ action: "restart" });
    });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/console/connections/conn-1/fleet");
    expect(JSON.parse(String(init?.body))).toEqual({ action: "restart" });
  });

  it("fetchWorkerEnv reads the plain-text env template (not JSON) and downloadDeployBundle POSTs for the zip", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("worker-env")) {
          return { ok: true, status: 200, text: async () => "LIVEKIT_URL=wss://x\n" } as unknown as Response;
        }
        return {
          ok: true,
          status: 200,
          headers: new Headers({ "content-disposition": 'attachment; filename="bundle.zip"' }),
          blob: async () => new Blob(["zip-bytes"]),
        } as unknown as Response;
      }),
    );
    const text = await fetchWorkerEnv("conn-1", "env");
    expect(text).toBe("LIVEKIT_URL=wss://x\n");

    // jsdom doesn't implement anchor.click()/createObjectURL by default in this project's setup;
    // stub just enough for the download side effect to run without throwing.
    const revoke = vi.fn();
    vi.stubGlobal("URL", { ...URL, createObjectURL: vi.fn(() => "blob:mock"), revokeObjectURL: revoke });
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    await downloadDeployBundle("conn-1", "fallback-name");
    expect(clickSpy).toHaveBeenCalled();
    expect(revoke).toHaveBeenCalledWith("blob:mock");
    clickSpy.mockRestore();
  });
});

describe("useCatalog / useRefreshCatalog", () => {
  it("reads GET /providers/{id}/catalog with kind + credential_id", async () => {
    const fetchMock = stubFetch(() => ({ kind: "avatars", items: [{ id: "a1", label: "Avatar one" }], source: "vendor" }));
    const { result } = renderHook(() => useCatalog("bey-avatar", "avatars", "cred-1"), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.items).toEqual([{ id: "a1", label: "Avatar one" }]);
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/api/console/providers/bey-avatar/catalog");
    expect(url).toContain("kind=avatars");
    expect(url).toContain("credential_id=cred-1");
  });

  it("useRefreshCatalog bypasses the cache with refresh=true", async () => {
    const fetchMock = stubFetch(() => ({ kind: "avatars", items: [], source: "vendor" }));
    const { result } = renderHook(() => useRefreshCatalog("bey-avatar", "avatars", null), { wrapper: wrapper() });
    await act(async () => {
      await result.current.mutateAsync();
    });
    expect(String(fetchMock.mock.calls[0][0])).toContain("refresh=true");
  });
});

describe("useUpdateProviderSettings", () => {
  it("PUTs /providers/{id}/settings", async () => {
    const fetchMock = stubFetch(() => ({ id: "openai-llm", enabled: false }));
    const { result } = renderHook(() => useUpdateProviderSettings(), { wrapper: wrapper() });
    await act(async () => {
      await result.current.mutateAsync({ id: "openai-llm", body: { enabled: false, default_credential_id: null } });
    });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/console/providers/openai-llm/settings");
    expect(init?.method).toBe("PUT");
  });
});
