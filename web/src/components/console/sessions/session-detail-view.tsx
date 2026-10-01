"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { SearchXIcon } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { CopyButton } from "@/components/shared/copy-button";
import { MetaList, type MetaItem } from "@/components/shared/data-display";
import { DescriptionList, type DescriptionItem } from "@/components/shared/description-list";
import { EmptyState } from "@/components/shared/empty-state";
import { Icon } from "@/components/shared/icon";
import { LoadingRegion } from "@/components/shared/loading-state";
import { PageHeader } from "@/components/shared/page-header";
import { RelativeTime } from "@/components/shared/relative-time";
import { Section, SectionRow } from "@/components/shared/section";
import { StatusPill, type StatusTone } from "@/components/shared/status-chip";
import { lifecycleStatus } from "@/components/shared/status-map";
import { useSessionDetail } from "@/components/console/lib/api-hooks";
import { ErrorBanner } from "@/components/console/shared/error-banner";
import { useSetBreadcrumbs } from "@/components/console/shell/breadcrumb-context";
import type { LocaleEvent, SessionDetailOut, SessionEventOut } from "@/contracts/lkap-contracts";
import { ApiError } from "@/lib/api";
import { EMPTY_VALUE, formatDuration } from "@/lib/format";

import { formatMs } from "./detail/builtin-event-kinds";
import { pickTab, visibleTabs } from "./detail/registry";
import { DEFAULT_SESSION_TAB_ID, sessionDetailSlots, sessionTabs } from "./detail/resolved";
import type { SessionTabDef } from "./detail/types";
import { RefreshButton } from "./refresh-button";
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

// Kept importable from here: `tests/console-session-detail-view.test.ts` and older callers use this path.
export { formatUsageLabel, formatUsageValue } from "./session-usage";

const BACK = { href: "/console/sessions", label: "Back to sessions" };

/**
 * Session detail — the detail archetype, wide (docs/ui/DESIGN-SYSTEM.md
 * section 7.4): a back link, the agent as the title with the status beside
 * it, Refresh plus any extension actions, a page-level alert for a real
 * failure, then a main column (call summary and the tabs from the registry
 * in `./detail` — V2-14 adds Recording, Cost and QA there without touching
 * this file, see `./README.md`) beside a side column of facts. The side
 * column stacks under the main one on narrow screens.
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
        <>
          <PageHeader back={BACK} title="Session not found" />
          <EmptyState
            icon={SearchXIcon}
            title="This session doesn't exist"
            description="It may belong to another workspace, or the link is wrong."
            action={
              <Button asChild variant="secondary">
                <Link href="/console/sessions">All sessions</Link>
              </Button>
            }
          />
        </>
      );
    }
    return (
      <>
        <PageHeader back={BACK} title="Session" />
        <ErrorBanner error={error} context={{ action: "load this session" }} onRetry={() => void refetch()} />
      </>
    );
  }

  return <SessionDetailContent session={session} tabs={tabsOverride ?? sessionTabs()} />;
}

function DetailSkeleton() {
  return (
    <LoadingRegion label="Loading session" className="flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <Skeleton className="h-7 w-36" />
        <div className="flex flex-wrap items-center gap-2.5">
          <Skeleton className="h-7 w-64" />
          <Skeleton className="h-[22px] w-16 rounded-pill" />
        </div>
      </div>
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="flex min-w-0 flex-col gap-8">
          <div className="grid grid-cols-3 gap-4 rounded-lg border border-border bg-card p-5 lg:grid-cols-6">
            {[0, 1, 2, 3, 4, 5].map((index) => (
              <div key={index} className="flex flex-col gap-1.5">
                <Skeleton className="h-3 w-16" />
                <Skeleton className="h-4 w-10" />
              </div>
            ))}
          </div>
          <div className="flex gap-4 border-b border-border pb-3">
            {[0, 1, 2, 3].map((index) => (
              <Skeleton key={index} className="h-4 w-20" />
            ))}
          </div>
          <Skeleton className="h-64 w-full rounded-lg" />
        </div>
        <div className="flex flex-col gap-3 rounded-lg border border-border bg-card p-5">
          {[0, 1, 2, 3, 4, 5].map((index) => (
            <div key={index} className="flex gap-4">
              <Skeleton className="h-3.5 w-20" />
              <Skeleton className="h-3.5 flex-1" />
            </div>
          ))}
        </div>
      </div>
    </LoadingRegion>
  );
}

function SessionDetailContent({ session, tabs }: { session: SessionDetailOut; tabs: SessionTabDef[] }) {
  const router = useRouter();
  const pathname = usePathname() ?? `/console/sessions/${session.id}`;
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();
  const [refreshing, setRefreshing] = React.useState(false);

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

  // The session and every query under it (events, …) share the `["sessions", id]` prefix.
  const refresh = async () => {
    setRefreshing(true);
    try {
      await queryClient.invalidateQueries({ queryKey: ["sessions", session.id] });
    } finally {
      setRefreshing(false);
    }
  };

  const swept = sweptReason(session);

  return (
    <div data-slot="session-detail-view">
      <PageHeader
        back={BACK}
        eyebrow="Session"
        title={
          <Link href={`/console/agents/${session.agent_id}`} className="rounded-sm hover:underline">
            {session.agent_name || "Unknown agent"}
          </Link>
        }
        badge={<SessionBadges session={session} />}
        actions={
          <>
            {headerActions.map((Action, index) => (
              <Action key={index} session={session} />
            ))}
            <RefreshButton onRefresh={() => void refresh()} refreshing={refreshing} />
          </>
        }
      />

      {session.status === "failed" && session.error && !swept ? (
        <Alert tone="danger" title="The call failed." className="mb-6">
          {session.error}
        </Alert>
      ) : null}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="flex min-w-0 flex-col gap-8">
          <SessionStats session={session} />

          {current ? (
            <Tabs value={current.id} onValueChange={selectTab} className="gap-4">
              <div className="-mx-4 overflow-x-auto px-4 md:mx-0 md:px-0">
                <TabsList variant="line" aria-label="Session views" className="w-max min-w-full justify-start">
                  {shown.map((tab) => (
                    <TabsTrigger key={tab.id} value={tab.id} className="flex-none">
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

        <aside aria-label="Session details" className="min-w-0">
          <Section id="session-facts" title="Details">
            <SectionRow>
              <SessionHeaderMeta session={session} />
            </SectionRow>
          </Section>
        </aside>
      </div>
    </div>
  );
}

function recordingStatus(session: SessionDetailOut): { tone: StatusTone; label: string } | null {
  switch (session.recording?.status) {
    case "requested":
    case "active":
      return { tone: lifecycleStatus("in_progress").tone, label: "Recording…" };
    case "ready":
      return { tone: lifecycleStatus("ready").tone, label: "Recording ready" };
    case "failed":
      return { tone: lifecycleStatus("failed").tone, label: "Recording failed" };
    default:
      return null;
  }
}

function qaTone(score: number): StatusTone {
  if (score >= 8) return "success";
  if (score >= 5) return "warning";
  return "danger";
}

/** The title's status pills: the session's state, a swept reason, the recording and the QA score. */
function SessionBadges({ session }: { session: SessionDetailOut }) {
  const swept = sweptReason(session);
  const recording = recordingStatus(session);
  const score = typeof session.qa?.score === "number" ? session.qa.score : null;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <StatusPill tone={sessionStatusTone(session)}>{SESSION_STATUS_LABEL[session.status]}</StatusPill>
      {swept ? <StatusPill tone="neutral">{sentenceCase(swept)}</StatusPill> : null}
      {recording ? <StatusPill tone={recording.tone}>{recording.label}</StatusPill> : null}
      {score !== null ? <StatusPill tone={qaTone(score)}>{`QA ${score}/10`}</StatusPill> : null}
    </div>
  );
}

