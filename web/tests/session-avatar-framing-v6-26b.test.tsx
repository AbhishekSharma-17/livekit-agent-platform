import * as React from "react";

import { cleanup, render, screen } from "@testing-library/react";
import { useSession } from "@livekit/components-react";
import { TokenSource } from "livekit-client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  AgentAvatarFraming,
  AgentOut,
  AgentPublicOut,
  ConnectResponse,
} from "@/contracts/lkap-contracts";
import { AgentSessionProvider } from "@/components/agents-ui/agent-session-provider";
import { DEFAULT_ASPECT_RATIO, stageAvatarFraming } from "@/components/session/avatar-framing";
import { SessionRoom, type SessionRoomProps } from "@/components/session/session-room";
import { fetchConnect, fetchPublicAgent, toPublicAgent } from "@/lib/livekit";

/**
 * V6-26b (docs/v6/_asks.md #151, #159): the builder's saved avatar Framing/Fit
 * reaches the session stage on every real surface — `/s/[slug]` and the embed
 * (both render `GET /v1/agents/{slug}` → `SessionRoom`) and the console's
 * `?mode=test` preview (the server-side `toPublicAgent()` copy before connect,
 * then `ConnectResponse.agent` from the console proxy). A `null` value keeps
 * the crop-free `auto` + `contain` default.
 *
 * `SessionRoom` runs inside a real (never-started) `useSession`; only the
 * LiveKit hooks that need a live agent participant are stubbed, so the stage
 * has an avatar video well to inspect.
 */

vi.mock("@livekit/components-react", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@livekit/components-react")>()),
  useVoiceAssistant: () => ({
    agent: undefined,
    audioTrack: undefined,
    videoTrack: { participant: {}, publication: { trackSid: "TR_avatar" }, source: "camera" },
  }),
  useTrackVolume: () => 0,
  VideoTrack: React.forwardRef<HTMLVideoElement, { className?: string; style?: React.CSSProperties; "data-testid"?: string }>(
    function FakeVideoTrack(props, ref) {
      return <video ref={ref} data-testid={props["data-testid"]} className={props.className} style={props.style} />;
    },
  ),
}));

// jsdom has no `navigator.mediaDevices` and no secure context; the control
// bar's device pickers require both on mount (and again as they unsubscribe,
// after a test has finished), so they stay defined for the whole file. An
// empty device list is all this file needs.
Object.defineProperty(window, "isSecureContext", { configurable: true, value: true });
Object.defineProperty(navigator, "mediaDevices", {
  configurable: true,
  value: {
    enumerateDevices: () => Promise.resolve([]),
    getUserMedia: () => Promise.reject(new Error("no media in tests")),
    addEventListener: () => {},
    removeEventListener: () => {},
  },
});

beforeEach(() => {
  stubFetch(undefined);
  vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.example.test");
});

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const PORTRAIT_COVER: AgentAvatarFraming = { framing: "portrait", fit: "cover", declared_aspect: "portrait" };
const PORTRAIT = 9 / 16;

/** A panel with no `video` block, so the stage (not the panel) draws the avatar. */
function publicAgent(avatarFraming: AgentAvatarFraming | null): AgentPublicOut {
  return {
    id: "agent-1",
    slug: "avatar-desk",
    name: "Ada",
    description: "",
    ui_panel_id: "composite",
    capabilities: {},
    pipeline_mode: "cascaded",
    panel: {
      panel_id: "composite",
      layout: "side",
      blocks: [{ id: "notes", type: "notes", title: "Notes", config: {}, order: 0 }],
    },
    avatar_framing: avatarFraming,
  };
}

function adminAgent(avatar: boolean): AgentOut {
  return {
    id: "agent-1",
    slug: "avatar-desk",
    name: "Ada",
    description: "",
    pack_id: "generic",
    ui_panel_id: "composite",
    published: false,
    config_version: 3,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    config: {
      instructions: "Be helpful.",
      pipeline: {
        mode: "cascaded",
        ...(avatar
          ? {
              avatar: { provider_id: "lemonslice-avatar", credential_id: "cred-1" },
              avatar_options: { participant_name: "Avatar", framing: "portrait", fit: "cover" },
            }
          : {}),
      },
      panel: {
        panel_id: "composite",
        layout: "side",
        blocks: [{ id: "notes", type: "notes", title: "Notes", config: {}, order: 0 }],
      },
    },
  } as AgentOut;
}

/** Every request in this file is answered by a stub — including the never-started
 * room's own `prepareConnection()` warm-up — so nothing ever leaves the process. */
function stubFetch(body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers(),
      json: () => Promise.resolve(body),
    }),
  );
}

