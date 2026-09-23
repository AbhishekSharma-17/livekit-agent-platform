"use client";

/**
 * Wrapper around the **vendored** `AgentControlBar` (docs/UI_UX_SPEC.md §5.4).
 *
 * The vendored component is never forked: this file restyles it from the
 * outside with descendant arbitrary variants (replacing its hard-coded
 * `blue-500` "on" state with the brand tokens), hides the controls a state
 * does not allow, and makes the rest inert while the room is connecting or
 * reconnecting — the vendored bar takes no per-control `disabled`, so the
 * wrapper dims those controls and swallows their clicks in the capture phase.
 *
 * Device errors (`onDeviceError`) leave a danger dot on the offending control
 * until that control succeeds again.
 */
import * as React from "react";
import { useCallback } from "react";
import type { Track } from "livekit-client";

import type { AgentUiState } from "@/components/shared/agent-state";
import { AgentControlBar } from "@/components/agents-ui/agent-control-bar";
import {
  CONTROL_LABEL,
  lockedControls,
  visibleControls,
  type ControlCapabilities,
  type SessionControlKey,
} from "@/components/session/session-state";
import { cn } from "@/lib/utils";

/**
 * Replaces the vendored `blue-500` "on" state with the brand tokens, and
 * holds "END CALL" at its resting tint on hover: measured with culori,
 * `--destructive` on `destructive/10` over `--card` is 4.68:1 (passes §2.2's
 * 4.5:1) but the vendored `hover:bg-destructive/20` drops it to 4.07:1, so
 * the hover affordance becomes a ring instead of a darker fill.
 *
 * Exported so the control bar's restyle can be proved in a preview/story
 * without a live room.
 */
export const CONTROL_BAR_BRAND_ON = [
  "[&_button[data-state=on]]:bg-brand-soft!",
  "[&_button[data-state=on]]:text-brand-text!",
  "[&_button[data-state=on]]:border-brand-line!",
  "[&_button[data-state=on]]:ring-brand-line!",
  // the device-select half of the mic/camera split pill follows its toggle
  "[&_button[data-state=on]+button]:bg-brand-soft!",
  "[&_button[data-state=on]+button]:text-brand-text!",
  // END CALL: keep the 4.68:1 resting tint on hover, show the hover as a ring.
  "[&_button[data-variant=destructive]]:hover:bg-destructive/10!",
  "[&_button[data-variant=destructive]]:hover:ring-2",
  "[&_button[data-variant=destructive]]:hover:ring-destructive/40",
].join(" ");

/** Dim + inert, per control, while the call is not ready for it. */
const LOCKED_CLASS: Record<SessionControlKey, string> = {
  leave: "",
  microphone:
    "[&_button[aria-label='Toggle_microphone']]:pointer-events-none [&_button[aria-label='Toggle_microphone']]:opacity-40 [&_button[aria-label='Toggle_microphone']+button]:pointer-events-none [&_button[aria-label='Toggle_microphone']+button]:opacity-40",
  camera:
    "[&_button[aria-label='Toggle_camera']]:pointer-events-none [&_button[aria-label='Toggle_camera']]:opacity-40 [&_button[aria-label='Toggle_camera']+button]:pointer-events-none [&_button[aria-label='Toggle_camera']+button]:opacity-40",
  screenShare:
    "[&_button[aria-label='Toggle_screen_share']]:pointer-events-none [&_button[aria-label='Toggle_screen_share']]:opacity-40",
  chat: "[&_button[aria-label='Toggle_transcript']]:pointer-events-none [&_button[aria-label='Toggle_transcript']]:opacity-40",
};

