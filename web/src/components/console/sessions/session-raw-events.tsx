"use client";

import * as React from "react";
import { BracesIcon } from "lucide-react";

import { CopyButton } from "@/components/shared/copy-button";
import { EmptyState } from "@/components/shared/empty-state";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import type { SessionDetailOut, SessionEventOut } from "@/contracts/lkap-contracts";
import { formatDateTime, toMillis } from "@/lib/format";

import { CodeBlock, DetailsDisclosure } from "./details-disclosure";
import { sessionOriginMs } from "./session-model";
import { TimelineSkeleton } from "./session-timeline";
import { formatOffset, prettyJson } from "./timeline-model";
import { EVENTS_MAX, useAllSessionEvents } from "./use-session-queries";

/**
 * Raw events tab (for engineers): every worker event in insertion order,
 * type and time on the row, the payload JSON behind "Details", and a copy of
 * the whole list as JSON.
 */
export function SessionRawEvents({ session }: { session: SessionDetailOut }) {
  const eventsQuery = useAllSessionEvents(session.id);

  if (eventsQuery.isLoading) return <TimelineSkeleton />;
  if (eventsQuery.isError) {
    return (
      <ErrorBanner
        message={`Couldn't load the events for this call — ${errorMessage(eventsQuery.error)}`}
        onRetry={() => eventsQuery.refetch()}
      />
    );
  }
  return (
    <RawEventsView
      session={session}
      events={eventsQuery.data?.items ?? []}
      truncated={eventsQuery.data?.truncated ?? false}
    />
  );
}

export function RawEventsView({
  session,
  events,
  truncated = false,
}: {
  session: SessionDetailOut;
  events: readonly SessionEventOut[];
  truncated?: boolean;
}) {
  const origin = sessionOriginMs(session);
  const json = React.useMemo(() => JSON.stringify(events, null, 2), [events]);

  if (events.length === 0) {
    return (
      <EmptyState
        icon={BracesIcon}
        title="No events recorded"
        description="The agent posts events while the call runs; this session has none."
      />
    );
  }

  return (
    <div data-slot="session-raw-events" className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">
          {events.length === 1 ? "1 event" : `${events.length.toLocaleString("en-GB")} events`}
          {truncated ? ` · showing the first ${EVENTS_MAX.toLocaleString("en-GB")}` : null}
        </p>
        <div className="flex items-center gap-1 text-xs text-muted-foreground">
          Copy all as JSON
          <CopyButton value={json} label="Copy all events as JSON" size="sm" />
        </div>
      </div>
      <ol aria-label="Raw events" className="divide-y divide-border rounded-lg border border-border bg-card">
        {events.map((event) => {
          const at = toMillis(event.ts);
          return (
            <li key={event.id} className="grid grid-cols-[3.5rem_minmax(0,1fr)] gap-3 px-4 py-2">
              <time
                dateTime={event.ts}
                title={formatDateTime(at, { seconds: true })}
                className="pt-0.5 font-mono text-xs tabular-nums text-muted-foreground"
              >
                {formatOffset(at, origin)}
              </time>
              <div className="min-w-0">
                <div className="flex flex-wrap items-baseline gap-x-2">
                  <span className="font-mono text-[0.8125rem] text-foreground">{event.type}</span>
                  <span className="font-mono text-[0.6875rem] tabular-nums text-muted-foreground">#{event.id}</span>
                </div>
                <DetailsDisclosure className="mt-1">
                  <CodeBlock value={prettyJson(event.payload)} />
                </DetailsDisclosure>
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
