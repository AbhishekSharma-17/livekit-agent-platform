import { FlaskConicalIcon, MessageCircleIcon } from "lucide-react";

import { BUILTIN_SECTIONS } from "./builtin-sections";
import { EDITOR_EXTENSIONS } from "./extensions";
import { resolveEditorSections, resolveEditorSlots } from "./registry";
import { ConversationSection } from "./sections/conversation-section";
import { TestsSection } from "./sections/tests-section";
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
 * V5-33: the Tests section (cases, run, run history and verdicts, the
 * publish gate). Same rationale as V5-11's own extension above — this
 * package's exclusive files are `sections.ts` / `registry.ts` /
 * `publish-popover.tsx`, not `extensions.ts`. `order: 85` sits after
 * "Limits" (80), the last built-in: testing comes once everything else is
 * configured.
 */
const testsSectionExtension: EditorExtension = {
  id: "V5-33",
  sections: [
    {
      id: "tests",
      label: "Tests",
      icon: FlaskConicalIcon,
      order: 85,
      Component: TestsSection,
      issuePaths: ["tests", "publish_gate"],
      issueKeywords: /\b(test case|publish gate|require.{0,3}tests?)/i,
      issueKeywordPriority: 15,
    },
  ],
};

/**
 * The editor's section registry (docs/UI_UX_SPEC.md §7.4 item 3): built-ins
 * plus `EDITOR_EXTENSIONS` plus V5-11's and V5-33's own extensions above,
 * resolved once at module load.
 */
export const EDITOR_SECTIONS = resolveEditorSections(BUILTIN_SECTIONS, [
  ...EDITOR_EXTENSIONS,
  conversationSectionExtension,
  testsSectionExtension,
]);
export const EDITOR_SLOTS = resolveEditorSlots(EDITOR_EXTENSIONS);

/** The section shown when `?section=` is absent or unknown. */
export const DEFAULT_SECTION_ID = "providers";
