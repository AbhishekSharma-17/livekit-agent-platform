import type { EditorExtension } from "@/components/console/agents/editor/types";
import { ConnectionChip } from "@/components/console/agents/providers-section/connection-chip";

/**
 * V2-13's `EditorExtension` (`agents/editor/README.md`): fills the
 * `connectionChip` header slot with the change popover. The Providers
 * section itself is the built-in one (`editor/builtin-sections.tsx` points at
 * `providers-section.tsx`), so there is one section and nothing replaces it.
 */
export const providersSectionExtension: EditorExtension = {
  id: "V2-13",
  slots: {
    connectionChip: ConnectionChip,
  },
};
