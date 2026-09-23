import { BUILTIN_SECTIONS } from "./builtin-sections";
import { EDITOR_EXTENSIONS } from "./extensions";
import { resolveEditorSections, resolveEditorSlots } from "./registry";

/**
 * The editor's section registry (docs/UI_UX_SPEC.md §7.4 item 3): built-ins
 * plus `EDITOR_EXTENSIONS`, resolved once at module load.
 */
export const EDITOR_SECTIONS = resolveEditorSections(BUILTIN_SECTIONS, EDITOR_EXTENSIONS);
export const EDITOR_SLOTS = resolveEditorSlots(EDITOR_EXTENSIONS);

/** The section shown when `?section=` is absent or unknown. */
export const DEFAULT_SECTION_ID = "providers";
