"use client";

/**
 * Connection banner for the session surface (docs/UI_UX_SPEC.md §5.4;
 * docs/ui/DESIGN-SYSTEM.md section 8.8, "offline or unavailable").
 *
 * Two states get a banner, a slim warning strip with no button that says what
 * happened and what happens next: **reconnecting**, and **offline** (the
 * browser lost its network, which LiveKit only notices a moment later). Connecting is the
 * top-strip chip, a failure is the stage overlay, and the end of a call is the
 * end-of-call card, so the banner never competes with any of them.
 *
 * The `role="status"` wrapper is always mounted, so screen readers hear the
 * banner when it appears; the spinner is the shared primitive (decorative,
 * still under reduced motion).
 */
import * as React from "react";

import type { AgentUiState } from "@/components/shared/agent-state";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";

export interface ConnectionBannerProps {
  /** The §5.4 state model. */
  agentState: AgentUiState;
  /** The browser reports no network (`useOnlineStatus`). */
  offline?: boolean;
  className?: string;
}

export function ConnectionBanner({
  agentState,
  offline = false,
  className,
}: ConnectionBannerProps) {
  const reconnecting = agentState === "reconnecting";
  const ended = agentState === "ended" || agentState === "failed";
  const show = reconnecting || (offline && !ended);

  return (
    <div role="status" aria-live="polite" className="shrink-0">
      {show && (
        <div
          data-testid="connection-banner"
          className={cn(
            "border-warning-border bg-warning-subtle text-warning-text text-body flex items-center justify-center gap-2 border-y px-4 py-1.5 text-center",
            className,
          )}
        >
          <Spinner />
          <span>
            {offline
              ? "You’re offline. Reconnecting… your call continues when you’re back online."
              : "Connection lost. Reconnecting… your call continues when it’s back."}
          </span>
        </div>
      )}
    </div>
  );
}
