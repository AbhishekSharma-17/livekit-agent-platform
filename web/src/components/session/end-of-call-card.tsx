"use client";

/**
 * End of call (docs/UI_UX_SPEC.md §5.6): the meter at rest, how long the call
 * lasted, and one way back in. Nothing pack-specific this pass.
 *
 * The card replaces the call the caller just left, so its heading takes focus
 * when it appears: screen readers hear "Call ended" instead of silence, and
 * keyboard focus is not stranded on a control that no longer exists. One
 * primary action (Start another call), last.
 */
import * as React from "react";
import { useEffect, useRef } from "react";
import { PhoneIcon } from "lucide-react";

import { Icon } from "@/components/shared/icon";
import { StateMeter } from "@/components/shared/state-meter";
import { Button } from "@/components/ui/button";
import {
  SessionCard,
  SessionCardScreen,
} from "@/components/session/session-card";
import { PHONE_TOUCH_TARGET } from "@/components/session/session-layout";
import { TestModeBar } from "@/components/session/test-mode-bar";
import { formatCallDuration } from "@/components/session/session-state";

export interface EndOfCallCardProps {
  agentName: string;
  /** Wall-clock length of the call, from the in-call timer. */
  durationMs: number;
  onRestart: () => void;
  testMode?: boolean;
  /** `/console/agents/<id>` — only shown in test mode. */
  backHref?: string;
}

export function EndOfCallCard({
  agentName,
  durationMs,
  onRestart,
  testMode,
  backHref,
}: EndOfCallCardProps) {
  const headingRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    headingRef.current?.focus({ preventScroll: true });
  }, []);

  return (
    <SessionCardScreen
      // §5.6 makes "Back to editor" the card's own secondary action, so the
      // test bar keeps its label here but not its link (one way back, not two).
      top={testMode ? <TestModeBar /> : undefined}
    >
      <SessionCard data-testid="end-of-call-card">
        <div className="flex flex-col items-center gap-4 p-6 text-center">
          <StateMeter state="ended" size="lg" bars={5} />
          <div>
            <h1
              ref={headingRef}
              tabIndex={-1}
              className="text-display font-semibold tracking-[-0.025em] text-balance focus:outline-none"
            >
              Call ended
            </h1>
            <p className="text-text-secondary mt-2 text-pretty tabular-nums">
              You talked with {agentName} for {formatCallDuration(durationMs)}.
            </p>
          </div>
          <div className="mt-2 flex w-full flex-col gap-2">
            {testMode && backHref && (
              <Button variant="secondary" size="lg" className={PHONE_TOUCH_TARGET} asChild>
                <a href={backHref}>Back to editor</a>
              </Button>
            )}
            <Button
              variant="primary"
              size="xl"
              className={PHONE_TOUCH_TARGET}
              onClick={onRestart}
            >
              <Icon as={PhoneIcon} size="xl" />
              Start another call
            </Button>
          </div>
        </div>
      </SessionCard>
    </SessionCardScreen>
  );
}
