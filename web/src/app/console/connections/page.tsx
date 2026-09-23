import Link from "next/link";
import type { Metadata } from "next";

import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/shared/page-header";
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
          <Button asChild>
            <Link href="/console/connections/new">New connection</Link>
          </Button>
        }
      />
      <ConnectionsTable />
    </div>
  );
}
