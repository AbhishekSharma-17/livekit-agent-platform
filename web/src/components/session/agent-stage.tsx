"use client";

/**
 * Hook-wired container for the stage seam: resolves the agent's avatar video,
 * the agent audio level and the local self-view from LiveKit, then renders the
 * pure `StageView` (docs/UI_UX_SPEC.md §5.3, WP-8 card item 1).
 *
 * Everything that is *not* room state (the §5.4 `agentState`, the elapsed
 * timer, the audio-blocked flag, the retry/leave callbacks) is passed in by
 * `session-room.tsx`, so the stage stays renderable without a room.
 */
import * as React from "react";
import { useMemo } from "react";
import {
  useLocalParticipant,
  useTrackVolume,
  useVoiceAssistant,
  type TrackReference,
} from "@livekit/components-react";
import { Track } from "livekit-client";

import type { AgentUiState } from "@/components/shared/agent-state";
import { StageView } from "@/components/session/stage-view";

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

export interface AgentStageProps {
  agentName: string;
  agentState: AgentUiState;
  compact?: boolean;
  elapsedMs?: number;
  audioBlocked?: boolean;
  onEnableAudio?: () => void;
  failureReasons?: string[] | null;
  onRetry?: () => void;
  onLeave?: () => void;
}

export function AgentStage({
  agentName,
  agentState,
  compact = false,
  elapsedMs,
  audioBlocked,
  onEnableAudio,
  failureReasons,
  onRetry,
  onLeave,
}: AgentStageProps) {
  const { audioTrack, videoTrack } = useVoiceAssistant();
  const cameraTrack = useLocalTrackRef(Track.Source.Camera);
  const screenTrack = useLocalTrackRef(Track.Source.ScreenShare);
  const localTrack = screenTrack ?? cameraTrack;

  // §5.4: while the agent speaks the meter is driven by the audio level and
  // keeps the same geometry as its keyframed states.
  const volume = useTrackVolume(
    agentState === "speaking" ? audioTrack : undefined,
  );

  return (
    <StageView
      agentState={agentState}
      agentName={agentName}
      videoTrack={videoTrack}
      audioTrack={audioTrack}
      localTrack={localTrack}
      localLabel={screenTrack ? "Screen" : "You"}
      compact={compact}
      elapsedMs={elapsedMs}
      audioBlocked={audioBlocked}
      onEnableAudio={onEnableAudio}
      level={agentState === "speaking" ? volume : undefined}
      failureReasons={failureReasons}
      onRetry={onRetry}
      onLeave={onLeave}
    />
  );
}
