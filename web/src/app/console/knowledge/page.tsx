"use client";

import { PageHeader } from "@/components/console/shared/page-header";
import { CreateKbDialog } from "@/components/console/knowledge/create-kb-dialog";
import { KbList } from "@/components/console/knowledge/kb-list";

export default function KnowledgePage() {
  return (
    <div>
      <PageHeader
        title="Knowledge"
        description="Upload documents, chunked and embedded for retrieval-augmented answers."
        actions={<CreateKbDialog />}
      />
      <KbList />
    </div>
  );
}
