"use client";

import * as React from "react";
import Link from "next/link";
import { PhoneIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, RelativeTime, ResponsiveTable, Section, StatusChip } from "@/components/shared";
import type { ResponsiveTableColumn } from "@/components/shared/responsive-table";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { formatDuration } from "@/lib/format";
import type { CallOut } from "@/contracts/lkap-contracts";

import { CallControls } from "./call-controls";
import { useCalls } from "./hooks";
import { AMD_RESULT_LABEL, callStatusMeta, TRANSFER_MODE_LABEL } from "./model";

function duration(call: CallOut): string {
  if (!call.answered_at) return "—";
  const end = call.ended_at ? Date.parse(call.ended_at) : Date.now();
  return formatDuration(end - Date.parse(call.answered_at));
}

/**
 * V5-32/36: what answered an outbound call (`amd_result`) and how it was
 * handed over (`transfer_mode`, `transfer_summary`) — under the status chip,
 * next to the existing `transfer_to` / `hangup_reason` lines. A summary can
 * run to 2000 characters, so it is truncated inline with a dialog for the
 * full text (never a side sheet).
 */
function CallOutcome({ call }: { call: CallOut }) {
  const [showSummary, setShowSummary] = React.useState(false);
  const amdLabel = call.amd_result ? AMD_RESULT_LABEL[call.amd_result] : null;
  const transferLabel = call.transfer_mode ? TRANSFER_MODE_LABEL[call.transfer_mode] : null;
  if (!amdLabel && !transferLabel) return null;
  return (
    <div className="flex flex-col gap-0.5">
      {amdLabel ? <span className="text-xs text-muted-foreground">Answered by: {amdLabel}</span> : null}
      {transferLabel ? <span className="text-xs text-muted-foreground">{transferLabel}</span> : null}
      {call.transfer_summary ? (
        <>
          <Button
            type="button"
            variant="link"
            className="h-auto justify-start p-0 text-xs"
            onClick={() => setShowSummary(true)}
          >
            View summary
          </Button>
          <Dialog open={showSummary} onOpenChange={setShowSummary}>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Call summary</DialogTitle>
              </DialogHeader>
              <p className="max-h-96 overflow-y-auto text-sm whitespace-pre-wrap">{call.transfer_summary}</p>
            </DialogContent>
          </Dialog>
        </>
      ) : null}
    </div>
  );
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
            <CallOutcome call={call} />
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
                <CallOutcome call={call} />
                <CallControls call={call} compact />
              </div>
            );
          }}
        />
      )}
    </Section>
  );
}
