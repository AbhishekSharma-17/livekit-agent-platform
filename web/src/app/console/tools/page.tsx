import { Suspense } from "react";
import type { Metadata } from "next";

import { ToolsPageTabs } from "@/components/console/tools/apps/tools-page-tabs";
import { McpOauthReturnToast } from "@/components/console/tools/mcp-oauth-status";

export const metadata: Metadata = { title: "Tools" };

/**
 * `/console/tools?tab=tools|apps` (docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md §1,
 * §3 WP-5 "Change"; docs/v5/COMPOSIO.md §6, V5-22 adds the Apps tab). The tab
 * strip and every list live in `ToolsPageTabs` (`useSearchParams`, so it
 * needs the `Suspense` boundary the other query-param-driven pages use, e.g.
 * `console/providers/page.tsx`).
 *
 * `?oauth=ok|error` is the MCP sign-in callback's redirect
 * (`GET /v1/oauth/mcp/callback`, V5-21): a toast and a `tools` cache
 * invalidation, mirroring `ToolsPageTabs`' own `?connect=ok|error` handling
 * for the Composio callback. Kept as its own component (`mcp-oauth-status.tsx`)
 * rather than folded into `ToolsPageTabs` (V5-22's file) so this page stays
 * the only file this package edits for it.
 */
export default function ToolsPage() {
  return (
    <Suspense>
      <McpOauthReturnToast />
      <ToolsPageTabs />
    </Suspense>
  );
}
