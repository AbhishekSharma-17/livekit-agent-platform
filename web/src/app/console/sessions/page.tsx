import type { Metadata } from "next";

import { PageHeader } from "@/components/shared/page-header";
import { SessionsTable } from "@/components/console/sessions/sessions-table";

export const metadata: Metadata = { title: "Sessions" };

/** `/console/sessions` (docs/UI_UX_SPEC.md §4.10, §7.8). */
export default function SessionsPage() {
  return (
    <div>
      <PageHeader title="Sessions" description="Every call across your agents, with its timeline, transcript and final panel." />
      <SessionsTable />
    </div>
  );
}
