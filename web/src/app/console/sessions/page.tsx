import type { Metadata } from "next";

import { Page } from "@/components/shared/page-header";
import { SessionsTable } from "@/components/console/sessions/sessions-table";

export const metadata: Metadata = { title: "Sessions" };

/**
 * `/console/sessions` — the list archetype (docs/ui/DESIGN-SYSTEM.md section
 * 7.4). The header lives in `SessionsTable`: its Refresh and Export CSV
 * actions read the list's own filters.
 */
export default function SessionsPage() {
  return (
    <Page>
      <SessionsTable />
    </Page>
  );
}
