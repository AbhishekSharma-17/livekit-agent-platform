"use client";

import { LockIcon } from "lucide-react";

import { EmptyState, PageHeader } from "@/components/shared";
import { CreateAgentFlow } from "@/components/console/agents/create/create-agent-flow";
import { ConsoleBreadcrumbs } from "@/components/console/shell/breadcrumb-context";
import { useWriteAccess } from "@/components/console/lib/roles";

/**
 * `/console/agents/new` (docs/UI_UX_SPEC.md §4.2): the guided pack + name
 * flow as a page, replacing the old `CreateAgentDialog` modal. The `Agents`
 * breadcrumb points at `/console/agents`, which WP-1 creates as part of the
 * console shell's route move — until it lands the link 404s.
 *
 * A viewer who navigates here directly (docs/v2/_asks.md V2-20-5) sees an
 * explanation instead of a form `POST /v1/agents` would 403 anyway.
 */
export default function NewAgentPage() {
  const { canWrite, isLoading } = useWriteAccess();
  return (
    <div>
      <ConsoleBreadcrumbs trail={[{ label: "Agents", href: "/console/agents" }, { label: "New agent" }]} />
      <PageHeader
        title="New agent"
        description="Start from a pack, then name your agent."
      />
      {isLoading ? null : canWrite ? (
        <CreateAgentFlow />
      ) : (
        <EmptyState
          icon={LockIcon}
          title="You can't create agents"
          description="Viewers can't do this — ask a builder, admin or owner."
        />
      )}
    </div>
  );
}
