"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { ArrowLeftIcon, ArrowRightIcon, DownloadIcon, HeadphonesIcon, HistoryIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { SearchableSelect } from "@/components/ui/searchable-select";
import { Skeleton } from "@/components/ui/skeleton";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { EmptyState, NoMatches } from "@/components/shared/empty-state";
import { Icon } from "@/components/shared/icon";
import { Highlight, matchesQuery, SEARCH_THRESHOLD, useRememberedQuery } from "@/components/shared/list-search";
import { LoadingRegion } from "@/components/shared/loading-state";
import { PageHeader } from "@/components/shared/page-header";
import { RelativeTime } from "@/components/shared/relative-time";
import { ResponsiveTable, type ResponsiveTableColumn } from "@/components/shared/responsive-table";
import { SearchField } from "@/components/shared/search-field";
import { SegmentedControl } from "@/components/shared/segmented-control";
import { StatusPill } from "@/components/shared/status-chip";
import { useAgents } from "@/components/console/lib/api-hooks";
import { formatUsd } from "@/components/console/lib/cost-hooks";
import { ErrorBanner } from "@/components/console/shared/error-banner";
import type { AgentOut, QaField, SessionOut } from "@/contracts/lkap-contracts";
import { EMPTY_VALUE, formatDuration } from "@/lib/format";
import { cn } from "@/lib/utils";

import { RefreshButton } from "./refresh-button";
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
  rangeStart,
  SESSION_STATUS_LABEL,
  sentenceCase,
  sessionDurationMs,
  sessionStatusTone,
  sweptReason,
  usageTurns,
  type SessionFilters,
  type SessionRange,
  type SessionStatus,
} from "./session-model";
import { SESSION_LIST_FETCH_LIMIT, useConnectionNames, useSessionList } from "./use-session-queries";

const ALL = "__all__";

const STATUS_ORDER: SessionStatus[] = ["active", "ended", "failed", "created"];

const FILTER_PARAMS: Record<keyof SessionFilters, string> = {
  agentId: "agent",
  status: "status",
  channel: "channel",
  connectionId: "connection",
  range: "range",
};

/** Where the list's filters are remembered between visits (spec section 9). */
export const SESSION_FILTERS_STORAGE_KEY = "lkap:sessions:filters";
const SEARCH_LIST_ID = "sessions";
const ID_PATTERN = /^[\w-]{1,80}$/;

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
 * Remembered filters, validated on read: a status, channel or range outside
 * the fixed sets and an id that isn't id-shaped are dropped, so a stale or
 * hand-edited entry can never break the list. Ids that no longer exist are
 * dropped later, once the agent and connection lists have loaded.
 */
export function readStoredFilters(raw: string | null): SessionFilters {
  if (!raw || raw.length > 1000) return EMPTY_FILTERS;
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return EMPTY_FILTERS;
  }
  if (!value || typeof value !== "object") return EMPTY_FILTERS;
  const stored = value as Record<string, unknown>;
  const text = (key: string) => (typeof stored[key] === "string" ? (stored[key] as string) : "");
  const id = (key: string) => (ID_PATTERN.test(text(key)) ? text(key) : "");
  return {
    agentId: id("agentId"),
    status: (STATUS_ORDER as string[]).includes(text("status")) ? text("status") : "",
    channel: (CHANNELS as string[]).includes(text("channel")) ? text("channel") : "",
    connectionId: id("connectionId"),
    range: RANGE_OPTIONS.some((option) => option.value && option.value === text("range")) ? (text("range") as SessionRange) : "",
  };
}

function loadStoredFilters(): SessionFilters {
  try {
    return readStoredFilters(window.localStorage.getItem(SESSION_FILTERS_STORAGE_KEY));
  } catch {
    return EMPTY_FILTERS;
  }
}

