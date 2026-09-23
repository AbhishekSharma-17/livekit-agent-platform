"use client";

import { PageHeader } from "@/components/shared/page-header";
import { ToolsList } from "@/components/console/tools/tools-list";

/**
 * `/console/tools` (docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md §1: "Build" group,
 * "Shared tools list ... needed because tools are referenced by flows and
 * multiple agents", §3 WP-5 "Change"). WP-1's sidebar already links here
 * (`nav-config.ts`); this page is what stops it 404ing.
 */
export default function ToolsPage() {
  return (
    <div>
      <PageHeader title="Tools" description="HTTP tools and MCP servers, shared across agents." />
      <ToolsList />
    </div>
  );
}
