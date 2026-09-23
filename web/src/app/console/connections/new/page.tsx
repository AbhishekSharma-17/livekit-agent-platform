import type { Metadata } from "next";

import { PageHeader } from "@/components/shared/page-header";
import { ConnectionCreateForm } from "@/components/console/connections/connection-create-form";

export const metadata: Metadata = { title: "New connection" };

export default function NewConnectionPage() {
  return (
    <div>
      <PageHeader
        title="New connection"
        breadcrumbs={[{ label: "Connections", href: "/console/connections" }, { label: "New" }]}
        description="Test the details before saving — nothing is stored until the test passes."
      />
      <ConnectionCreateForm />
    </div>
  );
}
