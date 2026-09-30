"use client";

import * as React from "react";
import Link from "next/link";
import { PhoneIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { EmptyState } from "@/components/shared/empty-state";
import { Highlight, ListNoMatches, ListSearchField, useListSearch } from "@/components/shared/list-search";
import { SkeletonRows } from "@/components/shared/loading-state";
import { RelativeTime } from "@/components/shared/relative-time";
import { ResponsiveTable, type ResponsiveTableColumn } from "@/components/shared/responsive-table";
import { Section, SectionRow } from "@/components/shared/section";
import { StatusPill } from "@/components/shared/status-chip";
import { ErrorBanner } from "@/components/console/shared/error-banner";
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

function directionLabel(call: CallOut): string {
  return call.direction === "inbound" ? "Inbound" : "Outbound";
}

function partiesLabel(call: CallOut): string {
  return `${call.from_e164 || "?"} → ${call.to_e164 || "?"}`;
}

/**
 * V5-32/36: what answered an outbound call (`amd_result`) and how it was
 * handed over (`transfer_mode`, `transfer_summary`) — under the status pill,
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
      {amdLabel ? <span className="text-caption text-text-secondary">Answered by: {amdLabel}</span> : null}
      {transferLabel ? <span className="text-caption text-text-secondary">{transferLabel}</span> : null}
      {call.transfer_summary ? (
        <>
          <Button type="button" variant="link" className="justify-start text-caption" onClick={() => setShowSummary(true)}>
            View summary
          </Button>
          <Dialog open={showSummary} onOpenChange={setShowSummary}>
            <DialogContent size="md">
              <DialogHeader>
                <DialogTitle>Call summary</DialogTitle>
              </DialogHeader>
              <DialogBody>
                <p className="text-body whitespace-pre-wrap text-foreground">{call.transfer_summary}</p>
              </DialogBody>
            </DialogContent>
          </Dialog>
        </>
      ) : null}
    </div>
  );
}

/**
 * Calls log (V2-17): inbound and outbound legs, newest first; polls while any
 * call is live. Search filters the loaded page (the api has no call search).
 */
export function CallsSection() {
  const { data, isLoading, isError, error, refetch } = useCalls();
  const calls = React.useMemo(() => data?.items ?? [], [data]);
  const search = useListSearch("telephony-calls", calls, (call) => [
    call.from_e164,
    call.to_e164,
    directionLabel(call),
    callStatusMeta(call.status).label,
    call.transfer_to,
  ]);
  const query = search.query;

  const columns: ResponsiveTableColumn<CallOut>[] = [
    {
      id: "when",
      header: "When",
      cell: (call) => {
        const at = call.started_at ?? call.answered_at ?? call.ended_at;
        return at ? <RelativeTime iso={at} className="text-label" /> : <span className="text-label">—</span>;
      },
    },
    {
      id: "direction",
      header: "Direction",
      cell: (call) => <span className="text-label">{directionLabel(call)}</span>,
    },
    {
      id: "parties",
      header: "From → To",
      cell: (call) => (
        <span className="font-mono text-label tabular-nums">
          <Highlight text={partiesLabel(call)} query={query} />
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
            <StatusPill tone={meta.tone} size="sm">
              {meta.label}
            </StatusPill>
            {call.transfer_to ? (
              <span className="font-mono text-caption text-text-secondary tabular-nums">to {call.transfer_to}</span>
            ) : call.hangup_reason && call.status !== "completed" ? (
              <span className="text-caption text-text-secondary">{call.hangup_reason}</span>
            ) : null}
            <CallOutcome call={call} />
          </div>
        );
      },
    },
    {
      id: "duration",
      header: "Duration",
      align: "end",
      cell: (call) => <span className="text-label tabular-nums">{duration(call)}</span>,
    },
    {
      id: "session",
      header: "Session",
      interactive: true,
      cell: (call) =>
        call.session_id ? (
          <Link
            className="text-label font-medium text-brand underline-offset-3 hover:underline"
            href={`/console/sessions/${call.session_id}`}
          >
            Open
          </Link>
        ) : (
          <span className="text-label text-text-secondary">—</span>
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
        <SectionRow>
          <SkeletonRows label="Loading calls" rows={3} rowClassName="h-12" />
        </SectionRow>
      ) : isError ? (
        <SectionRow>
          <ErrorBanner error={error} context={{ action: "load calls" }} onRetry={() => void refetch()} />
        </SectionRow>
      ) : calls.length === 0 ? (
        <SectionRow>
          <EmptyState
            variant="plain"
            icon={PhoneIcon}
            title="No calls yet"
            description="Inbound calls appear once a number is routed; place one from an agent's Test call menu."
          />
        </SectionRow>
      ) : (
        <>
          {search.showSearch ? (
            <SectionRow>
              <ListSearchField search={search} label="Search calls" total={calls.length} className="mb-0" />
            </SectionRow>
          ) : null}
          {search.noMatches ? (
            <SectionRow>
              <ListNoMatches search={search} items="calls" />
            </SectionRow>
          ) : (
            <ResponsiveTable<CallOut>
              columns={columns}
              rows={search.filtered}
              label="Calls"
              getRowKey={(call) => call.id}
              renderCard={(call) => {
                const meta = callStatusMeta(call.status);
                const at = call.started_at ?? call.answered_at ?? call.ended_at;
                return (
                  <div className="flex flex-col gap-2">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="min-w-0 font-mono text-label break-all tabular-nums">
                        <Highlight text={partiesLabel(call)} query={query} />
                      </span>
                      <StatusPill tone={meta.tone} size="sm">
                        {meta.label}
                      </StatusPill>
                    </div>
                    <p className="text-caption text-text-secondary tabular-nums">
                      {directionLabel(call)}
                      {at ? (
                        <>
                          {" · "}
                          <RelativeTime iso={at} />
                        </>
                      ) : null}
                      {call.answered_at ? ` · ${duration(call)}` : null}
                    </p>
                    <CallOutcome call={call} />
                    <CallControls call={call} compact />
                  </div>
                );
              }}
            />
          )}
        </>
      )}
    </Section>
  );
}
