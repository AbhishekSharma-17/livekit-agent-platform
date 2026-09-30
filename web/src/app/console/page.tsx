import type { Metadata } from "next";

import { Page } from "@/components/shared/page-header";
import { Overview } from "@/components/console/overview/overview";

export const metadata: Metadata = { title: "Overview" };

/**
 * `/console` — the console's first screen: the Overview archetype
 * (docs/ui/DESIGN-SYSTEM.md section 7.4, decision D6 in docs/ui/AUDIT.md).
 */
export default function ConsoleOverviewPage() {
  return (
    <Page>
      <Overview />
    </Page>
  );
}
