"use client";

import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useSessionEvents } from "@/components/console/lib/api-hooks";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import type { SessionEventOut } from "@/contracts/lkap-contracts";

/**
 * A one-line summary badge for events whose payload has an obvious headline
 * (tool name, duration, status) — see `agent/src/lkap_agent/observability.py`
 * for the payload shapes this reads (`tool_call_started`/`tool_call_ended`
 * carry `tool`/`duration_ms`/`status`; everything else falls back to the raw
 * JSON below it, which is always shown regardless).
 */
function eventHeadline(event: SessionEventOut): string | null {
  const payload = event.payload;
  if (!payload || typeof payload !== "object") return null;
  const p = payload as Record<string, unknown>;

  if (event.type === "tool_call_started" && typeof p.tool === "string") {
    return p.tool;
  }
  if (event.type === "tool_call_ended") {
    const parts: string[] = [];
    if (typeof p.status === "string") parts.push(p.status);
    if (typeof p.duration_ms === "number") parts.push(`${p.duration_ms}ms`);
    return parts.length > 0 ? parts.join(" · ") : null;
  }
  if (event.type === "agent_state" && typeof p.state === "string") {
    return p.state;
  }
  return null;
}

const STATUS_BADGE_VARIANT: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  success: "secondary",
  error: "destructive",
};

export function EventsTimeline({ sessionId }: { sessionId: string }) {
  const { data, isLoading, isError, error, refetch } = useSessionEvents(sessionId);

  if (isLoading) return <Skeleton className="h-32 w-full" />;
  if (isError) return <ErrorBanner message={errorMessage(error)} onRetry={() => refetch()} />;

  const events = data?.items ?? [];
  if (events.length === 0) {
    return <p className="text-sm text-muted-foreground">No events recorded.</p>;
  }

  return (
    <ol className="space-y-2">
      {events.map((event) => {
        const headline = eventHeadline(event);
        const status = typeof event.payload?.status === "string" ? (event.payload.status as string) : undefined;
        return (
          <li key={event.id} className="rounded-lg border border-border bg-muted/30 p-3 text-sm">
            <div className="mb-1 flex items-center justify-between gap-2">
              <div className="flex min-w-0 items-center gap-1.5">
                <Badge variant="secondary">{event.type}</Badge>
                {headline ? (
                  <Badge variant={status ? (STATUS_BADGE_VARIANT[status] ?? "outline") : "outline"} className="truncate">
                    {headline}
                  </Badge>
                ) : null}
              </div>
              <span className="shrink-0 text-xs text-muted-foreground">
                {new Date(event.ts).toLocaleTimeString()}
              </span>
            </div>
            <pre className="overflow-x-auto whitespace-pre-wrap font-mono text-xs text-foreground/80">
              {JSON.stringify(event.payload, null, 2)}
            </pre>
          </li>
        );
      })}
    </ol>
  );
}
