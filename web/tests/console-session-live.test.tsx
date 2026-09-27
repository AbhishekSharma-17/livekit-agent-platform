import * as React from "react";

import { act, cleanup, fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConnectionState } from "livekit-client";
import type { UseSessionReturn } from "@livekit/components-react";

import type { AgentOut, PanelLayout, SessionDetailOut, SessionEventOut, UiState } from "@/contracts/lkap-contracts";
import { ApiError } from "@/lib/api";

/**
 * V5-38 Console Live tab (`docs/v5/PLAN-V5.md` V5-38, `_asks.md` #251):
 *
 * - `tests/console-sessions-table.test.tsx` covers the sessions list's
 *   "Listen in" link.
 * - `builtin-tabs.test` below covers the tab registry hiding "Live" once a
 *   session isn't `active` (the acceptance line "the tab is absent on ended
 *   sessions").
 * - `useListenSession` (mocking `@livekit/components-react`'s `useSession` —
 *   the LiveKit room boundary — only) covers minting the listen token once
 *   and connecting hidden/listen-only ("requests the token once and
 *   connects with a mocked room").
 * - `LiveTabContent` (rendered directly with an injected `listen`, the same
 *   pattern `SessionRoom`/`TextChat` use elsewhere in this codebase — the
 *   thin `useSession`-calling wrapper itself is never unit-tested, matching
 *   `components/session/live-session.tsx`) covers the panel mirror and the
 *   whisper box posting and clearing.
 */

// ---------------------------------------------------------------------------
// LiveKit room boundary: only `useSession`/`useSessionMessages`/
// `RoomAudioRenderer` are mocked. Everything else (the real `TokenSource`,
// `SessionProvider`, our own hooks) runs for real.
// ---------------------------------------------------------------------------

let currentSession: UseSessionReturn;
const useSessionMock = vi.fn((...args: unknown[]): UseSessionReturn => {
  void args;
  return currentSession;
});
const useSessionMessagesMock = vi.fn((...args: unknown[]) => {
  void args;
  return { messages: [] as never[] };
});
const roomAudioRendererMock = vi.fn();

vi.mock("@livekit/components-react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@livekit/components-react")>();
  return {
    ...actual,
    useSession: (...args: unknown[]) => useSessionMock(...args),
    useSessionMessages: (...args: unknown[]) => useSessionMessagesMock(...args),
    RoomAudioRenderer: (props: unknown) => {
      roomAudioRendererMock(props);
      return null;
    },
  };
});

function fakeUseSessionReturn(overrides: Partial<UseSessionReturn> = {}): UseSessionReturn {
  return {
    room: {} as UseSessionReturn["room"],
    connectionState: ConnectionState.Disconnected,
    isConnected: false,
    internal: {} as UseSessionReturn["internal"],
    local: { cameraTrack: undefined, microphoneTrack: undefined, screenShareTrack: undefined },
    waitUntilConnected: vi.fn(),
    waitUntilDisconnected: vi.fn(),
    prepareConnection: vi.fn(),
    start: vi.fn().mockResolvedValue(undefined),
    end: vi.fn().mockResolvedValue(undefined),
    setEncryptionEnabled: vi.fn(),
    ...overrides,
  } as UseSessionReturn;
}

// ---------------------------------------------------------------------------
// The api boundary the Live tab talks to (never LiveKit): mint, whisper, feed.
// ---------------------------------------------------------------------------

const fetchListenTokenMock = vi.fn();
const whisperMutateAsync = vi.fn();
let whisperIsPending = false;
const liveEventsItems: SessionEventOut[] = [];

vi.mock("@/components/console/lib/api-hooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/console/lib/api-hooks")>();
  return {
    ...actual,
    fetchSessionListenToken: (...args: unknown[]) => fetchListenTokenMock(...args),
    useSessionWhisper: () => ({ mutateAsync: whisperMutateAsync, isPending: whisperIsPending }),
    useLiveSessionEvents: () => ({ data: { items: liveEventsItems, total: liveEventsItems.length } }),
  };
});

