import * as React from "react";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { StageView } from "@/components/session/stage-view";
import { AGENT_STATE_CAPTION } from "@/components/session/session-state";
import {
  AGENT_UI_STATES,
  toMeterState,
} from "@/components/shared/agent-state";

afterEach(cleanup);

// `VideoTrack` subscribes to a real participant; the stage only needs a
// stand-in element to prove where the video sits and how it is styled.
vi.mock("@livekit/components-react", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@livekit/components-react")>()),
  VideoTrack: (props: { className?: string; "data-testid"?: string }) => (
    <video data-testid={props["data-testid"]} className={props.className} />
  ),
}));

/**
 * UI_UX_SPEC §5.3/§5.4 — every state of the stage must be reachable through
 * props alone (that is what makes the preview route and the avatar/video
 * plug-ins possible).
 */
describe("StageView states", () => {
  it.each(AGENT_UI_STATES)("renders %s with its caption and meter", (state) => {
    render(<StageView agentState={state} agentName="Ada" />);
    const stage = screen.getByTestId("stage-view");
    expect(stage.getAttribute("data-state")).toBe(state);
    expect(screen.getByTestId("stage-caption").textContent).toBe(
      AGENT_STATE_CAPTION[state],
    );
    const meters = stage.querySelectorAll('[data-slot="state-meter"]');
    expect(meters.length).toBeGreaterThan(0);
    // `reconnecting` draws the `connecting` meter (§5.4).
    expect(meters[0].getAttribute("data-state")).toBe(toMeterState(state));
  });

  it("names the agent on the stage", () => {
    render(<StageView agentState="listening" agentName="Ada" />);
    expect(screen.getByText("Ada")).toBeTruthy();
  });

  it("keeps the elapsed chip for the video stage only (the caption carries it otherwise)", () => {
    render(
      <StageView agentState="listening" agentName="Ada" elapsedMs={252_000} />,
    );
    expect(screen.queryByTestId("stage-elapsed")).toBeNull();
  });

  it("marks the compact rail", () => {
    render(<StageView agentState="listening" agentName="Ada" compact />);
    expect(screen.getByTestId("stage-view").hasAttribute("data-compact")).toBe(
      true,
    );
  });
});

describe("StageView audio-blocked overlay (§5.2)", () => {
  it("offers a brand button that calls back", () => {
    const onEnableAudio = vi.fn();
    render(
      <StageView
        agentState="listening"
        agentName="Ada"
        audioBlocked
        onEnableAudio={onEnableAudio}
      />,
    );
    expect(screen.getByTestId("audio-blocked-overlay")).toBeTruthy();
    expect(screen.getByTestId("stage-caption").textContent).toMatch(
      /Muted by your browser/,
    );
    fireEvent.click(screen.getByRole("button", { name: /tap to hear ada/i }));
    expect(onEnableAudio).toHaveBeenCalledTimes(1);
  });

  it("is absent while playback works", () => {
    render(<StageView agentState="listening" agentName="Ada" />);
    expect(screen.queryByTestId("audio-blocked-overlay")).toBeNull();
  });
});

describe("StageView failure overlay (§5.4)", () => {
  it("explains the failure and offers try again / leave", () => {
    const onRetry = vi.fn();
    const onLeave = vi.fn();
    render(
      <StageView
        agentState="failed"
        agentName="Ada"
        failureReasons={["agent did not join within 10s"]}
        onRetry={onRetry}
        onLeave={onLeave}
      />,
    );
    const overlay = screen.getByTestId("stage-failure-overlay");
    expect(overlay.textContent).toMatch(/Ada couldn’t join the call/);
    expect(overlay.textContent).toMatch(/agent did not join within 10s/);
    fireEvent.click(screen.getByRole("button", { name: /try again/i }));
    fireEvent.click(screen.getByRole("button", { name: /leave/i }));
    expect(onRetry).toHaveBeenCalledTimes(1);
    expect(onLeave).toHaveBeenCalledTimes(1);
  });

  it("covers the stage so the meter never shows through the failure", () => {
    render(<StageView agentState="failed" agentName="Ada" />);
    const overlay = screen.getByTestId("stage-failure-overlay");
    expect(overlay.className).toContain("bg-stage");
    expect(overlay.className).toContain("absolute inset-0");
  });
});

/**
 * R-V2-19 (PLAN-V2 §8; amends UI_UX_SPEC §5.4): while reconnecting only the
 * stage **media** dims. The caption is the `aria-live` announcement the user
 * must read and the agent name identifies the call, so neither may sit under
 * an `opacity-60` ancestor (WCAG 1.4.3 — axe measured 3.46:1 when it did).
 */
describe("StageView reconnecting dim (R-V2-19)", () => {
  it.each([false, true])(
    "dims the meter but keeps the caption and name at full contrast (compact=%s)",
    (compact) => {
      render(<StageView agentState="reconnecting" agentName="Ada" compact={compact} />);
      const caption = screen.getByTestId("stage-caption");
      expect(caption.closest(".opacity-60")).toBeNull();
      expect(screen.getByText("Ada").closest(".opacity-60")).toBeNull();
      const meters = screen
        .getByTestId("stage-view")
        .querySelectorAll('[data-slot="state-meter"]');
      expect(meters.length).toBeGreaterThan(0);
      for (const meter of meters) {
        expect(meter.closest(".opacity-60")).not.toBeNull();
      }
    },
  );

  it("dims the avatar video while reconnecting", () => {
    const videoTrack = {
      participant: {},
      publication: {},
      source: "camera",
    } as unknown as React.ComponentProps<typeof StageView>["videoTrack"];
    render(<StageView agentState="reconnecting" agentName="Ada" videoTrack={videoTrack} />);
    const video = screen.getByRole("button", { name: /enlarge ada/i });
    expect(video.className).toContain("opacity-60");
  });

  it("does not dim anything in a settled state", () => {
    render(<StageView agentState="listening" agentName="Ada" />);
    expect(screen.getByTestId("stage-view").querySelector(".opacity-60")).toBeNull();
  });
});
