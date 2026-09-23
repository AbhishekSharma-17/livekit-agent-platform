import type { Metadata } from "next";

import { PageHeader } from "@/components/shared/page-header";
import { RequireWrite } from "@/components/shared/require-write";
import { ConnectionCreateForm } from "@/components/console/connections/connection-create-form";
import { ConsoleBreadcrumbs } from "@/components/console/shell/breadcrumb-context";

export const metadata: Metadata = { title: "New connection" };

export default function NewConnectionPage() {
  return (
    <div>
      <ConsoleBreadcrumbs trail={[{ label: "Connections", href: "/console/connections" }, { label: "New connection" }]} />
      <PageHeader
        title="New connection"
        description="Test the details before saving — nothing is stored until the test passes."
      />
      <RequireWrite min="admin" title="You can't create connections">
        <ConnectionCreateForm />
      </RequireWrite>
    </div>
  );
}
