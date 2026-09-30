"use client";

import * as React from "react";
import { MessageSquareTextIcon } from "lucide-react";

import { CopyButton } from "@/components/shared/copy-button";
import { EmptyState } from "@/components/shared/empty-state";
import { StatusPill } from "@/components/shared/status-chip";
import { ErrorBanner } from "@/components/console/shared/error-banner";
import type { SessionDetailOut } from "@/contracts/lkap-contracts";
import { formatDateTime } from "@/lib/format";

import { sessionOriginMs } from "./session-model";
import { TimelineSkeleton } from "./session-timeline";
import { collectTurns, formatOffset, type TurnEntry } from "./timeline-model";
import { useAllSessionEvents } from "./use-session-queries";

/**
 * Transcript tab: only the spoken turns, in order. Uses the stored
 * transcript; when the worker never posted one, falls back to the live
 * `user_turn`/`agent_turn` events (same rule as the timeline).
 */
export function SessionTranscript({ session }: { session: SessionDetailOut }) {
  const hasStored = (session.transcript?.length ?? 0) > 0;
  // Only needed for the fallback; react-query shares the fetch with the timeline.
  const eventsQuery = useAllSessionEvents(hasStored ? "" : session.id);

  if (!hasStored && eventsQuery.isLoading) return <TimelineSkeleton />;
  if (!hasStored && eventsQuery.isError) {
    return (
      <ErrorBanner
        error={eventsQuery.error}
        context={{ action: "load the events for this call" }}
        onRetry={() => void eventsQuery.refetch()}
      />
    );
  }

  const turns = collectTurns(session.transcript, eventsQuery.data?.items ?? []);
  return <TranscriptView session={session} turns={turns} fromEvents={!hasStored} />;
}

function speaker(turn: TurnEntry, agentName: string): string {
  return turn.role === "assistant" ? agentName : "You";
}

export function TranscriptView({
  session,
  turns,
  fromEvents,
}: {
  session: SessionDetailOut;
  turns: TurnEntry[];
  fromEvents: boolean;
}) {
  const agentName = session.agent_name || "Agent";
  const origin = sessionOriginMs(session);

  if (turns.length === 0) {
    return (
      <EmptyState
        icon={MessageSquareTextIcon}
        title="No transcript for this call"
        description="Nothing was said, or the agent never reported its turns."
      />
    );
  }

  const plainText = turns
    .map((turn) => `[${formatOffset(turn.at, origin)}] ${speaker(turn, agentName)}: ${turn.text}`)
    .join("\n");

  return (
    <div data-slot="session-transcript" className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-caption text-text-secondary">
          {turns.length === 1 ? "1 turn" : `${turns.length} turns`}
          {fromEvents ? " · rebuilt from live events (no final transcript was saved)" : null}
        </p>
        <div className="flex items-center gap-1 text-caption text-text-secondary">
          Copy transcript
          <CopyButton value={plainText} label="Copy transcript" size="sm" />
        </div>
      </div>
      <ol aria-label="Transcript" className="divide-y divide-border rounded-lg border border-border bg-card">
        {turns.map((turn) => (
          <li key={turn.key} className="grid grid-cols-[3.5rem_minmax(0,1fr)] gap-3 px-4 py-3">
            <time
              dateTime={new Date(turn.at).toISOString()}
              title={formatDateTime(turn.at, { seconds: true })}
              className="pt-0.5 font-mono text-caption tabular-nums text-text-secondary"
            >
              {formatOffset(turn.at, origin)}
            </time>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="text-caption font-medium text-foreground">{speaker(turn, agentName)}</span>
                {turn.interrupted ? (
                  <StatusPill tone="warning" size="sm">
                    Interrupted
                  </StatusPill>
                ) : null}
              </div>
              <p className="mt-0.5 text-body leading-relaxed whitespace-pre-wrap text-foreground">{turn.text}</p>
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}
