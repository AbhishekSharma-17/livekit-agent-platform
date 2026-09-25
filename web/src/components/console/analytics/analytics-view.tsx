"use client";

import * as React from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/shared/empty-state";
import { Section, SectionRow } from "@/components/shared/section";
import { ResponsiveTable, type ResponsiveTableColumn } from "@/components/shared/responsive-table";
import { useProviders } from "@/components/console/lib/api-hooks";
import { SLOT_LABELS } from "@/components/console/lib/cost-hooks";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { formatUsd } from "@/components/console/sessions/session-model";
import { api } from "@/lib/api";
import type { AnalyticsBucket, AnalyticsDriver, AnalyticsSummary, ProviderSpec } from "@/contracts/lkap-contracts";
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
  const providers = useProviders().data?.providers ?? [];
  const accuracy = summary.accuracy_pct != null ? `accuracy ${summary.accuracy_pct.toFixed(0)} %` : undefined;
  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <StatTile label="Sessions" value={(summary.sessions ?? 0).toLocaleString()} />
        <StatTile label="Minutes" value={(summary.minutes ?? 0).toLocaleString(undefined, { maximumFractionDigits: 1 })} />
        <StatTile label="Cost" value={formatUsd(summary.cost_usd) ?? "—"} />
        <StatTile label="Estimated" value={formatUsd(summary.estimated_usd) ?? "no estimate"} sub={accuracy} />
        <StatTile
          label="Failed"
          value={(summary.failed ?? 0).toLocaleString()}
          tone={(summary.failed ?? 0) > 0 ? "danger" : undefined}
        />
      </div>

      <Section id="by-day" title="Sessions cost by day">
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

      <Section id="cost-drivers" title="Top cost drivers">
        <SectionRow>
          {(summary.top_drivers ?? []).length === 0 ? (
            <EmptyState icon={BarChart3Icon} compact title="No priced usage in this range" />
          ) : (
            <TopDriversTable drivers={summary.top_drivers ?? []} providers={providers} />
          )}
        </SectionRow>
      </Section>

      <p className="max-w-[70ch] text-xs text-pretty text-muted-foreground">
        Actual cost is computed from usage at list prices; OpenRouter sessions use live prices and can be
        reconciled with OpenRouter&apos;s charge. Vendor invoices may differ (included minutes, volume tiers,
        taxes).
      </p>
    </div>
  );
}

function StatTile({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: "danger" }) {
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      <p className={tone === "danger" && value !== "0" ? "mt-1 font-heading text-2xl font-semibold text-danger-text" : "mt-1 font-heading text-2xl font-semibold text-foreground"}>
        {value}
      </p>
      {sub ? <p className="mt-0.5 text-xs text-muted-foreground">{sub}</p> : null}
    </div>
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

/**
 * Two series, actual vs. estimated cost per day (docs/v4/COSTS.md §5 item 6):
 * a solid `bg-brand` bar for actual, an outlined/hatched one for estimated —
 * a texture encoding (not color alone) so the pair stays distinguishable
 * without relying on hue (the dataviz guidance's CVD-safety rule), with a
 * legend since there are now two series. The accessible table below carries
 * both figures for screen readers and the no-JS case.
 */
function ByDayChart({ buckets }: { buckets: AnalyticsBucket[] }) {
  const max = Math.max(1, ...buckets.map((b) => Math.max(Number(b.cost_usd ?? 0), Number(b.estimated_usd ?? 0))));
  return (
    <div>
      <div className="mb-2 flex items-center gap-4 text-xs text-muted-foreground">
        <span className="flex items-center gap-1.5">
          <span aria-hidden="true" className="size-2.5 rounded-xs bg-brand" />
          Actual
        </span>
        <span className="flex items-center gap-1.5">
          <span aria-hidden="true" className="size-2.5 rounded-xs border-2 border-brand bg-transparent" />
          Estimated
        </span>
      </div>
      <div role="img" aria-label="Bar chart of actual and estimated cost per day" className="flex h-40 items-end gap-2">
        {buckets.map((bucket) => {
          const actual = Number(bucket.cost_usd ?? 0);
          const estimated = Number(bucket.estimated_usd ?? 0);
          const actualPct = Math.max(actual > 0 ? 2 : 0, Math.round((actual / max) * 100));
          const estimatedPct = Math.max(estimated > 0 ? 2 : 0, Math.round((estimated / max) * 100));
          return (
            <div
              key={bucket.key}
              className="group relative flex h-full min-w-0 flex-1 items-end justify-center gap-0.5"
              title={`${bucket.key}: ${formatUsd(bucket.cost_usd) ?? "no price"} actual, ${formatUsd(bucket.estimated_usd) ?? "no estimate"} estimated`}
            >
              <div className="w-full rounded-t-sm bg-brand transition-opacity group-hover:opacity-80" style={{ height: `${actualPct}%` }} />
              <div
                className="w-full rounded-t-sm border-2 border-brand bg-transparent transition-opacity group-hover:opacity-80"
                style={{ height: `${estimatedPct}%` }}
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
              <td>{bucket.key}</td>
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
          <span className="font-mono text-xs text-muted-foreground">
            {d.provider_id}
            {d.model ? ` · ${d.model}` : ""}
          </span>
        </span>
      ),
    },
    { id: "cost", header: "Cost", className: "text-right", cell: (d) => <span className="font-mono tabular-nums">{formatUsd(d.cost_usd) ?? "—"}</span> },
    { id: "share", header: "Share", className: "text-right", cell: (d) => <span className="font-mono tabular-nums text-muted-foreground">{d.share_pct.toFixed(0)}%</span> },
    {
      id: "estimated",
      header: "Estimated",
      className: "text-right",
      cell: (d) => <span className="font-mono tabular-nums text-muted-foreground">{formatUsd(d.estimated_usd) ?? "no estimate"}</span>,
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
          <span className="font-mono text-xs tabular-nums text-muted-foreground">{formatUsd(d.cost_usd) ?? "—"}</span>
        </div>
      )}
    />
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
