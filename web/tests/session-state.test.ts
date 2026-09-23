import { describe, expect, it } from "vitest";

import {
  AGENT_STATE_CAPTION,
  expectations,
  formatCallDuration,
  formatElapsed,
  lockedControls,
  micPermissionHint,
  toAgentUiState,
  visibleControls,
} from "@/components/session/session-state";
import { AGENT_UI_STATES } from "@/components/shared/agent-state";

/**
 * The §5.4 state model is the contract `StageView` renders and WP-10 drives,
 * so every branch is pinned here rather than in a component test.
 */
describe("toAgentUiState", () => {
  const base = {
    connectionState: "connected" as const,
    agentState: "listening",
    hasConnected: true,
  };

  it("reports a connect error as failed whatever the room says", () => {
    expect(
      toAgentUiState({ ...base, error: "Could not reach the agent service." }),
    ).toBe("failed");
    expect(
      toAgentUiState({
        ...base,
        connectionState: "connecting",
        agentState: "connecting",
        hasConnected: false,
        error: "boom",
      }),
    ).toBe("failed");
  });

  it("reports the agent's own failure", () => {
    expect(toAgentUiState({ ...base, agentState: "failed" })).toBe("failed");
  });

  it("maps the room's transport states", () => {
    expect(
      toAgentUiState({ ...base, connectionState: "reconnecting" }),
    ).toBe("reconnecting");
    expect(
      toAgentUiState({ ...base, connectionState: "connecting", hasConnected: false }),
    ).toBe("connecting");
  });

  it("only reports `ended` after the room has been connected once", () => {
    expect(
      toAgentUiState({
        ...base,
        connectionState: "disconnected",
        hasConnected: true,
      }),
    ).toBe("ended");
    expect(
      toAgentUiState({
        ...base,
        connectionState: "disconnected",
        hasConnected: false,
      }),
    ).toBe("connecting");
  });

  it("passes the agent's live states through and treats idle as listening", () => {
    for (const state of ["listening", "thinking", "speaking"]) {
      expect(toAgentUiState({ ...base, agentState: state })).toBe(state);
    }
    expect(toAgentUiState({ ...base, agentState: "idle" })).toBe("listening");
  });

  it("reads a connected room whose agent is not ready yet as connecting", () => {
    for (const state of [
      "initializing",
      "pre-connect-buffering",
      "connecting",
      "disconnected",
    ]) {
      expect(toAgentUiState({ ...base, agentState: state })).toBe("connecting");
    }
  });

  it("has a caption for every state", () => {
    for (const state of AGENT_UI_STATES) {
      expect(AGENT_STATE_CAPTION[state].length).toBeGreaterThan(0);
    }
    expect(AGENT_STATE_CAPTION.connecting).toBe("Connecting…");
    expect(AGENT_STATE_CAPTION.listening).toBe("Listening");
  });
});

describe("control visibility and locking (§5.4)", () => {
  const capabilities = { camera: true, screen_share: true, chat_input: true };

  it("shows only the controls the agent allows", () => {
    expect(
      visibleControls({
        state: "listening",
        capabilities: { camera: false, screen_share: false, chat_input: true },
        screenShareSupported: true,
      }),
    ).toEqual({
      leave: true,
      microphone: true,
      camera: false,
      screenShare: false,
      chat: true,
    });
  });

  it("hides screen share where the browser cannot do it", () => {
    const controls = visibleControls({
      state: "listening",
      capabilities,
      screenShareSupported: false,
    });
    expect(controls.screenShare).toBe(false);
    expect(controls.camera).toBe(true);
  });

  it("leaves only Leave after a failure", () => {
    expect(
      visibleControls({ state: "failed", capabilities, screenShareSupported: true }),
    ).toEqual({
      leave: true,
      microphone: false,
      camera: false,
      screenShare: false,
      chat: false,
    });
  });

  it("keeps the microphone usable while connecting and reconnecting", () => {
    expect(lockedControls("connecting")).toEqual(["camera", "screenShare", "chat"]);
    expect(lockedControls("reconnecting")).toEqual(["camera", "screenShare", "chat"]);
    expect(lockedControls("listening")).toEqual([]);
    expect(lockedControls("speaking")).toEqual([]);
  });
});

describe("durations", () => {
  it("formats the in-call timer", () => {
    expect(formatElapsed(0)).toBe("0:00");
    expect(formatElapsed(9_000)).toBe("0:09");
    expect(formatElapsed(252_000)).toBe("4:12");
    expect(formatElapsed(3_912_000)).toBe("1:05:12");
    expect(formatElapsed(-5)).toBe("0:00");
  });

  it("formats the end-of-call sentence", () => {
    expect(formatCallDuration(45_000)).toBe("45 s");
    expect(formatCallDuration(252_000)).toBe("4 min 12 s");
    expect(formatCallDuration(3_912_000)).toBe("1 h 5 min");
  });
});

describe("pre-call expectations (§5.1)", () => {
  it("always starts with the voice line and adds one line per capability", () => {
    expect(expectations("Ada", {})).toEqual([
      { icon: "mic", text: "Ada speaks first and listens while you talk" },
    ]);
    const all = expectations("Ada", {
      camera: true,
      screen_share: true,
      chat_input: true,
    });
    expect(all.map((item) => item.icon)).toEqual([
      "mic",
      "video",
      "screen",
      "chat",
    ]);
  });
});

describe("micPermissionHint", () => {
  it("names the place the setting lives per browser", () => {
    expect(micPermissionHint("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)")).toMatch(
      /Settings › Safari › Microphone/,
    );
    expect(
      micPermissionHint(
        "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36",
      ),
    ).toMatch(/Chrome/);
    expect(
      micPermissionHint(
        "Mozilla/5.0 (Macintosh) AppleWebKit/605.1.15 Version/17.0 Safari/605.1.15",
      ),
    ).toMatch(/Safari › Settings for This Website/);
    expect(micPermissionHint("Mozilla/5.0 Firefox/130.0")).toMatch(/Firefox/);
    expect(micPermissionHint("something else")).toMatch(/browser's settings/);
  });
});