function Room(props: Pick<SessionRoomProps, "agent" | "testMode" | "embed" | "avatarFraming">) {
  const [tokenSource] = React.useState(() =>
    TokenSource.literal({ serverUrl: "wss://livekit.example.test", participantToken: "token" }),
  );
  const session = useSession(tokenSource, { participantName: "Guest" });
  return (
    <AgentSessionProvider session={session}>
      <SessionRoom sessionId={null} uiPanelId={null} onRetry={() => {}} onLeave={() => {}} onEnded={() => {}} {...props} />
    </AgentSessionProvider>
  );
}

function stageWell() {
  return screen.getByRole("button", { name: /enlarge ada/i });
}

function expectPortraitCover() {
  const well = stageWell();
  expect(well.getAttribute("data-fit")).toBe("cover");
  expect(Number(well.getAttribute("data-aspect"))).toBeCloseTo(PORTRAIT);
  expect((screen.getByTestId("stage-video") as HTMLVideoElement).style.objectFit).toBe("cover");
}

function expectAutoContain() {
  const well = stageWell();
  expect(well.getAttribute("data-fit")).toBe("contain");
  expect(Number(well.getAttribute("data-aspect"))).toBeCloseTo(DEFAULT_ASPECT_RATIO);
}

describe("stageAvatarFraming (V6-26b)", () => {
  it("maps the public display hints onto the stage's props", () => {
    expect(stageAvatarFraming(PORTRAIT_COVER)).toEqual({
      framing: "portrait",
      fit: "cover",
      declaredAspect: "portrait",
    });
  });

  it("yields no props for a null or missing value (auto + contain)", () => {
    expect(stageAvatarFraming(null)).toBeUndefined();
    expect(stageAvatarFraming(undefined)).toBeUndefined();
  });
});

describe("toPublicAgent avatar_framing (test mode, V6-26b)", () => {
  it("copies the saved framing/fit through; the registry aspect is left to the connect response", () => {
    expect(toPublicAgent(adminAgent(true)).avatar_framing).toEqual({
      framing: "portrait",
      fit: "cover",
      declared_aspect: null,
    });
  });

  it("is null without an avatar", () => {
    expect(toPublicAgent(adminAgent(false)).avatar_framing).toBeNull();
  });
});

describe("SessionRoom renders AgentPublicOut.avatar_framing on every surface (V6-26b)", () => {
  it("public /s/[slug]: a portrait + cover agent frames the stage portrait, cover", async () => {
    stubFetch(publicAgent(PORTRAIT_COVER));
    const agent = await fetchPublicAgent("avatar-desk");
    render(<Room agent={agent} />);
    expectPortraitCover();
  });

  it("embed: the same public agent frames the embed's stage portrait, cover", async () => {
    stubFetch(publicAgent(PORTRAIT_COVER));
    const agent = await fetchPublicAgent("avatar-desk");
    render(<Room agent={agent} embed />);
    expectPortraitCover();
  });

  it("console ?mode=test, before connect: the toPublicAgent copy frames the stage portrait, cover", () => {
    render(<Room agent={toPublicAgent(adminAgent(true))} testMode />);
    expectPortraitCover();
  });

  it("console ?mode=test, connected: the proxied ConnectResponse.agent brings the declared aspect", async () => {
    const response: ConnectResponse = {
      agent: publicAgent({ framing: "auto", fit: "contain", declared_aspect: "portrait" }),
      participantName: "Guest",
      participantToken: "token",
      roomName: "room-1",
      serverUrl: "wss://livekit.example.test",
      sessionId: "session-1",
      uiPanelId: "composite",
    };
    stubFetch(response);
    const connected = await fetchConnect("avatar-desk", { participant_name: "Guest" }, { viaConsole: true });
    render(<Room agent={connected.agent} testMode />);
    const well = stageWell();
    expect(well.getAttribute("data-fit")).toBe("contain");
    expect(Number(well.getAttribute("data-aspect"))).toBeCloseTo(PORTRAIT);
  });

  it.each([
    ["public", {}],
    ["embed", { embed: true }],
    ["test mode", { testMode: true }],
  ])("%s: a null avatar_framing keeps the crop-free auto + contain default", (_surface, extra) => {
    render(<Room agent={publicAgent(null)} {...extra} />);
    expectAutoContain();
  });

  it("an explicit avatarFraming override still wins over the agent's own value", () => {
    render(<Room agent={publicAgent(PORTRAIT_COVER)} avatarFraming={{ framing: "square", fit: "contain" }} />);
    const well = stageWell();
    expect(well.getAttribute("data-fit")).toBe("contain");
    expect(Number(well.getAttribute("data-aspect"))).toBeCloseTo(1);
  });
});
