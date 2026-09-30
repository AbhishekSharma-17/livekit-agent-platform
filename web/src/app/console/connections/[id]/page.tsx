import { Suspense } from "react";
import type { Metadata } from "next";

import { Page } from "@/components/shared/page-header";
import { ConnectionDetail, ConnectionDetailSkeleton } from "@/components/console/connections/connection-detail";

export const metadata: Metadata = { title: "Connection" };

/**
 * `/console/connections/[id]?tab=overview|fleet|storage|deploy` (docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md §1): the
 * Detail / record archetype on a wide page (docs/ui/DESIGN-SYSTEM.md section 7.4).
 */
export default async function ConnectionDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <Page width="wide">
      <Suspense fallback={<ConnectionDetailSkeleton />}>
        <ConnectionDetail connectionId={id} />
      </Suspense>
    </Page>
  );
}
