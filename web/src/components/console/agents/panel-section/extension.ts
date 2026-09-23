import { PanelRightIcon } from "lucide-react";

import type { EditorExtension } from "@/components/console/agents/editor/types";

import { PanelComposer } from "./panel-composer";

/**
 * V2-11: the panel composer replaces the built-in `panel` section
 * ("Panel & capabilities") — same id, order, issue paths and keywords, so deep
 * links and issue routing are unchanged (editor README, "Adding or replacing
 * a section").
 */
export const panelComposerExtension: EditorExtension = {
  id: "V2-11",
  sections: [
    {
      id: "panel",
      label: "Panel & capabilities",
      icon: PanelRightIcon,
      order: 40,
      Component: PanelComposer,
      issuePaths: ["panel", "capabilities", "ui_panel_id"],
      issueKeywords: /\b(panel|camera|screen|vision|chat|block)/i,
      issueKeywordPriority: 50,
    },
  ],
};
