import type { Metadata } from "next";

import { PageHeader } from "@/components/shared/page-header";
import { AgentsTable } from "@/components/console/agents/agents-table";
import { NewAgentButton } from "@/components/console/agents/create/new-agent-button";

export const metadata: Metadata = { title: "Agents" };

/**
 * `/console/agents` — moved here from `/console` (docs/UI_UX_SPEC.md §7.2
 * item 3; docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md §1's Build group). "New agent"
 * opens the New agent dialog in place (R-V4-2, docs/v4/TEMPLATES.md §6).
 */
export default function ConsoleAgentsPage() {
  return (
    <div>
      <PageHeader
        title="Agents"
        description="Configure providers, instructions, tools and knowledge, then open a test call."
        actions={<NewAgentButton />}
      />
      <AgentsTable />
    </div>
  );
}
