import type { Metadata } from "next";

import { Page, PageHeader } from "@/components/shared/page-header";
import { RequireWrite } from "@/components/shared/require-write";
import { ConnectionCreateForm } from "@/components/console/connections/connection-create-form";
import { ConsoleBreadcrumbs } from "@/components/console/shell/breadcrumb-context";

export const metadata: Metadata = { title: "New connection" };

/** The Form archetype (docs/ui/DESIGN-SYSTEM.md section 7.4): a narrow page; "Create connection" is the primary. */
export default function NewConnectionPage() {
  return (
    <Page width="narrow">
      <ConsoleBreadcrumbs trail={[{ label: "Connections", href: "/console/connections" }, { label: "New connection" }]} />
      <PageHeader
        back={{ href: "/console/connections", label: "Back to connections" }}
        title="New connection"
        description="Test the details before you save. Nothing is stored until the test passes."
      />
      <RequireWrite min="admin" title="You can't add connections" description="Ask an admin to add connections.">
        <ConnectionCreateForm />
      </RequireWrite>
    </Page>
  );
}
