import * as React from "react";

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { VideoBlock } from "@/panels/blocks/video";
import type { BlockRenderProps } from "@/panels/blocks/types";
import type { VideoBlockState } from "@/contracts/lkap-contracts";
import type { PanelProps } from "@/panels/registry";

afterEach(cleanup);

const AVATAR_TRACK = {
  participant: {},
  publication: { trackSid: "TR_avatar" },
  source: "camera",
};

// `video.tsx` renders through the same `StageView` seam as the session stage
// (V6-26); this fakes the room hooks `LiveVideo` calls so it takes the
// `agent_avatar` branch, and forwards a ref from `VideoTrack` the same way
// `stage-view-framing.test.tsx` does, to fire real `videoWidth`/`videoHeight`.
vi.mock("@livekit/components-react", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@livekit/components-react")>()),
  useMaybeRoomContext: () => ({}),
  useVoiceAssistant: () => ({ audioTrack: undefined, videoTrack: AVATAR_TRACK }),
  useLocalParticipant: () => ({ localParticipant: { getTrackPublication: () => undefined } }),
  useTracks: () => [],
  VideoTrack: React.forwardRef<HTMLVideoElement, { className?: string; style?: React.CSSProperties; "data-testid"?: string }>(
    function FakeVideoTrack(props, ref) {
      return <video ref={ref} data-testid={props["data-testid"]} className={props.className} style={props.style} />;
    },
  ),
}));

function setVideoDimensions(video: HTMLVideoElement, width: number, height: number) {
  Object.defineProperty(video, "videoWidth", { value: width, configurable: true });
  Object.defineProperty(video, "videoHeight", { value: height, configurable: true });
  act(() => {
    fireEvent.loadedMetadata(video);
  });
}

const PANEL = {
  state: { blocks: {} },
  assets: new Map(),
  agent: { name: "Ada" },
  sessionId: "s1",
  perform: async () => undefined,
  transcript: [],
  connectionState: "connected",
} as unknown as PanelProps;

function renderBlock(data: Partial<VideoBlockState> = {}) {
  const props: BlockRenderProps<VideoBlockState> = {
    spec: { id: "video-1", type: "video" },
    data: { source: "agent_avatar", muted: false, ...data },
    panel: PANEL,
    title: null,
  };
  return render(<VideoBlock {...props} />);
}

describe("video block avatar framing (V6-26)", () => {
  it.each([
    ["portrait", 720, 1280],
    ["square", 600, 600],
    ["landscape", 1280, 720],
  ])("shows the whole %s avatar (contain, no crop) once the real track dimensions arrive", (_label, width, height) => {
    renderBlock();
    const video = screen.getByTestId("stage-video") as HTMLVideoElement;
    setVideoDimensions(video, width, height);
    expect(video.style.objectFit).toBe("contain");
    const well = screen.getByRole("button", { name: /enlarge ada/i });
    expect(Number(well.getAttribute("data-aspect"))).toBeCloseTo(width / height);
  });

  it("keeps a 16:9 footprint (no layout jump) for a source with no declared aspect, before the first frame", () => {
    renderBlock();
    const wrapper = document.querySelector('[data-slot="block-video-track"]') as HTMLElement;
    expect(wrapper.style.aspectRatio).toBeTruthy();
    expect(Number(wrapper.style.aspectRatio)).toBeCloseTo(16 / 9);
  });

  it("updates the wrapper's own footprint to the real aspect once measured, so it matches what StageView renders", () => {
    renderBlock();
    const video = screen.getByTestId("stage-video") as HTMLVideoElement;
    setVideoDimensions(video, 720, 1280);
    const wrapper = document.querySelector('[data-slot="block-video-track"]') as HTMLElement;
    expect(Number(wrapper.style.aspectRatio)).toBeCloseTo(720 / 1280);
  });
});
