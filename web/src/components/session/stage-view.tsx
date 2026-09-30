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
 * - `videoTrack` present → the video fills the well, sized to the track's own
 *   aspect (V6-26 — portrait, square or landscape, never a forced 16:9) and
 *   the meter steps aside; the state caption becomes a corner chip.
 * - `framing` / `fit` / `declaredAspect` (V6-26, `AvatarOptions` +
 *   `ProviderCapabilities.avatar_aspect`) steer that sizing before and after
 *   the first frame arrives — see `avatar-framing.ts`. Nothing set (the
 *   default for every agent stored before V6-26) renders `auto` + `contain`:
 *   the whole avatar visible, letterboxed, never cropped.
 * - `localTrack` present → self-view PiP (tap to enlarge / swap). This is the
 *   visitor's own camera, not an avatar, so it keeps its fixed tile shape.
 * - `compact` → the wide-layout rail: a 72 px strip on phones, a 200 px rail
 *   on desktop. The shell owns the box; the stage only fills it.
 * - Everything is driven by `agentState`; there is no internal state machine
 *   beyond the self-view / video-enlarge toggles.
 */
import * as React from "react";
import { useState } from "react";
import { VideoTrack, type TrackReference } from "@livekit/components-react";
import { PhoneOffIcon, RefreshCwIcon, Volume2Icon } from "lucide-react";

import { Icon } from "@/components/shared/icon";
import { StateMeter } from "@/components/shared/state-meter";
import {
  toMeterState,
  type AgentUiState,
} from "@/components/shared/agent-state";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { AGENT_STATE_CAPTION, formatElapsed } from "@/components/session/session-state";
import { PHONE_TOUCH_TARGET } from "@/components/session/session-layout";
import {
  aspectRatioStyle,
  resolveFrame,
  useMeasuredVideoAspect,
  type AvatarFit,
  type AvatarFraming,
} from "@/components/session/avatar-framing";
import { cn } from "@/lib/utils";

export interface StageViewProps {
  /** The §5.4 state model. Drives the meter, the caption and the overlays. */
  agentState: AgentUiState;
  agentName: string;
  /** Avatar (or any agent-published) video. Replaces the meter when present. */
  videoTrack?: TrackReference;
  /**
   * `AvatarOptions.framing` (V6-26). `undefined`/`"auto"` defers to
   * `declaredAspect`, then a 16:9 guess — never a crop. An explicit value
   * overrides the track's measured aspect only until a real frame arrives.
   */
  framing?: AvatarFraming | null;
  /** `AvatarOptions.fit` (V6-26). `undefined` renders like `"contain"`. */
  fit?: AvatarFit | null;
  /**
   * `ProviderCapabilities.avatar_aspect` for the selected avatar provider
   * (V6-26): a pre-connect hint so the well doesn't jump once the first
   * frame's real aspect arrives and turns out to agree with it.
   */
  declaredAspect?: AvatarFraming | null;
  /**
   * V6-26: called with the resolved well aspect ratio whenever it changes
   * (a guess, then the real measured one). A caller with no shell box of its
   * own — the `video` block, which sits inline in a panel — uses this to
   * size its own wrapper instead of a hard-coded `aspect-video`.
   */
  onAspectChange?: (aspectRatio: number) => void;
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
  framing,
  fit,
  declaredAspect,
  onAspectChange,
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
  // §5.4 as amended by R-V2-19 (WCAG 1.4.3): while the room reconnects only
  // the stage *media* dims — the video and the meter. The agent name and the
  // "Reconnecting…" caption (a live announcement) keep full contrast.
  const dimmed = agentState === "reconnecting";
  const mediaDim = dimmed
    ? "opacity-60 transition-opacity duration-(--duration-slow)"
    : "transition-opacity duration-(--duration-slow)";

