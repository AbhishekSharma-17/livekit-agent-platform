import type { Metadata } from "next";

import { Page } from "@/components/shared/page-header";
import { SessionDetailView } from "@/components/console/sessions/session-detail-view";

export const metadata: Metadata = { title: "Session" };

/**
 * `/console/sessions/[id]?tab=` (docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md §1); the
 * tab is read client-side. A wide page: the detail archetype
 * (docs/ui/DESIGN-SYSTEM.md section 7.4).
 */
export default async function SessionDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <Page width="wide">
      <SessionDetailView sessionId={id} />
    </Page>
  );
}
