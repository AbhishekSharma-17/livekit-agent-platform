import { MessageCircleIcon } from "lucide-react";

import { BUILTIN_SECTIONS } from "./builtin-sections";
import { EDITOR_EXTENSIONS } from "./extensions";
import { resolveEditorSections, resolveEditorSlots } from "./registry";
import { ConversationSection } from "./sections/conversation-section";
import type { EditorExtension } from "./types";

/**
 * V5-11: the Conversation section (presets, turn taking, the turn detector,
 * sounds, noise cancellation). Registered here directly (rather than through
 * `extensions.ts`, `README.md`'s usual append-only list) because this
 * package's exclusive files are `sections.ts` / `validation-map.ts` /
 * `registry.ts`, not `extensions.ts`. `order: 25` sits between "Instructions &
 * voice" (20) and "Flow" (30), matching the built-ins' spacing-by-10
 * convention for inserting between two of them.
 */
const conversationSectionExtension: EditorExtension = {
  id: "V5-11",
  sections: [
    {
      id: "conversation",
      label: "Conversation",
      icon: MessageCircleIcon,
      order: 25,
      Component: ConversationSection,
      issuePaths: [
        "pipeline.turn_handling",
        "pipeline.conversation_preset",
        "pipeline.turn_detector",
        "voice.thinking_sound",
        "voice.ambient_sound",
        "tools.execution_default",
      ],
      issueKeywords: /\b(turn.?tak|turn detector|endpointing|preemptive|conversation preset|ambient sound|background sound)/i,
      issueKeywordPriority: 25,
    },
  ],
};

/**
 * The editor's section registry (docs/UI_UX_SPEC.md §7.4 item 3): built-ins
 * plus `EDITOR_EXTENSIONS` plus V5-11's own extension above, resolved once at
 * module load.
 */
export const EDITOR_SECTIONS = resolveEditorSections(BUILTIN_SECTIONS, [
  ...EDITOR_EXTENSIONS,
  conversationSectionExtension,
]);
export const EDITOR_SLOTS = resolveEditorSlots(EDITOR_EXTENSIONS);

/** The section shown when `?section=` is absent or unknown. */
export const DEFAULT_SECTION_ID = "providers";
