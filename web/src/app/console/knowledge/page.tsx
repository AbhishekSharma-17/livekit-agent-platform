"use client";

import { Page, PageHeader } from "@/components/shared/page-header";
import { CreateKbDialog } from "@/components/console/knowledge/create-kb-dialog";
import { KbList } from "@/components/console/knowledge/kb-list";

export default function KnowledgePage() {
  return (
    <Page>
      <PageHeader
        title="Knowledge"
        description="Documents your agents search for answers during a call."
        actions={<CreateKbDialog />}
      />
      <KbList />
    </Page>
  );
}
