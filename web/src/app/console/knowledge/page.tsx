import { Suspense } from "react";
import type { Metadata } from "next";

import { KnowledgePageSkeleton, KnowledgePageTabs } from "@/components/console/knowledge/knowledge-page-tabs";

export const metadata: Metadata = { title: "Knowledge" };

/**
 * `/console/knowledge?tab=bases|connections` (UI-R1). The tab strip and both
 * lists live in `KnowledgePageTabs` (`useSearchParams`, so it needs the
 * `Suspense` boundary the other query-param-driven pages use, e.g.
 * `console/tools/page.tsx`). The knowledge base list is the default; the
 * Connections tab is where knowledge connections moved from Settings, and
 * `/console/settings?tab=knowledge-connections` redirects to it.
 */
export default function KnowledgePage() {
  return (
    <Suspense fallback={<KnowledgePageSkeleton />}>
      <KnowledgePageTabs />
    </Suspense>
  );
}
