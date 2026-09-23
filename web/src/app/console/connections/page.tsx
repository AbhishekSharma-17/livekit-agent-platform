import type { Metadata } from "next";

import { PageHeader } from "@/components/shared/page-header";
import { NewResourceButton } from "@/components/shared/new-resource-button";
import { ConnectionsTable } from "@/components/console/connections/connections-table";

export const metadata: Metadata = { title: "Connections" };

/**
 * `/console/connections` (UI_UX_SPEC-V2-AMENDMENTS §1, §2.1) — the LiveKit
 * deployments a workspace's agents run on.
 */
export default function ConsoleConnectionsPage() {
  return (
    <div>
      <PageHeader
        title="Connections"
        description="A connection is a LiveKit Cloud project or self-hosted server. Agents bind to one to run."
        actions={
          <NewResourceButton href="/console/connections/new" min="admin">
            New connection
          </NewResourceButton>
        }
      />
      <ConnectionsTable />
    </div>
  );
}
