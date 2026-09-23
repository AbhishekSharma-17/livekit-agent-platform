"use client";

import { ToolsList } from "@/components/console/tools/tools-list";

/**
 * `/console/tools` (docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md §1: "Build" group,
 * "Shared tools list ... needed because tools are referenced by flows and
 * multiple agents", §3 WP-5 "Change"). WP-1's sidebar already links here
 * (`nav-config.ts`); this page is what stops it 404ing.
 */
export default function ToolsPage() {
  return (
    // `ToolsList` renders the header: its add actions share the list's queries.
    <ToolsList />
  );
}
