"use client";

import { PageHeader } from "@/components/console/shared/page-header";
import { AgentsTable } from "@/components/console/agents/agents-table";
import { CreateAgentDialog } from "@/components/console/agents/create-agent-dialog";

export default function ConsoleAgentsPage() {
  return (
    <div>
      <PageHeader
        title="Agents"
        description="Configure providers, instructions, tools and knowledge, then open a test call."
        actions={<CreateAgentDialog />}
      />
      <AgentsTable />
    </div>
  );
}
