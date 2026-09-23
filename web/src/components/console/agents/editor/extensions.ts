import type { EditorExtension } from "./types";

import { providersSectionExtension } from "@/components/console/agents/providers-section/extension";
import { panelComposerExtension } from "@/components/console/agents/panel-section/extension";
import { telephonyExtension } from "@/components/console/telephony/extension";
import { flowBuilderExtension } from "@/components/console/flow/extension";
import { testChatExtension } from "@/components/console/agents/test-chat/extension";

/**
 * The agent editor's plug-in list (WP-3 contract; rules in `./README.md`).
 *
 * This file is **append-only and shared**: a later package (WP-4, WP-5,
 * V2-11, V2-13, V2-16, V2-17, V2-18) adds one import of an `EditorExtension`
 * exported from a module it owns, and one entry below. It never edits the
 * shell, the built-in list or another package's entry. Order matters only for
 * single-component slots (the last extension wins) and for concatenated
 * slots (menu items appear in list order).
 *
 * Example (V2-11, from its own `components/console/agents/panel-section/extension.ts`):
 *
 *   import { panelComposerExtension } from "@/components/console/agents/panel-section/extension";
import { telephonyExtension } from "@/components/console/telephony/extension";
 *   export const EDITOR_EXTENSIONS: EditorExtension[] = [panelComposerExtension];
 */
export const EDITOR_EXTENSIONS: EditorExtension[] = [
  providersSectionExtension,
  panelComposerExtension,
  telephonyExtension,
  flowBuilderExtension,
  testChatExtension,
];
