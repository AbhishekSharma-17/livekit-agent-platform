"use client";

import Link from "next/link";
import { HistoryIcon } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import { Avatar } from "@/components/shared/data-display";
import { EmptyState } from "@/components/shared/empty-state";
import { ListCard, ListCardRow } from "@/components/shared/list-card";
import { LoadingRegion } from "@/components/shared/loading-state";
import { RelativeTime } from "@/components/shared/relative-time";
import { StatusPill } from "@/components/shared/status-chip";
import { useSessions } from "@/components/console/lib/api-hooks";
import { ErrorBanner } from "@/components/console/shared/error-banner";
import { SESSION_STATUS_LABEL, sessionDurationMs, sessionStatusTone } from "@/components/console/sessions/session-model";
import { formatDuration } from "@/lib/format";

const ROWS = 8;

function RowsSkeleton() {
  return (
    <LoadingRegion label="Loading recent sessions">
      <ul className="divide-y divide-border overflow-hidden rounded-lg border border-border bg-card">
        {[0, 1, 2].map((index) => (
          <li key={index} className="flex min-h-14 items-center gap-3 px-4 py-3">
            <Skeleton className="size-7 rounded-pill" />
            <div className="flex flex-1 flex-col gap-1.5">
              <Skeleton className="h-3.5 w-40" />
              <Skeleton className="h-3 w-24" />
            </div>
            <Skeleton className="h-5 w-14 rounded-pill" />
          </li>
        ))}
      </ul>
    </LoadingRegion>
  );
}

/**
 * The Overview's recent-items list (docs/ui/DESIGN-SYSTEM.md section 7.4):
 * the newest sessions as list-card rows (agent, duration, when, status),
 * each opening the session, with "See all" to the sessions list.
 */
export function RecentSessions() {
  const { data, isLoading, isError, error, refetch } = useSessions(undefined, undefined, ROWS);
  const items = data?.items ?? [];

  return (
    <section id="recent-sessions" aria-labelledby="recent-sessions-title" className="flex flex-col gap-3.5">
      <div className="flex items-center justify-between gap-3">
        <h2 id="recent-sessions-title" className="text-title font-semibold tracking-[-0.008em] text-foreground">
          Recent sessions
        </h2>
        <Link href="/console/sessions" className="text-label font-medium text-brand hover:underline hover:underline-offset-[3px]">
          See all
        </Link>
      </div>
      {isLoading ? (
        <RowsSkeleton />
      ) : isError ? (
        <ErrorBanner error={error} context={{ action: "load recent sessions" }} onRetry={() => void refetch()} />
      ) : items.length === 0 ? (
        <EmptyState
          icon={HistoryIcon}
          title="No calls yet"
          description="Every test call and public call is recorded here with its transcript."
          action={
            <Link href="/console/agents" className="text-label font-medium text-brand hover:underline hover:underline-offset-[3px]">
              Open an agent
            </Link>
          }
        />
      ) : (
        <ListCard label="Recent sessions">
          {items.map((session) => {
            const duration = sessionDurationMs(session);
            const name = session.agent_name || "Unknown agent";
            return (
              <ListCardRow
                key={session.id}
                href={`/console/sessions/${session.id}`}
                leading={<Avatar name={name} />}
                title={name}
                meta={
                  <>
                    {duration === null ? "Not started" : formatDuration(duration)}
                    {" · "}
                    <RelativeTime iso={session.started_at ?? session.created_at} />
                  </>
                }
                trailing={
                  <StatusPill tone={sessionStatusTone(session)} size="sm">
                    {SESSION_STATUS_LABEL[session.status]}
                  </StatusPill>
                }
              />
            );
          })}
        </ListCard>
      )}
    </section>
  );
}
