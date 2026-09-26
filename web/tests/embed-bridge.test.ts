import { afterEach, describe, expect, it, vi } from "vitest";

import { postToEmbedParent } from "@/components/session/embed/embed-bridge";
import { createTextSessionTokenSource } from "@/components/session/embed/text-token-source";
import type { ConnectResponse } from "@/contracts/lkap-contracts";

describe("postToEmbedParent", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("posts a tagged message to window.parent when framed", () => {
    const postMessage = vi.fn();
    // jsdom's `window.parent` is read-only (equals `window` at top level); stub it
    // to simulate being inside an iframe, the way widget.js's embed actually is.
    vi.spyOn(window, "parent", "get").mockReturnValue({ postMessage } as unknown as Window);

    postToEmbedParent({ type: "state", state: "connected" });

    expect(postMessage).toHaveBeenCalledWith({ source: "lkap-embed", type: "state", state: "connected" }, "*");
  });

  it("does nothing when not framed (window.parent === window)", () => {
    const postMessage = vi.fn();
    window.postMessage = postMessage;

    postToEmbedParent({ type: "close" });

    expect(postMessage).not.toHaveBeenCalled();
  });
});

/**
 * R-V5-10 / V5-52: the text-session connect body (used by the console's Test
 * chat and the embed widget's text-mode layout, both via this module) carries
 * the browser's own timezone as the top-level `timezone` field — it wins over
 * `participant_metadata.timezone` (CONTRACTS `TextSessionCreate`).
 */
describe("createTextSessionTokenSource — sends the browser's timezone (R-V5-10)", () => {
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

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("posts a top-level timezone with the browser's IANA zone", async () => {
    vi.spyOn(Intl, "DateTimeFormat").mockReturnValue({
      resolvedOptions: () => ({ timeZone: "America/Chicago" }),
    } as unknown as Intl.DateTimeFormat);
    const fetchMock = mockFetchOnce(CONNECT_RESPONSE);
    vi.stubGlobal("fetch", fetchMock);

    const { tokenSource } = createTextSessionTokenSource("smoke-generic", {}, { viaConsole: true });
    await tokenSource.fetch({ participantName: "Guest" });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(init.body as string) as { timezone?: string | null };
    expect(body.timezone).toBe("America/Chicago");
  });

  it("omits timezone when Intl can't say (fails soft, never blocks the call)", async () => {
    vi.spyOn(Intl, "DateTimeFormat").mockImplementation(() => {
      throw new Error("no Intl");
    });
    const fetchMock = mockFetchOnce(CONNECT_RESPONSE);
    vi.stubGlobal("fetch", fetchMock);

    const { tokenSource } = createTextSessionTokenSource("smoke-generic", {}, { viaConsole: true });
    await tokenSource.fetch({ participantName: "Guest" });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(init.body as string) as { timezone?: string | null };
    expect(body.timezone).toBeUndefined();
  });
});
