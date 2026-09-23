"use client";

import * as React from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/shared/empty-state";
import { Section, SectionRow } from "@/components/shared/section";
import { ResponsiveTable, type ResponsiveTableColumn } from "@/components/shared/responsive-table";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { formatUsd } from "@/components/console/sessions/session-model";
import { api } from "@/lib/api";
import type { AnalyticsBucket, AnalyticsSummary } from "@/contracts/lkap-contracts";
import { BarChart3Icon } from "lucide-react";

/**
 * `/console/analytics?range=` (docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md §1, §2.6
 * "summary cards + by-day bars + by-agent table on the `dataviz`
 * guidance"). `GET /v1/analytics/summary` (`routers/analytics.py`) accepts
 * `today | 7d | 30d | all` — **not** the amendments' `90d`; this follows the
 * api, since that is the contract that actually exists.
 *
 * The by-day chart is a single-series magnitude bar chart (sessions per
 * day): one hue (`bg-brand`, the app's only accent, already
 * contrast-checked for both themes in `globals.css`), no legend needed for
 * one series (the section title names it), a visually-hidden data table
 * alongside the bars for the tabular/accessible view the dataviz guidance
 * asks every chart to carry, and a native `title` tooltip per bar.
 */
type Range = "today" | "7d" | "30d" | "all";
const RANGES: { value: Range; label: string }[] = [
  { value: "today", label: "Today" },
  { value: "7d", label: "Last 7 days" },
  { value: "30d", label: "Last 30 days" },
  { value: "all", label: "All time" },
];

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

export function AnalyticsView() {
  const router = useRouter();
  const pathname = usePathname() ?? "/console/analytics";
  const searchParams = useSearchParams();
  const range = readRange(searchParams);
  const query = useAnalyticsSummary(range);

  function setRange(next: string) {
    router.replace(`${pathname}?range=${next}`, { scroll: false });
  }

  return (
    <div className="space-y-6">
      <div className="flex justify-end">
        <Select value={range} onValueChange={setRange}>
          <SelectTrigger aria-label="Date range" className="w-40">
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
      </div>

      {query.isLoading ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-24 w-full" />
          ))}
        </div>
      ) : query.isError ? (
        <ErrorBanner message={`Couldn't load analytics — ${errorMessage(query.error)}`} onRetry={() => query.refetch()} />
      ) : query.data ? (
        <AnalyticsContent summary={query.data} />
      ) : null}
    </div>
  );
}

function AnalyticsContent({ summary }: { summary: AnalyticsSummary }) {
  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile label="Sessions" value={(summary.sessions ?? 0).toLocaleString()} />
        <StatTile label="Minutes" value={(summary.minutes ?? 0).toLocaleString(undefined, { maximumFractionDigits: 1 })} />
        <StatTile label="Cost" value={formatUsd(summary.cost_usd) ?? "—"} />
        <StatTile
          label="Failed"
          value={(summary.failed ?? 0).toLocaleString()}
          tone={(summary.failed ?? 0) > 0 ? "danger" : undefined}
        />
      </div>

      <Section id="by-day" title="Sessions by day">
        <SectionRow>
          {(summary.by_day ?? []).length === 0 ? (
            <EmptyState icon={BarChart3Icon} compact title="No sessions in this range" />
          ) : (
            <ByDayChart buckets={summary.by_day ?? []} />
          )}
        </SectionRow>
      </Section>

      <Section id="by-agent" title="By agent">
        <SectionRow>
          {(summary.by_agent ?? []).length === 0 ? (
            <EmptyState icon={BarChart3Icon} compact title="No sessions in this range" />
          ) : (
            <ByAgentTable buckets={summary.by_agent ?? []} />
          )}
        </SectionRow>
      </Section>
    </div>
  );
}

function StatTile({ label, value, tone }: { label: string; value: string; tone?: "danger" }) {
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      <p className={tone === "danger" && value !== "0" ? "mt-1 font-heading text-2xl font-semibold text-danger-text" : "mt-1 font-heading text-2xl font-semibold text-foreground"}>
        {value}
      </p>
    </div>
  );
}

function ByDayChart({ buckets }: { buckets: AnalyticsBucket[] }) {
  const max = Math.max(1, ...buckets.map((b) => b.sessions ?? 0));
  return (
    <div>
      <div role="img" aria-label="Bar chart of sessions per day" className="flex h-40 items-end gap-1">
        {buckets.map((bucket) => {
          const sessions = bucket.sessions ?? 0;
          const heightPct = Math.max(2, Math.round((sessions / max) * 100));
          return (
            <div
              key={bucket.key}
              className="group relative flex h-full min-w-0 flex-1 flex-col items-center justify-end"
              title={`${bucket.key}: ${sessions} session${sessions === 1 ? "" : "s"}`}
            >
              <div
                className="w-full rounded-t-sm bg-brand transition-opacity group-hover:opacity-80"
                style={{ height: `${heightPct}%` }}
              />
            </div>
          );
        })}
      </div>
      <div className="mt-1 flex gap-1 text-[0.625rem] text-muted-foreground">
        <span className="flex-1 truncate text-left">{buckets[0]?.key}</span>
        {buckets.length > 1 ? <span className="flex-1 truncate text-right">{buckets[buckets.length - 1]?.key}</span> : null}
      </div>
      {/* Accessible / tabular fallback for the bars above — same data, screen-reader and no-JS friendly. */}
      <table className="sr-only">
        <caption>Sessions per day</caption>
        <thead>
          <tr>
            <th>Day</th>
            <th>Sessions</th>
            <th>Minutes</th>
            <th>Cost</th>
          </tr>
        </thead>
        <tbody>
          {buckets.map((bucket) => (
            <tr key={bucket.key}>
              <td>{bucket.key}</td>
              <td>{bucket.sessions}</td>
              <td>{bucket.minutes}</td>
              <td>{formatUsd(bucket.cost_usd) ?? "no price"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ByAgentTable({ buckets }: { buckets: AnalyticsBucket[] }) {
  const columns: ResponsiveTableColumn<AnalyticsBucket>[] = [
    { id: "agent", header: "Agent", cell: (b) => <span className="font-medium text-foreground">{b.key}</span> },
    { id: "sessions", header: "Sessions", className: "text-right", cell: (b) => <span className="font-mono tabular-nums">{b.sessions}</span> },
    {
      id: "minutes",
      header: "Minutes",
      className: "text-right",
      cell: (b) => (
        <span className="font-mono tabular-nums">{(b.minutes ?? 0).toLocaleString(undefined, { maximumFractionDigits: 1 })}</span>
      ),
    },
    { id: "cost", header: "Cost", className: "text-right", cell: (b) => <span className="font-mono tabular-nums">{formatUsd(b.cost_usd) ?? "—"}</span> },
    { id: "failed", header: "Failed", className: "text-right", cell: (b) => <span className="font-mono tabular-nums text-muted-foreground">{b.failed}</span> },
  ];

  return (
    <ResponsiveTable<AnalyticsBucket>
      columns={columns}
      rows={buckets}
      label="Sessions by agent"
      getRowKey={(b) => b.key}
      renderCard={(b) => (
        <div className="flex items-center justify-between gap-2">
          <span className="font-medium text-foreground">{b.key}</span>
          <span className="font-mono text-xs tabular-nums text-muted-foreground">{b.sessions} sessions</span>
        </div>
      )}
    />
  );
}
