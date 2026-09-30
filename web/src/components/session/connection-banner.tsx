"use client";

/**
 * Connection banner for the session surface (docs/UI_UX_SPEC.md §5.4;
 * docs/ui/DESIGN-SYSTEM.md section 8.8, "offline or unavailable").
 *
 * Only one state gets a banner: **reconnecting**, a slim warning strip with
 * no button that says what happened and what happens next. Connecting is the
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
  className?: string;
}

export function ConnectionBanner({
  agentState,
  className,
}: ConnectionBannerProps) {
  const reconnecting = agentState === "reconnecting";

  return (
    <div role="status" aria-live="polite" className="shrink-0">
      {reconnecting && (
        <div
          data-testid="connection-banner"
          className={cn(
            "border-warning-border bg-warning-subtle text-warning-text text-body flex items-center justify-center gap-2 border-y px-4 py-1.5 text-center",
            className,
          )}
        >
          <Spinner />
          <span>Connection lost. Reconnecting… your call continues when it&rsquo;s back.</span>
        </div>
      )}
    </div>
  );
}
