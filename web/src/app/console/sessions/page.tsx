"use client";

import { PageHeader } from "@/components/console/shared/page-header";
import { SessionsTable } from "@/components/console/sessions/sessions-table";

export default function SessionsPage() {
  return (
    <div>
      <PageHeader title="Sessions" description="Every session across agents, with transcript, events and usage." />
      <SessionsTable />
    </div>
  );
}
