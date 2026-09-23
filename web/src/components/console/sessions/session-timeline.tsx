"use client";

import * as React from "react";
import { ChevronDownIcon, ChevronRightIcon, CircleDotIcon, ListTreeIcon, WrenchIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/shared/empty-state";
import { Icon } from "@/components/shared/icon";
import { StateMeter } from "@/components/shared/state-meter";
import { StatusChip } from "@/components/shared/status-chip";
import type { MeterState } from "@/components/shared/agent-state";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import type { SessionDetailOut, SessionEventOut } from "@/contracts/lkap-contracts";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

import { formatMs } from "./detail/builtin-event-kinds";
import { sessionEventKinds } from "./detail/resolved";
import type { TimelineEventKind, TimelineFilter, TimelineTone } from "./detail/types";
import { CodeBlock, DetailsDisclosure } from "./details-disclosure";
import {
  ALL_FILTERS,
  buildTimeline,
  formatOffset,
  prettyJson,
  stateWord,
  summarizeStates,
  TIMELINE_FILTERS,
  toolStatus,
  type EventEntry,
  type StateEntry,
  type TimelineRow,
  type ToolEntry,
  type TurnEntry,
} from "./timeline-model";
import { useAllSessionEvents } from "./use-session-queries";

/**
 * Timeline tab (docs/UI_UX_SPEC.md §4.10, §7.8 item 2): one column a reviewer
 * reads top to bottom. The merge/pair/fold logic is `./timeline-model.ts`.
 */
export function SessionTimeline({
  session,
  eventKinds,
}: {
  session: SessionDetailOut;
  /** Defaults to the registry (built-ins + extensions); tests pass their own. */
  eventKinds?: ReadonlyMap<string, TimelineEventKind>;
}) {
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
    <TimelineView
      session={session}
      events={eventsQuery.data?.items ?? []}
      eventKinds={eventKinds ?? sessionEventKinds()}
    />
  );
}

export function TimelineSkeleton() {
  return (
    <div className="space-y-3" aria-hidden="true">
      {[0, 1, 2, 3, 4].map((i) => (
        <div key={i} className="grid grid-cols-[3.5rem_minmax(0,1fr)] gap-3">
          <Skeleton className="h-4 w-10" />
          <Skeleton className="h-12 w-full" />
        </div>
      ))}
    </div>
  );
}

export function TimelineView({
  session,
  events,
  eventKinds,
}: {
  session: SessionDetailOut;
  events: readonly SessionEventOut[];
  eventKinds: ReadonlyMap<string, TimelineEventKind>;
}) {
  const [active, setActive] = React.useState<ReadonlySet<TimelineFilter>>(ALL_FILTERS);
  const { rows, counts, origin, entries } = React.useMemo(
    () => buildTimeline(session, events, eventKinds, active),
    [session, events, eventKinds, active],
  );

  if (entries.length === 0) {
    return (
      <EmptyState
        icon={ListTreeIcon}
        title="Nothing was recorded for this call"
        description="Turns, tool calls and agent state appear here once the agent joins and reports them."
      />
    );
  }

  const toggle = (filter: TimelineFilter) => {
    setActive((current) => {
      const next = new Set(current);
      if (next.has(filter)) next.delete(filter);
      else next.add(filter);
      return next;
    });
  };

  const minutes = rows.filter((row): row is Extract<TimelineRow, { kind: "minute" }> => row.kind === "minute");
  const agentName = session.agent_name || "Agent";

  return (
    <div data-slot="session-timeline" className="space-y-3">
      <div className="sticky top-[var(--console-topbar-height,3.5rem)] z-10 -mx-1 flex flex-col gap-2 bg-background px-1 py-2 lg:top-[var(--console-topbar-height,3rem)]">
        <div role="group" aria-label="Show in timeline" className="flex flex-wrap items-center gap-1.5">
          {TIMELINE_FILTERS.map((filter) => {
            const on = active.has(filter.id);
            return (
              <Button
                key={filter.id}
                type="button"
                size="sm"
                variant={on ? "secondary" : "ghost"}
                aria-pressed={on}
                disabled={counts[filter.id] === 0}
                onClick={() => toggle(filter.id)}
                className="h-7 gap-1.5 px-2.5 text-xs"
              >
                {filter.label}
                <span className="font-mono text-[0.6875rem] tabular-nums text-muted-foreground">{counts[filter.id]}</span>
              </Button>
            );
          })}
        </div>
        {minutes.length > 1 ? (
          <nav aria-label="Jump to minute" className="flex items-center gap-1 overflow-x-auto pb-0.5">
            <span className="mr-1 shrink-0 text-[0.6875rem] text-muted-foreground">Jump to</span>
            {minutes.map((marker) => (
              <a
                key={marker.key}
                href={`#timeline-minute-${marker.minute}`}
                className="shrink-0 rounded-xs px-1.5 py-0.5 font-mono text-[0.6875rem] tabular-nums text-muted-foreground outline-none hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
              >
                {formatOffset(marker.at, origin)}
              </a>
            ))}
          </nav>
        ) : null}
      </div>

      {rows.length === 0 ? (
        <EmptyState
          compact
          title="No rows match these filters"
          action={
            <Button type="button" variant="ghost" size="sm" onClick={() => setActive(ALL_FILTERS)}>
              Show everything
            </Button>
          }
        />
      ) : (
        <ol aria-label="Call timeline" className="flex flex-col">
          {rows.map((row) => (
            <TimelineRowView key={row.key} row={row} origin={origin} agentName={agentName} />
          ))}
        </ol>
      )}
    </div>
  );
}

