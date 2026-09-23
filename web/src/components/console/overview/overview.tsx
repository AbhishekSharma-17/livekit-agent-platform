"use client";

import Link from "next/link";

import { PageHeader } from "@/components/shared/page-header";
import { pluralize, toMillis } from "@/lib/format";
import { useAgents, useSessions } from "@/components/console/lib/api-hooks";
import { LiveNow } from "./live-now";
import { QuickActions } from "./quick-actions";
import { RecentSessions } from "./recent-sessions";
import { SetupChecklist } from "./setup-checklist";

const SEVEN_DAYS_MS = 7 * 24 * 60 * 60 * 1000;

function StatusLine() {
  const { data: agents } = useAgents();
  const { data: sessions } = useSessions();

  const agentItems = agents?.items ?? [];
  const liveCount = agentItems.filter((a) => a.published).length;

  const now = Date.now();
  const recentSessions = (sessions?.items ?? []).filter((s) => now - toMillis(s.created_at) <= SEVEN_DAYS_MS);
  const failedCount = recentSessions.filter((s) => s.status === "failed").length;

  return (
    <>
      <Link href="/console/agents" className="font-medium text-foreground hover:underline">
        {pluralize(agentItems.length, "agent", "agents")}
      </Link>
      {" · "}
      <Link href="/console/agents?status=live" className="font-medium text-foreground hover:underline">
        {liveCount} live
      </Link>
      {" · "}
      <Link href="/console/sessions" className="font-medium text-foreground hover:underline">
        {pluralize(recentSessions.length, "session", "sessions")} in the last 7 days
      </Link>
      {failedCount > 0 ? (
        <>
          {" · "}
          <Link href="/console/sessions?status=failed" className="font-medium text-danger-text hover:underline">
            {failedCount} failed
          </Link>
        </>
      ) : null}
    </>
  );
}

/**
 * `/console` — first screen, no hero metrics (docs/UI_UX_SPEC.md §4.1).
 * Left column (2/3): Setup + Recent sessions. Right column (1/3): Live now +
 * Quick actions.
 */
export function Overview() {
  return (
    <div>
      <PageHeader title="Overview" description={<StatusLine />} />
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          <SetupChecklist />
          <RecentSessions />
        </div>
        <div className="space-y-6">
          <LiveNow />
          <QuickActions />
        </div>
      </div>
    </div>
  );
}
