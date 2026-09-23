"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { CircleAlertIcon, SearchXIcon } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { CopyButton } from "@/components/shared/copy-button";
import { DescriptionList, type DescriptionItem } from "@/components/shared/description-list";
import { EmptyState } from "@/components/shared/empty-state";
import { Icon } from "@/components/shared/icon";
import { PageHeader } from "@/components/shared/page-header";
import { RelativeTime } from "@/components/shared/relative-time";
import { StatusChip, type StatusTone } from "@/components/shared/status-chip";
import { useSessionDetail } from "@/components/console/lib/api-hooks";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { useSetBreadcrumbs } from "@/components/console/shell/breadcrumb-context";
import type { SessionDetailOut } from "@/contracts/lkap-contracts";
import { ApiError } from "@/lib/api";
import { formatDuration } from "@/lib/format";

import { formatMs } from "./detail/builtin-event-kinds";
import { pickTab, visibleTabs } from "./detail/registry";
import { DEFAULT_SESSION_TAB_ID, sessionDetailSlots, sessionTabs } from "./detail/resolved";
import type { SessionTabDef } from "./detail/types";
import {
  channelLabel,
  formatUsd,
  pipelineModeLabel,
  SESSION_STATUS_LABEL,
  sentenceCase,
  sessionDurationMs,
  sessionStatusTone,
  sweptReason,
} from "./session-model";
import { describeUsage, UsageGroups } from "./session-usage";
import { collectTurns, pairToolCalls } from "./timeline-model";
import { useAllSessionEvents, useConnectionNames } from "./use-session-queries";
import { LoadingRegion } from "@/components/shared/loading-state";

// Kept importable from here: `tests/console-session-detail-view.test.ts` and older callers use this path.
export { formatUsageLabel, formatUsageValue } from "./session-usage";

/**
 * Session detail (docs/UI_UX_SPEC.md §4.10, §7.8 item 2;
 * docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md §2.4): header, stats strip, and the
 * tabs from the registry in `./detail` (V2-14 adds Recording, Cost and QA
 * there without touching this file — see `./README.md`).
 */
export function SessionDetailView({
  sessionId,
  tabs: tabsOverride,
}: {
  sessionId: string;
  /** Tests only: a tab list instead of the registry's. */
  tabs?: SessionTabDef[];
}) {
  const { data: session, isLoading, isError, error, refetch } = useSessionDetail(sessionId);

  useSetBreadcrumbs(
    session ? [{ label: "Sessions", href: "/console/sessions" }, { label: session.agent_name || "Session" }] : undefined,
  );

  if (isLoading) return <DetailSkeleton />;

  if (isError || !session) {
    if (error instanceof ApiError && error.status === 404) {
      return (
        <EmptyState
          icon={SearchXIcon}
          title="This session doesn't exist"
          description="It may belong to another workspace, or the link is wrong."
          action={
            <Button asChild variant="outline">
              <Link href="/console/sessions">All sessions</Link>
            </Button>
          }
        />
      );
    }
    return <ErrorBanner message={`Couldn't load this session — ${errorMessage(error)}`} onRetry={() => refetch()} />;
  }

  return <SessionDetailContent session={session} tabs={tabsOverride ?? sessionTabs()} />;
}

function DetailSkeleton() {
  return (
    <LoadingRegion label="Loading session" className="flex flex-col gap-6">
      <div className="space-y-3">
        <Skeleton className="h-7 w-64" />
        <div className="flex flex-wrap gap-2">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-6 w-24" />
          ))}
        </div>
      </div>
      <Skeleton className="h-28 w-full" />
      <Skeleton className="h-8 w-80" />
      <Skeleton className="h-64 w-full" />
    </LoadingRegion>
  );
}

function SessionDetailContent({ session, tabs }: { session: SessionDetailOut; tabs: SessionTabDef[] }) {
  const router = useRouter();
  const pathname = usePathname() ?? `/console/sessions/${session.id}`;
  const searchParams = useSearchParams();

  const shown = React.useMemo(() => visibleTabs(tabs, { session }), [tabs, session]);
  const current = pickTab(shown, searchParams?.get("tab"), DEFAULT_SESSION_TAB_ID);
  const headerActions = sessionDetailSlots().headerActions;

  const selectTab = (id: string) => {
    const params = new URLSearchParams(searchParams?.toString() ?? "");
    if (id === DEFAULT_SESSION_TAB_ID) params.delete("tab");
    else params.set("tab", id);
    const qs = params.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  };

  const swept = sweptReason(session);

  return (
    <div data-slot="session-detail-view">
      <PageHeader
        eyebrow="Session"
        title={
          <Link
            href={`/console/agents/${session.agent_id}`}
            className="rounded-xs outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring"
          >
            {session.agent_name || "Unknown agent"}
          </Link>
        }
        className="mb-3"
        actions={
          headerActions.length > 0 ? (
            <div className="flex flex-wrap items-center gap-2">
              {headerActions.map((Action, index) => (
                <Action key={index} session={session} />
              ))}
            </div>
          ) : undefined
        }
      />

      <div className="mb-6">
        <SessionHeaderMeta session={session} />
      </div>

      {session.status === "failed" && session.error && !swept ? (
        <Alert variant="danger" className="mb-6">
          <Icon as={CircleAlertIcon} size="md" />
          <AlertDescription>
            <span className="font-medium">The call failed.</span> {session.error}
          </AlertDescription>
        </Alert>
      ) : null}

      <SessionStats session={session} />

      {current ? (
        <Tabs value={current.id} onValueChange={selectTab} className="mt-8 gap-4">
          <div className="-mx-4 overflow-x-auto px-4 md:mx-0 md:px-0">
            <TabsList variant="line" aria-label="Session views" className="w-max min-w-full justify-start border-b border-border">
              {shown.map((tab) => (
                <TabsTrigger key={tab.id} value={tab.id} className="flex-none px-2.5">
                  <Icon as={tab.icon} size="sm" />
                  {tab.label}
                </TabsTrigger>
              ))}
            </TabsList>
          </div>
          {shown.map((tab) => (
            <TabsContent key={tab.id} value={tab.id} className="min-w-0">
              <tab.Component session={session} />
            </TabsContent>
          ))}
        </Tabs>
      ) : null}
    </div>
  );
}

