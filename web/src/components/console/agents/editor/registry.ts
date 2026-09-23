import type {
  EditorExtension,
  EditorSectionContext,
  EditorSectionDef,
  EditorSlots,
} from "./types";

/**
 * Pure resolution of the editor's sections and slots from the built-ins plus
 * the extensions list (`./extensions.ts`). No module-level mutable state:
 * the result is a function of its inputs, so tests can pass their own lists.
 */

export function resolveEditorSections(
  builtins: readonly EditorSectionDef[],
  extensions: readonly EditorExtension[] = [],
): EditorSectionDef[] {
  const byId = new Map<string, EditorSectionDef>();
  for (const def of builtins) byId.set(def.id, def);
  for (const extension of extensions) {
    for (const def of extension.sections ?? []) byId.set(def.id, def);
  }
  for (const extension of extensions) {
    for (const { id, patch } of extension.sectionPatches ?? []) {
      const current = byId.get(id);
      if (current) byId.set(id, { ...current, ...patch, id });
    }
  }
  return [...byId.values()].sort((a, b) => a.order - b.order || a.id.localeCompare(b.id));
}

export function visibleSections(
  sections: readonly EditorSectionDef[],
  ctx: EditorSectionContext,
): EditorSectionDef[] {
  return sections.filter((section) => (section.visible ? section.visible(ctx) : true));
}

export interface ResolvedEditorSlots {
  connectionChip?: EditorSlots["connectionChip"];
  modeChip?: EditorSlots["modeChip"];
  versionHistory?: EditorSlots["versionHistory"];
  testCallItems: NonNullable<EditorSlots["testCallItems"]>;
  headerActions: NonNullable<EditorSlots["headerActions"]>;
}

export function resolveEditorSlots(extensions: readonly EditorExtension[] = []): ResolvedEditorSlots {
  const resolved: ResolvedEditorSlots = { testCallItems: [], headerActions: [] };
  for (const { slots } of extensions) {
    if (!slots) continue;
    if (slots.connectionChip) resolved.connectionChip = slots.connectionChip;
    if (slots.modeChip) resolved.modeChip = slots.modeChip;
    if (slots.versionHistory) resolved.versionHistory = slots.versionHistory;
    resolved.testCallItems.push(...(slots.testCallItems ?? []));
    resolved.headerActions.push(...(slots.headerActions ?? []));
  }
  return resolved;
}
