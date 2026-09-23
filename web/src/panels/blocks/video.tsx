"use client";

/**
 * `video` block — one video track in the panel (CONTRACTS-V2 §4.4):
 * `source` is `agent_avatar` (`useVoiceAssistant().videoTrack`),
 * `user_camera` / `user_screen` (the local tracks) or `track:<sid>` (any
 * subscribed track by sid).
 *
 * The pixels go through WP-8's `StageView` seam (`videoTrack` fills the well),
 * so the block and the stage draw video the same way. Outside a room — the
 * console preview, tests — there is no track to resolve, so the block shows
 * its placeholder and never calls a LiveKit hook.
 */
import * as React from "react";
import { useMemo } from "react";
import { useMaybeRoomContext, useTracks, useVoiceAssistant, type TrackReference } from "@livekit/components-react";
import { Track } from "livekit-client";
import { VideoIcon } from "lucide-react";

import { Icon } from "@/components/shared/icon";
import type { VideoBlockState } from "@/contracts/lkap-contracts";
import { useLocalTrackRef } from "@/components/session/agent-stage";
import { StageView } from "@/components/session/stage-view";

import { BlockFrame } from "./frame";
import type { BlockRenderProps } from "./types";

const WAITING: Record<string, string> = {
  agent_avatar: "The avatar's video appears here when it joins.",
  user_camera: "Turn on your camera to show it here.",
  user_screen: "Share your screen to show it here.",
};

function waitingCopy(source: string): string {
  return WAITING[source] ?? "The video appears here when it starts.";
}

function VideoPlaceholder({ source }: { source: string }) {
  return (
    <div
      data-slot="block-video-placeholder"
      className="bg-muted/40 text-muted-foreground flex aspect-video w-full flex-col items-center justify-center gap-2 rounded-md px-4 text-center text-sm"
    >
      <Icon as={VideoIcon} size="lg" />
      <p>{waitingCopy(source)}</p>
    </div>
  );
}

/** Resolves the track from the room; only mounted inside a LiveKit room. */
function LiveVideo({ source, agentName, reconnecting }: { source: string; agentName: string; reconnecting: boolean }) {
  const { videoTrack: avatarTrack } = useVoiceAssistant();
  const cameraTrack = useLocalTrackRef(Track.Source.Camera);
  const screenTrack = useLocalTrackRef(Track.Source.ScreenShare);
  const sid = source.startsWith("track:") ? source.slice("track:".length) : null;
  const all = useTracks([Track.Source.Camera, Track.Source.ScreenShare, Track.Source.Unknown], {
    onlySubscribed: true,
  });
  const bySid = useMemo(
    () => (sid ? all.find((ref): ref is TrackReference => ref.publication?.trackSid === sid) : undefined),
    [all, sid],
  );

  let track: TrackReference | undefined;
  if (source === "agent_avatar") track = avatarTrack;
  else if (source === "user_camera") track = cameraTrack;
  else if (source === "user_screen") track = screenTrack;
  else track = bySid;

  if (!track) return <VideoPlaceholder source={source} />;
  return (
    <div data-slot="block-video-track" className="aspect-video w-full overflow-hidden rounded-md bg-black">
      <StageView
        agentState={reconnecting ? "reconnecting" : "listening"}
        agentName={source === "agent_avatar" ? agentName : "Video"}
        videoTrack={track}
      />
    </div>
  );
}

export function VideoBlock({ spec, data, panel, title, highlighted }: BlockRenderProps<VideoBlockState>) {
  const room = useMaybeRoomContext();
  const source = typeof data.source === "string" && data.source ? data.source : "agent_avatar";
  return (
    <BlockFrame spec={spec} title={title} highlighted={highlighted}>
      <div data-source={source} data-muted={data.muted ? "true" : undefined}>
        {room ? (
          <LiveVideo
            source={source}
            agentName={panel.agent.name}
            reconnecting={panel.connectionState === "reconnecting"}
          />
        ) : (
          <VideoPlaceholder source={source} />
        )}
      </div>
    </BlockFrame>
  );
}

export default VideoBlock;