function TimelineRowView({ row, origin, agentName }: { row: TimelineRow; origin: number; agentName: string }) {
  switch (row.kind) {
    case "minute":
      return (
        <li
          id={`timeline-minute-${row.minute}`}
          className="flex scroll-mt-32 items-center gap-3 pt-3 pb-1 first:pt-0"
          aria-label={`Minute ${row.minute}`}
        >
          <span className="font-mono text-[0.6875rem] font-medium tabular-nums text-muted-foreground">
            {formatOffset(row.at, origin)}
          </span>
          <span aria-hidden="true" className="h-px flex-1 bg-border" />
        </li>
      );
    case "turn":
      return <TurnRow entry={row} origin={origin} agentName={agentName} />;
    case "tool":
      return <ToolRow entry={row} origin={origin} />;
    case "state":
      return <StateTrack entry={row} origin={origin} />;
    case "event":
      return <EventRow entry={row} origin={origin} />;
  }
}

function RowFrame({
  at,
  origin,
  tone,
  thin,
  children,
  testId,
}: {
  at: number;
  origin: number;
  tone?: TimelineTone;
  thin?: boolean;
  children: React.ReactNode;
  testId: string;
}) {
  return (
    <li data-row={testId} className={cn("grid grid-cols-[3.5rem_minmax(0,1fr)] gap-3", thin ? "py-1" : "py-2")}>
      <time
        dateTime={new Date(at).toISOString()}
        title={formatDateTime(at, { seconds: true })}
        className={cn("pt-0.5 font-mono text-xs tabular-nums text-muted-foreground", thin && "pt-0")}
      >
        {formatOffset(at, origin)}
      </time>
      <div
        className={cn(
          "min-w-0",
          tone === "danger" && "rounded-md bg-danger-soft px-3 py-2 text-danger-text",
          tone === "warning" && "rounded-md bg-warning-soft px-3 py-2 text-warning-text",
        )}
      >
        {children}
      </div>
    </li>
  );
}

function TurnRow({ entry, origin, agentName }: { entry: TurnEntry; origin: number; agentName: string }) {
  const isAgent = entry.role === "assistant";
  return (
    <RowFrame at={entry.at} origin={origin} testId="turn">
      <div className="flex flex-wrap items-center gap-1.5">
        <StatusChip tone={isAgent ? "info" : "neutral"} size="sm">
          {isAgent ? agentName : "You"}
        </StatusChip>
        {entry.interrupted ? (
          <StatusChip tone="warning" size="sm">
            Interrupted
          </StatusChip>
        ) : null}
      </div>
      <p className="mt-1 text-sm leading-relaxed whitespace-pre-wrap text-foreground">{entry.text}</p>
    </RowFrame>
  );
}

function ToolRow({ entry, origin }: { entry: ToolEntry; origin: number }) {
  const status = toolStatus(entry.status);
  const duration = formatMs(entry.durationMs);
  const hasDetails = entry.args !== null || entry.result !== null;
  return (
    <RowFrame at={entry.at} origin={origin} tone={status.tone === "danger" ? "danger" : undefined} testId="tool">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <Icon as={WrenchIcon} size="sm" className="text-muted-foreground" />
        <span className="font-mono text-[0.8125rem] font-medium text-foreground">{entry.tool}</span>
        <StatusChip tone={status.tone} size="sm">
          {status.label}
        </StatusChip>
        {duration ? <span className="font-mono text-xs tabular-nums text-muted-foreground">{duration}</span> : null}
      </div>
      {hasDetails ? (
        <DetailsDisclosure>
          {entry.args !== null ? <CodeBlock label="Arguments" value={prettyJson(entry.args)} /> : null}
          {entry.result !== null ? <CodeBlock label="Result (preview)" value={prettyJson(entry.result)} /> : null}
        </DetailsDisclosure>
      ) : null}
    </RowFrame>
  );
}

