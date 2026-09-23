"use client";

/**
 * Superseded by the V2-11 panel composer
 * (`components/console/agents/panel-section/`), which replaces the `panel`
 * section through an `EditorExtension`. Kept as a re-export because the
 * built-in section list (`editor/builtin-sections.tsx`, WP-3) still imports
 * `PanelTab`, so an editor mounted without extensions shows the composer too.
 */
export { PanelComposer as PanelTab } from "@/components/console/agents/panel-section/panel-composer";
