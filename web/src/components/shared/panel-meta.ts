/**
 * Human metadata for the registered session panels (V2-11; F-30).
 *
 * The runtime source of truth for which panels exist is `PANELS` in
 * `@/panels/registry` — this file only carries the console's copy for each
 * one, so console screens (pack cards, the summary rail) can label a panel
 * without importing every panel component. `tests/registry.test.tsx` keeps
 * the two key sets identical, so a panel added to the registry without an
 * entry here (or the reverse) fails the build's tests. There is no separate
 * "known panel ids" list any more (`KNOWN_PANEL_IDS` is gone): the composer
 * derives its choices from `Object.keys(PANELS)`.
 *
 * Plain data, importable from both surfaces.
 */
export interface PanelMeta {
  label: string;
  description: string;
  /** Session layout the panel asks for by default (`PanelDefinition.layout`). */
  layout: "side" | "wide";
  /** Renders `PanelLayout.blocks` (the composite panel). */
  blocksAware?: boolean;
  /** Superseded by `composite`; kept so old agents still render and read well. */
  legacy?: boolean;
}

export const PANEL_META: Record<string, PanelMeta> = {
  composite: {
    label: "Block panel",
    description: "Build the panel from blocks: status, forms, tables, documents, photos, sources and more.",
    layout: "side",
    blocksAware: true,
  },
  generic: {
    label: "Session panel (classic)",
    description: "Status, notes, checklist, attachments and activity. Replaced by the four default blocks.",
    layout: "side",
    legacy: true,
  },
  insurance_notebook: {
    label: "Claim notebook",
    description:
      "The adjuster's notebook: handwritten notes, taped photos, sketch and stamp. Needs the insurance pack's tools.",
    layout: "wide",
  },
};

/** Metadata for `panelId`, or a neutral stand-in for an id this console doesn't know. */
export function panelMeta(panelId: string): PanelMeta {
  return (
    PANEL_META[panelId] ?? {
      label: panelId || "Panel",
      description: "A pack panel this console doesn't have a description for.",
      layout: "side",
    }
  );
}
