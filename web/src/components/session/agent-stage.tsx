"use client";

/**
 * The session stage: the agent's avatar video when one is published
 * (`useVoiceAssistant().videoTrack` resolves the `lk.publish_on_behalf`
 * participant), otherwise an audio visualizer, plus a picture-in-picture tile
 * for whichever local video source is live.
 */
import * as React from "react";
import { useMemo } from "react";
import {
  useLocalParticipant,
  useVoiceAssistant,
  VideoTrack,
  type TrackReference,
} from "@livekit/components-react";
import { Track } from "livekit-client";

import { AgentAudioVisualizerBar } from "@/components/agents-ui/agent-audio-visualizer-bar";
import { cn } from "@/lib/utils";

/** Track reference for a local source, or `undefined` when not publishing. */
export function useLocalTrackRef(
  source: Track.Source,
): TrackReference | undefined {
  const { localParticipant } = useLocalParticipant();
  const publication = localParticipant.getTrackPublication(source);
  return useMemo(
    () =>
      publication && !publication.isMuted
        ? { source, participant: localParticipant, publication }
        : undefined,
    [source, publication, localParticipant],
  );
}

const STATE_CAPTION: Record<string, string> = {
  connecting: "Connecting…",
  "pre-connect-buffering": "Listening…",
  initializing: "Getting ready…",
  listening: "Listening",
  thinking: "Thinking…",
  speaking: "Speaking",
  failed: "Agent unavailable",
  disconnected: "Call ended",
};

export interface AgentStageProps {
  /** Agent display name, used as the caption when idle. */
  agentName: string;
  compact?: boolean;
}

export function AgentStage({ agentName, compact = false }: AgentStageProps) {
  const { state, audioTrack, videoTrack } = useVoiceAssistant();
  const cameraTrack = useLocalTrackRef(Track.Source.Camera);
  const screenTrack = useLocalTrackRef(Track.Source.ScreenShare);
  const localTrack = screenTrack ?? cameraTrack;

  return (
    <div
      data-testid="agent-stage"
      className="relative flex h-full w-full items-center justify-center overflow-hidden"
    >
      {videoTrack ? (
        <VideoTrack
          trackRef={videoTrack}
          className="h-full w-full bg-black object-cover"
        />
      ) : (
        <div className="flex flex-col items-center gap-4 px-6 py-8">
          <AgentAudioVisualizerBar
            state={state}
            audioTrack={audioTrack}
            barCount={5}
            size={compact ? "md" : "lg"}
            className={cn(
              "text-foreground",
              compact ? "h-16 gap-1.5" : "h-24 gap-2",
            )}
          >
            <span className="min-h-1.5 w-2 rounded-full bg-current/15 transition-colors duration-200 ease-linear data-[lk-highlighted=true]:bg-current" />
          </AgentAudioVisualizerBar>
          <p className="text-muted-foreground text-center text-xs">
            <span className="text-foreground font-medium">{agentName}</span>
            {" · "}
            {STATE_CAPTION[state] ?? state}
          </p>
        </div>
      )}

      {localTrack && (
        <div
          data-testid="local-preview"
          className="border-border/70 absolute right-3 bottom-3 w-28 overflow-hidden rounded-lg border bg-black shadow-lg sm:w-36"
        >
          <VideoTrack
            trackRef={localTrack}
            className="aspect-video w-full object-cover"
          />
          <span className="bg-background/80 text-muted-foreground absolute top-1 left-1 rounded px-1 py-px text-[0.6rem] font-medium">
            {screenTrack ? "Screen" : "You"}
          </span>
        </div>
      )}
    </div>
  );
}
