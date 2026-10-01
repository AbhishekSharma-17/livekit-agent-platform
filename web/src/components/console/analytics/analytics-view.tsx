"use client";

import * as React from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import {
  BarChart3Icon,
  CircleAlertIcon,
  ClockIcon,
  CoinsIcon,
  DownloadIcon,
  MessagesSquareIcon,
  WalletIcon,
} from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { StatCard, StatGrid } from "@/components/shared/data-display";
import { EmptyState } from "@/components/shared/empty-state";
import { LoadingRegion } from "@/components/shared/loading-state";
import { PageHeader } from "@/components/shared/page-header";
import { ResponsiveTable, type ResponsiveTableColumn } from "@/components/shared/responsive-table";
import { Section, SectionRow } from "@/components/shared/section";
import { StatusPill } from "@/components/shared/status-chip";
import { lifecycleStatus, type LifecycleStatus } from "@/components/shared/status-map";
import { useProviders } from "@/components/console/lib/api-hooks";
import { SLOT_LABELS } from "@/components/console/lib/cost-hooks";
import { ErrorBanner } from "@/components/console/shared/error-banner";
import { RefreshButton } from "@/components/console/sessions/refresh-button";
import { formatUsd } from "@/components/console/sessions/session-model";
import { api } from "@/lib/api";
import type { AnalyticsBucket, AnalyticsDriver, AnalyticsSummary, ProviderSpec } from "@/contracts/lkap-contracts";
import { EMPTY_VALUE } from "@/lib/format";

/**
 * `/console/analytics?range=&tab=` — the analytics archetype
 * (docs/ui/DESIGN-SYSTEM.md section 7.4): the date range and Refresh in the
 * header, then tabs. Overview carries the stat grid, the estimates note, the
 * cost-by-day chart and breakdown cards with health badges; the Agents and
 * Cost drivers tabs are full tables with a CSV export.
 *
 * `GET /v1/analytics/summary` (`routers/analytics.py`) accepts
 * `today | 7d | 30d | all` — **not** the amendments' `90d`; this follows the
 * api, since that is the contract that actually exists.
 */
type Range = "today" | "7d" | "30d" | "all";
const RANGES: { value: Range; label: string }[] = [
  { value: "today", label: "Today" },
  { value: "7d", label: "Last 7 days" },
  { value: "30d", label: "Last 30 days" },
  { value: "all", label: "All time" },
];

type TabId = "overview" | "agents" | "drivers";
const TABS: { id: TabId; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "agents", label: "Agents" },
  { id: "drivers", label: "Cost drivers" },
];
const DEFAULT_TAB: TabId = "overview";
/** Rows each overview breakdown card shows; the tabs hold the rest. */
const BREAKDOWN_ROWS = 5;

const TITLE = "Analytics";
const DESCRIPTION = "Usage and cost across every agent in this workspace.";

function useAnalyticsSummary(range: Range) {
  return useQuery({
    queryKey: ["analytics", "summary", range] as const,
    queryFn: () => api.get<AnalyticsSummary>("analytics/summary", { range }),
  });
}

function readRange(params: URLSearchParams | null): Range {
  const value = params?.get("range");
  return RANGES.some((r) => r.value === value) ? (value as Range) : "30d";
}

function readTab(params: URLSearchParams | null): TabId {
  const value = params?.get("tab");
  return TABS.some((tab) => tab.id === value) ? (value as TabId) : DEFAULT_TAB;
}

function StatsSkeleton() {
  return (
    <StatGrid>
      {[0, 1, 2, 3, 4].map((index) => (
        <div key={index} className="flex flex-col gap-2.5 rounded-lg border border-border bg-card p-4">
          <Skeleton className="h-3.5 w-20" />
          <Skeleton className="h-7 w-16" />
          <Skeleton className="h-3.5 w-24" />
        </div>
      ))}
    </StatGrid>
  );
}

