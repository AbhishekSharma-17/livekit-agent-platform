"use client";

import Link from "next/link";
import { BotIcon, CircleAlertIcon, MessagesSquareIcon, RadioIcon, type LucideIcon } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import { StatCard, StatGrid } from "@/components/shared/data-display";
import { LoadingRegion } from "@/components/shared/loading-state";
import { useAgents, useSessions } from "@/components/console/lib/api-hooks";
import { ErrorBanner } from "@/components/console/shared/error-banner";
import type { AgentOut, SessionOut } from "@/contracts/lkap-contracts";
import { pluralize, toMillis } from "@/lib/format";

const SEVEN_DAYS_MS = 7 * 24 * 60 * 60 * 1000;

/**
 * The agents the Overview counts: archived agents are gone from every list
 * and never answer calls, so they count neither as agents nor as live.
 */
export function activeAgents(agents: readonly AgentOut[]): AgentOut[] {
  return agents.filter((agent) => !agent.archived_at);
}

/** Live means published and not archived: the agent answers calls at its public link. */
export function liveAgents(agents: readonly AgentOut[]): AgentOut[] {
  return activeAgents(agents).filter((agent) => agent.published);
}

export interface WeekCounts {
  sessions: number;
  failed: number;
  active: number;
  /**
   * The api returned one page and every row on it is from the last 7 days,
   * so older pages may hold more: the counts are a lower bound ("50+").
   */
  partial: boolean;
}

/** Sessions, failures and live calls in the last 7 days, from one page of `GET /v1/sessions`. */
export function weekCounts(page: { items: readonly SessionOut[]; total: number }, now: number = Date.now()): WeekCounts {
  const recent = page.items.filter((session) => now - toMillis(session.created_at) <= SEVEN_DAYS_MS);
  return {
    sessions: recent.length,
    failed: recent.filter((session) => session.status === "failed").length,
    active: recent.filter((session) => session.status === "active").length,
    partial: page.total > page.items.length && recent.length === page.items.length,
  };
}

function StatLink({
  href,
  label,
  value,
  icon,
  hint,
}: {
  href: string;
  label: string;
  value: string;
  icon: LucideIcon;
  hint: string;
}) {
  return (
    <Link href={href} data-slot="overview-stat" className="group block rounded-lg">
      <StatCard
        label={label}
        value={value}
        icon={icon}
        hint={hint}
        className="h-full transition-colors duration-(--duration-fast) group-hover:bg-muted"
      />
    </Link>
  );
}

function StatsSkeleton() {
  return (
    <LoadingRegion label="Loading the overview numbers">
      <StatGrid>
        {[0, 1, 2, 3].map((index) => (
          <div key={index} className="flex flex-col gap-2.5 rounded-lg border border-border bg-card p-4">
            <Skeleton className="h-3.5 w-24" />
            <Skeleton className="h-7 w-12" />
            <Skeleton className="h-3.5 w-32" />
          </div>
        ))}
      </StatGrid>
    </LoadingRegion>
  );
}

/**
 * The Overview's stat grid (decision D6): agents, live agents, sessions and
 * failures in the last 7 days. Each card opens the list it counts.
 */
export function OverviewStats() {
  const agentsQuery = useAgents();
  const sessionsQuery = useSessions();

  if (agentsQuery.isLoading || sessionsQuery.isLoading) return <StatsSkeleton />;

  if (agentsQuery.isError || sessionsQuery.isError) {
    return (
      <ErrorBanner
        error={agentsQuery.error ?? sessionsQuery.error}
        context={{ action: "load the overview numbers" }}
        onRetry={() => {
          if (agentsQuery.isError) void agentsQuery.refetch();
          if (sessionsQuery.isError) void sessionsQuery.refetch();
        }}
      />
    );
  }

  const all = agentsQuery.data?.items ?? [];
  const active = activeAgents(all);
  const live = liveAgents(all);
  const archived = all.length - active.length;
  const week = weekCounts(sessionsQuery.data ?? { items: [], total: 0 });
  const plus = week.partial ? "+" : "";

  return (
    <section aria-label="At a glance">
      <StatGrid>
        <StatLink
          href="/console/agents"
          label="Agents"
          icon={BotIcon}
          value={active.length.toLocaleString()}
          hint={archived > 0 ? `${pluralize(archived, "archived agent", "archived agents")} not counted` : "In this workspace"}
        />
        <StatLink
          href="/console/agents?status=live"
          label="Live"
          icon={RadioIcon}
          value={live.length.toLocaleString()}
          hint={live.length > 0 ? "Answering calls at their public link" : "Publish an agent to share its link"}
        />
        <StatLink
          href="/console/sessions?range=7d"
          label="Sessions, last 7 days"
          icon={MessagesSquareIcon}
          value={`${week.sessions.toLocaleString()}${plus}`}
          hint={week.active > 0 ? `${week.active.toLocaleString()} in progress now` : "Test calls and public calls"}
        />
        <StatLink
          href="/console/sessions?status=failed&range=7d"
          label="Failed, last 7 days"
          icon={CircleAlertIcon}
          value={`${week.failed.toLocaleString()}${week.partial && week.failed > 0 ? plus : ""}`}
          hint={week.failed > 0 ? "Open them to see what went wrong" : "No failed calls"}
        />
      </StatGrid>
    </section>
  );
}