function recordingChip(session: SessionDetailOut): { tone: StatusTone; label: string } | null {
  switch (session.recording?.status) {
    case "requested":
    case "active":
      return { tone: "info", label: "Recording…" };
    case "ready":
      return { tone: "success", label: "Recording ready" };
    case "failed":
      return { tone: "danger", label: "Recording failed" };
    default:
      return null;
  }
}

function qaTone(score: number): StatusTone {
  if (score >= 8) return "success";
  if (score >= 5) return "warning";
  return "danger";
}

function MetaItem({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1 whitespace-nowrap">
      <span className="text-muted-foreground">{label}</span>
      <span className="text-foreground">{children}</span>
    </span>
  );
}

/** Header meta row: status, channel, mode, time, duration, config version, connection, cost, QA, room. */
export function SessionHeaderMeta({ session }: { session: SessionDetailOut }) {
  const connections = useConnectionNames();
  const swept = sweptReason(session);
  const duration = sessionDurationMs(session);
  const channel = channelLabel(session.channel);
  const recording = recordingChip(session);
  const cost = formatUsd(session.cost?.total_usd ?? session.cost_usd);
  const score = typeof session.qa?.score === "number" ? session.qa.score : null;
  const connectionName = session.connection_id
    ? (connections.data?.get(session.connection_id) ?? session.connection_id)
    : null;
  const startedAt = session.started_at ?? null;

  return (
    <div className="flex flex-col gap-2.5">
      <div className="flex flex-wrap items-center gap-1.5">
        <StatusChip tone={sessionStatusTone(session)} dot={session.status === "failed" && !swept}>
          {SESSION_STATUS_LABEL[session.status]}
        </StatusChip>
        {swept ? <StatusChip tone="neutral">{sentenceCase(swept)}</StatusChip> : null}
        {channel ? <StatusChip tone="neutral">{channel}</StatusChip> : null}
        <StatusChip tone="neutral">{pipelineModeLabel(session.pipeline_mode)}</StatusChip>
        {recording ? <StatusChip tone={recording.tone}>{recording.label}</StatusChip> : null}
        {score !== null ? <StatusChip tone={qaTone(score)}>{`QA ${score}/10`}</StatusChip> : null}
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[0.8125rem]">
        <MetaItem label={startedAt ? "Started" : "Created"}>
          <RelativeTime iso={startedAt ?? session.created_at} withExact />
        </MetaItem>
        <MetaItem label="Duration">{duration === null ? "—" : formatDuration(duration)}</MetaItem>
        <MetaItem label="Config">{`v${session.config_version}`}</MetaItem>
        {connectionName ? <MetaItem label="Connection">{connectionName}</MetaItem> : null}
        {cost ? <MetaItem label="Cost">{cost}</MetaItem> : null}
        <span className="inline-flex items-center gap-1">
          <span className="text-muted-foreground">Room</span>
          <span className="font-mono text-xs text-foreground">{session.room_name}</span>
          <CopyButton value={session.room_name} label="Copy room name" size="xs" />
        </span>
      </div>
    </div>
  );
}

/**
 * Stats strip: turns, tool calls and errors (counted from the same data the
 * timeline shows), latency when the worker reported it, then `usage`.
 */
export function SessionStats({ session }: { session: SessionDetailOut }) {
  const eventsQuery = useAllSessionEvents(session.id);
  const events = React.useMemo(() => eventsQuery.data?.items ?? [], [eventsQuery.data]);
  const usage = React.useMemo(() => describeUsage(session.usage), [session.usage]);

  const counted = eventsQuery.isSuccess || (session.transcript?.length ?? 0) > 0;
  const pending = eventsQuery.isLoading ? "…" : "—";
  const turns = counted ? collectTurns(session.transcript, events).length : null;
  const tools = eventsQuery.isSuccess ? pairToolCalls(events).length : null;
  const errors = eventsQuery.isSuccess ? events.filter((event) => event.type === "error").length : null;

  const latency = session.latency;
  const latencyItems: DescriptionItem[] = [
    { term: "Response time (p50)", value: latency?.eou_to_first_audio_ms_p50 },
    { term: "LLM first token (p50)", value: latency?.llm_ttft_ms_p50 },
    { term: "TTS first byte (p50)", value: latency?.tts_ttfb_ms_p50 },
  ]
    .filter((item): item is { term: string; value: number } => typeof item.value === "number")
    .map((item) => ({ term: item.term, detail: formatMs(item.value), mono: true }));

  const items: DescriptionItem[] = [
    { term: "Turns", detail: turns ?? pending, mono: true },
    { term: "Tool calls", detail: tools ?? pending, mono: true },
    { term: "Errors", detail: errors ?? pending, mono: true },
    ...latencyItems,
    ...usage.pairs.map((pair) => ({ term: pair.label, detail: pair.value, mono: true })),
  ];

  return (
    <section aria-label="Call summary" className="rounded-lg border border-border bg-card p-4 md:p-5">
      <DescriptionList items={items} columns={3} className="grid-cols-3 lg:grid-cols-6" />
      {usage.groups.length > 0 ? (
        <div className="mt-5 border-t border-border pt-4">
          <UsageGroups groups={usage.groups} />
        </div>
      ) : null}
    </section>
  );
}
