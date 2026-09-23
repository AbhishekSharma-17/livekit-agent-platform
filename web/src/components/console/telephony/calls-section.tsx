"use client";

import * as React from "react";
import Link from "next/link";
import { PhoneIcon } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, RelativeTime, ResponsiveTable, Section, StatusChip } from "@/components/shared";
import type { ResponsiveTableColumn } from "@/components/shared/responsive-table";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { formatDuration } from "@/lib/format";
import type { CallOut } from "@/contracts/lkap-contracts";

import { CallControls } from "./call-controls";
import { useCalls } from "./hooks";
import { callStatusMeta } from "./model";

function duration(call: CallOut): string {
  if (!call.answered_at) return "—";
  const end = call.ended_at ? Date.parse(call.ended_at) : Date.now();
  return formatDuration(end - Date.parse(call.answered_at));
}

/** Calls log (V2-17): inbound and outbound legs, newest first; polls while any call is live. */
export function CallsSection() {
  const { data, isLoading, isError, error, refetch } = useCalls();
  const calls = data?.items ?? [];

  const columns: ResponsiveTableColumn<CallOut>[] = [
    {
      id: "when",
      header: "When",
      cell: (call) => {
        const at = call.started_at ?? call.answered_at ?? call.ended_at;
        return at ? <RelativeTime iso={at} className="text-sm" /> : <span className="text-sm">—</span>;
      },
    },
    {
      id: "direction",
      header: "Direction",
      cell: (call) => <span className="text-sm">{call.direction === "inbound" ? "Inbound" : "Outbound"}</span>,
    },
    {
      id: "parties",
      header: "From → To",
      cell: (call) => (
        <span className="font-mono text-[0.8125rem]">
          {call.from_e164 || "?"} → {call.to_e164 || "?"}
        </span>
      ),
    },
    {
      id: "status",
      header: "Status",
      cell: (call) => {
        const meta = callStatusMeta(call.status);
        return (
          <div className="flex flex-col gap-0.5">
            <StatusChip tone={meta.tone} size="sm" dot>
              {meta.label}
            </StatusChip>
            {call.transfer_to ? (
              <span className="text-xs text-muted-foreground">to {call.transfer_to}</span>
            ) : call.hangup_reason && call.status !== "completed" ? (
              <span className="text-xs text-muted-foreground">{call.hangup_reason}</span>
            ) : null}
          </div>
        );
      },
    },
    { id: "duration", header: "Duration", cell: (call) => <span className="text-sm tabular-nums">{duration(call)}</span> },
    {
      id: "session",
      header: "Session",
      interactive: true,
      cell: (call) =>
        call.session_id ? (
          <Link className="text-sm underline-offset-4 hover:underline" href={`/console/sessions/${call.session_id}`}>
            Open
          </Link>
        ) : (
          <span className="text-sm text-muted-foreground">—</span>
        ),
    },
    {
      id: "actions",
      header: <span className="sr-only">Actions</span>,
      align: "end",
      interactive: true,
      cell: (call) => <CallControls call={call} compact />,
    },
  ];

  return (
    <Section id="calls" title="Calls" description="Every phone call, inbound and outbound.">
      {isLoading ? (
        <div className="p-5">
          <Skeleton className="h-10 w-full" />
        </div>
      ) : isError ? (
        <div className="p-5">
          <ErrorBanner message={`Couldn't load calls: ${errorMessage(error)}`} onRetry={() => refetch()} />
        </div>
      ) : calls.length === 0 ? (
        <EmptyState
          compact
          icon={PhoneIcon}
          title="No calls yet"
          description="Inbound calls appear once a number is routed; place one from an agent's Test call menu."
          className="p-5"
        />
      ) : (
        <ResponsiveTable<CallOut>
          columns={columns}
          rows={calls}
          label="Calls"
          getRowKey={(call) => call.id}
          renderCard={(call) => {
            const meta = callStatusMeta(call.status);
            return (
              <div className="flex flex-col gap-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono text-sm">
                    {call.from_e164 || "?"} → {call.to_e164 || "?"}
                  </span>
                  <StatusChip tone={meta.tone} size="sm" dot>
                    {meta.label}
                  </StatusChip>
                </div>
                <CallControls call={call} compact />
              </div>
            );
          }}
        />
      )}
    </Section>
  );
}
