import { afterEach, describe, expect, it, vi } from "vitest";

import type { AgentOut, ConnectResponse } from "@/contracts/lkap-contracts";
import {
  createConnectTokenSource,
  toPublicAgent,
} from "@/lib/livekit";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const CONNECT_RESPONSE: ConnectResponse = {
  agent: {
    id: "agent-1",
    slug: "smoke-generic",
    name: "Smoke Generic",
    description: "",
    pipeline_mode: "cascaded",
    capabilities: {},
    ui_panel_id: "generic",
  },
  participantName: "Guest",
  participantToken: "token-abc",
  roomName: "room-1",
  serverUrl: "wss://example.livekit.cloud",
  sessionId: "session-1",
  uiPanelId: "generic",
};

function mockFetchOnce(body: unknown, status = 200) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  });
}

describe("toPublicAgent", () => {
  it("maps the admin AgentOut down to the browser-safe AgentPublicOut shape", () => {
    const agent: AgentOut = {
      id: "agent-1",
      slug: "smoke-generic",
      name: "Smoke Generic",
      description: "A draft agent.",
      pack_id: "generic",
      ui_panel_id: "generic",
      published: false,
      config_version: 1,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      config: {
        instructions: "Be helpful.",
        pipeline: { mode: "realtime" },
        capabilities: { camera: true, screen_share: false },
      },
    };

    expect(toPublicAgent(agent)).toEqual({
      id: "agent-1",
      slug: "smoke-generic",
      name: "Smoke Generic",
      description: "A draft agent.",
      ui_panel_id: "generic",
      capabilities: { camera: true, screen_share: false },
      pipeline_mode: "realtime",
    });
  });

  it("falls back to empty capabilities and cascaded mode when config omits them", () => {
    const agent: AgentOut = {
      id: "agent-1",
      slug: "smoke-generic",
      name: "Smoke Generic",
      description: "",
      pack_id: "generic",
      ui_panel_id: "generic",
      published: false,
      config_version: 1,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      config: {
        instructions: "Be helpful.",
        pipeline: {},
      },
    };

    const result = toPublicAgent(agent);
    expect(result.capabilities).toEqual({});
    expect(result.pipeline_mode).toBe("cascaded");
  });
});

describe("createConnectTokenSource — test mode (DECISIONS-W2 D-W2-1)", () => {
  it("with { viaConsole: true } posts to the console proxy path, never the public API", async () => {
    const fetchMock = mockFetchOnce(CONNECT_RESPONSE);
    vi.stubGlobal("fetch", fetchMock);

    const { tokenSource } = createConnectTokenSource(
      "smoke-generic",
      {},
      { viaConsole: true },
    );

    await tokenSource.fetch({ participantName: "Guest" });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/console/agents/smoke-generic/connect");
  });

  it("without access (or viaConsole: false) posts to the public API base URL", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.example.com");
    const fetchMock = mockFetchOnce(CONNECT_RESPONSE);
    vi.stubGlobal("fetch", fetchMock);

    const { tokenSource } = createConnectTokenSource("smoke-generic", {});

    await tokenSource.fetch({ participantName: "Guest" });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://api.example.com/v1/agents/smoke-generic/connect");
  });
});

describe("createConnectTokenSource — freeze after first connect (DECISIONS-W2 D-W2-2a)", () => {
  it("calling the token source twice, the second time with force, performs one fetch and returns identical credentials", async () => {
    const fetchMock = mockFetchOnce(CONNECT_RESPONSE);
    vi.stubGlobal("fetch", fetchMock);

    const { tokenSource } = createConnectTokenSource(
      "smoke-generic",
      {},
      { viaConsole: true },
    );

    const options = { participantName: "Guest" };
    const first = await tokenSource.fetch(options);
    // `useSession`'s unexpected-disconnect handler calls with `force: true`,
    // which bypasses livekit-client's own TokenSourceCached caching — the
    // freeze inside our custom callback is what stops a second network call.
    const second = await tokenSource.fetch(options, true);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(second.participantToken).toBe(first.participantToken);
    expect(second.serverUrl).toBe(first.serverUrl);
  });

  it("freeze() before any connect is a no-op, so a StrictMode effect re-run still connects exactly once", async () => {
    const fetchMock = mockFetchOnce(CONNECT_RESPONSE);
    vi.stubGlobal("fetch", fetchMock);

    const { tokenSource, freeze } = createConnectTokenSource("stage9-insurance");
    // React StrictMode (next dev): effect setup -> cleanup (freeze) -> setup again.
    freeze();
    const first = await tokenSource.fetch({ participantName: "Guest" });
    freeze(); // the real unmount after a connect: from now on, replay only
    const second = await tokenSource.fetch({ participantName: "Guest" }, true);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(first.participantToken).toBe(CONNECT_RESPONSE.participantToken);
    expect(second.participantToken).toBe(first.participantToken);
  });
});