function BodySkeleton() {
  return (
    <LoadingRegion label="Loading analytics" className="flex flex-col gap-8">
      <StatsSkeleton />
      <div className="rounded-lg border border-border bg-card p-5">
        <Skeleton className="mb-4 h-4 w-32" />
        <div className="flex h-40 items-end gap-2">
          {[40, 65, 30, 80, 55, 70, 45].map((height, index) => (
            <Skeleton key={index} className="flex-1 rounded-sm" style={{ height: `${height}%` }} />
          ))}
        </div>
      </div>
    </LoadingRegion>
  );
}

/** What the page shows while `useSearchParams` suspends: the header and the body's shape. */
export function AnalyticsFallback() {
  return (
    <>
      <PageHeader title={TITLE} description={DESCRIPTION} />
      <BodySkeleton />
    </>
  );
}

export function AnalyticsView() {
  const router = useRouter();
  const pathname = usePathname() ?? "/console/analytics";
  const searchParams = useSearchParams();
  const range = readRange(searchParams);
  const tab = readTab(searchParams);
  const query = useAnalyticsSummary(range);

  function writeParams(update: (params: URLSearchParams) => void) {
    const params = new URLSearchParams(searchParams?.toString() ?? "");
    update(params);
    const qs = params.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  }

  const setRange = (next: string) => writeParams((params) => params.set("range", next));
  const setTab = (next: string) =>
    writeParams((params) => {
      if (next === DEFAULT_TAB) params.delete("tab");
      else params.set("tab", next);
    });

  return (
    <>
      <PageHeader
        title={TITLE}
        description={DESCRIPTION}
        actions={
          <>
            <Select value={range} onValueChange={setRange}>
              <SelectTrigger aria-label="Date range" className="w-full sm:w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {RANGES.map((r) => (
                  <SelectItem key={r.value} value={r.value}>
                    {r.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <RefreshButton onRefresh={() => void query.refetch()} refreshing={query.isFetching && !query.isLoading} />
          </>
        }
      />

      {query.isLoading ? (
        <BodySkeleton />
      ) : query.isError ? (
        <ErrorBanner error={query.error} context={{ action: "load analytics" }} onRetry={() => void query.refetch()} />
      ) : query.data ? (
        <Tabs value={tab} onValueChange={setTab} className="gap-6">
          <div className="-mx-4 overflow-x-auto px-4 md:mx-0 md:px-0">
            <TabsList aria-label="Analytics views" className="w-max min-w-full justify-start">
              {TABS.map((item) => (
                <TabsTrigger key={item.id} value={item.id} className="flex-none">
                  {item.label}
                </TabsTrigger>
              ))}
            </TabsList>
          </div>
          <TabsContent value="overview" className="min-w-0">
            <OverviewTab summary={query.data} onSeeAll={setTab} />
          </TabsContent>
          <TabsContent value="agents" className="min-w-0">
            <AgentsTab buckets={query.data.by_agent ?? []} range={range} />
          </TabsContent>
          <TabsContent value="drivers" className="min-w-0">
            <DriversTab drivers={query.data.top_drivers ?? []} range={range} />
          </TabsContent>
        </Tabs>
      ) : null}
    </>
  );
}

const minutesText = (minutes: number | null | undefined) =>
  (minutes ?? 0).toLocaleString(undefined, { maximumFractionDigits: 1 });

function OverviewTab({ summary, onSeeAll }: { summary: AnalyticsSummary; onSeeAll: (tab: TabId) => void }) {
  const providers = useProviders().data?.providers ?? [];
  const failed = summary.failed ?? 0;
  const sessions = summary.sessions ?? 0;
  const byAgent = summary.by_agent ?? [];
  const drivers = summary.top_drivers ?? [];
  return (
    <div className="flex flex-col gap-8">
      <StatGrid>
        <StatCard
          label="Sessions"
          icon={MessagesSquareIcon}
          value={sessions.toLocaleString()}
          hint={sessions > 0 ? "Test calls and public calls" : "No calls in this range"}
        />
        <StatCard label="Minutes" icon={ClockIcon} value={minutesText(summary.minutes)} hint="Time on calls" />
        <StatCard label="Cost" icon={CoinsIcon} value={formatUsd(summary.cost_usd) ?? EMPTY_VALUE} hint="At list prices" />
        <StatCard
          label="Estimated"
          icon={WalletIcon}
          value={formatUsd(summary.estimated_usd) ?? "no estimate"}
          hint={summary.accuracy_pct != null ? `accuracy ${summary.accuracy_pct.toFixed(0)} %` : "Before the calls ran"}
        />
        <StatCard
          label="Failed"
          icon={CircleAlertIcon}
          value={failed.toLocaleString()}
          hint={failed > 0 ? "Needs review in Sessions" : "No failed calls"}
        />
      </StatGrid>

      <Alert tone="info" title="Costs are estimates">
        Actual cost is computed from usage at list prices; OpenRouter sessions use live prices and can be reconciled with
        OpenRouter&apos;s charge. Vendor invoices may differ (included minutes, volume tiers, taxes).
      </Alert>

      <Section id="by-day" title="Cost by day" description="Actual and estimated cost for each day in the range.">
        <SectionRow>
          {(summary.by_day ?? []).length === 0 ? (
            <EmptyState icon={BarChart3Icon} compact title="No sessions in this range" />
          ) : (
            <ByDayChart buckets={summary.by_day ?? []} />
          )}
        </SectionRow>
      </Section>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Section
          id="by-agent"
          title="By agent"
          aside={
            byAgent.length > BREAKDOWN_ROWS ? (
              <Button type="button" variant="link" size="sm" onClick={() => onSeeAll("agents")}>
                See all
              </Button>
            ) : undefined
          }
        >
          <SectionRow>
            {byAgent.length === 0 ? (
              <EmptyState icon={BarChart3Icon} compact title="No sessions in this range" />
            ) : (
              <ByAgentTable buckets={byAgent.slice(0, BREAKDOWN_ROWS)} />
            )}
          </SectionRow>
        </Section>

        <Section
          id="cost-drivers"
          title="Top cost drivers"
          aside={
            drivers.length > BREAKDOWN_ROWS ? (
              <Button type="button" variant="link" size="sm" onClick={() => onSeeAll("drivers")}>
                See all
              </Button>
            ) : undefined
          }
        >
          <SectionRow>
            {drivers.length === 0 ? (
              <EmptyState icon={BarChart3Icon} compact title="No priced usage in this range" />
            ) : (
              <TopDriversTable drivers={drivers.slice(0, BREAKDOWN_ROWS)} providers={providers} />
            )}
          </SectionRow>
        </Section>
      </div>
    </div>
  );
}

function AgentsTab({ buckets, range }: { buckets: AnalyticsBucket[]; range: Range }) {
  return (
    <Section
      id="agents-table"
      title="Sessions by agent"
      description="Every agent with calls in the range, with its minutes, cost and failures."
      aside={
        buckets.length > 0 ? (
          <ExportCsvButton
            filename={`analytics-agents-${range}.csv`}
            header={["Agent", "Sessions", "Minutes", "Cost (USD)", "Failed", "Health"]}
            rows={buckets.map((b) => [b.key, b.sessions, b.minutes ?? 0, b.cost_usd ?? "", b.failed, agentHealth(b).label])}
          />
        ) : undefined
      }
    >
      <SectionRow>
        {buckets.length === 0 ? (
          <EmptyState icon={BarChart3Icon} compact title="No sessions in this range" />
        ) : (
          <ByAgentTable buckets={buckets} />
        )}
      </SectionRow>
    </Section>
  );
}

function DriversTab({ drivers, range }: { drivers: AnalyticsDriver[]; range: Range }) {
  const providers = useProviders().data?.providers ?? [];
  return (
    <Section
      id="drivers-table"
      title="Cost drivers"
      description="What the money went on, largest first."
      aside={
        drivers.length > 0 ? (
          <ExportCsvButton
            filename={`analytics-cost-drivers-${range}.csv`}
            header={["Part of the agent", "Provider", "Model", "Unit", "Cost (USD)", "Share (%)", "Estimated (USD)"]}
            rows={drivers.map((d) => [
              driverLabel(d, providers),
              d.provider_id,
              d.model ?? "",
              d.unit,
              d.cost_usd ?? "",
              d.share_pct,
              d.estimated_usd ?? "",
            ])}
          />
        ) : undefined
      }
    >
      <SectionRow>
        {drivers.length === 0 ? (
          <EmptyState icon={BarChart3Icon} compact title="No priced usage in this range" />
        ) : (
          <TopDriversTable drivers={drivers} providers={providers} />
        )}
      </SectionRow>
    </Section>
  );
}

type CsvValue = string | number | null | undefined;

const QUOTE = '"';

/** One CSV field: quoted (inner quotes doubled) when it holds a comma, quote or line break. */
function csvField(value: CsvValue): string {
  const text = value == null ? "" : String(value);
  const needsQuotes = text.includes(QUOTE) || text.includes(",") || text.includes("\n") || text.includes("\r");
  return needsQuotes ? QUOTE + text.split(QUOTE).join(QUOTE + QUOTE) + QUOTE : text;
}

export function toCsv(header: string[], rows: CsvValue[][]): string {
  return [header, ...rows].map((row) => row.map(csvField).join(",")).join("\r\n");
}

/** Downloads the table on screen as CSV, built in the browser from the summary already loaded. */
function ExportCsvButton({
  filename,
  header,
  rows,
}: {
  filename: string;
  header: string[];
  rows: CsvValue[][];
}) {
  const download = () => {
    const blob = new Blob([toCsv(header, rows)], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.click();
    URL.revokeObjectURL(url);
  };
  return (
    <Button type="button" variant="secondary" size="sm" onClick={download}>
      <DownloadIcon aria-hidden="true" />
      Export CSV
    </Button>
  );
}

/** A plain part-of-the-agent name for one of `top_drivers` (which carries only ids, not a slot/label — ask #140). */
function driverLabel(driver: AnalyticsDriver, providers: ProviderSpec[]): string {
  const LIVEKIT_LABELS: Record<string, string> = {
    "livekit-agent": "Call minutes",
    "livekit-participant": "Participant minutes",
    "livekit-sip": "Phone minutes",
    "livekit-egress": "Recording",
  };
  if (LIVEKIT_LABELS[driver.provider_id]) return LIVEKIT_LABELS[driver.provider_id];
  const spec = providers.find((p) => p.id === driver.provider_id);
  const label = spec ? SLOT_LABELS[spec.kind as keyof typeof SLOT_LABELS] : undefined;
  return label ?? spec?.label ?? driver.provider_id;
}

/** "2026-01-02" -> "2 Jan", read as a calendar day (no time-zone shift). */
function formatDay(key: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(key);
  if (!match) return key;
  const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  return date.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

/**
 * Two series, actual vs. estimated cost per day (docs/v4/COSTS.md §5 item 6):
 * a solid `--chart-2` bar for actual and an outlined one for estimated — a
 * texture encoding (not colour alone) so the pair stays distinguishable
 * without relying on hue — with a legend, hairline gridlines at 0, half and
 * the maximum with tabular labels, and a visually hidden table carrying the
 * same figures for screen readers (spec 6.7: charts get a text alternative).
 */
function ByDayChart({ buckets }: { buckets: AnalyticsBucket[] }) {
  const max = Math.max(0.01, ...buckets.map((b) => Math.max(Number(b.cost_usd ?? 0), Number(b.estimated_usd ?? 0))));
  const ticks = [max, max / 2, 0];
  return (
    <div>
      <div className="mb-3 flex items-center gap-4 text-caption text-text-secondary">
        <span className="flex items-center gap-1.5">
          <span aria-hidden="true" className="size-2.5 rounded-sm bg-chart-2" />
          Actual
        </span>
        <span className="flex items-center gap-1.5">
          <span aria-hidden="true" className="size-2.5 rounded-sm border-2 border-chart-2" />
          Estimated
        </span>
      </div>
      <div className="flex gap-2">
        {/* Y axis: tabular labels beside the hairlines. */}
        <div aria-hidden="true" className="flex h-40 flex-col justify-between text-right text-caption text-text-tertiary tabular-nums">
          {ticks.map((tick, index) => (
            <span key={index} className="leading-none">
              {formatUsd(tick) ?? "$0"}
            </span>
          ))}
        </div>
        <div className="min-w-0 flex-1">
          {/* The hairlines share the bars' 160 px box, so they line up with the axis labels. */}
          <div className="relative h-40">
            <div aria-hidden="true" className="pointer-events-none absolute inset-0 flex flex-col justify-between">
              {ticks.map((_, index) => (
                <div key={index} className="h-px w-full bg-border" />
              ))}
            </div>
            <div role="img" aria-label="Bar chart of actual and estimated cost per day" className="relative flex h-40 items-end gap-2">
              {buckets.map((bucket) => {
                const actual = Number(bucket.cost_usd ?? 0);
                const estimated = Number(bucket.estimated_usd ?? 0);
                const actualPct = Math.max(actual > 0 ? 2 : 0, Math.round((actual / max) * 100));
                const estimatedPct = Math.max(estimated > 0 ? 2 : 0, Math.round((estimated / max) * 100));
                return (
                  <div
                    key={bucket.key}
                    className="group relative flex h-full min-w-0 flex-1 items-end justify-center gap-0.5"
                    title={`${formatDay(bucket.key)}: ${formatUsd(bucket.cost_usd) ?? "no price"} actual, ${formatUsd(bucket.estimated_usd) ?? "no estimate"} estimated`}
                  >
                    <div
                      className="w-full rounded-t-sm bg-chart-2 transition-opacity duration-(--duration-fast) group-hover:opacity-80"
                      style={{ height: `${actualPct}%` }}
                    />
                    <div
                      className="w-full rounded-t-sm border-2 border-b-0 border-chart-2 bg-card transition-opacity duration-(--duration-fast) group-hover:opacity-80"
                      style={{ height: `${estimatedPct}%` }}
                    />
                  </div>
                );
              })}
            </div>
          </div>
          <div className="mt-1.5 flex gap-1 text-caption text-text-tertiary tabular-nums">
            <span className="flex-1 truncate text-left">{formatDay(buckets[0]?.key ?? "")}</span>
            {buckets.length > 1 ? <span className="flex-1 truncate text-right">{formatDay(buckets[buckets.length - 1]?.key ?? "")}</span> : null}
          </div>
        </div>
      </div>
      {/* Text alternative for the bars above — same data, screen-reader and no-JS friendly. */}
      <table className="sr-only">
        <caption>Actual and estimated cost per day</caption>
        <thead>
          <tr>
            <th>Day</th>
            <th>Sessions</th>
            <th>Minutes</th>
            <th>Cost</th>
            <th>Estimated</th>
          </tr>
        </thead>
        <tbody>
          {buckets.map((bucket) => (
            <tr key={bucket.key}>
              <td>{formatDay(bucket.key)}</td>
              <td>{bucket.sessions}</td>
              <td>{bucket.minutes}</td>
              <td>{formatUsd(bucket.cost_usd) ?? "no price"}</td>
              <td>{formatUsd(bucket.estimated_usd) ?? "no estimate"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TopDriversTable({ drivers, providers }: { drivers: AnalyticsDriver[]; providers: ProviderSpec[] }) {
  const columns: ResponsiveTableColumn<AnalyticsDriver>[] = [
    {
      id: "label",
      header: "Part of the agent",
      cell: (d) => (
        <span className="flex flex-col">
          <span className="font-medium text-foreground">{driverLabel(d, providers)}</span>
          <span className="font-mono text-caption text-text-secondary">
            {d.provider_id}
            {d.model ? ` · ${d.model}` : ""}
          </span>
        </span>
      ),
    },
    { id: "cost", header: "Cost", align: "end", cell: (d) => <span className="tabular-nums">{formatUsd(d.cost_usd) ?? EMPTY_VALUE}</span> },
    {
      id: "share",
      header: "Share",
      align: "end",
      cell: (d) => <span className="tabular-nums text-text-secondary">{d.share_pct.toFixed(0)}%</span>,
    },
    {
      id: "estimated",
      header: "Estimated",
      align: "end",
      cell: (d) => <span className="tabular-nums text-text-secondary">{formatUsd(d.estimated_usd) ?? "no estimate"}</span>,
    },
  ];

  return (
    <ResponsiveTable<AnalyticsDriver>
      columns={columns}
      rows={drivers}
      label="Top cost drivers"
      getRowKey={(d, index) => `${d.provider_id}-${d.unit}-${index}`}
      renderCard={(d) => (
        <div className="flex items-center justify-between gap-2">
          <span className="font-medium text-foreground">{driverLabel(d, providers)}</span>
          <span className="text-caption tabular-nums text-text-secondary">{formatUsd(d.cost_usd) ?? EMPTY_VALUE}</span>
        </div>
      )}
    />
  );
}

/** An agent's health in the range, from the shared lifecycle tones: any failure needs a look. */
export function agentHealth(bucket: Pick<AnalyticsBucket, "failed" | "sessions">): LifecycleStatus {
  return (bucket.failed ?? 0) > 0
    ? { tone: lifecycleStatus("needs_review").tone, label: "Needs review" }
    : { tone: lifecycleStatus("ready").tone, label: "Healthy" };
}

function ByAgentTable({ buckets }: { buckets: AnalyticsBucket[] }) {
  const columns: ResponsiveTableColumn<AnalyticsBucket>[] = [
    { id: "agent", header: "Agent", cell: (b) => <span className="font-medium text-foreground">{b.key}</span> },
    {
      id: "health",
      header: "Health",
      cell: (b) => {
        const health = agentHealth(b);
        return (
          <StatusPill tone={health.tone} size="sm">
            {health.label}
          </StatusPill>
        );
      },
    },
    { id: "sessions", header: "Sessions", align: "end", cell: (b) => <span className="tabular-nums">{b.sessions}</span> },
    { id: "minutes", header: "Minutes", align: "end", cell: (b) => <span className="tabular-nums">{minutesText(b.minutes)}</span> },
    { id: "cost", header: "Cost", align: "end", cell: (b) => <span className="tabular-nums">{formatUsd(b.cost_usd) ?? EMPTY_VALUE}</span> },
    { id: "failed", header: "Failed", align: "end", cell: (b) => <span className="tabular-nums text-text-secondary">{b.failed}</span> },
  ];

  return (
    <ResponsiveTable<AnalyticsBucket>
      columns={columns}
      rows={buckets}
      label="Sessions by agent"
      getRowKey={(b) => b.key}
      renderCard={(b) => {
        const health = agentHealth(b);
        return (
          <div className="flex items-center justify-between gap-2">
            <div className="flex min-w-0 flex-col gap-1">
              <span className="truncate font-medium text-foreground">{b.key}</span>
              <span className="text-caption tabular-nums text-text-secondary">{`${b.sessions} sessions · ${formatUsd(b.cost_usd) ?? EMPTY_VALUE}`}</span>
            </div>
            <StatusPill tone={health.tone} size="sm">
              {health.label}
            </StatusPill>
          </div>
        );
      }}
    />
  );
}
