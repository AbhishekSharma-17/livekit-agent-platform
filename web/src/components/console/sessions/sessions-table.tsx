"use client";

import * as React from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { ChevronLeftIcon, ChevronRightIcon, HistoryIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { EmptyState } from "@/components/shared/empty-state";
import { Icon } from "@/components/shared/icon";
import { RelativeTime } from "@/components/shared/relative-time";
import { ResponsiveTable, type ResponsiveTableColumn } from "@/components/shared/responsive-table";
import { StatusChip } from "@/components/shared/status-chip";
import { useAgents } from "@/components/console/lib/api-hooks";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import type { SessionOut } from "@/contracts/lkap-contracts";
import { formatDuration } from "@/lib/format";
import { cn } from "@/lib/utils";

import {
  CHANNELS,
  channelLabel,
  EMPTY_FILTERS,
  filterSessions,
  pageCount,
  paginate,
  PAGE_SIZE,
  pipelineModeLabel,
  RANGE_OPTIONS,
  SESSION_STATUS_LABEL,
  sentenceCase,
  sessionDurationMs,
  sessionStatusTone,
  sweptReason,
  usageTurns,
  type SessionFilters,
  type SessionRange,
} from "./session-model";
import { SESSION_LIST_FETCH_LIMIT, useConnectionNames, useSessionList } from "./use-session-queries";

const ALL = "__all__";

const STATUS_FILTERS: { value: string; label: string }[] = [
  { value: "", label: "All" },
  { value: "active", label: SESSION_STATUS_LABEL.active },
  { value: "ended", label: SESSION_STATUS_LABEL.ended },
  { value: "failed", label: SESSION_STATUS_LABEL.failed },
  { value: "created", label: SESSION_STATUS_LABEL.created },
];

const FILTER_PARAMS: Record<keyof SessionFilters, string> = {
  agentId: "agent",
  status: "status",
  channel: "channel",
  connectionId: "connection",
  range: "range",
};

function readFilters(params: URLSearchParams | null): SessionFilters {
  const range = params?.get(FILTER_PARAMS.range) ?? "";
  return {
    agentId: params?.get(FILTER_PARAMS.agentId) ?? "",
    status: params?.get(FILTER_PARAMS.status) ?? "",
    channel: params?.get(FILTER_PARAMS.channel) ?? "",
    connectionId: params?.get(FILTER_PARAMS.connectionId) ?? "",
    range: RANGE_OPTIONS.some((option) => option.value === range) ? (range as SessionRange) : "",
  };
}

/**
 * Filters and page live in the query string (docs/UI_UX_SPEC.md §3.5) so a
 * filtered list is shareable and survives the back button; the list reacts to
 * local state, not to a router round trip.
 */
function useListState() {
  const router = useRouter();
  const pathname = usePathname() ?? "/console/sessions";
  const searchParams = useSearchParams();
  const [filters, setFilters] = React.useState<SessionFilters>(() => readFilters(searchParams));
  const [page, setPage] = React.useState<number>(() => Math.max(1, Number(searchParams?.get("page")) || 1));

  const write = React.useCallback(
    (nextFilters: SessionFilters, nextPage: number) => {
      const params = new URLSearchParams(searchParams?.toString() ?? "");
      for (const [key, param] of Object.entries(FILTER_PARAMS) as [keyof SessionFilters, string][]) {
        if (nextFilters[key]) params.set(param, nextFilters[key]);
        else params.delete(param);
      }
      if (nextPage > 1) params.set("page", String(nextPage));
      else params.delete("page");
      const qs = params.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [pathname, router, searchParams],
  );

  const setFilter = React.useCallback(
    (key: keyof SessionFilters, value: string) => {
      const next = { ...filters, [key]: value } as SessionFilters;
      setFilters(next);
      setPage(1);
      write(next, 1);
    },
    [filters, write],
  );

  const clear = React.useCallback(() => {
    setFilters(EMPTY_FILTERS);
    setPage(1);
    write(EMPTY_FILTERS, 1);
  }, [write]);

  const goTo = React.useCallback(
    (nextPage: number) => {
      setPage(nextPage);
      write(filters, nextPage);
    },
    [filters, write],
  );

  return { filters, page, setFilter, clear, goTo };
}

export function SessionsTable() {
  const { data, isLoading, isError, error, refetch } = useSessionList();
  const agentsQuery = useAgents();
  const connectionsQuery = useConnectionNames();
  const { filters, page, setFilter, clear, goTo } = useListState();

  const sessions = React.useMemo(() => data?.items ?? [], [data]);

  const agentOptions = React.useMemo(() => {
    const names = new Map<string, string>();
    for (const agent of agentsQuery.data?.items ?? []) names.set(agent.id, agent.name);
    for (const session of sessions) if (!names.has(session.agent_id)) names.set(session.agent_id, session.agent_name);
    return [...names.entries()].sort((a, b) => a[1].localeCompare(b[1]));
  }, [agentsQuery.data, sessions]);

  const connectionOptions = React.useMemo(() => {
    const names = new Map<string, string>(connectionsQuery.data ?? []);
    for (const session of sessions) {
      if (session.connection_id && !names.has(session.connection_id)) names.set(session.connection_id, session.connection_id);
    }
    return [...names.entries()].sort((a, b) => a[1].localeCompare(b[1]));
  }, [connectionsQuery.data, sessions]);

  const filtered = React.useMemo(() => filterSessions(sessions, filters), [sessions, filters]);
  const pages = pageCount(filtered.length);
  const currentPage = Math.min(page, pages);
  const rows = paginate(filtered, currentPage);
  const hasFilters = Object.values(filters).some(Boolean);

  if (isLoading) {
    return (
      <div className="space-y-2" aria-busy="true" aria-label="Loading sessions">
        <Skeleton className="h-9 w-full max-w-2xl" />
        {[0, 1, 2, 3, 4].map((i) => (
          <Skeleton key={i} className="h-12 w-full" />
        ))}
      </div>
    );
  }

  if (isError) {
    return <ErrorBanner message={`Couldn't load sessions — ${errorMessage(error)}`} onRetry={() => refetch()} />;
  }

  if (sessions.length === 0) {
    return (
      <EmptyState
        icon={HistoryIcon}
        title="No sessions yet"
        description="Sessions appear here once someone opens a test call or the public session page."
      />
    );
  }

  const columns: ResponsiveTableColumn<SessionOut>[] = [
    {
      id: "agent",
      header: "Agent",
      cell: (session) => (
        <div className="min-w-0">
          <div className="truncate font-medium text-foreground">{session.agent_name || "Unknown agent"}</div>
          <div className="truncate font-mono text-xs text-muted-foreground">{session.room_name}</div>
        </div>
      ),
    },
    { id: "status", header: "Status", cell: (session) => <SessionStatusChips session={session} /> },
    {
      id: "channel",
      header: "Channel",
      cell: (session) => <span className="text-muted-foreground">{channelLabel(session.channel) ?? "—"}</span>,
    },
    {
      id: "duration",
      header: "Duration",
      align: "end",
      cell: (session) => <span className="font-mono tabular-nums text-foreground">{durationText(session)}</span>,
    },
    {
      id: "started",
      header: "Started",
      cell: (session) => <StartedCell session={session} />,
    },
    {
      id: "mode",
      header: "Mode",
      cell: (session) => <span className="text-muted-foreground">{pipelineModeLabel(session.pipeline_mode)}</span>,
    },
    {
      id: "turns",
      header: "Turns",
      align: "end",
      cell: (session) => <span className="font-mono tabular-nums text-muted-foreground">{usageTurns(session.usage) ?? "—"}</span>,
    },
  ];

  const first = filtered.length === 0 ? 0 : (currentPage - 1) * PAGE_SIZE + 1;
  const last = Math.min(currentPage * PAGE_SIZE, filtered.length);
  const truncated = (data?.total ?? 0) > sessions.length;

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-2 lg:flex-row lg:flex-wrap lg:items-center">
        <div className="flex flex-wrap items-center gap-1" role="group" aria-label="Filter by status">
          {STATUS_FILTERS.map((filter) => (
            <Button
              key={filter.value || "all"}
              type="button"
              size="sm"
              variant={filters.status === filter.value ? "secondary" : "ghost"}
              aria-pressed={filters.status === filter.value}
              onClick={() => setFilter("status", filter.value)}
            >
              {filter.label}
            </Button>
          ))}
        </div>
        <div className="grid grid-cols-2 gap-2 sm:flex sm:flex-wrap sm:items-center">
          <FilterSelect
            label="Filter by agent"
            value={filters.agentId}
            allLabel="All agents"
            options={agentOptions}
            onChange={(value) => setFilter("agentId", value)}
            className="sm:w-48"
          />
          <FilterSelect
            label="Filter by date"
            value={filters.range}
            allLabel={RANGE_OPTIONS[0].label}
            options={RANGE_OPTIONS.filter((option) => option.value).map((option) => [option.value, option.label])}
            onChange={(value) => setFilter("range", value)}
            className="sm:w-36"
          />
          <FilterSelect
            label="Filter by channel"
            value={filters.channel}
            allLabel="All channels"
            options={CHANNELS.map((channel) => [channel, channelLabel(channel) ?? channel])}
            onChange={(value) => setFilter("channel", value)}
            className="sm:w-40"
          />
          <FilterSelect
            label="Filter by connection"
            value={filters.connectionId}
            allLabel="All connections"
            options={connectionOptions}
            onChange={(value) => setFilter("connectionId", value)}
            className="sm:w-44"
          />
        </div>
      </div>

      <ResponsiveTable<SessionOut>
        columns={columns}
        rows={rows}
        label="Sessions"
        getRowKey={(session) => session.id}
        rowHref={(session) => `/console/sessions/${session.id}`}
        renderCard={(session) => <SessionCard session={session} />}
        empty={
          <EmptyState
            icon={HistoryIcon}
            title="No sessions match these filters"
            compact
            action={
              <Button type="button" variant="ghost" size="sm" onClick={clear}>
                Clear filters
              </Button>
            }
          />
        }
      />

      {filtered.length > 0 ? (
        <nav aria-label="Pagination" className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
          <p aria-live="polite">
            {`${first}–${last} of ${filtered.length}`}
            {hasFilters ? ` (filtered from ${sessions.length})` : null}
            {truncated ? ` · newest ${SESSION_LIST_FETCH_LIMIT} of ${data?.total} loaded` : null}
          </p>
          {pages > 1 ? (
            <div className="flex items-center gap-1">
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={currentPage <= 1}
                onClick={() => goTo(currentPage - 1)}
              >
                <Icon as={ChevronLeftIcon} size="sm" />
                Previous
              </Button>
              <span className="px-2 tabular-nums">{`Page ${currentPage} of ${pages}`}</span>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={currentPage >= pages}
                onClick={() => goTo(currentPage + 1)}
              >
                Next
                <Icon as={ChevronRightIcon} size="sm" />
              </Button>
            </div>
          ) : null}
        </nav>
      ) : null}
    </div>
  );
}

function durationText(session: SessionOut): string {
  const ms = sessionDurationMs(session);
  return ms === null ? "—" : formatDuration(ms);
}

function StartedCell({ session }: { session: SessionOut }) {
  if (session.started_at) return <RelativeTime iso={session.started_at} className="text-muted-foreground" />;
  return (
    <span className="text-muted-foreground">
      <span className="sr-only">Not started; created </span>
      <RelativeTime iso={session.created_at} className="text-muted-foreground/70" />
    </span>
  );
}

/** Status chip plus, for sweep-failed rows, the muted reason chip (§4.10). */
export function SessionStatusChips({ session }: { session: SessionOut }) {
  const swept = sweptReason(session);
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <StatusChip tone={sessionStatusTone(session)} size="sm" dot={session.status === "failed" && !swept}>
        {SESSION_STATUS_LABEL[session.status]}
      </StatusChip>
      {swept ? (
        <StatusChip tone="neutral" size="sm" className="opacity-80">
          {sentenceCase(swept)}
        </StatusChip>
      ) : null}
    </div>
  );
}

function SessionCard({ session }: { session: SessionOut }) {
  const channel = channelLabel(session.channel);
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate font-medium text-foreground">{session.agent_name || "Unknown agent"}</div>
          <div className="truncate font-mono text-xs text-muted-foreground">{session.room_name}</div>
        </div>
        <SessionStatusChips session={session} />
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
        <StartedCell session={session} />
        <span className="font-mono tabular-nums text-foreground">{durationText(session)}</span>
        {channel ? <span>{channel}</span> : null}
        <span>{pipelineModeLabel(session.pipeline_mode)}</span>
      </div>
    </div>
  );
}

function FilterSelect({
  label,
  value,
  allLabel,
  options,
  onChange,
  className,
}: {
  label: string;
  value: string;
  allLabel: string;
  options: [string, string][];
  onChange: (value: string) => void;
  className?: string;
}) {
  return (
    <Select value={value || ALL} onValueChange={(next) => onChange(next === ALL ? "" : next)}>
      <SelectTrigger aria-label={label} className={cn("w-full", className)}>
        <SelectValue placeholder={allLabel} />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={ALL}>{allLabel}</SelectItem>
        {options.map(([optionValue, optionLabel]) => (
          <SelectItem key={optionValue} value={optionValue}>
            {optionLabel}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