const METER_STATES = new Set<MeterState>(["idle", "connecting", "listening", "thinking", "speaking", "failed", "ended"]);

function toMeter(state: string): MeterState {
  return METER_STATES.has(state as MeterState) ? (state as MeterState) : "idle";
}

function StateTrack({ entry, origin }: { entry: StateEntry; origin: number }) {
  const [open, setOpen] = React.useState(false);
  const panelId = React.useId();
  const last = entry.transitions[entry.transitions.length - 1];
  const summary = summarizeStates(entry.transitions.map((t) => t.state));
  const expandable = entry.transitions.length > 1;

  return (
    <RowFrame at={entry.at} origin={origin} thin testId="state">
      <div className="flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
        <StateMeter state={toMeter(last.state)} size="xs" />
        {expandable ? (
          <button
            type="button"
            aria-expanded={open}
            aria-controls={panelId}
            onClick={() => setOpen((value) => !value)}
            className="inline-flex min-w-0 items-center gap-1 rounded-xs text-left outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
          >
            <span className="truncate">{summary}</span>
            <Icon
              as={ChevronDownIcon}
              size="sm"
              className={cn("shrink-0 transition-transform duration-(--dur-1) motion-reduce:transition-none", open && "rotate-180")}
            />
          </button>
        ) : (
          <span className="truncate">{summary}</span>
        )}
      </div>
      {expandable && open ? (
        <ol id={panelId} aria-label="State changes" className="mt-1.5 space-y-0.5 border-l border-border pl-3">
          {entry.transitions.map((transition) => (
            <li key={transition.id} className="flex items-center gap-2 text-xs text-muted-foreground">
              <span
                className="font-mono tabular-nums"
                title={formatDateTime(transition.at, { seconds: true })}
              >
                {formatOffset(transition.at, origin)}
              </span>
              <span className="text-foreground">{stateWord(transition.state)}</span>
            </li>
          ))}
        </ol>
      ) : null}
    </RowFrame>
  );
}

function EventRow({ entry, origin }: { entry: EventEntry; origin: number }) {
  const kind = entry.eventKind;
  const latest = entry.events[entry.events.length - 1];
  const payload = (latest.payload ?? {}) as Record<string, unknown>;
  const title = kind ? kind.title(payload) : humanizeType(entry.type);
  const summary = kind?.summary?.(payload) ?? null;
  const count = entry.events.length;
  const tone = kind?.tone;
  const emphasised = tone === "danger" || tone === "warning";

  return (
    <RowFrame at={entry.at} origin={origin} tone={emphasised ? tone : undefined} thin={!emphasised} testId="event">
      <details data-slot="details-disclosure" className="group/details">
        <summary
          className={cn(
            "flex cursor-pointer list-none flex-wrap items-center gap-x-2 gap-y-0.5 rounded-xs text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden",
            emphasised ? "" : "text-muted-foreground hover:text-foreground",
          )}
        >
          <Icon as={kind?.icon ?? CircleDotIcon} size="sm" />
          <span className={cn("font-medium", emphasised ? "" : "text-foreground")}>{title}</span>
          {count > 1 ? <span className="font-mono tabular-nums">×{count}</span> : null}
          {summary ? <span className="min-w-0 break-words">{summary}</span> : null}
          <span className="inline-flex items-center gap-0.5 text-muted-foreground">
            <Icon
              as={ChevronRightIcon}
              size="sm"
              className="transition-transform duration-(--dur-1) ease-out group-open/details:rotate-90 motion-reduce:transition-none"
            />
            Details
          </span>
        </summary>
        <div className="mt-2">
          <CodeBlock
            label={count > 1 ? `Latest of ${count} · ${entry.type}` : entry.type}
            value={prettyJson(latest.payload)}
          />
        </div>
      </details>
    </RowFrame>
  );
}

function humanizeType(type: string): string {
  const spaced = type.replace(/[_-]+/g, " ").trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}
