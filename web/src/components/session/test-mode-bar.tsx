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
        "border-border bg-muted text-text-secondary text-caption flex w-full shrink-0 items-center gap-2 border-b px-3 py-1.5 pt-[max(0.375rem,env(safe-area-inset-top,0px))] font-medium lg:px-4",
        className,
      )}
    >
      <StateMeter state="idle" size="xs" />
      <span>Test call · this agent is a draft</span>
      {backHref && (
        // 48 px tall below `lg` (a touch target), the slim bar's own height above.
        <a
          href={backHref}
          className="text-brand hover:text-brand-hover -my-1.5 ml-auto inline-flex min-h-12 items-center rounded-sm underline-offset-3 hover:underline lg:my-0 lg:min-h-0"
        >
          Back to editor
        </a>
      )}
    </div>
  );
}
