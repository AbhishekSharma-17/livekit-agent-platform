import { Suspense } from "react";
import type { Metadata } from "next";

import { PageHeader } from "@/components/shared/page-header";
import { AnalyticsView } from "@/components/console/analytics/analytics-view";

export const metadata: Metadata = { title: "Analytics" };

/** `/console/analytics?range=` (docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md §1, §2.6). */
export default function AnalyticsPage() {
  return (
    <div>
      <PageHeader title="Analytics" description="Usage and cost across every agent in this workspace." />
      <Suspense>
        <AnalyticsView />
      </Suspense>
    </div>
  );
}
