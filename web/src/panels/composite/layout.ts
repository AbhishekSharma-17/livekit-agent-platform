/**
 * Panel layout delivery (R-V2-7, CONTRACTS-V2 §4.4) — and the **one** adapter
 * for the contract fields that have not reached the generated types yet.
 *
 * The browser learns a session's blocks from `ConnectResponse.agent.panel`
 * (`AgentPublicOut.panel: PanelLayout`), computed by the api's
 * `effective_layout(agent, pack)` at the session's pinned config version.
 * It never reads `AgentConfig` through the admin API to render a session.
 *
 * ---------------------------------------------------------------------------
 * ADAPTER — the one place that papers over contract gaps (V2-11 hand-off):
 *
 * 1. (Removed, R-V2-15.) The generated `BlockSpec` now carries
 *    `title?: string | null` (the contracts export stopped stripping a
 *    *property* named `title`); every importer uses `BlockSpec` /
 *    `PanelLayout` directly (the V2-11 aliases are gone, V2-19B-1).
 * 2. `AgentPublicOut.panel` is in the contract (V2-12), but an api that
 *    predates V2-12 omits it. `panelLayoutOf()` then derives the layout from
 *    `ui_panel_id` exactly as the api's `effective_layout` would for an
 *    agent without blocks: the four default blocks
 *    (`lkap_contracts.migrate.DEFAULT_COMPOSITE_BLOCKS`) for `generic` /
 *    `composite`, and no blocks for a custom panel. Dead once every api
 *    sends `panel`.
 * ---------------------------------------------------------------------------
 */
import type { AgentPublicOut, BlockSpec, PanelLayout } from "@/contracts/lkap-contracts";

export type BlockType = BlockSpec["type"];

/** A fully-defaulted layout: what the composite panel renders. */
export interface EffectivePanelLayout {
  panel_id: string;
  layout: "side" | "wide";
  /** Sorted by `order` (stable), duplicate / invalid ids dropped. */
  blocks: BlockSpec[];
}

/** The built-in panel that renders `PanelLayout.blocks`. */
export const COMPOSITE_PANEL_ID = "composite";

/** The v1 panel the migration rewrites to `composite` + the four blocks below. */
export const LEGACY_GENERIC_PANEL_ID = "generic";

/**
 * `lkap_contracts.migrate.DEFAULT_COMPOSITE_BLOCKS`, verbatim — what a v1
 * `generic` agent (and the generic pack's `default_panel`) shows.
 */
export const DEFAULT_COMPOSITE_BLOCKS: readonly BlockSpec[] = [
  { id: "status", type: "status", title: null, config: {}, order: 0 },
  { id: "notes", type: "notes", title: "Notes", config: {}, order: 1 },
  { id: "checklist", type: "checklist", title: "Still needed", config: {}, order: 2 },
  { id: "activity", type: "activity", title: "Activity", config: {}, order: 3 },
];

/**
 * Sort by `order` (stable for ties) and drop duplicate or invalid ids, exactly
 * like the worker's `resolve_block_specs` — so the browser renders the same
 * block list the worker seeds `UiState.blocks` from.
 */
export function normalizeBlocks(blocks: readonly BlockSpec[] | null | undefined): BlockSpec[] {
  const indexed = (blocks ?? []).map((spec, index) => ({ spec, index }));
  indexed.sort((a, b) => (a.spec.order ?? 0) - (b.spec.order ?? 0) || a.index - b.index);
  const seen = new Set<string>();
  const out: BlockSpec[] = [];
  for (const { spec } of indexed) {
    if (!spec.id || spec.id.includes("/") || seen.has(spec.id)) continue;
    seen.add(spec.id);
    out.push(spec);
  }
  return out;
}

/** Fill the defaults of a `PanelLayout` (panel id, layout, blocks). */
export function effectiveLayout(panel: PanelLayout | null | undefined): EffectivePanelLayout {
  return {
    panel_id: panel?.panel_id || COMPOSITE_PANEL_ID,
    layout: panel?.layout === "wide" ? "wide" : "side",
    blocks: normalizeBlocks(panel?.blocks),
  };
}

/**
 * The layout a session renders for `agent` (R-V2-7).
 *
 * `agent.panel` wins whenever the api sends it. Otherwise (ADAPTER item 2)
 * the layout is derived from `ui_panel_id` the way the api's
 * `effective_layout` would for an agent without `config.panel.blocks`.
 */
export function panelLayoutOf(agent: AgentPublicOut): EffectivePanelLayout {
  // Typed as required, but absent from a pre-V2-12 api's response.
  const panel = agent.panel as PanelLayout | null | undefined;
  if (panel) return effectiveLayout(panel);
  const id = agent.ui_panel_id;
  if (!id || id === COMPOSITE_PANEL_ID || id === LEGACY_GENERIC_PANEL_ID) {
    return {
      panel_id: COMPOSITE_PANEL_ID,
      layout: "side",
      blocks: normalizeBlocks(DEFAULT_COMPOSITE_BLOCKS),
    };
  }
  return { panel_id: id, layout: "side", blocks: [] };
}

/**
 * A stored `AgentConfig.panel` as a public layout, for the two places that
 * start from the admin `AgentOut` instead of the connect response (test-mode
 * pre-call, the console's end-of-call snapshot): a block panel saved without
 * blocks shows the default four, as the api's `effective_layout` does for the
 * generic pack. Anything else passes through.
 */
export function storedPanelLayout(panel: PanelLayout | null | undefined, uiPanelId: string): PanelLayout {
  const panelId = panel?.panel_id || uiPanelId || COMPOSITE_PANEL_ID;
  if ((panelId === COMPOSITE_PANEL_ID || panelId === LEGACY_GENERIC_PANEL_ID) && !panel?.blocks?.length) {
    return { panel_id: COMPOSITE_PANEL_ID, layout: panel?.layout ?? "side", blocks: DEFAULT_COMPOSITE_BLOCKS.map((b) => ({ ...b })) };
  }
  return { ...panel, panel_id: panelId };
}
