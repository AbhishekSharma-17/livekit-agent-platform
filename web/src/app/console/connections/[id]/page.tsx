import { Suspense } from "react";
import type { Metadata } from "next";

import { ConnectionDetail } from "@/components/console/connections/connection-detail";

export const metadata: Metadata = { title: "Connection" };

/** `/console/connections/[id]?tab=overview|fleet|storage|deploy` (docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md §1). */
export default async function ConnectionDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <Suspense>
      <ConnectionDetail connectionId={id} />
    </Suspense>
  );
}
