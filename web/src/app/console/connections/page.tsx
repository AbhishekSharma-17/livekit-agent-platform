import type { Metadata } from "next";

import { Page, PageHeader } from "@/components/shared/page-header";
import { NewResourceButton } from "@/components/shared/new-resource-button";
import { ConnectionsTable } from "@/components/console/connections/connections-table";

export const metadata: Metadata = { title: "Connections" };

/**
 * `/console/connections` (UI_UX_SPEC-V2-AMENDMENTS §1, §2.1) — the LiveKit
 * deployments a workspace's agents run on. The List archetype
 * (docs/ui/DESIGN-SYSTEM.md section 7.4): "New connection" is the one
 * primary; people below admin read a note instead (D12).
 */
export default function ConsoleConnectionsPage() {
  return (
    <Page>
      <PageHeader
        title="Connections"
        description="The LiveKit Cloud projects and self-hosted servers your agents run on."
        actions={
          <NewResourceButton href="/console/connections/new" min="admin" readOnlyNote="Ask an admin to add connections.">
            New connection
          </NewResourceButton>
        }
      />
      <ConnectionsTable />
    </Page>
  );
}