/** A danger dot on a control whose device failed to start. */
const ERROR_DOT: Record<"microphone" | "camera" | "screenShare", string> = {
  microphone:
    "[&_button[aria-label='Toggle_microphone']]:relative [&_button[aria-label='Toggle_microphone']]:after:absolute [&_button[aria-label='Toggle_microphone']]:after:top-1 [&_button[aria-label='Toggle_microphone']]:after:right-1 [&_button[aria-label='Toggle_microphone']]:after:size-1.5 [&_button[aria-label='Toggle_microphone']]:after:rounded-full [&_button[aria-label='Toggle_microphone']]:after:bg-danger [&_button[aria-label='Toggle_microphone']]:after:content-['']",
  camera:
    "[&_button[aria-label='Toggle_camera']]:relative [&_button[aria-label='Toggle_camera']]:after:absolute [&_button[aria-label='Toggle_camera']]:after:top-1 [&_button[aria-label='Toggle_camera']]:after:right-1 [&_button[aria-label='Toggle_camera']]:after:size-1.5 [&_button[aria-label='Toggle_camera']]:after:rounded-full [&_button[aria-label='Toggle_camera']]:after:bg-danger [&_button[aria-label='Toggle_camera']]:after:content-['']",
  screenShare:
    "[&_button[aria-label='Toggle_screen_share']]:relative [&_button[aria-label='Toggle_screen_share']]:after:absolute [&_button[aria-label='Toggle_screen_share']]:after:top-1 [&_button[aria-label='Toggle_screen_share']]:after:right-1 [&_button[aria-label='Toggle_screen_share']]:after:size-1.5 [&_button[aria-label='Toggle_screen_share']]:after:rounded-full [&_button[aria-label='Toggle_screen_share']]:after:bg-danger [&_button[aria-label='Toggle_screen_share']]:after:content-['']",
};

export interface SessionControlsProps {
  agentState: AgentUiState;
  capabilities: ControlCapabilities;
  /** `false` on phones (no `getDisplayMedia`): the control is hidden. */
  screenShareSupported: boolean;
  isConnected: boolean;
  isChatOpen: boolean;
  onIsChatOpenChange: (open: boolean) => void;
  onDisconnect: () => void;
  onDeviceError: (error: { source: Track.Source; error: Error }) => void;
  /** Controls whose last device attempt failed. */
  deviceErrors?: Partial<Record<"microphone" | "camera" | "screenShare", boolean>>;
  /**
   * The embed's compact bar (ask V2-18-9): tighter padding and no drop
   * shadow, restyled from outside like the rest (the vendored bar is never
   * forked). The buttons keep their size — the session's 40 px hit targets.
   */
  compact?: boolean;
  className?: string;
}

/** The compact (embed) restyle of the vendored bar's own chrome. */
export const CONTROL_BAR_COMPACT = "p-1.5! shadow-none! drop-shadow-none! [&_button]:ring-offset-0";

export function SessionControls({
  agentState,
  capabilities,
  screenShareSupported,
  isConnected,
  isChatOpen,
  onIsChatOpenChange,
  onDisconnect,
  onDeviceError,
  deviceErrors,
  compact = false,
  className,
}: SessionControlsProps) {
  const visible = visibleControls({
    state: agentState,
    capabilities,
    screenShareSupported,
  });
  const locked = lockedControls(agentState);

  // The vendored bar has no per-control `disabled`; stop the click before it
  // reaches the toggle instead (keeps keyboard activation blocked too).
  const swallowLocked = useCallback(
    (event: React.MouseEvent<HTMLDivElement>) => {
      if (locked.length === 0) return;
      const button = (event.target as HTMLElement).closest("button");
      const label = button?.getAttribute("aria-label");
      if (!label) return;
      const lockedLabels = locked.map((key) =>
        key === "leave" ? "" : CONTROL_LABEL[key],
      );
      if (lockedLabels.includes(label)) {
        event.preventDefault();
        event.stopPropagation();
      }
    },
    [locked],
  );

  return (
    <div
      data-testid="session-control-bar"
      data-compact={compact ? "" : undefined}
      onClickCapture={swallowLocked}
    >
      <AgentControlBar
        variant="livekit"
        isConnected={isConnected}
        isChatOpen={isChatOpen}
        onIsChatOpenChange={onIsChatOpenChange}
        onDisconnect={onDisconnect}
        onDeviceError={onDeviceError}
        controls={{
          leave: visible.leave,
          microphone: visible.microphone,
          camera: visible.camera,
          screenShare: visible.screenShare,
          chat: visible.chat,
        }}
        className={cn(
          "bg-card! border-border!",
          compact ? CONTROL_BAR_COMPACT : "shadow-md",
          CONTROL_BAR_BRAND_ON,
          locked.map((key) => LOCKED_CLASS[key]),
          deviceErrors?.microphone && ERROR_DOT.microphone,
          deviceErrors?.camera && ERROR_DOT.camera,
          deviceErrors?.screenShare && ERROR_DOT.screenShare,
          className,
        )}
      />
    </div>
  );
}
