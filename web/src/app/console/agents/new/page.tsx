import { Suspense } from "react";
import type { Metadata } from "next";

import { PageHeader } from "@/components/shared/page-header";
import { AgentsTable } from "@/components/console/agents/agents-table";
import { CreateAgentDeepLink } from "@/components/console/agents/create/create-agent-deep-link";
import { NewAgentButton } from "@/components/console/agents/create/new-agent-button";

export const metadata: Metadata = { title: "New agent" };

/**
 * `/console/agents/new` (R-V4-2, docs/v4/TEMPLATES.md §6.1): a deep link, not
 * a page of its own. It renders the agents list with the New agent dialog
 * open over it, so bookmarks and links keep working; `?template=<id>`
 * preselects a starter, and closing the dialog returns to `/console/agents`.
 */
export default function NewAgentPage() {
  return (
    <div>
      <PageHeader
        title="Agents"
        description="Configure providers, instructions, tools and knowledge, then open a test call."
        actions={<NewAgentButton />}
      />
      <AgentsTable />
      <Suspense fallback={null}>
        <CreateAgentDeepLink />
      </Suspense>
    </div>
  );
}
