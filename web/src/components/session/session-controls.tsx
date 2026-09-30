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
  DEVICE_ERROR_MESSAGE,
  lockedControls,
  visibleControls,
  type ControlCapabilities,
  type SessionControlKey,
} from "@/components/session/session-state";
import { cn } from "@/lib/utils";

/**
 * Replaces the vendored `blue-500` "on" state with the brand tokens, and puts
 * "END CALL" on the destructive status tokens instead of the vendored
 * opacity tints (`bg-destructive/10`, `hover:bg-destructive/20`): the
 * `--destructive-subtle` fill with `--destructive-text` ink, a pair the
 * contrast check (`pnpm check:contrast`) holds at 4.5:1 in both themes. The
 * fill stays put on hover (a darker fill would cost contrast); the hover
 * affordance is a `--destructive-border` ring.
 *
 * Exported so the control bar's restyle can be proved in a preview/story
 * without a live room.
 */
export const CONTROL_BAR_BRAND_ON = [
  "[&_button[data-state=on]]:bg-brand-subtle!",
  "[&_button[data-state=on]]:text-brand!",
  "[&_button[data-state=on]]:border-brand-border!",
  "[&_button[data-state=on]]:ring-brand-border!",
  // the device-select half of the mic/camera split pill follows its toggle
  "[&_button[data-state=on]+button]:bg-brand-subtle!",
  "[&_button[data-state=on]+button]:text-brand!",
  // END CALL: status tokens at rest and on hover, the hover shown as a ring.
  "[&_button[data-variant=destructive]]:bg-destructive-subtle!",
  "[&_button[data-variant=destructive]]:text-destructive-text!",
  "[&_button[data-variant=destructive]]:hover:bg-destructive-subtle!",
  "[&_button[data-variant=destructive]]:hover:ring-2",
  "[&_button[data-variant=destructive]]:hover:ring-destructive-border",
].join(" ");

/**
 * 48 px touch targets below `lg` (docs/ui/DESIGN-SYSTEM.md section 10), where
 * the bar is fixed to the bottom of a phone or tablet. The vendored toggles
 * are 36 px; this lifts every control (and the split pill's device half) from
 * outside, and hands back the vendored sizes from `lg` up. The 88 px (64 px
 * embed) spacer under the scroll region in `session-shell.tsx` still clears
 * the taller bar.
 */
export const CONTROL_BAR_TOUCH = "max-lg:[&_button]:min-h-12 max-lg:[&_button]:min-w-12";

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

/**
 * A danger dot on a control whose device failed to start. The dot is never the
 * only signal: the error toast says what happened, and the wrapper keeps an
 * `sr-only` line naming every failed device while its dot shows.
 */
const ERROR_DOT: Record<"microphone" | "camera" | "screenShare", string> = {
  microphone:
    "[&_button[aria-label='Toggle_microphone']]:relative [&_button[aria-label='Toggle_microphone']]:after:absolute [&_button[aria-label='Toggle_microphone']]:after:top-1 [&_button[aria-label='Toggle_microphone']]:after:right-1 [&_button[aria-label='Toggle_microphone']]:after:size-1.5 [&_button[aria-label='Toggle_microphone']]:after:rounded-pill [&_button[aria-label='Toggle_microphone']]:after:bg-destructive-solid [&_button[aria-label='Toggle_microphone']]:after:content-['']",
  camera:
    "[&_button[aria-label='Toggle_camera']]:relative [&_button[aria-label='Toggle_camera']]:after:absolute [&_button[aria-label='Toggle_camera']]:after:top-1 [&_button[aria-label='Toggle_camera']]:after:right-1 [&_button[aria-label='Toggle_camera']]:after:size-1.5 [&_button[aria-label='Toggle_camera']]:after:rounded-pill [&_button[aria-label='Toggle_camera']]:after:bg-destructive-solid [&_button[aria-label='Toggle_camera']]:after:content-['']",
  screenShare:
    "[&_button[aria-label='Toggle_screen_share']]:relative [&_button[aria-label='Toggle_screen_share']]:after:absolute [&_button[aria-label='Toggle_screen_share']]:after:top-1 [&_button[aria-label='Toggle_screen_share']]:after:right-1 [&_button[aria-label='Toggle_screen_share']]:after:size-1.5 [&_button[aria-label='Toggle_screen_share']]:after:rounded-pill [&_button[aria-label='Toggle_screen_share']]:after:bg-destructive-solid [&_button[aria-label='Toggle_screen_share']]:after:content-['']",
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
  const failedDevices = (Object.keys(ERROR_DOT) as Array<keyof typeof ERROR_DOT>).filter(
    (key) => deviceErrors?.[key],
  );

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
          // fixed (floating) below `lg`, so it takes the overlay shadow there
          compact ? CONTROL_BAR_COMPACT : "max-lg:shadow-overlay",
          CONTROL_BAR_BRAND_ON,
          CONTROL_BAR_TOUCH,
          locked.map((key) => LOCKED_CLASS[key]),
          deviceErrors?.microphone && ERROR_DOT.microphone,
          deviceErrors?.camera && ERROR_DOT.camera,
          deviceErrors?.screenShare && ERROR_DOT.screenShare,
          className,
        )}
      />
      {failedDevices.length > 0 && (
        <span data-testid="device-error-text" className="sr-only">
          {failedDevices.map((key) => DEVICE_ERROR_MESSAGE[key]).join(". ")}
        </span>
      )}
    </div>
  );
}