/** Plain wording for `LocaleEvent.source` (R-V5-10) — never "participant metadata" or "IANA". */
const CALLER_TIMEZONE_SOURCE_LABEL: Record<LocaleEvent["source"], string> = {
  browser: "detected from the browser",
  number: "from the phone number",
  business: "business timezone",
  workspace: "workspace default",
  default: "default",
};

/**
 * "Caller time zone: … (…)" (R-V5-10, V5-52). Prefers the worker's `locale`
 * session event (has the source); falls back to `SessionOut.caller_timezone`
 * once the summary lands, with no source wording since none is known then.
 */
export function callerTimezoneLabel(
  session: Pick<SessionDetailOut, "caller_timezone">,
  events: readonly SessionEventOut[],
): string | null {
  const localeEvent = events.find((event) => event.type === "locale");
  if (localeEvent) {
    const payload = localeEvent.payload as unknown as Partial<LocaleEvent>;
    if (typeof payload.caller_timezone === "string") {
      const source = payload.source ? (CALLER_TIMEZONE_SOURCE_LABEL[payload.source] ?? payload.source) : null;
      return source ? `${payload.caller_timezone} (${source})` : payload.caller_timezone;
    }
  }
  return session.caller_timezone ?? null;
}

/**
 * The facts card: when, how long, channel, mode, config version,
 * connection, cost, the caller's time zone and the room.
 */
export function SessionHeaderMeta({ session }: { session: SessionDetailOut }) {
  const connections = useConnectionNames();
  // Same query key as `SessionStats`' own call — React Query dedupes the request.
  const eventsQuery = useAllSessionEvents(session.id);
  const duration = sessionDurationMs(session);
  const channel = channelLabel(session.channel);
  const cost = formatUsd(session.cost?.total_usd ?? session.cost_usd);
  const connectionName = session.connection_id
    ? (connections.data?.get(session.connection_id) ?? session.connection_id)
    : null;
  const startedAt = session.started_at ?? null;
  const callerTimezone = callerTimezoneLabel(session, eventsQuery.data?.items ?? []);

  const items: MetaItem[] = [
    { term: startedAt ? "Started" : "Created", value: <RelativeTime iso={startedAt ?? session.created_at} withExact /> },
    { term: "Duration", value: duration === null ? EMPTY_VALUE : formatDuration(duration) },
    ...(channel ? [{ term: "Channel", value: channel }] : []),
    { term: "Mode", value: pipelineModeLabel(session.pipeline_mode) },
    { term: "Config", value: `v${session.config_version}` },
    ...(connectionName ? [{ term: "Connection", value: connectionName }] : []),
    ...(cost ? [{ term: "Cost", value: cost }] : []),
    ...(callerTimezone ? [{ term: "Caller time zone", value: callerTimezone }] : []),
    {
      term: "Room",
      value: (
        <span className="inline-flex max-w-full items-center gap-1">
          <span className="min-w-0 truncate font-mono text-caption">{session.room_name}</span>
          <CopyButton value={session.room_name} label="Copy room name" size="xs" />
        </span>
      ),
    },
  ];

  return <MetaList items={items} />;
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
  const pending = eventsQuery.isLoading ? "…" : EMPTY_VALUE;
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
    // The worker's `usage.turns` counts the replies that reported latency (the
    // p50s' sample), not the conversation's turns — label it so the strip
    // doesn't show two different "Turns".
    ...usage.pairs.map((pair) => ({
      term: pair.key === "turns" ? "Timed replies" : pair.label,
      detail: pair.value,
      mono: true,
    })),
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
