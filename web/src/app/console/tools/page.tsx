import { Suspense } from "react";
import type { Metadata } from "next";

import { ToolsPageTabs } from "@/components/console/tools/apps/tools-page-tabs";

export const metadata: Metadata = { title: "Tools" };

/**
 * `/console/tools?tab=tools|apps` (docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md §1,
 * §3 WP-5 "Change"; docs/v5/COMPOSIO.md §6, V5-22 adds the Apps tab). The tab
 * strip and every list live in `ToolsPageTabs` (`useSearchParams`, so it
 * needs the `Suspense` boundary the other query-param-driven pages use, e.g.
 * `console/providers/page.tsx`).
 */
export default function ToolsPage() {
  return (
    <Suspense>
      <ToolsPageTabs />
    </Suspense>
  );
}