  // V6-26: the well's own aspect and the video's fit/position, from the real
  // track dimensions once known (see `avatar-framing.ts` for the fallback
  // order). `videoTrack?.publication?.trackSid` identifies the track so a
  // swapped avatar never briefly renders at the previous one's measurement.
  const [videoRef, measured] = useMeasuredVideoAspect(videoTrack?.publication.trackSid);
  const frame = resolveFrame({
    framing,
    fit,
    declaredAspect,
    measured,
    forceCover: videoExpanded,
  });
  // The compact rail sizes itself from the well's own aspect (bounded by the
  // rail's fixed height); every other layout fills the box the shell gives
  // it and lets `objectFit` reconcile any mismatch.
  const compactUnexpanded = compact && !videoExpanded;
  const resolvedAspectRatio = frame.aspectRatio;
  React.useEffect(() => {
    onAspectChange?.(resolvedAspectRatio);
  }, [resolvedAspectRatio, onAspectChange]);

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
          data-fit={frame.objectFit}
          data-aspect={aspectRatioStyle(frame.aspectRatio)}
          data-measured={frame.measured ? "" : undefined}
          style={compactUnexpanded ? { aspectRatio: aspectRatioStyle(frame.aspectRatio) } : undefined}
          className={cn(
            // The well fills a clipped stage, so its focus outline sits inside.
            "bg-stage cursor-pointer transition-opacity duration-(--duration-slow) focus-visible:-outline-offset-2",
            dimmed && "opacity-60",
            compactUnexpanded ? "mx-auto h-full max-w-full" : "size-full",
          )}
        >
          <VideoTrack
            data-testid="stage-video"
            ref={videoRef}
            trackRef={videoTrack}
            className="size-full"
            style={{ objectFit: frame.objectFit, objectPosition: frame.objectPosition }}
          />
        </button>
      ) : (
        <div
          className={cn(
            "flex min-w-0 items-center justify-center",
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
                className={cn("lg:hidden", mediaDim)}
              />
              <StateMeter
                state={meterState}
                size="lg"
                bars={5}
                level={level}
                className={cn("hidden lg:inline-flex", mediaDim)}
              />
            </>
          ) : (
            <StateMeter state={meterState} size="lg" bars={5} level={level} className={mediaDim} />
          )}
          <p
            className={cn(
              "min-w-0",
              compact ? "text-left lg:text-center" : "text-center",
            )}
          >
            <span className="text-foreground block truncate font-medium">
              {agentName}
            </span>
            <span
              data-testid="stage-caption"
              aria-live="polite"
              className="text-text-secondary text-body block"
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
          className="bg-scrim text-text-secondary text-caption absolute top-2 left-2 rounded-sm px-1.5 py-0.5 font-mono tabular-nums"
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
            // `bg-black` is the video letterbox behind the camera frame, the one
            // allowlisted palette class on this surface (docs/ui/AUDIT.md S7,
            // scripts/design-lint-allowlist.txt). A floating tile, so it takes the
            // overlay shadow; the size change is instant (never animate width).
            "border-border shadow-overlay absolute right-3 bottom-3 overflow-hidden rounded border bg-black",
            selfViewLarge ? "w-1/2" : "w-24 sm:w-36",
          )}
        >
          <VideoTrack
            trackRef={localTrack}
            className="aspect-video w-full object-cover"
          />
          <span className="bg-scrim text-text-secondary text-caption absolute top-1 left-1 rounded-sm px-1 py-px leading-none font-medium">
            {localLabel}
          </span>
        </button>
      )}

      {/* §5.2 — playback is blocked until the visitor asks for it. */}
      {audioBlocked && !failed && (
        <div
          data-testid="audio-blocked-overlay"
          className="bg-scrim absolute inset-0 flex flex-col items-center justify-center gap-3 p-4 text-center backdrop-blur-sm"
        >
          <StateMeter state="ended" size={compact ? "md" : "lg"} />
          <Button
            variant="primary"
            size={compact ? "default" : "xl"}
            className={PHONE_TOUCH_TARGET}
            onClick={onEnableAudio}
          >
            <Icon as={Volume2Icon} size={compact ? "md" : "xl"} />
            Tap to hear {agentName}
          </Button>
        </div>
      )}

      {/* §5.4 — the agent never joined, or the connect call failed: say so,
          then the next step (Leave, then the primary Try again, last). The
          danger Alert is the live region (`role="alert"`). */}
      {failed && (
        <div
          data-testid="stage-failure-overlay"
          className="bg-stage absolute inset-0 flex flex-col items-center justify-center gap-3 overflow-y-auto p-4"
        >
          <Alert
            tone="danger"
            title={<>{agentName} couldn&rsquo;t join the call</>}
            className="text-body w-auto max-w-[46ch]"
          >
            {failureReasons && failureReasons.length > 0
              ? failureReasons.join("; ")
              : "Check your connection, then try again."}
          </Alert>
          <div className="flex flex-wrap items-center justify-center gap-2">
            {onLeave && (
              <Button
                variant="secondary"
                size={compact ? "default" : "lg"}
                className={PHONE_TOUCH_TARGET}
                onClick={onLeave}
              >
                <Icon as={PhoneOffIcon} size="md" />
                Leave
              </Button>
            )}
            {onRetry && (
              <Button
                variant="primary"
                size={compact ? "default" : "lg"}
                className={PHONE_TOUCH_TARGET}
                onClick={onRetry}
              >
                <Icon as={RefreshCwIcon} size="md" />
                Try again
              </Button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
