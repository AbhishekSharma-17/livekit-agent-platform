"use client";

import Link from "next/link";

import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/console/shared/page-header";
import { AgentsTable } from "@/components/console/agents/agents-table";

// WP-2 stopgap: `create-agent-dialog.tsx` is deleted per
// docs/UI_UX_SPEC.md §7.3 (replaced by the `/console/agents/new` guided
// flow). This file is WP-1's (the route move to `/console/agents` + the
// Overview page aren't built yet), so this is only the minimal swap needed
// to keep the app compiling — WP-1 owns folding this into the real shell.
export default function ConsoleAgentsPage() {
  return (
    <div>
      <PageHeader
        title="Agents"
        description="Configure providers, instructions, tools and knowledge, then open a test call."
        actions={
          <Button asChild>
            <Link href="/console/agents/new">New agent</Link>
          </Button>
        }
      />
      <AgentsTable />
    </div>
  );
}
