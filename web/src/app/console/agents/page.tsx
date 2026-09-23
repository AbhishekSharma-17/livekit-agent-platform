import Link from "next/link";
import type { Metadata } from "next";

import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/shared/page-header";
import { AgentsTable } from "@/components/console/agents/agents-table";

export const metadata: Metadata = { title: "Agents" };

/**
 * `/console/agents` — moved here from `/console` (docs/UI_UX_SPEC.md §7.2
 * item 3; docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md §1's Build group). Folds in
 * WP-2's stopgap (a bare `Link` to `/console/agents/new` in place of the
 * deleted create dialog) now that the real shell and route exist.
 */
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