vi.mock("@/components/console/sessions/use-session-queries", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/console/sessions/use-session-queries")>();
  return {
    ...actual,
    useSessionAgent: () => ({ data: AGENT, isLoading: false }),
  };
});

const uiStateMock = vi.fn();
vi.mock("@/hooks/useUiState", () => ({
  useUiState: (...args: unknown[]) => uiStateMock(...args),
}));

import { BUILTIN_SESSION_TABS } from "@/components/console/sessions/detail/builtin-tabs";
import { visibleTabs } from "@/components/console/sessions/detail/registry";
import { LiveSessionTab, LiveTabContent } from "@/components/console/sessions/live/live-tab";
import { useListenSession, type UseListenSessionReturn } from "@/components/console/sessions/live/listen-session";
import { WhisperBox } from "@/components/console/sessions/live/whisper-box";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function detail(overrides: Partial<SessionDetailOut> = {}): SessionDetailOut {
  return {
    id: "s-1",
    agent_id: "a-1",
    agent_name: "Claims desk",
    config_version: 1,
    room_name: "lkap-s1",
    status: "active",
    pipeline_mode: "cascaded",
    created_at: "2026-09-27T00:00:00Z",
    started_at: "2026-09-27T00:00:01Z",
    ended_at: null,
    usage: null,
    error: null,
    channel: "web",
    connection_id: null,
    transcript: [],
    final_ui_state: null,
    ...overrides,
  };
}

const PANEL: PanelLayout = {
  panel_id: "composite",
  blocks: [{ id: "handoff", type: "handoff", title: "Handoff" }],
};

const AGENT: AgentOut = {
  id: "a-1",
  name: "Claims desk",
  slug: "claims-desk",
  description: "",
  pack_id: "generic",
  ui_panel_id: "composite",
  published: true,
  config_version: 1,
  created_at: "2026-09-27T00:00:00Z",
  updated_at: "2026-09-27T00:00:00Z",
  config: { pipeline: { mode: "cascaded" }, capabilities: {}, panel: PANEL } as AgentOut["config"],
};

function uiStateWithHandoff(state: Record<string, unknown>) {
  const value: UiState = { v: 2, blocks: { handoff: state } };
  return { state: value, assets: new Map<string, string>(), seq: 1, connected: true, needsSnapshot: false, sessionId: "s-1" };
}

function listenReturn(overrides: Partial<UseListenSessionReturn> = {}): UseListenSessionReturn {
  return {
    session: fakeUseSessionReturn({ connectionState: ConnectionState.Connected, isConnected: true }),
    phase: "live",
    errorMessage: null,
    expiresAt: null,
    expiresInMs: null,
    reconnect: vi.fn(),
    ...overrides,
  };
}

beforeEach(() => {
  currentSession = fakeUseSessionReturn();
  useSessionMock.mockClear();
  useSessionMessagesMock.mockClear();
  roomAudioRendererMock.mockClear();
  fetchListenTokenMock.mockReset();
  whisperMutateAsync.mockReset();
  whisperIsPending = false;
  liveEventsItems.length = 0;
  uiStateMock.mockReturnValue(uiStateWithHandoff({ status: "idle" }));
});

afterEach(() => {
  cleanup();
});

// ---------------------------------------------------------------------------
// Tab registry: absent once the session isn't active
// ---------------------------------------------------------------------------

describe("the 'Live' tab registration", () => {
  it("is visible only while the session is active", () => {
    const live = BUILTIN_SESSION_TABS.find((tab) => tab.id === "live");
    expect(live).toBeTruthy();
    expect(visibleTabs(BUILTIN_SESSION_TABS, { session: detail({ status: "active" }) }).map((t) => t.id)).toContain("live");
    expect(visibleTabs(BUILTIN_SESSION_TABS, { session: detail({ status: "ended" }) }).map((t) => t.id)).not.toContain("live");
    expect(visibleTabs(BUILTIN_SESSION_TABS, { session: detail({ status: "failed" }) }).map((t) => t.id)).not.toContain("live");
  });
});

