"use client";

/**
 * The slim test-mode bar of docs/UI_UX_SPEC.md §5.1: it sits above the
 * pre-call card and above the in-call shell, and it is the *only* test-mode
 * chrome — end users never see a "draft agents allowed" badge.
 */
import * as React from "react";

import { StateMeter } from "@/components/shared/state-meter";
import { cn } from "@/lib/utils";

export interface TestModeBarProps {
  /** `/console/agents/<id>` — `AgentPublicOut` carries the id. */
  backHref?: string;
  className?: string;
}

export function TestModeBar({ backHref, className }: TestModeBarProps) {
  return (
    // A labelled region so the bar is inside a landmark wherever it renders —
    // above the pre-call / end-of-call `main` as well as inside the call's.
    <div
      data-testid="test-mode-bar"
      role="region"
      aria-label="Test call"
      className={cn(
        "bg-muted text-muted-foreground flex w-full shrink-0 items-center gap-2 px-3 py-1.5 text-xs font-medium lg:px-4",
        className,
      )}
    >
      <StateMeter state="idle" size="xs" />
      <span>Test call · this agent is a draft</span>
      {backHref && (
        <a
          href={backHref}
          className="text-brand-text focus-visible:ring-ring ml-auto rounded-xs underline-offset-4 hover:underline focus-visible:ring-2 focus-visible:outline-none"
        >
          Back to editor
        </a>
      )}
    </div>
  );
}
