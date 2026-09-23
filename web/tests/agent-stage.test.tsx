import * as React from "react";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AgentStage } from "@/components/session/agent-stage";

afterEach(cleanup);

// The container's only job is mapping room hooks onto `StageView`; stub the
// hooks so the mapping is observable without a room.
vi.mock("@livekit/components-react", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@livekit/components-react")>()),
  useVoiceAssistant: () => ({
    audioTrack: undefined,
    videoTrack: { participant: {}, publication: {}, source: "camera" },
  }),
  useLocalParticipant: () => ({ localParticipant: { getTrackPublication: () => undefined } }),
  useTrackVolume: () => 0,
  VideoTrack: (props: { className?: string; "data-testid"?: string }) => (
    <video data-testid={props["data-testid"]} className={props.className} />
  ),
}));

/**
 * R-V2-16 (PLAN-V2 §8): when a panel `video` block shows the agent's avatar,
 * the stage must not decode the same track a second time.
 */
describe("AgentStage avatar video (R-V2-16)", () => {
  it("shows the agent's video on the stage by default", () => {
    render(<AgentStage agentName="Ada" agentState="listening" />);
    expect(screen.getByTestId("stage-video")).toBeTruthy();
  });

  it("falls back to the meter when a video block already renders the avatar", () => {
    render(<AgentStage agentName="Ada" agentState="listening" suppressAgentVideo />);
    expect(screen.queryByTestId("stage-video")).toBeNull();
    expect(screen.getByTestId("stage-view").querySelector('[data-slot="state-meter"]')).not.toBeNull();
  });
});