describe("LiveSessionTab", () => {
  it("shows a plain empty state instead of connecting when the session isn't active", () => {
    render(<LiveSessionTab session={detail({ status: "ended" })} />);
    expect(screen.getByText("This call isn't live anymore")).toBeTruthy();
    expect(useSessionMock).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// useListenSession: mints the token once, connects hidden/listen-only
// ---------------------------------------------------------------------------

describe("useListenSession", () => {
  it("connects once, hidden and listen-only (no mic/camera/screen)", async () => {
    const { result } = renderHook(() => useListenSession("s-1"));
    await waitFor(() => expect(currentSession.start).toHaveBeenCalledTimes(1));
    expect(currentSession.start).toHaveBeenCalledWith(
      expect.objectContaining({
        tracks: { microphone: { enabled: false }, camera: { enabled: false }, screenShare: { enabled: false } },
      }),
    );
    expect(result.current.phase).toBe("connecting");
  });

  it("mints a fresh listen token through the api boundary and reports its expiry", async () => {
    fetchListenTokenMock.mockResolvedValue({
      serverUrl: "wss://example.livekit.cloud",
      participantToken: "tok-1",
      roomName: "lkap-s1",
      participantName: "Supervisor",
      identity: "supervisor:u1",
      sessionId: "s-1",
      expiresAt: "2026-09-27T00:15:00Z",
    });
    renderHook(() => useListenSession("s-1"));
    await waitFor(() => expect(useSessionMock).toHaveBeenCalled());
    const tokenSource = useSessionMock.mock.calls[0]?.[0] as { fetch: (options: object) => Promise<unknown> };

    let credentials: unknown;
    await act(async () => {
      credentials = await tokenSource.fetch({});
    });
    expect(fetchListenTokenMock).toHaveBeenCalledWith("s-1");
    expect(credentials).toEqual({ serverUrl: "wss://example.livekit.cloud", participantToken: "tok-1" });
  });

  it("reports phase 'not_live' when the api answers 409 not_live", async () => {
    fetchListenTokenMock.mockRejectedValue(new ApiError(409, "not_live", "the session is not live"));
    const { result } = renderHook(() => useListenSession("s-1"));
    await waitFor(() => expect(useSessionMock).toHaveBeenCalled());
    const tokenSource = useSessionMock.mock.calls[0]?.[0] as { fetch: (options: object) => Promise<unknown> };

    await act(async () => {
      await tokenSource.fetch({}).catch(() => {});
    });
    expect(result.current.phase).toBe("not_live");
  });

  it("phase moves from connecting to live to disconnected as the room's connection state changes, and reconnect() starts again", async () => {
    currentSession = fakeUseSessionReturn({ connectionState: ConnectionState.Connecting, isConnected: false });
    const { result, rerender } = renderHook(() => useListenSession("s-1"));
    expect(result.current.phase).toBe("connecting");

    currentSession = fakeUseSessionReturn({ connectionState: ConnectionState.Connected, isConnected: true });
    rerender();
    expect(result.current.phase).toBe("live");

    // A drop after having connected — most likely the 15-minute token
    // expiring — needs a manual reconnect, not a silent auto-retry.
    currentSession = fakeUseSessionReturn({ connectionState: ConnectionState.Disconnected, isConnected: false });
    rerender();
    expect(result.current.phase).toBe("disconnected");

    act(() => result.current.reconnect());
    expect(currentSession.start).toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// LiveTabContent: the panel mirror and the transcript
// ---------------------------------------------------------------------------

describe("LiveTabContent", () => {
  it("mirrors the panel state read-only through the same block renderers the live call uses", () => {
    uiStateMock.mockReturnValue(uiStateWithHandoff({ status: "connected", target: "Claims desk", agent_name: "Priya" }));
    render(<LiveTabContent session={detail()} listen={listenReturn()} muted={false} onMutedChange={vi.fn()} />);
    expect(screen.getByText("You're talking to Priya.")).toBeTruthy();
  });

  it("shows the live phase as a status chip and the mute switch", () => {
    render(<LiveTabContent session={detail()} listen={listenReturn({ phase: "live" })} muted={false} onMutedChange={vi.fn()} />);
    expect(screen.getByText("Live")).toBeTruthy();
    expect(screen.getByRole("switch", { name: /mute the call audio/i })).toBeTruthy();
  });

  it("shows a Reconnect action once the listen link has dropped", () => {
    const reconnect = vi.fn();
    render(
      <LiveTabContent session={detail()} listen={listenReturn({ phase: "disconnected", reconnect })} muted={false} onMutedChange={vi.fn()} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Reconnect" }));
    expect(reconnect).toHaveBeenCalledTimes(1);
  });

  it("shows the supervisor activity feed once a whisper event has landed", () => {
    liveEventsItems.push({
      id: 1,
      ts: "2026-09-27T00:05:00Z",
      type: "supervisor_whisper",
      payload: { id: "w1", by: "Priya", text: "Offer the premium plan", applied: "note" },
    });
    render(<LiveTabContent session={detail()} listen={listenReturn()} muted={false} onMutedChange={vi.fn()} />);
    expect(screen.getByText(/Priya left a note for the agent: “Offer the premium plan”/)).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// WhisperBox: posts, clears, and confirms the first send
// ---------------------------------------------------------------------------

describe("WhisperBox", () => {
  it("asks for confirmation before the first whisper, then posts and clears the box", async () => {
    whisperMutateAsync.mockResolvedValue({ id: "w1", delivered_to: 1 });
    render(<WhisperBox sessionId="s-1" />);

    const textarea = screen.getByLabelText("Message to the agent");
    fireEvent.change(textarea, { target: { value: "Offer the premium plan" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    // The confirmation dialog, not a second send yet.
    expect(whisperMutateAsync).not.toHaveBeenCalled();
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Send" }));

    await waitFor(() => expect(whisperMutateAsync).toHaveBeenCalledWith({ text: "Offer the premium plan", reply_now: false }));
    await waitFor(() => expect((textarea as HTMLTextAreaElement).value).toBe(""));
    expect(screen.getByText("Sent to the agent.")).toBeTruthy();
  });

  it("sends later whispers in the same tab without asking again", async () => {
    whisperMutateAsync.mockResolvedValue({ id: "w1", delivered_to: 1 });
    render(<WhisperBox sessionId="s-1" />);

    fireEvent.change(screen.getByLabelText("Message to the agent"), { target: { value: "First" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Send" }));
    await waitFor(() => expect(whisperMutateAsync).toHaveBeenCalledTimes(1));

    fireEvent.change(screen.getByLabelText("Message to the agent"), { target: { value: "Second" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    await waitFor(() => expect(whisperMutateAsync).toHaveBeenCalledTimes(2));
  });

  it("shows the api's 'no agent' message inline on a 409", async () => {
    whisperMutateAsync.mockRejectedValue(new ApiError(409, "no_agent", "no agent is in the room"));
    render(<WhisperBox sessionId="s-1" />);
    fireEvent.change(screen.getByLabelText("Message to the agent"), { target: { value: "Hello" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Send" }));
    expect(await screen.findByText(/No agent is in the room right now/)).toBeTruthy();
  });

  it("keeps Send disabled with an empty or whitespace-only message", () => {
    render(<WhisperBox sessionId="s-1" />);
    const sendButton = screen.getByRole("button", { name: "Send" });
    expect((sendButton as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Message to the agent"), { target: { value: "   " } });
    expect((sendButton as HTMLButtonElement).disabled).toBe(true);
  });
});
