import { afterEach, describe, expect, it, vi } from "vitest";

import type { AgentOut, ConnectResponse } from "@/contracts/lkap-contracts";
import {
  ConnectError,
  browserTimezone,
  classifyConnectError,
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
    panel: { panel_id: "generic", layout: "side", blocks: [] },
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
      // R-V2-7: the stored layout; a block panel with no blocks shows the default four.
      panel: {
        panel_id: "composite",
        layout: "side",
        blocks: [
          { id: "status", type: "status", title: null, config: {}, order: 0 },
          { id: "notes", type: "notes", title: "Notes", config: {}, order: 1 },
          { id: "checklist", type: "checklist", title: "Still needed", config: {}, order: 2 },
          { id: "activity", type: "activity", title: "Activity", config: {}, order: 3 },
        ],
      },
    });
  });

  it("passes a saved panel layout through (R-V2-7)", () => {
    const agent = {
      id: "agent-2",
      slug: "claims",
      name: "Claims",
      description: "",
      pack_id: "insurance_claim",
      ui_panel_id: "composite",
      published: false,
      config_version: 1,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      config: {
        instructions: "x",
        pipeline: {},
        panel: { panel_id: "composite", layout: "wide", blocks: [{ id: "t", type: "table", config: {}, order: 0 }] },
      },
    } as AgentOut;
    expect(toPublicAgent(agent).panel).toEqual({
      panel_id: "composite",
      layout: "wide",
      blocks: [{ id: "t", type: "table", config: {}, order: 0 }],
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

/** R-V5-10: the browser's own timezone, sent on connect (`participant_metadata.timezone`). */
describe("browserTimezone", () => {
  it("returns Intl's resolved zone", () => {
    vi.spyOn(Intl, "DateTimeFormat").mockReturnValue({
      resolvedOptions: () => ({ timeZone: "Europe/London" }),
    } as unknown as Intl.DateTimeFormat);

    expect(browserTimezone()).toBe("Europe/London");
  });

  it("fails soft (undefined) when Intl throws", () => {
    vi.spyOn(Intl, "DateTimeFormat").mockImplementation(() => {
      throw new Error("no Intl");
    });

    expect(browserTimezone()).toBeUndefined();
  });
});

describe("createConnectTokenSource — sends the browser's timezone (R-V5-10)", () => {
  it("posts participant_metadata.timezone with the browser's IANA zone", async () => {
    vi.spyOn(Intl, "DateTimeFormat").mockReturnValue({
      resolvedOptions: () => ({ timeZone: "Asia/Kolkata" }),
    } as unknown as Intl.DateTimeFormat);
    const fetchMock = mockFetchOnce(CONNECT_RESPONSE);
    vi.stubGlobal("fetch", fetchMock);

    const { tokenSource } = createConnectTokenSource("smoke-generic", {}, { viaConsole: true });
    await tokenSource.fetch({ participantName: "Guest" });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(init.body as string) as { participant_metadata?: Record<string, string> };
    expect(body.participant_metadata).toEqual({ timezone: "Asia/Kolkata" });
  });

  it("omits participant_metadata.timezone when Intl can't say (fails soft, never blocks the call)", async () => {
    vi.spyOn(Intl, "DateTimeFormat").mockImplementation(() => {
      throw new Error("no Intl");
    });
    const fetchMock = mockFetchOnce(CONNECT_RESPONSE);
    vi.stubGlobal("fetch", fetchMock);

    const { tokenSource } = createConnectTokenSource("smoke-generic", {}, { viaConsole: true });
    await tokenSource.fetch({ participantName: "Guest" });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(init.body as string) as { participant_metadata?: Record<string, string> };
    expect(body.participant_metadata).toEqual({});
  });
});

/**
 * UI_UX_SPEC §5.7: which "unavailable" page a load/connect failure produces.
 */
describe("classifyConnectError", () => {
  it("maps 404 to not_found and 403 to not_published", () => {
    expect(
      classifyConnectError(new ConnectError(404, "not_found", "nope")),
    ).toBe("not_found");
    expect(
      classifyConnectError(new ConnectError(403, "forbidden", "draft")),
    ).toBe("not_published");
  });

  it("treats a 403 through the console proxy as a token problem, not a draft", () => {
    // DECISIONS-W2 D-W2-1 item 5: in test mode the proxy's admin token is the
    // only thing a 401/403 can be about.
    expect(
      classifyConnectError(new ConnectError(403, "forbidden", "draft"), {
        viaConsole: true,
      }),
    ).toBe("other");
    expect(
      classifyConnectError(new ConnectError(401, "unauthorized", "no token")),
    ).toBe("other");
  });

  it("treats transport failures and a missing base URL as unreachable", () => {
    expect(classifyConnectError(new TypeError("fetch failed"))).toBe(
      "unreachable",
    );
    expect(
      classifyConnectError(
        new ConnectError(0, "missing_api_base_url", "not configured"),
      ),
    ).toBe("unreachable");
  });

  it("falls back to other for anything else the API returns", () => {
    expect(classifyConnectError(new ConnectError(500, "boom", "server"))).toBe(
      "other",
    );
  });
});
