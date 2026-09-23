import { AudioLinesIcon } from "lucide-react";

import type { EditorExtension } from "@/components/console/agents/editor/types";
import { ConnectionChip } from "@/components/console/agents/providers-section/connection-chip";
import { ProvidersSection } from "@/components/console/agents/providers-section/providers-section";

/**
 * V2-13's `EditorExtension` (`agents/editor/README.md`): replaces the
 * built-in `providers` section (WP-4's `tabs/providers-tab.tsx`) with the
 * connection-aware version, and fills the `connectionChip` header slot with
 * the change popover.
 */
export const providersSectionExtension: EditorExtension = {
  id: "V2-13",
  sections: [
    {
      id: "providers",
      label: "Providers",
      icon: AudioLinesIcon,
      order: 10,
      Component: ProvidersSection,
      issuePaths: ["pipeline", "connection_id"],
      issueKeywords: /\b(stt|llm|tts|realtime|provider|credential|model|avatar|image|vad|turn|noise)/i,
      issueKeywordPriority: 10,
    },
  ],
  slots: {
    connectionChip: ConnectionChip,
  },
};
