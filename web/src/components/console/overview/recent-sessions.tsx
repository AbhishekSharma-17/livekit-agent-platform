"use client";

import Link from "next/link";
import { HistoryIcon } from "lucide-react";

import { EmptyState } from "@/components/shared/empty-state";
import { RelativeTime } from "@/components/shared/relative-time";
import { Section, SectionRow } from "@/components/shared/section";
import { StatusChip, type StatusTone } from "@/components/shared/status-chip";
import { formatDuration, toMillis } from "@/lib/format";
import { useSessions } from "@/components/console/lib/api-hooks";
import type { SessionOut } from "@/contracts/lkap-contracts";

const STATUS_TONE: Record<SessionOut["status"], StatusTone> = {
  created: "neutral",
  active: "live",
  ended: "neutral",
  failed: "danger",
};

const STATUS_LABEL: Record<SessionOut["status"], string> = {
  created: "Created",
  active: "Live",
  ended: "Ended",
  failed: "Failed",
};

function sessionDuration(session: SessionOut): string {
  if (!session.started_at) return "—";
  const end = session.ended_at ?? new Date().toISOString();
  return formatDuration(toMillis(end) - toMillis(session.started_at));
}

/** docs/UI_UX_SPEC.md §4.1: "Recent sessions": 8 rows, "View all". */
export function RecentSessions() {
  const { data, isLoading } = useSessions(undefined, undefined, 8);
  const items = data?.items ?? [];

  return (
    <Section
      id="recent-sessions"
      title="Recent sessions"
      aside={
        <Link href="/console/sessions" className="text-sm font-medium text-brand-text hover:underline">
          View all
        </Link>
      }
    >
      {isLoading ? (
        <SectionRow className="text-sm text-muted-foreground">Loading…</SectionRow>
      ) : items.length === 0 ? (
        <SectionRow>
          <EmptyState
            compact
            icon={HistoryIcon}
            title="No calls yet"
            description="Every test call and public call is recorded here with its transcript."
            action={
              <Link href="/console/agents" className="text-sm font-medium text-brand-text hover:underline">
                Open an agent
              </Link>
            }
          />
        </SectionRow>
      ) : (
        items.map((session) => (
          <SectionRow key={session.id} className="flex items-center justify-between gap-3">
            <div className="min-w-0 flex-1">
              <Link href={`/console/sessions/${session.id}`} className="text-sm font-medium hover:underline">
                {session.agent_name}
              </Link>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {sessionDuration(session)} · <RelativeTime iso={session.created_at} />
              </p>
            </div>
            <StatusChip tone={STATUS_TONE[session.status]}>{STATUS_LABEL[session.status]}</StatusChip>
          </SectionRow>
        ))
      )}
    </Section>
  );
}