function storeFilters(filters: SessionFilters) {
  try {
    if (Object.values(filters).some(Boolean)) {
      window.localStorage.setItem(SESSION_FILTERS_STORAGE_KEY, JSON.stringify(filters));
    } else {
      window.localStorage.removeItem(SESSION_FILTERS_STORAGE_KEY);
    }
  } catch {
    // Private mode or blocked storage: the filters still work, they just aren't remembered.
  }
}

/**
 * Filters and page live in the query string (docs/UI_UX_SPEC.md §3.5) so a
 * filtered list is shareable and survives the back button; the list reacts to
 * local state, not to a router round trip. They are also remembered per
 * person (spec section 9): a visit with no filters in the URL picks up the
 * last ones used, read after mount so server and client markup agree.
 */
function useListState() {
  const router = useRouter();
  const pathname = usePathname() ?? "/console/sessions";
  const searchParams = useSearchParams();
  const [filters, setFilters] = React.useState<SessionFilters>(() => readFilters(searchParams));
  const [page, setPage] = React.useState<number>(() => Math.max(1, Number(searchParams?.get("page")) || 1));
  // Filters that came from storage rather than the URL: ids among them are checked against the loaded lists.
  const [restored, setRestored] = React.useState<ReadonlyArray<keyof SessionFilters>>([]);

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

  // Once, after mount: an empty URL restores the remembered filters.
  const writeRef = React.useRef(write);
  writeRef.current = write;
  React.useEffect(() => {
    const fromUrl = Object.values(FILTER_PARAMS).some((param) => searchParams?.get(param));
    if (fromUrl) return;
    const stored = loadStoredFilters();
    const keys = (Object.keys(stored) as (keyof SessionFilters)[]).filter((key) => stored[key]);
    if (keys.length === 0) return;
    setFilters(stored);
    setPage(1);
    setRestored(keys);
    writeRef.current(stored, 1);
    // Runs once: later URL changes are this hook's own writes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const apply = React.useCallback(
    (next: SessionFilters) => {
      setFilters(next);
      setPage(1);
      storeFilters(next);
      write(next, 1);
    },
    [write],
  );

  const setFilter = React.useCallback(
    (key: keyof SessionFilters, value: string) => {
      setRestored((prev) => prev.filter((restoredKey) => restoredKey !== key));
      apply({ ...filters, [key]: value } as SessionFilters);
    },
    [apply, filters],
  );

  const clear = React.useCallback(() => {
    setRestored([]);
    apply(EMPTY_FILTERS);
  }, [apply]);

  const goTo = React.useCallback(
    (nextPage: number) => {
      setPage(nextPage);
      write(filters, nextPage);
    },
    [filters, write],
  );

  return { filters, page, restored, setFilter, clear, goTo };
}

function TableSkeleton() {
  return (
    <LoadingRegion label="Loading sessions" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <Skeleton className="h-[34px] w-72 rounded" />
        <Skeleton className="h-9 w-56 rounded" />
      </div>
      <div className="flex flex-wrap gap-2">
        {[0, 1, 2, 3].map((index) => (
          <Skeleton key={index} className="h-9 w-40 rounded" />
        ))}
      </div>
      <div className="overflow-hidden rounded-lg border border-border bg-card">
        <div className="h-9 border-b border-border bg-muted" />
        {[0, 1, 2, 3, 4].map((index) => (
          <div key={index} className="flex items-center gap-6 border-b border-border px-3.5 py-3 last:border-b-0">
            <div className="flex flex-1 flex-col gap-1.5">
              <Skeleton className="h-3.5 w-40" />
              <Skeleton className="h-3 w-28" />
            </div>
            <Skeleton className="h-5 w-16 rounded-pill" />
            <Skeleton className="h-3.5 w-14" />
            <Skeleton className="h-3.5 w-20" />
          </div>
        ))}
      </div>
    </LoadingRegion>
  );
}

/**
 * `/console/sessions` (docs/ui/DESIGN-SYSTEM.md section 7.4, list archetype):
 * the header with Refresh and Export CSV, a segmented status filter with
 * counts, search (6+ sessions, remembered) and the agent / date / channel /
 * connection filters (remembered, validated), then the table — cards on
 * phones — with distinct "nothing yet" and "no matches" states. There is no
 * page-level primary: sessions are made by calls, not here.
 */
export function SessionsTable() {
  const { data, isLoading, isError, error, refetch, isFetching } = useSessionList();
  const agentsQuery = useAgents();
  const connectionsQuery = useConnectionNames();
  const { filters, page, restored, setFilter, clear, goTo } = useListState();
  const [query, setQuery] = useRememberedQuery(SEARCH_LIST_ID);
  // V5-34: optional post-call field columns (a single filtered agent's own fields).
  const [fieldColumns, setFieldColumns] = React.useState<string[]>([]);

  const sessions = React.useMemo(() => data?.items ?? [], [data]);

  const availableFields = React.useMemo(
    () => fieldsOfFilteredAgent(agentsQuery.data?.items ?? [], filters.agentId),
    [agentsQuery.data, filters.agentId],
  );
  // Switching agent (or clearing the filter) drops columns the new field set doesn't have.
  React.useEffect(() => {
    setFieldColumns((prev) => prev.filter((name) => availableFields.some((f) => f.name === name)));
  }, [availableFields]);

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

  // A remembered agent or connection that no longer exists is dropped once the lists are in.
  React.useEffect(() => {
    if (!data) return;
    if (restored.includes("agentId") && filters.agentId && agentsQuery.isFetched) {
      if (!agentOptions.some(([id]) => id === filters.agentId)) setFilter("agentId", "");
    }
    if (restored.includes("connectionId") && filters.connectionId && connectionsQuery.isFetched) {
      if (!connectionOptions.some(([id]) => id === filters.connectionId)) setFilter("connectionId", "");
    }
  }, [data, restored, filters, agentOptions, connectionOptions, agentsQuery.isFetched, connectionsQuery.isFetched, setFilter]);

  const searched = React.useMemo(
    () =>
      query.trim() === ""
        ? sessions
        : sessions.filter((session) =>
            matchesQuery(
              [
                session.agent_name,
                session.room_name,
                channelLabel(session.channel),
                SESSION_STATUS_LABEL[session.status],
                pipelineModeLabel(session.pipeline_mode),
              ],
              query,
            ),
          ),
    [sessions, query],
  );
  const filtered = React.useMemo(() => filterSessions(searched, filters), [searched, filters]);
  const statusCounts = React.useMemo(() => {
    const others = filterSessions(searched, { ...filters, status: "" });
    const counts: Record<string, number> = { "": others.length };
    for (const session of others) counts[session.status] = (counts[session.status] ?? 0) + 1;
    return counts;
  }, [searched, filters]);

  const pages = pageCount(filtered.length);
  const currentPage = Math.min(page, pages);
  const rows = paginate(filtered, currentPage);
  const hasFilters = Object.values(filters).some(Boolean) || query.trim() !== "";
  const clearAll = () => {
    setQuery("");
    clear();
  };

  const header = (
    <PageHeader
      title="Sessions"
      description="Every call across your agents, with its timeline, transcript and final panel."
      actions={
        <>
          <RefreshButton onRefresh={() => void refetch()} refreshing={isFetching && !isLoading} />
          <Button asChild variant="secondary">
            <a href={exportCsvHref(filters)} download="sessions.csv">
              <DownloadIcon aria-hidden="true" />
              Export CSV
            </a>
          </Button>
        </>
      }
    />
  );

  if (isLoading) {
    return (
      <>
        {header}
        <TableSkeleton />
      </>
    );
  }

  if (isError) {
    return (
      <>
        {header}
        <ErrorBanner error={error} context={{ action: "load sessions" }} onRetry={() => void refetch()} />
      </>
    );
  }

  if (sessions.length === 0) {
    return (
      <>
        {header}
        <EmptyState
          icon={HistoryIcon}
          title="No sessions yet"
          description="Sessions appear here once someone opens a test call or the public session page."
          action={
            <Button asChild variant="secondary">
              <Link href="/console/agents">Open an agent</Link>
            </Button>
          }
        />
      </>
    );
  }

  const columns: ResponsiveTableColumn<SessionOut>[] = [
    {
      id: "agent",
      header: "Agent",
      cell: (session) => (
        <div className="min-w-0">
          <div className="truncate font-medium text-foreground">
            <Highlight text={session.agent_name || "Unknown agent"} query={query} />
          </div>
          <div className="truncate font-mono text-caption text-text-secondary">
            <Highlight text={session.room_name} query={query} />
          </div>
        </div>
      ),
    },
    {
      id: "status",
      header: "Status",
      // V5-38: the "Listen in" link needs to stay clickable over the row's own link overlay.
      interactive: true,
      cell: (session) => <SessionStatusChips session={session} />,
    },
    {
      id: "channel",
      header: "Channel",
      cell: (session) => <span className="text-text-secondary">{channelLabel(session.channel) ?? EMPTY_VALUE}</span>,
    },
    {
      id: "duration",
      header: "Duration",
      align: "end",
      cell: (session) => <span className="tabular-nums text-foreground">{durationText(session)}</span>,
    },
    {
      id: "started",
      header: "Started",
      cell: (session) => <StartedCell session={session} />,
    },
    {
      id: "mode",
      header: "Mode",
      cell: (session) => <span className="text-text-secondary">{pipelineModeLabel(session.pipeline_mode)}</span>,
    },
    {
      id: "turns",
      header: "Turns",
      align: "end",
      cell: (session) => <span className="tabular-nums text-text-secondary">{usageTurns(session.usage) ?? EMPTY_VALUE}</span>,
    },
    {
      id: "cost",
      header: "Cost",
      align: "end",
      cell: (session) => <CostCell session={session} />,
    },
    ...fieldColumns.map(
      (name): ResponsiveTableColumn<SessionOut> => ({
        id: `field-${name}`,
        header: fieldLabel(name),
        cell: (session) => <span className="text-text-secondary">{fieldCellText(fieldCellValue(session, name))}</span>,
      }),
    ),
  ];

  const first = filtered.length === 0 ? 0 : (currentPage - 1) * PAGE_SIZE + 1;
  const last = Math.min(currentPage * PAGE_SIZE, filtered.length);
  const truncated = (data?.total ?? 0) > sessions.length;
  const showSearch = sessions.length >= SEARCH_THRESHOLD || query.trim() !== "";

  return (
    <>
      {header}
      <div className="flex flex-col gap-4">
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
            <SegmentedControl
              label="Filter by status"
              value={filters.status}
              onValueChange={(value) => setFilter("status", value)}
              options={[
                { value: "", label: "All", count: statusCounts[""] ?? 0 },
                ...STATUS_ORDER.map((status) => ({
                  value: status,
                  label: SESSION_STATUS_LABEL[status],
                  count: statusCounts[status] ?? 0,
                })),
              ]}
            />
            {showSearch ? (
              <SearchField
                value={query}
                onValueChange={setQuery}
                aria-label="Search sessions"
                placeholder="Search by agent, room or channel…"
                wrapperClassName="sm:w-72"
              />
            ) : null}
          </div>
          <div className="flex flex-col gap-2 lg:flex-row lg:flex-wrap lg:items-center">
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
            {availableFields.length > 0 ? (
              <SearchableSelect
                aria-label="Columns"
                multiple
                values={fieldColumns}
                onValuesChange={setFieldColumns}
                options={availableFields.map((f) => ({ value: f.name, label: fieldLabel(f.name) }))}
                placeholder="Columns"
                triggerClassName="w-auto sm:w-40"
                value={null}
                onValueChange={() => {}}
              />
            ) : null}
          </div>
        </div>

        {filtered.length === 0 ? (
          <NoMatches items="sessions" query={query} onClear={clearAll} />
        ) : (
          // The table's frame (spec 6.7): a bordered, rounded wrapper; phones get the card list.
          <div className="md:overflow-hidden md:rounded-lg md:border md:border-border md:bg-card">
            <ResponsiveTable<SessionOut>
              columns={columns}
              rows={rows}
              label="Sessions"
              getRowKey={(session) => session.id}
              rowHref={(session) => `/console/sessions/${session.id}`}
              renderCard={(session) => <SessionCard session={session} query={query} />}
            />
          </div>
        )}

        {filtered.length > 0 ? (
          <nav aria-label="Pagination" className="flex flex-wrap items-center justify-between gap-2 text-caption text-text-secondary">
            <p aria-live="polite" className="tabular-nums">
              {`${first} to ${last} of ${filtered.length}`}
              {hasFilters ? ` (filtered from ${sessions.length})` : null}
              {truncated ? ` · newest ${SESSION_LIST_FETCH_LIMIT} of ${data?.total} loaded` : null}
            </p>
            {pages > 1 ? (
              <div className="flex items-center gap-1">
                <Button type="button" variant="secondary" size="sm" disabled={currentPage <= 1} onClick={() => goTo(currentPage - 1)}>
                  <Icon as={ArrowLeftIcon} size="sm" />
                  Previous
                </Button>
                <span className="px-2 tabular-nums">{`Page ${currentPage} of ${pages}`}</span>
                <Button type="button" variant="secondary" size="sm" disabled={currentPage >= pages} onClick={() => goTo(currentPage + 1)}>
                  Next
                  <Icon as={ArrowRightIcon} size="sm" />
                </Button>
              </div>
            ) : null}
          </nav>
        ) : null}
      </div>
    </>
  );
}

function durationText(session: SessionOut): string {
  const ms = sessionDurationMs(session);
  return ms === null ? EMPTY_VALUE : formatDuration(ms);
}

/**
 * `GET /v1/sessions/export.csv` (V5-34, ask #178(7) — new, not an extension
 * of an existing button): the same filters as the list, so "Export CSV"
 * downloads exactly what's on screen. A plain link, not a fetch + blob: the
 * console proxy already carries the admin token, and the browser handles the
 * `Content-Disposition: attachment` response on its own.
 */
export function exportCsvHref(filters: SessionFilters): string {
  const params = new URLSearchParams();
  if (filters.agentId) params.set("agent_id", filters.agentId);
  if (filters.status) params.set("status", filters.status);
  if (filters.channel) params.set("channel", filters.channel);
  if (filters.connectionId) params.set("connection_id", filters.connectionId);
  const since = rangeStart(filters.range);
  if (since !== null) params.set("from", new Date(since).toISOString());
  const qs = params.toString();
  return `/api/console/sessions/export.csv${qs ? `?${qs}` : ""}`;
}

/** "claim_type" -> "Claim type", for the post-call field columns below. */
function fieldLabel(name: string): string {
  const spaced = name.replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/**
 * The post-call fields a single filtered agent defines, for the "Columns"
 * picker — the api's `_field_columns` (`routers/sessions.py`) uses the same
 * source (the agent's *current* `config.qa.fields`) for the CSV export's own
 * columns, so picking one here previews exactly what that column exports.
 * Ambiguous (no agent filter, or several agents) offers nothing: there's no
 * single field set to offer.
 */
function fieldsOfFilteredAgent(agents: AgentOut[], agentId: string): QaField[] {
  if (!agentId) return [];
  return agents.find((agent) => agent.id === agentId)?.config?.qa?.fields ?? [];
}

/**
 * `SessionOut` (the list row) doesn't carry `qa` yet — only `GET
 * /v1/sessions/{id}` and the CSV export compute a session's post-call field
 * values (`docs/v5/_asks.md`, an ask is filed for the list route to carry
 * them too). Until then this column always reads "—": the toggle and the
 * column itself are real, and start showing values the day that ask lands.
 */
function fieldCellValue(session: SessionOut, name: string): unknown {
  return (session as unknown as { qa?: { fields?: Record<string, unknown> } }).qa?.fields?.[name];
}

function fieldCellText(value: unknown): string {
  if (value === undefined) return EMPTY_VALUE;
  if (value === null) return "Not mentioned";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return String(value);
}

/**
 * The list's Cost column (docs/v4/COSTS.md §5 item 7): the actual cost, with
 * the creation-time estimate muted beside it when both exist — "$0.12 (≈
 * $0.10)". A session with no actual cost yet falls back to just the
 * estimate; one with neither shows "—" (never "$0" for an unpriced/pending
 * session — `cost_usd`/`estimated_usd` are `null`, not zero, until priced).
 */
export function CostCell({ session }: { session: SessionOut }) {
  const actual = formatUsd(session.cost_usd);
  const estimate = formatUsd(session.estimated_usd);
  if (actual && estimate) {
    return (
      <span className="tabular-nums text-foreground">
        {actual} <span className="text-text-secondary">(≈ {estimate})</span>
      </span>
    );
  }
  if (actual) return <span className="tabular-nums text-foreground">{actual}</span>;
  if (estimate) return <span className="tabular-nums text-text-secondary">≈ {estimate} · estimate</span>;
  return <span className="text-text-secondary">{EMPTY_VALUE}</span>;
}

function StartedCell({ session }: { session: SessionOut }) {
  if (session.started_at) return <RelativeTime iso={session.started_at} className="text-text-secondary" />;
  return (
    <span className="text-text-tertiary">
      <span className="sr-only">Not started, created </span>
      <RelativeTime iso={session.created_at} className="text-text-tertiary" />
    </span>
  );
}

/** Status pill plus, for sweep-failed rows, the muted reason pill (§4.10). */
export function SessionStatusChips({ session }: { session: SessionOut }) {
  const swept = sweptReason(session);
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <StatusPill tone={sessionStatusTone(session)} size="sm">
        {SESSION_STATUS_LABEL[session.status]}
      </StatusPill>
      {swept ? (
        <StatusPill tone="neutral" size="sm">
          {sentenceCase(swept)}
        </StatusPill>
      ) : null}
      {session.recording_status === "failed" ? (
        <StatusPill tone="danger" size="sm">
          Recording failed
        </StatusPill>
      ) : null}
      {session.status === "active" ? <LiveIndicatorLink sessionId={session.id} /> : null}
    </div>
  );
}

/**
 * V5-38: a link straight to the session's Live tab, next to the "Active"
 * status pill. The status column is `interactive`, which lifts it above the
 * row's own stretched link overlay (`ResponsiveTable`), so this stays
 * independently clickable.
 */
function LiveIndicatorLink({ sessionId }: { sessionId: string }) {
  return (
    <Link
      href={`/console/sessions/${sessionId}?tab=live`}
      className="inline-flex h-5 items-center gap-1 rounded-sm border border-brand-border bg-brand-subtle px-1.5 text-caption font-medium text-brand hover:underline"
    >
      <Icon as={HeadphonesIcon} size="xs" />
      Listen in
    </Link>
  );
}

function SessionCard({ session, query }: { session: SessionOut; query: string }) {
  const channel = channelLabel(session.channel);
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate font-medium text-foreground">
            <Highlight text={session.agent_name || "Unknown agent"} query={query} />
          </div>
          <div className="truncate font-mono text-caption text-text-secondary">
            <Highlight text={session.room_name} query={query} />
          </div>
        </div>
        <SessionStatusChips session={session} />
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-caption text-text-secondary">
        <StartedCell session={session} />
        <span className="tabular-nums text-foreground">{durationText(session)}</span>
        {channel ? <span>{channel}</span> : null}
        <span>{pipelineModeLabel(session.pipeline_mode)}</span>
        <CostCell session={session} />
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
