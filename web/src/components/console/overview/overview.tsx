"use client";

import { PageHeader } from "@/components/shared/page-header";
import { NewAgentButton } from "@/components/console/agents/create/new-agent-button";
import { useMe } from "@/components/console/shell/use-me";
import { LiveNow } from "./live-now";
import { OverviewStats } from "./overview-stats";
import { QuickActions } from "./quick-actions";
import { RecentSessions } from "./recent-sessions";
import { SetupChecklist } from "./setup-checklist";

/** "Welcome back, Ada": the first word of the signed-in person's name, when the api knows it. */
function greeting(name: string | undefined): string {
  const first = name?.trim().split(/\s+/)[0];
  return first ? `Welcome back, ${first}` : "Welcome back";
}

/**
 * `/console` — the Overview archetype (docs/ui/DESIGN-SYSTEM.md section 7.4,
 * decision D6 in docs/ui/AUDIT.md): a greeting with a one-sentence purpose
 * and the one primary action ("New agent"), a stat grid of counts with
 * hints, then two columns — setup and recent sessions on the left, live
 * agents and shortcuts in the aside.
 */
export function Overview() {
  const { me } = useMe();
  return (
    <div data-slot="overview">
      <PageHeader
        title={greeting(me?.user.name)}
        description="Build, publish and keep an eye on your voice and video agents from one place."
        actions={<NewAgentButton readOnlyNote="Ask a builder or admin to create agents." />}
      />
      <div className="flex flex-col gap-8">
        <OverviewStats />
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
          <div className="flex min-w-0 flex-col gap-8">
            <SetupChecklist />
            <RecentSessions />
          </div>
          <aside aria-label="Live agents and shortcuts" className="flex min-w-0 flex-col gap-4">
            <LiveNow />
            <QuickActions />
          </aside>
        </div>
      </div>
    </div>
  );
}
