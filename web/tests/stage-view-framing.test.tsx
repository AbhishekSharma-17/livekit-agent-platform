import * as React from "react";

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { StageView } from "@/components/session/stage-view";
import { DEFAULT_ASPECT_RATIO } from "@/components/session/avatar-framing";

afterEach(cleanup);

// A real `<video>` so `videoWidth`/`videoHeight` + `loadedmetadata`/`resize`
// exercise `useMeasuredVideoAspect` the way the browser would; the standalone
// `stage-view.test.tsx` mock only needs a stand-in element, this one needs a
// forwarded ref the fake track can dispatch events on.
vi.mock("@livekit/components-react", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@livekit/components-react")>()),
  VideoTrack: React.forwardRef<HTMLVideoElement, { className?: string; style?: React.CSSProperties; "data-testid"?: string }>(
    function FakeVideoTrack(props, ref) {
      return <video ref={ref} data-testid={props["data-testid"]} className={props.className} style={props.style} />;
    },
  ),
}));

const VIDEO_TRACK = {
  participant: {},
  publication: { trackSid: "TR_fake" },
  source: "camera",
} as unknown as React.ComponentProps<typeof StageView>["videoTrack"];

/** Sets a jsdom `<video>`'s decoded dimensions and fires the events the hook listens for. */
function setVideoDimensions(video: HTMLVideoElement, width: number, height: number) {
  Object.defineProperty(video, "videoWidth", { value: width, configurable: true });
  Object.defineProperty(video, "videoHeight", { value: height, configurable: true });
  act(() => {
    fireEvent.loadedMetadata(video);
  });
}

describe("StageView avatar framing (V6-26) — contain never crops", () => {
  it.each([
    ["portrait (9:16)", 720, 1280],
    ["square (1:1)", 600, 600],
    ["landscape (16:9)", 1280, 720],
  ])("sizes the well to a real %s track and keeps contain (no crop)", (_label, width, height) => {
    render(<StageView agentState="listening" agentName="Ada" videoTrack={VIDEO_TRACK} />);
    const video = screen.getByTestId("stage-video") as HTMLVideoElement;
    setVideoDimensions(video, width, height);

    const well = screen.getByRole("button", { name: /enlarge ada/i });
    expect(well.getAttribute("data-measured")).toBe("");
    expect(Number(well.getAttribute("data-aspect"))).toBeCloseTo(width / height);
    // contain by default: the whole avatar is visible, never cropped.
    expect(well.getAttribute("data-fit")).toBe("contain");
    expect(video.style.objectFit).toBe("contain");
  });

  it("a stored agent with no framing/fit renders the crop-free auto+contain default before any measurement", () => {
    render(<StageView agentState="listening" agentName="Ada" videoTrack={VIDEO_TRACK} />);
    const well = screen.getByRole("button", { name: /enlarge ada/i });
    expect(well.getAttribute("data-fit")).toBe("contain");
    expect(Number(well.getAttribute("data-aspect"))).toBeCloseTo(DEFAULT_ASPECT_RATIO);
  });
});

describe("StageView avatar framing (V6-26) — cover keeps the face region", () => {
  it("cover biases its object-position to the upper third, never the center", () => {
    render(<StageView agentState="listening" agentName="Ada" videoTrack={VIDEO_TRACK} fit="cover" />);
    const video = screen.getByTestId("stage-video") as HTMLVideoElement;
    expect(video.style.objectFit).toBe("cover");
    expect(video.style.objectPosition).toBe("50% 33%");
  });

  it("tapping to enlarge forces cover regardless of the authored fit", () => {
    render(<StageView agentState="listening" agentName="Ada" videoTrack={VIDEO_TRACK} fit="contain" />);
    fireEvent.click(screen.getByRole("button", { name: /enlarge ada/i }));
    const video = screen.getByTestId("stage-video") as HTMLVideoElement;
    expect(video.style.objectFit).toBe("cover");
  });
});

describe("StageView avatar framing (V6-26) — no jump before the first frame", () => {
  it("a declared aspect's pre-connect guess matches the real measurement once it arrives", () => {
    const { rerender } = render(
      <StageView agentState="listening" agentName="Ada" videoTrack={VIDEO_TRACK} declaredAspect="portrait" />,
    );
    const well = screen.getByRole("button", { name: /enlarge ada/i });
    const beforeAspect = Number(well.getAttribute("data-aspect"));
    expect(beforeAspect).toBeCloseTo(9 / 16);

    const video = screen.getByTestId("stage-video") as HTMLVideoElement;
    setVideoDimensions(video, 720, 1280);
    rerender(<StageView agentState="listening" agentName="Ada" videoTrack={VIDEO_TRACK} declaredAspect="portrait" />);
    const afterAspect = Number(well.getAttribute("data-aspect"));
    expect(afterAspect).toBeCloseTo(beforeAspect, 2);
  });
});

describe("StageView avatar framing (V6-26) — mobile-safe compact rail", () => {
  it("bounds the well's width so a landscape avatar never overflows a narrow compact rail", () => {
    render(<StageView agentState="listening" agentName="Ada" videoTrack={VIDEO_TRACK} compact />);
    const well = screen.getByRole("button", { name: /enlarge ada/i });
    expect(well.className).toContain("max-w-full");
    expect(well.className).toContain("h-full");
  });
});
