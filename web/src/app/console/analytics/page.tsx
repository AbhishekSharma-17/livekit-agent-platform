import { Suspense } from "react";
import type { Metadata } from "next";

import { Page } from "@/components/shared/page-header";
import { AnalyticsFallback, AnalyticsView } from "@/components/console/analytics/analytics-view";

export const metadata: Metadata = { title: "Analytics" };

/**
 * `/console/analytics?range=&tab=` — the analytics archetype on a wide page
 * (docs/ui/DESIGN-SYSTEM.md section 7.4). The header lives in `AnalyticsView`
 * (the date range and Refresh read the query string); while that suspends,
 * the fallback mirrors the header and the stat grid instead of a blank area.
 */
export default function AnalyticsPage() {
  return (
    <Page width="wide">
      <Suspense fallback={<AnalyticsFallback />}>
        <AnalyticsView />
      </Suspense>
    </Page>
  );
}
