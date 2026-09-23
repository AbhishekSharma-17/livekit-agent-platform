import { WorkflowIcon } from "lucide-react";

import type { EditorExtension } from "@/components/console/agents/editor/types";

import { FlowAwareInstructions } from "./flow-instructions";
import { FlowSection } from "./flow-section";
import { ModeSwitchChip } from "./mode-switch";
import { VersionHistory } from "./version-history";

/**
 * V2-16's `EditorExtension` (`agents/editor/README.md`):
 *
 * - replaces the built-in `flow` placeholder with the builder (same id, order,
 *   visibility and issue routing; `layout: "full"`); the canvas inside is a
 *   lazily loaded chunk (`flow-section.tsx`);
 * - fills the `modeChip` slot with the prompt ↔ flow switch dialog and the
 *   `versionHistory` slot with the history dialog (diff + restore);
 * - introduces the Instructions section as the base instructions in flow
 *   mode (R-V2-13) without changing WP-5's editor.
 */
export const flowBuilderExtension: EditorExtension = {
  id: "V2-16",
  sections: [
    {
      id: "flow",
      label: "Flow",
      icon: WorkflowIcon,
      order: 30,
      Component: FlowSection,
      visible: ({ mode }) => mode === "flow",
      layout: "full",
      issuePaths: ["flow"],
      issueKeywords: /\b(flow|node|edge|path|variable)\b/i,
      issueKeywordPriority: 80,
    },
  ],
  sectionPatches: [{ id: "instructions", patch: { Component: FlowAwareInstructions } }],
  slots: {
    modeChip: ModeSwitchChip,
    versionHistory: VersionHistory,
  },
};
