"use client";

/**
 * End of call (docs/UI_UX_SPEC.md §5.6): the meter at rest, how long the call
 * lasted, and one way back in. Nothing pack-specific this pass.
 */
import * as React from "react";
import { PhoneIcon } from "lucide-react";

import { Icon } from "@/components/shared/icon";
import { StateMeter } from "@/components/shared/state-meter";
import { Button } from "@/components/ui/button";
import {
  SessionCard,
  SessionCardScreen,
} from "@/components/session/session-card";
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
            <h1 className="text-[1.75rem] leading-[2.125rem] font-semibold tracking-[-0.02em] text-balance">
              Call ended
            </h1>
            <p className="text-muted-foreground mt-2 text-base text-pretty">
              You talked with {agentName} for {formatCallDuration(durationMs)}.
            </p>
          </div>
          <div className="mt-2 flex w-full flex-col gap-2">
            <Button variant="brand" size="xl" onClick={onRestart}>
              <Icon as={PhoneIcon} size="xl" />
              Start another call
            </Button>
            {testMode && backHref && (
              <Button variant="secondary" size="lg" asChild>
                <a href={backHref}>Back to editor</a>
              </Button>
            )}
          </div>
        </div>
      </SessionCard>
    </SessionCardScreen>
  );
}
