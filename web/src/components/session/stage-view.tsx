"use client";

/**
 * `StageView` — the session stage as a **pure presentational seam**
 * (docs/UI_UX_SPEC.md §5.3/§5.4, WP-8 card item 1).
 *
 * It renders every agent state, the avatar/self-view video, the audio-blocked
 * overlay and the failure overlay from props alone: no LiveKit hooks, no room,
 * no timers. `agent-stage.tsx` is the hook-wired container that maps
 * `useVoiceAssistant()` + the connection state onto these props; the preview
 * route (WP-10) drives the same props from a query string, and the v2 avatar /
 * video blocks (V2-11) and the embed layouts (V2-18) plug in here by supplying
 * `videoTrack` / `compact` rather than by forking the stage.
 *
 * Contract notes for anything plugging into this seam:
 * - `videoTrack` present → the video fills the well (`object-cover`) and the
 *   meter steps aside; the state caption becomes a corner chip.
 * - `localTrack` present → self-view PiP (tap to enlarge / swap).
 * - `compact` → the wide-layout rail: a 72 px strip on phones, a 200 px rail
 *   on desktop. The shell owns the box; the stage only fills it.
 * - Everything is driven by `agentState`; there is no internal state machine.
 */
import * as React from "react";
import { useState } from "react";
import { VideoTrack, type TrackReference } from "@livekit/components-react";
import { PhoneOffIcon, RotateCcwIcon, Volume2Icon } from "lucide-react";

import { Icon } from "@/components/shared/icon";
import { StateMeter } from "@/components/shared/state-meter";
import {
  toMeterState,
  type AgentUiState,
} from "@/components/shared/agent-state";
import { Button } from "@/components/ui/button";
import { AGENT_STATE_CAPTION, formatElapsed } from "@/components/session/session-state";
import { cn } from "@/lib/utils";

export interface StageViewProps {
  /** The §5.4 state model. Drives the meter, the caption and the overlays. */
  agentState: AgentUiState;
  agentName: string;
  /** Avatar (or any agent-published) video. Replaces the meter when present. */
  videoTrack?: TrackReference;
  /** The agent's audio track — kept in the contract for visualizer plug-ins. */
  audioTrack?: TrackReference;
  /** The local camera or screen-share track, shown as a self-view. */
  localTrack?: TrackReference;
  localLabel?: "You" | "Screen";
  /** Wide-layout rail (72 px strip on phones, 200 px rail on desktop). */
  compact?: boolean;
  /** Elapsed call time; rendered as a corner chip so it survives scrolling. */
  elapsedMs?: number;
  /** The browser is blocking playback: show the "Tap to hear" overlay (§5.2). */
  audioBlocked?: boolean;
  onEnableAudio?: () => void;
  /** 0–1 input level; drives the meter bars while the agent speaks. */
  level?: number;
  /** `useAgent().failureReasons`, shown inside the failure overlay. */
  failureReasons?: string[] | null;
  onRetry?: () => void;
  onLeave?: () => void;
  className?: string;
}

