"use client";

/**
 * Connection banner for the session surface (docs/UI_UX_SPEC.md §5.4).
 *
 * Only one state gets a banner now: **reconnecting** — a slim warning strip
 * with no button. Connecting is the top-strip chip, a failure is the stage
 * overlay, and the end of a call is the end-of-call card, so the banner no
 * longer competes with any of them.
 */
import * as React from "react";
import { Loader2Icon } from "lucide-react";

import { Icon } from "@/components/shared/icon";
import type { AgentUiState } from "@/components/shared/agent-state";
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
            "bg-warning-soft text-warning-text flex items-center justify-center gap-2 px-4 py-1.5 text-sm",
            className,
          )}
        >
          <Icon as={Loader2Icon} size="md" className="animate-spin motion-reduce:animate-none" />
          <span>Connection lost — reconnecting</span>
        </div>
      )}
    </div>
  );
}
