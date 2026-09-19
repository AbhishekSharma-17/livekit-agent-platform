"use client";

import * as React from "react";
import Link from "next/link";
import { ArrowLeftIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useSessionDetail } from "@/components/console/lib/api-hooks";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { PageHeader } from "@/components/console/shared/page-header";
import { EventsTimeline } from "@/components/console/sessions/events-timeline";

export function SessionDetailView({ sessionId }: { sessionId: string }) {
  const { data: session, isLoading, isError, error, refetch } = useSessionDetail(sessionId);

  return (
    <div>
      <Link href="/console/sessions" className="mb-4 flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeftIcon className="size-3.5" /> Sessions
      </Link>

      {isLoading ? (
        <Skeleton className="h-64 w-full" />
      ) : isError || !session ? (
        <ErrorBanner message={`Could not load this session: ${errorMessage(error)}`} onRetry={() => refetch()} />
      ) : (
        <>
          <PageHeader
            title={session.agent_name}
            description={`${session.room_name} · config v${session.config_version}`}
            actions={<Badge>{session.status}</Badge>}
          />

          <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat label="Mode" value={session.pipeline_mode} />
            <Stat label="Created" value={new Date(session.created_at).toLocaleString()} />
            <Stat label="Started" value={session.started_at ? new Date(session.started_at).toLocaleString() : "—"} />
            <Stat label="Ended" value={session.ended_at ? new Date(session.ended_at).toLocaleString() : "—"} />
          </div>

          {session.error ? (
            <div className="mb-4">
              <ErrorBanner message={session.error} />
            </div>
          ) : null}

          {session.usage ? (
            <div className="mb-4 rounded-xl border border-border bg-card p-4">
              <h3 className="mb-2 text-sm font-semibold">Usage</h3>
              <UsageSummary usage={session.usage} />
            </div>
          ) : null}

          <div className="grid gap-4 lg:grid-cols-2">
            <div className="rounded-xl border border-border bg-card p-4">
              <h3 className="mb-2 text-sm font-semibold">Transcript</h3>
              {!session.transcript || session.transcript.length === 0 ? (
                <p className="text-sm text-muted-foreground">No transcript recorded.</p>
              ) : (
                <div className="max-h-96 space-y-2 overflow-y-auto">
                  {session.transcript.map((turn, index) => (
                    <div key={index} className={turn.role === "assistant" ? "text-right" : "text-left"}>
                      <span
                        className={
                          turn.role === "assistant"
                            ? "inline-block rounded-lg bg-primary/10 px-3 py-1.5 text-sm"
                            : "inline-block rounded-lg bg-muted px-3 py-1.5 text-sm"
                        }
                      >
                        {turn.text}
                        {turn.interrupted ? <span className="ml-1 text-xs text-muted-foreground">(interrupted)</span> : null}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="rounded-xl border border-border bg-card p-4">
              <h3 className="mb-2 text-sm font-semibold">Events</h3>
              <div className="max-h-96 overflow-y-auto">
                <EventsTimeline sessionId={sessionId} />
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-border bg-card px-3 py-2">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="truncate text-sm font-medium">{value}</p>
    </div>
  );
}

/** `session_usage_updated` dumps `AgentSessionUsage` as a flat dict of counters/durations. */
export function formatUsageLabel(key: string): string {
  return key
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

export function formatUsageValue(value: unknown): string {
  if (typeof value === "number") {
    return Number.isInteger(value) ? value.toLocaleString() : value.toFixed(2);
  }
  if (typeof value === "string" || typeof value === "boolean") return String(value);
  if (value === null || value === undefined) return "—";
  return JSON.stringify(value);
}

/**
 * `usage` is an untyped dict (docs/CONTRACTS.md §7 `SessionOut.usage: dict | None`)
 * — its shape is whatever `AgentSessionUsage` happens to carry, so each entry
 * becomes its own tile rather than assuming specific keys exist.
 */
function UsageSummary({ usage }: { usage: Record<string, unknown> }) {
  const entries = Object.entries(usage);
  if (entries.length === 0) {
    return <p className="text-sm text-muted-foreground">No usage recorded.</p>;
  }
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      {entries.map(([key, value]) => (
        <Stat key={key} label={formatUsageLabel(key)} value={formatUsageValue(value)} />
      ))}
    </div>
  );
}