export function StageView({
  agentState,
  agentName,
  videoTrack,
  localTrack,
  localLabel = "You",
  compact = false,
  elapsedMs,
  audioBlocked = false,
  onEnableAudio,
  level,
  failureReasons,
  onRetry,
  onLeave,
  className,
}: StageViewProps) {
  const [selfViewLarge, setSelfViewLarge] = useState(false);
  const [videoExpanded, setVideoExpanded] = useState(false);
  const caption = audioBlocked
    ? "Muted by your browser — tap to enable sound"
    : AGENT_STATE_CAPTION[agentState];
  const meterState = toMeterState(agentState);
  const failed = agentState === "failed";
  // §5.4: the stage dims while the room reconnects.
  const dimmed = agentState === "reconnecting";

  return (
    <div
      data-testid="stage-view"
      data-slot="stage-view"
      data-state={agentState}
      data-compact={compact ? "" : undefined}
      className={cn(
        "relative flex size-full items-center justify-center overflow-hidden",
        className,
      )}
    >
      {videoTrack ? (
        <button
          type="button"
          aria-label={videoExpanded ? `Shrink ${agentName}` : `Enlarge ${agentName}`}
          onClick={() => setVideoExpanded((value) => !value)}
          className={cn(
            "focus-visible:ring-ring size-full cursor-pointer bg-black transition-opacity duration-(--dur-3) focus-visible:ring-2 focus-visible:outline-none",
            dimmed && "opacity-60",
            compact && !videoExpanded && "mx-auto aspect-video w-auto",
          )}
        >
          <VideoTrack
            data-testid="stage-video"
            trackRef={videoTrack}
            className="size-full object-cover"
          />
        </button>
      ) : (
        <div
          className={cn(
            "flex min-w-0 items-center justify-center transition-opacity duration-(--dur-3)",
            dimmed && "opacity-60",
            compact
              ? "w-full gap-3 px-4 lg:flex-col lg:gap-3 lg:px-6"
              : "flex-col gap-4 px-6 py-8",
          )}
        >
          {/* Two meters in compact mode: a small one for the phone strip, the
              stage meter from `lg` up. Only one is ever rendered visibly. */}
          {compact ? (
            <>
              <StateMeter
                state={meterState}
                size="md"
                level={level}
                className="lg:hidden"
              />
              <StateMeter
                state={meterState}
                size="lg"
                bars={5}
                level={level}
                className="hidden lg:inline-flex"
              />
            </>
          ) : (
            <StateMeter state={meterState} size="lg" bars={5} level={level} />
          )}
          <p
            className={cn(
              "min-w-0",
              compact ? "text-left lg:text-center" : "text-center",
            )}
          >
            <span className="text-foreground block truncate text-base font-medium">
              {agentName}
            </span>
            <span
              data-testid="stage-caption"
              aria-live="polite"
              className="text-muted-foreground block text-sm"
            >
              {caption}
            </span>
          </p>
        </div>
      )}

      {/* Elapsed time — mono, tabular, top-left so video never hides it. */}
      {elapsedMs !== undefined && videoTrack && !failed && (
        <span
          data-testid="stage-elapsed"
          className="bg-background/70 text-muted-foreground absolute top-2 left-2 rounded-xs px-1.5 py-0.5 font-mono text-xs tabular-nums"
        >
          {formatElapsed(elapsedMs)}
        </span>
      )}

      {/* Self-view: tap to enlarge (desktop) / swap focus (phones). */}
      {localTrack && (
        <button
          type="button"
          data-testid="local-preview"
          aria-pressed={selfViewLarge}
          aria-label={
            selfViewLarge ? "Shrink your video" : "Enlarge your video"
          }
          onClick={() => setSelfViewLarge((value) => !value)}
          className={cn(
            "border-border focus-visible:ring-ring absolute right-3 bottom-3 overflow-hidden rounded-md border bg-black shadow-md transition-[width] duration-(--dur-3) ease-out focus-visible:ring-2 focus-visible:outline-none motion-reduce:transition-none",
            selfViewLarge ? "w-1/2" : "w-24 sm:w-36",
          )}
        >
          <VideoTrack
            trackRef={localTrack}
            className="aspect-video w-full object-cover"
          />
          <span className="bg-background/80 text-muted-foreground absolute top-1 left-1 rounded-xs px-1 py-px text-[0.6875rem] leading-[0.875rem] font-medium">
            {localLabel}
          </span>
        </button>
      )}

      {/* §5.2 — playback is blocked until the visitor asks for it. */}
      {audioBlocked && !failed && (
        <div
          data-testid="audio-blocked-overlay"
          className="bg-stage/85 absolute inset-0 flex flex-col items-center justify-center gap-3 p-4 text-center backdrop-blur-[2px]"
        >
          <StateMeter state="ended" size={compact ? "md" : "lg"} />
          <Button
            variant="brand"
            size={compact ? "default" : "xl"}
            onClick={onEnableAudio}
          >
            <Icon as={Volume2Icon} size={compact ? "md" : "xl"} />
            Tap to hear {agentName}
          </Button>
        </div>
      )}

      {/* §5.4 — the agent never joined, or the connect call failed. */}
      {failed && (
        <div
          data-testid="stage-failure-overlay"
          role="alert"
          className="bg-stage absolute inset-0 flex flex-col items-center justify-center p-4"
        >
          <div className="bg-danger-soft text-danger-text flex flex-col items-center gap-3 rounded-lg p-4 text-center">
          <p className="max-w-[46ch]">
            <span className="block text-base font-medium">
              {agentName} couldn&rsquo;t join the call
            </span>
            {failureReasons && failureReasons.length > 0 && (
              <span className="mt-1 block text-sm">
                {failureReasons.join("; ")}
              </span>
            )}
          </p>
          <div className="flex flex-wrap items-center justify-center gap-2">
            {onRetry && (
              <Button variant="brand" size={compact ? "default" : "lg"} onClick={onRetry}>
                <Icon as={RotateCcwIcon} size="md" />
                Try again
              </Button>
            )}
            {onLeave && (
              <Button variant="secondary" size={compact ? "default" : "lg"} onClick={onLeave}>
                <Icon as={PhoneOffIcon} size="md" />
                Leave
              </Button>
            )}
          </div>
          </div>
        </div>
      )}
    </div>
  );
}
