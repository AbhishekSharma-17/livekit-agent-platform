"use client";

/**
 * The composite panel's `show_block` highlight (`HIGHLIGHT_MS`, `index.tsx`),
 * threaded to a block nested inside a `layout` (V6-10, D-V6-18).
 *
 * `CompositePanel` passes `highlighted` straight to each top-level `<Block>`
 * it renders, but a `layout`'s children are rendered by `blocks/index.tsx`'s
 * own `LayoutBlock` — not from that top-level map — so they have no other way
 * to learn which block id (if any) is currently highlighted. A plain context
 * instead of a prop: `blocks/index.tsx` cannot import from `composite/index.tsx`
 * without a cycle (composite already imports `<Block>` from it).
 */
import { createContext, useContext } from "react";

const BlockHighlightContext = createContext<string | null>(null);

export const BlockHighlightProvider = BlockHighlightContext.Provider;

/** The currently `show_block`-highlighted block id, or `null`. */
export function useBlockHighlight(): string | null {
  return useContext(BlockHighlightContext);
}
