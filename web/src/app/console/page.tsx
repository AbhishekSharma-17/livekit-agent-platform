import type { Metadata } from "next";

import { Overview } from "@/components/console/overview/overview";

export const metadata: Metadata = { title: "Overview" };

/**
 * `/console` — the console's first screen (docs/UI_UX_SPEC.md §3.4, §4.1).
 * Route move per §7.2 item 3: this used to render the agents list (a WP-2
 * stopgap, now `app/console/agents/page.tsx`); it renders the real Overview.
 */
export default function ConsoleOverviewPage() {
  return <Overview />;
}
