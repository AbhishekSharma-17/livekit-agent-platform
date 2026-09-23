"use client";

import { PageHeader } from "@/components/shared";
import { CreateAgentFlow } from "@/components/console/agents/create/create-agent-flow";
import { ConsoleBreadcrumbs } from "@/components/console/shell/breadcrumb-context";

/**
 * `/console/agents/new` (docs/UI_UX_SPEC.md §4.2): the guided pack + name
 * flow as a page, replacing the old `CreateAgentDialog` modal. The `Agents`
 * breadcrumb points at `/console/agents`, which WP-1 creates as part of the
 * console shell's route move — until it lands the link 404s.
 */
export default function NewAgentPage() {
  return (
    <div>
      <ConsoleBreadcrumbs trail={[{ label: "Agents", href: "/console/agents" }, { label: "New agent" }]} />
      <PageHeader
        title="New agent"
        description="Start from a pack, then name your agent."
      />
      <CreateAgentFlow />
    </div>
  );
}
